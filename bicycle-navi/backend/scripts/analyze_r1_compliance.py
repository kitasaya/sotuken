"""R1（本システムのルート）に残る一方通行違反の集計。

目的
----
「Google Maps のルートには違反が存在し、本システムはそれを回避できる」という
主張の後半を裏づけるため、本システムが最終的に利用者へ提示するルート（R1）に
一方通行違反が残っていないかを、既存の実行結果CSVから集計する。

前提（コードを読んで確認した事実。判定ロジックは一切変更していない）
------------------------------------------------------------------
`services/route_analyzer.py` の `analyze_route(v3)` は、法規判定を
**初期ルート（リルート前）** に対してのみ実行する。リルート後の準拠ルートを
再判定するコードは存在しない（`_build_response` 内に `check_*` の呼び出しがない）。
したがって `google_comparison.csv` の `system_violation_count` は
**リルート前**の違反数である（`backend/data/investigation_r1_material.md` §1.2・§3.1）。

ただし `_build_response` は、oneway 違反が1件もないペアでは
`compliant_route = original_route`（同一オブジェクト）を返す
（`route_analyzer.py:275-276`）。よって **rerouted=False のペアでは
判定対象の初期ルートと提示されるR1が同一経路**であり、既存の判定結果が
そのままR1の判定結果になる。

本スクリプトはこの区別に従って15ペアを「測定済み」と「未測定」に分け、
測定済みペアの一方通行違反数を集計する。再判定は行わない（GraphHopper と
8/1固定Overpass attic の再実行が必要なため、本スクリプト単体では不能）。

使い方
------
    python analyze_r1_compliance.py

出典データ
----------
- `backend/data/verify_v2_analyze_route.csv`（rerouted / violation_types / 距離）
- `backend/data/ground_truth_template.csv`（検出違反の rule 別明細）
"""

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
ROUTE_CSV = DATA / "verify_v2_analyze_route.csv"
GT_CSV = DATA / "ground_truth_template.csv"


def load_routes():
    with ROUTE_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_oneway_detections():
    """ground_truth_template.csv から detected_rule=='oneway' の行をラベル別に数える。"""
    counts = {}
    with GT_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["detected_rule"] == "oneway":
                counts[row["label"]] = counts.get(row["label"], 0) + 1
    return counts


def main():
    rows = load_routes()
    oneway = load_oneway_detections()

    measured, unmeasured = [], []
    for r in rows:
        label = r["label"]
        rerouted = r["rerouted"] == "True"
        n_oneway = oneway.get(label, 0)
        (unmeasured if rerouted else measured).append((label, n_oneway, r))

    print("=== R1（提示ルート）の一方通行違反：測定済みペア ===")
    print("  rerouted=False のためR1＝判定対象の初期ルート（同一オブジェクト）")
    total = 0
    for label, n, r in measured:
        total += n
        print(f"  {label:<24} oneway={n}  距離={r['new_distance_m']}m  距離差={r['new_distance_diff_m']}m")
    print(f"  -- 測定済み {len(measured)}/15ペア、一方通行違反 合計 {total} 件")

    print()
    print("=== R1（提示ルート）の一方通行違反：未測定ペア ===")
    print("  rerouted=True のためR1＝リルート後の準拠ルート。再判定されていない")
    for label, n, r in unmeasured:
        print(f"  {label:<24} 初期ルートのoneway={n}（リルートの原因）")
        print(f"    初期={r['new_original_distance_m']}m → 準拠={r['new_distance_m']}m "
              f"（+{r['new_distance_diff_m']}m）")
        print("    準拠ルートの違反数：未測定")

    print()
    print("=== 距離コスト ===")
    zero = [r for _, _, r in measured + unmeasured if float(r["new_distance_diff_m"]) == 0.0]
    nonzero = [r for _, _, r in measured + unmeasured if float(r["new_distance_diff_m"]) != 0.0]
    print(f"  距離差 ±0m のペア: {len(zero)}/15")
    for r in nonzero:
        orig = float(r["new_original_distance_m"])
        diff = float(r["new_distance_diff_m"])
        print(f"  距離増あり: {r['label']} +{diff}m（+{diff / orig * 100:.1f}%、分母={orig}m）")
    print("  ※ この増加率は当該1ペアの値であり、15ペアの平均ではない。")


if __name__ == "__main__":
    main()
