from services.http_clients import get_client

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "bicycle-navi-research/1.0 (aoyama-gakuin-university-miyaji-lab)"


async def geocode(query: str) -> dict:
    """
    住所・地名を緯度経度に変換する（Nominatim API）
    戻り値: { lat, lng, display_name }

    共有 httpx.AsyncClient（コネクションプール）を利用する。
    """
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "jp",
    }
    headers = {"User-Agent": USER_AGENT}

    client = get_client()
    resp = await client.get(
        NOMINATIM_URL, params=params, headers=headers, timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data:
        raise ValueError(f"住所が見つかりませんでした: {query}")

    result = data[0]
    return {
        "lat": float(result["lat"]),
        "lng": float(result["lon"]),
        "display_name": result["display_name"],
    }
