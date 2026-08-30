"""Batch 20: 現行12点と旧18点の方向判定を全件監査する。

既存の check_oneway_violation をそのまま呼び、その結果を各判定点直近の
OSM セグメント方向と Google polyline の進行方向による局所照合と比較する。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from pathlib import Path
import re
import subprocess

from analyze_batch20_oneway_direction import (
    BACKEND,
    GOOGLE_INPUT,
    PBF,
    ROOT,
    angle_deg,
    closest_on_geometry,
    google_route,
    haversine_m,
    vector,
)
from scripts.known_violations import KNOWN_VIOLATIONS
from scripts.local_osm_pbf import extract_local_highways
from services.external_route_scorer import _travel_vector_at, decode_polyline
from services.law_checker import check_oneway_violation


TAG = "measurement-freeze-20260828"
CURRENT_SOURCE = "bicycle-navi/backend/data/verify_match_margin_points.csv"
OLD_RECORD = ROOT / "docs" / "検証記録" / "検証記録_18点.md"
OUTPUT_CSV = BACKEND / "data" / "batch20_direction_impact.csv"
OUTPUT_JSON = BACKEND / "data" / "batch20_direction_impact.json"


def git_text(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{TAG}:{path}"], cwd=ROOT, text=True, encoding="utf-8"
    )


def point_ids_from_freeze() -> dict[tuple[str, int, int], str]:
    result = {}
    path = BACKEND / "data" / "measurement_freeze_20260828.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            detail = row.get("detail", "")
            match = re.search(r"point_id=([^;]+);.*way_id=(\d+); sample_idx=(\d+)", detail)
            if match:
                result[(row["label"], int(match.group(2)), int(match.group(3)))] = match.group(1)
    return result


def current_points() -> list[dict]:
    ids = point_ids_from_freeze()
    rows = csv.DictReader(io.StringIO(git_text(CURRENT_SOURCE)))
    result = []
    for row in rows:
        if row["oneway_violation"].strip().lower() != "true":
            continue
        way_id = int(row["perp_rank1_way_id"])
        sample_idx = int(row["sample_idx"])
        result.append({
            "population": "current12",
            "point_id": ids.get((row["label"], way_id, sample_idx), f"sample-{sample_idx}"),
            "label": row["label"],
            "way_id": way_id,
            "lat": float(row["lat"]),
            "lng": float(row["lng"]),
            "route_idx": int(row["route_idx"]),
            "recorded_group": row["verify_group"],
            "recorded_diag_angle_deg": row["diag_angle_deg"],
        })
    if len(result) != 12:
        raise RuntimeError(f"current oneway points must be 12, got {len(result)}")
    return result


def old_points() -> list[dict]:
    label_by_way = {way_id: values[1] for way_id, values in KNOWN_VIOLATIONS.items()}
    result = []
    pattern = re.compile(
        r"^\| (P\d{2}) \| (\d+) \| ([A-D]) \| ([0-9.]+) \| ([0-9.]+) \|"
    )
    for line in OLD_RECORD.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        point_id, way_id_text, group, lat, lng = match.groups()
        way_id = int(way_id_text)
        result.append({
            "population": "legacy18",
            "point_id": point_id,
            "label": label_by_way[way_id],
            "way_id": way_id,
            "lat": float(lat),
            "lng": float(lng),
            "route_idx": None,
            "recorded_group": "C" if point_id == "P18" else group,
            "recorded_diag_angle_deg": "",
        })
    if len(result) != 18:
        raise RuntimeError(f"legacy points must be 18, got {len(result)}")
    return result


def google_routes() -> dict[str, list[list[float]]]:
    with GOOGLE_INPUT.open(encoding="utf-8-sig", newline="") as stream:
        return {row["label"]: decode_polyline(row["polyline"]) for row in csv.DictReader(stream)}


def route_context(point: dict, route: list[list[float]]) -> tuple[int, float, list[float]]:
    query = [point["lng"], point["lat"]]
    if point["route_idx"] is not None:
        index = min(point["route_idx"], len(route) - 1)
    else:
        index = min(range(len(route)), key=lambda idx: haversine_m(query, route[idx]))
    return index, haversine_m(query, route[index]), _travel_vector_at(route, index)


async def main() -> None:
    points = current_points() + old_points()
    ways = extract_local_highways(
        PBF,
        [(point["lat"], point["lng"]) for point in points],
        node_buffer_m=150.0,
    )
    by_id = {int(way["id"]): way for way in ways}
    routes = google_routes()
    output = []

    for point in points:
        way = by_id[point["way_id"]]
        geometry = way["geometry"]
        route = routes[point["label"]]
        route_idx, route_point_distance, travel_vector = route_context(point, route)
        location = closest_on_geometry((point["lng"], point["lat"]), geometry)
        segment_index = location["segment_index_zero_based"]
        local_way_vector = vector(geometry[segment_index], geometry[segment_index + 1])
        oneway = way["tags"].get("oneway", "no")
        allowed_vector = [-local_way_vector[0], -local_way_vector[1]] if oneway == "-1" else local_way_vector
        local_angle = angle_deg(allowed_vector, travel_vector, at_lat=point["lat"])
        local_reverse = local_angle > 90.0
        existing = await check_oneway_violation(
            [[point["lng"], point["lat"]]],
            tags_list=[way["tags"]],
            geometries=[geometry],
            travel_vectors=[travel_vector],
        )
        output.append({
            **point,
            "oneway": oneway,
            "highway": way["tags"].get("highway", ""),
            "name": way["tags"].get("name", ""),
            "way_node_count": len(geometry),
            "closed_way": geometry[0] == geometry[-1],
            "route_idx_used": route_idx,
            "detection_to_route_vertex_m": round(route_point_distance, 3),
            "detection_to_way_m": round(location["distance_m"], 3),
            "local_way_segment_number_one_based": location["segment_number_one_based"],
            "local_allowed_direction_start": geometry[segment_index] if oneway != "-1" else geometry[segment_index + 1],
            "local_allowed_direction_end": geometry[segment_index + 1] if oneway != "-1" else geometry[segment_index],
            "travel_vector": travel_vector,
            "local_angle_route_vs_allowed_deg": round(local_angle, 3),
            "local_direction_says_reverse": local_reverse,
            "existing_function_detected": bool(existing),
            "existing_vs_local_consistent": bool(existing) == local_reverse,
        })

    fields = list(output[0].keys())
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    OUTPUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for population in ("current12", "legacy18"):
        rows = [row for row in output if row["population"] == population]
        print(population, "count", len(rows), "mismatches", sum(not row["existing_vs_local_consistent"] for row in rows))
        for row in rows:
            print(
                row["point_id"], row["way_id"], row["recorded_group"],
                f"angle={row['local_angle_route_vs_allowed_deg']}",
                f"local_reverse={row['local_direction_says_reverse']}",
                f"existing={row['existing_function_detected']}",
                f"closed={row['closed_way']}",
                f"consistent={row['existing_vs_local_consistent']}",
            )


if __name__ == "__main__":
    asyncio.run(main())
