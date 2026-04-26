import httpx
import logging

logger = logging.getLogger(__name__)
GH_BASE = "http://localhost:8989"
# 違反座標を中心とした回避エリアの半径（度単位、約100m）
BLOCK_RADIUS_DEG = 0.001


def _make_block_polygon(lat: float, lng: float) -> list:
    """違反座標を中心とした正方形ポリゴンの座標リストを返す（GeoJSON形式: [lng, lat]）"""
    d = BLOCK_RADIUS_DEG
    return [[
        [lng - d, lat - d],
        [lng + d, lat - d],
        [lng + d, lat + d],
        [lng - d, lat + d],
        [lng - d, lat - d],  # 閉じる
    ]]


async def get_compliant_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    violations: list,
) -> dict:
    """
    違反座標を GH custom_model の areas でブロックし、法規準拠ルートを返す。
    違反がない場合は通常ルートと同じ結果を返す。
    """
    if not violations:
        # 違反なし → 通常リクエストと同じ内容を返す
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{GH_BASE}/route", params={
                "point": [f"{origin_lat},{origin_lng}", f"{dest_lat},{dest_lng}"],
                "profile": "bike",
                "locale": "ja",
                "points_encoded": "false",
            })
            resp.raise_for_status()
            return resp.json()

    # 重複座標を除去（同一地点の違反が複数ある場合）
    seen = set()
    unique_violations = []
    for v in violations:
        key = (round(v["lat"], 4), round(v["lng"], 4))
        if key not in seen:
            seen.add(key)
            unique_violations.append(v)

    # custom_model を構築
    features = []
    priority_rules = []
    for idx, v in enumerate(unique_violations):
        area_id = f"blocked_area_{idx}"
        features.append({
            "type": "Feature",
            "id": area_id,
            "geometry": {
                "type": "Polygon",
                "coordinates": _make_block_polygon(v["lat"], v["lng"]),
            },
        })
        priority_rules.append({
            "if": f"in_{area_id}",
            "multiply_by": "0",
        })

    custom_model = {
        "priority": priority_rules,
        "areas": {
            "type": "FeatureCollection",
            "features": features,
        },
    }

    body = {
        "points": [[origin_lng, origin_lat], [dest_lng, dest_lat]],
        "profile": "bike",
        "locale": "ja",
        "points_encoded": False,
        "ch.disable": True,      # custom_model を使うには CH を無効化する必要がある
        "custom_model": custom_model,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{GH_BASE}/route", json=body)
        if resp.status_code >= 400:
            logger.error(
                "GraphHopper POST /route error %d: %s",
                resp.status_code,
                resp.text,
            )
        resp.raise_for_status()
        return resp.json()
