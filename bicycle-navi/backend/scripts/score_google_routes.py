"""Google ルートの polyline を採点し、結果を google_comparison.csv へ流し込む。

## 使い方

  # 特定の label だけを採点・確認（CSV には書き込まない）
  python3 scripts/score_google_routes.py --label 渋谷→新宿 --dry-run

  # 特定の label だけを採点して書き込む（推奨。他の行には一切触れない）
  python3 scripts/score_google_routes.py --label 渋谷→新宿 --write

  # 複数 label をまとめて指定することも可能
  python3 scripts/score_google_routes.py --label 渋谷→新宿 --label 東京→渋谷 --write

  # 入力ファイル全件を対象にする場合（Overpass 障害時に既存の正しい値を
  # 巻き込んで壊すリスクがあるため、通常は --label 指定を推奨）
  python3 scripts/score_google_routes.py --all --write

## 入力

  backend/data/google_routes_input.csv
  列：label, polyline（Google encoded polyline。常にダブルクォートで囲むこと）

## 出力（--dry-run）

  backend/data/score_google_routes_result.csv（一時確認用）

## 出力（--write）

  google_comparison.csv のうち、**採点対象に指定した label の行のみ**の
  google_oneway_violation_count / google_two_step_violation_count /
  google_total_violation_count 列を更新する。指定していない行・列（距離・
  時間・route_overlap_pct などの手入力列）には一切触れない。
  書き込み前に google_comparison.csv のバックアップを自動作成。

## 設計上の注意（2026-07-07 の事故を踏まえて）

  以前は実行のたびに入力ファイルの全行を無条件に再採点していた。Overpass が
  一時的に全エンドポイント失敗すると、該当地点のタグが空 {} として扱われ
  「違反なし」と区別できないまま結果が 0 に化ける。この状態で --write すると
  既に確定していた行が誤って上書きされる事故が起きた（品川→東京が3→0等）。
  そのため --all を明示しない限り --label 指定を必須とし、意図しない対象への
  書き込みを防ぐ。
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


async def main(dry_run: bool, labels: list[str] | None) -> None:
    print(f"入力: {INPUT_CSV}")
    with open(INPUT_CSV, encoding="utf-8", newline="") as f:
        all_input_rows = list(csv.DictReader(f))

    if labels is not None:
        wanted = set(labels)
        available = {row["label"] for row in all_input_rows}
        missing = wanted - available
        if missing:
            print(f"\n⚠ 入力ファイルに存在しない label が指定されました: {sorted(missing)}")
            sys.exit(1)
        target_rows = [row for row in all_input_rows if row["label"] in wanted]
        print(f"--label 指定により {len(target_rows)} 件のみ採点します: {[r['label'] for r in target_rows]}\n")
    else:
        target_rows = all_input_rows
        print(f"--all 指定により入力ファイル全 {len(target_rows)} 件を対象にします\n")

    results = await score_all(target_rows)

    # (1) リグレッション確認：渋谷→新宿が採点対象に含まれていなければ、
    # 書き込み対象を汚さないよう別途スコアだけ取得して確認する。
    if any(r["label"] == REGRESSION_LABEL for r in results):
        reg_ok = check_regression(results)
    else:
        reg_row = next((row for row in all_input_rows if row["label"] == REGRESSION_LABEL), None)
        if reg_row is None:
            print(f"\n⚠ リグレッション確認用の {REGRESSION_LABEL} が入力ファイルに存在しません")
            reg_ok = False
        else:
            print(f"\n[リグレッション確認] {REGRESSION_LABEL} を別途採点して確認します（書き込み対象には含めない）")
            reg_results = await score_all([reg_row])
            reg_ok = check_regression(reg_results)

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
    parser = argparse.ArgumentParser(description="Google ルートを採点して google_comparison.csv へ流し込む")
    parser.add_argument("--dry-run", action="store_true",
                        help="採点のみ実行。google_comparison.csv への書き込みはしない")
    parser.add_argument("--write", action="store_true",
                        help="採点後に google_comparison.csv を更新する")
    parser.add_argument("--label", action="append", default=None,
                        help="採点対象の label を指定（繰り返し指定可）。指定した行のみ処理する。"
                             "未指定の場合は --all を明示すること")
    parser.add_argument("--all", action="store_true",
                        help="入力ファイル全件を対象にする（--label 未指定時に必須の明示フラグ）")
    args = parser.parse_args()

    if not args.dry_run and not args.write:
        parser.print_help()
        sys.exit(1)

    if args.label is None and not args.all:
        print("⚠ --label を指定するか、全件対象にする場合は --all を明示してください。")
        print("  （Overpass 障害時に無関係な行を巻き込んで壊す事故を防ぐための必須化です）")
        sys.exit(1)

    if args.label is not None and args.all:
        print("⚠ --label と --all は同時に指定できません。")
        sys.exit(1)

    asyncio.run(main(dry_run=args.dry_run, labels=args.label))
