"""Batch 21: R2の全379判定点を固定PBFと既存判定器で再採点する。"""

from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path
import subprocess

from measure_batch21_exclusion import BACKEND, PBF, ROOT, extract_ways_by_ids
from services.external_route_scorer import _is_way_misaligned, _travel_vector_at, decode_polyline
from services.law_checker import check_oneway_violation


SOURCE_TAG = "measurement-freeze-20260828"
SOURCE_PATH = "bicycle-navi/backend/data/verify_match_margin_points.csv"
GOOGLE_INPUT = BACKEND / "data" / "google_routes_input.csv"
OUTPUT = BACKEND / "data" / "batch21_r2_remeasurement.csv"


def tagged_rows() -> list[dict]:
    content = subprocess.check_output(
        ["git", "show", f"{SOURCE_TAG}:{SOURCE_PATH}"],
        cwd=ROOT,
    ).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(content)))


def routes() -> dict[str, list[list[float]]]:
    with GOOGLE_INPUT.open(encoding="utf-8-sig", newline="") as stream:
        return {row["label"]: decode_polyline(row["polyline"]) for row in csv.DictReader(stream)}


async def main() -> None:
    rows = tagged_rows()
    route_by_label = routes()
    way_ids = {int(row["perp_rank1_way_id"]) for row in rows if row["perp_rank1_way_id"]}
    ways = extract_ways_by_ids(PBF, way_ids)

    output = []
    for row in rows:
        way_id_text = row["perp_rank1_way_id"]
        point = [float(row["lng"]), float(row["lat"])]
        route = route_by_label[row["label"]]
        route_idx = min(int(row["route_idx"]), len(route) - 1)
        travel_vector = _travel_vector_at(route, route_idx)
        if way_id_text:
            way_id = int(way_id_text)
            entry = ways[way_id]
            tags = entry["tags"]
            geometry = entry["geometry"]
        else:
            way_id = None
            tags = {}
            geometry = []
        filtered_tags = {} if _is_way_misaligned(geometry, travel_vector) else tags
        detected = bool(await check_oneway_violation(
            [point],
            tags_list=[filtered_tags],
            geometries=[geometry],
            travel_vectors=[travel_vector],
        ))
        old_detected = row["oneway_violation"].strip().lower() == "true"
        output.append({
            "label": row["label"],
            "sample_idx": row["sample_idx"],
            "route_idx": row["route_idx"],
            "lat": row["lat"],
            "lng": row["lng"],
            "way_id": way_id_text,
            "oneway": tags.get("oneway", ""),
            "match_margin_m": row["match_margin_m"],
            "old_oneway_violation": old_detected,
            "new_oneway_violation": detected,
            "unchanged": old_detected == detected,
        })

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)

    detected_rows = [row for row in output if row["new_oneway_violation"]]
    detected_margins = [float(row["match_margin_m"]) for row in detected_rows]
    print({
        "points": len(output),
        "changed": sum(not row["unchanged"] for row in output),
        "detected": len(detected_rows),
        "detected_way_ids": [int(row["way_id"]) for row in detected_rows],
        "way_28413951_matched_points": sum(row["way_id"] == "28413951" for row in output),
        "detected_margin_min": min(detected_margins),
        "detected_margin_max": max(detected_margins),
    })


if __name__ == "__main__":
    asyncio.run(main())
