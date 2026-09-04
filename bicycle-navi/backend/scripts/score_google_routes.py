"""保存済みGoogleルート15ペアを採点し、R2 v4 CSVを新規出力する。

## 使い方

  # 特定の label だけを採点・確認（CSV には書き込まない）
  python3 scripts/score_google_routes.py --label 渋谷→新宿 --dry-run

  # 特定の label だけをv4 CSVへ書き込む
  python3 scripts/score_google_routes.py --label 渋谷→新宿 --write

  # 複数 label をまとめて指定することも可能
  python3 scripts/score_google_routes.py --label 渋谷→新宿 --label 東京→渋谷 --write

  # 入力ファイル全件を対象にする場合
  python3 scripts/score_google_routes.py --all --write

## 入力

  backend/data/google_routes_input.csv
  列：label, polyline（Google encoded polyline。常にダブルクォートで囲むこと）

## 出力（--write）

  backend/data/r2_google_routes_v4.csv
  旧CSVは更新・上書きしない。

## 設計上の注意（2026-07-07 の事故を踏まえて）

  以前は実行のたびに入力ファイルの全行を無条件に再採点していた。Overpass が
  一時的に全エンドポイント失敗すると、該当地点のタグが空 {} として扱われ
  「違反なし」と区別できないまま結果が 0 に化ける。この状態で --write すると
  既に確定していた行が誤って上書きされる事故が起きた（品川→東京が3→0等）。
  現在は全エンドポイント失敗を専用例外として伝播し、ペアを ERROR にする。
  全対象が成功しない限り google_comparison.csv は1行も更新せず、終了コードも
  非ゼロにする。成功・失敗件数とendpoint別の判定対象数を標準出力へ記録する。
"""

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.external_route_scorer import score_external_route, decode_polyline
from services.experiment_settings import activate_experiment_overpass_date
from services.overpass import (
    format_overpass_usage_summary,
    get_overpass_usage_stats,
    reset_overpass_usage_stats,
)

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_CSV = DATA_DIR / "google_routes_input.csv"
OD_PAIRS_CSV = DATA_DIR / "od_pairs.csv"
BASELINE_CSV = DATA_DIR / "batch21_r2_remeasurement.csv"
RESULT_CSV = DATA_DIR / "r2_google_routes_v4.csv"
ALGO_VERSION = "v4"
REQUEST_INTERVAL_S = 3.0
PAIR_MAX_ATTEMPTS = 2
PAIR_RETRY_WAIT_S = 5.0

EXPECTED_ONEWAY_TOTAL = 12
EXPECTED_ONEWAY_ZERO_PAIRS = 6


def _decision_counts() -> dict[str, int]:
    return {
        name: values["decision_units"]
        for name, values in get_overpass_usage_stats().items()
    }


def _count_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {name: after.get(name, 0) - before.get(name, 0) for name in after}


def _format_endpoint_counts(counts: dict[str, int]) -> str:
    return "; ".join(f"{name}={count}" for name, count in counts.items())


async def score_all(
    input_rows: list[dict], interval_s: float = 0.0,
) -> tuple[list[dict], list[dict]]:
    results = []
    failures = []
    for index, row in enumerate(input_rows):
        if index > 0 and interval_s > 0:
            await asyncio.sleep(interval_s)
        label = row["label"]
        polyline_str = row.get("polyline", "").strip()
        if not polyline_str:
            error = "polyline が空"
            print(f"  [ERROR] {label}: {error}")
            failures.append({"label": label, "status": "ERROR", "error": error})
            continue

        try:
            coords = decode_polyline(polyline_str)
        except Exception as e:
            error = f"polyline decode failed: {type(e).__name__}: {e}"
            print(f"  [ERROR] {label}: {error}")
            failures.append({"label": label, "status": "ERROR", "error": error})
            continue
        if len(coords) < 2:
            error = f"polyline の座標点が不足: {len(coords)}"
            print(f"  [ERROR] {label}: {error}")
            failures.append({"label": label, "status": "ERROR", "error": error})
            continue
        print(f"  採点中: {label} ({len(coords)} 座標点)", flush=True)
        before = _decision_counts()
        score = None
        error = ""
        for attempt in range(1, PAIR_MAX_ATTEMPTS + 1):
            try:
                # ペア/試行ごとにContextVarを分離し、回路遮断を後続へ漏らさない。
                score = await asyncio.create_task(
                    score_external_route(coords, sample_interval_m=40.0)
                )
                break
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                if attempt < PAIR_MAX_ATTEMPTS:
                    print(f"    → 取得失敗（{attempt}/{PAIR_MAX_ATTEMPTS}）、{PAIR_RETRY_WAIT_S:.0f}秒後に再試行: {error}")
                    await asyncio.sleep(PAIR_RETRY_WAIT_S)
        if score is None:
            print(f"    → ERROR: {error}")
            failures.append({"label": label, "status": "ERROR", "error": error})
            continue
        endpoint_counts = _count_delta(before, _decision_counts())

        results.append({
            "label": label,
            "road_type": row.get("road_type", ""),
            "algo_version": ALGO_VERSION,
            "status": "OK",
            "error": "",
            "oneway_violation_count": score["oneway_violation_count"],
            "oneway_violation_count_high_conf": score["oneway_violation_count_high_conf"],
            "oneway_violation_count_low_conf": score["oneway_violation_count_low_conf"],
            "oneway_way_ids": ";".join(
                str(v.get("way_id")) for v in score["oneway_violations"]
                if v.get("way_id") is not None
            ),
            "two_step_required_intersections": score["two_step_required_intersections"],
            "right_turn_count": score["right_turn_count"],
            "two_step_unambiguous_determinate_count": score[
                "two_step_unambiguous_determinate_count"
            ],
            "two_step_ambiguous_determinate_count": score[
                "two_step_ambiguous_determinate_count"
            ],
            "two_step_unknown_count": score["two_step_unknown_count"],
            "two_step_excluded_count": score["two_step_excluded_count"],
            "two_step_excluded_edge_count_insufficient": score[
                "two_step_excluded_edge_count_insufficient"
            ],
            "two_step_excluded_entry_way": score["two_step_excluded_entry_way"],
            "two_step_excluded_exit_way": score["two_step_excluded_exit_way"],
            "two_step_diagnostics_json": json.dumps(
                [
                    {key: value for key, value in diagnostic.items()
                     if key != "old_two_step_detected"}
                    for diagnostic in score["two_step_diagnostics"]
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "sampled_points": score["sampled_points"],
            "overpass_endpoint_counts": _format_endpoint_counts(endpoint_counts),
        })
        print(
            f"    → oneway={score['oneway_violation_count']} "
            f"two_step_required_intersections={score['two_step_required_intersections']} "
            f"right_turns={score['right_turn_count']} "
            f"ambiguity={score['two_step_unambiguous_determinate_count']}/"
            f"{score['two_step_ambiguous_determinate_count']}/"
            f"{score['two_step_unknown_count']} "
            f"endpoint=[{_format_endpoint_counts(endpoint_counts)}]"
        )
    return results, failures


def load_oneway_baseline() -> dict[str, list[int]]:
    """batch21垂直距離法の検出行を、labelごとのway ID列として読む。"""
    baseline: dict[str, list[int]] = defaultdict(list)
    with open(BASELINE_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("new_oneway_violation", "").lower() == "true":
                baseline[row["label"]].append(int(row["way_id"]))
    return dict(baseline)


def check_oneway_regression(results: list[dict]) -> bool:
    """12件・ゼロ6ペアと、ペア別way ID多重集合をbatch21に照合する。"""
    baseline = load_oneway_baseline()
    result_by_label = {row["label"]: row for row in results}
    all_labels = [row["label"] for row in results]
    total = sum(row["oneway_violation_count"] for row in results)
    zero_pairs = sum(row["oneway_violation_count"] == 0 for row in results)
    differences = []
    for label in all_labels:
        expected_ids = baseline.get(label, [])
        actual_ids = [
            int(value) for value in result_by_label[label]["oneway_way_ids"].split(";")
            if value
        ]
        if Counter(actual_ids) != Counter(expected_ids):
            differences.append((label, expected_ids, actual_ids))

    print("\n[oneway再現性確認]")
    print(f"  合計: {total}（期待={EXPECTED_ONEWAY_TOTAL}）")
    print(f"  ゼロのペア: {zero_pairs}/{len(results)}（期待={EXPECTED_ONEWAY_ZERO_PAIRS}/15）")
    for label, expected_ids, actual_ids in differences:
        print(f"  差分: {label}: 期待way_id={expected_ids}, 実測way_id={actual_ids}")
    return (
        total == EXPECTED_ONEWAY_TOTAL
        and zero_pairs == EXPECTED_ONEWAY_ZERO_PAIRS
        and not differences
    )


def write_result_csv(results: list[dict]) -> None:
    fieldnames = [
        "label", "road_type", "algo_version", "status", "error",
        "oneway_violation_count",
        "oneway_violation_count_high_conf", "oneway_violation_count_low_conf",
        "oneway_way_ids",
        "two_step_required_intersections", "right_turn_count",
        "two_step_unambiguous_determinate_count",
        "two_step_ambiguous_determinate_count", "two_step_unknown_count",
        "two_step_excluded_count",
        "two_step_excluded_edge_count_insufficient",
        "two_step_excluded_entry_way", "two_step_excluded_exit_way",
        "two_step_diagnostics_json",
        "sampled_points",
        "overpass_endpoint_counts",
    ]
    with open(RESULT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    print(f"\nR2 v4結果を新規保存しました: {RESULT_CSV}")


async def main(dry_run: bool, labels: list[str] | None, interval_s: float) -> int:
    settings = activate_experiment_overpass_date()
    reset_overpass_usage_stats()
    print(
        "実験用Overpass取得時点: "
        f"{settings['overpass_snapshot_date']} "
        f"(GraphHopper: {settings['graphhopper_data_date']})"
    )
    print(f"入力: {INPUT_CSV}")
    with open(INPUT_CSV, encoding="utf-8", newline="") as f:
        all_input_rows = list(csv.DictReader(f))
    with open(OD_PAIRS_CSV, encoding="utf-8", newline="") as f:
        road_type_by_label = {
            row["label"]: row["road_type"] for row in csv.DictReader(f)
        }
    all_input_rows = [
        {**row, "road_type": road_type_by_label.get(row["label"], "")}
        for row in all_input_rows
    ]

    if labels is not None:
        wanted = set(labels)
        available = {row["label"] for row in all_input_rows}
        missing = wanted - available
        if missing:
            print(f"\n⚠ 入力ファイルに存在しない label が指定されました: {sorted(missing)}")
            return 1
        target_rows = [row for row in all_input_rows if row["label"] in wanted]
        print(f"--label 指定により {len(target_rows)} 件のみ採点します: {[r['label'] for r in target_rows]}\n")
    else:
        target_rows = all_input_rows
        print(f"--all 指定により入力ファイル全 {len(target_rows)} 件を対象にします\n")

    results, failures = await score_all(target_rows, interval_s)
    print(f"\n[実行サマリ] 成功={len(results)}件 / 失敗={len(failures)}件")
    print(format_overpass_usage_summary())

    if failures:
        print("\n⚠ 失敗したペアがあるためv4 CSVは出力しません。")
        for failure in failures:
            print(f"  - {failure['label']}: {failure['error']}")
        return 1

    full_run = labels is None and len(results) == 15
    reg_ok = check_oneway_regression(results) if full_run else True
    if full_run and not reg_ok:
        print("\n⚠ oneway再現性が不一致のためv4 CSVは出力せず停止します。")
        return 1

    if dry_run:
        print("\n[dry-run] v4 CSVへの書き込みをスキップしました。")
        return 0

    if not full_run:
        print("\n⚠ 正式なv4 CSVは --all の15ペア完走時だけ出力します。")
        return 1

    write_result_csv(results)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="保存済みGoogleルートを採点してR2 v4 CSVを作る")
    parser.add_argument("--dry-run", action="store_true",
                        help="採点のみ実行。v4 CSVは作成しない")
    parser.add_argument("--write", action="store_true",
                        help="15ペア完走・oneway再現確認後にv4 CSVを新規作成する")
    parser.add_argument("--label", action="append", default=None,
                        help="採点対象の label を指定（繰り返し指定可）。指定した行のみ処理する。"
                             "未指定の場合は --all を明示すること")
    parser.add_argument("--all", action="store_true",
                        help="入力ファイル全件を対象にする（--label 未指定時に必須の明示フラグ）")
    parser.add_argument("--interval", type=float, default=REQUEST_INTERVAL_S,
                        help=f"ペア間の待機秒数（既定: {REQUEST_INTERVAL_S}）")
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

    sys.exit(asyncio.run(main(
        dry_run=args.dry_run, labels=args.label, interval_s=args.interval,
    )))
