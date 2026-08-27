"""B群残存2事例・way 151808609 の候補構造を固定PBFから再構成する（バッチ9・分析専用）。

way 151808609 はバッチ9時点ではD群（Googleルート外・原因未特定）としていたが、
2026-08-27（バッチ11）のストリートビュー再確認で「車両進入禁止」標識が確認され、
真の違反へ再分類された。D群は消滅している。本スクリプトの対象点・出力は変えていない。

判定ロジックは一切変更せず、本番の距離関数
`services.overpass._point_to_way_dist_m` と PBF 読取
`scripts.local_osm_pbf.extract_local_highways` を import して再利用する。
外部APIへの通信は行わない。既存CSVは上書きしない。

出力: backend/data/b_group_residual_candidates.csv
       backend/data/b_group_residual_summary.json
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
sys.path.insert(0, str(BACKEND_DIR))
SITE_PACKAGES = BACKEND_DIR / ".venv" / "Lib" / "site-packages"
if SITE_PACKAGES.exists():
    sys.path.insert(0, str(SITE_PACKAGES))

from scripts.local_osm_pbf import extract_local_highways  # noqa: E402
from services.overpass import _point_to_way_dist_m  # noqa: E402

PBF_PATH = BACKEND_DIR.parent / "graphhopper" / "kanto-260801.osm.pbf"
MARGIN_CSV = DATA_DIR / "verify_match_margin_points.csv"
OUT_CSV = DATA_DIR / "b_group_residual_candidates.csv"
OUT_JSON = DATA_DIR / "b_group_residual_summary.json"

# (label, sample_idx) を対象にする。B群残存2点 + D群1点 + 構造比較用の隣接点。
TARGETS = [
    ("品川→東京", 17),   # 直前点（旧海岸通り本線）
    ("品川→東京", 18),   # B群残存: way 741785139
    ("品川→東京", 19),   # 直後点（旧海岸通り本線）
    ("立川→国分寺", 2),  # 直前点
    ("立川→国分寺", 3),  # B群残存: way 1429406683
    ("立川→国分寺", 4),  # 直後点（同名 138427587）
    ("千葉→幕張本郷", 95),
    ("千葉→幕張本郷", 96),  # way 151808609（バッチ9時点はD群。2026-08-27に真の違反へ再分類）
    ("千葉→幕張本郷", 97),
]

TOP_N = 8


def load_points() -> list[dict]:
    rows = []
    with MARGIN_CSV.open(encoding="utf-8-sig", newline="") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            key = (row["label"], int(row["sample_idx"]))
            if key in TARGETS:
                row["_csv_line"] = lineno
                rows.append(row)
    return rows


def bearing_deg(a, b) -> float:
    """a, b = (lon, lat)。局所平面近似での方位角（度、東=0、反時計正）。"""
    kx = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.degrees(math.atan2(b[1] - a[1], (b[0] - a[0]) * kx))


def nearest_segment_bearing(lat: float, lng: float, geometry: list) -> float | None:
    if len(geometry) < 2:
        return None
    best = None
    best_d = float("inf")
    for i in range(len(geometry) - 1):
        d = _point_to_way_dist_m(lat, lng, [geometry[i], geometry[i + 1]])
        if d < best_d:
            best_d = d
            best = (geometry[i], geometry[i + 1])
    return bearing_deg(*best)


def main() -> None:
    points = load_points()
    if len(points) != len(TARGETS):
        print(f"WARN: 対象{len(TARGETS)}点のうちCSVで見つかったのは{len(points)}点", flush=True)
    coords = [(float(p["lat"]), float(p["lng"])) for p in points]
    ways = extract_local_highways(PBF_PATH, coords, node_buffer_m=120.0)
    print(f"candidate ways in buffer: {len(ways)}", flush=True)

    out_rows = []
    summary: dict = {
        "pbf": str(PBF_PATH.name),
        "margin_csv": str(MARGIN_CSV.name),
        "node_buffer_m": 120.0,
        "points": [],
    }

    for p, (lat, lng) in zip(points, coords):
        ranked = []
        for w in ways:
            d = _point_to_way_dist_m(lat, lng, w["geometry"])
            if d == float("inf") or d > 60.0:
                continue
            ranked.append((d, w))
        ranked.sort(key=lambda t: t[0])

        prev_next = [q for q in points if q["label"] == p["label"]]
        travel = None
        idx = int(p["sample_idx"])
        nxt = next((q for q in prev_next if int(q["sample_idx"]) == idx + 1), None)
        prv = next((q for q in prev_next if int(q["sample_idx"]) == idx - 1), None)
        if prv and nxt:
            travel = bearing_deg(
                (float(prv["lng"]), float(prv["lat"])),
                (float(nxt["lng"]), float(nxt["lat"])),
            )

        pt_summary = {
            "label": p["label"],
            "sample_idx": idx,
            "csv_line": p["_csv_line"],
            "lat": lat,
            "lng": lng,
            "csv_perp_rank1_way_id": p["perp_rank1_way_id"],
            "csv_rank2_way_id": p["rank2_way_id"],
            "csv_match_dist_m": p["match_dist_m"],
            "csv_match_margin_m": p["match_margin_m"],
            "csv_known_group": p["known_group_perp_rank1"],
            "travel_bearing_deg": None if travel is None else round(travel, 1),
            "candidates": [],
        }

        for rank, (d, w) in enumerate(ranked[:TOP_N], start=1):
            t = w["tags"]
            seg_b = nearest_segment_bearing(lat, lng, w["geometry"])
            angle = None
            if travel is not None and seg_b is not None:
                angle = abs((travel - seg_b + 180) % 360 - 180)
            rec = {
                "rank": rank,
                "way_id": w["id"],
                "dist_m": round(d, 3),
                "highway": t.get("highway", ""),
                "oneway": t.get("oneway", ""),
                "name": t.get("name", ""),
                "bicycle": t.get("bicycle", ""),
                "nodes": len(w["geometry"]),
                "way_bearing_deg": None if seg_b is None else round(seg_b, 1),
                "angle_vs_travel_deg": None if angle is None else round(angle, 1),
            }
            pt_summary["candidates"].append(rec)
            out_rows.append({"label": p["label"], "sample_idx": idx, **rec})

        if len(ranked) >= 2:
            pt_summary["recomputed_margin_m"] = round(ranked[1][0] - ranked[0][0], 3)
        summary["points"].append(pt_summary)

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
