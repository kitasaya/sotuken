import csv
import io
import logging
import math
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from services.route_analyzer import analyze_route

logger = logging.getLogger(__name__)

router = APIRouter()

OD_PAIRS_CSV = Path(__file__).parent.parent / "data" / "od_pairs.csv"
GROUND_TRUTH_CSV = Path(__file__).parent.parent / "data" / "ground_truth.csv"
GOOGLE_COMPARISON_CSV = Path(__file__).parent.parent / "data" / "google_comparison.csv"

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


# ---------------------------------------------------------------------------
# Ground truth 比較エンドポイント（タスク R1）
# ---------------------------------------------------------------------------

_PROX_THRESHOLD_M = 300  # 近傍マッチングの距離しきい値（メートル）


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _system_detected(violations: list, rule: str, lat: float, lng: float, way_id: str | None) -> bool:
    """violations リストに対して way_id 一致（v3）または近傍（300m以内）でマッチするか判定する"""
    for v in violations:
        if v.get("rule") != rule:
            continue
        # way_id による完全一致（v3 edge_id モード時）
        if way_id and str(v.get("way_id", "")) == way_id:
            return True
        # 近傍座標マッチング（v1 または way_id 非対応時）
        if _haversine_m(lat, lng, v.get("lat", 0), v.get("lng", 0)) <= _PROX_THRESHOLD_M:
            return True
    return False


def _calc_metrics(tp: int, fp: int, fn: int, tn: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


@router.post("/experiment/ground-truth/compare")
async def ground_truth_compare():
    """ground_truth.csv と v1/v3 の検出結果を照合し、混同行列・Precision/Recall/F1 を CSV で返す。

    confidence >= 0.7 の violations のみを「検出」としてカウントする。
    """
    # ground_truth.csv を読み込む
    ground_truth_rows: list[dict] = []
    with open(GROUND_TRUTH_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ground_truth_rows.append(row)

    # label → OD 座標のマッピング
    od_map = {r.label: r for r in _load_od_pairs()}

    # (label, algo_version) → violations のキャッシュ（重複実行を防ぐ）
    violations_cache: dict[tuple, list] = {}
    unique_labels = list(dict.fromkeys(r["label"] for r in ground_truth_rows))

    for label in unique_labels:
        od = od_map.get(label)
        if od is None:
            logger.warning("ground_truth の label '%s' が od_pairs.csv に存在しません", label)
            continue
        for version in ("v1", "v3"):
            key = (label, version)
            if key in violations_cache:
                continue
            try:
                result = await analyze_route(
                    od.origin_lat, od.origin_lng,
                    od.dest_lat, od.dest_lng,
                    algo_version=version,
                )
                # confidence >= 0.7 のみを「検出済み」とカウント
                violations_cache[key] = [
                    v for v in result["violations"] if v.get("confidence", 0.4) >= 0.7
                ]
            except Exception as e:
                logger.error("ルート実行失敗 label=%s version=%s: %s", label, version, e)
                violations_cache[key] = []

    # (label, algo_version, rule) ごとに TP/FP/FN/TN を集計
    Rules = ("oneway", "two_step_turn")
    rule_col = {"oneway": "true_oneway_violation", "two_step_turn": "true_two_step_required"}
    counters: dict[tuple, dict] = {}

    for row in ground_truth_rows:
        label = row["label"]
        if label not in od_map:
            continue
        way_id = row.get("way_id", "").strip() or None
        try:
            lat = float(row["point_lat"])
            lng = float(row["point_lng"])
        except (ValueError, KeyError):
            continue

        for version in ("v1", "v3"):
            violations = violations_cache.get((label, version), [])
            for rule in Rules:
                key = (label, version, rule)
                if key not in counters:
                    counters[key] = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
                true_val = row.get(rule_col[rule], "false").strip().lower() in ("true", "1", "yes")
                detected = _system_detected(violations, rule, lat, lng, way_id)
                if true_val and detected:
                    counters[key]["TP"] += 1
                elif not true_val and detected:
                    counters[key]["FP"] += 1
                elif true_val and not detected:
                    counters[key]["FN"] += 1
                else:
                    counters[key]["TN"] += 1

    # 出力 CSV の行を組み立て
    output_rows = []
    for (label, version, rule), c in sorted(counters.items()):
        m = _calc_metrics(c["TP"], c["FP"], c["FN"], c["TN"])
        output_rows.append({
            "label": label,
            "algo_version": version,
            "rule": rule,
            **m,
        })

    # 全体集計行（ALL）を algo_version × rule ごとに追加
    for version in ("v1", "v3"):
        for rule in Rules:
            tp = sum(counters.get((lbl, version, rule), {}).get("TP", 0) for lbl in unique_labels)
            fp = sum(counters.get((lbl, version, rule), {}).get("FP", 0) for lbl in unique_labels)
            fn = sum(counters.get((lbl, version, rule), {}).get("FN", 0) for lbl in unique_labels)
            tn = sum(counters.get((lbl, version, rule), {}).get("TN", 0) for lbl in unique_labels)
            m = _calc_metrics(tp, fp, fn, tn)
            output_rows.append({"label": "ALL", "algo_version": version, "rule": rule, **m})

    fieldnames = ["label", "algo_version", "rule", "TP", "FP", "FN", "TN", "precision", "recall", "f1"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(output_rows)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ground_truth_comparison.csv"},
    )


# ---------------------------------------------------------------------------
# Google Maps 手動比較集計エンドポイント（タスク R2）
# ---------------------------------------------------------------------------

@router.post("/experiment/google-comparison/summary")
async def google_comparison_summary():
    """google_comparison.csv を読み込み、本システム vs Google Maps の違反数差分と
    距離差分を集計した CSV を返す。外部 API 呼び出しなし。
    """
    rows: list[dict] = []
    with open(GOOGLE_COMPARISON_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    def _int(val: str, default: int = 0) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _float(val: str, default: float = 0.0) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    per_route_rows = []
    for row in rows:
        google_total = _int(row.get("google_oneway_violation_count", "")) + \
                       _int(row.get("google_two_step_violation_count", ""))
        sys_dist = _float(row.get("system_distance_m", ""))
        goog_dist = _float(row.get("google_distance_m", ""))
        sys_vio = _int(row.get("system_violation_count", ""))
        sys_vio_hc = _int(row.get("system_violation_count_high_conf", ""))
        per_route_rows.append({
            "label": row.get("label", ""),
            "road_type": row.get("road_type", ""),
            "system_distance_m": sys_dist,
            "system_violation_count": sys_vio,
            "system_violation_count_high_conf": sys_vio_hc,
            "google_distance_m": goog_dist,
            "google_total_violation_count": google_total,
            "violation_diff": sys_vio - google_total,  # 正: 本システムが多く検出
            "distance_diff_m": round(sys_dist - goog_dist, 1),
            "route_overlap_pct": _float(row.get("route_overlap_pct", "")),
        })

    def _aggregate(target_rows: list[dict], label: str) -> dict:
        n = len(target_rows)
        if n == 0:
            return {"label": label, "road_type": "（集計）", "n": 0,
                    "mean_system_distance_m": "", "mean_system_violation_count": "",
                    "mean_system_violation_count_high_conf": "",
                    "mean_google_distance_m": "", "mean_google_total_violation_count": "",
                    "mean_violation_diff": "", "mean_distance_diff_m": "",
                    "mean_route_overlap_pct": ""}
        return {
            "label": label,
            "road_type": "（集計）",
            "n": n,
            "mean_system_distance_m": round(sum(r["system_distance_m"] for r in target_rows) / n, 1),
            "mean_system_violation_count": round(sum(r["system_violation_count"] for r in target_rows) / n, 2),
            "mean_system_violation_count_high_conf": round(sum(r["system_violation_count_high_conf"] for r in target_rows) / n, 2),
            "mean_google_distance_m": round(sum(r["google_distance_m"] for r in target_rows) / n, 1),
            "mean_google_total_violation_count": round(sum(r["google_total_violation_count"] for r in target_rows) / n, 2),
            "mean_violation_diff": round(sum(r["violation_diff"] for r in target_rows) / n, 2),
            "mean_distance_diff_m": round(sum(r["distance_diff_m"] for r in target_rows) / n, 1),
            "mean_route_overlap_pct": round(sum(r["route_overlap_pct"] for r in target_rows) / n, 1),
        }

    per_route_fieldnames = [
        "label", "road_type", "system_distance_m", "system_violation_count",
        "system_violation_count_high_conf", "google_distance_m",
        "google_total_violation_count", "violation_diff", "distance_diff_m", "route_overlap_pct",
    ]
    agg_fieldnames = [
        "label", "road_type", "n",
        "mean_system_distance_m", "mean_system_violation_count",
        "mean_system_violation_count_high_conf",
        "mean_google_distance_m", "mean_google_total_violation_count",
        "mean_violation_diff", "mean_distance_diff_m", "mean_route_overlap_pct",
    ]

    output = io.StringIO()

    # ① per-route セクション
    output.write("# per-route\n")
    writer = csv.DictWriter(output, fieldnames=per_route_fieldnames)
    writer.writeheader()
    writer.writerows(per_route_rows)

    # ② 集計セクション
    output.write("\n# aggregate\n")
    agg_writer = csv.DictWriter(output, fieldnames=agg_fieldnames)
    agg_writer.writeheader()
    agg_writer.writerow(_aggregate(per_route_rows, "ALL"))
    road_types = list(dict.fromkeys(r["road_type"] for r in per_route_rows))
    for rt in road_types:
        subset = [r for r in per_route_rows if r["road_type"] == rt]
        agg_writer.writerow(_aggregate(subset, rt))

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=google_comparison_summary.csv"},
    )
