"""Googleルート379判定点のway候補・マージン感度を再集計する。

判定ロジックは変更せず、保存済みOSM PBFと既存379点から診断情報を再構成する。
出力はすべて backend/data の新規ファイルで、既存CSVを上書きしない。

実行例（リポジトリルートから）:
  $env:PYTHONPATH='bicycle-navi/backend/.venv/Lib/site-packages'
  <python> bicycle-navi/backend/scripts/analyze_match_margin_deep_dive.py
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
sys.path.insert(0, str(BACKEND_DIR))
SITE_PACKAGES = BACKEND_DIR / ".venv" / "Lib" / "site-packages"
if SITE_PACKAGES.exists():
    sys.path.insert(0, str(SITE_PACKAGES))

from scripts.local_osm_pbf import extract_local_highways  # noqa: E402
from services.overpass import _point_to_way_dist_m  # noqa: E402


EXISTING_POINTS_CSV = DATA_DIR / "dryrun_nonroad_filter_points.csv"
PBF_PATH = BACKEND_DIR.parent / "graphhopper" / "kanto-260801.osm.pbf"
POINTS_CSV = DATA_DIR / "margin_deep_dive_points.csv"
CROSSTAB_ALL_CSV = DATA_DIR / "margin_highway_crosstab_all.csv"
CROSSTAB_AMBIG_CSV = DATA_DIR / "margin_highway_crosstab_ambiguous.csv"
RANK2_TAG_CSV = DATA_DIR / "margin_rank2_tag_distribution.csv"
PATTERN_CSV = DATA_DIR / "margin_pattern_counts.csv"
SENSITIVITY_CSV = DATA_DIR / "margin_threshold_sensitivity.csv"
SENSITIVITY_PNG = DATA_DIR / "margin_threshold_sensitivity.png"
SUMMARY_JSON = DATA_DIR / "margin_deep_dive_summary.json"

THRESHOLDS_M = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0]

# 既存 dryrun_nonroad_filter.py の候補除外を基礎に、明示的な通行禁止も加える。
NONROAD_HIGHWAYS = {"footway", "path", "steps", "pedestrian", "corridor", "platform"}
FORBIDDEN_HIGHWAYS = NONROAD_HIGHWAYS | {"motorway", "motorway_link"}
BICYCLE_EXPLICIT_ALLOW = {"yes", "designated", "permissive", "official"}
BICYCLE_EXPLICIT_DENY = {"no", "use_sidepath"}
ACCESS_DENY = {"no", "private"}

FACILITY_HIGHWAYS = {"footway", "cycleway", "path", "pedestrian", "steps", "corridor", "platform"}
MOTOR_ROAD_HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road",
}
ONEWAY_TRUE = {"yes", "1", "true", "-1", "reversible", "alternating"}


@dataclass
class Candidate:
    way_id: int | None
    dist_m: float
    tags: dict
    geometry: list

    @property
    def highway(self) -> str:
        return self.tags.get("highway", "")

    @property
    def oneway(self) -> str:
        return self.tags.get("oneway", "")

    @property
    def name(self) -> str:
        return self.tags.get("name", "")


@dataclass
class PointProbe:
    label: str
    sample_idx: int
    route_idx: int
    lat: float
    lng: float
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def rank1(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def rank2(self) -> Candidate | None:
        return self.candidates[1] if len(self.candidates) >= 2 else None

    @property
    def match_margin_m(self) -> float | None:
        return margin(self.candidates)


def blank(value: object) -> str:
    text = str(value or "").strip()
    return text if text else "(missing)"


def bicycle_traversable(cand: Candidate) -> bool:
    """タグだけによる診断用の自転車通行可能判定。

    - bicycle=yes/designated/permissive/official は明示許可として優先する。
    - bicycle=no/use_sidepath は除外する。
    - access=no/private は明示的な自転車許可がなければ除外する。
    - 既存ドライランの非車道群と motorway(_link) は明示許可がなければ除外する。
    - その他の highway（cyclewayを含む）は通行可能候補として残す。
    """
    bicycle = cand.tags.get("bicycle", "").strip().lower()
    access = cand.tags.get("access", "").strip().lower()
    highway = cand.highway.strip().lower()
    if bicycle in BICYCLE_EXPLICIT_ALLOW:
        return True
    if bicycle in BICYCLE_EXPLICIT_DENY:
        return False
    if access in ACCESS_DENY:
        return False
    if highway in FORBIDDEN_HIGHWAYS:
        return False
    return bool(highway)


def _xy_m(point: list[float], lat0: float) -> tuple[float, float]:
    lon, lat = point
    return (
        lon * 111_320.0 * math.cos(math.radians(lat0)),
        lat * 110_540.0,
    )


def local_axis(cand: Candidate, lat: float, lng: float) -> tuple[float, float] | None:
    """判定点への距離が最小となる線分の無向軸ベクトルを返す。"""
    geom = cand.geometry
    if len(geom) < 2:
        return None
    px, py = _xy_m([lng, lat], lat)
    best = None
    best_d2 = float("inf")
    for a, b in zip(geom, geom[1:]):
        ax, ay = _xy_m(a, lat)
        bx, by = _xy_m(b, lat)
        vx, vy = bx - ax, by - ay
        vv = vx * vx + vy * vy
        if vv == 0:
            continue
        t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / vv))
        qx, qy = ax + t * vx, ay + t * vy
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = (vx, vy)
    return best


def acute_angle_deg(v1: tuple[float, float] | None, v2: tuple[float, float] | None) -> float | None:
    if not v1 or not v2:
        return None
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return None
    cosine = abs((v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def geometries_share_node(c1: Candidate, c2: Candidate, lat0: float, tolerance_m: float = 0.5) -> bool:
    if not c1.geometry or not c2.geometry:
        return False
    points2 = [_xy_m(p, lat0) for p in c2.geometry]
    limit2 = tolerance_m * tolerance_m
    for p1 in c1.geometry:
        x1, y1 = _xy_m(p1, lat0)
        if any((x1 - x2) ** 2 + (y1 - y2) ** 2 <= limit2 for x2, y2 in points2):
            return True
    return False


def classify_pair(probe: PointProbe, c1: Candidate | None, c2: Candidate | None) -> tuple[str, float | None, bool]:
    """上位2wayを(a)〜(d)へ規則分類する。

    (a): road系同士、同名、双方oneway、局所方位差<=15°、共有ノードなし。
    (b): road系とfoot/cycle/path系の組合せ、局所方位差<=20°。
    (c): 共有ノードあり、または局所方位差>=45°（交差点・接続部）。
    (d): 上記以外。分類は診断用で、目視正解ではない。
    """
    if c1 is None or c2 is None:
        return "(d) その他（候補不足）", None, False
    a1 = local_axis(c1, probe.lat, probe.lng)
    a2 = local_axis(c2, probe.lat, probe.lng)
    angle = acute_angle_deg(a1, a2)
    shared = geometries_share_node(c1, c2, probe.lat)
    h1, h2 = c1.highway, c2.highway
    name1 = c1.name.strip()
    name2 = c2.name.strip()
    oneway1 = c1.oneway.strip().lower() in ONEWAY_TRUE
    oneway2 = c2.oneway.strip().lower() in ONEWAY_TRUE
    if (
        angle is not None and angle <= 15.0 and not shared
        and h1 in MOTOR_ROAD_HIGHWAYS and h2 in MOTOR_ROAD_HIGHWAYS
        and name1 and name1 == name2 and oneway1 and oneway2
    ):
        return "(a) 同一道路の分離ウェイ", angle, shared
    facility_road = (
        (h1 in FACILITY_HIGHWAYS and h2 in MOTOR_ROAD_HIGHWAYS)
        or (h2 in FACILITY_HIGHWAYS and h1 in MOTOR_ROAD_HIGHWAYS)
    )
    if angle is not None and angle <= 20.0 and facility_road:
        return "(b) 車道と並走する歩道・自転車道", angle, shared
    if shared or (angle is not None and angle >= 45.0):
        return "(c) 交差点付近の交差・接続way", angle, shared
    return "(d) その他", angle, shared


def margin(candidates: list[Candidate]) -> float | None:
    if len(candidates) < 2:
        return None
    return round(candidates[1].dist_m - candidates[0].dist_m, 3)


def record_for_probe(probe: PointProbe) -> dict[str, object]:
    c1, c2 = probe.rank1, probe.rank2
    filtered = [c for c in probe.candidates if bicycle_traversable(c)]
    f1 = filtered[0] if filtered else None
    f2 = filtered[1] if len(filtered) >= 2 else None
    current_margin = probe.match_margin_m
    filtered_margin = margin(filtered)
    pattern, angle, shared = classify_pair(probe, c1, c2)

    def field(cand: Candidate | None, key: str) -> object:
        if cand is None:
            return ""
        if key == "way_id":
            return cand.way_id
        if key == "dist_m":
            return round(cand.dist_m, 3)
        return cand.tags.get(key, "")

    return {
        "label": probe.label,
        "sample_idx": probe.sample_idx,
        "route_idx": probe.route_idx,
        "lat": round(probe.lat, 7),
        "lng": round(probe.lng, 7),
        "candidate_count": len(probe.candidates),
        "rank1_way_id": field(c1, "way_id"),
        "rank1_highway": field(c1, "highway"),
        "rank1_bicycle": field(c1, "bicycle"),
        "rank1_access": field(c1, "access"),
        "rank1_oneway": field(c1, "oneway"),
        "rank1_name": field(c1, "name"),
        "rank1_dist_m": field(c1, "dist_m"),
        "rank2_way_id": field(c2, "way_id"),
        "rank2_highway": field(c2, "highway"),
        "rank2_bicycle": field(c2, "bicycle"),
        "rank2_access": field(c2, "access"),
        "rank2_oneway": field(c2, "oneway"),
        "rank2_name": field(c2, "name"),
        "rank2_dist_m": field(c2, "dist_m"),
        "match_margin_m": current_margin if current_margin is not None else "",
        "match_ambiguous_2m": current_margin is not None and current_margin < 2.0,
        "pair_local_angle_deg": "" if angle is None else round(angle, 1),
        "pair_shared_node": shared,
        "pair_pattern": pattern,
        "filtered_candidate_count": len(filtered),
        "filtered_rank1_way_id": field(f1, "way_id"),
        "filtered_rank1_highway": field(f1, "highway"),
        "filtered_rank1_dist_m": field(f1, "dist_m"),
        "filtered_rank2_way_id": field(f2, "way_id"),
        "filtered_rank2_highway": field(f2, "highway"),
        "filtered_rank2_dist_m": field(f2, "dist_m"),
        "filtered_margin_m": filtered_margin if filtered_margin is not None else "",
        "filtered_ambiguous_2m": filtered_margin is not None and filtered_margin < 2.0,
        "ambiguity_changed_2m": (
            (current_margin is not None and current_margin < 2.0)
            != (filtered_margin is not None and filtered_margin < 2.0)
        ),
    }


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"No rows for {path}")
    names = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def crosstab(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts = Counter((blank(r["rank1_highway"]), blank(r["rank2_highway"])) for r in rows)
    return [
        {"rank1_highway": h1, "rank2_highway": h2, "count": count}
        for (h1, h2), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def tag_distribution(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    scopes = {
        "all": rows,
        "margin_lt_2m": [r for r in rows if r["match_ambiguous_2m"]],
    }
    for scope, scoped in scopes.items():
        for tag in ("highway", "bicycle", "access", "oneway"):
            counts = Counter(blank(r[f"rank2_{tag}"]) for r in scoped)
            for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
                output.append({
                    "scope": scope,
                    "tag": tag,
                    "value": value,
                    "count": count,
                    "pct_within_scope": round(100.0 * count / len(scoped), 2) if scoped else 0.0,
                })
    return output


def pattern_counts(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    for scope, scoped in (
        ("all", rows),
        ("margin_lt_2m", [r for r in rows if r["match_ambiguous_2m"]]),
    ):
        counts = Counter(str(r["pair_pattern"]) for r in scoped)
        for pattern, count in sorted(counts.items()):
            output.append({
                "scope": scope,
                "pattern": pattern,
                "count": count,
                "pct_within_scope": round(100.0 * count / len(scoped), 2) if scoped else 0.0,
            })
    return output


def sensitivity(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output = []
    n = len(rows)
    for threshold in THRESHOLDS_M:
        current = sum(
            1 for r in rows
            if r["match_margin_m"] != "" and float(r["match_margin_m"]) < threshold
        )
        filtered = sum(
            1 for r in rows
            if r["filtered_margin_m"] != "" and float(r["filtered_margin_m"]) < threshold
        )
        output.append({
            "threshold_m": threshold,
            "current_ambiguous_count": current,
            "current_ambiguous_pct": round(100.0 * current / n, 2),
            "filtered_ambiguous_count": filtered,
            "filtered_ambiguous_pct": round(100.0 * filtered / n, 2),
        })
    return output


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_sensitivity(rows: list[dict[str, object]]) -> None:
    scale = 2
    width, height = 1200 * scale, 760 * scale
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    left, right, top, bottom = 150 * scale, 70 * scale, 110 * scale, 120 * scale
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_x, max_y = 10.0, 100.0

    def xy(x: float, y: float) -> tuple[float, float]:
        return left + x / max_x * plot_w, top + (max_y - y) / max_y * plot_h

    title_font = _font(30 * scale, bold=True)
    label_font = _font(18 * scale)
    tick_font = _font(15 * scale)
    legend_font = _font(16 * scale)
    draw.text((width / 2, 45 * scale), "Ambiguous Rate vs Match-Margin Threshold", fill="#172B4D", font=title_font, anchor="mm")

    for y in range(0, 101, 10):
        x1, yy = xy(0, y)
        x2, _ = xy(max_x, y)
        draw.line((x1, yy, x2, yy), fill="#D9E2EC", width=2 * scale)
        draw.text((left - 18 * scale, yy), f"{y}%", fill="#52606D", font=tick_font, anchor="rm")
    for x in (0, 2, 4, 6, 8, 10):
        xx, y1 = xy(x, 0)
        _, y2 = xy(x, 100)
        draw.line((xx, y1, xx, y2), fill="#EEF2F6", width=2 * scale)
        draw.text((xx, top + plot_h + 18 * scale), f"{x:g}", fill="#52606D", font=tick_font, anchor="ma")
    draw.line((left, top, left, top + plot_h), fill="#334E68", width=3 * scale)
    draw.line((left, top + plot_h, left + plot_w, top + plot_h), fill="#334E68", width=3 * scale)

    draw.text((left + plot_w / 2, height - 38 * scale), "Threshold (m), ambiguous if margin < threshold", fill="#334E68", font=label_font, anchor="mm")
    draw.text((left, top - 28 * scale), "Ambiguous rate (%)", fill="#334E68", font=label_font, anchor="lm")

    series = [
        ("Current candidates", "current_ambiguous_pct", "#D64545"),
        ("Bicycle-traversable candidates", "filtered_ambiguous_pct", "#147D92"),
    ]
    for label, key, color in series:
        points = [xy(float(r["threshold_m"]), float(r[key])) for r in rows]
        draw.line(points, fill=color, width=5 * scale, joint="curve")
        for point in points:
            radius = 6 * scale
            draw.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=color, outline="white", width=2 * scale)

    lx, ly = left + 40 * scale, top + 35 * scale
    for i, (label, _, color) in enumerate(series):
        yy = ly + i * 38 * scale
        draw.line((lx, yy, lx + 48 * scale, yy), fill=color, width=5 * scale)
        draw.text((lx + 62 * scale, yy), label, fill="#243B53", font=legend_font, anchor="lm")

    image = image.resize((1200, 760), Image.Resampling.LANCZOS)
    image.save(SENSITIVITY_PNG, format="PNG", optimize=True)


def distribution(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "min": None, "median": None, "max": None}
    n = len(ordered)
    if n % 2:
        median = ordered[n // 2]
    else:
        median = (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {
        "n": n,
        "min": round(ordered[0], 3),
        "median": round(median, 3),
        "max": round(ordered[-1], 3),
    }


def main() -> int:
    with EXISTING_POINTS_CSV.open(encoding="utf-8-sig", newline="") as stream:
        existing = list(csv.DictReader(stream))
    if len(existing) != 379:
        raise RuntimeError(f"Expected 379 existing points, got {len(existing)}")

    point_pairs = [(float(row["lat"]), float(row["lng"])) for row in existing]
    elements = extract_local_highways(PBF_PATH, point_pairs)
    points_by_label: dict[str, list[tuple[float, float]]] = {}
    for row in existing:
        points_by_label.setdefault(row["label"], []).append((float(row["lat"]), float(row["lng"])))
    union_by_label: dict[str, list[dict]] = {}
    for label, label_points in points_by_label.items():
        union_by_label[label] = [
            element for element in elements
            if any(
                _point_to_way_dist_m(lat, lng, element["geometry"]) <= 20.0
                for lat, lng in label_points
            )
        ]
        print(f"Local union {label}: {len(union_by_label[label])} ways", flush=True)
    probes: list[PointProbe] = []
    reconstruction_mismatches = []
    candidate_count_mismatches = 0
    for row in existing:
        lat, lng = float(row["lat"]), float(row["lng"])
        ranked = []
        for element in union_by_label[row["label"]]:
            distance = _point_to_way_dist_m(lat, lng, element["geometry"])
            ranked.append((distance, element))
        ranked.sort(key=lambda item: item[0])
        candidates = [
            Candidate(
                way_id=element["id"],
                dist_m=distance,
                tags=element["tags"],
                geometry=element["geometry"],
            )
            for distance, element in ranked
        ]
        probe = PointProbe(
            label=row["label"],
            sample_idx=int(row["sample_idx"]),
            route_idx=int(row["route_idx"]),
            lat=lat,
            lng=lng,
            candidates=candidates,
        )
        probes.append(probe)
        local_ids = (
            probe.rank1.way_id if probe.rank1 else None,
            probe.rank2.way_id if probe.rank2 else None,
        )
        existing_ids = (
            int(row["rank1_way_id"]) if row["rank1_way_id"] else None,
            int(row["rank2_way_id"]) if row["rank2_way_id"] else None,
        )
        if local_ids != existing_ids:
            reconstruction_mismatches.append({
                "label": row["label"],
                "sample_idx": row["sample_idx"],
                "existing_rank1": existing_ids[0],
                "existing_rank2": existing_ids[1],
                "local_rank1": local_ids[0],
                "local_rank2": local_ids[1],
            })
        if len(candidates) != int(row["candidate_count"]):
            candidate_count_mismatches += 1

    records = [record_for_probe(probe) for probe in probes]
    if len(records) != 379:
        raise RuntimeError(f"Expected 379 points, got {len(records)}; outputs not written")

    write_csv(POINTS_CSV, records)
    all_cross = crosstab(records)
    amb_rows = [r for r in records if r["match_ambiguous_2m"]]
    write_csv(CROSSTAB_ALL_CSV, all_cross)
    write_csv(CROSSTAB_AMBIG_CSV, crosstab(amb_rows))
    write_csv(RANK2_TAG_CSV, tag_distribution(records))
    write_csv(PATTERN_CSV, pattern_counts(records))
    sensitivity_rows = sensitivity(records)
    write_csv(SENSITIVITY_CSV, sensitivity_rows)
    draw_sensitivity(sensitivity_rows)

    current_margins = [float(r["match_margin_m"]) for r in records if r["match_margin_m"] != ""]
    filtered_margins = [float(r["filtered_margin_m"]) for r in records if r["filtered_margin_m"] != ""]
    summary = {
        "source_pbf": str(PBF_PATH),
        "source_points_csv": str(EXISTING_POINTS_CSV),
        "reconstruction_rank12_mismatches": len(reconstruction_mismatches),
        "reconstruction_candidate_count_mismatches": candidate_count_mismatches,
        "reconstruction_mismatch_examples": reconstruction_mismatches[:20],
        "points": len(records),
        "filter_definition": {
            "explicit_allow": sorted(BICYCLE_EXPLICIT_ALLOW),
            "explicit_deny": sorted(BICYCLE_EXPLICIT_DENY),
            "access_deny": sorted(ACCESS_DENY),
            "default_forbidden_highways": sorted(FORBIDDEN_HIGHWAYS),
        },
        "current_margin": distribution(current_margins),
        "filtered_margin": distribution(filtered_margins),
        "current_ambiguous_2m": sum(bool(r["match_ambiguous_2m"]) for r in records),
        "filtered_ambiguous_2m": sum(bool(r["filtered_ambiguous_2m"]) for r in records),
        "ambiguity_changed_2m": sum(bool(r["ambiguity_changed_2m"]) for r in records),
        "rank1_changed_by_filter": sum(r["rank1_way_id"] != r["filtered_rank1_way_id"] for r in records),
        "rank2_changed_by_filter": sum(r["rank2_way_id"] != r["filtered_rank2_way_id"] for r in records),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
