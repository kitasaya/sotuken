"""R2-auto 採点器の素の精度を ground_truth.csv 6点で検証する（ステップ1・ステップ2）。

半径20m・折れ角45度は変更しない。座標逆引き方式そのものの精度を before として記録し、
oneway 向き整合チェック（_is_way_misaligned）導入後の after も同じ表に並べる。
"""
import asyncio
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.overpass import _post_with_retry, get_way_tags_by_ids
from services.external_route_scorer import (
    _travel_vector_at, _haversine_m, _turn_angle_deg, _is_right_turn,
    _is_way_misaligned, score_external_route,
)
from services.law_checker import check_oneway_violation, check_two_step_turn

REPO_ROOT = Path(__file__).parent.parent.parent.parent
GROUND_TRUTH_CSV = REPO_ROOT / "調査結果" / "ground_truth.csv"
ROUTE_JSON = REPO_ROOT / "調査結果" / "response_渋谷新宿.json"
OUT_CSV = Path(__file__).parent.parent / "data" / "scorer_validation_groundtruth.csv"


async def get_nearest_way_with_id(lat: float, lng: float, radius: int = 20) -> dict:
    """get_bulk_way_data と同じ最近傍ノード方式だが、診断用に way id も返す。"""
    query = f"""[out:json][timeout:30];
(
  way(around:{radius},{lat},{lng})[highway];
);
out geom tags;
"""
    elements = await _post_with_retry(query)
    best = {"id": None, "tags": {}, "geometry": []}
    best_dist = float("inf")
    for elem in elements:
        geom_nodes = elem.get("geometry", [])
        for node in geom_nodes:
            d = (node["lat"] - lat) ** 2 + (node["lon"] - lng) ** 2
            if d < best_dist:
                best_dist = d
                best = {
                    "id": elem.get("id"),
                    "tags": elem.get("tags", {}),
                    "geometry": [[n["lon"], n["lat"]] for n in geom_nodes],
                }
    return best


def find_nearest_route_idx(coords: list, lat: float, lng: float) -> int:
    best_idx, best_dist = 0, float("inf")
    for i, c in enumerate(coords):
        d = _haversine_m(c, [lng, lat])
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_idx


def classify(true_val: bool, detected: bool) -> str:
    if true_val and detected:
        return "TP"
    if not true_val and detected:
        return "FP"
    if true_val and not detected:
        return "FN"
    return "TN"


async def main():
    rows = list(csv.DictReader(open(GROUND_TRUTH_CSV, encoding="utf-8")))
    route = json.load(open(ROUTE_JSON, encoding="utf-8"))
    coords = route["route"]["points"]["coordinates"]
    route_label = rows[0]["label"]

    output_rows = []
    for r in rows:
        label = r["label"]
        lat, lng = float(r["point_lat"]), float(r["point_lng"])
        expected_way_id = r["way_id"]
        true_oneway = r["true_oneway_violation"].strip().lower() == "true"
        true_2step = r["true_two_step_required"].strip().lower() == "true"

        matched = await get_nearest_way_with_id(lat, lng)
        matched_way_id = matched["id"]
        tags = matched["tags"]
        geometry = matched["geometry"]
        match_ok = (str(matched_way_id) == str(expected_way_id))

        idx = find_nearest_route_idx(coords, lat, lng)
        tv = _travel_vector_at(coords, idx)

        oneway_violations = await check_oneway_violation(
            [[lng, lat]], tags_list=[tags], geometries=[geometry], travel_vectors=[tv],
        )
        oneway_detected_before = len(oneway_violations) > 0

        # after: 向き整合チェックで誤マッチ判定された場合はタグを無効化
        after_tags = {} if _is_way_misaligned(geometry, tv) else tags
        oneway_violations_after = await check_oneway_violation(
            [[lng, lat]], tags_list=[after_tags], geometries=[geometry], travel_vectors=[tv],
        )
        oneway_detected_after = len(oneway_violations_after) > 0

        # 実際の採点パイプラインと同様、まず右折候補点として抽出されるかを判定する
        # （_extract_right_turn_points と同じロジック：折れ角45度以上 かつ 右折方向）
        v_in = _travel_vector_at(coords, max(idx - 1, 0))
        v_out = _travel_vector_at(coords, min(idx + 1, len(coords) - 1))
        angle = _turn_angle_deg(v_in, v_out)
        is_turn_candidate = angle >= 45.0 and _is_right_turn(v_in, v_out)

        if is_turn_candidate:
            two_step_violations = await check_two_step_turn([[lng, lat]], tags_list=[tags])
            two_step_detected = len(two_step_violations) > 0
        else:
            two_step_detected = False

        for rule, true_val, detected, detected_after, extra in (
            ("oneway", true_oneway, oneway_detected_before, oneway_detected_after, ""),
            ("two_step_turn", true_2step, two_step_detected, two_step_detected, f"turn_angle_deg={angle:.1f}"),
        ):
            output_rows.append({
                "label": label,
                "point_lat": lat,
                "point_lng": lng,
                "rule": rule,
                "ground_truth": true_val,
                "scorer_detected_before": detected,
                "scorer_detected_after": detected_after,
                "matched_way_id": matched_way_id,
                "expected_way_id": expected_way_id,
                "match_ok": match_ok,
                "result_before": classify(true_val, detected),
                "result_after": classify(true_val, detected_after),
                "notes": extra,
            })

    n_groundtruth_points = len(rows)

    # --- 追加検証: 40m間隔リサンプリングの実走査で偽陽性が観測された3点
    # （R2-auto 実地検証で way_id レベルまで原因特定済み）。修正前(_is_way_misaligned 導入前)の
    # 挙動と修正後を同じ3点で再照合する。ground_truth.csv には oneway=True の正例が無いため、
    # この3点をベースライン表の oneway 正例（の裏返し=FP源）として別途含める。
    fp_points = [
        (35.659482, 139.700572, 24, 1156909226),
        (35.660436, 139.700822, 28, 260252968),
        (35.664087, 139.701327, 38, 375809332),
    ]
    real_tags = await get_way_tags_by_ids([w for *_, w in fp_points])

    for lat, lng, idx, real_wid in fp_points:
        matched = await get_nearest_way_with_id(lat, lng)
        tv = _travel_vector_at(coords, idx)
        real_oneway_tag = real_tags.get(real_wid, {}).get("tags", {}).get("oneway")

        before_v = await check_oneway_violation(
            [[lng, lat]], tags_list=[matched["tags"]],
            geometries=[matched["geometry"]], travel_vectors=[tv],
        )
        after_tags = {} if _is_way_misaligned(matched["geometry"], tv) else matched["tags"]
        after_v = await check_oneway_violation(
            [[lng, lat]], tags_list=[after_tags],
            geometries=[matched["geometry"]], travel_vectors=[tv],
        )

        # route.py の v3 解析（edge_id ベース・正確な進行方向照合）が同ルートで
        # oneway 違反0件と判定済み（comparison.violation_count=1, two_step_turnのみ）。
        # よってこの地点群の ground truth は false。
        output_rows.append({
            "label": f"{route_label}（追加検証: 40m間隔リサンプリング点）",
            "point_lat": lat,
            "point_lng": lng,
            "rule": "oneway",
            "ground_truth": False,
            "scorer_detected_before": len(before_v) > 0,
            "scorer_detected_after": len(after_v) > 0,
            "matched_way_id": matched["id"],
            "expected_way_id": real_wid,
            "match_ok": str(matched["id"]) == str(real_wid),
            "result_before": classify(False, len(before_v) > 0),
            "result_after": classify(False, len(after_v) > 0),
            "notes": f"実way={real_wid}(oneway={real_oneway_tag}) を採点器は別wayと誤マッチ",
        })

    fieldnames = [
        "label", "point_lat", "point_lng", "rule", "ground_truth",
        "scorer_detected_before", "scorer_detected_after",
        "matched_way_id", "expected_way_id", "match_ok",
        "result_before", "result_after", "notes",
    ]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

        # 集計（before/after 両方）
        for phase, result_key in (("before", "result_before"), ("after", "result_after")):
            for rule in ("oneway", "two_step_turn"):
                rule_rows = [row for row in output_rows if row["rule"] == rule]
                tp = sum(1 for row in rule_rows if row[result_key] == "TP")
                fp = sum(1 for row in rule_rows if row[result_key] == "FP")
                fn = sum(1 for row in rule_rows if row[result_key] == "FN")
                tn = sum(1 for row in rule_rows if row[result_key] == "TN")
                precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
                f.write(f"# [{phase}] {rule}: TP={tp} FP={fp} FN={fn} TN={tn} "
                    f"precision={precision:.3f} recall={recall:.3f}\n")

        # ground_truth.csv 由来の点のみ対象（oneway/two_stepで重複カウントのため/2）。
        # 追加検証行（40m間隔リサンプリング点）は別カテゴリのため対象外。
        gt_rows = output_rows[: n_groundtruth_points * 2]
        mismatch = sum(1 for row in gt_rows if not row["match_ok"]) / 2
        f.write(f"# way_id mismatch (ground_truth.csv {n_groundtruth_points}点): "
                f"{int(mismatch)}/{n_groundtruth_points} ({mismatch / n_groundtruth_points * 100:.1f}%)\n")
        f.write("# 注: ground_truth.csv にはoneway=Trueの正例が無いため、上記表のoneway TP/FNは常に0。\n")
        f.write("#     oneway偽陽性の実例は下記「追加検証」3行（40m間隔リサンプリング点）を参照。\n")

    print(f"written: {OUT_CSV}")
    for row in output_rows:
        print(row)


if __name__ == "__main__":
    asyncio.run(main())
