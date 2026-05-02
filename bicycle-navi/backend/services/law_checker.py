import httpx
from services.overpass import get_bulk_way_tags


def _sample(points: list) -> list:
    """ルート座標を最大10点にサンプリングする"""
    step = max(1, len(points) // 10)
    return points[::step]


def _dot2d(v1: list, v2: list) -> float:
    return v1[0] * v2[0] + v1[1] * v2[1]


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
                way_vec = [geom[-1][0] - geom[0][0], geom[-1][1] - geom[0][1]]
                dot = _dot2d(way_vec, tv)
                # oneway=-1 は始点→終点方向が「逆」を意味する
                going_wrong_way = (dot > 0) if oneway == "-1" else (dot < 0)
                if not going_wrong_way:
                    continue  # 順方向走行、違反なし
                confidence = 1.0  # 進行方向照合済み

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


async def check_two_step_turn(points: list, tags_list: list[dict] | None = None) -> list:
    """二段階右折要否の判定（幹線道路 or 3車線以上）。
    右折 instruction 地点の座標リストを受け取ることを前提とする。
    """
    violations = []
    if tags_list is None:
        sampled = _sample(points)
        try:
            tags_list = await get_bulk_way_tags(sampled)
        except (httpx.HTTPError, httpx.TimeoutException):
            return violations
        iter_points = sampled
        confidence = 0.4
    else:
        iter_points = points
        confidence = 0.7

    for i, point in enumerate(iter_points):
        lng, lat = point[0], point[1]
        tags = tags_list[i] if i < len(tags_list) else {}
        highway = tags.get("highway", "")
        try:
            lanes = int(tags.get("lanes", "0"))
        except (ValueError, TypeError):
            lanes = 0

        if highway in ("primary", "secondary") or lanes >= 3:
            violations.append({
                "lat": lat, "lng": lng,
                "rule": "two_step_turn",
                "message": "二段階右折が必要な交差点です",
                "confidence": confidence,
            })
    return violations
