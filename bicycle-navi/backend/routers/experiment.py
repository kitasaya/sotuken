import asyncio
import csv
import io
import logging
from pathlib import Path
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

# アルゴリズムバージョン（適用前後の比較用）
ALGO_VERSION = "v3-edge_id+direction+instruction+confidence"
OD_PAIRS_CSV = Path(__file__).parent.parent / "data" / "od_pairs.csv"


class RoutePoint(BaseModel):
    label: str
    road_type: str = ""
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
            high_conf = sum(1 for v in violations if v.get("confidence", 0.4) >= 0.7)

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
                "road_type": r.road_type,
                "algo_version": ALGO_VERSION,
                "origin_lat": r.origin_lat,
                "origin_lng": r.origin_lng,
                "dest_lat": r.dest_lat,
                "dest_lng": r.dest_lng,
                "original_distance_m": round(orig_dist, 1),
                "compliant_distance_m": round(comp_dist, 1),
                "distance_diff_m": round(diff_m, 1),
                "distance_diff_pct": diff_pct,
                "violation_count": len(violations),
                "violation_count_high_conf": high_conf,
                "violation_count_low_conf": len(violations) - high_conf,
                "violation_types": ",".join(sorted({v["rule"] for v in violations})),
                "rerouted": rerouted,
                "error": "",
            })
        except Exception as e:
            results.append({
                "label": r.label,
                "road_type": r.road_type,
                "algo_version": ALGO_VERSION,
                "origin_lat": r.origin_lat,
                "origin_lng": r.origin_lng,
                "dest_lat": r.dest_lat,
                "dest_lng": r.dest_lng,
                "original_distance_m": "",
                "compliant_distance_m": "",
                "distance_diff_m": "",
                "distance_diff_pct": "",
                "violation_count": "",
                "violation_count_high_conf": "",
                "violation_count_low_conf": "",
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
        "road_type",
        "algo_version",
        "origin_lat", "origin_lng",
        "dest_lat", "dest_lng",
        "original_distance_m",
        "compliant_distance_m",
        "distance_diff_m",
        "distance_diff_pct",
        "violation_count",
        "violation_count_high_conf",
        "violation_count_low_conf",
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


def _load_od_pairs() -> list[RoutePoint]:
    """backend/data/od_pairs.csv からプリセット O-D ペアを読み込む"""
    routes = []
    with open(OD_PAIRS_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            routes.append(RoutePoint(
                label=row["label"],
                road_type=row.get("road_type", ""),
                origin_lat=float(row["origin_lat"]),
                origin_lng=float(row["origin_lng"]),
                dest_lat=float(row["dest_lat"]),
                dest_lng=float(row["dest_lng"]),
            ))
    return routes


@router.post("/experiment/batch/od-pairs")
async def batch_od_pairs():
    """od_pairs.csv のプリセット O-D ペアを一括実行して JSON で返す"""
    routes = _load_od_pairs()
    return await batch_experiment(BatchRequest(routes=routes))


@router.post("/experiment/batch/od-pairs/csv")
async def batch_od_pairs_csv():
    """od_pairs.csv のプリセット O-D ペアを一括実行して CSV でダウンロードする"""
    routes = _load_od_pairs()
    return await batch_experiment_csv(BatchRequest(routes=routes))
