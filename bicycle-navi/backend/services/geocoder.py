import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "bicycle-navi-research/1.0 (aoyama-gakuin-university-miyaji-lab)"


async def geocode(query: str) -> dict:
    """
    住所・地名を緯度経度に変換する（Nominatim API）
    戻り値: { lat, lng, display_name }
    """
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "jp",
    }
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(NOMINATIM_URL, params=params, headers=headers)
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
