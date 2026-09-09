"""タスクJ1の閾値を事後的に根拠づけ、踏切を物理箇所単位にまとめる補助集計。

既存の `r2_point_features_detail_j1.csv`（117行）から集計するだけで、抽出そのものは
やり直さない。判定器・経路探索には触れない。

## 使い方

  # A（距離分布）と B（踏切クラスタリング）だけ。Overpass 不要
  python scripts/analyze_j1_thresholds.py --offline

  # A・B に加えて C（方向適用判定）。Overpass を使う
  python scripts/analyze_j1_thresholds.py

  # クラスタ閾値を明示する（既定は B のギャップ分布から決めた 25m）
  python scripts/analyze_j1_thresholds.py --cluster-gap-m 25

## 各節の目的

  A: `dist_to_route_m` が「経路上のノード群」と「交差道路側のノード群」に
     二峰で分かれ、その谷に 3m があるかを確認する。谷が無ければ 3m は恣意的な
     線引きであり、そう書く。分布を作るのが目的であって、谷を見つけるのが目的ではない。
  B: 1か所の踏切が線路本数・way分割で複数ノードになるため、`route_position_m` の
     近接でまとめて「箇所」数にする。閾値は決め打ちせず、隣接ギャップの分布から決める。
     level_crossing（車道）と crossing（歩道・自転車道）は同一クラスタに入りうる。
  C: on-route と判定した一時停止ノードについて、経路がその標識に向かって進入して
     いるかを判定する。標識を背にして出ていく向きなら適用外。
"""

import argparse
import asyncio
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.experiment_settings import activate_experiment_overpass_date
from services.external_route_scorer import (
    _haversine_m,
    _travel_vector_at,
    _turn_angle_deg,
    decode_polyline,
)
from services.overpass import _post_with_retry

DATA_DIR = Path(__file__).parent.parent / "data"
DETAIL_CSV = DATA_DIR / "r2_point_features_detail_j1.csv"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"

STRICT_ON_ROUTE_M = 3.0
CUMULATIVE_THRESHOLDS = [2, 3, 5, 8, 10, 15, 20]
HISTOGRAM_BIN_M = 0.5
DEFAULT_CLUSTER_GAP_M = 25.0
RAILWAY_FEATURES = ["踏切_車道", "踏切_歩道自転車道"]


def load_detail_rows() -> list[dict]:
    with open(DETAIL_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["dist_to_route_m"] = float(row["dist_to_route_m"])
        row["route_position_m"] = float(row["route_position_m"])
        row["tags"] = json.loads(row["tags_json"])
    return rows


# ---------------------------------------------------------------------------
# A. dist_to_route_m の分布
# ---------------------------------------------------------------------------

def report_distance_distribution(rows: list[dict]) -> None:
    print("=" * 72)
    print("A. dist_to_route_m の分布")
    print("=" * 72)

    for feature_type in sorted({row["feature_type"] for row in rows}):
        selected = [r for r in rows if r["feature_type"] == feature_type]
        distances = sorted(r["dist_to_route_m"] for r in selected)
        print(f"\n## {feature_type}（n={len(selected)}）")

        bins: Counter = Counter()
        for value in distances:
            bins[int(value / HISTOGRAM_BIN_M)] += 1
        print(f"  0.5m刻みヒストグラム（空ビンも表示）")
        max_bin = max(bins) if bins else 0
        for index in range(max_bin + 1):
            low = index * HISTOGRAM_BIN_M
            count = bins.get(index, 0)
            bar = "#" * count
            print(f"    {low:5.1f}-{low + HISTOGRAM_BIN_M:4.1f}m  {count:3d} {bar}")

        print("  累積件数")
        for threshold in CUMULATIVE_THRESHOLDS:
            count = sum(1 for value in distances if value <= threshold)
            share = count / len(distances) * 100 if distances else 0.0
            print(f"    <= {threshold:2d}m : {count:3d} 件 ({share:5.1f}%)")

        gaps = [
            (distances[i + 1] - distances[i], distances[i], distances[i + 1])
            for i in range(len(distances) - 1)
        ]
        gaps.sort(reverse=True)
        print("  値の間隔が大きい箇所 上位5（谷の候補）")
        for gap, low, high in gaps[:5]:
            print(f"    {gap:5.2f}m の空白: {low:5.2f}m と {high:5.2f}m の間")

        print("  parent_way_highways 別の距離帯内訳")
        by_highway: dict[str, Counter] = defaultdict(Counter)
        for row in selected:
            band = (
                "<=3m" if row["dist_to_route_m"] <= 3
                else "3-10m" if row["dist_to_route_m"] <= 10
                else "10-20m"
            )
            by_highway[row["parent_way_highways"] or "(親way不明)"][band] += 1
        for highway, counter in sorted(
            by_highway.items(), key=lambda kv: -sum(kv[1].values())
        ):
            total = sum(counter.values())
            detail = " ".join(
                f"{band}={counter.get(band, 0)}"
                for band in ["<=3m", "3-10m", "10-20m"]
            )
            print(f"    {highway:28s} n={total:3d}  {detail}")


# ---------------------------------------------------------------------------
# B. 踏切のクラスタリング
# ---------------------------------------------------------------------------

def _cluster(label_rows: list[dict], cluster_gap_m: float) -> list[list[dict]]:
    label_rows = sorted(label_rows, key=lambda r: r["route_position_m"])
    clusters: list[list[dict]] = [[label_rows[0]]]
    for row in label_rows[1:]:
        previous = clusters[-1][-1]
        if row["route_position_m"] - previous["route_position_m"] <= cluster_gap_m:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    return clusters


def report_on_route_sensitivity(rows: list[dict], cluster_gap_m: float) -> None:
    """on-route 判定距離を変えたときの箇所数の振れ幅を出す。

    3m は一時停止の距離分布から決めた線であり、踏切の分布から決めたものではない。
    踏切側で何件が閾値の取り方に依存するかを明示する。
    """
    print("\n## on-route 判定距離を変えたときの箇所数（クラスタ閾値は固定）")
    print("    閾値  ノード数  箇所数  検出ペア数")
    for threshold in [2, 3, 5, 8, 10, 20]:
        selected = [
            row for row in rows
            if row["feature_type"] in RAILWAY_FEATURES
            and row["dist_to_route_m"] <= threshold
        ]
        by_label: dict[str, list[dict]] = defaultdict(list)
        for row in selected:
            by_label[row["label"]].append(row)
        clusters = sum(
            len(_cluster(label_rows, cluster_gap_m))
            for label_rows in by_label.values()
        )
        print(
            f"    {threshold:3d}m  {len(selected):7d}  {clusters:6d}  "
            f"{len(by_label):8d}/15"
        )


def report_crossing_clusters(rows: list[dict], cluster_gap_m: float,
                             on_route_m: float) -> None:
    print("\n" + "=" * 72)
    print("B. 踏切の物理箇所単位への統合")
    print("=" * 72)

    on_route = [
        row for row in rows
        if row["feature_type"] in RAILWAY_FEATURES
        and row["dist_to_route_m"] <= on_route_m
    ]
    print(
        f"\non-route（垂線距離{on_route_m:.0f}m以内）のノード: "
        f"{len(on_route)}件 / 全踏切ノード "
        f"{sum(1 for r in rows if r['feature_type'] in RAILWAY_FEATURES)}件"
    )
    for feature_type in RAILWAY_FEATURES:
        count = sum(1 for r in on_route if r["feature_type"] == feature_type)
        print(f"  {feature_type}: {count}件")

    by_label: dict[str, list[dict]] = defaultdict(list)
    for row in on_route:
        by_label[row["label"]].append(row)

    print("\n## 隣接ノード間ギャップ（同一ペア内・route_position_m 差）")
    all_gaps = []
    for label, label_rows in by_label.items():
        label_rows.sort(key=lambda r: r["route_position_m"])
        for i in range(len(label_rows) - 1):
            gap = label_rows[i + 1]["route_position_m"] - label_rows[i]["route_position_m"]
            all_gaps.append((gap, label))
    for gap, label in sorted(all_gaps):
        print(f"    {gap:8.1f}m  ({label})")
    if not all_gaps:
        print("    （ギャップなし。1ペアあたり1ノード以下）")

    report_on_route_sensitivity(rows, cluster_gap_m)

    print(f"\n## クラスタ閾値 {cluster_gap_m:.0f}m での統合結果"
          f"（on-route {on_route_m:.0f}m以内）")
    total_clusters = 0
    composition = Counter()
    for label in sorted(by_label):
        label_rows = sorted(by_label[label], key=lambda r: r["route_position_m"])
        clusters = _cluster(label_rows, cluster_gap_m)
        total_clusters += len(clusters)
        print(f"\n  {label}: ノード{len(label_rows)}件 → {len(clusters)}箇所")
        for index, cluster in enumerate(clusters, start=1):
            types = {row["feature_type"] for row in cluster}
            if types == set(RAILWAY_FEATURES):
                kind = "車道＋歩道"
            elif types == {"踏切_車道"}:
                kind = "車道のみ"
            else:
                kind = "歩道のみ"
            composition[kind] += 1
            span = (
                cluster[-1]["route_position_m"] - cluster[0]["route_position_m"]
            )
            print(
                f"    箇所{index}: ノード{len(cluster)}件 {kind} "
                f"位置{cluster[0]['route_position_m']:.1f}-"
                f"{cluster[-1]['route_position_m']:.1f}m（幅{span:.1f}m） "
                f"node_id={','.join(row['node_id'] for row in cluster)}"
            )

    print(f"\n  合計: {total_clusters}箇所")
    for kind, count in composition.most_common():
        print(f"    {kind}: {count}箇所")


# ---------------------------------------------------------------------------
# C. 一時停止 on-route ノードの方向適用判定
# ---------------------------------------------------------------------------

async def fetch_ways(way_ids: list[int]) -> dict[int, dict]:
    if not way_ids:
        return {}
    ids = ",".join(str(way_id) for way_id in way_ids)
    query = f"""[out:json][timeout:120];
way(id:{ids});
out body geom;
"""
    elements = await _post_with_retry(query)
    return {int(elem["id"]): elem for elem in elements if elem.get("id") is not None}


def _way_forward_vector(way: dict, node_id: int) -> list | None:
    """way の当該ノードにおける forward 方向ベクトル（前後ノードの変位）。"""
    node_ids = [int(n) for n in way.get("nodes", [])]
    geometry = [[n["lon"], n["lat"]] for n in way.get("geometry", [])]
    if node_id not in node_ids or len(geometry) != len(node_ids) or len(geometry) < 2:
        return None
    index = node_ids.index(node_id)
    start = geometry[max(index - 1, 0)]
    end = geometry[min(index + 1, len(geometry) - 1)]
    if start == end:
        return None
    return [end[0] - start[0], end[1] - start[1]]


def _nearest_route_index(coords: list, lat: float, lng: float) -> int:
    return min(
        range(len(coords)),
        key=lambda i: _haversine_m(coords[i], [lng, lat]),
    )


async def report_direction_applicability(rows: list[dict]) -> None:
    print("\n" + "=" * 72)
    print("C. 一時停止 on-route ノードの方向適用判定")
    print("=" * 72)

    targets = [
        row for row in rows
        if row["feature_type"] == "一時停止"
        and row["dist_to_route_m"] <= STRICT_ON_ROUTE_M
    ]
    print(f"\n対象: {len(targets)}件（垂線距離{STRICT_ON_ROUTE_M:.0f}m以内）")

    with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
        polyline_by_label = {r["label"]: r["polyline"] for r in csv.DictReader(f)}
    coords_cache: dict[str, list] = {}

    way_ids = sorted({
        int(way_id)
        for row in targets
        for way_id in row["parent_way_ids"].split(";") if way_id
    })
    ways = await fetch_ways(way_ids)

    no_direction_tag = 0
    verdicts = Counter()
    for row in targets:
        label = row["label"]
        if label not in coords_cache:
            coords_cache[label] = decode_polyline(polyline_by_label[label])
        coords = coords_cache[label]
        lat, lng = float(row["lat"]), float(row["lng"])
        route_index = _nearest_route_index(coords, lat, lng)
        travel_vector = _travel_vector_at(coords, route_index)

        node_id = int(row["node_id"])
        direction_tag = row["tags"].get("direction", "")
        stop_tag = row["tags"].get("stop", "")
        if not direction_tag:
            no_direction_tag += 1

        for way_id_str, highway in zip(
            row["parent_way_ids"].split(";"),
            row["parent_way_highways"].split(";"),
        ):
            if not way_id_str:
                continue
            way = ways.get(int(way_id_str))
            forward = _way_forward_vector(way, node_id) if way else None
            if forward is None:
                deviation = None
                travelling = "判定不能"
            else:
                deviation = _turn_angle_deg(forward, travel_vector)
                travelling = "forward" if deviation < 90 else "backward"

            if travelling == "判定不能":
                verdict = "判定不能"
            elif not direction_tag:
                verdict = "適用（directionタグ無しを両方向として扱った）"
            elif direction_tag == travelling:
                verdict = "適用"
            elif direction_tag in {"forward", "backward"}:
                verdict = "適用外"
            else:
                verdict = f"判定不能（direction={direction_tag}）"
            verdicts[verdict.split("（")[0]] += 1

            print(
                f"\n  node_id={node_id} / direction={direction_tag or 'none'} / "
                f"stop={stop_tag or 'none'} / way={way_id_str}({highway}) / "
                f"偏差角="
                + ("判定不能" if deviation is None else f"{deviation:.1f}度")
                + f" / 経路の進行={travelling} / 判定={verdict}"
            )
            print(f"    {row['street_view_url']}")

    print(f"\n  判定内訳: {dict(verdicts)}")
    print(
        f"  direction タグ無しを両方向として扱った件数: {no_direction_tag}件"
        f" / 対象{len(targets)}件"
    )
    all_stops = [row for row in rows if row["feature_type"] == "一時停止"]
    corridor_no_direction = sum(
        1 for row in all_stops if not row["tags"].get("direction")
    )
    print(
        f"  参考: コリドー20m内の一時停止{len(all_stops)}件のうち "
        f"direction タグ無しは {corridor_no_direction}件"
    )


async def main(args) -> int:
    rows = load_detail_rows()
    print(f"入力: {DETAIL_CSV}（{len(rows)}行）\n")
    report_distance_distribution(rows)
    report_crossing_clusters(rows, args.cluster_gap_m, args.on_route_m)
    if not args.offline:
        activate_experiment_overpass_date()
        await report_direction_applicability(rows)
    else:
        print("\n[offline] C（方向適用判定）はスキップしました。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="J1の距離閾値の根拠づけと踏切クラスタリング",
    )
    parser.add_argument("--offline", action="store_true",
                        help="Overpass を使う C をスキップする")
    parser.add_argument("--cluster-gap-m", type=float, default=DEFAULT_CLUSTER_GAP_M,
                        help=f"踏切クラスタの分割閾値m（既定: {DEFAULT_CLUSTER_GAP_M}）")
    parser.add_argument("--on-route-m", type=float, default=STRICT_ON_ROUTE_M,
                        help=f"経路上と判定する垂線距離m（既定: {STRICT_ON_ROUTE_M}）")
    sys.exit(asyncio.run(main(parser.parse_args())))
