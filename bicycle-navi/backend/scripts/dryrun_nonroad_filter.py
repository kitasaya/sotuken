"""② 非車道除外フィルタのドライラン分析（実装はしない・判断材料の収集のみ）。

`highway` が footway / path / steps / pedestrian / corridor / platform の候補を
最近傍探索から除外した場合に何が起きるかを、**コードには入れずにシミュレート**して
集計する。除外の例外は `bicycle=yes` / `bicycle=designated`。除外の結果候補が
0本になった場合は除外前の候補にフォールバックする。

前提：`services/overpass.py` は①（垂線距離マッチング＋マージン記録）のみ適用済み。
非車道除外フィルタは **このスクリプトの中だけ** に存在する。

  python3 scripts/dryrun_nonroad_filter.py                 # 15ペア全件
  python3 scripts/dryrun_nonroad_filter.py --label 渋谷→新宿  # 部分実行

出力（いずれも新規ファイル。既存CSVは一切書き換えない）：
  data/dryrun_nonroad_filter.md          … 報告書
  data/dryrun_nonroad_filter_points.csv  … 判定点ごとの明細
"""

import argparse
import asyncio
import csv
import datetime
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.known_violations import (
    GROUP_EXPECTATION,
    GROUP_WAY_IDS,
    KNOWN_VIOLATIONS,
    group_of,
    note_of,
)
from scripts.route_match_probe import (
    Candidate,
    PointProbe,
    fmt,
    is_oneway_violation,
    probe_route,
)
from services.overpass import MATCH_AMBIGUOUS_MARGIN_M

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"
OUT_MD = DATA_DIR / "dryrun_nonroad_filter.md"
OUT_CSV = DATA_DIR / "dryrun_nonroad_filter_points.csv"

# ---------------------------------------------------------------------------
# 検討中のフィルタ（このスクリプト内のみ。services/ には入れない）
# ---------------------------------------------------------------------------
NONROAD_HIGHWAYS = {"footway", "path", "steps", "pedestrian", "corridor", "platform"}
BICYCLE_ALLOWED = {"yes", "designated"}

# Overpass 公開サーバーへの連続リクエスト間隔（秒）
REQUEST_INTERVAL_S = 3.0


def is_nonroad(cand: Candidate) -> bool:
    """highway が除外対象リストに該当するか（bicycle 例外は考慮しない）。"""
    return cand.highway in NONROAD_HIGHWAYS


def is_bicycle_exempt(cand: Candidate) -> bool:
    """bicycle=yes / designated が付いていて除外を免れるか。"""
    return cand.bicycle in BICYCLE_ALLOWED


def is_excluded(cand: Candidate) -> bool:
    """このフィルタで実際に除外されるか。"""
    return is_nonroad(cand) and not is_bicycle_exempt(cand)


def apply_filter(candidates: list[Candidate]) -> tuple[list[Candidate], bool]:
    """除外を適用した候補リストと、フォールバックが発生したかを返す。"""
    kept = [c for c in candidates if not is_excluded(c)]
    if not kept:
        return candidates, True   # 候補0本 → 除外前にフォールバック
    return kept, False


# ---------------------------------------------------------------------------
# 判定点1つぶんの分析結果
# ---------------------------------------------------------------------------

async def analyze_point(probe: PointProbe) -> dict:
    base = probe.rank1
    filtered_cands, fallback_used = apply_filter(probe.candidates)
    filt = filtered_cands[0] if filtered_cands else None
    switched = (base is not None and filt is not None and base.way_id != filt.way_id)

    base_detected, base_misaligned, base_conf = await is_oneway_violation(probe, base)
    filt_detected, filt_misaligned, filt_conf = await is_oneway_violation(probe, filt)

    return {
        "label": probe.label,
        "sample_idx": probe.sample_idx,
        "route_idx": probe.route_idx,
        "lat": round(probe.lat, 7),
        "lng": round(probe.lng, 7),
        "candidate_count": len(probe.candidates),

        # ノード距離ベース（垂線距離修正前）の選択 — 群の同定用
        "node_rank1_way_id": probe.node_rank1_way_id,
        "node_rank1_group": group_of(probe.node_rank1_way_id),

        # ベースライン（①のみ適用）
        "rank1_way_id": base.way_id if base else None,
        "rank1_highway": base.highway if base else "",
        "rank1_oneway": base.oneway if base else "",
        "rank1_name": base.name if base else "",
        "rank1_bicycle": base.bicycle if base else "",
        "rank1_dist_m": probe.match_dist_m,
        "rank2_way_id": probe.rank2.way_id if probe.rank2 else None,
        "rank2_highway": probe.rank2.highway if probe.rank2 else "",
        "rank2_oneway": probe.rank2.oneway if probe.rank2 else "",
        "rank2_dist_m": round(probe.rank2.dist_m, 3) if probe.rank2 else None,
        "match_margin_m": probe.match_margin_m,
        "match_ambiguous": probe.match_ambiguous,
        "rank1_group": group_of(base.way_id if base else None),

        # フィルタ判定
        "rank1_is_nonroad": is_nonroad(base) if base else False,
        "rank1_bicycle_exempt": (is_nonroad(base) and is_bicycle_exempt(base)) if base else False,
        "rank1_excluded": is_excluded(base) if base else False,

        # フィルタ適用後
        "filt_rank1_way_id": filt.way_id if filt else None,
        "filt_rank1_highway": filt.highway if filt else "",
        "filt_rank1_oneway": filt.oneway if filt else "",
        "filt_rank1_name": filt.name if filt else "",
        "filt_rank1_dist_m": round(filt.dist_m, 3) if filt else None,
        "filt_rank1_group": group_of(filt.way_id if filt else None),
        "filt_fallback_used": fallback_used,
        "switched": switched,

        # oneway 判定（フィルタ前 / 後）
        "oneway_violation_baseline": base_detected,
        "oneway_confidence_baseline": base_conf,
        "oneway_misaligned_baseline": base_misaligned,
        "oneway_violation_filtered": filt_detected,
        "oneway_confidence_filtered": filt_conf,
        "oneway_misaligned_filtered": filt_misaligned,
        "violation_changed": base_detected != filt_detected,

        # 診断専用（候補選択には一切使わない）
        "diag_angle_baseline_deg": probe.angle_to_travel_deg(base),
        "diag_angle_filtered_deg": probe.angle_to_travel_deg(filt),
    }


# ---------------------------------------------------------------------------
# 報告書の生成
# ---------------------------------------------------------------------------

def _group_status(rows: list[dict], way_id: int) -> dict:
    """指定 way_id が、ベースライン/フィルタ後にそれぞれ検出されているかを集計する。"""
    base_hits, filt_hits, related = [], [], []
    for r in rows:
        touches = way_id in (r["rank1_way_id"], r["filt_rank1_way_id"], r["node_rank1_way_id"])
        if not touches:
            continue
        related.append(r)
        if r["rank1_way_id"] == way_id and r["oneway_violation_baseline"]:
            base_hits.append(r)
        if r["filt_rank1_way_id"] == way_id and r["oneway_violation_filtered"]:
            filt_hits.append(r)
    return {
        "way_id": way_id,
        "base_hits": base_hits,
        "filt_hits": filt_hits,
        "related": related,
    }


def _verdict(group: str, base_n: int, filt_n: int) -> str:
    if group in ("A", "C"):
        if base_n == 0 and filt_n == 0:
            return "ベースラインで既に未検出（フィルタとは無関係）"
        if filt_n >= base_n:
            return "✅ 残存（期待どおり）"
        return "❌ **フィルタで消失**"
    if group == "B":
        if base_n == 0 and filt_n == 0:
            return "✅ 消滅したまま（期待どおり）"
        if filt_n < base_n:
            return "フィルタで追加的に消失"
        return "⚠ ベースラインで検出されている"
    return "参考"


def build_markdown(rows: list[dict], labels_run: list[str], skipped: list[str]) -> str:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(rows)

    nonroad_rows = [r for r in rows if r["rank1_is_nonroad"]]
    exempt_rows = [r for r in nonroad_rows if r["rank1_bicycle_exempt"]]
    excluded_rows = [r for r in rows if r["rank1_excluded"]]
    switched_rows = [r for r in rows if r["switched"]]
    fallback_rows = [r for r in rows if r["filt_fallback_used"]]
    changed_rows = [r for r in rows if r["violation_changed"]]
    ambiguous_rows = [r for r in rows if r["match_ambiguous"]]

    base_viol = [r for r in rows if r["oneway_violation_baseline"]]
    filt_viol = [r for r in rows if r["oneway_violation_filtered"]]

    # --- C群の安全性が最重要の結論 -------------------------------------------
    # 判定基準: C群（真の違反）が1点でもフィルタによって消えたら不採用。
    c_status = [_group_status(rows, w) for w in GROUP_WAY_IDS["C"]]
    c_base_total = sum(len(s["base_hits"]) for s in c_status)
    c_filt_total = sum(len(s["filt_hits"]) for s in c_status)
    c_lost = [s for s in c_status if len(s["filt_hits"]) < len(s["base_hits"])]

    partial_run = len(labels_run) < 15

    if c_lost:
        headline = (
            "❌ **不採用を推奨。** C群（真の違反）の検出がフィルタによって減少した: "
            + "、".join(
                f"way {s['way_id']}（{len(s['base_hits'])}点 → {len(s['filt_hits'])}点）"
                for s in c_lost
            )
            + "。依頼時に定義した判定基準に従い、非車道除外フィルタは採用すべきでない。"
        )
    elif partial_run:
        headline = (
            f"⚠ **部分実行のため採否は判断できない。** {len(labels_run)}/15 ペアのみを分析した。"
            "C群を含むペア（横浜→みなとみらい・立川→国分寺）を含む全15ペアで再実行すること。"
        )
    elif c_base_total == 0:
        headline = (
            "⚠ **前提が崩れているため採否を判断できない。** C群（真の違反 3点）が"
            "**ベースラインの時点で1点も検出されていない**。フィルタ以前の問題として、"
            "①適用後の採点が期待どおりに動いているか（RESEARCH.md 21.9節では"
            "「C群3点は無影響」と記録されている）を先に確認する必要がある。"
        )
    elif not excluded_rows:
        headline = (
            f"⚪ **フィルタは一度も作動しない。** 全 {total} 判定点のうち、rank1 の "
            "`highway` が除外対象リストに該当した点は 0 件だった。"
            "採用しても不採用にしても15ペアの採点結果は変わらないため、"
            "実装する積極的な理由がない。"
        )
    elif not changed_rows:
        headline = (
            f"⚪ **無害だが効果もない。** {len(excluded_rows)} 点で除外が作動したが、"
            "oneway 判定が変化した点は 0 件。"
            f"C群 {c_base_total} 点は全て保持されている（→ 不採用条件には該当しない）が、"
            "検出結果が1件も変わらないため実装の効果は確認できない。"
        )
    else:
        headline = (
            f"✅ **C群は保持されている（{c_base_total}点 → {c_filt_total}点）。** "
            f"除外が作動したのは {len(excluded_rows)} 点、うち oneway 判定が変化したのは "
            f"{len(changed_rows)} 点。不採用条件には該当しないため、"
            "下記の群別表（3節）と判定変化の一覧（4節）を確認のうえ採否を判断すること。"
        )

    L: list[str] = []
    a = L.append

    a("# ② 非車道除外フィルタ ドライラン分析")
    a("")
    a(f"生成日時: {ts}  ")
    a(f"生成スクリプト: `backend/scripts/dryrun_nonroad_filter.py`  ")
    a(f"対象: `data/google_routes_input.csv` の {len(labels_run)} ペア / 判定点 {total} 点")
    if skipped:
        a(f"  ⚠ polyline が空でスキップ: {', '.join(skipped)}")
    a("")
    a("**本ドライランではコードを変更していない。** 除外フィルタは")
    a("`scripts/dryrun_nonroad_filter.py` の中だけでシミュレートしており、")
    a("`services/overpass.py` には①（垂線距離マッチング＋マージン記録）しか入っていない。")
    a("")

    a("## 結論")
    a("")
    a(headline)
    a("")
    a("判定基準（依頼時に定義）: **C群2件（3点）のいずれかが消えた場合、"
      "非車道除外フィルタは不採用。**")
    a("")

    # --- 検討中のフィルタ仕様 -------------------------------------------------
    a("## 検討中のフィルタ仕様")
    a("")
    a(f"- 除外対象 `highway`: {' / '.join(f'`{h}`' for h in sorted(NONROAD_HIGHWAYS))}")
    a(f"- 除外の例外: `bicycle` が {' または '.join(f'`{b}`' for b in sorted(BICYCLE_ALLOWED))}"
      "（自転車が合法的に走行できるため）")
    a("- 除外の結果候補が0本になった場合は、除外前の候補にフォールバックする")
    a("")
    a("**方向情報（travel_vector との角度）は候補の選択に使っていない。** "
      "測定対象が「進行方向が逆か」であるため、方向で候補を選ぶと循環論法になる。"
      "角度は診断列 `diag_angle_*_deg` に記録するだけに留めている。")
    a("")

    # --- 1. 集計 -------------------------------------------------------------
    a("## 1. 集計")
    a("")
    a("| 項目 | 件数 | 全判定点に対する割合 |")
    a("|---|---:|---:|")

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}%" if total else "—"

    a(f"| 全判定点 | {total} | 100.0% |")
    a(f"| rank1 の `highway` が除外対象リストに該当 | {len(nonroad_rows)} | {pct(len(nonroad_rows))} |")
    a(f"| └ うち `bicycle=yes/designated` で除外されない | {len(exempt_rows)} | {pct(len(exempt_rows))} |")
    a(f"| └ **実際に除外された** | {len(excluded_rows)} | {pct(len(excluded_rows))} |")
    a(f"| 　└ うち rank2 以下へ切り替わった | {len(switched_rows)} | {pct(len(switched_rows))} |")
    a(f"| 　└ うち候補0本でフォールバック | {len(fallback_rows)} | {pct(len(fallback_rows))} |")
    a(f"| oneway 判定がフィルタ前後で変化 | {len(changed_rows)} | {pct(len(changed_rows))} |")
    a(f"| （参考）`match_ambiguous=True`（マージン<{MATCH_AMBIGUOUS_MARGIN_M}m） | {len(ambiguous_rows)} | {pct(len(ambiguous_rows))} |")
    a("")
    a(f"oneway 違反の検出数: ベースライン **{len(base_viol)} 点** → フィルタ後 **{len(filt_viol)} 点**")
    a("")

    if nonroad_rows:
        a("### 除外対象 highway の内訳（rank1）")
        a("")
        a("| highway | 該当点数 | うち bicycle 例外 | 実際に除外 |")
        a("|---|---:|---:|---:|")
        counter = Counter(r["rank1_highway"] for r in nonroad_rows)
        for hw, n in sorted(counter.items(), key=lambda kv: -kv[1]):
            ex = sum(1 for r in exempt_rows if r["rank1_highway"] == hw)
            exc = sum(1 for r in excluded_rows if r["rank1_highway"] == hw)
            a(f"| `{hw}` | {n} | {ex} | {exc} |")
        a("")
    else:
        a("### 除外対象 highway の内訳（rank1）")
        a("")
        a("該当なし。15ペアの全判定点で rank1 の `highway` は除外対象リストに含まれなかった。")
        a("")

    # --- 2. 切り替わり先 -----------------------------------------------------
    a("## 2. 除外された点の切り替わり先")
    a("")
    if not excluded_rows:
        a("除外が作動した判定点が無いため該当なし。")
        a("")
    else:
        a("| label | idx | 除外された rank1 | highway | 切り替わり先 | highway | oneway | 距離 | マージン | 判定変化 |")
        a("|---|---:|---:|---|---:|---|---|---:|---:|---|")
        for r in excluded_rows:
            change = "なし"
            if r["violation_changed"]:
                change = ("**検出→消滅**" if r["oneway_violation_baseline"]
                          else "**新規出現**")
            fb = "（フォールバック）" if r["filt_fallback_used"] else ""
            a(
                f"| {r['label']} | {r['sample_idx']} | {fmt(r['rank1_way_id'])} | "
                f"`{r['rank1_highway']}` | {fmt(r['filt_rank1_way_id'])}{fb} | "
                f"`{r['filt_rank1_highway']}` | {fmt(r['filt_rank1_oneway'])} | "
                f"{fmt(r['filt_rank1_dist_m'])}m | {fmt(r['match_margin_m'])}m | {change} |"
            )
        a("")

    # --- 3. 18件の既知違反への影響 ------------------------------------------
    a("## 3. 18件の既知違反への影響")
    a("")
    a("既知18件は**垂線距離修正前（ノード距離ベース）**の採点で検出されたものである"
      "（RESEARCH.md 29節）。ベースライン列は①適用後の状態なので、B群と A群の一部は"
      "この時点で既に消えている（RESEARCH.md 21.9節）。")
    a("")
    a("| 群 | way_id | label | 期待 | ベースライン<br>検出点数 | フィルタ後<br>検出点数 | 判定 |")
    a("|---|---:|---|---|---:|---:|---|")
    for group in ("A", "C", "B", "D"):
        for way_id in GROUP_WAY_IDS[group]:
            st = _group_status(rows, way_id)
            bn, fn = len(st["base_hits"]), len(st["filt_hits"])
            _, lbl, _, _, _ = KNOWN_VIOLATIONS[way_id]
            a(f"| {group} | {way_id} | {lbl} | {GROUP_EXPECTATION[group].split('（')[0].split('。')[0]} "
              f"| {bn} | {fn} | {_verdict(group, bn, fn)} |")
    a("")

    a("### 群ごとの期待")
    a("")
    for group in ("A", "B", "C", "D"):
        a(f"- **{group}群**: {GROUP_EXPECTATION[group]}")
    a("")

    # 既知 way が rank1/rank2/ノード距離rank1 のいずれかに現れた判定点の明細
    related_rows = [
        r for r in rows
        if r["rank1_group"] or r["filt_rank1_group"] or r["node_rank1_group"]
    ]
    if related_rows:
        a("### 既知18件の way に触れた判定点の明細")
        a("")
        a("`node_rank1` は垂線距離修正**前**（ノード距離）に選ばれていた way。"
          "想定外4地点の同定に必要なため併記する。")
        a("")
        a("| label | idx | node_rank1<br>(群) | rank1<br>(群) | rank1 highway | oneway | "
          "距離 | マージン | 曖昧 | 除外 | フィルタ後 rank1<br>(群) | ベース検出 | フィルタ後検出 |")
        a("|---|---:|---|---|---|---|---:|---:|---|---|---|---|---|")
        for r in related_rows:
            def tag(wid, grp):
                return f"{fmt(wid)}{f' ({grp})' if grp else ''}"
            a(
                f"| {r['label']} | {r['sample_idx']} "
                f"| {tag(r['node_rank1_way_id'], r['node_rank1_group'])} "
                f"| {tag(r['rank1_way_id'], r['rank1_group'])} "
                f"| `{r['rank1_highway']}` | {fmt(r['rank1_oneway'])} "
                f"| {fmt(r['rank1_dist_m'])}m | {fmt(r['match_margin_m'])}m "
                f"| {fmt(r['match_ambiguous'])} | {fmt(r['rank1_excluded'])} "
                f"| {tag(r['filt_rank1_way_id'], r['filt_rank1_group'])} "
                f"| {fmt(r['oneway_violation_baseline'])} | {fmt(r['oneway_violation_filtered'])} |"
            )
        a("")

    # --- 4. 判定が変化した点 -------------------------------------------------
    a("## 4. oneway 判定が変化した判定点")
    a("")
    if not changed_rows:
        a("フィルタ前後で oneway 判定が変化した判定点は **0件**。")
        a("")
    else:
        a("| label | idx | 座標 | 変化 | ベース rank1 | フィルタ後 rank1 | マージン | 備考 |")
        a("|---|---:|---|---|---|---|---:|---|")
        for r in changed_rows:
            direction = ("検出 → 消滅" if r["oneway_violation_baseline"] else "未検出 → 新規出現")
            base_note = note_of(r["rank1_way_id"]) or note_of(r["filt_rank1_way_id"])
            a(
                f"| {r['label']} | {r['sample_idx']} | {r['lat']:.5f}, {r['lng']:.5f} "
                f"| **{direction}** "
                f"| {fmt(r['rank1_way_id'])} `{r['rank1_highway']}` oneway={fmt(r['rank1_oneway'])} "
                f"| {fmt(r['filt_rank1_way_id'])} `{r['filt_rank1_highway']}` oneway={fmt(r['filt_rank1_oneway'])} "
                f"| {fmt(r['match_margin_m'])}m | {base_note} |"
            )
        a("")

    # --- 5. ルート別サマリ ---------------------------------------------------
    a("## 5. ルート別サマリ")
    a("")
    a("| label | 判定点 | 除外対象 highway | 実除外 | 曖昧 | ベース違反 | フィルタ後違反 |")
    a("|---|---:|---:|---:|---:|---:|---:|")
    for lbl in labels_run:
        rs = [r for r in rows if r["label"] == lbl]
        if not rs:
            continue
        a(
            f"| {lbl} | {len(rs)} "
            f"| {sum(1 for r in rs if r['rank1_is_nonroad'])} "
            f"| {sum(1 for r in rs if r['rank1_excluded'])} "
            f"| {sum(1 for r in rs if r['match_ambiguous'])} "
            f"| {sum(1 for r in rs if r['oneway_violation_baseline'])} "
            f"| {sum(1 for r in rs if r['oneway_violation_filtered'])} |"
        )
    a("")

    a("---")
    a("")
    a(f"判定点ごとの全項目は `{OUT_CSV.name}` を参照。")
    a("")
    a("**②の実装可否はこの報告書を確認したうえで判断すること。"
      "本スクリプトは分析のみで、`services/` には一切変更を加えていない。**")
    a("")
    return "\n".join(L)


# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "label", "sample_idx", "route_idx", "lat", "lng", "candidate_count",
    "node_rank1_way_id", "node_rank1_group",
    "rank1_way_id", "rank1_group", "rank1_highway", "rank1_oneway", "rank1_name",
    "rank1_bicycle", "rank1_dist_m",
    "rank2_way_id", "rank2_highway", "rank2_oneway", "rank2_dist_m",
    "match_margin_m", "match_ambiguous",
    "rank1_is_nonroad", "rank1_bicycle_exempt", "rank1_excluded",
    "filt_rank1_way_id", "filt_rank1_group", "filt_rank1_highway", "filt_rank1_oneway",
    "filt_rank1_name", "filt_rank1_dist_m", "filt_fallback_used", "switched",
    "oneway_violation_baseline", "oneway_confidence_baseline", "oneway_misaligned_baseline",
    "oneway_violation_filtered", "oneway_confidence_filtered", "oneway_misaligned_filtered",
    "violation_changed",
    "diag_angle_baseline_deg", "diag_angle_filtered_deg",
]


async def main(labels: list[str] | None, radius: int) -> int:
    with open(INPUT_CSV, encoding="utf-8", newline="") as f:
        input_rows = list(csv.DictReader(f))

    if labels:
        available = {r["label"] for r in input_rows}
        missing = set(labels) - available
        if missing:
            print(f"⚠ 入力ファイルに存在しない label: {sorted(missing)}")
            return 1
        input_rows = [r for r in input_rows if r["label"] in labels]

    print(f"入力: {INPUT_CSV}")
    print(f"対象: {len(input_rows)} ペア（半径 {radius}m・サンプル間隔 40m）\n")

    all_rows: list[dict] = []
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

        print(f"  分析中: {label}", flush=True)
        probes, _ = await probe_route(label, polyline, radius=radius)
        if not probes:
            print(f"    ⚠ 判定点が得られませんでした")
            skipped.append(label)
            continue
        if all(not p.candidates for p in probes):
            print(f"    ⚠ 全判定点で候補0本。Overpass 障害の可能性があります。中止します。")
            return 1

        route_rows = [await analyze_point(p) for p in probes]
        all_rows.extend(route_rows)
        labels_run.append(label)

        nonroad = sum(1 for r in route_rows if r["rank1_is_nonroad"])
        excluded = sum(1 for r in route_rows if r["rank1_excluded"])
        changed = sum(1 for r in route_rows if r["violation_changed"])
        bv = sum(1 for r in route_rows if r["oneway_violation_baseline"])
        fv = sum(1 for r in route_rows if r["oneway_violation_filtered"])
        print(f"    → 判定点={len(route_rows)} 非車道rank1={nonroad} 実除外={excluded} "
              f"判定変化={changed} 違反 {bv}→{fv}")

    if not all_rows:
        print("\n⚠ 分析できた判定点がありません。出力を作成せず終了します。")
        return 1

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\n明細を書き出しました → {OUT_CSV}")

    OUT_MD.write_text(build_markdown(all_rows, labels_run, skipped), encoding="utf-8")
    print(f"報告書を書き出しました → {OUT_MD}")
    print("\n既存CSV（google_comparison.csv 等）には一切書き込んでいません。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="② 非車道除外フィルタのドライラン分析（実装はしない）")
    parser.add_argument("--label", action="append", default=None,
                        help="分析対象の label（繰り返し指定可）。未指定なら全15ペア")
    parser.add_argument("--radius", type=int, default=20,
                        help="Overpass の探索半径（既定 20m・採点器と同じ）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.label, args.radius)))
