import httpx
import logging

GH_BASE = "http://localhost:8989"
logger = logging.getLogger(__name__)

_BASE_PARAMS = {
    "profile": "bike",
    "locale": "ja",
    "points_encoded": "false",
}

async def get_route(origin_lat, origin_lng, dest_lat, dest_lng):
    """
    GraphHopper にルートを問い合わせる。
    details=osm_way_id を要求し、GH が対応していない場合は details なしで再試行する。
    """
    points = [f"{origin_lat},{origin_lng}", f"{dest_lat},{dest_lng}"]

    # まず osm_way_id details つきで試みる
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{GH_BASE}/route", params={
                **_BASE_PARAMS,
                "point": points,
                "details": ["osm_way_id"],
            })
            resp.raise_for_status()
            return resp.json()
        except (httpx.RemoteProtocolError, httpx.HTTPStatusError) as e:
            logger.warning("details=osm_way_id が拒否されました。details なしで再試行します: %s", e)

        # フォールバック: details なし
        resp = await client.get(f"{GH_BASE}/route", params={
            **_BASE_PARAMS,
            "point": points,
        })
        resp.raise_for_status()
        return resp.json()
