"""R1 ground_truth.csv 再構築のための下準備スクリプト（タスク1・タスク2）。

15 O-Dペア全件について POST /api/route を呼び出し、violations[] から
way_id・座標・検出ルール・confidence を抽出したテンプレート CSV を生成する。
続けて Overpass から OSM 生タグを取得し、osm_tags_raw 列に埋め込む。

true_oneway_violation / true_two_step_required は空欄のまま出力する
（マサヤさんが人手で判定する列。判定ロジックはここでは実装しない）。

## 使い方

  # 事前にバックエンドサーバーを起動しておくこと（docs/SETUP.md 参照）
  #   cd backend && python -m uvicorn main:app --reload --port 8000

  python scripts/prepare_ground_truth.py

## 出力

  backend/data/ground_truth_template.csv
  （既存の backend/data/ground_truth.csv は変更しない）
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.overpass import get_way_tags_by_ids

DATA_DIR = Path(__file__).parent.parent / "data"
OD_PAIRS_CSV = DATA_DIR / "od_pairs.csv"
OUTPUT_CSV = DATA_DIR / "ground_truth_template.csv"

FIELDNAMES = [
    "label", "way_id", "point_lat", "point_lng",
    "true_oneway_violation", "true_two_step_required",
    "detected_rule", "system_confidence", "osm_tags_raw", "notes",
]

# osm_tags_raw に抽出する判定関連タグキー（この順で出力）
RELEVANT_TAG_KEYS = [
    "oneway", "oneway:bicycle", "cycleway", "cycleway:left", "cycleway:right",
    "highway", "junction",
]

FALLBACK_NOTE = "fallback: way_id unavailable"
NO_VIOLATION_NOTE = "no violations detected by current system"
OVERPASS_FAIL_NOTE = "(overpass fetch failed)"
NO_RELEVANT_TAGS_NOTE = "(no relevant tags)"


async def fetch_route(client: httpx.AsyncClient, base_url: str, od_row: dict) -> dict:
    payload = {
        "origin_lat": float(od_row["origin_lat"]),
        "origin_lng": float(od_row["origin_lng"]),
        "dest_lat": float(od_row["dest_lat"]),
        "dest_lng": float(od_row["dest_lng"]),
    }
    resp = await client.post(f"{base_url}/api/route", json=payload, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def build_rows_for_label(label: str, route_result: dict) -> list[dict]:
    """1ラベル分のレスポンスから ground_truth_template.csv の行を組み立てる。"""
    violations = route_result.get("violations", [])
    using_edge_ids = route_result.get("comparison", {}).get("using_edge_ids", False)

    if not violations:
        return [{
            "label": label,
            "way_id": "",
            "point_lat": "",
            "point_lng": "",
            "true_oneway_violation": "",
            "true_two_step_required": "",
            "detected_rule": "",
            "system_confidence": "",
            "osm_tags_raw": "",
            "notes": NO_VIOLATION_NOTE,
        }]

    rows = []
    for v in violations:
        way_id = v.get("way_id")
        if using_edge_ids and way_id is not None:
            way_id_out, notes = way_id, ""
        else:
            way_id_out, notes = "", FALLBACK_NOTE
        rows.append({
            "label": label,
            "way_id": way_id_out,
            "point_lat": v.get("lat", ""),
            "point_lng": v.get("lng", ""),
            "true_oneway_violation": "",
            "true_two_step_required": "",
            "detected_rule": v.get("rule", ""),
            "system_confidence": v.get("confidence", ""),
            "osm_tags_raw": "",
            "notes": notes,
        })
    return rows


def format_osm_tags(tags: dict) -> str:
    parts = [f"{key}={tags[key]}" for key in RELEVANT_TAG_KEYS if tags.get(key)]
    return "; ".join(parts) if parts else NO_RELEVANT_TAGS_NOTE


async def enrich_with_osm_tags(rows: list[dict]) -> None:
    """タスク2: way_id が埋まっている行に osm_tags_raw を追記する（既存関数を再利用）。"""
    way_ids = sorted({int(r["way_id"]) for r in rows if r["way_id"] not in ("", None)})
    if not way_ids:
        return

    try:
        tags_map = await get_way_tags_by_ids(way_ids)
    except Exception as e:
        print(f"  ⚠ Overpass 一括取得に失敗: {e}")
        tags_map = {}

    for r in rows:
        if r["way_id"] in ("", None):
            continue
        entry = tags_map.get(int(r["way_id"]))
        r["osm_tags_raw"] = OVERPASS_FAIL_NOTE if entry is None else format_osm_tags(entry.get("tags", {}))


async def main(base_url: str) -> None:
    with open(OD_PAIRS_CSV, encoding="utf-8", newline="") as f:
        od_rows = list(csv.DictReader(f))

    print(f"対象 O-D ペア: {len(od_rows)} 件（{base_url}/api/route）")

    all_rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for od_row in od_rows:
            label = od_row["label"]
            print(f"  取得中: {label}", flush=True)
            try:
                route_result = await fetch_route(client, base_url, od_row)
            except Exception as e:
                print(f"  ⚠ {label}: /api/route 呼び出し失敗 ({e})。この label をスキップします。")
                continue
            rows = build_rows_for_label(label, route_result)
            all_rows.extend(rows)
            n_viol = 0 if rows[0]["notes"] == NO_VIOLATION_NOTE else len(rows)
            print(f"    → violations={n_viol}")

    print("\nOverpass から OSM 生タグを取得中...")
    await enrich_with_osm_tags(all_rows)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)

    n_labels = len({r["label"] for r in all_rows})
    n_way_id_ok = sum(1 for r in all_rows if r["way_id"] not in ("", None))
    n_fallback = sum(1 for r in all_rows if r["notes"] == FALLBACK_NOTE)
    print(f"\n出力: {OUTPUT_CSV}")
    print(f"  行数: {len(all_rows)}")
    print(f"  対象ラベル数: {n_labels} / {len(od_rows)}")
    print(f"  way_id 取得成功: {n_way_id_ok} 行")
    print(f"  way_id フォールバック（空欄）: {n_fallback} 行")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="15 O-Dペア全件を /api/route に投げ、ground_truth_template.csv を生成する"
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="バックエンドAPIのベースURL（デフォルト: http://localhost:8000）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.base_url))
