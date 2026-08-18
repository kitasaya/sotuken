"""`extract_fn_candidates.py` の診断ロジックの smoke テスト（ネットワーク不要）。

GraphHopper と Overpass の応答を合成データでスタブし、FN候補の抽出と
`fn_reason` の診断が仕様どおりに動くことを確認する。ネットワークには
一切アクセスせず、`backend/data/` 配下の既存ファイルも書き換えない
（出力先は一時ディレクトリに差し替える）。

  python3 scripts/smoke_test_fn_candidates.py

全ケース成功で exit 0、1件でも失敗すれば exit 1。

## 合成ルート

西→東へ直進する13点のルート上に、6本の way を並べる。

  way 1001  oneway=yes / ジオメトリ順方向        → 順走なので未検出（forward_travel）
  way 1002  oneway=yes / ジオメトリ逆方向        → 逆走なので検出される（FN候補にならない）
  way 1003  oneway=yes + oneway:bicycle=no / 逆  → 自転車除外（bicycle_exempt）
  way 1004  oneway なし                          → 母集団に入らない
  way 1005  oneway=-1 / ジオメトリ順方向         → -1 は判定が反転するので検出される
  way 1006  oneway=yes + cycleway=opposite / 逆  → 自転車除外（bicycle_exempt）
"""

import asyncio
import csv
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import extract_fn_candidates as fnc
from services import overpass as overpass_mod
from services import route_analyzer

# 渋谷駅付近を起点に、東へ約20m刻みで13点並べる
BASE_LAT, BASE_LNG = 35.6580, 139.7016
_M_PER_DEG_LAT = 110_540.0
STEP_M = 20.0


def _deg_lng(m: float, lat: float = BASE_LAT) -> float:
    return m / (111_320.0 * math.cos(math.radians(lat)))


ROUTE_POINTS = [[BASE_LNG + _deg_lng(STEP_M * i), BASE_LAT] for i in range(13)]

# (way_id, start_idx, end_idx, ジオメトリを逆順にするか, タグ)
WAY_SPEC = [
    (1001, 0, 2, False, {"highway": "residential", "oneway": "yes", "name": "順走テスト路"}),
    (1002, 2, 4, True, {"highway": "residential", "oneway": "yes", "name": "逆走テスト路"}),
    (1003, 4, 6, True, {"highway": "residential", "oneway": "yes",
                        "oneway:bicycle": "no", "name": "自転車除外路"}),
    (1004, 6, 8, False, {"highway": "residential", "name": "一方通行でない路"}),
    (1005, 8, 10, False, {"highway": "residential", "oneway": "-1", "name": "逆向き一方通行路"}),
    (1006, 10, 12, True, {"highway": "residential", "oneway": "yes",
                          "cycleway": "opposite", "name": "対向レーン路"}),
]

# 期待される結果: way_id -> (母集団に入るか, 検出されるか, fn_reason)
EXPECTED = {
    1001: (True, False, "forward_travel"),
    1002: (True, True, None),
    1003: (True, False, "bicycle_exempt"),
    1004: (False, False, None),
    1005: (True, True, None),
    1006: (True, False, "bicycle_exempt"),
}


def _elements() -> list[dict]:
    """Overpass が返す way element 群（合成）。"""
    els = []
    for wid, s, e, reverse, tags in WAY_SPEC:
        nodes = ROUTE_POINTS[s:e + 1]
        if reverse:
            nodes = list(reversed(nodes))
        els.append({
            "type": "way", "id": wid, "tags": tags,
            "geometry": [{"lon": p[0], "lat": p[1]} for p in nodes],
        })
    return els


def _route_data() -> dict:
    return {
        "paths": [{
            "distance": STEP_M * (len(ROUTE_POINTS) - 1),
            "time": 60_000,
            "points": {"type": "LineString", "coordinates": ROUTE_POINTS},
            "instructions": [],
            "details": {
                "osm_way_id": [[s, e, wid] for wid, s, e, _, _ in WAY_SPEC],
                "road_class": [],
            },
        }]
    }


# ---------------------------------------------------------------------------
# 検査ヘルパ
# ---------------------------------------------------------------------------

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# ケース
# ---------------------------------------------------------------------------

async def case_pipeline(tmp: Path) -> None:
    """スタブしたルートで抽出パイプライン全体を通す。"""
    print("\n[1] 抽出パイプライン（GraphHopper / Overpass をスタブ）")

    async def fake_get_route(*_args, **_kwargs):
        return _route_data()

    async def fake_compliant_route(*_args, **_kwargs):
        return _route_data()

    async def fake_post(_query: str) -> list:
        return _elements()

    overpass_mod.clear_way_cache()
    route_analyzer.get_route = fake_get_route
    route_analyzer.get_compliant_route = fake_compliant_route
    overpass_mod._post_with_retry = fake_post

    od_row = {
        "label": "スタブ", "road_type": "テスト",
        "origin_lat": BASE_LAT, "origin_lng": BASE_LNG,
        "dest_lat": BASE_LAT, "dest_lng": ROUTE_POINTS[-1][0],
    }
    pair_rows, all_ways, fn_candidates = await fnc.collect([od_row], interval_s=0.0)

    by_id = {w["way_id"]: w for w in all_ways}
    fn_by_id = {c["way_id"]: c for c in fn_candidates}

    check(len(all_ways) == len(WAY_SPEC), "通過 way を全件拾えている",
          f"{len(all_ways)} 件（期待 {len(WAY_SPEC)}）")
    check(pair_rows[0]["n_segments"] == len(WAY_SPEC), "way 区間数が details と一致",
          f"{pair_rows[0]['n_segments']}")
    check(pair_rows[0]["using_edge_ids"] is True, "edge_id ベース判定で動いている")

    for wid, (in_pop, detected, reason) in EXPECTED.items():
        w = by_id.get(wid)
        if w is None:
            check(False, f"way {wid} が通過 way に含まれる")
            continue
        check(w["is_oneway"] is in_pop,
              f"way {wid}: 母集団に入る={in_pop}", f"oneway={w['oneway']!r}")
        check(w["detected"] is detected, f"way {wid}: 検出={detected}")
        if reason is None:
            check(wid not in fn_by_id, f"way {wid}: FN候補ではない")
        else:
            c = fn_by_id.get(wid)
            check(c is not None and c["fn_reason"] == reason,
                  f"way {wid}: fn_reason={reason}",
                  f"実際={c['fn_reason'] if c else 'FN候補に無い'}")

    n_expected_fn = sum(1 for v in EXPECTED.values() if v[2] is not None)
    check(len(fn_candidates) == n_expected_fn, "FN候補の件数",
          f"{len(fn_candidates)} 件（期待 {n_expected_fn}）")
    check(all(fnc.NEEDS_MANUAL_CHECK[c["fn_reason"]] is False for c in fn_candidates),
          "全FN候補が「人手確認不要」に分類される（正しい非検出のみのため）")

    # 出力（一時ディレクトリへ差し替え。data/ の既存ファイルは触らない）
    fnc.OUT_CSV = tmp / "fn_candidates_oneway.csv"
    fnc.OUT_MD = tmp / "fn_candidates_oneway.md"
    fnc.write_csv(fn_candidates)
    md = fnc.build_markdown(
        {"source": "stub", "data_date": "2026-05-11T20:20:52Z",
         "import_date": "", "gh_version": "", "error": ""},
        "2026-01-01 00:00:00", pair_rows, all_ways, fn_candidates, 1, 0.0,
    )
    fnc.OUT_MD.write_text(md, encoding="utf-8")

    with open(fnc.OUT_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    check(len(rows) == n_expected_fn, "CSV の行数が FN候補数と一致", f"{len(rows)} 行")
    check(list(rows[0].keys()) == fnc.CSV_FIELDNAMES, "CSV の列が定義どおり")
    check(all(r["osm_url"].endswith(r["way_id"]) for r in rows),
          "osm_url が way_id を指している")
    check(all(r["osm_tags_raw"] and r["osm_tags_raw"] != "(no tags)" for r in rows),
          "osm_tags_raw が全件埋まっている")

    for heading in ("## A. 実行条件", "## B. 母集団のサイズ", "## C. FN候補の診断内訳",
                    "## D. 人手確認が必要なFN候補の一覧", "## E. タグ基準 Recall の暫定値"):
        check(heading in md, f"レポートに `{heading}` がある")
    check("2026-05-11T20:20:52Z" in md, "レポートに datareader.data.date が記録される")


def case_diagnose_units() -> None:
    """`diagnose()` の各分岐を直接叩く（パイプラインでは再現できない分岐を含む）。"""
    print("\n[2] diagnose() の分岐")

    def rec(**over) -> dict:
        base = {
            "tags": {"oneway": "yes", "highway": "residential"},
            "geometry": [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]],
            "travel_vector": [1.0, 0.0],
            "going_wrong_way": True,
            "against_votes": 2, "direction_votes": 2,
            "angle_deg": 180.0, "trimmed_length_m": 100.0,
            "using_edge_ids": True,
        }
        base.update(over)
        return base

    cases = [
        ("oneway:bicycle=no は bicycle_exempt",
         rec(tags={"oneway": "yes", "oneway:bicycle": "no"}), "bicycle_exempt"),
        ("cycleway=opposite_lane は bicycle_exempt",
         rec(tags={"oneway": "yes", "cycleway": "opposite_lane"}), "bicycle_exempt"),
        ("cycleway=opposite_track は bicycle_exempt",
         rec(tags={"oneway": "yes", "cycleway": "opposite_track"}), "bicycle_exempt"),
        ("順走は forward_travel", rec(going_wrong_way=False, against_votes=0), "forward_travel"),
        ("逆走かつ区間長 < 20m は short_segment", rec(trimmed_length_m=12.0), "short_segment"),
        ("区間長 20m ちょうどは short_segment にしない", rec(trimmed_length_m=20.0), "unknown"),
        ("タグ空は tag_fetch_failed", rec(tags={}, geometry=[]), "tag_fetch_failed"),
        ("ノード1本は tag_fetch_failed", rec(geometry=[[0.0, 0.0]]), "tag_fetch_failed"),
        ("using_edge_ids=False は tag_fetch_failed",
         rec(using_edge_ids=False), "tag_fetch_failed"),
        ("説明がつかない未検出は unknown", rec(), "unknown"),
    ]
    for name, r, expected in cases:
        reason, detail = fnc.diagnose(r)
        check(reason == expected, name, f"{reason}（期待 {expected}）")
        check(bool(detail), f"{name}: 診断の詳細文が空でない")

    # 優先順: 自転車除外は進行方向照合より先に効く
    reason, _ = fnc.diagnose(rec(tags={"oneway": "yes", "oneway:bicycle": "no"},
                                 going_wrong_way=False))
    check(reason == "bicycle_exempt", "優先順1（bicycle_exempt）が優先順2（forward_travel）に勝つ")

    # using_edge_ids=False は自転車除外より先に効く（way 単位の評価が行われていないため）
    reason, _ = fnc.diagnose(rec(tags={"oneway": "yes", "oneway:bicycle": "no"},
                                 using_edge_ids=False))
    check(reason == "tag_fetch_failed",
          "using_edge_ids=False は他の理由より先に tag_fetch_failed になる")


def case_segment_dedup() -> None:
    """同じ way が複数区間に現れた場合、最初の区間のみ採用すること。"""
    print("\n[3] way 区間の重複解決（_analyze_v3 と同一規則）")
    original = {
        "points": {"coordinates": ROUTE_POINTS},
        "details": {"osm_way_id": [[0, 2, 2001], [2, 4, 2002], [4, 8, 2001]]},
    }
    info, n_seg = fnc.collect_way_segments(original)
    check(n_seg == 3, "セグメント数は details の要素数", f"{n_seg}")
    check(set(info) == {2001, 2002}, "way_id はユニーク化される", f"{sorted(info)}")
    check(info[2001]["start_idx"] == 0 and info[2001]["end_idx"] == 2,
          "重複 way は最初の区間を採用する",
          f"start={info[2001]['start_idx']} end={info[2001]['end_idx']}")


def case_direction_reuse() -> None:
    """逆走判定が law_checker._check_direction そのものであること。"""
    print("\n[4] 進行方向照合は law_checker の関数をそのまま使う")
    from services.law_checker import _check_direction

    fwd_geom = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
    rev_geom = list(reversed(fwd_geom))
    tv = [1.0, 0.0]

    check(_check_direction(fwd_geom, tv, "yes") is False, "順方向 + oneway=yes → 逆走でない")
    check(_check_direction(rev_geom, tv, "yes") is True, "逆方向 + oneway=yes → 逆走")
    check(_check_direction(fwd_geom, tv, "-1") is True, "順方向 + oneway=-1 → 逆走（判定が反転）")
    check(_check_direction(rev_geom, tv, "-1") is False, "逆方向 + oneway=-1 → 逆走でない")

    check(fnc.ONEWAY_VALUES == ("yes", "true", "1", "-1"),
          "母集団の oneway 値が law_checker の判定条件と同じ", f"{fnc.ONEWAY_VALUES}")
    check(fnc.CYCLEWAY_EXEMPT_VALUES == ("opposite", "opposite_lane", "opposite_track"),
          "自転車除外の cycleway 値が law_checker と同じ")
    check(fnc.SHORT_SEGMENT_M == 20.0, "短区間しきい値が law_checker と同じ（20m）")


async def main() -> int:
    orig_get_route = route_analyzer.get_route
    orig_compliant = route_analyzer.get_compliant_route
    orig_post = overpass_mod._post_with_retry
    orig_csv, orig_md = fnc.OUT_CSV, fnc.OUT_MD
    try:
        with tempfile.TemporaryDirectory() as td:
            await case_pipeline(Path(td))
        case_diagnose_units()
        case_segment_dedup()
        case_direction_reuse()
    finally:
        route_analyzer.get_route = orig_get_route
        route_analyzer.get_compliant_route = orig_compliant
        overpass_mod._post_with_retry = orig_post
        fnc.OUT_CSV, fnc.OUT_MD = orig_csv, orig_md
        overpass_mod.clear_way_cache()

    failed = [r for r in _results if not r[0]]
    print(f"\n{'=' * 70}")
    print(f"合計 {len(_results)} 件 / 成功 {len(_results) - len(failed)} 件 / 失敗 {len(failed)} 件")
    if failed:
        print("\n失敗したケース:")
        for _, name, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print("全ケース成功。FN候補の抽出と fn_reason の診断は仕様どおり動作する。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
