import asyncio
import csv
import io
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from services.graphhopper import get_route
from services.overpass import get_bulk_way_tags
from services.law_checker import (
    _sample,
    check_oneway_violation,
    # check_sidewalk_violation はスコープ除外のためコメントアウト
    check_cycleway_recommendation,
    check_two_step_turn,
)
from services.rerouter import get_compliant_route

logger = logging.getLogger(__name__)

router = APIRouter()


class RoutePoint(BaseModel):
    label: str
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float


class BatchRequest(BaseModel):
    routes: list[RoutePoint]


@router.post("/experiment/batch")
async def batch_experiment(req: BatchRequest):
    """複数ルートの比較データをまとめて返す（JSON + CSV ダウンロード用）"""
    results = []

    for r in req.routes:
        try:
            route_data = await get_route(r.origin_lat, r.origin_lng, r.dest_lat, r.dest_lng)
            points = route_data["paths"][0]["points"]["coordinates"]
            sampled = _sample(points)
            try:
                tags_list = await get_bulk_way_tags(sampled)
            except Exception as e:
                logger.warning("Overpass一括取得失敗（チェックをスキップ）: %s", e)
                tags_list = [{} for _ in sampled]

            (oneway_v, two_step_v, _) = await asyncio.gather(
                check_oneway_violation(sampled, tags_list),
                check_two_step_turn(sampled, tags_list),
                check_cycleway_recommendation(sampled, tags_list),
            )
            violations = oneway_v + two_step_v

            original_route = route_data["paths"][0]
            if violations:
                compliant_data = await get_compliant_route(
                    r.origin_lat, r.origin_lng,
                    r.dest_lat, r.dest_lng,
                    violations,
                )
                compliant_route = compliant_data["paths"][0]
                rerouted = True
            else:
                compliant_route = original_route
                rerouted = False

            orig_dist = original_route.get("distance", 0)
            comp_dist = compliant_route.get("distance", 0)
            diff_m = comp_dist - orig_dist
            diff_pct = round((diff_m / orig_dist * 100), 2) if orig_dist > 0 else 0.0

            results.append({
                "label": r.label,
                "origin_lat": r.origin_lat,
                "origin_lng": r.origin_lng,
                "dest_lat": r.dest_lat,
                "dest_lng": r.dest_lng,
                "original_distance_m": round(orig_dist, 1),
                "compliant_distance_m": round(comp_dist, 1),
                "distance_diff_m": round(diff_m, 1),
                "distance_diff_pct": diff_pct,
                "violation_count": len(violations),
                "violation_types": ",".join(sorted({v["rule"] for v in violations})),
                "rerouted": rerouted,
                "error": "",
            })
        except Exception as e:
            results.append({
                "label": r.label,
                "origin_lat": r.origin_lat,
                "origin_lng": r.origin_lng,
                "dest_lat": r.dest_lat,
                "dest_lng": r.dest_lng,
                "original_distance_m": "",
                "compliant_distance_m": "",
                "distance_diff_m": "",
                "distance_diff_pct": "",
                "violation_count": "",
                "violation_types": "",
                "rerouted": "",
                "error": str(e),
            })

    return {"results": results}


@router.post("/experiment/batch/csv")
async def batch_experiment_csv(req: BatchRequest):
    """複数ルートの比較データをCSVファイルとしてダウンロードする"""
    # JSON と同じロジックで結果を収集
    json_resp = await batch_experiment(req)
    results = json_resp["results"]

    fieldnames = [
        "label",
        "origin_lat", "origin_lng",
        "dest_lat", "dest_lng",
        "original_distance_m",
        "compliant_distance_m",
        "distance_diff_m",
        "distance_diff_pct",
        "violation_count",
        "violation_types",
        "rerouted",
        "error",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=experiment_results.csv"},
    )
