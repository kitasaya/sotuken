import asyncio
import logging
import time
from fastapi import APIRouter
from pydantic import BaseModel
from services.graphhopper import get_route
from services.overpass import get_bulk_way_tags
from services.law_checker import (
    _sample,
    check_oneway_violation,
    check_sidewalk_violation,
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

    # ② Overpass を1回だけ呼び出してタグを一括取得し、4チェックで共有する
    points = route_data["paths"][0]["points"]["coordinates"]
    sampled = _sample(points)
    t0 = time.perf_counter()
    try:
        tags_list = await get_bulk_way_tags(sampled)
    except Exception as e:
        logger.warning("Overpass一括取得失敗（チェックをスキップ）: %s", e)
        tags_list = [{} for _ in sampled]
    elapsed_fetch = time.perf_counter() - t0
    logger.info("Overpass一括取得完了: %.1f秒 (%d点)", elapsed_fetch, len(sampled))

    # タグ取得済みなので各チェックは同期的に即完了する
    (
        oneway_violations,
        sidewalk_violations,
        two_step_violations,
        recommendations,
    ) = await asyncio.gather(
        check_oneway_violation(sampled, tags_list),
        check_sidewalk_violation(sampled, tags_list),
        check_two_step_turn(sampled, tags_list),
        check_cycleway_recommendation(sampled, tags_list),
    )
    logger.info("法規チェック完了: oneway=%d sidewalk=%d two_step=%d",
                len(oneway_violations), len(sidewalk_violations), len(two_step_violations))
    violations = oneway_violations + sidewalk_violations + two_step_violations

    # ③ 違反がある場合は法規準拠リルート
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
        },
    }
