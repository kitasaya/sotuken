"""Batch 21: 固定PBFで15ペアの標準ルートとLayer 1出力を比較する。

条件BはGraphHopperの設定済みbike profileをそのまま使う標準GETルート。
条件Aは現行システムと同じく、条件Bの一方通行違反を検出した場合だけ
rerouter.pyと同じcustom_model areasをPOSTし、違反がなければBをそのまま返す。
判定はservices.law_checker.check_oneway_violationを再利用する。
"""

from __future__ import annotations

import asyncio
import csv
import json
import math
from pathlib import Path
import struct
import sys
import types
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
ROOT = PROJECT.parent
DATA = BACKEND / "data"
PBF = PROJECT / "graphhopper" / "kanto-260801.osm.pbf"
OD_PAIRS = DATA / "od_pairs.csv"
OUT_CSV = DATA / "batch21_exclusion_comparison.csv"
OUT_JSON = DATA / "batch21_exclusion_comparison.json"
TRAVERSALS_CSV = DATA / "batch21_tagged_oneway_traversals.csv"
GH_BASE = "http://localhost:8989"
ONEWAY_VALUES = {"yes", "true", "1", "-1"}
EXEMPT_CYCLEWAYS = {"opposite", "opposite_lane", "opposite_track"}


def _install_httpx_import_stub() -> None:
    try:
        __import__("httpx")
        return
    except ModuleNotFoundError:
        pass
    module = types.ModuleType("httpx")
    module.AsyncClient = object
    module.HTTPError = RuntimeError
    module.TimeoutException = RuntimeError
    module.Limits = object
    sys.modules["httpx"] = module


sys.path.insert(0, str(BACKEND))
_install_httpx_import_stub()

from scripts.local_osm_pbf import (  # noqa: E402
    _data_blocks,
    _dense_nodes,
    _primitive_block,
    _ways,
)
from services.law_checker import check_oneway_violation  # noqa: E402
from services.rerouter import _make_block_polygon  # noqa: E402
from services.route_analyzer import _trim_geometry  # noqa: E402


def graphhopper_get(od: dict) -> dict:
    params = [
        ("point", f"{od['origin_lat']},{od['origin_lng']}"),
        ("point", f"{od['dest_lat']},{od['dest_lng']}"),
        ("profile", "bike"),
        ("locale", "ja"),
        ("points_encoded", "false"),
        ("details", "osm_way_id"),
        ("details", "road_class"),
    ]
    with urlopen(f"{GH_BASE}/route?{urlencode(params)}", timeout=60) as response:
        return json.load(response)["paths"][0]


def graphhopper_custom(od: dict, violations: list[dict]) -> dict:
    features = []
    priority = []
    seen = set()
    for violation in violations:
        key = (round(violation["lat"], 4), round(violation["lng"], 4))
        if key in seen:
            continue
        seen.add(key)
        area_id = f"blocked_area_{len(features)}"
        features.append({
            "type": "Feature",
            "id": area_id,
            "geometry": {
                "type": "Polygon",
                "coordinates": _make_block_polygon(violation["lat"], violation["lng"]),
            },
        })
        priority.append({"if": f"in_{area_id}", "multiply_by": "0"})
    body = {
        "points": [
            [float(od["origin_lng"]), float(od["origin_lat"])],
            [float(od["dest_lng"]), float(od["dest_lat"])],
        ],
        "profile": "bike",
        "locale": "ja",
        "points_encoded": False,
        "ch.disable": True,
        "custom_model": {
            "priority": priority,
            "areas": {"type": "FeatureCollection", "features": features},
        },
        "details": ["osm_way_id", "road_class"],
    }
    request = Request(
        f"{GH_BASE}/route",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)["paths"][0]


def collect_way_info(route: dict) -> dict[int, dict]:
    points = route["points"]["coordinates"]
    result = {}
    for start, end, way_id in route.get("details", {}).get("osm_way_id", []):
        way_id = int(way_id)
        if way_id in result:
            continue
        start, end = int(start), int(end)
        result[way_id] = {
            "start_idx": start,
            "end_idx": end,
            "point": points[(start + end) // 2],
        }
    return result


def extract_ways_by_ids(pbf_path: Path, target_ids: set[int]) -> dict[int, dict]:
    """固定PBFを2回走査し、指定wayのタグ・全ノード座標を得る。"""
    selected = {}
    refs = set()
    print(f"PBF pass 1/2: {len(target_ids)} target way IDs", flush=True)
    for block_index, block in _data_blocks(pbf_path):
        strings, groups, _, _, _ = _primitive_block(block)
        for group in groups:
            for way_id, tags, way_refs in _ways(group, strings):
                if way_id not in target_ids:
                    continue
                selected[way_id] = {"tags": tags, "refs": way_refs}
                refs.update(way_refs)
        if block_index % 1000 == 0:
            print(f"  blocks={block_index}, ways={len(selected)}", flush=True)
    missing = target_ids - set(selected)
    if missing:
        raise RuntimeError(f"PBF missing {len(missing)} way IDs: {sorted(missing)[:10]}")

    coordinates = {}
    print(f"PBF pass 2/2: {len(refs)} node coordinates", flush=True)
    for block_index, block in _data_blocks(pbf_path):
        _, groups, granularity, lat_offset, lon_offset = _primitive_block(block)
        for group in groups:
            for node_id, lat, lon in _dense_nodes(group, granularity, lat_offset, lon_offset):
                if node_id in refs:
                    coordinates[node_id] = [lon, lat]
        if block_index % 1000 == 0:
            print(f"  blocks={block_index}, nodes={len(coordinates)}", flush=True)
    return {
        way_id: {
            "tags": entry["tags"],
            "geometry": [coordinates[ref] for ref in entry["refs"]],
        }
        for way_id, entry in selected.items()
    }


async def score_route(route: dict, ways: dict[int, dict]) -> dict:
    points = route["points"]["coordinates"]
    info = collect_way_info(route)
    way_ids = list(info)
    check_points = []
    tags = []
    geometries = []
    vectors = []
    for way_id in way_ids:
        segment = info[way_id]
        start = segment["start_idx"]
        end = min(segment["end_idx"], len(points) - 1)
        p_start, p_end = points[start], points[end]
        route_segment = points[start:end + 1]
        entry = ways[way_id]
        check_points.append(segment["point"])
        tags.append(entry["tags"])
        geometries.append(_trim_geometry(entry["geometry"], p_start, p_end, route_segment))
        vectors.append([p_end[0] - p_start[0], p_end[1] - p_start[1]])
    violations = await check_oneway_violation(
        check_points,
        tags_list=tags,
        geometries=geometries,
        travel_vectors=vectors,
    )
    point_to_way = {(point[1], point[0]): way_id for point, way_id in zip(check_points, way_ids)}
    for violation in violations:
        violation["way_id"] = point_to_way[(violation["lat"], violation["lng"])]
    return {
        "violations": violations,
        "way_ids": way_ids,
        "info": info,
        "trimmed": dict(zip(way_ids, geometries)),
        "vectors": dict(zip(way_ids, vectors)),
    }


def same_geometry(a: dict, b: dict) -> bool:
    return a["points"]["coordinates"] == b["points"]["coordinates"]


async def main() -> None:
    with OD_PAIRS.open(encoding="utf-8-sig", newline="") as stream:
        od_rows = list(csv.DictReader(stream))

    standard_routes = {row["label"]: graphhopper_get(row) for row in od_rows}
    target_ids = {
        way_id
        for route in standard_routes.values()
        for way_id in collect_way_info(route)
    }
    ways = extract_ways_by_ids(PBF, target_ids)

    standard_scores = {
        label: await score_route(route, ways)
        for label, route in standard_routes.items()
    }

    condition_a_routes = {}
    invoked = {}
    for od in od_rows:
        label = od["label"]
        violations = standard_scores[label]["violations"]
        invoked[label] = bool(violations)
        condition_a_routes[label] = (
            graphhopper_custom(od, violations) if violations else standard_routes[label]
        )

    extra_ids = {
        way_id
        for route in condition_a_routes.values()
        for way_id in collect_way_info(route)
    } - set(ways)
    if extra_ids:
        ways.update(extract_ways_by_ids(PBF, extra_ids))
    condition_a_scores = {
        label: await score_route(route, ways)
        for label, route in condition_a_routes.items()
    }

    rows = []
    detail = []
    traversal_rows = []
    for od in od_rows:
        label = od["label"]
        b_route = standard_routes[label]
        a_route = condition_a_routes[label]
        b_score = standard_scores[label]
        a_score = condition_a_scores[label]
        b_way_ids = [int(v["way_id"]) for v in b_score["violations"]]
        a_way_ids = [int(v["way_id"]) for v in a_score["violations"]]
        rows.append({
            "label": label,
            "road_type": od["road_type"],
            "condition_a_distance_m": round(float(a_route["distance"]), 1),
            "condition_b_distance_m": round(float(b_route["distance"]), 1),
            "a_minus_b_m": round(float(a_route["distance"]) - float(b_route["distance"]), 1),
            "condition_a_oneway_count": len(a_way_ids),
            "condition_a_oneway_way_ids": ";".join(map(str, a_way_ids)),
            "condition_b_oneway_count": len(b_way_ids),
            "condition_b_oneway_way_ids": ";".join(map(str, b_way_ids)),
            "custom_model_area_invoked": invoked[label],
            "route_geometry_identical": same_geometry(a_route, b_route),
            "condition_b_contains_way_28413948": 28_413_948 in b_score["way_ids"],
            "condition_b_contains_way_28413951": 28_413_951 in b_score["way_ids"],
        })
        detail.append({
            "label": label,
            "condition_a": {"route": a_route, "oneway_violations": a_score["violations"]},
            "condition_b": {"route": b_route, "oneway_violations": b_score["violations"]},
        })

        detected = set(b_way_ids)
        for way_id, segment in b_score["info"].items():
            tags = ways[way_id]["tags"]
            oneway = tags.get("oneway", "no")
            if oneway not in ONEWAY_VALUES:
                continue
            if tags.get("oneway:bicycle") == "no" or tags.get("cycleway", "") in EXEMPT_CYCLEWAYS:
                result = "bicycle_exempt"
            elif way_id in detected:
                result = "detected"
            else:
                result = "forward_travel"
            traversal_rows.append({
                "label": label,
                "way_id": way_id,
                "start_idx": segment["start_idx"],
                "end_idx": segment["end_idx"],
                "oneway": oneway,
                "highway": tags.get("highway", ""),
                "name": tags.get("name", ""),
                "closed_way": ways[way_id]["geometry"][0] == ways[way_id]["geometry"][-1],
                "result": result,
            })

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    OUT_JSON.write_text(json.dumps({"summary_rows": rows, "details": detail}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with TRAVERSALS_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(traversal_rows[0]))
        writer.writeheader()
        writer.writerows(traversal_rows)

    counts = {name: sum(row["result"] == name for row in traversal_rows) for name in ("detected", "bicycle_exempt", "forward_travel")}
    print(json.dumps({
        "pairs": len(rows),
        "condition_b_violation_pairs": sum(row["condition_b_oneway_count"] > 0 for row in rows),
        "condition_a_b_different_pairs": sum(not row["route_geometry_identical"] for row in rows),
        "condition_b_28413948_pairs": [row["label"] for row in rows if row["condition_b_contains_way_28413948"]],
        "condition_b_28413951_pairs": [row["label"] for row in rows if row["condition_b_contains_way_28413951"]],
        "tagged_oneway_traversals": len(traversal_rows),
        "tagged_oneway_results": counts,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
