"""バッチ6: 候補way選択が法規判定へ与える影響を監査する。

判定・候補順位・右折抽出は services/ の既存関数を import して使う。
このスクリプトは保存済み8/1 PBFと既存CSVを読み、分析用CSV/PNG/JSONだけを生成する。
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
SITE_PACKAGES = BACKEND_DIR / ".venv" / "Lib" / "site-packages"
sys.path.insert(0, str(BACKEND_DIR))
if SITE_PACKAGES.exists():
    sys.path.insert(0, str(SITE_PACKAGES))

from scripts.analyze_match_margin_deep_dive import bicycle_traversable  # noqa: E402
from scripts.known_violations import KNOWN_VIOLATIONS, group_of  # noqa: E402
from scripts.local_osm_pbf import extract_local_highways  # noqa: E402
from scripts.route_match_probe import Candidate, PointProbe, is_oneway_violation  # noqa: E402
from services.external_route_scorer import (  # noqa: E402
    _extract_right_turn_points,
    _resample_by_distance,
    _travel_vector_at,
    decode_polyline,
)
from services.law_checker import _check_direction, check_two_step_turn  # noqa: E402
from services.overpass import _point_to_way_dist_m, _rank_way_candidates  # noqa: E402


PBF_PATH = BACKEND_DIR.parent / "graphhopper" / "kanto-260801.osm.pbf"
ROUTES_CSV = DATA_DIR / "google_routes_input.csv"
OLD_POINTS_CSV = DATA_DIR / "dryrun_nonroad_filter_points.csv"
FIXED_POINTS_CSV = DATA_DIR / "verify_match_margin_points.csv"
BATCH5_POINTS_CSV = DATA_DIR / "margin_deep_dive_points.csv"
BATCH5_SENSITIVITY_CSV = DATA_DIR / "margin_threshold_sensitivity.csv"

DECISION_POINTS_CSV = DATA_DIR / "decision_ambiguity_points.csv"
DECISION_SENSITIVITY_CSV = DATA_DIR / "decision_ambiguity_sensitivity.csv"
DECISION_SENSITIVITY_PNG = DATA_DIR / "decision_ambiguity_sensitivity.png"
FIRST_AUDIT_CSV = DATA_DIR / "first_candidate_audit.csv"
LAYER2_CSV = DATA_DIR / "decision_ambiguity_layer2.csv"
D1_CSV = DATA_DIR / "dataset_offset_d1_distribution.csv"
SNAPSHOT_CSV = DATA_DIR / "snapshot_candidate_differences.csv"
SUMMARY_JSON = DATA_DIR / "decision_ambiguity_summary.json"

THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0]
ONEWAY_VALUES = {"yes", "true", "1", "-1"}
OPPOSITE_CYCLEWAYS = {"opposite", "opposite_lane", "opposite_track"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"No rows for {path}")
    names = fields or list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def overpass_element(element: dict) -> dict:
    """ローカルPBF要素をproductionの _rank_way_candidates 入力形式へ変換。"""
    return {
        "id": element["id"],
        "tags": element["tags"],
        "geometry": [{"lon": p[0], "lat": p[1]} for p in element["geometry"]],
    }


def candidate(element: dict, dist_m: float) -> Candidate:
    return Candidate(
        way_id=element["id"],
        dist_m=dist_m,
        tags=element["tags"],
        geometry=element["geometry"],
    )


def semantic_oneway(tags: dict) -> str:
    raw = str(tags.get("oneway", "")).strip().lower()
    if raw in {"yes", "true", "1"}:
        return "forward"
    if raw == "-1":
        return "reverse"
    return "none"


def exemption_reason(tags: dict) -> str:
    if tags.get("oneway:bicycle") == "no":
        return "oneway:bicycle=no"
    cycleway = tags.get("cycleway", "")
    if cycleway in OPPOSITE_CYCLEWAYS:
        return f"cycleway={cycleway}"
    return ""


async def oneway_result(probe: PointProbe, cand: Candidate) -> dict:
    detected, misaligned, confidence = await is_oneway_violation(probe, cand)
    tags = cand.tags
    raw_oneway = str(tags.get("oneway", ""))
    normalized = semantic_oneway(tags)
    exempt = exemption_reason(tags)
    direction = "not_applicable"
    if normalized != "none" and len(cand.geometry) >= 2 and any(probe.travel_vector):
        direction = "against" if _check_direction(
            cand.geometry, probe.travel_vector, raw_oneway.strip().lower()
        ) else "with"

    if detected:
        status = "violation"
    elif misaligned:
        status = "out_of_scope_axis_misaligned"
    elif exempt:
        status = "legal_bicycle_exempt"
    elif normalized != "none":
        status = "legal_forward"
    else:
        status = "out_of_scope_no_oneway"

    # バッチ6の定義は `oneway` タグの「値」そのものを比較対象に含める。
    # 空欄と明示的な `no` は法的意味は同じだが、再現性のため生値版を主指標とし、
    # 意味正規化版も副指標として保持する。
    compare_tuple = (status, raw_oneway, direction, exempt)
    semantic_compare_tuple = (status, normalized, direction, exempt)
    return {
        "status": status,
        "violation": detected,
        "misaligned": misaligned,
        "confidence": confidence,
        "oneway_raw": raw_oneway,
        "oneway_semantic": normalized,
        "direction": direction,
        "exemption": exempt,
        "compare_tuple": compare_tuple,
        "semantic_compare_tuple": semantic_compare_tuple,
    }


def route_context(routes: list[dict]) -> tuple[dict, list[tuple[float, float]], list[dict]]:
    contexts = {}
    all_points: list[tuple[float, float]] = []
    right_turn_contexts = []
    for row in routes:
        label = row["label"]
        coords = decode_polyline(row["polyline"])
        sampled_idx = _resample_by_distance(coords, 40.0)
        samples = []
        for sample_idx, route_idx in enumerate(sampled_idx):
            lng, lat = coords[route_idx]
            samples.append({
                "sample_idx": sample_idx,
                "route_idx": route_idx,
                "lng": lng,
                "lat": lat,
                "travel_vector": _travel_vector_at(coords, route_idx),
            })
            all_points.append((lat, lng))
        contexts[label] = {"coords": coords, "sampled_idx": sampled_idx, "samples": samples}
        for point in _extract_right_turn_points(coords, sampled_idx):
            right_turn_contexts.append({"label": label, "lng": point[0], "lat": point[1]})
            all_points.append((point[1], point[0]))
    return contexts, all_points, right_turn_contexts


def pbf_unions(elements: list[dict], contexts: dict) -> dict[str, list[dict]]:
    unions = {}
    for label, ctx in contexts.items():
        points = [(s["lat"], s["lng"]) for s in ctx["samples"]]
        unions[label] = [
            elem for elem in elements
            if any(_point_to_way_dist_m(lat, lng, elem["geometry"]) <= 20.0 for lat, lng in points)
        ]
        print(f"PBF union {label}: {len(unions[label])} ways", flush=True)
    return unions


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def d1_distribution(rows: list[dict[str, str]]) -> list[dict]:
    scopes = {
        "all_379": rows,
        "margin_ge_10m": [r for r in rows if float(r["match_margin_m"]) >= 10.0],
    }
    output = []
    for scope, scoped in scopes.items():
        values = [float(r["match_dist_m"]) for r in scoped]
        metrics = {
            "n": len(values),
            "min": min(values),
            "p25": percentile(values, 0.25),
            "median": percentile(values, 0.50),
            "p75": percentile(values, 0.75),
            "p95": percentile(values, 0.95),
            "max": max(values),
            "mean": sum(values) / len(values),
        }
        for metric, value in metrics.items():
            output.append({
                "scope": scope,
                "metric": metric,
                "value": value if metric == "n" else round(float(value), 3),
            })
    return output


def _font(size: int, bold: bool = False):
    # Windows 候補を先に並べる（既存の出力を変えないため順序は維持）。
    # macOS 候補は Windows 候補がすべて不在のときだけ使われる。
    paths = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
             else "/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for path in paths:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_sensitivity(rows: list[dict]) -> None:
    scale = 2
    width, height = 1200 * scale, 760 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = 150 * scale, 70 * scale, 110 * scale, 120 * scale
    plot_w, plot_h = width - left - right, height - top - bottom

    def xy(x: float, y: float):
        return left + x / 10.0 * plot_w, top + (100.0 - y) / 100.0 * plot_h

    draw.text((width / 2, 45 * scale), "Geometric vs Decision-Impact Ambiguity",
              fill="#172B4D", font=_font(30 * scale, True), anchor="mm")
    for y in range(0, 101, 10):
        x1, yy = xy(0, y)
        x2, _ = xy(10, y)
        draw.line((x1, yy, x2, yy), fill="#D9E2EC", width=2 * scale)
        draw.text((left - 18 * scale, yy), f"{y}%", fill="#52606D", font=_font(15 * scale), anchor="rm")
    for x in (0, 2, 4, 6, 8, 10):
        xx, y1 = xy(x, 0)
        _, y2 = xy(x, 100)
        draw.line((xx, y1, xx, y2), fill="#EEF2F6", width=2 * scale)
        draw.text((xx, top + plot_h + 18 * scale), f"{x:g}", fill="#52606D", font=_font(15 * scale), anchor="ma")
    draw.line((left, top, left, top + plot_h), fill="#334E68", width=3 * scale)
    draw.line((left, top + plot_h, left + plot_w, top + plot_h), fill="#334E68", width=3 * scale)
    draw.text((left + plot_w / 2, height - 38 * scale), "Threshold (m), ambiguous if margin < threshold",
              fill="#334E68", font=_font(18 * scale), anchor="mm")
    draw.text((left, top - 28 * scale), "Rate among all 379 points (%)",
              fill="#334E68", font=_font(18 * scale), anchor="lm")

    series = [
        ("Geometric (current)", "geometric_pct", "#D64545"),
        ("Geometric (filtered)", "filtered_pct", "#147D92"),
        ("Decision-impact tuple", "decision_impact_pct", "#6B46C1"),
    ]
    for _, key, color in series:
        points = [xy(float(r["threshold_m"]), float(r[key])) for r in rows]
        draw.line(points, fill=color, width=5 * scale, joint="curve")
        for px, py in points:
            radius = 6 * scale
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color, outline="white", width=2 * scale)
    lx, ly = left + 40 * scale, top + 35 * scale
    for i, (label, _, color) in enumerate(series):
        yy = ly + i * 38 * scale
        draw.line((lx, yy, lx + 48 * scale, yy), fill=color, width=5 * scale)
        draw.text((lx + 62 * scale, yy), label, fill="#243B53", font=_font(16 * scale), anchor="lm")
    image.resize((1200, 760), Image.Resampling.LANCZOS).save(DECISION_SENSITIVITY_PNG, optimize=True)


async def main() -> int:
    routes = read_csv(ROUTES_CSV)
    old_rows = read_csv(OLD_POINTS_CSV)
    fixed_rows = read_csv(FIXED_POINTS_CSV)
    batch5_rows = read_csv(BATCH5_POINTS_CSV)
    batch5_sensitivity = read_csv(BATCH5_SENSITIVITY_CSV)
    if not (len(old_rows) == len(fixed_rows) == len(batch5_rows) == 379):
        raise RuntimeError("Expected 379 rows in all point datasets")

    contexts, all_points, right_turn_contexts = route_context(routes)
    fixed_by_key = {(r["label"], int(r["sample_idx"])): r for r in fixed_rows}
    old_by_key = {(r["label"], int(r["sample_idx"])): r for r in old_rows}
    batch5_by_key = {(r["label"], int(r["sample_idx"])): r for r in batch5_rows}
    if set(fixed_by_key) != set(old_by_key) or set(fixed_by_key) != set(batch5_by_key):
        raise RuntimeError("Point keys differ across datasets")

    # サンプリング点・進行ベクトルはproduction関数で再構成し、保存CSVと照合する。
    sample_by_key = {}
    for label, ctx in contexts.items():
        for sample in ctx["samples"]:
            key = (label, sample["sample_idx"])
            saved = fixed_by_key[key]
            if abs(sample["lat"] - float(saved["lat"])) > 1e-7 or abs(sample["lng"] - float(saved["lng"])) > 1e-7:
                raise RuntimeError(f"Sample coordinate mismatch: {key}")
            sample_by_key[key] = sample

    elements = extract_local_highways(PBF_PATH, all_points)
    way_by_id = {int(e["id"]): e for e in elements}
    unions = pbf_unions(elements, contexts)

    decision_rows = []
    first_rows = []
    local_pbf_rows = []
    legacy_violations = []
    missing_way_ids = set()

    for key in sorted(fixed_by_key, key=lambda x: (list(contexts).index(x[0]), x[1])):
        label, sample_idx = key
        fixed = fixed_by_key[key]
        old = old_by_key[key]
        b5 = batch5_by_key[key]
        sample = sample_by_key[key]
        probe = PointProbe(
            label=label,
            sample_idx=sample_idx,
            route_idx=int(fixed["route_idx"]),
            lat=float(fixed["lat"]),
            lng=float(fixed["lng"]),
            travel_vector=sample["travel_vector"],
        )

        candidates = []
        for rank, id_field, dist_field in (
            (1, "perp_rank1_way_id", "match_dist_m"),
            (2, "rank2_way_id", "rank2_dist_m"),
        ):
            wid = int(fixed[id_field])
            elem = way_by_id.get(wid)
            if elem is None:
                missing_way_ids.add(wid)
                continue
            candidates.append((rank, candidate(elem, float(fixed[dist_field]))))
        if len(candidates) != 2:
            continue
        c1, c2 = candidates[0][1], candidates[1][1]
        r1 = await oneway_result(probe, c1)
        r2 = await oneway_result(probe, c2)
        tuple_diff = r1["compare_tuple"] != r2["compare_tuple"]
        semantic_tuple_diff = r1["semantic_compare_tuple"] != r2["semantic_compare_tuple"]
        violation_flip = r1["violation"] != r2["violation"]
        margin_m = float(fixed["match_margin_m"])
        decision_rows.append({
            "label": label,
            "sample_idx": sample_idx,
            "route_idx": fixed["route_idx"],
            "lat": fixed["lat"],
            "lng": fixed["lng"],
            "margin_m": margin_m,
            "geometric_ambiguous_2m": margin_m < 2.0,
            "decision_tuple_diff": tuple_diff,
            "semantic_decision_tuple_diff": semantic_tuple_diff,
            "decision_impact_ambiguous_2m": tuple_diff and margin_m < 2.0,
            "violation_flip": violation_flip,
            "violation_flip_ambiguous_2m": violation_flip and margin_m < 2.0,
            "rank1_way_id": c1.way_id,
            "rank1_highway": c1.highway,
            "rank1_oneway": r1["oneway_raw"],
            "rank1_oneway_semantic": r1["oneway_semantic"],
            "rank1_direction": r1["direction"],
            "rank1_exemption": r1["exemption"],
            "rank1_status": r1["status"],
            "rank1_violation": r1["violation"],
            "rank1_misaligned": r1["misaligned"],
            "rank2_way_id": c2.way_id,
            "rank2_highway": c2.highway,
            "rank2_oneway": r2["oneway_raw"],
            "rank2_oneway_semantic": r2["oneway_semantic"],
            "rank2_direction": r2["direction"],
            "rank2_exemption": r2["exemption"],
            "rank2_status": r2["status"],
            "rank2_violation": r2["violation"],
            "rank2_misaligned": r2["misaligned"],
        })

        traversable = bicycle_traversable(c1)
        historical_node_id = int(old["node_rank1_way_id"]) if old["node_rank1_way_id"] else None
        historical_group = group_of(historical_node_id)
        first_rows.append({
            "label": label,
            "sample_idx": sample_idx,
            "lat": fixed["lat"],
            "lng": fixed["lng"],
            "rank1_way_id": c1.way_id,
            "rank1_highway": c1.highway,
            "rank1_bicycle": c1.tags.get("bicycle", ""),
            "rank1_access": c1.tags.get("access", ""),
            "rank1_oneway": c1.tags.get("oneway", ""),
            "rank1_oneway_bicycle": c1.tags.get("oneway:bicycle", ""),
            "rank1_cycleway": c1.tags.get("cycleway", ""),
            "rank1_traversable_by_batch5_filter": traversable,
            "rank1_nontraversable_reason": "" if traversable else (
                f"bicycle={c1.tags.get('bicycle')}" if c1.tags.get("bicycle") in {"no", "use_sidepath"}
                else f"access={c1.tags.get('access')}" if c1.tags.get("access") in {"no", "private"}
                else f"highway={c1.highway}"
            ),
            "current_rank1_violation": r1["violation"],
            "current_rank1_status": r1["status"],
            "historical_node_rank1_way_id": historical_node_id or "",
            "historical_known_group": historical_group,
            "historical_known_detection_location": bool(historical_group),
            "batch5_filtered_rank1_way_id": b5["filtered_rank1_way_id"],
            "batch5_filtered_rank1_highway": b5["filtered_rank1_highway"],
            "batch5_filtered_margin_m": b5["filtered_margin_m"],
            "batch5_nonambiguous_to_ambiguous": (
                b5["match_ambiguous_2m"] == "False" and b5["filtered_ambiguous_2m"] == "True"
            ),
        })

        if historical_node_id and historical_node_id in way_by_id:
            old_cand = candidate(way_by_id[historical_node_id], 0.0)
            old_result = await oneway_result(probe, old_cand)
            if old_result["violation"]:
                legacy_violations.append((key, historical_node_id, historical_group))

        # ローカルPBFのfull-segment候補集合でもproduction順位関数を使う。
        ranked = _rank_way_candidates(
            probe.lat, probe.lng, [overpass_element(e) for e in unions[label]]
        )
        pbf_candidates = []
        for dist, elem in ranked[:2]:
            plain = way_by_id[int(elem["id"])]
            pbf_candidates.append(candidate(plain, dist))
        p1 = await oneway_result(probe, pbf_candidates[0])
        local_pbf_rows.append({
            "key": key,
            "rank1": pbf_candidates[0].way_id,
            "rank2": pbf_candidates[1].way_id,
            "candidate_count": len(ranked),
            "margin": pbf_candidates[1].dist_m - pbf_candidates[0].dist_m,
            "violation": p1["violation"],
        })

    if missing_way_ids:
        raise RuntimeError(f"PBF missing fixed candidate ways: {sorted(missing_way_ids)}")
    if len(decision_rows) != 379:
        raise RuntimeError(f"Expected 379 decision rows, got {len(decision_rows)}")

    write_csv(DECISION_POINTS_CSV, decision_rows)
    write_csv(FIRST_AUDIT_CSV, first_rows)

    # Layer 2: productionの右折抽出・順位・判定をそのまま利用。
    layer2_rows = []
    for n, rt in enumerate(right_turn_contexts):
        label = rt["label"]
        rt_for_label = [x for x in right_turn_contexts if x["label"] == label]
        rt_union = [
            e for e in elements
            if any(_point_to_way_dist_m(x["lat"], x["lng"], e["geometry"]) <= 20.0 for x in rt_for_label)
        ]
        ranked = _rank_way_candidates(
            rt["lat"], rt["lng"], [overpass_element(e) for e in rt_union]
        )
        if len(ranked) < 2:
            continue
        cands = [candidate(way_by_id[int(elem["id"])], dist) for dist, elem in ranked[:2]]
        results = []
        for cand in cands:
            violations = await check_two_step_turn(
                [[rt["lng"], rt["lat"]]], tags_list=[cand.tags]
            )
            results.append(bool(violations))
        layer2_rows.append({
            "label": label,
            "right_turn_index_global": n,
            "lat": round(rt["lat"], 7),
            "lng": round(rt["lng"], 7),
            "margin_m": round(cands[1].dist_m - cands[0].dist_m, 3),
            "rank1_way_id": cands[0].way_id,
            "rank1_highway": cands[0].tags.get("highway", ""),
            "rank1_lanes": cands[0].tags.get("lanes", ""),
            "rank1_two_step_required": results[0],
            "rank2_way_id": cands[1].way_id,
            "rank2_highway": cands[1].tags.get("highway", ""),
            "rank2_lanes": cands[1].tags.get("lanes", ""),
            "rank2_two_step_required": results[1],
            "decision_diff": results[0] != results[1],
            "decision_impact_ambiguous_2m": results[0] != results[1] and (cands[1].dist_m - cands[0].dist_m) < 2.0,
        })
    if layer2_rows:
        write_csv(LAYER2_CSV, layer2_rows)

    # 3系列の感度曲線。系列1・2はバッチ5出力を再掲する。
    b5_sens_by_threshold = {float(r["threshold_m"]): r for r in batch5_sensitivity}
    sensitivity = []
    for threshold in THRESHOLDS:
        decision_count = sum(
            bool(r["decision_tuple_diff"]) and float(r["margin_m"]) < threshold
            for r in decision_rows
        )
        violation_flip_count = sum(
            bool(r["violation_flip"]) and float(r["margin_m"]) < threshold
            for r in decision_rows
        )
        old_s = b5_sens_by_threshold[threshold]
        sensitivity.append({
            "threshold_m": threshold,
            "geometric_count": int(old_s["current_ambiguous_count"]),
            "geometric_pct": float(old_s["current_ambiguous_pct"]),
            "filtered_count": int(old_s["filtered_ambiguous_count"]),
            "filtered_pct": float(old_s["filtered_ambiguous_pct"]),
            "decision_impact_count": decision_count,
            "decision_impact_pct": round(100.0 * decision_count / 379, 2),
            "violation_flip_count": violation_flip_count,
            "violation_flip_pct": round(100.0 * violation_flip_count / 379, 2),
        })
    write_csv(DECISION_SENSITIVITY_CSV, sensitivity)
    draw_sensitivity(sensitivity)

    d1_rows = d1_distribution(fixed_rows)
    write_csv(D1_CSV, d1_rows)

    # 時点差: 7/30 live → 8/1 attic Overpass → 8/1 local PBF full-segment union。
    local_by_key = {r["key"]: r for r in local_pbf_rows}
    snapshot_rows = []
    for key, fixed in fixed_by_key.items():
        old = old_by_key[key]
        local = local_by_key[key]
        snapshot_rows.append({
            "label": key[0],
            "sample_idx": key[1],
            "old_20260730_candidate_count": old["candidate_count"],
            "fixed_20260801_overpass_candidate_count": fixed["candidate_count"],
            "pbf_20260801_candidate_count": local["candidate_count"],
            "old_rank1": old["rank1_way_id"],
            "fixed_rank1": fixed["perp_rank1_way_id"],
            "pbf_rank1": local["rank1"],
            "old_rank2": old["rank2_way_id"],
            "fixed_rank2": fixed["rank2_way_id"],
            "pbf_rank2": local["rank2"],
            "old_to_fixed_count_diff": int(old["candidate_count"]) != int(fixed["candidate_count"]),
            "old_to_fixed_rank12_diff": (old["rank1_way_id"], old["rank2_way_id"]) != (fixed["perp_rank1_way_id"], fixed["rank2_way_id"]),
            "fixed_to_pbf_count_diff": int(fixed["candidate_count"]) != int(local["candidate_count"]),
            "fixed_to_pbf_rank12_diff": (fixed["perp_rank1_way_id"], fixed["rank2_way_id"]) != (str(local["rank1"]), str(local["rank2"])),
        })
    write_csv(SNAPSHOT_CSV, snapshot_rows)

    current_violations = [r for r in decision_rows if r["rank1_violation"]]
    pbf_violations = [r for r in local_pbf_rows if r["violation"]]
    nontraversable = [r for r in first_rows if not r["rank1_traversable_by_batch5_filter"]]
    three_points = [r for r in first_rows if r["batch5_nonambiguous_to_ambiguous"]]
    d1_summary = {
        scope: {r["metric"]: r["value"] for r in d1_rows if r["scope"] == scope}
        for scope in {r["scope"] for r in d1_rows}
    }
    summary = {
        "points": 379,
        "source_fixed_candidates": str(FIXED_POINTS_CSV),
        "source_pbf": str(PBF_PATH),
        "decision_impact_2m": sum(r["decision_impact_ambiguous_2m"] for r in decision_rows),
        "semantic_decision_impact_2m": sum(
            r["semantic_decision_tuple_diff"] and r["geometric_ambiguous_2m"]
            for r in decision_rows
        ),
        "violation_flip_2m": sum(r["violation_flip_ambiguous_2m"] for r in decision_rows),
        "geometric_ambiguous_2m": sum(r["geometric_ambiguous_2m"] for r in decision_rows),
        "rank1_current_oneway_violations": len(current_violations),
        "rank1_current_oneway_violation_nontraversable": sum(
            (not next(x for x in first_rows if x["label"] == r["label"] and x["sample_idx"] == r["sample_idx"])["rank1_traversable_by_batch5_filter"])
            for r in current_violations
        ),
        "rank1_nontraversable_count": len(nontraversable),
        "batch5_nonambiguous_to_ambiguous_points": len(three_points),
        "legacy_node_based_oneway_violations_reconstructed": len(legacy_violations),
        "legacy_known_group_counts": dict(Counter(group for _, _, group in legacy_violations)),
        "local_pbf_oneway_violations": len(pbf_violations),
        "layer2_right_turn_points": len(layer2_rows),
        "layer2_rank1_required": sum(r["rank1_two_step_required"] for r in layer2_rows),
        "layer2_decision_diff": sum(r["decision_diff"] for r in layer2_rows),
        "layer2_decision_impact_2m": sum(r["decision_impact_ambiguous_2m"] for r in layer2_rows),
        "d1": d1_summary,
        "snapshot": {
            "old_to_fixed_candidate_count_diff_points": sum(r["old_to_fixed_count_diff"] for r in snapshot_rows),
            "old_to_fixed_rank12_diff_points": sum(r["old_to_fixed_rank12_diff"] for r in snapshot_rows),
            "fixed_to_pbf_candidate_count_diff_points": sum(r["fixed_to_pbf_count_diff"] for r in snapshot_rows),
            "fixed_to_pbf_rank12_diff_points": sum(r["fixed_to_pbf_rank12_diff"] for r in snapshot_rows),
        },
        "historical_known_violations_total_points": sum(v[3] for v in KNOWN_VIOLATIONS.values()),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
