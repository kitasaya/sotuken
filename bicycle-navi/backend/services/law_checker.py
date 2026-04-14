import httpx
from services.overpass import get_bulk_way_tags


def _sample(points: list) -> list:
    """ルート座標を最大10点にサンプリングする"""
    step = max(1, len(points) // 10)
    return points[::step]


async def check_oneway_violation(points: list, tags_list: list[dict] | None = None) -> list:
    """onewayタグによる逆走チェック。
    tags_list が与えられた場合は Overpass 呼び出しを省略する（points はサンプリング済みとみなす）。
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

    for i, point in enumerate(iter_points):
        lng, lat = point[0], point[1]
        tags = tags_list[i] if i < len(tags_list) else {}
        if tags.get("oneway", "no") in ("yes", "true", "1"):
            violations.append({
                "lat": lat, "lng": lng,
                "rule": "oneway",
                "message": "一方通行のため逆走の可能性があります",
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
    else:
        iter_points = points

    for i, point in enumerate(iter_points):
        lng, lat = point[0], point[1]
        tags = tags_list[i] if i < len(tags_list) else {}
        cycleway = tags.get("cycleway", "")
        if cycleway in ("lane", "track"):
            recommendations.append({
                "lat": lat, "lng": lng,
                "rule": "cycleway_available",
                "message": f"自転車レーンあり（{cycleway}）",
            })
    return recommendations


async def check_two_step_turn(points: list, tags_list: list[dict] | None = None) -> list:
    """二段階右折要否の判定（幹線道路 or 3車線以上）。"""
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
            })
    return violations
