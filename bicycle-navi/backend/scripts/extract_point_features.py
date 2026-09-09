"""Googleルート15ペア上の点的規制（一時停止・踏切）をOSMから抽出して集計する。

## 目的

論文 3-1-2 の表に転記する2つの数字（検出数・検出ゼロのペア数）を、
feature_type ごとに1行で読み取れる形で出力する。あわせて1行1ノードの
明細と、収録率検証用のノード列挙を出力する。

## 使い方

  # 15ペア全件を集計して出力1・出力2を書き込む
  python scripts/extract_point_features.py --all --write

  # 収録率検証用のノード列挙（出力3）だけを作る
  python scripts/extract_point_features.py --write \
      --survey-label 千葉→幕張本郷 --survey-label 高円寺→中野

  # 書き込まずに確認だけする
  python scripts/extract_point_features.py --all --dry-run

## 入力

  backend/data/google_routes_input.csv （label, polyline。R2 v4 と同一の凍結入力）

## 出力

  出力1 backend/data/r2_point_features_j1.csv        feature_type ごとの集計
  出力2 backend/data/r2_point_features_detail_j1.csv 1行1ノードの明細（座標7桁）
  出力3 backend/data/stop_survey_segments_j1.csv     --survey-label 経路のノード列挙

## 集計上の約束（人手判断を奪わないための制約）

  - 一時停止・譲れ・踏切（車道）・踏切（歩道自転車道）は行を分け、合算しない。
    「案内対象地点数」のような合成値は作らない。
  - 検出0件は「該当なし」ではなく「OSM上に該当タグを持つノードが0件」と書く。
  - detected_node_count はコリドー（既定20m）内のノード数であり、経路の道路に
    立つ標識と、交差する側道側に立つ標識を区別しない。厳しい読み（垂線距離3m
    以内）を detected_node_count_within_3m に並記する。どちらを表に採るかは
    人間が決める。両者を足さない。
  - railway=level_crossing（車道）と railway=crossing（歩道・自転車道）は
    別 feature_type のまま出す。論文表への合算可否は人間が判断する。

## 交差点判定について

  出力3の highway_tag は交差点ノード自身のタグである。OSM の一時停止ノードは
  交差点ノードではなく手前の way 中間ノード（接続エッジ数2）に置かれることが多いため、
  近傍の stop/give_way ノードを nearby_stop_* 列に別立てで記録する。両者を混同しない。

  出力3の交差点判定は v4 の two-step 判定と同じ数え方を再利用する。
  接続エッジ数は overpass._way_edge_contribution（端点=1・中間=2・閉ループ=2）、
  除外 highway 9種は overpass.INTERSECTION_EXCLUDED_HIGHWAYS をそのまま使い、
  非除外 way の寄与合計が3以上のノードを交差点とする。再実装はしない。

## Overpass 障害時の扱い

  score_google_routes.py と同じ fail-closed 方針。対象のどれか1本でも取得に
  失敗したらCSVは1件も書かず、終了コードを非ゼロにする。取得失敗が
  「検出0件」に化けることを防ぐ。
"""

import argparse
import asyncio
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.experiment_settings import activate_experiment_overpass_date
from services.external_route_scorer import _haversine_m, decode_polyline
from services.overpass import (
    INTERSECTION_EXCLUDED_HIGHWAYS,
    _M_PER_DEG_LAT,
    _M_PER_DEG_LNG_EQUATOR,
    _point_to_segment_dist_m,
    _post_with_retry,
    _way_edge_contribution,
    format_overpass_usage_summary,
    reset_overpass_usage_stats,
)

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"
SUMMARY_CSV = DATA_DIR / "r2_point_features_j1.csv"
DETAIL_CSV = DATA_DIR / "r2_point_features_detail_j1.csv"
SURVEY_CSV = DATA_DIR / "stop_survey_segments_j1.csv"

EXPECTED_PAIR_TOTAL = 15
CORRIDOR_RADIUS_M = 20        # Overpass around のコリドー半径（既存判定と同じ 20m）
NODE_ON_ROUTE_M = 10.0        # 出力3で「経路上」とみなすノードの垂線距離上限
NEARBY_STOP_M = 15.0          # 出力3で交差点の近傍とみなす stop/give_way ノードの距離
STRICT_ON_ROUTE_M = 3.0       # 出力1で「経路の道路そのものの上」と厳しく読む距離
REQUEST_INTERVAL_S = 3.0
PAIR_MAX_ATTEMPTS = 2         # score_google_routes.py と同じ再試行回数
PAIR_RETRY_WAIT_S = 5.0

# feature_type の定義。行の分離単位そのものであり、まとめない。
FEATURE_TYPES = [
    {
        "feature_type": "一時停止",
        "osm_key": "highway",
        "osm_value": "stop",
        "target": "一時停止標識のあるノード",
        "counting_unit": "OSMノード数（進行方向別に direction=forward/backward で分かれる場合がある）",
    },
    {
        "feature_type": "譲れ",
        "osm_key": "highway",
        "osm_value": "give_way",
        "target": "前方優先道路（譲れ）標識のあるノード",
        "counting_unit": "OSMノード数（進行方向別に direction=forward/backward で分かれる場合がある）",
    },
    {
        "feature_type": "踏切_車道",
        "osm_key": "railway",
        "osm_value": "level_crossing",
        "target": "車道と鉄道の平面交差",
        "counting_unit": "OSMノード数（1か所の踏切が線路本数・way分割により複数ノードになる）",
    },
    {
        "feature_type": "踏切_歩道自転車道",
        "osm_key": "railway",
        "osm_value": "crossing",
        "target": "歩道・自転車道と鉄道の平面交差",
        "counting_unit": "OSMノード数（1か所の踏切が線路本数・way分割により複数ノードになる）",
    },
]

_FEATURE_BY_TAG = {(f["osm_key"], f["osm_value"]): f for f in FEATURE_TYPES}
_HIGHWAY_VALUES = [f["osm_value"] for f in FEATURE_TYPES if f["osm_key"] == "highway"]
_RAILWAY_VALUES = [f["osm_value"] for f in FEATURE_TYPES if f["osm_key"] == "railway"]

MERGE_CAUTION = "他のfeature_typeと合算しない"
ZERO_NOTE = "OSM上に該当タグを持つノードが0件"


def _street_view_url(lat: float, lng: float) -> str:
    """緯度経度から Google Street View（pano）を開くURLを組み立てる。"""
    return (
        "https://www.google.com/maps/@?api=1&map_action=pano"
        f"&viewpoint={lat:.7f},{lng:.7f}"
    )


def _polyline_arg(coords: list) -> str:
    """Overpass の around に渡す lat,lon 連結文字列（コリドーの折れ線）。"""
    return ",".join(f"{lat:.6f},{lng:.6f}" for lng, lat in coords)


def _segment_t(lat: float, lng: float, a: list, b: list) -> float:
    """線分 a-b 上での射影パラメータ t（0..1）。進行順の並べ替えにだけ使う。

    距離そのものは overpass._point_to_segment_dist_m を正とする。
    """
    kx = _M_PER_DEG_LNG_EQUATOR * math.cos(math.radians(lat))
    ky = _M_PER_DEG_LAT
    ax, ay = (a[0] - lng) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lng) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return 0.0
    return max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len_sq))


def _route_cumulative_m(coords: list) -> list[float]:
    """始点からの累積距離。ノードを進行順に並べる基準に使う。"""
    cumulative = [0.0]
    for i in range(len(coords) - 1):
        cumulative.append(cumulative[-1] + _haversine_m(coords[i], coords[i + 1]))
    return cumulative


def _locate_on_route(lat: float, lng: float, coords: list,
                     cumulative: list[float]) -> tuple[float, float]:
    """ノードの (経路までの垂線距離m, 起点からの進行距離m) を返す。"""
    best = (float("inf"), 0.0)
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        dist = _point_to_segment_dist_m(lat, lng, a, b)
        if dist < best[0]:
            t = _segment_t(lat, lng, a, b)
            along = cumulative[i] + t * (cumulative[i + 1] - cumulative[i])
            best = (dist, along)
    return best


# ---------------------------------------------------------------------------
# 出力1・出力2：点的規制ノードの抽出
# ---------------------------------------------------------------------------

async def fetch_point_feature_nodes(coords: list, radius: int) -> list[dict]:
    """経路コリドー内の一時停止・譲れ・踏切ノードを1クエリで取得する。"""
    polyline = _polyline_arg(coords)
    highway_re = "|".join(_HIGHWAY_VALUES)
    railway_re = "|".join(_RAILWAY_VALUES)
    query = f"""[out:json][timeout:180];
(
  node(around:{radius},{polyline})["highway"~"^({highway_re})$"];
  node(around:{radius},{polyline})["railway"~"^({railway_re})$"];
);
out body;
"""
    return await _post_with_retry(query)


async def fetch_parent_ways(node_ids: list[int]) -> dict[int, list[dict]]:
    """ノードが属する highway way を引く（明細で親道路種別を確認するため）。"""
    if not node_ids:
        return {}
    ids = ",".join(str(node_id) for node_id in node_ids)
    query = f"""[out:json][timeout:120];
node(id:{ids})->.n;
way(bn.n)[highway];
out body;
"""
    elements = await _post_with_retry(query)
    wanted = set(node_ids)
    parents: dict[int, list[dict]] = {}
    for elem in elements:
        way_id = elem.get("id")
        if way_id is None:
            continue
        highway = (elem.get("tags") or {}).get("highway", "")
        for node_id in elem.get("nodes", []):
            if int(node_id) in wanted:
                parents.setdefault(int(node_id), []).append(
                    {"way_id": int(way_id), "highway": highway}
                )
    return parents


async def extract_point_features(label: str, coords: list, radius: int) -> list[dict]:
    elements = await fetch_point_feature_nodes(coords, radius)
    cumulative = _route_cumulative_m(coords)

    rows_by_node: dict[int, list[dict]] = {}
    for elem in elements:
        node_id, lat, lng = elem.get("id"), elem.get("lat"), elem.get("lon")
        if node_id is None or lat is None or lng is None:
            continue
        tags = elem.get("tags") or {}
        dist_m, along_m = _locate_on_route(lat, lng, coords, cumulative)
        for (key, value), feature in _FEATURE_BY_TAG.items():
            if tags.get(key) != value:
                continue
            rows_by_node.setdefault(int(node_id), []).append({
                "label": label,
                "feature_type": feature["feature_type"],
                "osm_key": key,
                "osm_value": value,
                "node_id": int(node_id),
                "lat": f"{lat:.7f}",
                "lng": f"{lng:.7f}",
                "dist_to_route_m": round(dist_m, 2),
                "route_position_m": round(along_m, 1),
                "direction_tag": tags.get("direction", ""),
                "tags_json": json.dumps(tags, ensure_ascii=False,
                                        separators=(",", ":")),
                "street_view_url": _street_view_url(lat, lng),
            })

    parents = await fetch_parent_ways(sorted(rows_by_node))
    rows = []
    for node_id, node_rows in rows_by_node.items():
        parent_ways = parents.get(node_id, [])
        for row in node_rows:
            row["parent_way_ids"] = ";".join(str(w["way_id"]) for w in parent_ways)
            row["parent_way_highways"] = ";".join(w["highway"] for w in parent_ways)
            rows.append(row)
    rows.sort(key=lambda r: (r["route_position_m"], r["node_id"], r["feature_type"]))
    return rows


# ---------------------------------------------------------------------------
# 出力3：収録率検証用の交差点ノード列挙
# ---------------------------------------------------------------------------

async def fetch_corridor_ways(coords: list, radius: int) -> list[dict]:
    """経路コリドー内の highway way を geometry 付きで取得する。"""
    query = f"""[out:json][timeout:180];
way(around:{radius},{_polyline_arg(coords)})[highway];
out body geom;
"""
    return await _post_with_retry(query)


async def fetch_node_tags(node_ids: list[int]) -> dict[int, dict]:
    """交差点ノードのタグ（highway=stop / give_way の有無）を引く。"""
    if not node_ids:
        return {}
    ids = ",".join(str(node_id) for node_id in node_ids)
    query = f"""[out:json][timeout:120];
node(id:{ids});
out body;
"""
    elements = await _post_with_retry(query)
    return {
        int(elem["id"]): (elem.get("tags") or {})
        for elem in elements if elem.get("id") is not None
    }


def _corridor_node_coordinates(elements: list[dict]) -> dict[int, list[float]]:
    coordinates: dict[int, list[float]] = {}
    for elem in elements:
        for node_id, node in zip(elem.get("nodes", []), elem.get("geometry", [])):
            if "lon" in node and "lat" in node:
                coordinates[int(node_id)] = [node["lon"], node["lat"]]
    return coordinates


def _connected_edge_counts(elements: list[dict],
                           node_ids: list[int]) -> dict[int, int]:
    """v4 の two-step と同じ数え方で、非除外 way の接続エッジ数を数える。"""
    counts = {node_id: 0 for node_id in node_ids}
    seen_way_ids: set[int] = set()
    for elem in elements:
        way_id = elem.get("id")
        if way_id is None or int(way_id) in seen_way_ids:
            continue
        seen_way_ids.add(int(way_id))
        if (elem.get("tags") or {}).get("highway", "") in INTERSECTION_EXCLUDED_HIGHWAYS:
            continue
        way_nodes = [int(n) for n in elem.get("nodes", [])]
        for node_id in counts.keys() & set(way_nodes):
            counts[node_id] += _way_edge_contribution(node_id, way_nodes)
    return counts


async def fetch_stop_giveway_nodes(coords: list, radius: int) -> list[dict]:
    """コリドー内の highway=stop / give_way ノードを座標付きで返す。

    OSM の一時停止ノードは交差点ノードそのものではなく、手前の way 中間ノードに
    置かれることが多い。交差点ノードのタグだけを見ると収録済みの標識まで
    「タグなし」に数えてしまうため、近傍ノードとして別列に記録する。
    """
    elements = await fetch_point_feature_nodes(coords, radius)
    nodes = []
    for elem in elements:
        tags = elem.get("tags") or {}
        if tags.get("highway") not in set(_HIGHWAY_VALUES):
            continue
        if elem.get("lat") is None or elem.get("lon") is None:
            continue
        nodes.append({
            "node_id": int(elem["id"]),
            "lat": elem["lat"],
            "lng": elem["lon"],
            "highway": tags["highway"],
        })
    return nodes


async def extract_survey_nodes(label: str, coords: list, radius: int,
                               node_corridor_m: float,
                               nearby_stop_m: float) -> list[dict]:
    elements = await fetch_corridor_ways(coords, radius)
    coordinates = _corridor_node_coordinates(elements)
    cumulative = _route_cumulative_m(coords)

    on_route: dict[int, tuple[float, float]] = {}
    for node_id, (lng, lat) in coordinates.items():
        dist_m, along_m = _locate_on_route(lat, lng, coords, cumulative)
        if dist_m <= node_corridor_m:
            on_route[node_id] = (dist_m, along_m)

    edge_counts = _connected_edge_counts(elements, list(on_route))
    intersection_ids = [node_id for node_id, count in edge_counts.items() if count >= 3]
    tags_by_node = await fetch_node_tags(sorted(intersection_ids))
    stop_nodes = await fetch_stop_giveway_nodes(coords, radius)

    rows = []
    for node_id in intersection_ids:
        lng, lat = coordinates[node_id]
        nearest = min(
            (
                (_haversine_m([lng, lat], [n["lng"], n["lat"]]), n)
                for n in stop_nodes if n["node_id"] != node_id
            ),
            key=lambda pair: pair[0],
            default=(float("inf"), None),
        )
        has_nearby = nearest[1] is not None and nearest[0] <= nearby_stop_m
        rows.append({
            "label": label,
            "node_id": node_id,
            "lat": f"{lat:.7f}",
            "lng": f"{lng:.7f}",
            "highway_tag": tags_by_node.get(node_id, {}).get("highway", ""),
            "connected_edge_count": edge_counts[node_id],
            "street_view_url": _street_view_url(lat, lng),
            "nearby_stop_tag": nearest[1]["highway"] if has_nearby else "",
            "nearby_stop_node_id": nearest[1]["node_id"] if has_nearby else "",
            "nearby_stop_dist_m": round(nearest[0], 2) if has_nearby else "",
            "_along_m": on_route[node_id][1],
        })
    rows.sort(key=lambda r: (r["_along_m"], r["node_id"]))
    for seq, row in enumerate(rows, start=1):
        row["seq"] = seq
        row.pop("_along_m")
    return rows


# ---------------------------------------------------------------------------
# 集計とCSV出力
# ---------------------------------------------------------------------------

def build_summary(labels: list[str], detail_rows: list[dict],
                  radius: int, snapshot_date: str) -> list[dict]:
    summary = []
    for feature in FEATURE_TYPES:
        per_label = {
            label: sum(
                1 for row in detail_rows
                if row["label"] == label
                and row["feature_type"] == feature["feature_type"]
            )
            for label in labels
        }
        detected = sum(per_label.values())
        zero_pairs = sum(1 for count in per_label.values() if count == 0)
        # コリドー内には、経路の道路ではなく交差する側道側に立つ標識ノードも入る。
        # どの距離で「経路上」と読むかは人間が決める判断なので、厳しい側の
        # 読み（垂線距離3m以内）も同じ行に並べて出す。合算はしない。
        per_label_strict = {
            label: sum(
                1 for row in detail_rows
                if row["label"] == label
                and row["feature_type"] == feature["feature_type"]
                and row["dist_to_route_m"] <= STRICT_ON_ROUTE_M
            )
            for label in labels
        }
        detected_strict = sum(per_label_strict.values())
        zero_pairs_strict = sum(1 for count in per_label_strict.values() if count == 0)
        note = ZERO_NOTE if detected == 0 else "検出ノードは出力2の明細に全件記載"
        summary.append({
            "feature_type": feature["feature_type"],
            "osm_tag": f'{feature["osm_key"]}={feature["osm_value"]}',
            "target_description": feature["target"],
            "detected_node_count": detected,
            "pairs_with_detection": len(labels) - zero_pairs,
            "pairs_with_zero_detection": zero_pairs,
            "detected_node_count_within_3m": detected_strict,
            "pairs_with_zero_detection_within_3m": zero_pairs_strict,
            "pairs_total": len(labels),
            "counting_unit": feature["counting_unit"],
            "per_label_counts": ";".join(
                f"{label}:{per_label[label]}" for label in labels
            ),
            "corridor_radius_m": radius,
            "overpass_snapshot_date": snapshot_date,
            "note": f"{note}／{MERGE_CAUTION}",
        })
    return summary


SUMMARY_FIELDS = [
    "feature_type", "osm_tag", "target_description",
    "detected_node_count", "pairs_with_detection", "pairs_with_zero_detection",
    "detected_node_count_within_3m", "pairs_with_zero_detection_within_3m",
    "pairs_total", "counting_unit", "per_label_counts",
    "corridor_radius_m", "overpass_snapshot_date", "note",
]

DETAIL_FIELDS = [
    "label", "feature_type", "osm_key", "osm_value", "node_id",
    "lat", "lng", "dist_to_route_m", "route_position_m",
    "parent_way_ids", "parent_way_highways", "direction_tag",
    "tags_json", "street_view_url",
]

# 指定8列に加えて nearby_stop_* 3列を持つ。OSMの一時停止ノードは交差点ノード
# そのものではなく手前の way 中間ノードに置かれることが多く、highway_tag だけでは
# 収録済みの標識を「タグなし」に数えてしまうため。判定の主軸は highway_tag。
SURVEY_FIELDS = [
    "label", "seq", "node_id", "lat", "lng",
    "highway_tag", "connected_edge_count", "street_view_url",
    "nearby_stop_tag", "nearby_stop_node_id", "nearby_stop_dist_m",
]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    print(f"  書き込み: {path} （{len(rows)}行）")


async def _run_with_retry(label: str, factory, failures: list) -> list | None:
    """1対象ぶんの抽出を再試行付きで実行する。全滅したら failures に積む。

    ペア/試行ごとに asyncio.create_task で ContextVar を分離し、Overpass の
    回路遮断（同一コンテキスト内の全滅フラグ）を後続の対象へ漏らさない。
    """
    error = ""
    for attempt in range(1, PAIR_MAX_ATTEMPTS + 1):
        try:
            return await asyncio.create_task(factory())
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            if attempt < PAIR_MAX_ATTEMPTS:
                print(
                    f"    → 取得失敗（{attempt}/{PAIR_MAX_ATTEMPTS}）、"
                    f"{PAIR_RETRY_WAIT_S:.0f}秒後に再試行: {error}"
                )
                await asyncio.sleep(PAIR_RETRY_WAIT_S)
    failures.append((label, error))
    print(f"  [ERROR] {label}: {error}")
    return None


async def main(args) -> int:
    settings = activate_experiment_overpass_date()
    snapshot_date = settings["overpass_snapshot_date"]
    reset_overpass_usage_stats()
    print(f"実験用Overpass取得時点: {snapshot_date}")
    print(f"入力: {INPUT_CSV}")
    print(
        f"コリドー半径: {args.corridor_m}m / "
        f"出力3のノード採用距離: {args.node_corridor_m}m"
    )

    with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
        input_rows = list(csv.DictReader(f))
    coords_by_label = {}
    for row in input_rows:
        polyline = (row.get("polyline") or "").strip()
        coords_by_label[row["label"]] = decode_polyline(polyline) if polyline else []
    available = list(coords_by_label)

    missing = (set(args.label or []) | set(args.survey_label or [])) - set(available)
    if missing:
        print(f"⚠ 入力ファイルに存在しない label が指定されました: {sorted(missing)}")
        return 1

    if args.all:
        feature_labels = available
    elif args.label:
        feature_labels = [l for l in available if l in set(args.label)]
    else:
        feature_labels = []
    survey_labels = (
        [l for l in available if l in set(args.survey_label)]
        if args.survey_label else []
    )

    failures: list[tuple[str, str]] = []
    detail_rows: list[dict] = []
    survey_rows: list[dict] = []
    requests_done = 0

    if feature_labels:
        print(f"\n[出力1・出力2] 対象 {len(feature_labels)} ペア")
    for label in feature_labels:
        if requests_done and args.interval > 0:
            await asyncio.sleep(args.interval)
        requests_done += 1
        coords = coords_by_label[label]
        if len(coords) < 2:
            failures.append((label, "polyline の座標点が不足"))
            print(f"  [ERROR] {label}: polyline の座標点が不足")
            continue
        rows = await _run_with_retry(
            label, lambda: extract_point_features(label, coords, args.corridor_m),
            failures,
        )
        if rows is None:
            continue
        detail_rows.extend(rows)
        breakdown = {
            feature["feature_type"]: sum(
                1 for r in rows if r["feature_type"] == feature["feature_type"]
            )
            for feature in FEATURE_TYPES
        }
        print(
            f"  {label}: 座標{len(coords)}点 → "
            + " ".join(f"{name}={count}" for name, count in breakdown.items()),
            flush=True,
        )

    if survey_labels:
        print(f"\n[出力3] 対象 {len(survey_labels)} ペア")
    for label in survey_labels:
        if requests_done and args.interval > 0:
            await asyncio.sleep(args.interval)
        requests_done += 1
        coords = coords_by_label[label]
        if len(coords) < 2:
            failures.append((label, "polyline の座標点が不足"))
            print(f"  [ERROR] {label}: polyline の座標点が不足")
            continue
        rows = await _run_with_retry(
            label,
            lambda: extract_survey_nodes(label, coords, args.corridor_m,
                                         args.node_corridor_m, args.nearby_stop_m),
            failures,
        )
        if rows is None:
            continue
        survey_rows.extend(rows)
        tagged = sum(1 for r in rows if r["highway_tag"] in {"stop", "give_way"})
        nearby = sum(1 for r in rows if r["nearby_stop_tag"])
        print(
            f"  {label}: 交差点ノード{len(rows)}件"
            f"（ノード自体が highway=stop/give_way {tagged}件 / "
            f"{args.nearby_stop_m:.0f}m以内に stop/give_way ノードあり {nearby}件）",
            flush=True,
        )

    print(f"\n[実行サマリ] 失敗={len(failures)}件")
    print(format_overpass_usage_summary())
    if failures:
        print("\n⚠ 取得に失敗した対象があるためCSVは1件も出力しません。")
        for label, error in failures:
            print(f"  - {label}: {error}")
        return 1

    summary_rows: list[dict] = []
    if feature_labels:
        summary_rows = build_summary(
            feature_labels, detail_rows, args.corridor_m, snapshot_date,
        )
        print("\n[出力1の内容]")
        for row in summary_rows:
            print(
                f"  {row['feature_type']}（{row['osm_tag']}）: "
                f"検出{row['detected_node_count']}件"
                f"（うち経路から3m以内 {row['detected_node_count_within_3m']}件） / "
                f"検出ゼロのペア {row['pairs_with_zero_detection']}/{row['pairs_total']}"
                f"（3m以内基準では {row['pairs_with_zero_detection_within_3m']}"
                f"/{row['pairs_total']}）"
            )

    if args.dry_run:
        print("\n[dry-run] CSVへの書き込みをスキップしました。")
        return 0

    print()
    if feature_labels:
        if len(feature_labels) != EXPECTED_PAIR_TOTAL:
            print(
                f"⚠ 出力1・出力2は{EXPECTED_PAIR_TOTAL}ペア完走時だけ書き込みます"
                f"（今回の対象は{len(feature_labels)}ペア）。"
            )
            return 1
        write_csv(SUMMARY_CSV, SUMMARY_FIELDS, summary_rows)
        write_csv(DETAIL_CSV, DETAIL_FIELDS, detail_rows)

    if survey_labels:
        write_csv(SURVEY_CSV, SURVEY_FIELDS, survey_rows)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Googleルート上の一時停止・踏切ノードを抽出して集計する",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="抽出のみ実行。CSVは作成しない")
    parser.add_argument("--write", action="store_true",
                        help="CSVを書き込む（出力1・出力2は15ペア完走時のみ）")
    parser.add_argument("--label", action="append", default=None,
                        help="出力1・出力2の対象 label（繰り返し指定可）")
    parser.add_argument("--all", action="store_true",
                        help="出力1・出力2を入力ファイル全件で行う")
    parser.add_argument("--survey-label", action="append", default=None,
                        help="出力3（収録率検証）の対象 label（繰り返し指定可）")
    parser.add_argument("--corridor-m", type=int, default=CORRIDOR_RADIUS_M,
                        help=f"Overpass around のコリドー半径m（既定: {CORRIDOR_RADIUS_M}）")
    parser.add_argument("--node-corridor-m", type=float, default=NODE_ON_ROUTE_M,
                        help=f"出力3で経路上とみなす垂線距離m（既定: {NODE_ON_ROUTE_M}）")
    parser.add_argument("--nearby-stop-m", type=float, default=NEARBY_STOP_M,
                        help="出力3で交差点の近傍とみなす stop/give_way ノードの距離m"
                             f"（既定: {NEARBY_STOP_M}）")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL_S,
                        help=f"対象間の待機秒数（既定: {REQUEST_INTERVAL_S}）")
    args = parser.parse_args()

    if not args.dry_run and not args.write:
        parser.print_help()
        sys.exit(1)
    if args.label and args.all:
        print("⚠ --label と --all は同時に指定できません。")
        sys.exit(1)
    if not args.all and not args.label and not args.survey_label:
        print("⚠ --all / --label / --survey-label のいずれかを指定してください。")
        sys.exit(1)

    sys.exit(asyncio.run(main(args)))
