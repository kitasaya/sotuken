import httpx

GH_BASE = "http://localhost:8989"

async def get_route(origin_lat, origin_lng, dest_lat, dest_lng):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GH_BASE}/route", params={
            "point": [f"{origin_lat},{origin_lng}", f"{dest_lat},{dest_lng}"],
            "profile": "bike",
            "locale": "ja",
            "points_encoded": "false",
        })
        resp.raise_for_status()
        return resp.json()
