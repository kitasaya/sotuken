import logging
import math

import httpx
from services.overpass import get_bulk_intersection_data, get_bulk_way_tags

logger = logging.getLogger(__name__)


def _sample(points: list) -> list:
    """ルート座標を最大10点にサンプリングする"""
    step = max(1, len(points) // 10)
    return points[::step]


def _dot2d(v1: list, v2: list) -> float:
    return v1[0] * v2[0] + v1[1] * v2[1]


def _haversine_m(a: list, b: list) -> float:
    """2点間の距離をメートルで返す。a, b = [lng, lat]"""
    R = 6_371_000
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = math.radians(b[1] - a[1])
    dlng = math.radians(b[0] - a[0])
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(x, 1.0)))


def _geom_length_m(geom: list) -> float:
    """ジオメトリノード列の総距離をメートルで返す。"""
    return sum(_haversine_m(geom[i], geom[i + 1]) for i in range(len(geom) - 1))


def _check_direction(geom: list, tv: list, oneway: str) -> bool:
    """way ジオメトリとトラベルベクトルから逆走かどうかを判定する。
    3点以上のカーブ区間は多数決、2点の直線は始終点ベクトルで判定。
    """
    if len(geom) >= 3:
        against = sum(
            1 for j in range(len(geom) - 1)
            if (
                (_dot2d([geom[j + 1][0] - geom[j][0], geom[j + 1][1] - geom[j][1]], tv) > 0)
                if oneway == "-1"
                else (_dot2d([geom[j + 1][0] - geom[j][0], geom[j + 1][1] - geom[j][1]], tv) < 0)
            )
        )
        return against > (len(geom) - 1) / 2
    else:
        way_vec = [geom[-1][0] - geom[0][0], geom[-1][1] - geom[0][1]]
        return (_dot2d(way_vec, tv) > 0) if oneway == "-1" else (_dot2d(way_vec, tv) < 0)


async def check_oneway_violation(
    points: list,
    tags_list: list[dict] | None = None,
    geometries: list[list] | None = None,
    travel_vectors: list[list] | None = None,
) -> list:
    """onewayタグによる逆走チェック。
    tags_list が与えられた場合は Overpass 呼び出しを省略する（points はサンプリング済みとみなす）。
    geometries と travel_vectors が与えられた場合は進行方向照合を行い偽陽性を排除する。
    """
    violations = []
    if tags_list is None:
        sampled = _sample(points)
        try:
            tags_list = await get_bulk_way_tags(sampled)
        except (httpx.HTTPError, httpx.TimeoutException):
            return violations
        iter_points = sampled
    else:
        iter_points = points

    # geometries/travel_vectors が提供されていれば edge_id ベース（最低 0.7）
    base_confidence = 0.4 if (geometries is None or travel_vectors is None) else 0.7

    for i, point in enumerate(iter_points):
        lng, lat = point[0], point[1]
        tags = tags_list[i] if i < len(tags_list) else {}
        oneway = tags.get("oneway", "no")

        if oneway not in ("yes", "true", "1", "-1"):
            continue

        # 自転車除外タグの確認
        if tags.get("oneway:bicycle") == "no":
            continue
        cycleway = tags.get("cycleway", "")
        if cycleway in ("opposite", "opposite_lane", "opposite_track"):
            continue

        # 進行方向照合（geometry と travel_vector が利用可能な場合）
        confidence = base_confidence
        if (geometries is not None and travel_vectors is not None
                and i < len(geometries) and i < len(travel_vectors)):
            geom = geometries[i]
            tv = travel_vectors[i]
            if len(geom) >= 2 and (tv[0] != 0 or tv[1] != 0):
                going_wrong_way = _check_direction(geom, tv, oneway)
                if not going_wrong_way:
                    continue  # 順方向走行、違反なし
                # 短い区間は照合精度が低いため信頼度を下げる
                confidence = 0.7 if _geom_length_m(geom) < 20.0 else 1.0

        violations.append({
            "lat": lat, "lng": lng,
            "rule": "oneway",
            "message": "一方通行のため逆走の可能性があります",
            "confidence": confidence,
        })
    return violations


async def check_sidewalk_violation(points: list, tags_list: list[dict] | None = None) -> list:
    """sidewalk=no による歩道通行不可チェック。"""
    violations = []
    if tags_list is None:
        sampled = _sample(points)
        try:
            tags_list = await get_bulk_way_tags(sampled)
        except (httpx.HTTPError, httpx.TimeoutException):
            return violations
        iter_points = sampled
    else:
        iter_points = points

    for i, point in enumerate(iter_points):
        lng, lat = point[0], point[1]
        tags = tags_list[i] if i < len(tags_list) else {}
        if tags.get("sidewalk", "") == "no":
            violations.append({
                "lat": lat, "lng": lng,
                "rule": "sidewalk",
                "message": "歩道通行不可の道路です",
            })
    return violations


async def check_cycleway_recommendation(points: list, tags_list: list[dict] | None = None) -> list:
    """cycleway=lane/track による自転車レーン推奨情報の収集。"""
    recommendations = []
    if tags_list is None:
        sampled = _sample(points)
        try:
            tags_list = await get_bulk_way_tags(sampled)
        except (httpx.HTTPError, httpx.TimeoutException):
            return recommendations
        iter_points = sampled
        confidence = 0.4
    else:
        iter_points = points
        confidence = 0.7

    for i, point in enumerate(iter_points):
        lng, lat = point[0], point[1]
        tags = tags_list[i] if i < len(tags_list) else {}
        cycleway = tags.get("cycleway", "")
        if cycleway in ("lane", "track"):
            recommendations.append({
                "lat": lat, "lng": lng,
                "rule": "cycleway_available",
                "message": f"自転車レーンあり（{cycleway}）",
                "confidence": confidence,
            })
    return recommendations


async def check_two_step_turn(
    points: list,
    tags_list: list[dict] | None = None,
    *,
    intersection_data: list[dict] | None = None,
    entry_way_ids: list[int | None] | None = None,
    exit_way_ids: list[int | None] | None = None,
) -> list:
    """右折 instruction 地点が道路エッジ3本以上の交差点なら案内を返す。

    tags_list は旧呼び出しとの引数互換のため残すが、highway/lanes 判定には使わない。

    道交法2条1項5号の「交差点」は、私道や駐車場出入口と公道が交わる部分も
    含みうる。本実装が進入元 way の除外リスト該当時にも案内を出さないのは、
    駐車場・私道からの発進に対する過剰案内を避ける設計判断であり、法解釈上の
    非該当を意味しない。この割り切りにより一部の交差点右折が未検出となる。
    """
    violations = []
    intersection_data_supplied = intersection_data is not None
    if intersection_data is None:
        try:
            intersection_data = await get_bulk_intersection_data(
                points, entry_way_ids=entry_way_ids, exit_way_ids=exit_way_ids,
            )
        except (httpx.HTTPError, httpx.TimeoutException):
            return violations

    for i, point in enumerate(points):
        lng, lat = point[0], point[1]
        data = intersection_data[i] if i < len(intersection_data) else {}
        has_way_context = (
            data.get("entry_way_id") is not None
            and data.get("exit_way_id") is not None
        )
        confidence = 0.7 if intersection_data_supplied or has_way_context else 0.4
        detected = data.get("is_intersection") and not data.get("entry_or_exit_excluded", False)
        logger.info(
            "two_step_turn判定: detected=%s node_id=%s edge_count=%s "
            "entry_way_id=%s exit_way_id=%s connected_ways=%s",
            detected,
            data.get("node_id"),
            data.get("edge_count", 0),
            data.get("entry_way_id"),
            data.get("exit_way_id"),
            data.get("connected_ways", []),
        )
        if detected:
            violations.append({
                "lat": lat, "lng": lng,
                "rule": "two_step_turn",
                "message": "二段階右折が必要な交差点です",
                "confidence": confidence,
                "node_id": data.get("node_id"),
                "edge_count": data.get("edge_count", 0),
                "connected_ways": data.get("connected_ways", []),
                "entry_way_id": data.get("entry_way_id"),
                "exit_way_id": data.get("exit_way_id"),
            })
    return violations
