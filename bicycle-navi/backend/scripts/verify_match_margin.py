"""① match_margin_m / match_ambiguous 実装後の検証（A・B・C）。

  A. マージン分布 …… 14地点（想定外4・B群4・C群3・残存A群3）の match_margin_m を
     群ごとに集計し、RESEARCH.md 21.10節の報告値と一致するかを確認する
  B. 曖昧地点の割合 … google_routes_input.csv の全判定点のうち
     match_ambiguous=True の割合（論文で「M件は判定不能」と報告する際の母数）
  C. 自システムへの影響 … od_pairs.csv の15ペアで analyze_route(v3) を実行し、
     距離・違反数が google_comparison.csv の値から変化していないことを確認

  python3 scripts/verify_match_margin.py                      # A・B・C
  python3 scripts/verify_match_margin.py --skip-analyze-route # A・B のみ（GraphHopper不要）
  python3 scripts/verify_match_margin.py --only-analyze-route # C のみ

出力（いずれも新規ファイル。既存CSVは一切書き換えない）：
  data/fix_verification_v2.md             … 報告書
  data/verify_match_margin_points.csv     … 全判定点の明細（A・B の生データ）
  data/verify_v2_analyze_route.csv        … C の実行結果
"""

import argparse
import asyncio
import csv
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.known_violations import (
    CONTROL_A_WAY_IDS,
    CONTROL_B_WAY_IDS,
    CONTROL_C_WAY_IDS,
    KNOWN_VIOLATIONS,
    UNEXPECTED_EXTRA,
    UNEXPECTED_WAY_IDS,
    group_of,
)
from scripts.route_match_probe import fmt, is_oneway_violation, median, probe_route
from services.overpass import MATCH_AMBIGUOUS_MARGIN_M

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"
OD_PAIRS_CSV = DATA_DIR / "od_pairs.csv"
COMPARISON_CSV = DATA_DIR / "google_comparison.csv"
OUT_MD = DATA_DIR / "fix_verification_v2.md"
OUT_POINTS_CSV = DATA_DIR / "verify_match_margin_points.csv"
OUT_ANALYZE_CSV = DATA_DIR / "verify_v2_analyze_route.csv"

REQUEST_INTERVAL_S = 3.0

# RESEARCH.md 21.10節で報告されたマージンの範囲
REPORTED_UNEXPECTED_RANGE = (0.50, 1.74)
REPORTED_NORMAL_MIN = 2.75

# 検証Aの4群（表示順）
VERIFY_GROUPS = ["想定外", "B群", "C群", "残存A群"]

_GROUP_BY_WAY_ID: dict[int, str] = {}
for _w in UNEXPECTED_WAY_IDS:
    _GROUP_BY_WAY_ID[_w] = "想定外"
for _w in CONTROL_B_WAY_IDS:
    _GROUP_BY_WAY_ID[_w] = "B群"
for _w in CONTROL_C_WAY_IDS:
    _GROUP_BY_WAY_ID[_w] = "C群"
for _w in CONTROL_A_WAY_IDS:
    _GROUP_BY_WAY_ID[_w] = "残存A群"


def verify_group_of(label: str, sample_idx: int, node_rank1_way_id) -> str:
    """判定点が検証Aの14地点のどれに当たるかを返す。該当なしなら空文字。

    **ノード距離ベース（垂線距離修正前）の rank1 で同定する。** 想定外4地点は
    修正後の rank1 が歩道・交差道路に変わっているため、修正後の way_id では引けない。
    """
    extra_label, extra_idx, _ = UNEXPECTED_EXTRA
    if label == extra_label and sample_idx == extra_idx:
        return "想定外"
    if node_rank1_way_id is None:
        return ""
    return _GROUP_BY_WAY_ID.get(int(node_rank1_way_id), "")


# ---------------------------------------------------------------------------
# A・B: 判定点の収集
# ---------------------------------------------------------------------------

async def collect_points(labels: list[str] | None) -> tuple[list[dict], list[str], list[str]]:
    with open(INPUT_CSV, encoding="utf-8", newline="") as f:
        input_rows = list(csv.DictReader(f))

    if labels:
        available = {r["label"] for r in input_rows}
        missing = set(labels) - available
        if missing:
            raise SystemExit(f"⚠ 入力ファイルに存在しない label: {sorted(missing)}")
        input_rows = [r for r in input_rows if r["label"] in labels]

    rows: list[dict] = []
    labels_run: list[str] = []
    skipped: list[str] = []

    for i, row in enumerate(input_rows):
        label = row["label"]
        polyline = row.get("polyline", "").strip()
        if not polyline:
            print(f"  [SKIP] {label}: polyline が空")
            skipped.append(label)
            continue

        if i > 0:
            await asyncio.sleep(REQUEST_INTERVAL_S)

        print(f"  採点中: {label}", flush=True)
        probes, _ = await probe_route(label, polyline)
        if not probes:
            skipped.append(label)
            continue
        if all(not p.candidates for p in probes):
            raise SystemExit(
                f"⚠ {label}: 全判定点で候補0本。Overpass 障害の可能性があるため中止します。"
            )

        for p in probes:
            detected, misaligned, conf = await is_oneway_violation(p, p.rank1)
            rows.append({
                "label": p.label,
                "sample_idx": p.sample_idx,
                "route_idx": p.route_idx,
                "lat": round(p.lat, 7),
                "lng": round(p.lng, 7),
                "candidate_count": len(p.candidates),
                "node_rank1_way_id": p.node_rank1_way_id,
                "perp_rank1_way_id": p.rank1.way_id if p.rank1 else None,
                "perp_rank1_highway": p.rank1.highway if p.rank1 else "",
                "perp_rank1_oneway": p.rank1.oneway if p.rank1 else "",
                "perp_rank1_name": p.rank1.name if p.rank1 else "",
                "rank2_way_id": p.rank2.way_id if p.rank2 else None,
                "rank2_highway": p.rank2.highway if p.rank2 else "",
                "rank2_dist_m": round(p.rank2.dist_m, 3) if p.rank2 else None,
                "match_dist_m": p.match_dist_m,
                "match_margin_m": p.match_margin_m,
                "match_ambiguous": p.match_ambiguous,
                "verify_group": verify_group_of(p.label, p.sample_idx, p.node_rank1_way_id),
                "known_group_node_rank1": group_of(p.node_rank1_way_id),
                "known_group_perp_rank1": group_of(p.rank1.way_id if p.rank1 else None),
                "oneway_violation": detected,
                "oneway_confidence": conf,
                "oneway_misaligned": misaligned,
                # 診断専用（候補選択には使わない）
                "diag_angle_deg": p.angle_to_travel_deg(p.rank1),
            })

        amb = sum(1 for r in rows if r["label"] == label and r["match_ambiguous"])
        print(f"    → 判定点={len(probes)} 曖昧={amb}")
        labels_run.append(label)

    return rows, labels_run, skipped


# ---------------------------------------------------------------------------
# C: analyze_route(v3) の実行
# ---------------------------------------------------------------------------

async def run_analyze_route() -> list[dict]:
    from services.route_analyzer import analyze_route

    with open(OD_PAIRS_CSV, encoding="utf-8", newline="") as f:
        pairs = list(csv.DictReader(f))

    baseline: dict[str, dict] = {}
    if COMPARISON_CSV.exists():
        with open(COMPARISON_CSV, encoding="utf-8", newline="") as f:
            baseline = {r["label"]: r for r in csv.DictReader(f)}

    results = []
    for p in pairs:
        label = p["label"]
        print(f"  analyze_route(v3): {label}", flush=True)
        try:
            res = await analyze_route(
                float(p["origin_lat"]), float(p["origin_lng"]),
                float(p["dest_lat"]), float(p["dest_lng"]),
                algo_version="v3",
            )
        except Exception as e:
            print(f"    ⚠ 失敗: {e}")
            results.append({"label": label, "road_type": p["road_type"], "error": str(e)})
            continue

        comp = res["comparison"]
        violations = res["violations"]
        high_conf = sum(1 for v in violations if v.get("confidence", 0.4) >= 0.7)
        time_s = round(res["route"].get("time", 0) / 1000, 1)

        base = baseline.get(label, {})

        def diff(new, old_str):
            try:
                old = float(old_str)
            except (TypeError, ValueError):
                return None
            return round(new - old, 1)

        results.append({
            "label": label,
            "road_type": p["road_type"],
            "new_distance_m": comp["compliant_distance_m"],
            "csv_system_distance_m": base.get("system_distance_m", ""),
            "distance_delta_m": diff(comp["compliant_distance_m"], base.get("system_distance_m")),
            "new_time_s": time_s,
            "csv_system_time_s": base.get("system_time_s", ""),
            "time_delta_s": diff(time_s, base.get("system_time_s")),
            "new_violation_count": comp["violation_count"],
            "csv_system_violation_count": base.get("system_violation_count", ""),
            "new_violation_count_high_conf": high_conf,
            "csv_system_violation_count_high_conf": base.get("system_violation_count_high_conf", ""),
            "new_original_distance_m": comp["original_distance_m"],
            "new_distance_diff_m": comp["distance_diff_m"],
            "rerouted": comp["rerouted"],
            "using_edge_ids": comp["using_edge_ids"],
            "violation_types": ",".join(sorted(comp["violation_types"])),
            "error": "",
        })
        print(f"    → dist={comp['compliant_distance_m']}m violations={comp['violation_count']} "
              f"(high_conf={high_conf}) edge_id={comp['using_edge_ids']}")

    return results


# ---------------------------------------------------------------------------
# 報告書
# ---------------------------------------------------------------------------

def build_markdown(
    point_rows: list[dict],
    labels_run: list[str],
    skipped: list[str],
    analyze_rows: list[dict] | None,
) -> str:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L: list[str] = []
    a = L.append

    a("# ① match_margin_m / match_ambiguous 実装の検証（v2）")
    a("")
    a(f"生成日時: {ts}  ")
    a("生成スクリプト: `backend/scripts/verify_match_margin.py`")
    a("")
    a("## 前提（重要）")
    a("")
    a("本検証時点の `services/overpass.py` は、**このタスクで書き直した**")
    a("`_point_to_segment_dist_m` / `_point_to_way_dist_m` / `_rank_way_candidates` により")
    a("point-to-curve マッチングを行っている。RESEARCH.md 21.8節が記録している")
    a("2026-07-25 の垂線距離修正はリポジトリに未コミットであり、コード上は")
    a("ノード距離ベースのままだったため、①の前提として復元したものである。")
    a("")
    a("したがって **21.10節の報告値と完全一致する保証はない**（別実装のため）。")
    a("A項ではその一致・乖離をそのまま提示する。数値を合わせにいく調整は行っていない。")
    a("")

    # ---- A ----------------------------------------------------------------
    a("## A. マージン分布（14地点）")
    a("")
    a("14地点 = 想定外4 + B群4（正しく消えた対照）+ C群3（真の違反）+ 残存A群3。")
    a("")
    a("**同定方法：** 想定外4地点は垂線距離マッチング後の rank1 が歩道・交差道路に")
    a("変わっているため、修正後の way_id では引けない。各判定点について")
    a("**ノード距離ベース（修正前）の rank1 way_id** を別途算出し、それで群を同定している。")
    a(f"新宿→池袋 は way_id ではなく判定点 idx={UNEXPECTED_EXTRA[1]} で特定されているため、")
    a("サンプル番号で照合している（サンプリング間隔 40m・元の再実行スクリプトと同条件）。")
    a("")

    matched = [r for r in point_rows if r["verify_group"]]
    if not matched:
        a("⚠ **14地点が1点も同定できなかった。** ノード距離 rank1 の way_id が")
        a("既知の way_id と一致しなかった。全ペアを対象に実行したか、")
        a("Overpass が正常に応答したかを確認すること。")
        a("")
    else:
        a("| 群 | label | idx | node_rank1<br>(修正前) | perp_rank1<br>(修正後) | rank2 | "
          "match_dist_m | **match_margin_m** | match_ambiguous | rank1 highway | oneway | 検出 |")
        a("|---|---|---:|---:|---:|---:|---:|---:|---|---|---|---|")
        order = {g: i for i, g in enumerate(VERIFY_GROUPS)}
        for r in sorted(matched, key=lambda r: (order.get(r["verify_group"], 9),
                                                r["label"], r["sample_idx"])):
            a(
                f"| {r['verify_group']} | {r['label']} | {r['sample_idx']} "
                f"| {fmt(r['node_rank1_way_id'])} | {fmt(r['perp_rank1_way_id'])} "
                f"| {fmt(r['rank2_way_id'])} | {fmt(r['match_dist_m'])} "
                f"| **{fmt(r['match_margin_m'])}** | {fmt(r['match_ambiguous'])} "
                f"| `{r['perp_rank1_highway']}` | {fmt(r['perp_rank1_oneway'])} "
                f"| {fmt(r['oneway_violation'])} |"
            )
        a("")

        a("### 群ごとの分布")
        a("")
        a("| 群 | 地点数 | 最小 | 中央値 | 最大 | 曖昧判定 |")
        a("|---|---:|---:|---:|---:|---:|")
        stats: dict[str, list[float]] = {}
        for g in VERIFY_GROUPS:
            vals = [r["match_margin_m"] for r in matched
                    if r["verify_group"] == g and r["match_margin_m"] is not None]
            stats[g] = vals
            n_amb = sum(1 for r in matched if r["verify_group"] == g and r["match_ambiguous"])
            n_pts = sum(1 for r in matched if r["verify_group"] == g)
            a(f"| {g} | {n_pts} | {fmt(min(vals) if vals else None)} "
              f"| {fmt(median(vals))} | {fmt(max(vals) if vals else None)} | {n_amb}/{n_pts} |")
        a("")

        a("### RESEARCH.md 21.10節の報告値との一致確認")
        a("")
        lo, hi = REPORTED_UNEXPECTED_RANGE
        a(f"報告値: 想定外4地点 = {lo:.2f}〜{hi:.2f}m（全て 2m 未満） / "
          f"正常10地点 = 全て {REPORTED_NORMAL_MIN:.2f}m 以上")
        a("")
        a("| 確認項目 | 報告値 | 今回の実測 | 一致 |")
        a("|---|---|---|---|")

        unexp = stats.get("想定外", [])
        normal = stats.get("B群", []) + stats.get("C群", []) + stats.get("残存A群", [])

        def verdict(ok: bool | None) -> str:
            if ok is None:
                return "判定不可（データ不足）"
            return "✅ 一致" if ok else "❌ **不一致**"

        ok_unexp_max = (max(unexp) < 2.0) if unexp else None
        ok_normal_min = (min(normal) >= REPORTED_NORMAL_MIN) if normal else None
        ok_gap = (max(unexp) < min(normal)) if (unexp and normal) else None

        a(f"| 想定外群の最大マージン | < 2.00m | {fmt(max(unexp) if unexp else None)}m "
          f"| {verdict(ok_unexp_max)} |")
        a(f"| 想定外群の範囲 | {lo:.2f}〜{hi:.2f}m "
          f"| {fmt(min(unexp) if unexp else None)}〜{fmt(max(unexp) if unexp else None)}m "
          f"| （参考値。別実装のため厳密一致は期待しない） |")
        a(f"| 正常群の最小マージン | >= {REPORTED_NORMAL_MIN:.2f}m "
          f"| {fmt(min(normal) if normal else None)}m | {verdict(ok_normal_min)} |")
        a(f"| 両群の断絶 | あり | 想定外最大 {fmt(max(unexp) if unexp else None)}m "
          f"< 正常最小 {fmt(min(normal) if normal else None)}m | {verdict(ok_gap)} |")
        a("")
        a(f"閾値 `MATCH_AMBIGUOUS_MARGIN_M = {MATCH_AMBIGUOUS_MARGIN_M}` "
          "がこの断絶の内側に収まっていれば、想定外群のみが `match_ambiguous=True` になる。")
        a("")

        expected_counts = {"想定外": 4, "B群": 4, "C群": 3, "残存A群": 3}
        found_counts = {g: sum(1 for r in matched if r["verify_group"] == g)
                        for g in VERIFY_GROUPS}
        if found_counts != expected_counts:
            a("⚠ **同定された地点数が期待と異なる。**")
            a("")
            a("| 群 | 期待 | 同定 |")
            a("|---|---:|---:|")
            for g in VERIFY_GROUPS:
                mark = "" if found_counts[g] == expected_counts[g] else " ⚠"
                a(f"| {g} | {expected_counts[g]} | {found_counts[g]}{mark} |")
            a("")
            a("同一 way_id 上で複数の判定点がマッチした場合や、サンプリング位置の差で")
            a("該当点が拾えなかった場合に起こりうる。上の明細表で実際にどの点が拾われたかを確認すること。")
            a("")

    # ---- B ----------------------------------------------------------------
    a("## B. 15ペア全体での曖昧地点の割合")
    a("")
    total = len(point_rows)
    amb_total = sum(1 for r in point_rows if r["match_ambiguous"])
    single = sum(1 for r in point_rows if r["match_margin_m"] is None and r["candidate_count"] >= 1)
    nocand = sum(1 for r in point_rows if r["candidate_count"] == 0)
    a(f"**全判定点 {total} 点のうち `match_ambiguous=True` は "
      f"{amb_total} 点（{amb_total / total * 100:.1f}%）。**")
    a("")
    a("論文で「N件について評価した。M件はマッチング曖昧のため判定不能」と報告する際、")
    a(f"この {amb_total}/{total} が母数になる。")
    a("")
    a("| 内訳 | 件数 | 割合 |")
    a("|---|---:|---:|")
    a(f"| 全判定点 | {total} | 100.0% |")
    a(f"| `match_ambiguous=True`（マージン < {MATCH_AMBIGUOUS_MARGIN_M}m） | {amb_total} | {amb_total / total * 100:.1f}% |")
    a(f"| 候補が1本のみ（margin=None・曖昧扱いしない） | {single} | {single / total * 100:.1f}% |")
    a(f"| 候補0本（マッチ不成立） | {nocand} | {nocand / total * 100:.1f}% |")
    a("")
    if skipped:
        a(f"⚠ スキップしたペア: {', '.join(skipped)}")
        a("")

    a("### ルート別内訳")
    a("")
    a("| label | 判定点 | 曖昧 | 割合 | 候補1本 | 候補0本 | oneway違反 |")
    a("|---|---:|---:|---:|---:|---:|---:|")
    for lbl in labels_run:
        rs = [r for r in point_rows if r["label"] == lbl]
        if not rs:
            continue
        n = len(rs)
        na = sum(1 for r in rs if r["match_ambiguous"])
        a(f"| {lbl} | {n} | {na} | {na / n * 100:.1f}% "
          f"| {sum(1 for r in rs if r['match_margin_m'] is None and r['candidate_count'] >= 1)} "
          f"| {sum(1 for r in rs if r['candidate_count'] == 0)} "
          f"| {sum(1 for r in rs if r['oneway_violation'])} |")
    a(f"| **合計** | **{total}** | **{amb_total}** | **{amb_total / total * 100:.1f}%** "
      f"| **{single}** | **{nocand}** | "
      f"**{sum(1 for r in point_rows if r['oneway_violation'])}** |")
    a("")

    # ---- C ----------------------------------------------------------------
    a("## C. 自システムへの影響（od_pairs.csv 15ペア・analyze_route v3）")
    a("")
    if analyze_rows is None:
        a("**未実行**（`--skip-analyze-route` 指定、または GraphHopper 未起動）。")
        a("")
        a("GraphHopper（`http://localhost:8989`）を起動したうえで")
        a("`python3 scripts/verify_match_margin.py` を実行すると本節が埋まる。")
        a("")
    else:
        errors = [r for r in analyze_rows if r.get("error")]
        ok_rows = [r for r in analyze_rows if not r.get("error")]
        changed = [
            r for r in ok_rows
            if (r["distance_delta_m"] not in (None, 0.0))
            or (str(r["new_violation_count"]) != str(r["csv_system_violation_count"]))
        ]
        if errors:
            a(f"⚠ {len(errors)} ペアで実行に失敗した: "
              + "、".join(f"{r['label']}（{r['error']}）" for r in errors))
            a("")
        if not changed and ok_rows:
            a(f"✅ **{len(ok_rows)} ペア全てで距離・違反数が `google_comparison.csv` の値と一致した。**")
            a("①はキー追加のみであり、v3 は `osm_way_id` details が取れる限り")
            a("`get_way_tags_by_ids` を使うため、`get_bulk_way_data` の変更の影響を受けない。")
        elif changed:
            a(f"⚠ **{len(changed)} ペアで差分が出た。** 下表の `using_edge_ids` を確認すること。")
            a("`using_edge_ids=False` なら Overpass の by-ID 取得が失敗して")
            a("`get_bulk_way_data` のフォールバック経路（`route_analyzer.py:166`）に落ちており、")
            a("垂線距離マッチングの復元が効いた可能性がある。`True` のまま差分が出た場合は")
            a("①とは無関係の要因（GraphHopper のグラフ更新等）を疑うこと。")
        a("")
        a("| label | 距離(新) | 距離(CSV) | 差 | 時間(新) | 時間(CSV) | 差 | "
          "違反(新) | 違反(CSV) | high_conf(新) | high_conf(CSV) | edge_id | リルート |")
        a("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
        for r in analyze_rows:
            if r.get("error"):
                a(f"| {r['label']} | — | — | — | — | — | — | — | — | — | — | — | ⚠ {r['error']} |")
                continue
            a(
                f"| {r['label']} | {fmt(r['new_distance_m'], 1)} | {fmt(r['csv_system_distance_m'])} "
                f"| {fmt(r['distance_delta_m'], 1)} "
                f"| {fmt(r['new_time_s'], 1)} | {fmt(r['csv_system_time_s'])} "
                f"| {fmt(r['time_delta_s'], 1)} "
                f"| {r['new_violation_count']} | {fmt(r['csv_system_violation_count'])} "
                f"| {r['new_violation_count_high_conf']} "
                f"| {fmt(r['csv_system_violation_count_high_conf'])} "
                f"| {fmt(r['using_edge_ids'])} | {fmt(r['rerouted'])} |"
            )
        a("")
        a("`high_conf` は `confidence >= 0.7` の件数（`routers/experiment.py:67` と同じ定義）。")
        a("")

    # ---- D ----------------------------------------------------------------
    a("## D. キー追加の非破壊性")
    a("")
    a("`get_bulk_way_data` の呼び出し側は以下の4箇所で、いずれも `tags` と `geometry` しか読まない。")
    a("")
    a("| 箇所 | 読むキー |")
    a("|---|---|")
    a("| `services/external_route_scorer.py:175` | `tags`, `geometry` |")
    a("| `services/external_route_scorer.py:202` | `tags` |")
    a("| `services/route_analyzer.py:166` | `tags`, `geometry` |")
    a("| `services/overpass.py`（`get_bulk_way_tags`） | `tags` |")
    a("")
    a("実行時の確認は `python3 scripts/smoke_test_match_keys.py`（Overpass 不要）。")
    a("既存キーの形式不変・新キーのセマンティクス・way id 重複除去・")
    a("point-to-curve 選択・呼び出し側の完走・垂線距離の独立検証を含む。")
    a("")

    a("---")
    a("")
    a(f"判定点ごとの明細は `{OUT_POINTS_CSV.name}`、")
    a(f"analyze_route の結果は `{OUT_ANALYZE_CSV.name}` を参照。")
    a("既存CSV（`google_comparison.csv` 等）は一切書き換えていない。")
    a("")
    return "\n".join(L)


POINT_CSV_FIELDS = [
    "label", "sample_idx", "route_idx", "lat", "lng", "candidate_count",
    "node_rank1_way_id", "perp_rank1_way_id", "perp_rank1_highway",
    "perp_rank1_oneway", "perp_rank1_name",
    "rank2_way_id", "rank2_highway", "rank2_dist_m",
    "match_dist_m", "match_margin_m", "match_ambiguous",
    "verify_group", "known_group_node_rank1", "known_group_perp_rank1",
    "oneway_violation", "oneway_confidence", "oneway_misaligned",
    "diag_angle_deg",
]

ANALYZE_CSV_FIELDS = [
    "label", "road_type",
    "new_distance_m", "csv_system_distance_m", "distance_delta_m",
    "new_time_s", "csv_system_time_s", "time_delta_s",
    "new_violation_count", "csv_system_violation_count",
    "new_violation_count_high_conf", "csv_system_violation_count_high_conf",
    "new_original_distance_m", "new_distance_diff_m",
    "rerouted", "using_edge_ids", "violation_types", "error",
]


async def main(args) -> int:
    point_rows: list[dict] = []
    labels_run: list[str] = []
    skipped: list[str] = []

    if not args.only_analyze_route:
        print(f"[A・B] 入力: {INPUT_CSV}\n")
        point_rows, labels_run, skipped = await collect_points(args.label)
        if not point_rows:
            print("\n⚠ 判定点が得られませんでした。中止します。")
            return 1
        with open(OUT_POINTS_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=POINT_CSV_FIELDS)
            w.writeheader()
            w.writerows(point_rows)
        print(f"\n判定点の明細を書き出しました → {OUT_POINTS_CSV}")

    analyze_rows = None
    if not args.skip_analyze_route:
        print(f"\n[C] analyze_route(v3) を実行します（GraphHopper が必要）\n")
        analyze_rows = await run_analyze_route()
        with open(OUT_ANALYZE_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ANALYZE_CSV_FIELDS)
            w.writeheader()
            for r in analyze_rows:
                w.writerow({k: r.get(k, "") for k in ANALYZE_CSV_FIELDS})
        print(f"\nanalyze_route の結果を書き出しました → {OUT_ANALYZE_CSV}")

    if args.only_analyze_route and OUT_POINTS_CSV.exists():
        # C だけ再実行した場合、A・B は前回の明細から復元して報告書を作り直す
        with open(OUT_POINTS_CSV, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                r["match_margin_m"] = float(r["match_margin_m"]) if r["match_margin_m"] else None
                r["match_dist_m"] = float(r["match_dist_m"]) if r["match_dist_m"] else None
                r["candidate_count"] = int(r["candidate_count"] or 0)
                r["sample_idx"] = int(r["sample_idx"] or 0)
                r["match_ambiguous"] = r["match_ambiguous"] == "True"
                r["oneway_violation"] = r["oneway_violation"] == "True"
                r["node_rank1_way_id"] = r["node_rank1_way_id"] or None
                r["perp_rank1_way_id"] = r["perp_rank1_way_id"] or None
                r["rank2_way_id"] = r["rank2_way_id"] or None
                point_rows.append(r)
        labels_run = list(dict.fromkeys(r["label"] for r in point_rows))
        print(f"A・B は前回の {OUT_POINTS_CSV.name} から復元しました（{len(point_rows)} 点）")

    if not point_rows:
        print("\n⚠ A・B のデータがないため報告書を生成できません。"
              "--only-analyze-route を外して実行してください。")
        return 1

    OUT_MD.write_text(
        build_markdown(point_rows, labels_run, skipped, analyze_rows), encoding="utf-8")
    print(f"報告書を書き出しました → {OUT_MD}")
    print("\n既存CSV（google_comparison.csv 等）には一切書き込んでいません。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="① match_margin_m / match_ambiguous 実装の検証（A・B・C）")
    parser.add_argument("--label", action="append", default=None,
                        help="A・B の対象 label（繰り返し指定可）。未指定なら全15ペア")
    parser.add_argument("--skip-analyze-route", action="store_true",
                        help="C（analyze_route）をスキップする。GraphHopper 不要")
    parser.add_argument("--only-analyze-route", action="store_true",
                        help="C のみ実行し、A・B は前回の明細CSVから復元する")
    args = parser.parse_args()

    if args.skip_analyze_route and args.only_analyze_route:
        parser.error("--skip-analyze-route と --only-analyze-route は同時に指定できません")

    sys.exit(asyncio.run(main(args)))
