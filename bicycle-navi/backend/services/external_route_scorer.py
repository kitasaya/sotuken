"""外部ルート（Google Maps 等）の座標列を、本システムと同一の判定器で採点する。

R2（Google Maps 比較）の手動カウントを半自動化するためのモジュール。
Google ルートには OSM way_id が無いため、座標ベースで最近傍 way とその
geometry を Overpass から取得し（get_bulk_way_data）、ルートの進行方向から
travel_vector を構築したうえで、law_checker の各判定関数をそのまま再利用する。

設計上の要点:
  - 判定基準を自システムと完全に共有する（同じ law_checker を呼ぶ）。
    これにより「採点の物差しが両者で同一」という実験上の公平性を担保する。
  - way_id が無いため confidence は座標ベース上限（oneway は方向照合込みで
    最大 1.0、ただし way_id 完全一致ではない点に留意）。
  - two_step_turn は「右折地点」が必要だが、外部ルートには instruction が
    無いため、進行方向が大きく変化する点（折れ角しきい値）を右折候補として
    抽出して判定する。
"""

import math

from services.law_checker import (
    check_oneway_violation,
    check_two_step_turn,
    _geom_length_m,
)
from services.overpass import (
    INTERSECTION_EXCLUDED_HIGHWAYS,
    get_bulk_intersection_data,
    get_bulk_way_data,
)


def _haversine_m(a: list, b: list) -> float:
    """2点間距離（m）。a, b = [lng, lat]"""
    R = 6_371_000
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = math.radians(b[1] - a[1])
    dlng = math.radians(b[0] - a[0])
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(x, 1.0)))


def _resample_by_distance(coords: list, interval_m: float = 40.0) -> list[int]:
    """座標列を一定間隔（既定 40m）でサンプリングし、採用する元インデックスを返す。

    Google の polyline は密度がまちまちなので、距離等間隔にして判定点の
    粗密を一定化する。始点・終点は必ず含む。
    """
    if len(coords) <= 2:
        return list(range(len(coords)))
    picked = [0]
    acc = 0.0
    for i in range(1, len(coords)):
        acc += _haversine_m(coords[i - 1], coords[i])
        if acc >= interval_m:
            picked.append(i)
            acc = 0.0
    if picked[-1] != len(coords) - 1:
        picked.append(len(coords) - 1)
    return picked


_TRAVEL_VECTOR_SPAN_M = 30.0


def _side_reference_indices(
    coords: list, idx: int, span_m: float = _TRAVEL_VECTOR_SPAN_M,
) -> tuple[int, int]:
    """idx の前後で累積距離が span_m に達する座標インデックスを返す。

    oneway 判定の travel_vector が従来から参照している範囲の算出に使う。
    """
    n = len(coords)
    j = idx
    back = 0.0
    while j > 0 and back < span_m:
        back += _haversine_m(coords[j - 1], coords[j])
        j -= 1
    k = idx
    fwd = 0.0
    while k < n - 1 and fwd < span_m:
        fwd += _haversine_m(coords[k], coords[k + 1])
        k += 1
    return j, k


def _travel_vector_at(coords: list, idx: int, span_m: float = 30.0) -> list:
    """idx 地点における進行方向ベクトル。前後 span_m ぶんの変位で近似する。"""
    j, k = _side_reference_indices(coords, idx, span_m)
    p_start = coords[j]
    p_end = coords[k]
    return [p_end[0] - p_start[0], p_end[1] - p_start[1]]


def _turn_angle_deg(v1: list, v2: list) -> float:
    """2ベクトルのなす角（度）。0=直進、180=Uターン。"""
    def norm(v):
        m = math.hypot(v[0], v[1])
        return [v[0] / m, v[1] / m] if m > 0 else [0.0, 0.0]
    a, b = norm(v1), norm(v2)
    dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))
    return math.degrees(math.acos(dot))


_ALIGNMENT_THRESHOLD_DEG = 60.0  # この角度を超えて travel_vector と乖離する way は誤マッチとみなす


def _way_axis_vector(geometry: list) -> list | None:
    """way geometry の始点→終点ベクトル（おおまかな道路の向き）。"""
    if not geometry or len(geometry) < 2:
        return None
    return [geometry[-1][0] - geometry[0][0], geometry[-1][1] - geometry[0][1]]


def _is_way_misaligned(geometry: list, travel_vector: list,
                        threshold_deg: float = _ALIGNMENT_THRESHOLD_DEG) -> bool:
    """最近傍マッチで拾った way の向きが travel_vector と大きく食い違うか判定する。

    座標逆引き（最近傍ノード）方式は対向車線・交差する別 way を誤って拾うことがある。
    交差点で直交する道（横切る別 way）は「平行からのズレ」が90度近くになるため、
    進行方向とおおむね平行（0度）・反平行（180度）かどうかで誤マッチを弾く。
    """
    axis = _way_axis_vector(geometry)
    if axis is None or (travel_vector[0] == 0 and travel_vector[1] == 0):
        return False  # 判定材料が無い場合は従来通り（誤マッチを弾けない側に倒す）
    angle = _turn_angle_deg(axis, travel_vector)
    deviation_from_parallel = min(angle, 180.0 - angle)
    return deviation_from_parallel > threshold_deg


def _is_right_turn(v_in: list, v_out: list) -> bool:
    """進入ベクトルから退出ベクトルへが右折か（外積の符号で判定）。

    [lng, lat] 平面で、cross = vin_x*vout_y - vin_y*vout_x。
    北半球の地図座標（lng=x, lat=y）では cross < 0 が右折（時計回り）。
    """
    cross = v_in[0] * v_out[1] - v_in[1] * v_out[0]
    return cross < 0


def _extract_right_turn_contexts(coords: list, sampled_idx: list,
                                 angle_threshold_deg: float = 45.0) -> list[dict]:
    """右折候補と、直前・直後1セグメントの範囲を返す。

    instruction の無い外部ルートで two_step_turn を評価するための近似。
    entry/exit 推定では短い道路区間を飛び越えないよう、右折点に隣接する
    1セグメントずつを使う。共有ノードそのものでは候補wayが同距離になりやすいため、
    get_bulk_way_data に渡す参照座標は各セグメントの中点とする。
    """
    turns = []
    for idx in sampled_idx:
        if idx <= 0 or idx >= len(coords) - 1:
            continue
        v_in = _travel_vector_at(coords, max(idx - 1, 0))
        v_out = _travel_vector_at(coords, min(idx + 1, len(coords) - 1))
        angle = _turn_angle_deg(v_in, v_out)
        if angle >= angle_threshold_deg and _is_right_turn(v_in, v_out):
            turns.append({
                "point": coords[idx],
                "route_idx": idx,
                "entry_ref_idx": idx - 1,
                "exit_ref_idx": idx + 1,
                "angle_deg": round(angle, 1),
            })
    return turns


def _extract_right_turn_points(coords: list, sampled_idx: list,
                               angle_threshold_deg: float = 45.0) -> list:
    """後方互換用: 右折候補の座標だけを返す。"""
    return [
        turn["point"]
        for turn in _extract_right_turn_contexts(coords, sampled_idx, angle_threshold_deg)
    ]


def _way_candidates(match: dict) -> list[dict]:
    """get_bulk_way_data の2m帯候補を正規化する（旧mockとも互換）。"""
    candidates = match.get("match_candidates")
    if candidates is not None:
        return candidates
    way_id = match.get("match_way_id")
    if way_id is None:
        return []
    tags = match.get("tags") or {}
    return [{
        "way_id": way_id,
        "highway": tags.get("highway"),
        "distance_m": match.get("match_dist_m"),
        "tags": tags,
    }]


def _candidate_diagnostic(candidate: dict) -> dict:
    return {
        "way_id": candidate.get("way_id"),
        "highway": candidate.get("highway"),
        "distance_m": candidate.get("distance_m"),
    }


def _intersection_for_candidates(base: dict, entry: dict, exit_: dict) -> dict:
    """共通のエッジ数を保ち、候補wayの除外判定だけを差し替える。"""
    entry_highway = entry.get("highway") or ""
    exit_highway = exit_.get("highway") or ""
    entry_excluded = entry_highway in INTERSECTION_EXCLUDED_HIGHWAYS
    exit_excluded = exit_highway in INTERSECTION_EXCLUDED_HIGHWAYS
    return {
        **base,
        "entry_way_id": entry.get("way_id"),
        "exit_way_id": exit_.get("way_id"),
        "entry_highway": entry_highway,
        "exit_highway": exit_highway,
        "entry_excluded": entry_excluded,
        "exit_excluded": exit_excluded,
        "entry_or_exit_excluded": entry_excluded or exit_excluded,
    }


def _segment_midpoint(a: list, b: list) -> list:
    """GeoJSON座標2点の中点。垂直距離マッチの参照座標として使う。"""
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]


def _legacy_two_step_match(exit_match: dict) -> bool:
    """旧 R2 条件（進入先 primary/secondary または lanes>=3）の追跡用。"""
    tags = exit_match.get("tags") or {}
    if tags.get("highway") in {"primary", "secondary"}:
        return True
    try:
        return int(str(tags.get("lanes", "0")).split(";")[0]) >= 3
    except (TypeError, ValueError):
        return False


async def score_external_route(coords: list, *, sample_interval_m: float = 40.0) -> dict:
    """外部ルートの座標列を採点する。

    coords: [[lng, lat], ...]（GeoJSON 座標順。Google polyline をこの形に変換して渡す）

    戻り値:
      {
        "oneway_violations": [...],       # law_checker と同形式
        "two_step_violations": [...],
        "oneway_violation_count": int,
        "two_step_required_intersections": int,
        "route_distance_m": float,
        "sampled_points": int,
      }
    """
    if not coords or len(coords) < 2:
        return {
            "oneway_violations": [], "two_step_violations": [],
            "oneway_violation_count": 0,
            "oneway_violation_count_high_conf": 0,
            "oneway_violation_count_low_conf": 0,
            "two_step_required_intersections": 0,
            "right_turn_count": 0,
            "two_step_excluded_count": 0,
            "two_step_excluded_edge_count_insufficient": 0,
            "two_step_excluded_entry_way": 0,
            "two_step_excluded_exit_way": 0,
            "two_step_unknown_count": 0,
            "two_step_unambiguous_determinate_count": 0,
            "two_step_ambiguous_determinate_count": 0,
            "old_two_step_detected_count": 0,
            "two_step_diagnostics": [],
            "route_distance_m": 0.0,
            "sampled_points": 0,
        }

    route_distance_m = sum(
        _haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1)
    )

    # ① 距離等間隔サンプリング
    sampled_idx = _resample_by_distance(coords, sample_interval_m)
    sampled_points = [coords[i] for i in sampled_idx]

    # ② 各サンプル点の最近傍 way（タグ + geometry）を一括取得
    way_data = await get_bulk_way_data(sampled_points)
    tags_list = [d["tags"] for d in way_data]
    geometries = [d["geometry"] for d in way_data]

    # ③ 各サンプル点の進行方向ベクトル
    travel_vectors = [_travel_vector_at(coords, i) for i in sampled_idx]

    # ③.5 oneway 限定：最近傍マッチが travel_vector と大きく食い違う（交差する別 way を
    # 誤って拾った）場合はタグを無効化し、check_oneway_violation に渡さない。
    # 座標逆引き方式に内在する誤マッチ対策（R2-auto の検証で確認・way_id レベルで原因特定済み）。
    oneway_tags_list = [
        {} if _is_way_misaligned(geom, tv) else tags
        for tags, geom, tv in zip(tags_list, geometries, travel_vectors)
    ]

    # ④ oneway 判定（自システムと同一の関数・方向照合あり）
    oneway_violations = await check_oneway_violation(
        sampled_points,
        tags_list=oneway_tags_list,
        geometries=geometries,
        travel_vectors=travel_vectors,
    )
    # 判定ロジックは変えず、R2再現性監査用にマッチ済みway IDを付加する。
    for violation in oneway_violations:
        matched_index = next((
            i for i, point in enumerate(sampled_points)
            if point[0] == violation.get("lng") and point[1] == violation.get("lat")
        ), None)
        if matched_index is not None:
            violation["way_id"] = way_data[matched_index].get("match_way_id")
            violation["match_dist_m"] = way_data[matched_index].get("match_dist_m")
            violation["match_margin_m"] = way_data[matched_index].get("match_margin_m")

    # ⑤ two_step_turn 判定（右折候補点のみを対象に）。進入元・進入先は、
    # oneway と同じ get_bulk_way_data の点-曲線間垂直距離法で一括推定する。
    turn_contexts = _extract_right_turn_contexts(coords, sampled_idx)
    two_step_violations = []
    two_step_diagnostics = []
    if turn_contexts:
        side_reference_points = []
        for turn in turn_contexts:
            side_reference_points.extend([
                _segment_midpoint(
                    coords[turn["entry_ref_idx"]], coords[turn["route_idx"]],
                ),
                _segment_midpoint(
                    coords[turn["route_idx"]], coords[turn["exit_ref_idx"]],
                ),
            ])
        side_matches = await get_bulk_way_data(side_reference_points)

        rank1_entry_ids = []
        rank1_exit_ids = []
        for turn_index, turn in enumerate(turn_contexts):
            entry_match = side_matches[turn_index * 2]
            exit_match = side_matches[turn_index * 2 + 1]
            entry_candidates = _way_candidates(entry_match)
            exit_candidates = _way_candidates(exit_match)
            diagnostic = {
                **turn,
                "entry_way_id": entry_match.get("match_way_id"),
                "exit_way_id": exit_match.get("match_way_id"),
                "entry_highway": (entry_match.get("tags") or {}).get("highway"),
                "exit_highway": (exit_match.get("tags") or {}).get("highway"),
                "entry_match_dist_m": entry_match.get("match_dist_m"),
                "exit_match_dist_m": exit_match.get("match_dist_m"),
                "entry_match_margin_m": entry_match.get("match_margin_m"),
                "exit_match_margin_m": exit_match.get("match_margin_m"),
                "entry_candidates": [
                    _candidate_diagnostic(candidate) for candidate in entry_candidates
                ],
                "exit_candidates": [
                    _candidate_diagnostic(candidate) for candidate in exit_candidates
                ],
                "ambiguous": len(entry_candidates) > 1 or len(exit_candidates) > 1,
                "old_two_step_detected": _legacy_two_step_match(exit_match),
            }
            if not entry_candidates or not exit_candidates:
                reasons = []
                if not entry_candidates:
                    reasons.append("entry_way_unmatched")
                if not exit_candidates:
                    reasons.append("exit_way_unmatched")
                diagnostic.update({
                    "status": "unknown",
                    "determinate": False,
                    "ambiguous_but_determinate": False,
                    "unknown_reasons": reasons,
                    "combination_results": [],
                })
            rank1_entry_ids.append(entry_match.get("match_way_id"))
            rank1_exit_ids.append(exit_match.get("match_way_id"))
            two_step_diagnostics.append(diagnostic)

        intersection_data = await get_bulk_intersection_data(
            [turn["point"] for turn in turn_contexts],
            entry_way_ids=rank1_entry_ids,
            exit_way_ids=rank1_exit_ids,
        )

        combination_points = []
        combination_data = []
        combination_meta = []
        for turn_index, diagnostic in enumerate(two_step_diagnostics):
            if diagnostic.get("status") == "unknown":
                continue
            base = intersection_data[turn_index]
            entry_candidates = _way_candidates(side_matches[turn_index * 2])
            exit_candidates = _way_candidates(side_matches[turn_index * 2 + 1])
            for entry in entry_candidates:
                for exit_ in exit_candidates:
                    data = _intersection_for_candidates(base, entry, exit_)
                    combination_points.append(diagnostic["point"])
                    combination_data.append(data)
                    combination_meta.append((turn_index, entry, exit_, data))

        combination_violations = await check_two_step_turn(
            combination_points, intersection_data=combination_data,
        ) if combination_points else []
        detected_keys = {
            (
                v.get("lat"), v.get("lng"), v.get("node_id"),
                v.get("entry_way_id"), v.get("exit_way_id"),
            )
            for v in combination_violations
        }

        results_by_turn: dict[int, list[dict]] = {}
        for turn_index, entry, exit_, data in combination_meta:
            point = turn_contexts[turn_index]["point"]
            key = (
                point[1], point[0], data.get("node_id"),
                entry.get("way_id"), exit_.get("way_id"),
            )
            entry_excluded = data.get("entry_excluded", False)
            exit_excluded = data.get("exit_excluded", False)
            edge_insufficient = data.get("edge_count", 0) < 3
            reasons = []
            if edge_insufficient:
                reasons.append("edge_count_insufficient")
            if entry_excluded:
                reasons.append("entry_way_excluded")
            if exit_excluded:
                reasons.append("exit_way_excluded")
            results_by_turn.setdefault(turn_index, []).append({
                "entry_way_id": entry.get("way_id"),
                "entry_highway": entry.get("highway"),
                "exit_way_id": exit_.get("way_id"),
                "exit_highway": exit_.get("highway"),
                "decision": "detected" if key in detected_keys else "excluded",
                "reasons": reasons,
            })

        final_detected_points = []
        final_detected_data = []
        for turn_index, diagnostic in enumerate(two_step_diagnostics):
            if diagnostic.get("status") == "unknown":
                continue
            base = intersection_data[turn_index]
            outcomes = results_by_turn.get(turn_index, [])
            decisions = {outcome["decision"] for outcome in outcomes}
            determinate = len(decisions) == 1
            ambiguous = diagnostic["ambiguous"]
            diagnostic.update({
                "node_id": base.get("node_id"),
                "edge_count": base.get("edge_count", 0),
                "connected_ways": base.get("connected_ways", []),
                "determinate": determinate,
                "ambiguous_but_determinate": ambiguous and determinate,
                "combination_results": outcomes,
            })
            if not determinate:
                diagnostic.update({
                    "status": "unknown",
                    "unknown_reasons": ["candidate_combinations_disagree"],
                })
                continue

            decision = next(iter(decisions))
            diagnostic["status"] = decision
            diagnostic["edge_count_insufficient"] = base.get("edge_count", 0) < 3
            diagnostic["entry_excluded"] = all(
                "entry_way_excluded" in outcome["reasons"] for outcome in outcomes
            )
            diagnostic["exit_excluded"] = all(
                "exit_way_excluded" in outcome["reasons"] for outcome in outcomes
            )
            if decision == "detected":
                representative = combination_data[
                    next(
                        i for i, meta in enumerate(combination_meta)
                        if meta[0] == turn_index
                    )
                ]
                final_detected_points.append(diagnostic["point"])
                final_detected_data.append(representative)

        if final_detected_points:
            two_step_violations = await check_two_step_turn(
                final_detected_points, intersection_data=final_detected_data,
            )

    high_conf_count = sum(v.get("confidence", 0) >= 0.7 for v in oneway_violations)
    excluded_diagnostics = [d for d in two_step_diagnostics if d.get("status") == "excluded"]

    return {
        "oneway_violations": oneway_violations,
        "two_step_violations": two_step_violations,
        "oneway_violation_count": len(oneway_violations),
        "oneway_violation_count_high_conf": high_conf_count,
        "oneway_violation_count_low_conf": len(oneway_violations) - high_conf_count,
        "two_step_required_intersections": len(two_step_violations),
        "right_turn_count": len(turn_contexts),
        "two_step_excluded_count": len(excluded_diagnostics),
        "two_step_excluded_edge_count_insufficient": sum(
            bool(d.get("edge_count_insufficient")) for d in excluded_diagnostics
        ),
        "two_step_excluded_entry_way": sum(
            bool(d.get("entry_excluded")) for d in excluded_diagnostics
        ),
        "two_step_excluded_exit_way": sum(
            bool(d.get("exit_excluded")) for d in excluded_diagnostics
        ),
        "two_step_unknown_count": sum(
            d.get("status") == "unknown" for d in two_step_diagnostics
        ),
        "two_step_unambiguous_determinate_count": sum(
            not d.get("ambiguous", False) and d.get("determinate", False)
            for d in two_step_diagnostics
        ),
        "two_step_ambiguous_determinate_count": sum(
            d.get("ambiguous", False) and d.get("determinate", False)
            for d in two_step_diagnostics
        ),
        "old_two_step_detected_count": sum(
            bool(d.get("old_two_step_detected")) for d in two_step_diagnostics
        ),
        "two_step_diagnostics": two_step_diagnostics,
        "route_distance_m": round(route_distance_m, 1),
        "sampled_points": len(sampled_points),
    }


# ---------------------------------------------------------------------------
# Google polyline デコード（Encoded Polyline Algorithm Format）
# ---------------------------------------------------------------------------

def decode_polyline(encoded: str) -> list:
    """Google の encoded polyline を [[lng, lat], ...] にデコードする。

    Google Directions API の overview_polyline / steps の polyline 文字列に対応。
    返却は GeoJSON 座標順（[lng, lat]）で、本モジュールの coords 入力と整合する。
    """
    coords = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        for unit in ("lat", "lng"):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if unit == "lat":
                lat += delta
            else:
                lng += delta
        coords.append([lng / 1e5, lat / 1e5])
    return coords
