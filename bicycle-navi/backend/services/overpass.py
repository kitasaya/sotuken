import contextvars
import httpx
import logging
from collections import OrderedDict
from typing import Optional
from services.http_clients import get_client

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

# Overpass 公開インスタンスは User-Agent の指定がないと 406 等で拒否することがある。
# 学術用途であることを明示する識別子を付与する。
_OVERPASS_HEADERS = {
    "User-Agent": "bicycle-navi-research/1.0 (Aoyama Gakuin University; academic)",
    "Accept": "application/json",
}

# 1 リクエスト内で Overpass の全エンドポイントが失敗した場合、同じリクエスト内の
# 後続の Overpass 呼び出しを即座にスキップする（合計 timeout 時間を圧縮する）。
# FastAPI/Starlette は各リクエストを別 asyncio タスクで実行するため、
# ContextVar はリクエストごとに独立し、リクエストをまたいでフラグが漏れない。
_overpass_circuit_broken: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_overpass_circuit_broken", default=False
)

# ---------------------------------------------------------------------------
# way_id ベース取得のプロセス内 LRU キャッシュ（P1: Overpass キャッシュ層）
# ---------------------------------------------------------------------------
# way 1 件あたり ~1KB として 10,000 件で約 10MB 程度を想定
_WAY_CACHE_MAX = 10000
_way_cache: "OrderedDict[int, dict]" = OrderedDict()


def _way_cache_get(way_id: int) -> Optional[dict]:
    """LRU としてアクセスを末尾に移動させつつエントリを返す。未キャッシュなら None。"""
    entry = _way_cache.get(way_id)
    if entry is None:
        return None
    _way_cache.move_to_end(way_id)
    return entry


def _way_cache_put(way_id: int, entry: dict) -> None:
    """キャッシュに格納し、容量超過分は LRU で破棄する。"""
    if way_id in _way_cache:
        _way_cache.move_to_end(way_id)
        _way_cache[way_id] = entry
        return
    _way_cache[way_id] = entry
    if len(_way_cache) > _WAY_CACHE_MAX:
        _way_cache.popitem(last=False)


def clear_way_cache() -> None:
    """テスト・運用補助用。プロセス内 LRU キャッシュを空にする。"""
    _way_cache.clear()


async def get_way_tags(lat: float, lng: float, radius: int = 20) -> dict:
    """指定座標付近の道路タグを取得する（単一座標用・後方互換）"""
    query = f"""
    [out:json][timeout:10];
    way(around:{radius},{lat},{lng})[highway];
    out tags;
    """
    client = get_client()
    resp = await client.post(
        OVERPASS_URL, data={"data": query},
        headers=_OVERPASS_HEADERS, timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    if data["elements"]:
        return data["elements"][0].get("tags", {})
    return {}


async def _post_with_retry(query: str) -> list:
    """
    Overpass API へ POST する。失敗したら次のエンドポイントへ即切り替え（リトライなし）。
    すべて失敗した場合は空リストを返す。最悪でも endpoints × timeout 秒で終わる。

    同一リクエスト内で一度全エンドポイントが失敗した場合は、後続の呼び出しを
    即座にスキップする（_overpass_circuit_broken フラグ）。例えば by-ID 取得が
    失敗してフォールバックで点ベース取得を呼ぶケースで、無意味に再度
    timeout×3 を待たないようにする。

    共有 httpx.AsyncClient を利用してコネクションプールを再利用する。
    """
    if _overpass_circuit_broken.get():
        logger.info("Overpass: 同一リクエスト内で既に全失敗済み、スキップ")
        return []

    client = get_client()
    for url in OVERPASS_ENDPOINTS:
        try:
            resp = await client.post(
                url, data={"data": query},
                headers=_OVERPASS_HEADERS,
                timeout=_PER_ENDPOINT_TIMEOUT,
            )
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
    _overpass_circuit_broken.set(True)
    return []


async def get_way_tags_by_ids(way_ids: list[int]) -> dict[int, dict]:
    """
    OSM way ID リストから直接タグと geometry を取得する（edge_id ベース判定用）。

    戻り値: {way_id: {"tags": tags_dict, "geometry": [[lon, lat], ...]}}

    プロセス内 LRU キャッシュ（_way_cache）でヒットした way_id は Overpass を
    呼ばずに返す。Overpass 呼び出しは未キャッシュ分のみ。Overpass が返さなかった
    way_id は負キャッシュしない（次回再取得する）。これにより、全エンドポイント
    失敗時に呼び出し側がフォールバック判定（空 dict）できる挙動を維持する。
    """
    if not way_ids:
        return {}

    result: dict[int, dict] = {}
    uncached_ids: list[int] = []
    for wid in way_ids:
        cached = _way_cache_get(wid)
        if cached is not None:
            result[wid] = cached
        else:
            uncached_ids.append(wid)

    cache_hit = len(result)
    cache_miss = len(uncached_ids)
    overpass_called = 0

    if uncached_ids:
        ids_str = ",".join(str(i) for i in uncached_ids)
        query = f"""[out:json][timeout:30];
way(id:{ids_str});
out tags geom;
"""
        overpass_called = 1
        elements = await _post_with_retry(query)
        for elem in elements:
            if "id" not in elem:
                continue
            wid = elem["id"]
            tags = elem.get("tags", {})
            geometry = [[n["lon"], n["lat"]] for n in elem.get("geometry", [])]
            entry = {"tags": tags, "geometry": geometry}
            _way_cache_put(wid, entry)
            result[wid] = entry

    logger.info(
        "Overpass by-ID: cache_hit=%d cache_miss=%d overpass_called=%d cache_size=%d",
        cache_hit, cache_miss, overpass_called, len(_way_cache),
    )
    return result


async def get_bulk_way_data(points: list, radius: int = 20) -> list[dict]:
    """
    複数座標を1回のOverpassクエリでまとめて取得する（タグ＋ジオメトリ付き）。

    points: [[lng, lat], ...] 形式（GeoJSON座標順）
    戻り値: 各座標に対応する {"tags": {...}, "geometry": [[lon, lat], ...]} のリスト

    Union構文で全座標を一括取得し、各座標に最も近いノードを持つ way を選択する。
    中心点ではなく実ジオメトリの最近傍ノードで選択するため、
    並行する対向車線 way の誤選択を低減できる。
    """
    if not points:
        return []

    parts = "\n".join(
        f"  way(around:{radius},{lat},{lng})[highway];"
        for lng, lat in points
    )
    query = f"""[out:json][timeout:30];
(
{parts}
);
out geom tags;
"""

    elements = await _post_with_retry(query)

    result = []
    for lng, lat in points:
        best: dict = {"tags": {}, "geometry": []}
        best_dist = float("inf")
        for elem in elements:
            geom_nodes = elem.get("geometry", [])
            # 実ジオメトリの各ノードまでの最短距離で最近傍 way を選択
            for node in geom_nodes:
                d = (node["lat"] - lat) ** 2 + (node["lon"] - lng) ** 2
                if d < best_dist:
                    best_dist = d
                    best = {
                        "tags": elem.get("tags", {}),
                        "geometry": [[n["lon"], n["lat"]] for n in geom_nodes],
                    }
        result.append(best)

    return result


async def get_bulk_way_tags(points: list, radius: int = 20) -> list[dict]:
    """
    複数座標を1回のOverpassクエリでまとめて取得する（タグのみ・後方互換）。

    points: [[lng, lat], ...] 形式（GeoJSON座標順）
    戻り値: 各座標に対応するタグのリスト（points と同順）
    """
    data = await get_bulk_way_data(points, radius)
    return [d["tags"] for d in data]
