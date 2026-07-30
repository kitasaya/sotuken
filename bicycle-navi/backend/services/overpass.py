import contextvars
import httpx
import logging
import math
from collections import OrderedDict
from typing import Optional
from services.http_clients import get_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 最近傍 way マッチングの曖昧さ判定（診断情報）
# ---------------------------------------------------------------------------
# 最近傍探索の1位候補と2位候補の垂線距離差（マージン）がこの値未満の地点は、
# どちらの way を選ぶかが不安定であり「原理的に判定不能」として扱う。
#
# 根拠（RESEARCH.md 21.10 / 24.3節・investigation_perpendicular_side_effects.md の実測）:
#   想定外の判定変化を示した4地点 … マージン 0.50〜1.74m（全て 2m 未満）
#   正常に動作した10地点           … マージン 2.75m 以上
# 両者の間に明確な断絶があるため、その間を取って 2.0m を閾値とする。
#
# なお「進行方向と一致する候補を選ぶ」タイブレークは採用しない。測定対象が
# 「進行方向が逆か（＝逆走）」であるため、方向で候補を選ぶと答えを使って答えを
# 決める循環論法になる（RESEARCH.md 21.11節）。マージンはあくまで診断情報として
# 記録するのみで、候補の選択には一切影響させない。
MATCH_AMBIGUOUS_MARGIN_M = 2.0

# 局所平面近似で使う 1 度あたりの距離（m）
_M_PER_DEG_LAT = 110_540.0
_M_PER_DEG_LNG_EQUATOR = 111_320.0

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


# ---------------------------------------------------------------------------
# 垂線距離ベースの最近傍 way 選択（point-to-curve matching）
# ---------------------------------------------------------------------------
# 「点から way を構成するノード（頂点）までの距離」で最近傍 way を決めると、
# ノード密度の不均一性により誤マッチが生じる。OSM 上で上下線が分離マッピング
# された幹線道路では、線そのものは近いがノードが疎な正しい車線ではなく、
# 線は遠いがノードが密な対向車線が選ばれる。誤選択先が対向車線であるため、
# この誤マッチは必ず「逆走」として判定され、系統的に一方通行違反の偽陽性を
# 生成する（RESEARCH.md 21.5〜21.7節で実データにより実証）。
#
# map matching の用語では point-to-point matching → point-to-curve matching への
# 移行にあたる（White et al. 2000）。


def _point_to_segment_dist_m(lat: float, lng: float, a: list, b: list) -> float:
    """点 (lat, lng) から線分 a-b への垂線距離（m）。a, b = [lon, lat]。

    局所平面近似（判定点の緯度で経度スケールを補正）で計算する。射影が線分の
    外に落ちる場合は t を [0, 1] にクランプし、近い方の端点までの距離を返す。
    """
    kx = _M_PER_DEG_LNG_EQUATOR * math.cos(math.radians(lat))
    ky = _M_PER_DEG_LAT

    ax, ay = (a[0] - lng) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lng) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay

    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(ax, ay)

    # 原点（判定点）を線分 a-b に射影したパラメータ t を [0, 1] にクランプ
    t = -(ax * dx + ay * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(ax + t * dx, ay + t * dy)


def _point_to_way_dist_m(lat: float, lng: float, geometry: list) -> float:
    """点 (lat, lng) から way ジオメトリへの最短垂線距離（m）。

    geometry: [[lon, lat], ...]。ノードが1個しかない way は点距離で代替する。
    ノードが無い way は inf を返す（候補から実質的に除外される）。
    """
    if not geometry:
        return float("inf")
    if len(geometry) == 1:
        return _point_to_segment_dist_m(lat, lng, geometry[0], geometry[0])
    return min(
        _point_to_segment_dist_m(lat, lng, geometry[i], geometry[i + 1])
        for i in range(len(geometry) - 1)
    )


def _elem_geometry(elem: dict) -> list:
    """Overpass の way element を [[lon, lat], ...] 形式のジオメトリに変換する。"""
    return [[n["lon"], n["lat"]] for n in elem.get("geometry", [])]


def _rank_way_candidates(lat: float, lng: float, elements: list) -> list[tuple[float, dict]]:
    """判定点に対する way 候補を垂線距離の昇順で返す。

    戻り値: [(垂線距離m, elem), ...]

    **way id で重複除去する。** Union クエリの結果に同一 way が複数含まれると
    rank2 が rank1 と同じ way になり、マージン 0 の偽の「曖昧」判定を生むため。

    ドライラン・検証スクリプトからも import して使う（診断ロジックを一箇所に集約）。
    """
    best_by_id: dict = {}
    unidentified: list[tuple[float, dict]] = []

    for elem in elements:
        geometry = _elem_geometry(elem)
        if not geometry:
            continue
        d = _point_to_way_dist_m(lat, lng, geometry)
        if d == float("inf"):
            continue
        wid = elem.get("id")
        if wid is None:
            unidentified.append((d, elem))
            continue
        prev = best_by_id.get(wid)
        if prev is None or d < prev[0]:
            best_by_id[wid] = (d, elem)

    ranked = list(best_by_id.values()) + unidentified
    ranked.sort(key=lambda t: t[0])
    return ranked


async def get_bulk_way_data(points: list, radius: int = 20) -> list[dict]:
    """
    複数座標を1回のOverpassクエリでまとめて取得する（タグ＋ジオメトリ付き）。

    points: [[lng, lat], ...] 形式（GeoJSON座標順）
    戻り値: 各座標に対応する以下の dict のリスト（points と同順）

        {
          "tags": {...},
          "geometry": [[lon, lat], ...],
          "match_way_id": int | None,      # rank1 の way id
          "match_dist_m": float | None,    # rank1 の垂線距離（m）
          "match_margin_m": float | None,  # rank1 と rank2 の垂線距離差（m）
          "match_ambiguous": bool,         # マージンが MATCH_AMBIGUOUS_MARGIN_M 未満
        }

    Union構文で全座標を一括取得し、各座標について **点から way の線分への垂線距離**
    が最小の way を選択する（point-to-curve matching）。ノード距離ではなく線分への
    距離を使うため、ノードが疎な正しい車線を取りこぼして対向車線を誤選択する
    問題を避けられる。

    match_* キーは診断情報であり、選択そのものには影響しない。候補が1本以下の
    場合 match_margin_m は None、match_ambiguous は False になる。候補が0本の場合は
    tags/geometry が空で match_way_id / match_dist_m も None。
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
    ambiguous_count = 0
    for lng, lat in points:
        ranked = _rank_way_candidates(lat, lng, elements)

        if not ranked:
            result.append({
                "tags": {}, "geometry": [],
                "match_way_id": None, "match_dist_m": None,
                "match_margin_m": None, "match_ambiguous": False,
            })
            continue

        rank1_dist, rank1_elem = ranked[0]
        margin = (ranked[1][0] - rank1_dist) if len(ranked) >= 2 else None
        ambiguous = margin is not None and margin < MATCH_AMBIGUOUS_MARGIN_M
        if ambiguous:
            ambiguous_count += 1

        result.append({
            "tags": rank1_elem.get("tags", {}),
            "geometry": _elem_geometry(rank1_elem),
            "match_way_id": rank1_elem.get("id"),
            "match_dist_m": round(rank1_dist, 3),
            "match_margin_m": None if margin is None else round(margin, 3),
            "match_ambiguous": ambiguous,
        })

    logger.info(
        "Overpass bulk: points=%d candidates=%d match_ambiguous=%d (margin<%.1fm)",
        len(points), len(elements), ambiguous_count, MATCH_AMBIGUOUS_MARGIN_M,
    )
    return result


async def get_bulk_way_tags(points: list, radius: int = 20) -> list[dict]:
    """
    複数座標を1回のOverpassクエリでまとめて取得する（タグのみ・後方互換）。

    points: [[lng, lat], ...] 形式（GeoJSON座標順）
    戻り値: 各座標に対応するタグのリスト（points と同順）
    """
    data = await get_bulk_way_data(points, radius)
    return [d["tags"] for d in data]
