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
from services.overpass import (
    _M_PER_DEG_LAT,
    _M_PER_DEG_LNG_EQUATOR,
    _post_with_retry,
    get_bulk_way_data,
)

DATA_DIR = Path(__file__).parent.parent / "data"
DETAIL_CSV = DATA_DIR / "r2_point_features_detail_j1.csv"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"

STRICT_ON_ROUTE_M = 3.0
CORRIDOR_RADIUS_M = 20        # J1 抽出と同じコリドー半径
CROSSING_NODE_MATCH_M = 30.0  # F で踏切ノードを対応づける半径
RAIL_VALUES = ("rail", "light_rail", "narrow_gauge")
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


# ---------------------------------------------------------------------------
# F. 経路と鉄道線の幾何交差
# ---------------------------------------------------------------------------

def _plane_scales(lat: float) -> tuple[float, float]:
    return _M_PER_DEG_LNG_EQUATOR * math.cos(math.radians(lat)), _M_PER_DEG_LAT


def _route_cumulative_m(coords: list) -> list[float]:
    """始点からの累積距離。`extract_point_features.py` と同じ定義。"""
    cumulative = [0.0]
    for i in range(len(coords) - 1):
        cumulative.append(cumulative[-1] + _haversine_m(coords[i], coords[i + 1]))
    return cumulative


async def fetch_railway_ways(coords: list, radius: int) -> tuple[list, list]:
    """コリドー内の鉄道wayを取得する。rail系とtramを分けて返す。

    tram は路面電車で踏切ではないため対象外だが、件数を報告するために取得する。
    """
    polyline = ",".join(f"{lat:.6f},{lng:.6f}" for lng, lat in coords)
    rail_re = "|".join(RAIL_VALUES)
    query = f"""[out:json][timeout:180];
(
  way(around:{radius},{polyline})["railway"~"^({rail_re})$"];
  way(around:{radius},{polyline})["railway"="tram"];
);
out body geom;
"""
    elements = await _post_with_retry(query)
    rails, trams = [], []
    for elem in elements:
        railway = (elem.get("tags") or {}).get("railway", "")
        if railway in RAIL_VALUES:
            rails.append(elem)
        elif railway == "tram":
            trams.append(elem)
    return rails, trams


def _segment_intersection(a1: list, a2: list, b1: list, b2: list,
                          kx: float, ky: float) -> tuple[float, list] | None:
    """線分 a1-a2 と b1-b2 の交点。局所平面近似（数十m規模なので十分）。

    戻り値: (a 側のパラメータ t, 交点 [lng, lat])。交わらなければ None。
    """
    ax, ay = 0.0, 0.0
    bx, by = (a2[0] - a1[0]) * kx, (a2[1] - a1[1]) * ky
    cx, cy = (b1[0] - a1[0]) * kx, (b1[1] - a1[1]) * ky
    dx, dy = (b2[0] - a1[0]) * kx, (b2[1] - a1[1]) * ky

    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denominator = rx * sy - ry * sx
    if abs(denominator) < 1e-9:
        return None  # 平行または退化。接している場合も交点なしとして扱う
    t = ((cx - ax) * sy - (cy - ay) * sx) / denominator
    u = ((cx - ax) * ry - (cy - ay) * rx) / denominator
    if not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    return t, [a1[0] + t * (a2[0] - a1[0]), a1[1] + t * (a2[1] - a1[1])]


def _layer_value(tags: dict) -> tuple[int | None, bool]:
    """layer の整数値と、解釈できたかどうかを返す。未指定は 0 扱い。"""
    raw = tags.get("layer")
    if raw is None:
        return 0, True
    try:
        return int(str(raw).strip()), True
    except ValueError:
        return None, False


def _is_structure(tags: dict) -> bool:
    for key in ("bridge", "tunnel"):
        value = str(tags.get(key, "no")).strip().lower()
        if value not in {"no", ""}:
            return True
    return False


def classify_crossing(road_tags: dict | None, rail_tags: dict) -> tuple[str, str]:
    """平面交差 / 立体交差 / 不明 を返す。判断がつかないものは平面に寄せない。

    戻り値: (区分, 理由)
    """
    if road_tags is None:
        return "不明", "道路側wayを同定できない"
    if _is_structure(road_tags) or _is_structure(rail_tags):
        return "立体交差", "bridge/tunnel あり"
    road_layer, road_ok = _layer_value(road_tags)
    rail_layer, rail_ok = _layer_value(rail_tags)
    if not road_ok or not rail_ok:
        return "不明", "layer の値を解釈できない"
    if road_layer != rail_layer:
        return "立体交差", f"layer 不一致 ({road_layer} vs {rail_layer})"
    if road_layer != 0:
        return "不明", f"layer が両側とも {road_layer} で bridge/tunnel 無し"
    return "平面交差", "両側とも layer 未指定・bridge/tunnel 無し"


async def fetch_crossing_nodes(points: list, radius: float) -> list[dict]:
    """交差点周辺の踏切ノードを取得する。距離は呼び出し側で全件記録する。"""
    if not points:
        return []
    parts = "\n".join(
        f'  node(around:{radius:.0f},{lat:.7f},{lng:.7f})'
        '["railway"~"^(level_crossing|crossing)$"];'
        for lng, lat in points
    )
    query = f"""[out:json][timeout:120];
(
{parts}
);
out body;
"""
    return await _post_with_retry(query)


async def compute_geometric_crossings(label: str, coords: list,
                                      cumulative: list[float],
                                      road_way_resolver=None) -> dict:
    """経路と鉄道線の幾何交差を求める。

    road_way_resolver: 経路セグメント index から道路側 way_id を返す関数。
      自システム経路では GraphHopper の `osm_way_id` detail で親wayが直接わかるため
      これを渡す。Google 経路には way_id が無いので None のまま、点-曲線垂直距離
      マッチ（get_bulk_way_data）で同定する。**この非対称は親wayの特定方法だけで、
      平面/立体の判定基準（bridge/tunnel/layer）は両者で同一である。**
    """
    rails, trams = await fetch_railway_ways(coords, CORRIDOR_RADIUS_M)
    kx, ky = _plane_scales(coords[len(coords) // 2][1])

    raw: list[dict] = []
    for rail in rails:
        rail_geometry = [[n["lon"], n["lat"]] for n in rail.get("geometry", [])]
        rail_tags = rail.get("tags") or {}
        for i in range(len(coords) - 1):
            for j in range(len(rail_geometry) - 1):
                hit = _segment_intersection(
                    coords[i], coords[i + 1],
                    rail_geometry[j], rail_geometry[j + 1], kx, ky,
                )
                if hit is None:
                    continue
                t, point = hit
                position = cumulative[i] + t * (cumulative[i + 1] - cumulative[i])
                raw.append({
                    "label": label,
                    "point": point,
                    "route_index": i,
                    "route_position_m": position,
                    "rail_way_id": int(rail["id"]),
                    "rail_railway": rail_tags.get("railway", ""),
                    "rail_tags": rail_tags,
                })
    raw.sort(key=lambda r: r["route_position_m"])

    if raw and road_way_resolver is not None:
        # 自システム経路：GraphHopper の osm_way_id detail で親wayが直接わかる。
        resolved = [road_way_resolver(row["route_index"]) for row in raw]
        way_elements = await fetch_ways(sorted({w for w in resolved if w}))
        for row, way_id in zip(raw, resolved):
            tags = (way_elements.get(way_id) or {}).get("tags") if way_id else None
            row["road_way_id"] = way_id
            row["road_tags"] = tags or None
            row["road_highway"] = (tags or {}).get("highway", "")
            row["road_match_dist_m"] = None
            row["verdict"], row["verdict_reason"] = classify_crossing(
                row["road_tags"], row["rail_tags"],
            )
    elif raw:
        # Google 経路：way_id が無いため、点-曲線垂直距離マッチで同定する。
        # 進行方向は使わない（way の選択に方向を持ち込まない制約に従う）。
        matches = await get_bulk_way_data([r["point"] for r in raw])
        for row, match in zip(raw, matches):
            row["road_way_id"] = match.get("match_way_id")
            row["road_tags"] = match.get("tags") or None
            row["road_highway"] = (match.get("tags") or {}).get("highway", "")
            row["road_match_dist_m"] = match.get("match_dist_m")
            row["verdict"], row["verdict_reason"] = classify_crossing(
                row["road_tags"], row["rail_tags"],
            )

    planar = [r for r in raw if r["verdict"] == "平面交差"]
    if planar:
        nodes = await fetch_crossing_nodes(
            [r["point"] for r in planar], CROSSING_NODE_MATCH_M,
        )
        for row in planar:
            nearest = min(
                (
                    (_haversine_m(row["point"], [node["lon"], node["lat"]]), node)
                    for node in nodes
                ),
                key=lambda pair: pair[0],
                default=(float("inf"), None),
            )
            if nearest[1] is None or nearest[0] > CROSSING_NODE_MATCH_M:
                row["nearest_node_id"] = None
                row["nearest_node_dist_m"] = None
                row["nearest_node_railway"] = ""
            else:
                row["nearest_node_id"] = int(nearest[1]["id"])
                row["nearest_node_dist_m"] = round(nearest[0], 2)
                row["nearest_node_railway"] = (
                    (nearest[1].get("tags") or {}).get("railway", "")
                )

    return {
        "label": label,
        "rail_way_count": len(rails),
        "tram_way_count": len(trams),
        "crossings": raw,
        "planar": planar,
    }


def _street_view_url(point: list) -> str:
    return (
        "https://www.google.com/maps/@?api=1&map_action=pano"
        f"&viewpoint={point[1]:.7f},{point[0]:.7f}"
    )


def _cluster_positions(positions: list[float], gap_m: float) -> list[list[float]]:
    if not positions:
        return []
    positions = sorted(positions)
    clusters = [[positions[0]]]
    for value in positions[1:]:
        if value - clusters[-1][-1] <= gap_m:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


async def report_geometric_crossings(rows: list[dict], args) -> list[str]:
    print("\n" + "=" * 72)
    print("F. 経路と鉄道線の幾何交差")
    print("=" * 72)

    with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
        input_rows = list(csv.DictReader(f))

    lines: list[str] = ["## F. 経路と鉄道線の幾何交差", ""]
    lines.append("| ペア | 鉄道way | tram way | 交差点 | 平面 | 立体 | 不明 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")

    results = []
    for index, input_row in enumerate(input_rows):
        label = input_row["label"]
        coords = decode_polyline(input_row["polyline"])
        cumulative = _route_cumulative_m(coords)
        if index > 0:
            await asyncio.sleep(args.interval)
        # ペア/試行ごとに ContextVar を分離し、Overpass の回路遮断を後続へ漏らさない。
        result = None
        for attempt in range(1, 3):
            try:
                result = await asyncio.create_task(
                    compute_geometric_crossings(label, coords, cumulative)
                )
                break
            except Exception as e:
                print(f"    → 取得失敗（{attempt}/2）: {type(e).__name__}: {e}")
                if attempt < 2:
                    await asyncio.sleep(5.0)
        if result is None:
            raise RuntimeError(f"{label}: 鉄道wayの取得に失敗。F は中断する")
        results.append(result)
        counts = Counter(r["verdict"] for r in result["crossings"])
        print(
            f"  {label}: 鉄道way={result['rail_way_count']} "
            f"tram={result['tram_way_count']} 交差={len(result['crossings'])} "
            f"平面={counts.get('平面交差', 0)} 立体={counts.get('立体交差', 0)} "
            f"不明={counts.get('不明', 0)}",
            flush=True,
        )
        lines.append(
            f"| {label} | {result['rail_way_count']} | {result['tram_way_count']} | "
            f"{len(result['crossings'])} | {counts.get('平面交差', 0)} | "
            f"{counts.get('立体交差', 0)} | {counts.get('不明', 0)} |"
        )

    total = Counter()
    for result in results:
        total.update(r["verdict"] for r in result["crossings"])
    lines.append(
        f"| **合計** | | {sum(r['tram_way_count'] for r in results)} | "
        f"{sum(len(r['crossings']) for r in results)} | "
        f"{total.get('平面交差', 0)} | {total.get('立体交差', 0)} | "
        f"{total.get('不明', 0)} |"
    )

    lines += ["", "### F-1. 平面交差の明細", ""]
    lines.append(
        "| ペア | 位置(m) | 鉄道way | 道路way (highway) | "
        "最寄り踏切ノード | 距離(m) | railway | Street View |"
    )
    lines.append("|---|---:|---:|---|---|---:|---|---|")
    for result in results:
        for row in result["planar"]:
            node_id = row.get("nearest_node_id")
            distance = row.get("nearest_node_dist_m")
            lines.append(
                f"| {row['label']} | {row['route_position_m']:.1f} | "
                f"{row['rail_way_id']} | {row['road_way_id']} "
                f"({row['road_highway']}) | "
                f"{node_id if node_id else '**なし**'} | "
                f"{distance if distance is not None else '-'} | "
                f"{row.get('nearest_node_railway', '') or '-'} | "
                f"[SV]({_street_view_url(row['point'])}) |"
            )

    # 交差した道路側 way の highway で分類する。railway のタグ値では分類しない。
    by_highway = Counter(
        row["road_highway"] for result in results for row in result["planar"]
    )
    lines += ["", "### F-2. 道路側 highway による分類（診断用）", ""]
    lines.append("| 道路側 highway | 平面交差数 |")
    lines.append("|---|---:|")
    for highway, count in by_highway.most_common():
        lines.append(f"| {highway or '(不明)'} | {count} |")

    lines += ["", "### F-3. B の8箇所との対応", ""]
    b_rows = [
        row for row in rows
        if row["feature_type"] in RAILWAY_FEATURES
        and row["dist_to_route_m"] <= args.on_route_m
    ]
    b_by_label: dict[str, list[float]] = defaultdict(list)
    for row in b_rows:
        b_by_label[row["label"]].append(row["route_position_m"])

    lines.append("| ペア | F の平面交差(箇所) | B の箇所 | 対応 |")
    lines.append("|---|---:|---:|---|")
    only_f: list[str] = []
    only_b: list[str] = []
    for result in results:
        label = result["label"]
        f_clusters = _cluster_positions(
            [row["route_position_m"] for row in result["planar"]], args.cluster_gap_m,
        )
        b_clusters = _cluster_positions(b_by_label.get(label, []), args.cluster_gap_m)
        if not f_clusters and not b_clusters:
            continue
        f_centres = [sum(c) / len(c) for c in f_clusters]
        b_centres = [sum(c) / len(c) for c in b_clusters]
        matched_b = set()
        for centre in f_centres:
            hit = next(
                (
                    i for i, other in enumerate(b_centres)
                    if i not in matched_b and abs(other - centre) <= args.match_m
                ),
                None,
            )
            if hit is None:
                only_f.append(f"{label} 位置{centre:.1f}m")
            else:
                matched_b.add(hit)
        for i, centre in enumerate(b_centres):
            if i not in matched_b:
                only_b.append(f"{label} 位置{centre:.1f}m")
        lines.append(
            f"| {label} | {len(f_clusters)} | {len(b_clusters)} | "
            f"一致{len(matched_b)} |"
        )

    lines += ["", f"- F にあって B に無い: {len(only_f)}件"]
    for item in only_f:
        lines.append(f"  - {item}")
    lines += [f"- B にあって F に無い: {len(only_b)}件"]
    for item in only_b:
        lines.append(f"  - {item}")

    print(f"\n  平面={total.get('平面交差', 0)} 立体={total.get('立体交差', 0)} "
          f"不明={total.get('不明', 0)}")
    print(f"  F のみ={len(only_f)}件 / B のみ={len(only_b)}件")
    for item in only_f:
        print(f"    F のみ: {item}")
    for item in only_b:
        print(f"    B のみ: {item}")
    return lines


# ---------------------------------------------------------------------------
# G. 一時停止コリドー全件の方向適用判定（診断）
# ---------------------------------------------------------------------------

async def report_all_stop_direction(rows: list[dict]) -> list[str]:
    """コリドー20m内の一時停止全件に方向適用判定を広げる。

    **この判定は診断のみに使う。** way の選択・候補の絞り込みには一切用いない
    （測定対象が進行方向そのものであるため、方向で候補を選ぶと循環論法になる。
    `RESEARCH.md` 21.11節）。3m 閾値の外に、経路に適用される向きの標識が
    残っていないかを目視確認する候補として出すだけである。
    """
    print("\n" + "=" * 72)
    print("G. 一時停止コリドー全件の方向適用判定（診断）")
    print("=" * 72)

    targets = sorted(
        (row for row in rows if row["feature_type"] == "一時停止"),
        key=lambda r: r["dist_to_route_m"],
    )
    with open(INPUT_CSV, encoding="utf-8-sig", newline="") as f:
        polyline_by_label = {r["label"]: r["polyline"] for r in csv.DictReader(f)}
    coords_cache: dict[str, list] = {}
    way_ids = sorted({
        int(way_id)
        for row in targets
        for way_id in row["parent_way_ids"].split(";") if way_id
    })
    ways = await fetch_ways(way_ids)

    lines = [
        "## G. 一時停止コリドー全件の方向適用判定（診断）", "",
        "| dist(m) | ペア | node_id | direction | 偏差角 | 経路の進行 | 判定 | "
        "親way (highway) | Street View |",
        "|---:|---|---|---|---:|---|---|---|---|",
    ]
    verdicts = Counter()
    beyond_strict_applicable = []
    for row in targets:
        label = row["label"]
        if label not in coords_cache:
            coords_cache[label] = decode_polyline(polyline_by_label[label])
        coords = coords_cache[label]
        lat, lng = float(row["lat"]), float(row["lng"])
        travel_vector = _travel_vector_at(
            coords, _nearest_route_index(coords, lat, lng),
        )
        node_id = int(row["node_id"])
        direction_tag = row["tags"].get("direction", "")

        way_id_str = row["parent_way_ids"].split(";")[0]
        highway = row["parent_way_highways"].split(";")[0]
        way = ways.get(int(way_id_str)) if way_id_str else None
        forward = _way_forward_vector(way, node_id) if way else None
        if forward is None:
            deviation, travelling, verdict = None, "判定不能", "判定不能"
        else:
            deviation = _turn_angle_deg(forward, travel_vector)
            travelling = "forward" if deviation < 90 else "backward"
            if not direction_tag:
                verdict = "適用（タグ無し）"
            elif direction_tag == travelling:
                verdict = "適用"
            elif direction_tag in {"forward", "backward"}:
                verdict = "適用外"
            else:
                verdict = "判定不能"
        verdicts[verdict] += 1
        # 「適用外」も "適用" で始まるため、前方一致では判定できない。
        if verdict != "適用外" and verdict.startswith("適用") \
                and row["dist_to_route_m"] > STRICT_ON_ROUTE_M:
            beyond_strict_applicable.append(row)
        lines.append(
            f"| {row['dist_to_route_m']:.2f} | {label} | {node_id} | "
            f"{direction_tag or 'none'} | "
            + ("-" if deviation is None else f"{deviation:.1f}")
            + f" | {travelling} | {verdict} | {way_id_str} ({highway}) | "
            f"[SV]({row['street_view_url']}) |"
        )

    lines += [
        "",
        f"- 判定内訳: " + " / ".join(
            f"{name} {count}件" for name, count in verdicts.most_common()
        ),
        f"- **3m 超で「適用」と判定された取りこぼし候補: "
        f"{len(beyond_strict_applicable)}件**",
    ]
    print(f"\n  判定内訳: {dict(verdicts)}")
    print(f"  3m超で適用と判定された候補: {len(beyond_strict_applicable)}件")
    bands = Counter()
    for row in beyond_strict_applicable:
        distance = row["dist_to_route_m"]
        bands["3-5m" if distance <= 5 else "5-10m" if distance <= 10 else "10-20m"] += 1
    if bands:
        lines.append(
            "- 距離帯別: " + " / ".join(f"{band} {count}件" for band, count in bands.items())
        )
        print(f"  距離帯別: {dict(bands)}")
    return lines


async def main(args) -> int:
    rows = load_detail_rows()
    print(f"入力: {DETAIL_CSV}（{len(rows)}行）\n")
    report_distance_distribution(rows)
    report_crossing_clusters(rows, args.cluster_gap_m, args.on_route_m)
    if args.offline:
        print("\n[offline] C・F・G はスキップしました。")
        return 0

    activate_experiment_overpass_date()
    markdown: list[str] = []
    if not args.skip_f:
        await report_direction_applicability(rows)
        markdown += await report_geometric_crossings(rows, args) + [""]
    markdown += await report_all_stop_direction(rows)
    if args.emit_md:
        Path(args.emit_md).write_text(
            "\n".join(markdown) + "\n", encoding="utf-8",
        )
        print(f"\nmarkdown を書き出しました: {args.emit_md}")
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
    parser.add_argument("--match-m", type=float, default=30.0,
                        help="F と B の箇所を同一とみなす位置差m（既定: 30）")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="F のペア間待機秒数（既定: 3）")
    parser.add_argument("--skip-f", action="store_true",
                        help="C・F をスキップして G だけ実行する")
    parser.add_argument("--emit-md", default=None,
                        help="F・G の表を markdown として書き出すパス")
    sys.exit(asyncio.run(main(parser.parse_args())))
