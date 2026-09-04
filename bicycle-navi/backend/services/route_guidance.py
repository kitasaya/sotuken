"""走行中に提示する一時停止・踏切案内。

違反判定や経路評価とは独立して、GraphHopper が通過した OSM way の
実走行区間に含まれるタグ付き node だけを抽出する。
"""

import logging
import math

from services.overpass import get_route_guidance_data

logger = logging.getLogger(__name__)


def _haversine_m(a: list, b: list) -> float:
    """[lng, lat] の2点間距離をメートルで返す。"""
    radius = 6_371_000
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = math.radians(b[0] - a[0])
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(min(value, 1.0)))


def _point_segment_projection(point: list, a: list, b: list) -> tuple[float, float]:
    """point の a-b 上への射影率と、経緯度平面上の距離二乗を返す。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        return 0.0, (point[0] - a[0]) ** 2 + (point[1] - a[1]) ** 2
    ratio = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denom
    ratio = max(0.0, min(1.0, ratio))
    projected = [a[0] + ratio * dx, a[1] + ratio * dy]
    dist_sq = (point[0] - projected[0]) ** 2 + (point[1] - projected[1]) ** 2
    return ratio, dist_sq


def _arc_fit_score(arc: list[dict], route_segment: list) -> float:
    """ルート座標列と候補弧の距離二乗和。閉ループの弧選択だけに使う。"""
    if len(arc) < 2:
        return float("inf")
    coordinates = [item["coordinate"] for item in arc]
    return sum(
        min(
            _point_segment_projection(point, a, b)[1]
            for a, b in zip(coordinates, coordinates[1:])
        )
        for point in route_segment
    )


def _ordered_fit_score(path: list[dict], route_segment: list) -> float:
    """同一点を始終点とする一周時に、進行順も含めて適合度を測る。"""
    if len(path) < 2 or len(route_segment) < 2:
        return float("inf")
    route_index = 0
    score = 0.0
    for item in path:
        coordinate = item["coordinate"]
        candidates = []
        for index in range(route_index, len(route_segment) - 1):
            ratio, dist_sq = _point_segment_projection(
                coordinate, route_segment[index], route_segment[index + 1]
            )
            candidates.append((dist_sq, index, ratio))
        if not candidates:
            break
        dist_sq, route_index, _ = min(candidates)
        score += dist_sq
    return score


def _traversed_way_nodes(
    way: dict,
    p_start: list,
    p_end: list,
    route_segment: list,
) -> list[dict]:
    """way 全体から実際に通過した弧だけを、走行順の node 列で返す。"""
    nodes = [
        {"node_id": int(node_id), "coordinate": coordinate}
        for node_id, coordinate in zip(way.get("nodes", []), way.get("geometry", []))
    ]
    if len(nodes) < 2:
        return nodes

    def dist_sq(item: dict, point: list) -> float:
        coordinate = item["coordinate"]
        return (coordinate[0] - point[0]) ** 2 + (coordinate[1] - point[1]) ** 2

    is_closed = nodes[0]["node_id"] == nodes[-1]["node_id"]
    if not is_closed or len(nodes) < 4:
        start = min(range(len(nodes)), key=lambda i: dist_sq(nodes[i], p_start))
        end = min(range(len(nodes)), key=lambda i: dist_sq(nodes[i], p_end))
        if start <= end:
            return nodes[start:end + 1]
        return list(reversed(nodes[end:start + 1]))

    ring = nodes[:-1]
    start = min(range(len(ring)), key=lambda i: dist_sq(ring[i], p_start))
    end = min(range(len(ring)), key=lambda i: dist_sq(ring[i], p_end))

    def forward_arc(first: int, last: int) -> list[dict]:
        if first <= last:
            return ring[first:last + 1]
        return ring[first:] + ring[:last + 1]

    if start == end:
        # 一周して同じ node に戻るケース。両方向の一周を作り、ルート座標の
        # 出現順に合う方を選ぶ。末尾の同一 node も保持する。
        forward = ring[start:] + ring[:start] + [ring[start]]
        reverse = [forward[0], *reversed(forward[1:-1]), forward[-1]]
        return min(
            (forward, reverse),
            key=lambda candidate: _ordered_fit_score(candidate, route_segment),
        )

    forward = forward_arc(start, end)
    reverse = list(reversed(forward_arc(end, start)))
    return min(
        (forward, reverse),
        key=lambda candidate: _arc_fit_score(candidate, route_segment),
    )


def _route_distances(points: list) -> list[float]:
    distances = [0.0]
    for first, second in zip(points, points[1:]):
        distances.append(distances[-1] + _haversine_m(first, second))
    return distances


def _node_route_distances(
    nodes: list[dict],
    points: list,
    cumulative: list[float],
    start_index: int,
    end_index: int,
) -> list[float]:
    """走行順を保ちながら各 OSM node をルート起点距離へ写像する。"""
    first = max(0, min(start_index, len(points) - 1))
    last = max(first + 1, min(end_index, len(points) - 1))
    previous_distance = cumulative[first] - 0.01
    result = []

    for item in nodes:
        candidates = []
        for index in range(first, last):
            ratio, dist_sq = _point_segment_projection(
                item["coordinate"], points[index], points[index + 1]
            )
            distance = cumulative[index] + ratio * _haversine_m(
                points[index], points[index + 1]
            )
            if distance + 0.01 >= previous_distance:
                candidates.append((dist_sq, distance))
        if candidates:
            _, distance = min(candidates, key=lambda value: (value[0], value[1]))
        else:
            distance = previous_distance
        result.append(distance)
        previous_distance = distance
    return result


def build_route_guidance(
    points: list,
    way_id_details: list,
    ways: dict[int, dict],
    tagged_nodes: dict[int, dict],
) -> list[dict]:
    """実走行区間に含まれる stop / level_crossing node を案内に変換する。"""
    if len(points) < 2:
        return []
    cumulative = _route_distances(points)
    guidance = []

    for raw_start, raw_end, raw_way_id in way_id_details:
        start, end, way_id = int(raw_start), int(raw_end), int(raw_way_id)
        if start >= len(points) - 1 or end <= start:
            continue
        end = min(end, len(points) - 1)
        way = ways.get(way_id)
        if not way:
            continue
        route_segment = points[start:end + 1]
        traversed = _traversed_way_nodes(
            way, points[start], points[end], route_segment,
        )
        distances = _node_route_distances(traversed, points, cumulative, start, end)
        for item, distance in zip(traversed, distances):
            node_id = item["node_id"]
            tags = tagged_nodes.get(node_id, {}).get("tags", {})
            guidance_type = None
            if tags.get("highway") == "stop":
                guidance_type = "stop_sign"
            elif tags.get("railway") == "level_crossing":
                guidance_type = "level_crossing"
            if guidance_type is None:
                continue
            lng, lat = item["coordinate"]
            candidate = {
                "type": guidance_type,
                "node_id": node_id,
                "lat": lat,
                "lng": lng,
                "way_id": way_id,
                "distance_from_start_m": round(distance, 1),
            }
            # way 境界で同一通過 node が重複しても1回だけにする。一方、周回で
            # 同じ node を再通過した場合は起点距離が異なるため別件のまま残る。
            duplicate = any(
                existing["node_id"] == node_id
                and abs(existing["distance_from_start_m"] - candidate["distance_from_start_m"]) < 1.0
                for existing in guidance[-2:]
            )
            if not duplicate:
                guidance.append(candidate)

    return sorted(guidance, key=lambda item: item["distance_from_start_m"])


async def get_guidance_for_route(route: dict) -> list[dict]:
    """GraphHopper path 1本について、案内地点を1回のバルク取得で返す。"""
    points = route.get("points", {}).get("coordinates", [])
    details = route.get("details", {}).get("osm_way_id", [])
    if not points or not details:
        logger.warning("案内検出を省略: osm_way_id detail がありません")
        return []

    way_ids = list(dict.fromkeys(int(segment[2]) for segment in details))
    try:
        data = await get_route_guidance_data(way_ids)
    except Exception as exc:
        # 案内用データの不調で経路案内全体を失敗させない。違反判定結果も不変。
        logger.warning("案内用Overpass取得に失敗: %s", exc)
        return []
    return build_route_guidance(points, details, data["ways"], data["tagged_nodes"])
