import httpx
import logging

logger = logging.getLogger(__name__)

# 順番に試すエンドポイント（メイン → 代替1 → 代替2）
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS_URL = OVERPASS_ENDPOINTS[0]  # 後方互換用
# 1エンドポイントあたりの最大待機時間（秒）
_PER_ENDPOINT_TIMEOUT = 10.0


async def get_way_tags(lat: float, lng: float, radius: int = 20) -> dict:
    """指定座標付近の道路タグを取得する（単一座標用・後方互換）"""
    query = f"""
    [out:json][timeout:10];
    way(around:{radius},{lat},{lng})[highway];
    out tags;
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(OVERPASS_URL, data={"data": query})
        resp.raise_for_status()
        data = resp.json()
        if data["elements"]:
            return data["elements"][0].get("tags", {})
        return {}


async def _post_with_retry(query: str) -> list:
    """
    Overpass API へ POST する。失敗したら次のエンドポイントへ即切り替え（リトライなし）。
    すべて失敗した場合は空リストを返す。最悪でも endpoints × timeout 秒で終わる。
    """
    for url in OVERPASS_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=_PER_ENDPOINT_TIMEOUT) as client:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                logger.info("Overpass 成功: %s", url)
                return resp.json().get("elements", [])
        except httpx.HTTPStatusError as e:
            logger.warning("Overpass %s → %d, 次のエンドポイントへ", url, e.response.status_code)
        except httpx.TimeoutException:
            logger.warning("Overpass %s → タイムアウト(%ds), 次のエンドポイントへ", url, int(_PER_ENDPOINT_TIMEOUT))
        except Exception as e:
            logger.warning("Overpass %s → エラー(%s), 次のエンドポイントへ", url, e)
    logger.error("Overpass: 全エンドポイントで失敗。空結果を返します。")
    return []


async def get_way_tags_by_ids(way_ids: list[int]) -> dict[int, dict]:
    """
    OSM way ID リストから直接タグと geometry を取得する（edge_id ベース判定用）。

    戻り値: {way_id: {"tags": tags_dict, "geometry": [[lon, lat], ...]}}
    """
    if not way_ids:
        return {}
    ids_str = ",".join(str(i) for i in way_ids)
    query = f"""[out:json][timeout:30];
way(id:{ids_str});
out tags geom;
"""
    elements = await _post_with_retry(query)
    result = {}
    for elem in elements:
        if "id" not in elem:
            continue
        tags = elem.get("tags", {})
        geometry = [[n["lon"], n["lat"]] for n in elem.get("geometry", [])]
        result[elem["id"]] = {"tags": tags, "geometry": geometry}
    return result


async def get_bulk_way_tags(points: list, radius: int = 20) -> list[dict]:
    """
    複数座標を1回のOverpassクエリでまとめて取得する。

    points: [[lng, lat], ...] 形式（GeoJSON座標順）
    戻り値: 各座標に対応するタグのリスト（points と同順）

    Union構文で全座標を一括取得し、各座標に最も中心が近い way のタグを返す。
    """
    if not points:
        return []

    # Union クエリを構築（各座標を中心とした around クエリを結合）
    parts = "\n".join(
        f"  way(around:{radius},{lat},{lng})[highway];"
        for lng, lat in points
    )
    query = f"""[out:json][timeout:30];
(
{parts}
);
out center tags;
"""

    elements = await _post_with_retry(query)

    # 各座標に対して、返ってきた way の中から center が最も近いものを対応付ける
    result = []
    for lng, lat in points:
        best_tags: dict = {}
        best_dist = float("inf")
        for elem in elements:
            center = elem.get("center", {})
            clat = center.get("lat")
            clng = center.get("lon")
            if clat is None or clng is None:
                continue
            dist = (clat - lat) ** 2 + (clng - lng) ** 2
            if dist < best_dist:
                best_dist = dist
                best_tags = elem.get("tags", {})
        result.append(best_tags)

    return result
