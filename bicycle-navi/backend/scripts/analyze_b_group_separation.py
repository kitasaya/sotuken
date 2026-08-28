"""B群10点の分離way構造を2026-08-01固定PBFから調べる（バッチ13・分析専用）。

判定ロジックは変更しない。既存のPBF読取と同一の有限線分距離定義を使い、B群の各点について
対向車線候補、局所的な中心線間隔、進行方向との角度差、分離関連タグを記録する。

出力:
  backend/data/b_group_separation_evidence.csv
  backend/data/b_group_separation_features.csv
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
sys.path.insert(0, str(BACKEND_DIR))

from scripts.local_osm_pbf import (  # noqa: E402
    _data_blocks,
    _dense_nodes,
    _haversine_m,
    _primitive_block,
    _target_grid,
    _ways,
)
PBF_PATH = BACKEND_DIR.parent / "graphhopper" / "kanto-260801.osm.pbf"
POINTS_CSV = DATA_DIR / "verify_match_margin_points.csv"
OUT_EVIDENCE = DATA_DIR / "b_group_separation_evidence.csv"
OUT_FEATURES = DATA_DIR / "b_group_separation_features.csv"

# opposing_way_id は既存のB群調査と新規2点診断で特定された候補。
# P05は同名・逆方向の対向wayがなく、並走する順走候補を比較対象として記録する。
TARGETS = [
    {"point_id": "P02", "label": "東京→渋谷", "sample_idx": 15, "way_id": 474601303, "opposing_way_id": 1250775758, "pair_type": "opposing"},
    {"point_id": "P03", "label": "東京→渋谷", "sample_idx": 18, "way_id": 271979254, "opposing_way_id": 858775692, "pair_type": "opposing"},
    {"point_id": "P05", "label": "品川→東京", "sample_idx": 18, "way_id": 741785139, "opposing_way_id": 1033723439, "pair_type": "parallel_candidate"},
    {"point_id": "P06", "label": "品川→東京", "sample_idx": 25, "way_id": 667962675, "opposing_way_id": 667962674, "pair_type": "opposing"},
    {"point_id": "P11", "label": "立川→国分寺", "sample_idx": 3, "way_id": 1429406683, "opposing_way_id": 138427587, "pair_type": "opposing"},
    {"point_id": "P14", "label": "川崎→武蔵小杉", "sample_idx": 11, "way_id": 263457845, "opposing_way_id": 31875063, "pair_type": "opposing"},
    {"point_id": "P15", "label": "千葉→幕張本郷", "sample_idx": 19, "way_id": 22961575, "opposing_way_id": 23052404, "pair_type": "opposing"},
    {"point_id": "P16", "label": "千葉→幕張本郷", "sample_idx": 24, "way_id": 22961575, "opposing_way_id": 23052404, "pair_type": "opposing"},
    {"point_id": "N01", "label": "新宿→池袋", "sample_idx": 22, "way_id": 228180934, "opposing_way_id": 648625671, "pair_type": "opposing"},
    {"point_id": "N02", "label": "川崎→武蔵小杉", "sample_idx": 5, "way_id": 992241606, "opposing_way_id": 263456981, "pair_type": "opposing"},
]

FEATURE_KEYS = {"barrier", "divider", "dual_carriageway", "landuse", "natural", "railway"}
FEATURE_VALUES = {
    "grass", "grassland", "meadow", "scrub", "hedge", "fence", "guard_rail",
    "wall", "retaining_wall", "kerb", "median", "traffic_island", "tram", "rail",
}


def _point_to_segment_dist_m(lat: float, lng: float, a: list, b: list) -> float:
    """services.overpass と同じ局所平面近似による点・線分距離。"""
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    ax, ay = (a[0] - lng) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lng) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len_sq))
    return math.hypot(ax + t * dx, ay + t * dy)


def _point_to_way_dist_m(lat: float, lng: float, geometry: list) -> float:
    """services.overpass._point_to_way_dist_m と同じ有限線分距離。"""
    if not geometry:
        return float("inf")
    if len(geometry) == 1:
        return _point_to_segment_dist_m(lat, lng, geometry[0], geometry[0])
    return min(
        _point_to_segment_dist_m(lat, lng, geometry[i], geometry[i + 1])
        for i in range(len(geometry) - 1)
    )


def load_point_rows() -> tuple[dict[tuple[str, int], dict], list[dict]]:
    by_key: dict[tuple[str, int], dict] = {}
    all_rows: list[dict] = []
    with POINTS_CSV.open(encoding="utf-8-sig", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            row["_csv_line"] = line
            row["sample_idx"] = int(row["sample_idx"])
            all_rows.append(row)
            by_key[(row["label"], row["sample_idx"])] = row
    return by_key, all_rows


def extract_local_ways(points: list[tuple[float, float]], node_buffer_m: float = 120.0) -> dict[int, dict]:
    """分析点近傍のタグ付きwayを抽出する。highway以外のbarrier/landuse等も含む。"""
    cell_deg = 0.005
    grid = _target_grid(points, cell_deg)
    near_nodes: set[int] = set()

    print(f"PBF pass 1/3: nodes within {node_buffer_m:g}m", flush=True)
    for _, block in _data_blocks(PBF_PATH):
        _, groups, granularity, lat_offset, lon_offset = _primitive_block(block)
        for group in groups:
            for node_id, lat, lon in _dense_nodes(group, granularity, lat_offset, lon_offset):
                cell = (math.floor(lat / cell_deg), math.floor(lon / cell_deg))
                nearby = []
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        nearby.extend(grid.get((cell[0] + di, cell[1] + dj), ()))
                if any(_haversine_m(lat, lon, plat, plon) <= node_buffer_m for plat, plon in nearby):
                    near_nodes.add(node_id)

    selected: dict[int, dict] = {}
    all_refs: set[int] = set()
    print(f"PBF pass 2/3: ways touching {len(near_nodes)} nodes", flush=True)
    for _, block in _data_blocks(PBF_PATH):
        strings, groups, _, _, _ = _primitive_block(block)
        for group in groups:
            for way_id, tags, refs in _ways(group, strings):
                if not any(ref in near_nodes for ref in refs):
                    continue
                if "highway" not in tags and not FEATURE_KEYS.intersection(tags):
                    continue
                selected[way_id] = {"id": way_id, "tags": tags, "refs": refs}
                all_refs.update(refs)

    coordinates: dict[int, tuple[float, float]] = {}
    print(f"PBF pass 3/3: coordinates for {len(all_refs)} refs", flush=True)
    for _, block in _data_blocks(PBF_PATH):
        _, groups, granularity, lat_offset, lon_offset = _primitive_block(block)
        for group in groups:
            for node_id, lat, lon in _dense_nodes(group, granularity, lat_offset, lon_offset):
                if node_id in all_refs:
                    coordinates[node_id] = (lon, lat)

    result = {}
    for way_id, way in selected.items():
        geometry = [coordinates[r] for r in way["refs"] if r in coordinates]
        if len(geometry) >= 2:
            result[way_id] = {"id": way_id, "tags": way["tags"], "geometry": geometry}
    return result


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    kx = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.degrees(math.atan2(b[1] - a[1], (b[0] - a[0]) * kx))


def closest_point_on_segment(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    mean_lat = math.radians((lat + a[1] + b[1]) / 3)
    sx = 111_320.0 * math.cos(mean_lat)
    sy = 110_540.0
    ax, ay = (a[0] - lon) * sx, (a[1] - lat) * sy
    bx, by = (b[0] - lon) * sx, (b[1] - lat) * sy
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
    return lon + (ax + t * dx) / sx, lat + (ay + t * dy) / sy


def nearest_segment(lat: float, lon: float, geometry: list[tuple[float, float]]):
    best = None
    best_d = float("inf")
    for a, b in zip(geometry, geometry[1:]):
        d = _point_to_way_dist_m(lat, lon, [a, b])
        if d < best_d:
            best_d = d
            best = (a, b)
    return best_d, best


def local_centerline_gap(lat: float, lon: float, first: dict, second: dict) -> float:
    _, seg = nearest_segment(lat, lon, first["geometry"])
    point = closest_point_on_segment(lat, lon, *seg)
    return _point_to_way_dist_m(point[1], point[0], second["geometry"])


def angle_to_travel(row: dict, rows: dict[tuple[str, int], dict], way: dict) -> float | None:
    prev = rows.get((row["label"], row["sample_idx"] - 1))
    nxt = rows.get((row["label"], row["sample_idx"] + 1))
    if not prev or not nxt:
        return None
    travel = bearing_deg((float(prev["lng"]), float(prev["lat"])), (float(nxt["lng"]), float(nxt["lat"])))
    _, seg = nearest_segment(float(row["lat"]), float(row["lng"]), way["geometry"])
    way_bearing = bearing_deg(*seg)
    return abs((travel - way_bearing + 180) % 360 - 180)


def feature_summary(tags: dict) -> str:
    return "; ".join(f"{k}={tags[k]}" for k in sorted(tags) if k in FEATURE_KEYS)


def is_relevant_feature(tags: dict) -> bool:
    if "barrier" in tags or "divider" in tags or "dual_carriageway" in tags:
        return True
    return any(tags.get(k) in FEATURE_VALUES for k in ("landuse", "natural", "railway"))


def main() -> None:
    rows, _ = load_point_rows()
    point_rows = []
    for target in TARGETS:
        row = rows[(target["label"], target["sample_idx"])]
        point_rows.append((float(row["lat"]), float(row["lng"])))
    ways = extract_local_ways(point_rows)

    evidence = []
    features = []
    for target in TARGETS:
        row = rows[(target["label"], target["sample_idx"])]
        lat, lon = float(row["lat"]), float(row["lng"])
        target_way = ways.get(target["way_id"])
        other_way = ways.get(target["opposing_way_id"])
        if target_way is None:
            raise RuntimeError(f"target way not found: {target['way_id']}")

        nearby_features = []
        for way in ways.values():
            if way["id"] in {target["way_id"], target["opposing_way_id"]}:
                continue
            if not is_relevant_feature(way["tags"]):
                continue
            dist = _point_to_way_dist_m(lat, lon, way["geometry"])
            if dist <= 20.0:
                rec = {
                    "point_id": target["point_id"],
                    "feature_way_id": way["id"],
                    "distance_from_point_m": round(dist, 3),
                    "feature_tags": feature_summary(way["tags"]),
                }
                nearby_features.append(rec)
                features.append(rec)

        target_tags = target_way["tags"]
        other_tags = other_way["tags"] if other_way else {}
        # 現行rank1が対象wayなら、実際の採点時と同じ30m spanの診断値をCSVから採用する。
        # 旧方式でのみ対象wayだった点は、前後の現行サンプル点と局所線分から再計算する。
        angle = angle_to_travel(row, rows, target_way)
        if int(row["perp_rank1_way_id"] or 0) == target["way_id"] and row["diag_angle_deg"]:
            angle = float(row["diag_angle_deg"])

        evidence.append({
            "point_id": target["point_id"],
            "source_csv_line": row["_csv_line"],
            "label": target["label"],
            "sample_idx": target["sample_idx"],
            "lat": row["lat"],
            "lng": row["lng"],
            "way_id": target["way_id"],
            "opposing_way_exists": target["pair_type"] == "opposing" and other_way is not None,
            "comparison_way_id": target["opposing_way_id"],
            "comparison_type": target["pair_type"],
            "way_name": target_tags.get("name", ""),
            "comparison_name": other_tags.get("name", ""),
            "names_match": bool(target_tags.get("name")) and target_tags.get("name") == other_tags.get("name"),
            "point_to_way_m": round(_point_to_way_dist_m(lat, lon, target_way["geometry"]), 3),
            "point_to_comparison_way_m": "" if not other_way else round(_point_to_way_dist_m(lat, lon, other_way["geometry"]), 3),
            "local_centerline_gap_m": "" if not other_way else round(local_centerline_gap(lat, lon, target_way, other_way), 3),
            "wrong_way_angle_vs_travel_deg": "" if angle is None else round(angle, 1),
            "target_divider_tags": feature_summary(target_tags),
            "comparison_divider_tags": feature_summary(other_tags),
            "nearby_separation_feature_count": len(nearby_features),
            "nearby_separation_features": " | ".join(f"way {f['feature_way_id']} ({f['feature_tags']}, {f['distance_from_point_m']}m)" for f in nearby_features),
        })

    with OUT_EVIDENCE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(evidence[0]))
        writer.writeheader()
        writer.writerows(evidence)
    with OUT_FEATURES.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["point_id", "feature_way_id", "distance_from_point_m", "feature_tags"])
        writer.writeheader()
        writer.writerows(features)
    print(f"wrote {OUT_EVIDENCE}")
    print(f"wrote {OUT_FEATURES}")


if __name__ == "__main__":
    main()
