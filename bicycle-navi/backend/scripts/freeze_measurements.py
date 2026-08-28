"""2026-08-28 測定凍結用の集約スクリプト。

判定ロジックは呼び出さず、固定PBF・固定attic条件で再生成済みの明細CSVを
検算して、論文参照用の一つの長形式CSVとOD端点CSVを生成する。

生成物:
  backend/data/measurement_freeze_20260828.csv
  backend/data/measurement_freeze_od_20260828.csv
  backend/data/ground_truth.csv
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from pathlib import Path
from statistics import median


BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent.parent
DATA = BACKEND / "data"

GOOGLE_INPUT = DATA / "google_routes_input.csv"
OD_PAIRS = DATA / "od_pairs.csv"
GOOGLE_COMPARISON = DATA / "google_comparison.csv"
R1_ROUTES = DATA / "verify_v2_analyze_route.csv"
MARGIN_POINTS = DATA / "verify_match_margin_points.csv"
DECISION_POINTS = DATA / "decision_ambiguity_points.csv"
DECISION_LAYER2 = DATA / "decision_ambiguity_layer2.csv"
FN_CANDIDATES = DATA / "fn_candidates_oneway.csv"
GT_TEMPLATE = DATA / "ground_truth_template.csv"
GT_OUTPUT = DATA / "ground_truth.csv"
OUTPUT = DATA / "measurement_freeze_20260828.csv"
OD_OUTPUT = DATA / "measurement_freeze_od_20260828.csv"
OLD_RECORD = ROOT / "docs" / "検証記録" / "検証記録_18点.md"
NEW_RECORD = ROOT / "docs" / "検証記録" / "検証記録_新規2点.md"

FIELDS = [
    "metric_no", "record_type", "label", "road_type", "item", "value",
    "numerator", "denominator", "unit", "status", "detail", "source_input",
]


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def truthy(value: str) -> bool:
    return value.strip().lower() == "true"


def decode_polyline(encoded: str) -> list[list[float]]:
    """Google encoded polylineを[lng, lat]へ復号する。"""
    index = 0
    lat = 0
    lng = 0
    coords: list[list[float]] = []
    while index < len(encoded):
        values = []
        for _ in range(2):
            result = 0
            shift = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lng += values[1]
        coords.append([lng / 1e5, lat / 1e5])
    return coords


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def validation_groups() -> tuple[
    dict[tuple[float, float], tuple[str, str]], dict[int, tuple[str, str]]
]:
    groups: dict[tuple[float, float], tuple[str, str]] = {}
    ways: dict[int, tuple[str, str]] = {}
    for path in (OLD_RECORD, NEW_RECORD):
        for line in path.read_text(encoding="utf-8").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 5 or not (cells[0].startswith("P") or cells[0].startswith("N")):
                continue
            try:
                lat, lng = float(cells[3]), float(cells[4])
            except ValueError:
                continue
            groups[(round(lat, 5), round(lng, 5))] = (cells[2], cells[0])
            ways[int(cells[1])] = (cells[2], cells[0])
    if Counter(group for group, _ in groups.values()) != Counter({"A": 6, "B": 10, "C": 4}):
        raise RuntimeError("validation records did not yield A=6, B=10, C=4")
    return groups, ways


def add(rows: list[dict[str, str]], metric_no: int, record_type: str, item: str,
        *, value: object = "", numerator: object = "", denominator: object = "",
        unit: str = "", status: str = "measured", label: str = "", road_type: str = "",
        detail: str = "", source_input: str = "") -> None:
    rows.append({
        "metric_no": str(metric_no), "record_type": record_type, "label": label,
        "road_type": road_type, "item": item, "value": str(value),
        "numerator": str(numerator), "denominator": str(denominator), "unit": unit,
        "status": status, "detail": detail, "source_input": source_input,
    })


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def write_ground_truth() -> tuple[int, int, int]:
    rows = load_csv(GT_TEMPLATE)
    if GT_OUTPUT.exists():
        existing = load_csv(GT_OUTPUT)
        if any(r.get("true_oneway_violation") or r.get("true_two_step_required") for r in existing):
            raise RuntimeError("ground_truth.csv contains human labels; refusing to overwrite")
    with GT_OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    detected = sum(bool(row["detected_rule"]) for row in rows)
    return len(rows), len({row["label"] for row in rows}), detected


def main() -> None:
    rows: list[dict[str, str]] = []
    groups, validation_ways = validation_groups()
    od_rows = load_csv(OD_PAIRS)
    od_by_label = {row["label"]: row for row in od_rows}

    # OD端点：Google polyline自体の先頭・末尾から算出する。
    od_output_rows = []
    for line_no, route in enumerate(load_csv(GOOGLE_INPUT), start=2):
        coords = decode_polyline(route["polyline"])
        od = od_by_label[route["label"]]
        od_output_rows.append({
            "label": route["label"], "road_type": od["road_type"],
            "r1_origin_lat": od["origin_lat"], "r1_origin_lng": od["origin_lng"],
            "r1_dest_lat": od["dest_lat"], "r1_dest_lng": od["dest_lng"],
            "polyline_start_lat": f"{coords[0][1]:.5f}",
            "polyline_start_lng": f"{coords[0][0]:.5f}",
            "polyline_end_lat": f"{coords[-1][1]:.5f}",
            "polyline_end_lng": f"{coords[-1][0]:.5f}",
            "origin_offset_m": f"{haversine_m(float(od['origin_lat']), float(od['origin_lng']), coords[0][1], coords[0][0]):.1f}",
            "dest_offset_m": f"{haversine_m(float(od['dest_lat']), float(od['dest_lng']), coords[-1][1], coords[-1][0]):.1f}",
            "google_routes_input_line": str(line_no),
        })
    with OD_OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(od_output_rows[0]))
        writer.writeheader()
        writer.writerows(od_output_rows)

    margin_rows = load_csv(MARGIN_POINTS)
    detected_oneway = [row for row in margin_rows if truthy(row["oneway_violation"])]
    if len(detected_oneway) != 12:
        raise RuntimeError(f"expected 12 current oneway points, got {len(detected_oneway)}")
    detected_group_counts = Counter()
    for row in detected_oneway:
        key = (round(float(row["lat"]), 5), round(float(row["lng"]), 5))
        if key in groups:
            group, point_id = groups[key]
        else:
            way_id = int(row["perp_rank1_way_id"])
            if way_id not in validation_ways:
                raise RuntimeError(f"current detected point has no validated group: {key}")
            group, historical_point_id = validation_ways[way_id]
            point_id = historical_point_id + "'"
        detected_group_counts[group] += 1
        add(rows, 1, "point", "R2 oneway検出点", value=1, numerator=1, denominator=379,
            unit="point", label=row["label"], detail=(
                f"point_id={point_id}; group={group}; way_id={row['perp_rank1_way_id']}; "
                f"sample_idx={row['sample_idx']}; margin_m={row['match_margin_m']}"
            ), source_input="verify_match_margin_points.csv")
    if detected_group_counts != Counter({"A": 4, "B": 4, "C": 4}):
        raise RuntimeError(f"unexpected current group counts: {detected_group_counts}")
    add(rows, 1, "summary", "R2 oneway検出点", value=12, numerator=12, denominator=379,
        unit="point", detail="40m間隔の379判定点", source_input="verify_match_margin_points.csv")
    for group in ("A", "B", "C"):
        add(rows, 1, "group_summary", f"R2 oneway {group}群", value=detected_group_counts[group],
            numerator=detected_group_counts[group], denominator=12, unit="point",
            source_input="検証記録_18点.md;検証記録_新規2点.md")
    layer2 = load_csv(DECISION_LAYER2)
    layer2_count = sum(truthy(row["rank1_two_step_required"]) for row in layer2)
    add(rows, 1, "summary", "R2 two_step_turn検出点", value=layer2_count,
        numerator=layer2_count, denominator=len(layer2), unit="point",
        source_input="decision_ambiguity_layer2.csv")
    add(rows, 1, "summary", "R2検出合計", value=12 + layer2_count,
        unit="point", detail="oneway 12点とtwo_step_turn 2点は分母が異なるため単一率にしない",
        source_input="verify_match_margin_points.csv;decision_ambiguity_layer2.csv")

    # R1の提示ルート。一方通行だけを対象とし、リルート後未採点の1ペアを明示する。
    r1_rows = load_csv(R1_ROUTES)
    gt_template = load_csv(GT_TEMPLATE)
    initial_oneway = Counter(row["label"] for row in gt_template if row["detected_rule"] == "oneway")
    measured_r1_labels = []
    for route in r1_rows:
        rerouted = truthy(route["rerouted"])
        if rerouted:
            add(rows, 2, "pair", "R1提示ルートの一方通行違反", value="", unit="count",
                status="unmeasured", label=route["label"], road_type=route["road_type"],
                detail="リルート後準拠ルートのgeometry未保存。Docker起動失敗のため再取得不可",
                source_input="verify_v2_analyze_route.csv")
        else:
            count = initial_oneway[route["label"]]
            measured_r1_labels.append(route["label"])
            add(rows, 2, "pair", "R1提示ルートの一方通行違反", value=count,
                numerator=count, denominator=1, unit="count", label=route["label"],
                road_type=route["road_type"], detail="rerouted=Falseのため提示ルート=判定済み初期ルート",
                source_input="verify_v2_analyze_route.csv;ground_truth_template.csv")
    add(rows, 2, "summary", "R1提示ルートの一方通行違反", value=0, numerator=0,
        denominator=len(measured_r1_labels), unit="count", status="partial",
        detail="14/15ペアを測定。横浜→みなとみらいは未測定",
        source_input="verify_v2_analyze_route.csv;ground_truth_template.csv")

    # 違反ゼロペアはR1とR2を対称な精度比較として扱わず、定義別に記録する。
    add(rows, 3, "summary", "R1一方通行違反ゼロペア", value=14, numerator=14,
        denominator=14, unit="pair", status="partial", detail="測定済み14ペア内。1ペア未測定",
        source_input="verify_v2_analyze_route.csv;ground_truth_template.csv")
    google_rows = load_csv(GOOGLE_COMPARISON)
    r2_zero_oneway = sum(int(row["google_oneway_violation_count"]) == 0 for row in google_rows)
    r2_zero_total = sum(int(row["google_total_violation_count"]) == 0 for row in google_rows)
    add(rows, 3, "summary", "R2一方通行検出ゼロペア", value=r2_zero_oneway,
        numerator=r2_zero_oneway, denominator=15, unit="pair", detail="R1との精度の直接比較には使わない",
        source_input="google_comparison.csv")
    add(rows, 3, "summary", "R2全検出ゼロペア", value=r2_zero_total,
        numerator=r2_zero_total, denominator=15, unit="pair", detail="onewayとtwo_step_turnの合計が0",
        source_input="google_comparison.csv")

    # タグ空間内の網羅性。実在規制のタグ欠落は母集団外。
    fn_rows = load_csv(FN_CANDIDATES)
    fn_counts = Counter(row["fn_reason"] for row in fn_rows)
    for reason in ("bicycle_exempt", "forward_travel", "short_segment", "tag_fetch_failed", "unknown"):
        add(rows, 4, "category", f"タグ付きoneway未検出:{reason}", value=fn_counts[reason],
            numerator=fn_counts[reason], denominator=len(fn_rows), unit="traversal",
            source_input="fn_candidates_oneway.csv")
    unresolved = sum(fn_counts[key] for key in ("short_segment", "tag_fetch_failed", "unknown"))
    add(rows, 4, "summary", "タグ空間内の未説明未検出", value=unresolved,
        numerator=unresolved, denominator=len(fn_rows), unit="traversal",
        detail="Recallとは呼ばない。OSMにonewayタグがない実在規制は母集団外",
        source_input="fn_candidates_oneway.csv")
    add(rows, 4, "summary", "タグ付きoneway通過", value=len(fn_rows) + 1,
        numerator=len(fn_rows) + 1, denominator=662, unit="traversal",
        detail="検出1 + 未検出診断253。unique wayは238",
        source_input="fn_candidates_oneway.csv;rerun_260801_summary.md")

    # 距離のみ。R2は保存polylineの復号距離を使い、手入力の丸め距離は使わない。
    google_by_label = {row["label"]: row for row in google_rows}
    for route in r1_rows:
        google = google_by_label[route["label"]]
        r1_distance = float(route["new_distance_m"])
        r2_distance = float(google["scorer_route_distance_m"])
        diff = r1_distance - r2_distance
        add(rows, 5, "pair", "R1・R2距離差", value=f"{diff:.1f}", unit="m",
            label=route["label"], road_type=route["road_type"],
            detail=(f"R1={r1_distance:.1f}m; R2={r2_distance:.1f}m; "
                    f"R1-R2={diff:.1f}m; R2分母差率={diff / r2_distance * 100:.1f}%"),
            source_input="verify_v2_analyze_route.csv;google_comparison.csv")
    reroute_diffs = [float(row["new_distance_diff_m"]) for row in r1_rows]
    add(rows, 5, "summary", "R1内部のリルート距離差ゼロ", value=sum(d == 0 for d in reroute_diffs),
        numerator=sum(d == 0 for d in reroute_diffs), denominator=15, unit="pair",
        source_input="verify_v2_analyze_route.csv")
    yokohama = next(row for row in r1_rows if row["label"] == "横浜→みなとみらい")
    original = float(yokohama["new_original_distance_m"])
    reroute_diff = float(yokohama["new_distance_diff_m"])
    add(rows, 5, "summary", "R1内部のリルート距離増", value=f"{reroute_diff:.1f}",
        numerator=f"{reroute_diff:.1f}", denominator=f"{original:.1f}", unit="m",
        label=yokohama["label"], detail=f"{reroute_diff / original * 100:.1f}%",
        source_input="verify_v2_analyze_route.csv")

    # 曖昧性の三段階。
    decision_rows = load_csv(DECISION_POINTS)
    stages = [
        ("幾何的曖昧", "geometric_ambiguous_2m"),
        ("判定影響曖昧", "decision_impact_ambiguous_2m"),
        ("違反有無反転", "violation_flip_ambiguous_2m"),
    ]
    for item, field in stages:
        count = sum(truthy(row[field]) for row in decision_rows)
        add(rows, 6, "summary", item, value=count, numerator=count, denominator=len(decision_rows),
            unit="point", detail=f"2.0m閾値; {count / len(decision_rows) * 100:.2f}%",
            source_input="decision_ambiguity_points.csv")

    # マージン分布：全379点と現行oneway 12点を分けて記録する。
    all_margins = [float(row["margin_m"]) for row in decision_rows]
    detected_margins = [float(row["match_margin_m"]) for row in detected_oneway]
    for scope, values in (("全379判定点", all_margins), ("現行oneway検出12点", detected_margins)):
        for name, value in (
            ("min", min(values)), ("p25", percentile(values, 0.25)),
            ("median", median(values)), ("p75", percentile(values, 0.75)),
            ("max", max(values)),
        ):
            add(rows, 7, "distribution", f"{scope}:{name}", value=f"{value:.3f}",
                denominator=len(values), unit="m", source_input=(
                    "decision_ambiguity_points.csv" if len(values) == 379 else "verify_match_margin_points.csv"
                ))
    for threshold in (1.5, 2.0, 2.5):
        all_count = sum(value < threshold for value in all_margins)
        detected_count = sum(value < threshold for value in detected_margins)
        add(rows, 7, "threshold", f"全379判定点:margin<{threshold:.1f}m", value=all_count,
            numerator=all_count, denominator=379, unit="point", source_input="decision_ambiguity_points.csv")
        add(rows, 7, "threshold", f"現行oneway 12点:判定不能<{threshold:.1f}m", value=detected_count,
            numerator=detected_count, denominator=12, unit="point", source_input="verify_match_margin_points.csv")

    gt_rows, gt_labels, gt_detected = write_ground_truth()
    add(rows, 8, "summary", "ground_truth.csv再構築行", value=gt_rows, numerator=gt_rows,
        denominator=gt_labels, unit="row", status="candidate_only",
        detail=f"15ラベル、検出候補{gt_detected}行。true_*は人手未入力のため精度指標は未測定",
        source_input="ground_truth_template.csv")

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {OUTPUT}: {len(rows)} data rows")
    print(f"wrote {OD_OUTPUT}: {len(od_output_rows)} data rows")
    print(f"wrote {GT_OUTPUT}: {gt_rows} data rows; human truth labels remain blank")


if __name__ == "__main__":
    main()
