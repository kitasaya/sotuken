"""OD台帳座標とアプリの地名ジオコーディング結果を比較する。

本番との乖離を避けるため、座標取得は services.geocoder.geocode を
そのまま再利用する。生応答は data/od_geocode_mismatch.json に保存する。
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.geocoder import NOMINATIM_URL, geocode
from services.http_clients import close_client


BACKEND_DIR = Path(__file__).parent.parent
OD_PATH = BACKEND_DIR / "data" / "od_pairs.csv"
COMPARISON_PATH = BACKEND_DIR / "data" / "google_comparison.csv"
VERIFY_ROUTE_PATH = BACKEND_DIR / "data" / "verify_v2_analyze_route.csv"
OUTPUT_PATH = BACKEND_DIR / "data" / "od_geocode_mismatch.json"


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の大円距離（m）。"""
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlng = math.radians(lng2 - lng1)
    value = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, value)))


def load_od_pair() -> dict:
    with OD_PATH.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["label"] == "新宿→池袋":
                return row
    raise ValueError("新宿→池袋 が od_pairs.csv にありません")


def load_csv_row(path: Path, label: str) -> tuple[list[str], dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            if row["label"] == label:
                return list(reader.fieldnames or []), row
    raise ValueError(f"{label} が {path.name} にありません")


async def main() -> None:
    od = load_od_pair()
    comparison_fields, _ = load_csv_row(COMPARISON_PATH, od["label"])
    _, verify_route = load_csv_row(VERIFY_ROUTE_PATH, od["label"])
    try:
        origin, destination = await asyncio.gather(geocode("新宿駅"), geocode("池袋駅"))
    finally:
        await close_client()

    origin_od = {"lat": float(od["origin_lat"]), "lng": float(od["origin_lng"])}
    destination_od = {"lat": float(od["dest_lat"]), "lng": float(od["dest_lng"])}
    result = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "geocoder": "services.geocoder.geocode",
        "endpoint": NOMINATIM_URL,
        "query_countrycodes": "jp",
        "query_limit": 1,
        "od_source": "backend/data/od_pairs.csv:4",
        "comparison_source": "backend/data/google_comparison.csv:4",
        "comparison_has_rerouted_column": "rerouted" in comparison_fields,
        "reroute_source": "backend/data/verify_v2_analyze_route.csv:4",
        "label": od["label"],
        "rerouted": verify_route["rerouted"].lower() == "true",
        "origin": {
            "query": "新宿駅",
            "od": origin_od,
            "geocoded": origin,
            "difference_m": round(haversine_m(origin_od["lat"], origin_od["lng"], origin["lat"], origin["lng"]), 3),
        },
        "destination": {
            "query": "池袋駅",
            "od": destination_od,
            "geocoded": destination,
            "difference_m": round(haversine_m(destination_od["lat"], destination_od["lng"], destination["lat"], destination["lng"]), 3),
        },
    }
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
