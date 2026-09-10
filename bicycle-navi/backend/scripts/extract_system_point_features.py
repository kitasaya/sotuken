"""自システム経路15ペアに、Google側と完全に同一条件の点的規制抽出を行う。

## 目的

一時停止・踏切は経路選択で消えない案内対象である。Google経路と自システム経路で
同程度の数になることを確認し、「経路が決まっても違反の有無が決まらない」ことの
定量的裏付けにする。**優劣の比較ではない。**

## 制約

- 経路探索ロジック・`rerouter.py` には触らない。`custom_model` / `areas` を発生させない。
  `services.graphhopper.get_route`（profile=bike の素の呼び出し）だけを使う。
- 抽出結果を `violations` 配列に入れない。既存の凍結測定ファイルは読むだけ。
- Overpass / GraphHopper が失敗したら例外で中断し、CSVを1行も書かない。
  取得失敗が「0件」に化けることを防ぐ。
- 判定は `extract_point_features.py` / `analyze_j1_thresholds.py` の既存実装を
  そのまま import して使う。再実装しない。

## 測定条件（Google側と同一）

- GraphHopper 11.0 / kanto-260801.osm.pbf、Overpass attic 2026-08-01T20:21:21Z
- 一時停止：コリドー20mで候補取得 → 経路からの垂線距離3m以内 →
  `direction` と進行方向の照合による方向適用判定
- 踏切：経路ポリラインと鉄道way（rail / light_rail / narrow_gauge）の幾何交差 →
  bridge / tunnel / layer による立体交差の除外 → 30m以内の踏切ノードとの対応づけ →
  25mクラスタリング。**距離閾値方式（旧B）は使わない**（偽陽性を1件出したため）。

## 検出条件の非対称（記録のみ。片側だけ精度を上げる実装はしない）

自システム側は GraphHopper の `osm_way_id` detail が取れるため、親wayの特定を
way_id で直接行える。Google側は座標逆引き（点-曲線垂直距離マッチ）に依存する。
この非対称は二段階右折における instruction 有無の非対称と同じ構造である。
**way_id を使うのは親wayの特定だけで、判定基準そのものは両者で同一。**

## 使い方

  python scripts/extract_system_point_features.py --dry-run
  python scripts/extract_system_point_features.py --write
"""

import argparse
import asyncio
import csv
import sys
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR.parent))
sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_j1_thresholds as j1
import extract_point_features as epf

from services.experiment_settings import activate_experiment_overpass_date
from services.external_route_scorer import _travel_vector_at, _turn_angle_deg
from services.graphhopper import get_route
from services.http_clients import close_client

DATA_DIR = SCRIPTS_DIR.parent / "data"
OD_PAIRS_CSV = DATA_DIR / "od_pairs.csv"
OUTPUT_CSV = DATA_DIR / "j1_system_point_features.csv"

STOP_VALUES = {"stop", "give_way"}
REQUEST_INTERVAL_S = 3.0

FIELDNAMES = [
    "label", "road_type", "feature_type", "osm_tag", "detection_method",
    "corridor_candidates", "on_route_way_candidates", "on_route_candidates",
    "excluded_count",
    "detected_count", "counting_unit",
    "pairs_total", "pairs_with_detection", "pairs_with_zero_detection",
    "route_distance_m", "overpass_snapshot_date", "note",
]

TOTAL_LABEL = "合計(15ペア)"
MERGE_CAUTION = "一時停止と踏切は合算しない"


async def fetch_system_route(row: dict) -> dict:
    """自システムの経路を取得する。custom_model は指定しない（素の bike プロファイル）。"""
    data = await get_route(
        float(row["origin_lat"]), float(row["origin_lng"]),
        float(row["dest_lat"]), float(row["dest_lng"]),
    )
    paths = data.get("paths") or []
    if not paths:
        raise RuntimeError(f"{row['label']}: GraphHopper が経路を返さなかった")
    path = paths[0]
    coords = path["points"]["coordinates"]
    if len(coords) < 2:
        raise RuntimeError(f"{row['label']}: 経路の座標点が不足 ({len(coords)})")
    intervals = (path.get("details") or {}).get("osm_way_id") or []
    return {
        "coords": coords,
        "distance_m": path.get("distance", 0.0),
        "way_intervals": [
            (int(start), int(end), int(way_id))
            for start, end, way_id in intervals if way_id is not None
        ],
    }


def _way_id_resolver(way_intervals: list):
    """経路セグメント index から osm_way_id を返す関数を作る。

    GraphHopper の details は [from_index, to_index, value] の半開区間で返る。
    セグメント i は座標 i→i+1 なので、from <= i < to の区間を引く。
    """
    def resolve(index: int) -> int | None:
        for start, end, way_id in way_intervals:
            if start <= index < end:
                return way_id
        return None
    return resolve


def _route_way_ids(way_intervals: list) -> set[int]:
    return {way_id for _, _, way_id in way_intervals}


async def extract_stop_candidates(label: str, coords: list, cumulative: list,
                                  route_way_ids: set[int]) -> dict:
    """一時停止候補を Google 側と同一手順で抽出し、方向適用を判定する。"""
    elements = await epf.fetch_point_feature_nodes(coords, epf.CORRIDOR_RADIUS_M)
    corridor: list[dict] = []
    for element in elements:
        tags = element.get("tags") or {}
        if tags.get("highway") not in STOP_VALUES:
            continue
        lat, lng = element.get("lat"), element.get("lon")
        if lat is None or lng is None:
            continue
        dist_m, along_m = epf._locate_on_route(lat, lng, coords, cumulative)
        corridor.append({
            "node_id": int(element["id"]),
            "lat": lat, "lng": lng, "tags": tags,
            "dist_to_route_m": round(dist_m, 2),
            "route_position_m": round(along_m, 1),
        })

    if not corridor:
        return {"corridor": [], "on_route": [], "applicable": [], "on_route_way": []}

    # 親wayの特定だけ way_id で行う（Google側は座標逆引き）。判定基準は変えない。
    # コリドー全件について「経路が実際に通る way 上のノードか」を持たせ、
    # 3m 基準の取りこぼしを診断できるようにする。
    parents = await epf.fetch_parent_ways([row["node_id"] for row in corridor])
    for row in corridor:
        candidates = parents.get(row["node_id"], [])
        on_path = [w for w in candidates if w["way_id"] in route_way_ids]
        chosen = on_path[0] if on_path else (candidates[0] if candidates else None)
        row["parent_way_id"] = chosen["way_id"] if chosen else None
        row["parent_highway"] = chosen["highway"] if chosen else ""
        row["parent_on_route"] = bool(on_path)

    on_route_way = [row for row in corridor if row["parent_on_route"]]
    on_route = [
        row for row in corridor
        if row["dist_to_route_m"] <= j1.STRICT_ON_ROUTE_M
    ]
    if not on_route:
        return {
            "corridor": corridor, "on_route": [], "applicable": [],
            "on_route_way": on_route_way,
        }

    ways = await j1.fetch_ways(
        sorted({row["parent_way_id"] for row in on_route if row["parent_way_id"]})
    )
    applicable = []
    for row in on_route:
        travel_vector = _travel_vector_at(
            coords, j1._nearest_route_index(coords, row["lat"], row["lng"]),
        )
        way = ways.get(row["parent_way_id"]) if row["parent_way_id"] else None
        forward = j1._way_forward_vector(way, row["node_id"]) if way else None
        direction_tag = row["tags"].get("direction", "")
        if forward is None:
            row["deviation_deg"] = None
            row["travelling"] = "判定不能"
            row["verdict"] = "判定不能"
        else:
            row["deviation_deg"] = round(_turn_angle_deg(forward, travel_vector), 1)
            row["travelling"] = "forward" if row["deviation_deg"] < 90 else "backward"
            if not direction_tag:
                row["verdict"] = "適用（タグ無し）"
            elif direction_tag == row["travelling"]:
                row["verdict"] = "適用"
            elif direction_tag in {"forward", "backward"}:
                row["verdict"] = "適用外"
            else:
                row["verdict"] = "判定不能"
        if row["verdict"] != "適用外" and row["verdict"].startswith("適用"):
            applicable.append(row)

    return {
        "corridor": corridor, "on_route": on_route, "applicable": applicable,
        "on_route_way": on_route_way,
    }


async def extract_crossings(label: str, coords: list, cumulative: list,
                            resolver) -> dict:
    """踏切を幾何交差方式（F）で確定する。距離閾値方式は使わない。"""
    result = await j1.compute_geometric_crossings(
        label, coords, cumulative, road_way_resolver=resolver,
    )
    planar = result["planar"]
    with_node = [row for row in planar if row.get("nearest_node_id")]
    clusters = j1._cluster_positions(
        [row["route_position_m"] for row in with_node], j1.DEFAULT_CLUSTER_GAP_M,
    )
    verdicts = Counter(row["verdict"] for row in result["crossings"])
    return {
        "intersections": len(result["crossings"]),
        "planar": len(planar),
        "grade_separated": verdicts.get("立体交差", 0),
        "unknown": verdicts.get("不明", 0),
        "planar_with_node": len(with_node),
        "clusters": len(clusters),
        "cluster_positions": [round(sum(c) / len(c), 1) for c in clusters],
        "detail": with_node,
        "tram_way_count": result["tram_way_count"],
        "rail_way_count": result["rail_way_count"],
    }


def build_rows(per_pair: list[dict], snapshot_date: str) -> list[dict]:
    rows = []
    for entry in per_pair:
        rows.append({
            "label": entry["label"],
            "road_type": entry["road_type"],
            "feature_type": "一時停止",
            "osm_tag": "highway=stop / give_way",
            "detection_method": "コリドー20m→垂線距離3m以内→方向適用判定",
            "corridor_candidates": len(entry["stops"]["corridor"]),
            "on_route_way_candidates": len(entry["stops"]["on_route_way"]),
            "on_route_candidates": len(entry["stops"]["on_route"]),
            "excluded_count": (
                len(entry["stops"]["on_route"]) - len(entry["stops"]["applicable"])
            ),
            "detected_count": len(entry["stops"]["applicable"]),
            "counting_unit": "OSMノード数（進行方向が適用されるもののみ）",
            "route_distance_m": entry["distance_m"],
            "overpass_snapshot_date": snapshot_date,
            "note": MERGE_CAUTION,
        })
        crossings = entry["crossings"]
        rows.append({
            "label": entry["label"],
            "road_type": entry["road_type"],
            "feature_type": "踏切",
            "osm_tag": "railway=rail/light_rail/narrow_gauge との幾何交差",
            "detection_method": "幾何交差→立体交差除外→踏切ノード対応→25mクラスタ",
            "corridor_candidates": crossings["intersections"],
            "on_route_way_candidates": crossings["planar"],
            "on_route_candidates": crossings["planar_with_node"],
            "excluded_count": crossings["grade_separated"],
            "detected_count": crossings["clusters"],
            "counting_unit": "物理箇所数（クラスタ後）",
            "route_distance_m": entry["distance_m"],
            "overpass_snapshot_date": snapshot_date,
            "note": (
                f"{MERGE_CAUTION}／立体交差{crossings['grade_separated']}・"
                f"不明{crossings['unknown']}・tram way{crossings['tram_way_count']}"
            ),
        })

    for feature_type in ("一時停止", "踏切"):
        selected = [row for row in rows if row["feature_type"] == feature_type]
        detected = sum(row["detected_count"] for row in selected)
        zero_pairs = sum(1 for row in selected if row["detected_count"] == 0)
        template = selected[0]
        rows.append({
            "label": TOTAL_LABEL,
            "road_type": "",
            "feature_type": feature_type,
            "osm_tag": template["osm_tag"],
            "detection_method": template["detection_method"],
            "corridor_candidates": sum(row["corridor_candidates"] for row in selected),
            "on_route_way_candidates": sum(
                row["on_route_way_candidates"] for row in selected
            ),
            "on_route_candidates": sum(row["on_route_candidates"] for row in selected),
            "excluded_count": sum(row["excluded_count"] for row in selected),
            "detected_count": detected,
            "counting_unit": template["counting_unit"],
            "pairs_total": len(selected),
            "pairs_with_detection": len(selected) - zero_pairs,
            "pairs_with_zero_detection": zero_pairs,
            "route_distance_m": round(
                sum(row["route_distance_m"] for row in selected), 1,
            ),
            "overpass_snapshot_date": snapshot_date,
            "note": MERGE_CAUTION,
        })
    return rows


PAIR_MAX_ATTEMPTS = 3
PAIR_RETRY_WAIT_S = 20.0


async def _with_retry(label: str, factory):
    """Overpass の一時障害を再試行する。使い切ったら例外を送出して全体を止める。

    ペア/試行ごとに asyncio.create_task で ContextVar を分離し、回路遮断を
    後続へ漏らさない。空の結果を0件として書き込まないための fail-closed。
    """
    error: Exception | None = None
    for attempt in range(1, PAIR_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.create_task(factory())
        except Exception as e:
            error = e
            print(
                f"    → 取得失敗（{attempt}/{PAIR_MAX_ATTEMPTS}）"
                f"{PAIR_RETRY_WAIT_S:.0f}秒後に再試行: {type(e).__name__}: {e}",
                flush=True,
            )
            if attempt < PAIR_MAX_ATTEMPTS:
                await asyncio.sleep(PAIR_RETRY_WAIT_S)
    raise RuntimeError(f"{label}: 取得に失敗したため中断する（{error}）")


async def main(args) -> int:
    settings = activate_experiment_overpass_date()
    snapshot_date = settings["overpass_snapshot_date"]
    print(f"実験用Overpass取得時点: {snapshot_date}")
    print(f"GraphHopper: {__import__('services.graphhopper', fromlist=['GH_BASE']).GH_BASE}")

    with open(OD_PAIRS_CSV, encoding="utf-8-sig", newline="") as f:
        od_rows = list(csv.DictReader(f))
    print(f"対象: {len(od_rows)} ペア\n")

    per_pair = []
    for index, od_row in enumerate(od_rows):
        if index > 0:
            await asyncio.sleep(args.interval)
        label = od_row["label"]
        route = await fetch_system_route(od_row)
        coords = route["coords"]
        cumulative = j1._route_cumulative_m(coords)
        resolver = _way_id_resolver(route["way_intervals"])
        route_way_ids = _route_way_ids(route["way_intervals"])

        stops = await _with_retry(
            label, lambda: extract_stop_candidates(
                label, coords, cumulative, route_way_ids,
            ),
        )
        await asyncio.sleep(args.interval)
        crossings = await _with_retry(
            label, lambda: extract_crossings(label, coords, cumulative, resolver),
        )
        per_pair.append({
            "label": label,
            "road_type": od_row.get("road_type", ""),
            "distance_m": round(route["distance_m"], 1),
            "stops": stops,
            "crossings": crossings,
        })
        print(
            f"  {label}: {len(coords)}座標 {route['distance_m']:.0f}m "
            f"way{len(route_way_ids)}本 / "
            f"一時停止 コリドー{len(stops['corridor'])}"
            f"→経路way上{len(stops['on_route_way'])}→3m内{len(stops['on_route'])}"
            f"→適用{len(stops['applicable'])} / "
            f"踏切 交差{crossings['intersections']}→平面{crossings['planar']}"
            f"→ノード対応{crossings['planar_with_node']}→{crossings['clusters']}箇所",
            flush=True,
        )

    rows = build_rows(per_pair, snapshot_date)
    totals = {row["feature_type"]: row for row in rows if row["label"] == TOTAL_LABEL}
    print("\n[合計]")
    for feature_type, row in totals.items():
        print(
            f"  {feature_type}: 検出{row['detected_count']} / "
            f"検出ゼロのペア {row['pairs_with_zero_detection']}/{row['pairs_total']}"
        )

    print("\n[一時停止・方向適用と判定した明細]")
    for entry in per_pair:
        for row in entry["stops"]["applicable"]:
            print(
                f"  {entry['label']}: node={row['node_id']} "
                f"dist={row['dist_to_route_m']}m direction="
                f"{row['tags'].get('direction', 'none')} "
                f"偏差角={row['deviation_deg']} way={row['parent_way_id']}"
                f"({row['parent_highway']}) 経路way一致={row['parent_on_route']}"
            )
    print("\n[踏切・確定箇所]")
    for entry in per_pair:
        if entry["crossings"]["clusters"]:
            print(
                f"  {entry['label']}: {entry['crossings']['clusters']}箇所 "
                f"位置={entry['crossings']['cluster_positions']}"
            )

    if args.dry_run:
        print("\n[dry-run] CSVへの書き込みをスキップしました。")
        return 0

    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in FIELDNAMES})
    print(f"\n書き込み: {OUTPUT_CSV}（{len(rows)}行）")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="自システム経路15ペアの一時停止・踏切を同一条件で抽出する",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="抽出のみ実行。CSVは作成しない")
    parser.add_argument("--write", action="store_true", help="CSVを書き込む")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL_S,
                        help=f"Overpass 呼び出しの間隔秒（既定: {REQUEST_INTERVAL_S}）")
    args = parser.parse_args()
    if not args.dry_run and not args.write:
        parser.print_help()
        sys.exit(1)

    async def _run() -> int:
        try:
            return await main(args)
        finally:
            await close_client()

    sys.exit(asyncio.run(_run()))
