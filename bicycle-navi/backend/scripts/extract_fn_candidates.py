"""R1（検出精度評価）の見逃し（FN）候補を抽出する。

`ground_truth_template.csv` は **検出された違反の行しか持たない** ため、Recall の
分母（見逃しの母集団）が存在しない。ルート上の全 way を人手確認するのは非現実的
なので、次の論理で母集団を絞り込む。

    一方通行違反は `oneway=yes`（または `-1` 等）タグを持つ way 上でしか
    発生し得ない。したがって見逃し候補は「初期ルートが通った way のうち、
    oneway 系タグを持つが違反として検出されなかったもの」に限定できる。

**この絞り込みの限界（必ず併記すること）：** 「現地は一方通行だが OSM にタグが
ない」ケースはこの方法では抽出できない。これは限界1（RESEARCH.md 24.1節・A群）の
裏返しであり、原理的に測定不能である。本スクリプトが測るのは
**「タグ基準の Recall」** であって現実基準の Recall ではない。

## 処理の流れ

  Step 1  od_pairs.csv の15ペアについて `analyze_route(v3)` を実行し、
          `original_route.details.osm_way_id` から **初期ルート** が通った
          全 way_id を抽出する（リルート後ではない。`analyze_route` は初期
          ルートに対して判定しているため `original_route` 側が正しい対象）。
  Step 2  全 way_id のタグを Overpass（`get_way_tags_by_ids`）で取得し、
          `oneway ∈ {yes, true, 1, -1}` のものを母集団として抽出する。
  Step 3  **同一実行の** `violations[]`（rule=oneway）に含まれない oneway way を
          FN候補とする。過去の CSV の way_id は使わない（OSM 更新で変わるため）。
  Step 4  各FN候補について非検出の理由（`fn_reason`）を機械的に診断する。
  Step 5  集計してレポート（.md）と明細（.csv）を出力する。

## 判定ロジックの再実装をしない

診断は `services/` の関数をそのまま呼ぶ。特に進行方向照合は `law_checker` の
`_check_direction` を、ジオメトリのクリップは `route_analyzer` の `_trim_geometry`
をそのまま使い、このスクリプト側で複製・近似しない（判定器と診断がずれると
FN候補の説明として意味をなさないため）。

**方向情報は「なぜ検出されなかったか」の説明にのみ使い、マッチ候補の選択には
一切使わない**（循環論法の回避・RESEARCH.md 21.11節）。そもそも edge_id ベース
判定では way_id を GraphHopper から直接受け取るため最近傍マッチング自体が無い。

## 使い方

  # GraphHopper（localhost:8989）が起動していること。docs/SETUP.md 参照。
  # バックエンドAPI（uvicorn）の起動は不要（analyze_route を直接呼ぶ）。
  python3 scripts/extract_fn_candidates.py

  python3 scripts/extract_fn_candidates.py --labels 渋谷→新宿 東京→渋谷  # 一部だけ
  python3 scripts/extract_fn_candidates.py --interval 5                   # Overpass 間隔

## 出力（いずれも新規ファイル。既存データは一切書き換えない）

  backend/data/fn_candidates_oneway.md    … レポート
  backend/data/fn_candidates_oneway.csv   … 全FN候補の明細
"""

import argparse
import asyncio
import csv
import datetime
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.route_match_probe import fmt
from services.external_route_scorer import _turn_angle_deg, _way_axis_vector
from services.graphhopper import GH_BASE
from services.http_clients import close_client
from services.law_checker import _check_direction, _dot2d, _geom_length_m
from services.overpass import MATCH_AMBIGUOUS_MARGIN_M, get_bulk_way_data, get_way_tags_by_ids
from services.route_analyzer import _trim_geometry, analyze_route

DATA_DIR = Path(__file__).parent.parent / "data"
OD_PAIRS_CSV = DATA_DIR / "od_pairs.csv"
OUT_MD = DATA_DIR / "fn_candidates_oneway.md"
OUT_CSV = DATA_DIR / "fn_candidates_oneway.csv"

# GraphHopper のビルド済みグラフのメタデータ（/info が使えない場合のフォールバック）
GH_PROPERTIES = Path(__file__).parent.parent.parent / "graphhopper" / "default-gh" / "properties.txt"

# law_checker.check_oneway_violation が違反判定の対象とする oneway 値
ONEWAY_VALUES = ("yes", "true", "1", "-1")
# law_checker が自転車除外とみなす cycleway 値
CYCLEWAY_EXEMPT_VALUES = ("opposite", "opposite_lane", "opposite_track")
# law_checker が「短区間のため信頼度を下げる」しきい値
SHORT_SEGMENT_M = 20.0

REQUEST_INTERVAL_S = 3.0

# fn_reason（優先順）と、人手確認の要否
FN_REASONS = ["bicycle_exempt", "forward_travel", "short_segment", "tag_fetch_failed", "unknown"]
NEEDS_MANUAL_CHECK = {
    "bicycle_exempt": False,
    "forward_travel": False,
    "short_segment": True,
    "tag_fetch_failed": True,
    "unknown": True,
}
REASON_DESCRIPTION = {
    "bicycle_exempt": "`oneway:bicycle=no` または `cycleway=opposite*`（正しい非検出）",
    "forward_travel": "進行方向照合の結果、順走だった（正しい非検出）",
    "short_segment": "クリップ後の区間長が 20m 未満（要確認）",
    "tag_fetch_failed": "Overpass からタグ／ジオメトリを取得できなかった（要確認）",
    "unknown": "上記のいずれにも当てはまらない（**要確認・最優先**）",
}

CSV_FIELDNAMES = [
    "label", "way_id", "fn_reason", "needs_manual_check",
    "point_lat", "point_lng", "osm_url",
    "oneway", "highway", "name", "osm_tags_raw",
    "start_idx", "end_idx", "geom_nodes", "trimmed_length_m",
    "going_wrong_way", "against_votes", "direction_votes",
    "angle_way_axis_vs_travel_deg", "travel_vector_lng", "travel_vector_lat",
    "oneway_bicycle", "cycleway", "using_edge_ids", "diagnosis_detail",
    # unknown 候補のみ補足取得する最近傍マッチ診断（21.12節の match_* と同じ意味）
    "match_way_id", "match_dist_m", "match_margin_m", "match_ambiguous",
]


# ---------------------------------------------------------------------------
# 実行条件の記録
# ---------------------------------------------------------------------------

async def fetch_gh_data_date() -> dict:
    """GraphHopper の `datareader.data.date` 等の実行条件を取得する。

    OSM は更新され続けるため、この値がないとレポートの数値が再現不能になる。
    `/info` が返さない場合は `graphhopper/default-gh/properties.txt` を読む。
    """
    info = {"source": "", "data_date": "", "import_date": "", "gh_version": "", "error": ""}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GH_BASE}/info")
            resp.raise_for_status()
            body = resp.json()
        info["source"] = f"{GH_BASE}/info"
        info["data_date"] = str(body.get("data_date", "") or "")
        info["import_date"] = str(body.get("import_date", "") or "")
        info["gh_version"] = str(body.get("version", "") or "")
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"

    if not info["data_date"] and GH_PROPERTIES.exists():
        props = {}
        for line in GH_PROPERTIES.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                props[k.strip()] = v.strip()
        if props.get("datareader.data.date"):
            info["source"] = str(GH_PROPERTIES)
            info["data_date"] = props.get("datareader.data.date", "")
            info["import_date"] = props.get("datareader.import.date", "")
    return info


# ---------------------------------------------------------------------------
# Step 1: 初期ルートが通った way の抽出
# ---------------------------------------------------------------------------

def collect_way_segments(original_route: dict) -> tuple[dict[int, dict], int]:
    """`original_route.details.osm_way_id` から way_id ごとの区間情報を組み立てる。

    戻り値: ({way_id: {"point", "start_idx", "end_idx"}}, セグメント数)

    **`route_analyzer._analyze_v3` の `way_id_info` と同一の規則**（同じ way_id が
    複数区間に現れた場合は最初の区間のみを採用）で構築する。判定器が実際に評価した
    のがその区間だからであり、ここがずれると診断が判定器の説明にならない。
    """
    details = original_route.get("details", {}).get("osm_way_id", [])
    points = original_route.get("points", {}).get("coordinates", [])
    way_id_info: dict[int, dict] = {}
    for seg in details:
        start_idx, end_idx, wid = int(seg[0]), int(seg[1]), int(seg[2])
        if wid in way_id_info:
            continue
        mid_idx = (start_idx + end_idx) // 2
        way_id_info[wid] = {
            "point": points[min(mid_idx, len(points) - 1)] if points else None,
            "start_idx": start_idx,
            "end_idx": end_idx,
        }
    return way_id_info, len(details)


async def run_pair(od_row: dict) -> dict:
    """1 O-Dペアについて analyze_route(v3) を実行し、初期ルートの通過 way を集める。"""
    label = od_row["label"]
    result = await analyze_route(
        float(od_row["origin_lat"]), float(od_row["origin_lng"]),
        float(od_row["dest_lat"]), float(od_row["dest_lng"]),
        algo_version="v3",
    )
    original_route = result["original_route"]
    points = original_route.get("points", {}).get("coordinates", [])
    way_id_info, n_segments = collect_way_segments(original_route)

    # Step 3 の材料：**同一実行の** violations から oneway 違反の way_id を取る
    detected_oneway = {
        int(v["way_id"]) for v in result.get("violations", [])
        if v.get("rule") == "oneway" and v.get("way_id") is not None
    }

    return {
        "label": label,
        "road_type": od_row.get("road_type", ""),
        "original_distance_m": round(original_route.get("distance", 0.0), 1),
        "compliant_distance_m": result["comparison"]["compliant_distance_m"],
        "rerouted": result["comparison"]["rerouted"],
        "using_edge_ids": result["comparison"]["using_edge_ids"],
        "n_segments": n_segments,
        "way_id_info": way_id_info,
        "points": points,
        "detected_oneway_way_ids": detected_oneway,
        "n_oneway_violations": sum(1 for v in result.get("violations", [])
                                   if v.get("rule") == "oneway"),
        "n_violations": len(result.get("violations", [])),
        "error": "",
    }


# ---------------------------------------------------------------------------
# Step 4: 非検出理由の診断
# ---------------------------------------------------------------------------

def diagnose(way: dict) -> tuple[str, str]:
    """FN候補1件の `fn_reason` と診断の詳細文を返す。

    優先順（最初に該当したものを採用）:
      1 bicycle_exempt   … `oneway:bicycle=no` / `cycleway=opposite*`
      2 forward_travel   … 進行方向照合で順走
      3 short_segment    … クリップ後の区間長が 20m 未満
      4 tag_fetch_failed … タグ／ジオメトリを取得できなかった
      5 unknown          … 上記のいずれでもない

    ただし **そのペアで `using_edge_ids=False`**（Overpass の by-ID 取得が失敗し
    判定器が way 単位の評価をしていない）場合だけは、1〜3 を評価する前に
    `tag_fetch_failed` を返す。この場合 way ごとの照合そのものが行われておらず、
    「順走だったから検出されなかった」と書くのは事実に反するため。
    """
    tags = way["tags"]

    if not way["using_edge_ids"]:
        return "tag_fetch_failed", (
            "このペアは `using_edge_ids=False`（Overpass by-ID 取得に失敗）。"
            "判定器は way 単位ではなく10点サンプリングで判定しており、"
            "この way は個別に評価されていない"
        )

    # 1. 自転車除外タグ（law_checker が continue する条件と同一）
    if tags.get("oneway:bicycle") == "no":
        return "bicycle_exempt", "`oneway:bicycle=no`（自転車は一方通行の対象外）"
    cycleway = tags.get("cycleway", "")
    if cycleway in CYCLEWAY_EXEMPT_VALUES:
        return "bicycle_exempt", f"`cycleway={cycleway}`（逆向き自転車通行可）"

    geom = way["geometry"]
    tv = way["travel_vector"]
    has_direction_input = len(geom) >= 2 and tv is not None and (tv[0] != 0 or tv[1] != 0)

    if has_direction_input:
        # 2. 進行方向照合（law_checker._check_direction をそのまま使う）
        if not way["going_wrong_way"]:
            return "forward_travel", (
                f"進行方向照合で順走（way軸と進行方向の角度差 {fmt(way['angle_deg'], 1)}°"
                f"・逆走側セグメント {way['against_votes']}/{way['direction_votes']}）"
            )
        # 3. 短区間（law_checker では confidence 0.7 に下がるだけで検出は残る）
        if way["trimmed_length_m"] < SHORT_SEGMENT_M:
            return "short_segment", (
                f"逆走判定だがクリップ後の区間長 {fmt(way['trimmed_length_m'], 1)}m "
                f"< {SHORT_SEGMENT_M:.0f}m"
            )

    # 4. タグ／ジオメトリ欠損
    if not tags:
        return "tag_fetch_failed", "Overpass がこの way のタグを返さなかった"
    if len(geom) < 2:
        return "tag_fetch_failed", (
            f"ジオメトリのノード数が {len(geom)} 件で進行方向照合が行えない"
        )

    # 5. 説明がつかない
    return "unknown", (
        "oneway タグがあり、自転車除外もなく、逆走判定（"
        f"逆走側セグメント {way['against_votes']}/{way['direction_votes']}・"
        f"角度差 {fmt(way['angle_deg'], 1)}°）でも順走と説明できないのに未検出"
    )


def build_way_record(label: str, way_id: int, info: dict, entry: dict,
                     points: list, using_edge_ids: bool) -> dict:
    """1 way ぶんの診断用レコードを組み立てる。

    ジオメトリのクリップ・travel_vector の作り方は `route_analyzer._analyze_v3` と
    同一にする（`_trim_geometry` をそのまま呼ぶ）。
    """
    tags = entry.get("tags", {}) if entry else {}
    raw_geom = entry.get("geometry", []) if entry else []

    start_idx = info["start_idx"]
    end_idx = min(info["end_idx"], len(points) - 1) if points else info["end_idx"]
    p_start = points[start_idx] if points and start_idx < len(points) else None
    p_end = points[end_idx] if points and end_idx < len(points) else None

    if p_start is not None and p_end is not None:
        travel_vector = [p_end[0] - p_start[0], p_end[1] - p_start[1]]
        geom = _trim_geometry(raw_geom, p_start, p_end) if raw_geom else []
    else:
        travel_vector = None
        geom = raw_geom

    oneway = tags.get("oneway", "no")
    going_wrong_way = None
    against_votes = None
    direction_votes = None
    angle_deg = None

    if len(geom) >= 2 and travel_vector is not None and (travel_vector[0] or travel_vector[1]):
        # 判定器と同じ関数で逆走判定する（再実装しない）
        going_wrong_way = _check_direction(geom, travel_vector, oneway)
        # 内訳（多数決の票数）は診断表示用に別途数える
        direction_votes = len(geom) - 1
        against_votes = sum(
            1 for j in range(len(geom) - 1)
            if (
                (_dot2d([geom[j + 1][0] - geom[j][0], geom[j + 1][1] - geom[j][1]], travel_vector) > 0)
                if oneway == "-1"
                else (_dot2d([geom[j + 1][0] - geom[j][0], geom[j + 1][1] - geom[j][1]], travel_vector) < 0)
            )
        )
        axis = _way_axis_vector(geom)
        if axis is not None:
            angle_deg = round(_turn_angle_deg(axis, travel_vector), 1)

    mid = info["point"]
    return {
        "label": label,
        "way_id": way_id,
        "tags": tags,
        "oneway": oneway,
        "highway": tags.get("highway", ""),
        "name": tags.get("name", ""),
        "point_lat": round(mid[1], 7) if mid else None,
        "point_lng": round(mid[0], 7) if mid else None,
        "start_idx": info["start_idx"],
        "end_idx": info["end_idx"],
        "geometry": geom,
        "geom_nodes": len(geom),
        "trimmed_length_m": round(_geom_length_m(geom), 2) if len(geom) >= 2 else 0.0,
        "travel_vector": travel_vector,
        "going_wrong_way": going_wrong_way,
        "against_votes": against_votes,
        "direction_votes": direction_votes,
        "angle_deg": angle_deg,
        "using_edge_ids": using_edge_ids,
        "tags_available": bool(entry),
    }


# ---------------------------------------------------------------------------
# unknown 候補の補足診断（最近傍マッチの曖昧さ）
# ---------------------------------------------------------------------------

async def enrich_unknown_with_match_info(candidates: list[dict]) -> None:
    """`unknown` の候補についてのみ、その地点の最近傍 way 候補の状況を追加取得する。

    edge_id ベース判定では way_id を GraphHopper から直接受け取るため、本来
    最近傍マッチングは介在しない。それでも `unknown` が出た場合は限界2・限界3
    （RESEARCH.md 24.2 / 24.3節）の別の現れ方である可能性があるため、
    `match_ambiguous` と最近傍候補の状況を併記できるようにしておく。

    **この情報は説明のためだけに取得する。判定にも候補選択にも使わない。**
    """
    targets = [c for c in candidates if c["fn_reason"] == "unknown" and c["point_lng"] is not None]
    if not targets:
        return
    pts = [[c["point_lng"], c["point_lat"]] for c in targets]
    try:
        data = await get_bulk_way_data(pts)
    except Exception as e:
        print(f"  ⚠ unknown候補の最近傍マッチ情報の取得に失敗: {e}")
        return
    for c, d in zip(targets, data):
        c["match_way_id"] = d.get("match_way_id")
        c["match_dist_m"] = d.get("match_dist_m")
        c["match_margin_m"] = d.get("match_margin_m")
        c["match_ambiguous"] = d.get("match_ambiguous")


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

async def collect(od_rows: list[dict], interval_s: float) -> tuple[list[dict], list[dict], list[dict]]:
    """全ペアを処理し、(ペア集計, 全通過way, FN候補) を返す。"""
    pair_rows: list[dict] = []
    all_ways: list[dict] = []
    fn_candidates: list[dict] = []

    for i, od_row in enumerate(od_rows):
        label = od_row["label"]
        if i > 0:
            await asyncio.sleep(interval_s)
        print(f"  [{i + 1}/{len(od_rows)}] {label}", flush=True)

        # 各ペアを独立タスクで走らせ、Overpass のサーキットブレーカ
        # （_overpass_circuit_broken は ContextVar）をペア間で持ち越さない。
        # FastAPI がリクエストごとにタスクを分けているのと同じ条件にする。
        try:
            pair = await asyncio.create_task(run_pair(od_row))
        except Exception as e:
            print(f"    ⚠ 失敗: {type(e).__name__}: {e}")
            pair_rows.append({
                "label": label, "road_type": od_row.get("road_type", ""),
                "error": f"{type(e).__name__}: {e}",
                "original_distance_m": None, "n_ways": 0, "n_segments": 0,
                "n_oneway_ways": 0, "n_detected": 0, "n_fn": 0,
                "using_edge_ids": None, "rerouted": None, "n_tag_missing": 0,
            })
            continue

        way_ids = sorted(pair["way_id_info"].keys())

        # Step 2: 通過 way のタグを取得（判定器と同じ関数・同じキャッシュを使う）
        try:
            tags_map = await asyncio.create_task(get_way_tags_by_ids(way_ids))
        except Exception as e:
            print(f"    ⚠ Overpass タグ取得に失敗: {e}")
            tags_map = {}

        n_oneway = 0
        n_fn = 0
        n_tag_missing = 0
        for wid in way_ids:
            entry = tags_map.get(wid)
            if entry is None:
                n_tag_missing += 1
            rec = build_way_record(label, wid, pair["way_id_info"][wid], entry,
                                   pair["points"], pair["using_edge_ids"])
            rec["detected"] = wid in pair["detected_oneway_way_ids"]
            rec["is_oneway"] = rec["oneway"] in ONEWAY_VALUES
            all_ways.append(rec)

            if not rec["is_oneway"]:
                continue
            n_oneway += 1
            # Step 3: 同一実行の violations に無ければ FN候補
            if rec["detected"]:
                continue
            n_fn += 1
            reason, detail = diagnose(rec)
            cand = dict(rec)
            cand["fn_reason"] = reason
            cand["diagnosis_detail"] = detail
            cand["match_way_id"] = None
            cand["match_dist_m"] = None
            cand["match_margin_m"] = None
            cand["match_ambiguous"] = None
            fn_candidates.append(cand)

        pair_rows.append({
            "label": label,
            "road_type": pair["road_type"],
            "original_distance_m": pair["original_distance_m"],
            "n_ways": len(way_ids),
            "n_segments": pair["n_segments"],
            "n_oneway_ways": n_oneway,
            "n_detected": len(pair["detected_oneway_way_ids"]),
            "n_fn": n_fn,
            "n_tag_missing": n_tag_missing,
            "using_edge_ids": pair["using_edge_ids"],
            "rerouted": pair["rerouted"],
            "error": "",
        })
        print(f"    → 通過way={len(way_ids)} (区間{pair['n_segments']}) "
              f"oneway={n_oneway} 検出={len(pair['detected_oneway_way_ids'])} FN候補={n_fn}")

    await enrich_unknown_with_match_info(fn_candidates)
    return pair_rows, all_ways, fn_candidates


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def tags_raw(tags: dict) -> str:
    """OSM タグ全文を `k=v; k=v` 形式にする（人手確認用なので省略しない）。"""
    if not tags:
        return "(no tags)"
    return "; ".join(f"{k}={tags[k]}" for k in sorted(tags))


def osm_url(way_id: int) -> str:
    return f"https://www.openstreetmap.org/way/{way_id}"


def write_csv(fn_candidates: list[dict]) -> None:
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for c in fn_candidates:
            tv = c["travel_vector"] or [None, None]
            writer.writerow({
                "label": c["label"],
                "way_id": c["way_id"],
                "fn_reason": c["fn_reason"],
                "needs_manual_check": NEEDS_MANUAL_CHECK[c["fn_reason"]],
                "point_lat": c["point_lat"],
                "point_lng": c["point_lng"],
                "osm_url": osm_url(c["way_id"]),
                "oneway": c["oneway"],
                "highway": c["highway"],
                "name": c["name"],
                "osm_tags_raw": tags_raw(c["tags"]),
                "start_idx": c["start_idx"],
                "end_idx": c["end_idx"],
                "geom_nodes": c["geom_nodes"],
                "trimmed_length_m": c["trimmed_length_m"],
                "going_wrong_way": c["going_wrong_way"],
                "against_votes": c["against_votes"],
                "direction_votes": c["direction_votes"],
                "angle_way_axis_vs_travel_deg": c["angle_deg"],
                "travel_vector_lng": tv[0],
                "travel_vector_lat": tv[1],
                "oneway_bicycle": c["tags"].get("oneway:bicycle", ""),
                "cycleway": c["tags"].get("cycleway", ""),
                "using_edge_ids": c["using_edge_ids"],
                "diagnosis_detail": c["diagnosis_detail"],
                "match_way_id": c["match_way_id"],
                "match_dist_m": c["match_dist_m"],
                "match_margin_m": c["match_margin_m"],
                "match_ambiguous": c["match_ambiguous"],
            })


def pct(n: int, d: int) -> str:
    return "—" if not d else f"{n / d * 100:.1f}%"


def build_markdown(gh_info: dict, started_at: str, pair_rows: list[dict],
                   all_ways: list[dict], fn_candidates: list[dict],
                   od_total: int, interval_s: float) -> str:
    L: list[str] = []
    a = L.append

    ok_pairs = [p for p in pair_rows if not p["error"]]
    err_pairs = [p for p in pair_rows if p["error"]]

    total_ways = len(all_ways)                       # 延べ（ペア×way）
    unique_ways = len({w["way_id"] for w in all_ways})
    oneway_ways = [w for w in all_ways if w["is_oneway"]]
    n_oneway = len(oneway_ways)
    n_oneway_unique = len({w["way_id"] for w in oneway_ways})
    detected = [w for w in oneway_ways if w["detected"]]
    n_detected = len(detected)
    n_fn = len(fn_candidates)
    n_tag_missing = sum(p["n_tag_missing"] for p in ok_pairs)

    counts = {r: sum(1 for c in fn_candidates if c["fn_reason"] == r) for r in FN_REASONS}
    manual = [c for c in fn_candidates if NEEDS_MANUAL_CHECK[c["fn_reason"]]]

    # ---- 見出し ----------------------------------------------------------
    a("# R1 見逃し（FN）候補の抽出 — 一方通行")
    a("")
    a("`ground_truth_template.csv` は検出された違反の行しか持たないため、Recall の分母")
    a("（見逃しの母集団）が存在しない。本レポートはその母集団を論理的に絞り込んで作る。")
    a("")
    a("> 一方通行違反は `oneway=yes`（または `-1` 等）タグを持つ way 上でしか発生し得ない。")
    a("> したがって見逃し候補は「初期ルートが通った way のうち、`oneway` 系タグを持つが")
    a("> 違反として検出されなかったもの」に限定できる。")
    a("")
    a("**この絞り込みの限界（重要）：** 「現地は一方通行だが OSM にタグがない」ケースは")
    a("この方法では抽出できない。これは限界1（RESEARCH.md 24.1節・A群6件）の裏返しであり、")
    a("**原理的に測定不能**である。したがって本レポートが与えるのは")
    a("**「タグ基準の Recall」であって現実基準の Recall ではない。** 論文で引用する際は")
    a("この条件を必ず併記すること。")
    a("")
    a("生成スクリプト: `backend/scripts/extract_fn_candidates.py`  ")
    a(f"明細CSV: `backend/data/{OUT_CSV.name}`")
    a("")

    # ---- A ---------------------------------------------------------------
    a("## A. 実行条件")
    a("")
    a("OSM は更新され続けるため、下記の実行日時と GraphHopper のデータ基準時刻を")
    a("外して数値を引用すると再現できない。")
    a("")
    a("| 項目 | 値 |")
    a("|---|---|")
    a(f"| 実行日時（ローカル） | {started_at} |")
    a(f"| 完了日時（ローカル） | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |")
    a(f"| GraphHopper `datareader.data.date` | `{gh_info['data_date'] or '取得失敗'}` |")
    a(f"| GraphHopper `datareader.import.date` | `{gh_info['import_date'] or '—'}` |")
    a(f"| GraphHopper バージョン | {gh_info['gh_version'] or '—'} |")
    a(f"| 取得元 | {gh_info['source'] or '—'} |")
    a("| 判定バージョン | v3（edge_id ベース + 進行方向照合） |")
    a(f"| 対象 | `od_pairs.csv` {od_total} ペア（成功 {len(ok_pairs)} / 失敗 {len(err_pairs)}） |")
    a(f"| Overpass 呼び出し間隔 | {interval_s:.1f} 秒 |")
    if gh_info["error"]:
        a(f"| ⚠ `/info` エラー | `{gh_info['error']}` |")
    a("")
    if not gh_info["data_date"]:
        a("> ⚠ **`datareader.data.date` を取得できていない。** この状態のレポートは")
        a("> 再現条件が欠けているため、論文への引用前に GraphHopper の `/info` または")
        a("> `graphhopper/default-gh/properties.txt` から値を確認して追記すること。")
        a("")

    a("### ペアごとの初期ルート")
    a("")
    a("`analyze_route` は **初期ルート**（`original_route`）に対して判定しているため、")
    a("通過 way もリルート後ではなく初期ルートから取っている。")
    a("")
    a("| label | 道路種別 | 初期ルート距離(m) | 通過way(ユニーク) | way区間数 | "
      "oneway way | 検出 | FN候補 | using_edge_ids | リルート |")
    a("|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    for p in pair_rows:
        if p["error"]:
            a(f"| {p['label']} | {p['road_type']} | — | — | — | — | — | — | — | "
              f"⚠ {p['error']} |")
            continue
        a(f"| {p['label']} | {p['road_type']} | {fmt(p['original_distance_m'], 1)} "
          f"| {p['n_ways']} | {p['n_segments']} | {p['n_oneway_ways']} "
          f"| {p['n_detected']} | {p['n_fn']} | {fmt(p['using_edge_ids'])} "
          f"| {fmt(p['rerouted'])} |")
    a(f"| **合計** | | | **{total_ways}** | "
      f"**{sum(p['n_segments'] for p in ok_pairs)}** | **{n_oneway}** | "
      f"**{n_detected}** | **{n_fn}** | | |")
    a("")
    if n_tag_missing:
        a(f"⚠ Overpass がタグを返さなかった way が {n_tag_missing} 件ある。"
          "これらは oneway かどうか判定できないため母集団に入っていない"
          "（母集団を過小評価している可能性がある）。")
        a("")

    # ---- B ---------------------------------------------------------------
    a("## B. 母集団のサイズ")
    a("")
    a("「延べ」は同じ way が別ペアで再登場した場合に別件として数えたもの、")
    a("「ユニーク」は way_id で重複を除いたもの。判定器はペアごとに独立して")
    a("判定するため、**Recall の母集団は延べの側**である。")
    a("")
    a("| 項目 | 件数（延べ） | 件数（ユニーク） | 延べに対する割合 |")
    a("|---|---:|---:|---:|")
    a(f"| 初期ルートが通った way | {total_ways} | {unique_ways} | 100.0% |")
    a(f"| うち `oneway` タグを持つ way | {n_oneway} | {n_oneway_unique} | {pct(n_oneway, total_ways)} |")
    a(f"| うち違反として検出された way | {n_detected} | {len({w['way_id'] for w in detected})} "
      f"| {pct(n_detected, total_ways)} |")
    a(f"| **FN候補（oneway あり・未検出）** | **{n_fn}** "
      f"| **{len({c['way_id'] for c in fn_candidates})}** | **{pct(n_fn, total_ways)}** |")
    a("")
    a(f"`oneway` way に対する検出率（内訳の確認用）: {n_detected}/{n_oneway} = "
      f"{pct(n_detected, n_oneway)}")
    a("")
    a(f"**この `oneway` way {n_oneway} 件が Recall の分母（母集団）になる。**")
    a("")

    # ---- C ---------------------------------------------------------------
    a("## C. FN候補の診断内訳")
    a("")
    a("優先順に最初に該当した理由を `fn_reason` として記録している。")
    a("")
    a("| 優先 | `fn_reason` | 判定条件 | 人手確認 | 件数 | FN候補に占める割合 |")
    a("|---:|---|---|---|---:|---:|")
    for i, r in enumerate(FN_REASONS, start=1):
        need = "**要確認**" if NEEDS_MANUAL_CHECK[r] else "不要（正しい非検出）"
        a(f"| {i} | `{r}` | {REASON_DESCRIPTION[r]} | {need} | {counts[r]} | {pct(counts[r], n_fn)} |")
    a(f"| | **合計** | | | **{n_fn}** | |")
    a("")
    n_manual = len(manual)
    a(f"**人手確認が必要なFN候補: {n_manual} 件**"
      f"（`short_segment` {counts['short_segment']} / "
      f"`tag_fetch_failed` {counts['tag_fetch_failed']} / "
      f"`unknown` {counts['unknown']}）")
    a("")
    if counts["unknown"] == 0:
        a("`unknown` は **0 件**。`check_oneway_violation` は `oneway` タグの有無を")
        a("決定的に判定するため、タグがあれば必ず判定に入る。設計どおりに動いている")
        a("ことの確認であり、数字を作りにいった結果ではない。この結果は")
        a("**「タグベース判定は見逃さないが空振りする」という非対称性**を示す材料になる")
        a("（Precision 側の A群・B群＝誤検出と対をなす）。")
    else:
        a(f"⚠ `unknown` が {counts['unknown']} 件出ている。`check_oneway_violation` は")
        a("`oneway` タグがあれば必ず判定に入るため、本来これは 0 件になるはずである。")
        a("限界2（分離ウェイの誤マッチ・24.2節）・限界3（候補の僅差性・24.3節）の")
        a("別の現れ方である可能性がある。下表に各候補の `match_ambiguous` と")
        a("最近傍候補の状況を併記した。")
    a("")
    if counts["short_segment"]:
        a(f"補足: `short_segment` が {counts['short_segment']} 件ある。現行の")
        a("`check_oneway_violation` では短区間でも confidence が 0.7 に下がるだけで")
        a("検出自体は残るため、本来この理由で未検出になることはない。"
          "**この件数が 0 でない場合は判定経路の確認が必要。**")
        a("")

    # ---- D ---------------------------------------------------------------
    a("## D. 人手確認が必要なFN候補の一覧")
    a("")
    a("この表がそのまま人手判定の作業リストになる。`osm_url` を開き、"
      "現地（Street View）と OSM タグを突き合わせて真の見逃しかどうかを判定する。")
    a("")
    if not manual:
        a("**該当なし。** 全FN候補が `bicycle_exempt` / `forward_travel`"
          "（＝正しい非検出）で説明できた。")
        a("")
    else:
        a("| # | way_id | label | 座標(lat, lng) | OSM | `fn_reason` | 診断の詳細 |")
        a("|---:|---:|---|---|---|---|---|")
        order = {r: i for i, r in enumerate(FN_REASONS)}
        manual_sorted = sorted(manual, key=lambda c: (order[c["fn_reason"]], c["label"], c["way_id"]))
        for i, c in enumerate(manual_sorted, start=1):
            a(f"| {i} | {c['way_id']} | {c['label']} "
              f"| {fmt(c['point_lat'], 7)}, {fmt(c['point_lng'], 7)} "
              f"| [way]({osm_url(c['way_id'])}) | `{c['fn_reason']}` | {c['diagnosis_detail']} |")
        a("")
        a("### OSMタグ全文と診断値")
        a("")
        a("| # | way_id | OSMタグ全文 | 区間長(m) | ノード数 | 角度差(°) | 逆走票 | "
          "`going_wrong_way` |")
        a("|---:|---:|---|---:|---:|---:|---:|---|")
        for i, c in enumerate(manual_sorted, start=1):
            votes = ("—" if c["direction_votes"] is None
                     else f"{c['against_votes']}/{c['direction_votes']}")
            a(f"| {i} | {c['way_id']} | `{tags_raw(c['tags'])}` "
              f"| {fmt(c['trimmed_length_m'], 1)} | {c['geom_nodes']} "
              f"| {fmt(c['angle_deg'], 1)} | {votes} | {fmt(c['going_wrong_way'])} |")
        a("")
        unknowns = [c for c in manual_sorted if c["fn_reason"] == "unknown"]
        if unknowns:
            a("### `unknown` 候補の最近傍マッチ状況（限界2・限界3の確認用）")
            a("")
            a("edge_id ベース判定では way_id を GraphHopper から直接受け取るため、"
              "本来ここに最近傍マッチングは介在しない。それでも `unknown` が出た場合の"
              "補足情報として、当該地点で最近傍マッチングを行った場合の候補状況を"
              "**説明のためだけに**併記する（判定にも候補選択にも使っていない）。")
            a("")
            a(f"| way_id | label | `match_way_id` | `match_dist_m` | `match_margin_m` "
              f"| `match_ambiguous`（< {MATCH_AMBIGUOUS_MARGIN_M}m） |")
            a("|---:|---|---:|---:|---:|---|")
            for c in unknowns:
                a(f"| {c['way_id']} | {c['label']} | {fmt(c['match_way_id'])} "
                  f"| {fmt(c['match_dist_m'], 3)} | {fmt(c['match_margin_m'], 3)} "
                  f"| {fmt(c['match_ambiguous'])} |")
            a("")
            a("`match_way_id` が FN候補の way_id と食い違っている、あるいは")
            a("`match_ambiguous=True` の場合、その候補は限界3（候補の僅差性）の影響下にある。")
            a("")

    # ---- E ---------------------------------------------------------------
    a("## E. タグ基準 Recall の暫定値")
    a("")
    a("**人手判定前なので確定値は出せない。** 下記は上下の幅を示すためのもの。")
    a("")
    a("記号: D = 同一実行で検出された `oneway` 違反 way 数、"
      "U = `unknown`、S = `short_segment`、T = `tag_fetch_failed`。")
    a("")
    a(f"- D = {n_detected}, U = {counts['unknown']}, "
      f"S = {counts['short_segment']}, T = {counts['tag_fetch_failed']}")
    a("")
    a("| 仮定 | 計算式 | Recall |")
    a("|---|---|---:|")
    if n_detected == 0:
        a("| — | — | 検出が0件のため算出不能 |")
    else:
        best = n_detected / n_detected
        worst_u = n_detected / (n_detected + counts["unknown"])
        worst_all = n_detected / (n_detected + n_manual)
        a(f"| 最良（`unknown` は全て正しい非検出） | {n_detected} / ({n_detected} + 0) "
          f"| **{best:.3f}** |")
        a(f"| 最悪（`unknown` は全て真の見逃し） | {n_detected} / ({n_detected} + {counts['unknown']}) "
          f"| **{worst_u:.3f}** |")
        a(f"| 参考（要確認 {n_manual} 件を全て見逃しと仮定） "
          f"| {n_detected} / ({n_detected} + {n_manual}) | {worst_all:.3f} |")
    a("")
    a("**この値の読み方（注意）：**")
    a("")
    a("- 分子の D は「検出された件数」であり、**真陽性の件数ではない**。"
      "現地確認で偽陽性と判明した分だけ分子は下がる。R1 の Precision 側"
      "（現地確認済みの分類）を確定させてから改めて計算すること。")
    a("- 分母は **OSM に `oneway` タグがある way に限定**されている。"
      "「現地は一方通行だがタグがない」道（限界1・A群と同じ構造）は最初から"
      "母集団に入らないため、この Recall は現実基準の Recall の**上限側**に偏る。")
    a("- `bicycle_exempt` / `forward_travel` は「検出しないのが正しい」ケースなので、"
      "見逃しには数えていない（真陰性に相当する）。")
    a("")

    # ---- 付記 ------------------------------------------------------------
    a("## 付記：診断ロジックの出所")
    a("")
    a("- 進行方向照合は `services/law_checker.py` の `_check_direction` を、"
      "ジオメトリのクリップは `services/route_analyzer.py` の `_trim_geometry` を"
      "**そのまま呼んでいる**（スクリプト側で再実装・近似していない）。")
    a("- way ごとの区間（`start_idx`〜`end_idx`）と travel_vector の作り方は"
      "`_analyze_v3` と同一。同じ way が複数区間に現れた場合に最初の区間のみを"
      "採用する規則も揃えてある。")
    a("- **方向情報は「なぜ検出されなかったか」の説明にのみ使い、マッチ候補の選択には"
      "使っていない**（循環論法の回避・RESEARCH.md 21.11節）。")
    a("- 検出された違反の way_id は過去の CSV ではなく"
      "**同一実行のレスポンス `violations[]`** から取っている（OSM 更新で値が変わるため）。")
    a("")

    if err_pairs:
        a("## ⚠ 失敗したペア")
        a("")
        for p in err_pairs:
            a(f"- {p['label']}: `{p['error']}`")
        a("")
        a("失敗したペアは母集団に含まれていない。上記の件数はその分だけ過小である。")
        a("")

    return "\n".join(L) + "\n"


async def main(labels: list[str] | None, interval_s: float) -> int:
    with open(OD_PAIRS_CSV, encoding="utf-8", newline="") as f:
        od_rows = list(csv.DictReader(f))
    od_total = len(od_rows)

    if labels:
        available = {r["label"] for r in od_rows}
        missing = set(labels) - available
        if missing:
            print(f"⚠ od_pairs.csv に存在しない label: {sorted(missing)}")
            return 1
        od_rows = [r for r in od_rows if r["label"] in labels]

    started_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"開始: {started_at}")

    gh_info = await fetch_gh_data_date()
    print(f"GraphHopper datareader.data.date = {gh_info['data_date'] or '取得失敗'}"
          f"（{gh_info['source'] or gh_info['error']}）")

    print(f"対象 O-D ペア: {len(od_rows)} 件")
    try:
        pair_rows, all_ways, fn_candidates = await collect(od_rows, interval_s)
    finally:
        await close_client()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(fn_candidates)
    OUT_MD.write_text(
        build_markdown(gh_info, started_at, pair_rows, all_ways, fn_candidates,
                       od_total, interval_s),
        encoding="utf-8",
    )

    counts = {r: sum(1 for c in fn_candidates if c["fn_reason"] == r) for r in FN_REASONS}
    print(f"\n出力: {OUT_MD}")
    print(f"出力: {OUT_CSV}")
    print(f"  通過way（延べ）: {len(all_ways)} / ユニーク: {len({w['way_id'] for w in all_ways})}")
    print(f"  oneway way: {sum(1 for w in all_ways if w['is_oneway'])}")
    print(f"  FN候補: {len(fn_candidates)}")
    for r in FN_REASONS:
        print(f"    {r}: {counts[r]}")
    if any(p["error"] for p in pair_rows):
        print("  ⚠ 失敗したペアがあります。レポート末尾を確認してください。")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="R1 の見逃し（FN）候補を抽出し、レポートと明細CSVを出力する"
    )
    parser.add_argument("--labels", nargs="*", default=None,
                        help="処理する label を限定する（既定: od_pairs.csv 全件）")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL_S,
                        help=f"ペア間の待機秒数（既定: {REQUEST_INTERVAL_S}）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.labels, args.interval)))
