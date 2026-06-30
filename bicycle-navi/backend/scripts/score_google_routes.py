"""Google ルートの polyline を一括採点し、結果を google_comparison.csv へ流し込む。

## 使い方

  # ステップ1：採点結果を確認（CSV には書き込まない）
  python3 scripts/score_google_routes.py --dry-run

  # ステップ2：確認後に google_comparison.csv へ流し込む
  python3 scripts/score_google_routes.py --write

## 入力

  backend/data/google_routes_input.csv
  列：label, polyline（Google encoded polyline。常にダブルクォートで囲むこと）

## 出力（--dry-run）

  backend/data/score_google_routes_result.csv（一時確認用）

## 出力（--write）

  backend/data/google_comparison.csv の google_oneway_violation_count /
  google_two_step_violation_count / google_total_violation_count 列のみを更新。
  手入力列（距離・時間・route_overlap_pct など）は一切上書きしない。
  書き込み前に google_comparison.csv のバックアップを自動作成。
"""

import argparse
import asyncio
import csv
import datetime
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.external_route_scorer import score_external_route, decode_polyline

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"
COMPARISON_CSV = DATA_DIR / "google_comparison.csv"
RESULT_CSV = DATA_DIR / "score_google_routes_result.csv"

# リグレッション確認：渋谷→新宿は oneway=1 が期待値
REGRESSION_LABEL = "渋谷→新宿"
REGRESSION_EXPECTED_ONEWAY = 1

# google_comparison.csv で採点器が埋める列（それ以外は触らない）
SCORED_COLUMNS = {
    "google_oneway_violation_count",
    "google_two_step_violation_count",
    "google_total_violation_count",
    "scorer_sampled_points",
    "scorer_route_distance_m",
    "scored_at",
}


async def score_all(input_rows: list[dict]) -> list[dict]:
    results = []
    for row in input_rows:
        label = row["label"]
        polyline_str = row.get("polyline", "").strip()
        if not polyline_str:
            print(f"  [SKIP] {label}: polyline が空")
            continue

        coords = decode_polyline(polyline_str)
        print(f"  採点中: {label} ({len(coords)} 座標点)", flush=True)
        score = await score_external_route(coords, sample_interval_m=40.0)

        results.append({
            "label": label,
            "oneway_violation_count": score["oneway_violation_count"],
            "two_step_violation_count": score["two_step_violation_count"],
            "total_violation_count": score["total_violation_count"],
            "route_distance_m": score["route_distance_m"],
            "sampled_points": score["sampled_points"],
        })
        print(
            f"    → oneway={score['oneway_violation_count']} "
            f"two_step={score['two_step_violation_count']} "
            f"total={score['total_violation_count']} "
            f"dist={score['route_distance_m']}m"
        )
    return results


def check_regression(results: list[dict]) -> bool:
    for r in results:
        if r["label"] == REGRESSION_LABEL:
            actual = r["oneway_violation_count"]
            ok = actual == REGRESSION_EXPECTED_ONEWAY
            status = "OK" if ok else "FAIL"
            print(
                f"\n[リグレッション確認] {REGRESSION_LABEL}: "
                f"oneway={actual} (期待値={REGRESSION_EXPECTED_ONEWAY}) → {status}"
            )
            return ok
    print(f"\n[リグレッション確認] {REGRESSION_LABEL} がスコア結果に見つかりません（入力ファイルを確認）")
    return False


def check_label_match(results: list[dict]) -> bool:
    """採点結果の label が google_comparison.csv に全件存在するか確認する。"""
    with open(COMPARISON_CSV, encoding="utf-8", newline="") as f:
        comparison_labels = {row["label"] for row in csv.DictReader(f)}

    all_ok = True
    print("\n[label 照合]")
    for r in results:
        label = r["label"]
        if label in comparison_labels:
            print(f"  OK : {label}")
        else:
            print(f"  MISS: {label!r} — google_comparison.csv に対応行なし（流し込みをスキップ）")
            all_ok = False
    return all_ok


def write_result_csv(results: list[dict]) -> None:
    fieldnames = [
        "label", "oneway_violation_count", "two_step_violation_count",
        "total_violation_count", "route_distance_m", "sampled_points",
    ]
    with open(RESULT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\n結果を {RESULT_CSV} に保存しました（--dry-run 確認用）")


def write_to_comparison(results: list[dict]) -> None:
    """google_comparison.csv の採点列のみを更新する。手入力列は保持。"""
    # バックアップ
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = COMPARISON_CSV.with_name(f"google_comparison_backup_{ts}.csv")
    shutil.copy2(COMPARISON_CSV, backup)
    print(f"\nバックアップ: {backup.name}")

    # 既存 CSV を読み込む
    with open(COMPARISON_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        existing_rows = list(reader)

    # 採点列が無ければ末尾に追加
    for col in ["google_total_violation_count", "scorer_sampled_points",
                "scorer_route_distance_m", "scored_at"]:
        if col not in fieldnames:
            fieldnames = list(fieldnames) + [col]

    # label → 採点結果 の辞書
    score_map = {r["label"]: r for r in results}

    updated = 0
    for row in existing_rows:
        label = row.get("label", "")
        if label not in score_map:
            continue
        s = score_map[label]
        row["google_oneway_violation_count"] = s["oneway_violation_count"]
        row["google_two_step_violation_count"] = s["two_step_violation_count"]
        row["google_total_violation_count"] = s["total_violation_count"]
        row["scorer_sampled_points"] = s["sampled_points"]
        row["scorer_route_distance_m"] = s["route_distance_m"]
        row["scored_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated += 1
        print(f"  更新: {label}")

    with open(COMPARISON_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)

    print(f"\n{updated} 行を更新しました → {COMPARISON_CSV.name}")


async def main(dry_run: bool) -> None:
    print(f"入力: {INPUT_CSV}")
    with open(INPUT_CSV, encoding="utf-8", newline="") as f:
        input_rows = list(csv.DictReader(f))
    print(f"{len(input_rows)} 件の入力を読み込みました\n")

    results = await score_all(input_rows)

    # (1) リグレッション確認
    reg_ok = check_regression(results)

    # (2) label 照合
    label_ok = check_label_match(results)

    # 結果一時保存（常に出力）
    write_result_csv(results)

    if dry_run:
        print("\n[dry-run] google_comparison.csv への書き込みをスキップしました。")
        print("  --write を付けて再実行すると流し込みを行います。")
        if not reg_ok:
            print("  ⚠ リグレッション失敗 — 採点ロジックを確認してください。")
        sys.exit(0 if reg_ok else 1)

    if not reg_ok:
        print("\n⚠ リグレッション失敗のため google_comparison.csv への書き込みを中止します。")
        sys.exit(1)

    # (3) 手入力列は上書きしない（write_to_comparison 内でラベル一致列のみ更新）
    write_to_comparison(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google ルートを一括採点して google_comparison.csv へ流し込む")
    parser.add_argument("--dry-run", action="store_true",
                        help="採点のみ実行。google_comparison.csv への書き込みはしない")
    parser.add_argument("--write", action="store_true",
                        help="採点後に google_comparison.csv を更新する")
    args = parser.parse_args()

    if not args.dry_run and not args.write:
        parser.print_help()
        sys.exit(1)

    asyncio.run(main(dry_run=args.dry_run))
