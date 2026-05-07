import csv
import io
import logging
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from services.route_analyzer import analyze_route

logger = logging.getLogger(__name__)

router = APIRouter()

OD_PAIRS_CSV = Path(__file__).parent.parent / "data" / "od_pairs.csv"

CSV_FIELDNAMES = [
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


class RoutePoint(BaseModel):
    label: str
    road_type: str = ""
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float


class BatchRequest(BaseModel):
    routes: list[RoutePoint]
    algo_version: str = "v3"  # "v1" | "v3"


@router.post("/experiment/batch")
async def batch_experiment(req: BatchRequest):
    """複数ルートの比較データをまとめて返す（JSON + CSV ダウンロード用）"""
    results = []

    for r in req.routes:
        try:
            result = await analyze_route(
                r.origin_lat, r.origin_lng,
                r.dest_lat, r.dest_lng,
                algo_version=req.algo_version,
            )
            violations = result["violations"]
            comp = result["comparison"]
            high_conf = sum(1 for v in violations if v.get("confidence", 0.4) >= 0.7)

            results.append({
                "label": r.label,
                "road_type": r.road_type,
                "algo_version": comp.get("algo_version", req.algo_version),
                "origin_lat": r.origin_lat,
                "origin_lng": r.origin_lng,
                "dest_lat": r.dest_lat,
                "dest_lng": r.dest_lng,
                "original_distance_m": comp["original_distance_m"],
                "compliant_distance_m": comp["compliant_distance_m"],
                "distance_diff_m": comp["distance_diff_m"],
                "distance_diff_pct": comp["distance_diff_pct"],
                "violation_count": comp["violation_count"],
                "violation_count_high_conf": high_conf,
                "violation_count_low_conf": comp["violation_count"] - high_conf,
                "violation_types": ",".join(sorted({v["rule"] for v in violations})),
                "rerouted": comp["rerouted"],
                "error": "",
            })
        except Exception as e:
            results.append({
                "label": r.label,
                "road_type": r.road_type,
                "algo_version": req.algo_version,
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


def _results_to_csv_response(results: list, filename: str = "experiment_results.csv") -> StreamingResponse:
    """結果リストを CSV StreamingResponse に変換する"""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(results)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/experiment/batch/csv")
async def batch_experiment_csv(req: BatchRequest):
    """複数ルートの比較データをCSVファイルとしてダウンロードする"""
    json_resp = await batch_experiment(req)
    return _results_to_csv_response(json_resp["results"])


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
    """od_pairs.csv のプリセット O-D ペアを一括実行して JSON で返す（v3 固定）"""
    routes = _load_od_pairs()
    return await batch_experiment(BatchRequest(routes=routes, algo_version="v3"))


@router.post("/experiment/batch/od-pairs/csv")
async def batch_od_pairs_csv():
    """od_pairs.csv のプリセット O-D ペアを一括実行して CSV でダウンロードする（v3 固定）"""
    routes = _load_od_pairs()
    json_resp = await batch_experiment(BatchRequest(routes=routes, algo_version="v3"))
    return _results_to_csv_response(json_resp["results"])


@router.post("/experiment/batch/od-pairs/compare/csv")
async def batch_od_pairs_compare_csv():
    """同じ O-D ペアを v1 と v3 の両方で実行し、1つの CSV にまとめて返す（論文比較用）"""
    routes = _load_od_pairs()
    v1_resp = await batch_experiment(BatchRequest(routes=routes, algo_version="v1"))
    v3_resp = await batch_experiment(BatchRequest(routes=routes, algo_version="v3"))
    combined = v1_resp["results"] + v3_resp["results"]
    return _results_to_csv_response(combined, filename="experiment_v1_vs_v3.csv")
