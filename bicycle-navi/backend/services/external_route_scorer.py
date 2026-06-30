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
from services.overpass import get_bulk_way_data


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


def _travel_vector_at(coords: list, idx: int, span_m: float = 30.0) -> list:
    """idx 地点における進行方向ベクトル。前後 span_m ぶんの変位で近似する。"""
    n = len(coords)
    # 後方 span
    j = idx
    back = 0.0
    while j > 0 and back < span_m:
        back += _haversine_m(coords[j - 1], coords[j])
        j -= 1
    # 前方 span
    k = idx
    fwd = 0.0
    while k < n - 1 and fwd < span_m:
        fwd += _haversine_m(coords[k], coords[k + 1])
        k += 1
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


def _extract_right_turn_points(coords: list, sampled_idx: list,
                               angle_threshold_deg: float = 45.0) -> list:
    """進行方向が大きく右に折れる点を右折候補として抽出し、その座標を返す。

    instruction の無い外部ルートで two_step_turn を評価するための近似。
    """
    turn_points = []
    for idx in sampled_idx:
        if idx <= 0 or idx >= len(coords) - 1:
            continue
        v_in = _travel_vector_at(coords, max(idx - 1, 0))
        v_out = _travel_vector_at(coords, min(idx + 1, len(coords) - 1))
        angle = _turn_angle_deg(v_in, v_out)
        if angle >= angle_threshold_deg and _is_right_turn(v_in, v_out):
            turn_points.append(coords[idx])
    return turn_points


async def score_external_route(coords: list, *, sample_interval_m: float = 40.0) -> dict:
    """外部ルートの座標列を採点する。

    coords: [[lng, lat], ...]（GeoJSON 座標順。Google polyline をこの形に変換して渡す）

    戻り値:
      {
        "oneway_violations": [...],       # law_checker と同形式
        "two_step_violations": [...],
        "oneway_violation_count": int,
        "two_step_violation_count": int,
        "total_violation_count": int,
        "route_distance_m": float,
        "sampled_points": int,
      }
    """
    if not coords or len(coords) < 2:
        return {
            "oneway_violations": [], "two_step_violations": [],
            "oneway_violation_count": 0, "two_step_violation_count": 0,
            "total_violation_count": 0, "route_distance_m": 0.0,
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
    # 座標逆引き方式に内在する誤マッチ対策（R2-auto 実地検証で確認・way_id レベルで原因特定済み）。
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

    # ⑤ two_step_turn 判定（右折候補点のみを対象に）
    right_turn_pts = _extract_right_turn_points(coords, sampled_idx)
    two_step_violations = []
    if right_turn_pts:
        rt_way_data = await get_bulk_way_data(right_turn_pts)
        rt_tags = [d["tags"] for d in rt_way_data]
        two_step_violations = await check_two_step_turn(right_turn_pts, tags_list=rt_tags)

    return {
        "oneway_violations": oneway_violations,
        "two_step_violations": two_step_violations,
        "oneway_violation_count": len(oneway_violations),
        "two_step_violation_count": len(two_step_violations),
        "total_violation_count": len(oneway_violations) + len(two_step_violations),
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
