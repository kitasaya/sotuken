"""Batch 20の現行12点・旧18点で非閉ループ修正前後を比較する。"""

from __future__ import annotations

import asyncio
import csv

from audit_batch20_direction_impact import (
    BACKEND,
    PBF,
    current_points,
    google_routes,
    old_points,
    route_context,
)
from scripts.local_osm_pbf import extract_local_highways
from services.law_checker import check_oneway_violation
from services.route_analyzer import _trim_geometry


OUTPUT = BACKEND / "data" / "batch21_nonclosed_regression_30.csv"


def before_fix(geom: list, p_start: list, p_end: list) -> list:
    def dist_sq(a, b):
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

    i_s = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_start))
    i_e = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_end))
    lo, hi = min(i_s, i_e), max(i_s, i_e)
    trimmed = geom[lo:hi + 1]
    return trimmed if len(trimmed) >= 2 else geom


async def detected(point: dict, way: dict, geometry: list, vector: list[float]) -> bool:
    result = await check_oneway_violation(
        [[point["lng"], point["lat"]]],
        tags_list=[way["tags"]],
        geometries=[geometry],
        travel_vectors=[vector],
    )
    return bool(result)


async def main() -> None:
    points = current_points() + old_points()
    ways = extract_local_highways(
        PBF,
        [(point["lat"], point["lng"]) for point in points],
        node_buffer_m=150.0,
    )
    by_id = {int(way["id"]): way for way in ways}
    routes = google_routes()
    rows = []

    for point in points:
        way = by_id[point["way_id"]]
        geom = way["geometry"]
        if geom[0] == geom[-1]:
            raise AssertionError(f"監査対象は非閉ループのはず: {point['point_id']}")
        route = routes[point["label"]]
        route_idx, _, vector = route_context(point, route)
        start_idx = max(0, route_idx - 1)
        end_idx = min(len(route) - 1, route_idx + 1)
        segment = route[start_idx:end_idx + 1]
        old_geom = before_fix(geom, segment[0], segment[-1])
        new_geom = _trim_geometry(geom, segment[0], segment[-1], segment)
        old_detected = await detected(point, way, old_geom, vector)
        new_detected = await detected(point, way, new_geom, vector)
        rows.append({
            "population": point["population"],
            "point_id": point["point_id"],
            "way_id": point["way_id"],
            "geometry_identical": old_geom == new_geom,
            "before_detected": old_detected,
            "after_detected": new_detected,
            "classification_identical": old_detected == new_detected,
        })

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    geometry_mismatches = sum(row["geometry_identical"] is False for row in rows)
    classification_mismatches = sum(row["classification_identical"] is False for row in rows)
    print(f"points={len(rows)} geometry_mismatches={geometry_mismatches} "
          f"classification_mismatches={classification_mismatches}")
    if len(rows) != 30 or geometry_mismatches or classification_mismatches:
        raise AssertionError("非閉ループ30点の回帰比較に不一致")


if __name__ == "__main__":
    asyncio.run(main())
