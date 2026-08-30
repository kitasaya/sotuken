"""Batch 20: way 28413951 の同定と一方通行方向判定を再現する。

固定 PBF、保存済み Google polyline、稼働中の固定 GraphHopper 11.0 を入力にする。
逆走判定は再実装せず、services.law_checker.check_oneway_violation を呼び出す。
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
from pathlib import Path
import sys
import types
from urllib.parse import urlencode
from urllib.request import urlopen


BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
ROOT = PROJECT.parent
PBF = PROJECT / "graphhopper" / "kanto-260801.osm.pbf"
GOOGLE_INPUT = BACKEND / "data" / "google_routes_input.csv"
OUTPUT = BACKEND / "data" / "batch20_oneway_direction.json"
TARGET_WAY_ID = 28_413_951
LABEL = "横浜→みなとみらい"
ORIGIN = (35.4658, 139.6225)
DESTINATION = (35.4581, 139.6380)
REVIEW_POINT = (139.6233374, 35.4643064)  # [lng, lat]
DOCUMENTED_DECISION_POINT = (139.623297, 35.464037)  # docs/台帳/未確定事項リスト.md:30


def _install_httpx_import_stub() -> None:
    """依存未導入の分析環境でも、ネットワーク未使用の判定関数を import 可能にする。"""
    try:
        __import__("httpx")
        return
    except ModuleNotFoundError:
        pass

    module = types.ModuleType("httpx")

    class HTTPError(Exception):
        pass

    class TimeoutException(HTTPError):
        pass

    class AsyncClient:
        pass

    class Limits:
        def __init__(self, **_: object) -> None:
            pass

    module.HTTPError = HTTPError
    module.TimeoutException = TimeoutException
    module.AsyncClient = AsyncClient
    module.Limits = Limits
    sys.modules["httpx"] = module


sys.path.insert(0, str(BACKEND))
_install_httpx_import_stub()

from scripts.local_osm_pbf import extract_local_highways  # noqa: E402
from services.external_route_scorer import (  # noqa: E402
    _travel_vector_at,
    decode_polyline,
)
from services.law_checker import check_oneway_violation  # noqa: E402
from services.route_analyzer import _trim_geometry  # noqa: E402


def haversine_m(a: list[float] | tuple[float, float], b: list[float] | tuple[float, float]) -> float:
    radius = 6_371_000.0
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = math.radians(b[0] - a[0])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, value)))


def local_xy(point: list[float] | tuple[float, float], origin: tuple[float, float]) -> tuple[float, float]:
    lng, lat = point
    return (
        math.radians(lng - origin[0]) * 6_371_000.0 * math.cos(math.radians(origin[1])),
        math.radians(lat - origin[1]) * 6_371_000.0,
    )


def closest_on_geometry(point: tuple[float, float], geometry: list[list[float]]) -> dict:
    px, py = 0.0, 0.0
    cumulative = 0.0
    best: dict | None = None
    for index, (a, b) in enumerate(zip(geometry, geometry[1:])):
        ax, ay = local_xy(a, point)
        bx, by = local_xy(b, point)
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = max(0.0, min(1.0, (-(ax - px) * dx - (ay - py) * dy) / denom)) if denom else 0.0
        qx, qy = ax + t * dx, ay + t * dy
        distance = math.hypot(qx, qy)
        segment_length = haversine_m(a, b)
        candidate = {
            "distance_m": distance,
            "segment_index_zero_based": index,
            "segment_number_one_based": index + 1,
            "segment_fraction": t,
            "distance_from_start_m": cumulative + t * segment_length,
            "segment_start": a,
            "segment_end": b,
        }
        if best is None or distance < best["distance_m"]:
            best = candidate
        cumulative += segment_length
    if best is None:
        raise ValueError("geometry needs at least two nodes")
    best["way_length_m"] = cumulative
    return best


def angle_deg(a: list[float], b: list[float], *, at_lat: float) -> float:
    scale = math.cos(math.radians(at_lat))
    ax, ay = a[0] * scale, a[1]
    bx, by = b[0] * scale, b[1]
    denom = math.hypot(ax, ay) * math.hypot(bx, by)
    if denom == 0:
        return 0.0
    dot = max(-1.0, min(1.0, (ax * bx + ay * by) / denom))
    return math.degrees(math.acos(dot))


def vector(a: list[float], b: list[float]) -> list[float]:
    return [b[0] - a[0], b[1] - a[1]]


def graphhopper_route() -> dict:
    params = [
        ("point", f"{ORIGIN[0]},{ORIGIN[1]}"),
        ("point", f"{DESTINATION[0]},{DESTINATION[1]}"),
        ("profile", "bike"),
        ("locale", "ja"),
        ("points_encoded", "false"),
        ("details", "osm_way_id"),
        ("details", "road_class"),
    ]
    with urlopen("http://localhost:8989/route?" + urlencode(params), timeout=30) as response:
        return json.load(response)["paths"][0]


def google_route() -> tuple[str, list[list[float]]]:
    with GOOGLE_INPUT.open(encoding="utf-8-sig", newline="") as stream:
        row = next(row for row in csv.DictReader(stream) if row["label"] == LABEL)
    return row["polyline"], decode_polyline(row["polyline"])


def nearest_route_segment(point: tuple[float, float], route: list[list[float]]) -> dict:
    result = closest_on_geometry(point, route)
    index = result["segment_index_zero_based"]
    result["travel_vector"] = vector(route[index], route[index + 1])
    return result


async def main() -> None:
    path = graphhopper_route()
    route_points = path["points"]["coordinates"]
    target_detail = next(seg for seg in path["details"]["osm_way_id"] if int(seg[2]) == TARGET_WAY_ID)
    start_idx, end_idx = int(target_detail[0]), int(target_detail[1])
    midpoint_idx = (start_idx + end_idx) // 2
    decision_point = route_points[midpoint_idx]

    local_ways = extract_local_highways(
        PBF,
        [(DOCUMENTED_DECISION_POINT[1], DOCUMENTED_DECISION_POINT[0]), (REVIEW_POINT[1], REVIEW_POINT[0])],
        node_buffer_m=250.0,
    )
    by_id = {int(way["id"]): way for way in local_ways}
    target = by_id[TARGET_WAY_ID]
    target_geometry = target["geometry"]
    p_start, p_end = route_points[start_idx], route_points[end_idx]
    trimmed_geometry = _trim_geometry(target_geometry, p_start, p_end)
    system_travel_vector = vector(p_start, p_end)

    violations = await check_oneway_violation(
        [decision_point],
        tags_list=[target["tags"]],
        geometries=[trimmed_geometry],
        travel_vectors=[system_travel_vector],
    )

    inspection_matches = []
    decision_matches = []
    nearby_oneways = []
    for way in local_ways:
        geometry = way["geometry"]
        if len(geometry) < 2:
            continue
        inspection_match = closest_on_geometry(REVIEW_POINT, geometry)
        decision_match = closest_on_geometry(tuple(decision_point), geometry)
        inspection_matches.append({"way_id": way["id"], "highway": way["tags"].get("highway"), **inspection_match})
        decision_matches.append({"way_id": way["id"], "highway": way["tags"].get("highway"), **decision_match})
        oneway = way["tags"].get("oneway")
        if oneway in {"yes", "true", "1", "-1"} and min(
            closest_on_geometry(DOCUMENTED_DECISION_POINT, geometry)["distance_m"],
            inspection_match["distance_m"],
        ) <= 100.0:
            nearby_oneways.append({
                "way_id": way["id"],
                "oneway": oneway,
                "tags": way["tags"],
                "geometry": geometry,
                "direction_start": geometry[0],
                "direction_end": geometry[-1],
                "distance_to_documented_decision_point_m": closest_on_geometry(DOCUMENTED_DECISION_POINT, geometry)["distance_m"],
                "distance_to_review_point_m": inspection_match["distance_m"],
            })

    inspection_matches.sort(key=lambda item: item["distance_m"])
    decision_matches.sort(key=lambda item: item["distance_m"])
    nearby_oneways.sort(key=lambda item: min(item["distance_to_documented_decision_point_m"], item["distance_to_review_point_m"]))

    encoded, google_points = google_route()
    google_near_target = nearest_route_segment(tuple(decision_point), google_points)
    target_decision_position = closest_on_geometry(tuple(decision_point), target_geometry)
    review_target_position = closest_on_geometry(REVIEW_POINT, target_geometry)
    local_segment_index = target_decision_position["segment_index_zero_based"]
    osm_local_vector = vector(target_geometry[local_segment_index], target_geometry[local_segment_index + 1])

    per_segment = []
    against_count = 0
    for index, (a, b) in enumerate(zip(trimmed_geometry, trimmed_geometry[1:])):
        way_vector = vector(a, b)
        dot = way_vector[0] * system_travel_vector[0] + way_vector[1] * system_travel_vector[1]
        if dot < 0:
            against_count += 1
        per_segment.append({
            "trimmed_segment_index_zero_based": index,
            "start": a,
            "end": b,
            "dot_product_raw_degrees": dot,
            "angle_to_system_travel_vector_deg": angle_deg(way_vector, system_travel_vector, at_lat=decision_point[1]),
            "counted_against_for_oneway_yes": dot < 0,
        })

    bicycle_related_tags = {
        key: value for key, value in target["tags"].items()
        if "bicycle" in key or key == "cycleway" or key.startswith("cycleway:")
    }

    result = {
        "inputs": {
            "pbf": str(PBF.relative_to(ROOT)),
            "google_input": str(GOOGLE_INPUT.relative_to(ROOT)),
            "graphhopper_distance_m": path["distance"],
            "documented_decision_point_lng_lat": DOCUMENTED_DECISION_POINT,
            "review_point_lng_lat": REVIEW_POINT,
            "google_polyline": encoded,
        },
        "target_way": {
            "way_id": TARGET_WAY_ID,
            "tags": target["tags"],
            "bicycle_related_tags": bicycle_related_tags,
            "geometry": target_geometry,
            "start": target_geometry[0],
            "end": target_geometry[-1],
            "documented_decision_point_position": closest_on_geometry(DOCUMENTED_DECISION_POINT, target_geometry),
            "actual_graphhopper_decision_point_lng_lat": decision_point,
            "actual_graphhopper_decision_point_position": target_decision_position,
            "review_point_position": review_target_position,
        },
        "review_point_nearest_highways": inspection_matches[:10],
        "decision_point_nearest_highways": decision_matches[:10],
        "nearby_oneway_ways_within_100m_of_documented_or_review_point": nearby_oneways,
        "system_original_route_reproduction": {
            "osm_way_id_detail": target_detail,
            "route_start_coordinate_index": start_idx,
            "route_end_coordinate_index": end_idx,
            "decision_coordinate_index": midpoint_idx,
            "route_coordinates_on_detail_inclusive": route_points[start_idx:end_idx + 1],
            "p_start": p_start,
            "p_end": p_end,
            "travel_vector": system_travel_vector,
            "trimmed_way_geometry": trimmed_geometry,
            "per_way_segment_calculation": per_segment,
            "against_count": against_count,
            "segment_count": len(trimmed_geometry) - 1,
            "majority_threshold": (len(trimmed_geometry) - 1) / 2,
            "existing_check_oneway_violation_output": violations,
            "existing_function_detected_violation": bool(violations),
            "local_osm_segment_angle_to_system_travel_deg": angle_deg(osm_local_vector, system_travel_vector, at_lat=decision_point[1]),
        },
        "google_route_comparison": {
            "decoded_coordinate_count": len(google_points),
            "nearest_segment_to_system_decision_point": google_near_target,
            "angle_local_osm_segment_to_google_travel_deg": angle_deg(
                osm_local_vector, google_near_target["travel_vector"], at_lat=decision_point[1]
            ),
            "distance_from_google_route_to_system_decision_point_m": google_near_target["distance_m"],
        },
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT),
        "target_tags": target["tags"],
        "decision_point": decision_point,
        "review_to_target_m": review_target_position["distance_m"],
        "review_nearest": inspection_matches[:3],
        "nearby_oneway_ids": [item["way_id"] for item in nearby_oneways],
        "existing_function_detected_violation": bool(violations),
        "against_count": against_count,
        "segment_count": len(trimmed_geometry) - 1,
        "google_distance_to_decision_m": google_near_target["distance_m"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
