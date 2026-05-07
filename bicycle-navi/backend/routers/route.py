import asyncio
import logging
import time
from fastapi import APIRouter
from pydantic import BaseModel
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

router = APIRouter()

class RouteRequest(BaseModel):
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float

@router.post("/route")
async def calculate_route(req: RouteRequest):
    # ① GraphHopper で初期ルート取得
    route_data = await get_route(req.origin_lat, req.origin_lng, req.dest_lat, req.dest_lng)

    # ② Overpass でタグを取得（edge_id ベース → フォールバック: 点ベース）
    points = route_data["paths"][0]["points"]["coordinates"]
    way_id_details = route_data["paths"][0].get("details", {}).get("osm_way_id", [])
    using_edge_ids = bool(way_id_details)

    # ② b) 二段階右折チェック用：右折 instruction の地点を先に抽出（Overpass 呼び出し前）
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

    t0 = time.perf_counter()
    geometries: list[list] | None = None
    travel_vectors: list[list] | None = None
    way_id_to_data: dict = {}
    two_step_tags_arg: list = []

    if using_edge_ids:
        # osm_way_id details: [[start_idx, end_idx, way_id], ...]
        # way_id ごとにルート上の代表座標（区間中点）と区間端点インデックスを記録
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
            check_points = [way_id_info[wid]["point"] for wid in unique_way_ids]
            tags_list = [way_id_to_data.get(wid, {}).get("tags", {}) for wid in unique_way_ids]
            geometries = [way_id_to_data.get(wid, {}).get("geometry", []) for wid in unique_way_ids]
            travel_vectors = []
            for wid in unique_way_ids:
                info = way_id_info[wid]
                p_start = points[info["start_idx"]]
                p_end = points[min(info["end_idx"], len(points) - 1)]
                travel_vectors.append([p_end[0] - p_start[0], p_end[1] - p_start[1]])
            # 右折地点のタグを way_id_to_data から解決
            for idx in two_step_idxs:
                wid = None
                for seg in way_id_details:
                    s, e, w = int(seg[0]), int(seg[1]), int(seg[2])
                    if s <= idx <= e:
                        wid = w
                        break
                two_step_tags_arg.append(way_id_to_data.get(wid, {}).get("tags", {}) if wid else {})
            logger.info("edge_idベース判定: %d ways, %.1f秒", len(unique_way_ids), time.perf_counter() - t0)
        except Exception as e:
            logger.warning("Overpass by-ID取得失敗（点ベースにフォールバック）: %s", e)
            using_edge_ids = False
            geometries = None
            travel_vectors = None
            two_step_tags_arg = []

    if not using_edge_ids:
        sampled = _sample(points)
        # sampled + two_step_pts を1回のクエリでまとめて取得（ジオメトリ付き）
        combined_pts = sampled + two_step_pts
        try:
            combined_data = await get_bulk_way_data(combined_pts)
        except Exception as e:
            logger.warning("Overpass一括取得失敗（チェックをスキップ）: %s", e)
            combined_data = [{"tags": {}, "geometry": []} for _ in combined_pts]
        sampled_data = combined_data[:len(sampled)]
        tags_list = [d["tags"] for d in sampled_data]
        two_step_tags_arg = [d["tags"] for d in combined_data[len(sampled):]]
        # サンプル点ごとの進行方向ベクトルを隣接点から近似（方向照合を有効化）
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

    # ③ 法規チェック（タグ取得済みなので各チェックは即完了する）
    (
        oneway_violations,
        two_step_violations,
        recommendations,
    ) = await asyncio.gather(
        check_oneway_violation(check_points, tags_list, geometries=geometries, travel_vectors=travel_vectors),
        # check_sidewalk_violation はスコープ除外のためコメントアウト
        check_two_step_turn(two_step_pts, two_step_tags_arg),
        check_cycleway_recommendation(check_points, tags_list),
    )
    logger.info("法規チェック完了: oneway=%d two_step=%d (edge_id=%s)",
                len(oneway_violations), len(two_step_violations), using_edge_ids)
    violations = oneway_violations + two_step_violations

    # ④ 違反がある場合は法規準拠リルート
    original_route = route_data["paths"][0]
    rerouted = False
    if violations:
        try:
            compliant_data = await get_compliant_route(
                req.origin_lat, req.origin_lng,
                req.dest_lat, req.dest_lng,
                violations,
            )
            compliant_route = compliant_data["paths"][0]
            rerouted = True
        except Exception as e:
            logger.warning("リルート失敗（元ルートで代替）: %s", e)
            compliant_route = original_route
    else:
        compliant_route = original_route

    orig_dist = original_route.get("distance", 0)
    comp_dist = compliant_route.get("distance", 0)
    diff_m = comp_dist - orig_dist
    diff_pct = round((diff_m / orig_dist * 100), 2) if orig_dist > 0 else 0.0
    violation_types = list({v["rule"] for v in violations})

    return {
        "original_route": original_route,
        "compliant_route": compliant_route,
        "route": compliant_route,          # 後方互換: 既存フロントエンドが参照するキー
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
        },
    }
