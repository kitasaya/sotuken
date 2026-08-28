"""20検証点の進行方向と rank1/rank2 way の局所方向差を測定する。

検証記録の座標を入力の正とし、verify_match_margin_points.csv の
rank1/rank2 と対応づける。way候補の距離と一方通行判定は、
本番の services.overpass._point_to_way_dist_m および
services.law_checker.check_oneway_violation を再利用する。候補の選択に
方向情報は使用しない。
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.external_route_scorer import _haversine_m, decode_polyline
from services.law_checker import check_oneway_violation
from services.overpass import _point_to_way_dist_m
from scripts.local_osm_pbf import extract_local_highways


BACKEND_DIR = Path(__file__).parent.parent
ROOT_DIR = BACKEND_DIR.parent.parent
DATA_DIR = BACKEND_DIR / "data"
PBF_PATH = BACKEND_DIR.parent / "graphhopper" / "kanto-260801.osm.pbf"
VERIFY_PATH = DATA_DIR / "verify_match_margin_points.csv"
ROUTES_PATH = DATA_DIR / "google_routes_input.csv"
OLD_RECORD_PATH = ROOT_DIR / "docs" / "検証記録" / "検証記録_18点.md"
NEW_RECORD_PATH = ROOT_DIR / "docs" / "検証記録" / "検証記録_新規2点.md"
CSV_OUTPUT = DATA_DIR / "direction_difference_points.csv"
JSON_OUTPUT = DATA_DIR / "direction_difference_summary.json"
IMAGE_OUTPUT = DATA_DIR / "direction_difference_distribution.png"


def parse_validation_points(path: Path) -> list[dict]:
    """検証記録の本表から点ID・way ID・群・座標を読み取る。"""
    points = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not re.match(r"^\| (?:P\d{2}|N\d{2}) \|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 11:
            continue
        points.append({
            "point_id": cells[0],
            "way_id": int(cells[1]),
            "group": cells[2],
            "lat": float(cells[3]),
            "lng": float(cells[4]),
            "record_source": f"{path.relative_to(ROOT_DIR).as_posix()}:{line_no}",
        })
    return points


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def attach_verify_rows(points: list[dict]) -> None:
    rows = load_csv(VERIFY_PATH)
    for point in points:
        matches = [
            (line_no, row)
            for line_no, row in enumerate(rows, start=2)
            if abs(float(row["lat"]) - point["lat"]) < 0.000001
            and abs(float(row["lng"]) - point["lng"]) < 0.000001
        ]
        if len(matches) != 1:
            raise ValueError(f"{point['point_id']}: verify CSV match count={len(matches)}")
        line_no, row = matches[0]
        point.update({
            "label": row["label"],
            "sample_idx": int(row["sample_idx"]),
            "route_idx": int(row["route_idx"]),
            "rank1_way_id": int(row["perp_rank1_way_id"]),
            "rank2_way_id": int(row["rank2_way_id"]),
            "rank1_dist_m": float(row["match_dist_m"]),
            "rank2_dist_m": float(row["rank2_dist_m"]),
            "verify_source": f"bicycle-navi/backend/data/verify_match_margin_points.csv:{line_no}",
        })


def load_routes() -> dict[str, list]:
    return {
        row["label"]: decode_polyline(row["polyline"])
        for row in load_csv(ROUTES_PATH)
    }


def direction_reference(coords: list, idx: int) -> dict:
    """直前点（始点は直後点）を基本とし、5m未満なら外側へ延長する。"""
    if idx < 0 or idx >= len(coords):
        raise IndexError(idx)
    if len(coords) < 2:
        raise ValueError("ルート座標が2点未満")

    step = 1 if idx == 0 else -1
    direct_idx = idx + step
    direct_distance = _haversine_m(coords[idx], coords[direct_idx])
    ref_idx = direct_idx
    while 0 <= ref_idx < len(coords) and _haversine_m(coords[idx], coords[ref_idx]) < 5.0:
        next_idx = ref_idx + step
        if next_idx < 0 or next_idx >= len(coords):
            break
        ref_idx = next_idx

    if idx == 0:
        start, end = coords[idx], coords[ref_idx]
        method = "forward"
    else:
        start, end = coords[ref_idx], coords[idx]
        method = "backward"
    return {
        "ref_idx": ref_idx,
        "direct_distance_m": direct_distance,
        "used_distance_m": _haversine_m(start, end),
        "extended": direct_distance < 5.0 and ref_idx != direct_idx,
        "skipped_points": abs(ref_idx - direct_idx),
        "method": method,
        "vector": [end[0] - start[0], end[1] - start[1]],
    }


def bearing_deg(start: list, end: list) -> float:
    """初期方位角（0=北、90=東）。"""
    lat1, lat2 = math.radians(start[1]), math.radians(end[1])
    dlng = math.radians(end[0] - start[0])
    y = math.sin(dlng) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def nearest_segment(lat: float, lng: float, geometry: list) -> tuple[int, float, float]:
    """本番の有限線分距離で最近傍セグメントを選ぶ。"""
    if len(geometry) < 2:
        raise ValueError("way geometry has fewer than two points")
    ranked = [
        (_point_to_way_dist_m(lat, lng, [geometry[i], geometry[i + 1]]), i)
        for i in range(len(geometry) - 1)
    ]
    distance, index = min(ranked)
    return index, distance, bearing_deg(geometry[index], geometry[index + 1])


def angle_difference_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


async def production_oneway_result(point: dict, way: dict, vector: list) -> tuple[bool, float]:
    violations = await check_oneway_violation(
        [[point["lng"], point["lat"]]],
        tags_list=[way["tags"]],
        geometries=[way["geometry"]],
        travel_vectors=[vector],
    )
    return (bool(violations), float(violations[0]["confidence"]) if violations else 0.0)


def percentile(values: list[float], p: float) -> float:
    """NumPy/Pandasの既定と同じ線形補間パーセンタイル。"""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty percentile input")
    pos = (len(ordered) - 1) * p
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def group_stats(rows: list[dict], field: str) -> dict:
    values = [float(row[field]) for row in rows]
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "p25": round(percentile(values, 0.25), 3),
        "median": round(percentile(values, 0.5), 3),
        "p75": round(percentile(values, 0.75), 3),
        "max": round(max(values), 3),
    }


def write_plot(rows: list[dict]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1800, 820
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    groups = ["A", "B", "C"]
    colors = {"A": "#4C78A8", "B": "#F58518", "C": "#E45756"}
    panels = [(90, 100, 830, 700), (970, 100, 1710, 700)]

    def ymap(value: float, panel: tuple) -> float:
        return panel[3] - value / 180.0 * (panel[3] - panel[1])

    def xmap(value: float, panel: tuple) -> float:
        return panel[0] + value / 180.0 * (panel[2] - panel[0])

    for panel in panels:
        draw.rectangle(panel, outline="#222222", width=2)
        for tick in (0, 45, 90, 135, 180):
            y = ymap(tick, panel)
            draw.line((panel[0], y, panel[2], y), fill="#DDDDDD", width=1)
            draw.text((panel[0] - 38, y - 7), str(tick), fill="#222222", font=font)
    draw.line((panels[0][0], ymap(90, panels[0]), panels[0][2], ymap(90, panels[0])), fill="#666666", width=2)

    x_positions = {"A": 250, "B": 460, "C": 670}
    for group in groups:
        subset = [float(row["rank1_direction_diff_deg"]) for row in rows if row["group"] == group]
        stats = {
            "min": min(subset), "p25": percentile(subset, 0.25), "median": percentile(subset, 0.5),
            "p75": percentile(subset, 0.75), "max": max(subset),
        }
        x = x_positions[group]
        draw.line((x, ymap(stats["min"], panels[0]), x, ymap(stats["max"], panels[0])), fill="#222222", width=3)
        draw.rectangle((x - 45, ymap(stats["p75"], panels[0]), x + 45, ymap(stats["p25"], panels[0])), outline=colors[group], width=5)
        draw.line((x - 45, ymap(stats["median"], panels[0]), x + 45, ymap(stats["median"], panels[0])), fill=colors[group], width=5)
        draw.line((x - 25, ymap(stats["min"], panels[0]), x + 25, ymap(stats["min"], panels[0])), fill="#222222", width=3)
        draw.line((x - 25, ymap(stats["max"], panels[0]), x + 25, ymap(stats["max"], panels[0])), fill="#222222", width=3)
        for index, value in enumerate(subset):
            jitter = ((index % 5) - 2) * 7
            y = ymap(value, panels[0])
            draw.ellipse((x + jitter - 6, y - 6, x + jitter + 6, y + 6), fill=colors[group], outline="white")
        draw.text((x - 5, panels[0][3] + 16), group, fill="#222222", font=font)

    p2 = panels[1]
    draw.line((xmap(90, p2), p2[1], xmap(90, p2), p2[3]), fill="#666666", width=2)
    draw.line((p2[0], ymap(90, p2), p2[2], ymap(90, p2)), fill="#666666", width=2)
    for tick in (0, 45, 90, 135, 180):
        x = xmap(tick, p2)
        draw.line((x, p2[3], x, p2[3] + 8), fill="#222222", width=1)
        draw.text((x - 8, p2[3] + 12), str(tick), fill="#222222", font=font)
    for row in rows:
        x = xmap(float(row["rank1_direction_diff_deg"]), p2)
        y = ymap(float(row["rank2_direction_diff_deg"]), p2)
        color = colors[row["group"]]
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline="white")
        draw.text((x + 9, y - 8), row["point_id"], fill=color, font=font)

    draw.text((235, 52), "Rank-1 local direction difference by group", fill="#111111", font=font)
    draw.text((1190, 52), "Rank-1 / rank-2 direction difference", fill="#111111", font=font)
    draw.text((325, 744), "Ground-truth group", fill="#111111", font=font)
    draw.text((1190, 744), "Rank-1 difference (degrees)", fill="#111111", font=font)
    draw.text((25, 370), "Direction difference (degrees)", fill="#111111", font=font)
    draw.text((885, 370), "Rank-2 difference (degrees)", fill="#111111", font=font)
    legend_x = 1430
    for index, group in enumerate(groups):
        y = 75 + index * 18
        draw.rectangle((legend_x, y, legend_x + 12, y + 12), fill=colors[group])
        count = sum(row["group"] == group for row in rows)
        draw.text((legend_x + 18, y), f"{group} (n={count})", fill="#222222", font=font)
    image.save(IMAGE_OUTPUT)


def load_output_rows() -> list[dict]:
    rows = load_csv(CSV_OUTPUT)
    numeric_fields = ("rank1_direction_diff_deg", "rank2_direction_diff_deg")
    for row in rows:
        for field in numeric_fields:
            row[field] = float(row[field])
    return rows


async def main() -> None:
    points = parse_validation_points(OLD_RECORD_PATH) + parse_validation_points(NEW_RECORD_PATH)
    if len(points) != 20:
        raise ValueError(f"expected 20 validation points, got {len(points)}")
    if {p["group"] for p in points} != {"A", "B", "C"}:
        raise ValueError("unexpected validation groups")
    attach_verify_rows(points)
    routes = load_routes()

    for point in points:
        coords = routes[point["label"]]
        route_point = coords[point["route_idx"]]
        offset = _haversine_m(route_point, [point["lng"], point["lat"]])
        if offset > 0.8:
            raise ValueError(f"{point['point_id']}: route_idx offset {offset:.3f}m")
        direction = direction_reference(coords, point["route_idx"])
        start = coords[direction["ref_idx"]] if direction["method"] == "backward" else coords[point["route_idx"]]
        end = coords[point["route_idx"]] if direction["method"] == "backward" else coords[direction["ref_idx"]]
        point.update({
            "route_point_offset_m": round(offset, 3),
            "direction_method": direction["method"],
            "direction_ref_route_idx": direction["ref_idx"],
            "direct_neighbor_distance_m": round(direction["direct_distance_m"], 3),
            "direction_used_distance_m": round(direction["used_distance_m"], 3),
            "direction_extended": direction["extended"],
            "direction_skipped_points": direction["skipped_points"],
            "travel_bearing_deg": round(bearing_deg(start, end), 3),
            "travel_vector": direction["vector"],
        })

    ways = extract_local_highways(
        PBF_PATH,
        [(point["lat"], point["lng"]) for point in points],
        node_buffer_m=120.0,
    )
    way_by_id = {int(way["id"]): way for way in ways}

    output_rows = []
    for point in points:
        row = dict(point)
        vector = row.pop("travel_vector")
        for rank in (1, 2):
            way_id = point[f"rank{rank}_way_id"]
            way = way_by_id.get(way_id)
            if way is None:
                raise ValueError(f"{point['point_id']}: way {way_id} missing from local PBF extraction")
            segment_idx, segment_dist, way_bearing = nearest_segment(point["lat"], point["lng"], way["geometry"])
            detected, confidence = await production_oneway_result(point, way, vector)
            row.update({
                f"rank{rank}_highway": way["tags"].get("highway", ""),
                f"rank{rank}_oneway": way["tags"].get("oneway", ""),
                f"rank{rank}_nearest_segment_idx": segment_idx,
                f"rank{rank}_nearest_segment_dist_m": round(segment_dist, 3),
                f"rank{rank}_way_bearing_deg": round(way_bearing, 3),
                f"rank{rank}_direction_diff_deg": round(angle_difference_deg(point["travel_bearing_deg"], way_bearing), 3),
                f"rank{rank}_production_oneway_detected": detected,
                f"rank{rank}_production_confidence": confidence,
            })
        output_rows.append(row)

    output_rows.sort(key=lambda row: (row["group"], row["point_id"]))
    fields = list(output_rows[0].keys())
    with CSV_OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    stats = {
        group: {
            "rank1": group_stats([row for row in output_rows if row["group"] == group], "rank1_direction_diff_deg"),
            "rank2": group_stats([row for row in output_rows if row["group"] == group], "rank2_direction_diff_deg"),
        }
        for group in ("A", "B", "C")
    }
    b1, c1 = stats["B"]["rank1"], stats["C"]["rank1"]
    range_overlap = max(b1["min"], c1["min"]) <= min(b1["max"], c1["max"])
    quadrant = {}
    for group in ("A", "B", "C"):
        subset = [row for row in output_rows if row["group"] == group]
        quadrant[group] = {
            "rank1_ge_90_rank2_lt_90": sum(row["rank1_direction_diff_deg"] >= 90 and row["rank2_direction_diff_deg"] < 90 for row in subset),
            "rank1_ge_90_rank2_ge_90": sum(row["rank1_direction_diff_deg"] >= 90 and row["rank2_direction_diff_deg"] >= 90 for row in subset),
            "rank1_lt_90_rank2_lt_90": sum(row["rank1_direction_diff_deg"] < 90 and row["rank2_direction_diff_deg"] < 90 for row in subset),
            "rank1_lt_90_rank2_ge_90": sum(row["rank1_direction_diff_deg"] < 90 and row["rank2_direction_diff_deg"] >= 90 for row in subset),
        }
    summary = {
        "population": {
            "description": "旧方式で16way・18点＋現行方式で新規に検出した2way・2点（計18way・20点）",
            "A": sum(row["group"] == "A" for row in output_rows),
            "B": sum(row["group"] == "B" for row in output_rows),
            "C": sum(row["group"] == "C" for row in output_rows),
        },
        "direction_rule": "raw polylineの直前点→当該点。始点は当該点→直後点。直接参照点が5m未満なら同じ方向へ外側の点まで延長",
        "nearest_segment_rule": "services.overpass._point_to_way_dist_mで最短の局所線分",
        "percentile_rule": "sorted values, linear interpolation at (n-1)*p",
        "stats": stats,
        "rank1_B_C_ranges_overlap": range_overlap,
        "B3_decision": "(b)" if range_overlap else "(a)",
        "extended_direction_point_count": sum(row["direction_extended"] for row in output_rows),
        "direct_neighbor_under_5m_count": sum(row["direct_neighbor_distance_m"] < 5.0 for row in output_rows),
        "quadrant_counts_at_90_deg": quadrant,
        "csv": "backend/data/direction_difference_points.csv",
        "image": "backend/data/direction_difference_distribution.png",
    }
    JSON_OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_plot(output_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--plot-only" in sys.argv:
        write_plot(load_output_rows())
    else:
        asyncio.run(main())
