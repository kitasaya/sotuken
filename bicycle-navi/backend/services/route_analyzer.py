import asyncio
import logging
import time
from services.graphhopper import get_route
from services.overpass import get_bulk_way_data, get_bulk_way_tags, get_way_tags_by_ids
from services.law_checker import (
    _sample,
    check_oneway_violation,
    check_cycleway_recommendation,
    check_two_step_turn,
)
from services.rerouter import get_compliant_route

logger = logging.getLogger(__name__)


def _extract_right_turns(route_data: dict, points: list) -> tuple[list, list[int]]:
    """GraphHopper instructions から sign=2/3 の地点だけを抽出する。"""
    turn_points: list = []
    turn_indices: list[int] = []
    for instruction in route_data["paths"][0].get("instructions", []):
        if instruction.get("sign") not in (2, 3):
            continue
        interval = instruction.get("interval", [])
        if not interval or not points:
            continue
        index = min(int(interval[0]), len(points) - 1)
        turn_points.append(points[index])
        turn_indices.append(index)
    return turn_points, turn_indices


def _way_ids_at_turn(way_id_details: list, point_index: int) -> tuple[int | None, int | None]:
    """maneuver 点の直前（進入元）と直後（進入先）の OSM way ID を返す。"""
    segments = [(int(s), int(e), int(w)) for s, e, w in way_id_details]
    entry = next((w for s, e, w in reversed(segments) if s < point_index and e == point_index), None)
    exit_ = next((w for s, e, w in segments if s == point_index and e > point_index), None)

    # detail の境界と instruction の点が完全一致しない場合は隣接区間で補う。
    if entry is None:
        entry = next((w for s, e, w in reversed(segments) if s <= point_index - 1 < e), None)
    if exit_ is None:
        exit_ = next((w for s, e, w in segments if s <= point_index < e), None)
    return entry, exit_


def _point_to_segment_dist_sq(point: list, a: list, b: list) -> float:
    """経緯度平面上で点と有限線分の距離二乗を返す（候補弧の比較専用）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        return (point[0] - a[0]) ** 2 + (point[1] - a[1]) ** 2
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denom
    t = max(0.0, min(1.0, t))
    qx, qy = a[0] + t * dx, a[1] + t * dy
    return (point[0] - qx) ** 2 + (point[1] - qy) ** 2


def _arc_fit_score(arc: list, route_segment: list) -> float:
    """ルート座標列が候補弧へどれだけ近いかを距離二乗和で評価する。"""
    if len(arc) < 2:
        return float("inf")
    return sum(
        min(_point_to_segment_dist_sq(point, a, b) for a, b in zip(arc, arc[1:]))
        for point in route_segment
    )


def _trim_geometry(
    geom: list,
    p_start: list,
    p_end: list,
    route_segment: list | None = None,
) -> list:
    """OSM way ジオメトリをルートが通過した区間のノード列にクリップする。
    p_start / p_end に最も近いノードを両端とし、その間のサブリストを返す。
    """
    if len(geom) < 2:
        return geom

    def dist_sq(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

    # 非閉ループは従来ロジックをそのまま維持する。
    if geom[0] != geom[-1] or len(geom) < 4:
        i_s = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_start))
        i_e = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_end))

        lo, hi = min(i_s, i_e), max(i_s, i_e)
        trimmed = geom[lo: hi + 1]
        return trimmed if len(trimmed) >= 2 else geom

    # 閉ループは末尾の重複ノードを除き、始終点間の2つの弧を作る。
    # 実ルート座標列への距離二乗和が小さい弧を採用することで、先頭と末尾が
    # 同一座標でも、実際に通った側のセグメント列を選べる。
    ring = geom[:-1]
    i_s = min(range(len(ring)), key=lambda i: dist_sq(ring[i], p_start))
    i_e = min(range(len(ring)), key=lambda i: dist_sq(ring[i], p_end))

    def forward_arc(start: int, end: int) -> list:
        if start <= end:
            return ring[start:end + 1]
        return ring[start:] + ring[:end + 1]

    arc_start_to_end = forward_arc(i_s, i_e)
    arc_end_to_start = forward_arc(i_e, i_s)
    route_segment = route_segment or [p_start, p_end]
    candidates = [arc for arc in (arc_start_to_end, arc_end_to_start) if len(arc) >= 2]
    if not candidates:
        return geom
    return min(candidates, key=lambda arc: _arc_fit_score(arc, route_segment))


async def analyze_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    *,
    algo_version: str = "v3",  # "v1" | "v3"
) -> dict:
    """
    GraphHopper でルート取得 → Overpass でタグ取得 → 法規チェック → リルート までを実行し、
    route.py のレスポンスと同じ構造の dict を返す。

    algo_version="v3": edge_id ベース判定 + 進行方向照合 + 右折 instruction 連動（現行 route.py と同じ）
    algo_version="v1": 点ベース判定のみ（改善前の挙動を再現・比較用）
    """
    # ① GraphHopper でルート取得
    route_data = await get_route(origin_lat, origin_lng, dest_lat, dest_lng)

    points = route_data["paths"][0]["points"]["coordinates"]

    if algo_version == "v3":
        result = await _analyze_v3(route_data, points, origin_lat, origin_lng, dest_lat, dest_lng)
    else:
        result = await _analyze_v1(route_data, points, origin_lat, origin_lng, dest_lat, dest_lng)

    return result


# ---------------------------------------------------------------------------
# v3: edge_id ベース判定（現行 route.py と完全に同じロジック）
# ---------------------------------------------------------------------------

async def _analyze_v3(route_data, points, origin_lat, origin_lng, dest_lat, dest_lng):
    way_id_details = route_data["paths"][0].get("details", {}).get("osm_way_id", [])
    using_edge_ids = bool(way_id_details)

    # 二段階右折チェック用：右折 instruction の地点を先に抽出
    two_step_pts, two_step_idxs = _extract_right_turns(route_data, points)
    turn_way_pairs = [_way_ids_at_turn(way_id_details, idx) for idx in two_step_idxs]
    two_step_entry_wids = [pair[0] for pair in turn_way_pairs]
    two_step_exit_wids = [pair[1] for pair in turn_way_pairs]

    t0 = time.perf_counter()
    geometries: list[list] | None = None
    travel_vectors: list[list] | None = None
    way_id_to_data: dict = {}

    if using_edge_ids:
        way_id_info: dict[int, dict] = {}
        for seg in way_id_details:
            start_idx, end_idx, wid = int(seg[0]), int(seg[1]), int(seg[2])
            if wid not in way_id_info:
                mid_idx = (start_idx + end_idx) // 2
                way_id_info[wid] = {
                    "point": points[min(mid_idx, len(points) - 1)],
                    "start_idx": start_idx,
                    "end_idx": end_idx,
                }

        unique_way_ids = list(way_id_info.keys())
        try:
            way_id_to_data = await get_way_tags_by_ids(unique_way_ids)
            missing_way_ids = [wid for wid in unique_way_ids if wid not in way_id_to_data]
            if missing_way_ids:
                raise RuntimeError(
                    "Overpass returned no data for %d/%d way IDs: %s"
                    % (
                        len(missing_way_ids), len(unique_way_ids),
                        ",".join(str(wid) for wid in missing_way_ids[:10]),
                    )
                )
            check_points = [way_id_info[wid]["point"] for wid in unique_way_ids]
            tags_list = [way_id_to_data.get(wid, {}).get("tags", {}) for wid in unique_way_ids]
            geometries = []
            travel_vectors = []
            for wid in unique_way_ids:
                info = way_id_info[wid]
                p_start = points[info["start_idx"]]
                p_end = points[min(info["end_idx"], len(points) - 1)]
                travel_vectors.append([p_end[0] - p_start[0], p_end[1] - p_start[1]])
                raw_geom = way_id_to_data.get(wid, {}).get("geometry", [])
                route_segment = points[info["start_idx"]:min(info["end_idx"], len(points) - 1) + 1]
                geometries.append(_trim_geometry(raw_geom, p_start, p_end, route_segment))
            logger.info("edge_idベース判定: %d ways, %.1f秒", len(unique_way_ids), time.perf_counter() - t0)
        except Exception as e:
            # タグ取得失敗を road_class だけで継続すると、oneway=0 の偽成功になる。
            # 呼び出し側でペア全体を ERROR として扱えるよう必ず伝播させる。
            logger.error("Overpass by-ID取得失敗（判定を中止）: %s", e)
            raise

    if not using_edge_ids:
        sampled = _sample(points)
        sampled_data = await get_bulk_way_data(sampled)
        tags_list = [d["tags"] for d in sampled_data]
        geometries = [d["geometry"] for d in sampled_data]
        travel_vectors = []
        for k in range(len(sampled)):
            if k + 1 < len(sampled):
                tv = [sampled[k + 1][0] - sampled[k][0], sampled[k + 1][1] - sampled[k][1]]
            else:
                tv = [sampled[k][0] - sampled[k - 1][0], sampled[k][1] - sampled[k - 1][1]]
            travel_vectors.append(tv)
        check_points = sampled
        logger.info("点ベース判定（フォールバック）: %d点+右折%d点, %.1f秒",
                    len(sampled), len(two_step_pts), time.perf_counter() - t0)

    (oneway_violations, two_step_violations, recommendations) = await asyncio.gather(
        check_oneway_violation(check_points, tags_list, geometries=geometries, travel_vectors=travel_vectors),
        check_two_step_turn(
            two_step_pts,
            entry_way_ids=two_step_entry_wids,
            exit_way_ids=two_step_exit_wids,
        ),
        check_cycleway_recommendation(check_points, tags_list),
    )
    logger.info("法規チェック完了(v3): oneway=%d two_step=%d (edge_id=%s)",
                len(oneway_violations), len(two_step_violations), using_edge_ids)

    # edge_id モード時は violations に way_id を付与（ground truth 評価のマッチングに使用）
    if using_edge_ids:
        cp_to_wid = {(check_points[i][1], check_points[i][0]): unique_way_ids[i]
                     for i in range(len(unique_way_ids))}
        for v in oneway_violations:
            v["way_id"] = cp_to_wid.get((v["lat"], v["lng"]))
        for v in two_step_violations:
            # 従来の ground-truth 照合用 way_id は進入先 way とする。
            v["way_id"] = v.get("exit_way_id")

    violations = oneway_violations + two_step_violations

    return await _build_response(
        route_data, violations, recommendations,
        origin_lat, origin_lng, dest_lat, dest_lng,
        using_edge_ids=using_edge_ids,
        algo_version="v3",
    )


# ---------------------------------------------------------------------------
# v1: 点ベース判定のみ（改善前の挙動を再現）
# ---------------------------------------------------------------------------

async def _analyze_v1(route_data, points, origin_lat, origin_lng, dest_lat, dest_lng):
    sampled = _sample(points)
    tags_list = await get_bulk_way_tags(sampled)
    two_step_pts, _ = _extract_right_turns(route_data, points)

    # v1: oneway は旧点ベースのまま。二段階右折は全版共通で右折 instruction のみ。
    (oneway_violations, two_step_violations, recommendations) = await asyncio.gather(
        check_oneway_violation(sampled, tags_list),  # geometries/travel_vectors なし → confidence=0.4
        check_two_step_turn(two_step_pts),
        check_cycleway_recommendation(sampled, tags_list),
    )
    logger.info("法規チェック完了(v1): oneway=%d two_step=%d", len(oneway_violations), len(two_step_violations))

    # v1 では confidence を全件 0.4 に上書き
    violations = oneway_violations + two_step_violations
    for v in violations:
        v["confidence"] = 0.4

    return await _build_response(
        route_data, violations, recommendations,
        origin_lat, origin_lng, dest_lat, dest_lng,
        using_edge_ids=False,
        algo_version="v1",
    )


# ---------------------------------------------------------------------------
# 共通: レスポンス組み立て + リルート
# ---------------------------------------------------------------------------

async def _build_response(
    route_data, violations, recommendations,
    origin_lat, origin_lng, dest_lat, dest_lng,
    *, using_edge_ids: bool, algo_version: str,
) -> dict:
    original_route = route_data["paths"][0]
    # two_step_turn は走行手順の指示であり経路変更不要。oneway のみリルート対象とする
    reroute_violations = [v for v in violations if v["rule"] == "oneway"]
    rerouted = False
    if reroute_violations:
        try:
            compliant_data = await get_compliant_route(
                origin_lat, origin_lng, dest_lat, dest_lng, reroute_violations,
            )
            compliant_route = compliant_data["paths"][0]
            rerouted = True
        except Exception as e:
            logger.warning("リルート失敗（元ルートで代替）: %s", e)
            compliant_route = original_route
    else:
        compliant_route = original_route

    # 各 violation に「リルートの原因になったか」フラグを付与（F2 フロント差別化用）
    for v in violations:
        v["triggered_reroute"] = (v["rule"] == "oneway" and rerouted)

    orig_dist = original_route.get("distance", 0)
    comp_dist = compliant_route.get("distance", 0)
    diff_m = comp_dist - orig_dist
    diff_pct = round((diff_m / orig_dist * 100), 2) if orig_dist > 0 else 0.0
    violation_types = list({v["rule"] for v in violations})
    oneway_violation_count = sum(v["rule"] == "oneway" for v in violations)
    two_step_required_intersections = sum(v["rule"] == "two_step_turn" for v in violations)

    return {
        "original_route": original_route,
        "compliant_route": compliant_route,
        "route": compliant_route,
        "violations": violations,
        "compliant": len(violations) == 0,
        "recommendations": recommendations,
        "rerouted": rerouted,
        "comparison": {
            "original_distance_m": round(orig_dist, 1),
            "compliant_distance_m": round(comp_dist, 1),
            "distance_diff_m": round(diff_m, 1),
            "distance_diff_pct": diff_pct,
            # 後方互換フィールド。ただし集計対象は経路除外に関わる oneway のみ。
            "violation_count": oneway_violation_count,
            "oneway_violation_count": oneway_violation_count,
            "two_step_required_intersections": two_step_required_intersections,
            "violation_types": violation_types,
            "rerouted": rerouted,
            "using_edge_ids": using_edge_ids,
            "algo_version": algo_version,
        },
    }
