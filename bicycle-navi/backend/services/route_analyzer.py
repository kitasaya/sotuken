import asyncio
import bisect
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


def _trim_geometry(geom: list, p_start: list, p_end: list) -> list:
    """OSM way ジオメトリをルートが通過した区間のノード列にクリップする。
    p_start / p_end に最も近いノードを両端とし、その間のサブリストを返す。
    """
    if len(geom) < 2:
        return geom

    def dist_sq(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

    i_s = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_start))
    i_e = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_end))

    lo, hi = min(i_s, i_e), max(i_s, i_e)
    trimmed = geom[lo: hi + 1]
    return trimmed if len(trimmed) >= 2 else geom


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

    # road_class detail（GH ローカル取得）: Overpass 不要で two_step_turn の primary/secondary 判定に使用
    road_class_details = route_data["paths"][0].get("details", {}).get("road_class", [])
    rc_starts = [int(seg[0]) for seg in road_class_details]

    def _road_class_at(idx: int) -> str:
        """idx を含む road_class セグメントの値を返す。見つからなければ空文字。"""
        k = bisect.bisect_right(rc_starts, idx) - 1
        if k >= 0 and int(road_class_details[k][0]) <= idx <= int(road_class_details[k][1]):
            return str(road_class_details[k][2])
        return ""

    # 二段階右折チェック用：右折 instruction の地点を先に抽出
    instructions = route_data["paths"][0].get("instructions", [])
    two_step_pts: list = []
    two_step_idxs: list = []
    for instr in instructions:
        if instr.get("sign") not in (2, 3):  # TURN_RIGHT / TURN_SHARP_RIGHT のみ
            continue
        interval = instr.get("interval", [])
        if not interval:
            continue
        idx = interval[0]
        two_step_pts.append(points[min(idx, len(points) - 1)])
        two_step_idxs.append(idx)

    # road_class からローカルタグを構築（Overpass 不要・常に利用可能）
    # Overpass が利用できる場合は lanes 情報で補完する
    two_step_local_tags = [{"highway": _road_class_at(idx)} for idx in two_step_idxs]

    t0 = time.perf_counter()
    geometries: list[list] | None = None
    travel_vectors: list[list] | None = None
    way_id_to_data: dict = {}
    two_step_tags_arg: list = []
    two_step_wids: list = []

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
            if not way_id_to_data and unique_way_ids:
                # Overpass が無音で空を返した（全エンドポイント失敗）→ フォールバックへ
                raise RuntimeError("Overpass returned empty for all %d way IDs" % len(unique_way_ids))
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
                geometries.append(_trim_geometry(raw_geom, p_start, p_end))
            # 右折地点のタグを way_id_to_data から解決（二分探索 O(N log M)）
            start_indices = [int(seg[0]) for seg in way_id_details]
            for i, idx in enumerate(two_step_idxs):
                wid = None
                k = bisect.bisect_right(start_indices, idx) - 1
                if k >= 0:
                    s, e, w = int(way_id_details[k][0]), int(way_id_details[k][1]), int(way_id_details[k][2])
                    if s <= idx <= e:
                        wid = w
                # Overpass タグと road_class ローカルタグをマージ（lanes 等 road_class にない情報を補完）
                overpass_tags = way_id_to_data.get(wid, {}).get("tags", {}) if wid else {}
                merged = {**two_step_local_tags[i], **overpass_tags}
                two_step_tags_arg.append(merged)
                two_step_wids.append(wid)
            logger.info("edge_idベース判定: %d ways, %.1f秒", len(unique_way_ids), time.perf_counter() - t0)
        except Exception as e:
            logger.warning("Overpass by-ID取得失敗（road_classローカルタグで代替）: %s", e)
            using_edge_ids = False
            geometries = None
            travel_vectors = None
            # road_class ローカルタグで two_step_turn は継続検出
            two_step_tags_arg = list(two_step_local_tags)
            two_step_wids = []

    if not using_edge_ids:
        sampled = _sample(points)
        combined_pts = sampled + two_step_pts
        overpass_ok = False
        try:
            combined_data = await get_bulk_way_data(combined_pts)
            overpass_ok = True
        except Exception as e:
            logger.warning("Overpass一括取得失敗（road_classローカルタグで継続）: %s", e)
            combined_data = [{"tags": {}, "geometry": []} for _ in combined_pts]
        sampled_data = combined_data[:len(sampled)]
        tags_list = [d["tags"] for d in sampled_data]
        geometries = [d["geometry"] for d in sampled_data]
        if overpass_ok:
            # Overpass が成功した場合のみ road_class ローカルタグと Overpass タグをマージ
            overpass_two_step = [d["tags"] for d in combined_data[len(sampled):]]
            two_step_tags_arg = [
                {**local, **over}
                for local, over in zip(two_step_local_tags, overpass_two_step)
            ]
        elif not two_step_tags_arg:
            # Overpass 失敗かつまだ設定されていない場合: road_class ローカルタグのみ使用
            two_step_tags_arg = list(two_step_local_tags)
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
        check_two_step_turn(two_step_pts, two_step_tags_arg),
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
        if two_step_wids:
            ts_to_wid = {(two_step_pts[j][1], two_step_pts[j][0]): two_step_wids[j]
                         for j in range(min(len(two_step_wids), len(two_step_pts)))}
            for v in two_step_violations:
                v["way_id"] = ts_to_wid.get((v["lat"], v["lng"]))

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
    try:
        tags_list = await get_bulk_way_tags(sampled)
    except Exception as e:
        logger.warning("Overpass一括取得失敗（チェックをスキップ）: %s", e)
        tags_list = [{} for _ in sampled]

    # v1: 進行方向照合なし・右折 instruction 限定なし（全サンプル点を渡す）
    (oneway_violations, two_step_violations, recommendations) = await asyncio.gather(
        check_oneway_violation(sampled, tags_list),  # geometries/travel_vectors なし → confidence=0.4
        check_two_step_turn(sampled, tags_list),     # 全サンプル点 → 過検出する旧挙動
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
            "violation_count": len(violations),
            "violation_types": violation_types,
            "rerouted": rerouted,
            "using_edge_ids": using_edge_ids,
            "algo_version": algo_version,
        },
    }
