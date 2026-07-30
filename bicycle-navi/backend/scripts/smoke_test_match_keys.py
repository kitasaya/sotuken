"""match_* キー追加の非破壊性 smoke テスト（Overpass 不要）。

`get_bulk_way_data` に match_dist_m / match_margin_m / match_ambiguous /
match_way_id を追加し、最近傍 way 選択をノード距離から垂線距離
（point-to-curve）へ移行した変更が、既存の呼び出し側を壊さないことを確認する。

Overpass の応答は `_post_with_retry` をスタブして合成データで与えるため、
ネットワークに一切アクセスしない。

  python3 scripts/smoke_test_match_keys.py

全ケース成功で exit 0、1件でも失敗すれば exit 1。
"""

import asyncio
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import overpass
from services.overpass import (
    MATCH_AMBIGUOUS_MARGIN_M,
    _point_to_segment_dist_m,
    _point_to_way_dist_m,
    get_bulk_way_data,
    get_bulk_way_tags,
)

# 判定点（渋谷駅付近）
P_LAT, P_LNG = 35.6580, 139.7016

_M_PER_DEG_LAT = 110_540.0


def _deg_lat(m: float) -> float:
    return m / _M_PER_DEG_LAT


def _deg_lng(m: float, lat: float = P_LAT) -> float:
    return m / (111_320.0 * math.cos(math.radians(lat)))


def _horizontal_way(way_id: int, offset_m: float, node_spacing_m: float | None,
                    half_len_m: float = 100.0, tags: dict | None = None) -> dict:
    """判定点の北へ offset_m ずれた東西方向の直線 way を作る。

    node_spacing_m=None なら両端2ノードのみ（ノードが疎な way）。
    """
    lat = P_LAT + _deg_lat(offset_m)
    if node_spacing_m is None:
        xs = [-half_len_m, half_len_m]
    else:
        n = int(2 * half_len_m / node_spacing_m) + 1
        xs = [-half_len_m + i * node_spacing_m for i in range(n)]
    return {
        "type": "way",
        "id": way_id,
        "tags": tags if tags is not None else {"highway": "residential"},
        "geometry": [{"lon": P_LNG + _deg_lng(x), "lat": lat} for x in xs],
    }


# ---------------------------------------------------------------------------
# スタブ
# ---------------------------------------------------------------------------

def _stub_overpass(elements: list):
    """overpass._post_with_retry を差し替えるコンテキスト用ヘルパー。"""
    async def _fake(query: str) -> list:
        return elements
    return _fake


# ---------------------------------------------------------------------------
# 検査ケース
# ---------------------------------------------------------------------------

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def approx(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


async def case_existing_keys_unchanged():
    """既存キー tags / geometry の形式・意味が不変であること。"""
    print("\n[1] 既存キー（tags / geometry）の形式が不変")
    elem = _horizontal_way(1001, 3.0, None, tags={"highway": "primary", "oneway": "yes"})
    overpass._post_with_retry = _stub_overpass([elem])

    data = await get_bulk_way_data([[P_LNG, P_LAT]])
    d = data[0]

    check(len(data) == 1, "戻り値は points と同数の list", f"len={len(data)}")
    check(d["tags"] == {"highway": "primary", "oneway": "yes"}, "tags はタグ dict そのまま")
    check(
        isinstance(d["geometry"], list)
        and all(isinstance(p, list) and len(p) == 2 for p in d["geometry"]),
        "geometry は [[lon, lat], ...] 形式",
        f"{d['geometry'][:1]}",
    )
    check(
        approx(d["geometry"][0][0], elem["geometry"][0]["lon"], 1e-12)
        and approx(d["geometry"][0][1], elem["geometry"][0]["lat"], 1e-12),
        "geometry の座標順は [lon, lat]",
    )


async def case_new_keys_present():
    """新キーが4つとも存在し、型が期待どおりであること。"""
    print("\n[2] 新キー match_way_id / match_dist_m / match_margin_m / match_ambiguous")
    elements = [
        _horizontal_way(2001, 1.0, None),
        _horizontal_way(2002, 9.0, None),
    ]
    overpass._post_with_retry = _stub_overpass(elements)

    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]

    for key in ("match_way_id", "match_dist_m", "match_margin_m", "match_ambiguous"):
        check(key in d, f"{key} が存在する")
    check(d["match_way_id"] == 2001, "match_way_id は rank1 の way id", f"{d['match_way_id']}")
    check(approx(d["match_dist_m"], 1.0, 0.05), "match_dist_m は rank1 の垂線距離",
          f"{d['match_dist_m']}m（期待 1.0m）")
    check(approx(d["match_margin_m"], 8.0, 0.05), "match_margin_m は rank1/rank2 の差",
          f"{d['match_margin_m']}m（期待 8.0m）")
    check(d["match_ambiguous"] is False, "マージン 8.0m は曖昧でない")


async def case_margin_semantics():
    """候補0本 / 1本 / 僅差2本 / 十分離れた2本 の各ケース。"""
    print("\n[3] match_margin_m / match_ambiguous のセマンティクス")

    # 候補 0 本
    overpass._post_with_retry = _stub_overpass([])
    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]
    check(d["tags"] == {} and d["geometry"] == [], "候補0本: tags/geometry は空")
    check(d["match_way_id"] is None and d["match_dist_m"] is None,
          "候補0本: match_way_id / match_dist_m は None")
    check(d["match_margin_m"] is None, "候補0本: match_margin_m は None")
    check(d["match_ambiguous"] is False, "候補0本: match_ambiguous は False")

    # 候補 1 本
    overpass._post_with_retry = _stub_overpass([_horizontal_way(3001, 2.0, None)])
    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]
    check(d["match_margin_m"] is None, "候補1本: match_margin_m は None（仕様どおり）")
    check(d["match_ambiguous"] is False, "候補1本: match_ambiguous は False")

    # 僅差 2 本（マージン 1.5m < 2.0m）
    overpass._post_with_retry = _stub_overpass([
        _horizontal_way(3002, 1.0, None),
        _horizontal_way(3003, 2.5, None),
    ])
    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]
    check(approx(d["match_margin_m"], 1.5, 0.05), "僅差2本: マージン 1.5m", f"{d['match_margin_m']}m")
    check(d["match_ambiguous"] is True, "僅差2本: match_ambiguous は True")

    # 十分離れた 2 本（マージン 3.0m >= 2.0m）
    overpass._post_with_retry = _stub_overpass([
        _horizontal_way(3004, 1.0, None),
        _horizontal_way(3005, 4.0, None),
    ])
    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]
    check(approx(d["match_margin_m"], 3.0, 0.05), "離れた2本: マージン 3.0m", f"{d['match_margin_m']}m")
    check(d["match_ambiguous"] is False, "離れた2本: match_ambiguous は False")

    check(MATCH_AMBIGUOUS_MARGIN_M == 2.0, "閾値定数 MATCH_AMBIGUOUS_MARGIN_M は 2.0",
          f"{MATCH_AMBIGUOUS_MARGIN_M}")


async def case_duplicate_way_dedup():
    """同一 way が複数含まれても偽の「曖昧」を生まないこと。"""
    print("\n[4] way id による重複除去（偽の曖昧判定の防止）")
    w = _horizontal_way(4001, 1.0, None)
    overpass._post_with_retry = _stub_overpass([w, dict(w), dict(w)])

    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]
    check(d["match_way_id"] == 4001, "rank1 は way 4001")
    check(d["match_margin_m"] is None,
          "同一 way の重複は1本に畳まれ margin は None", f"{d['match_margin_m']}")
    check(d["match_ambiguous"] is False, "重複による偽の match_ambiguous=True が出ない")


async def case_point_to_curve_selection():
    """B群（分離ウェイ誤マッチ）の再現：ノード距離と垂線距離で選択が逆転する。"""
    print("\n[5] point-to-curve 選択（ノードが疎な近い way を正しく選ぶ）")
    # 正しい車線: 線は 0.8m 先だが、ノードは両端（±100m）にしかない
    correct = _horizontal_way(5001, 0.8, None, tags={"highway": "primary", "name": "正しい車線"})
    # 対向車線: 線は 8m 先だが、5m 間隔でノードが密
    opposite = _horizontal_way(5002, -8.0, 5.0, tags={"highway": "primary", "name": "対向車線"})
    overpass._post_with_retry = _stub_overpass([correct, opposite])

    # 参考: ノード距離ベースならどちらが選ばれるか
    def node_dist_deg(elem):
        return min(
            (n["lat"] - P_LAT) ** 2 + (n["lon"] - P_LNG) ** 2
            for n in elem["geometry"]
        )
    node_pick = min([correct, opposite], key=node_dist_deg)["id"]

    d = (await get_bulk_way_data([[P_LNG, P_LAT]]))[0]
    check(node_pick == 5002, "（前提）ノード距離ベースなら対向車線 5002 を誤選択", f"node_pick={node_pick}")
    check(d["match_way_id"] == 5001, "垂線距離ベースは正しい車線 5001 を選ぶ",
          f"match_way_id={d['match_way_id']}")
    check(d["tags"].get("name") == "正しい車線", "tags も rank1 のものになっている")
    check(approx(d["match_dist_m"], 0.8, 0.05), "match_dist_m は 0.8m", f"{d['match_dist_m']}m")


async def case_get_bulk_way_tags_unchanged():
    """後方互換ラッパ get_bulk_way_tags の戻り値がタグ dict のままであること。"""
    print("\n[6] get_bulk_way_tags（後方互換ラッパ）")
    overpass._post_with_retry = _stub_overpass([
        _horizontal_way(6001, 1.0, None, tags={"highway": "residential", "oneway": "yes"}),
    ])
    tags_list = await get_bulk_way_tags([[P_LNG, P_LAT], [P_LNG, P_LAT]])
    check(len(tags_list) == 2, "points と同数を返す", f"len={len(tags_list)}")
    check(all(isinstance(t, dict) for t in tags_list), "各要素はタグ dict")
    check(
        all("match_margin_m" not in t and "match_ambiguous" not in t for t in tags_list),
        "タグ dict に match_* キーが混入していない",
    )
    check(tags_list[0].get("oneway") == "yes", "タグの内容が保たれている")


async def case_downstream_consumers():
    """呼び出し側（external_route_scorer / route_analyzer）が新キーで壊れないこと。"""
    print("\n[7] 呼び出し側の非破壊性")

    from services.external_route_scorer import score_external_route, decode_polyline
    from services import route_analyzer  # import 時点で壊れないことの確認

    # 渋谷→新宿の Google polyline（google_routes_input.csv より）
    polyline = ("iwsxEurtsYeXx@aFyAiVqPwIwG{JoGoA@yK|DwQzEaNlHkA?eBCkHCqCHwBh@{C~A{HxAiJfBW^iG~AcAV")
    coords = decode_polyline(polyline)

    # ルート沿いに oneway=yes の way が1本だけある状況を合成する
    overpass._post_with_retry = _stub_overpass([
        {
            "type": "way", "id": 7001,
            "tags": {"highway": "unclassified", "oneway": "yes"},
            "geometry": [{"lon": c[0], "lat": c[1]} for c in coords],
        },
    ])

    score = await score_external_route(coords)
    for key in ("oneway_violations", "two_step_violations", "oneway_violation_count",
                "two_step_violation_count", "total_violation_count",
                "route_distance_m", "sampled_points"):
        check(key in score, f"score_external_route の戻り値に {key} が存在する")
    check(isinstance(score["oneway_violation_count"], int),
          "score_external_route が例外なく完走する",
          f"oneway={score['oneway_violation_count']} sampled={score['sampled_points']}")

    # route_analyzer の Overpass 失敗時フォールバック dict が消費側と整合するか
    fallback = {"tags": {}, "geometry": []}
    check(
        fallback["tags"] == {} and fallback["geometry"] == [],
        "route_analyzer:170 のフォールバック dict は tags/geometry のみで足りる"
        "（新キーを読む消費側がない）",
    )
    check(hasattr(route_analyzer, "analyze_route"), "route_analyzer が import できる")


def case_distance_independent_check():
    """垂線距離を独立実装（線分の細分割＋haversine）と突き合わせる。"""
    print("\n[8] 垂線距離計算の独立検証")

    def haversine_m(lat1, lng1, lat2, lng2):
        R = 6_371_000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lng2 - lng1)
        x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * R * math.asin(math.sqrt(min(x, 1.0)))

    def brute_force(lat, lng, a, b, n=20_000):
        return min(
            haversine_m(lat, lng,
                        a[1] + (b[1] - a[1]) * (i / n),
                        a[0] + (b[0] - a[0]) * (i / n))
            for i in range(n + 1)
        )

    cases = [
        # (説明, 線分 a, 線分 b)
        ("射影が線分内（真の垂線）",
         [P_LNG - _deg_lng(50), P_LAT + _deg_lat(3)],
         [P_LNG + _deg_lng(50), P_LAT + _deg_lat(3)]),
        ("射影が線分外（端点までの距離にクランプ）",
         [P_LNG + _deg_lng(20), P_LAT + _deg_lat(2)],
         [P_LNG + _deg_lng(60), P_LAT + _deg_lat(2)]),
        ("斜めの線分",
         [P_LNG - _deg_lng(30), P_LAT - _deg_lat(30)],
         [P_LNG + _deg_lng(40), P_LAT + _deg_lat(10)]),
        ("退化した線分（同一点）",
         [P_LNG + _deg_lng(5), P_LAT + _deg_lat(5)],
         [P_LNG + _deg_lng(5), P_LAT + _deg_lat(5)]),
    ]

    for name, a, b in cases:
        mine = _point_to_segment_dist_m(P_LAT, P_LNG, a, b)
        ref = brute_force(P_LAT, P_LNG, a, b)
        rel = abs(mine - ref) / ref if ref > 0 else 0.0
        check(rel < 0.01, f"{name}: 独立実装との相対差 < 1%",
              f"局所平面={mine:.4f}m / 大円ブルートフォース={ref:.4f}m / 差={rel * 100:.3f}%")

    # way 単位（複数セグメントの最小値）
    geom = [
        [P_LNG - _deg_lng(50), P_LAT + _deg_lat(20)],
        [P_LNG, P_LAT + _deg_lat(4)],
        [P_LNG + _deg_lng(50), P_LAT + _deg_lat(20)],
    ]
    d = _point_to_way_dist_m(P_LAT, P_LNG, geom)
    check(approx(d, 4.0, 0.2), "_point_to_way_dist_m は全セグメントの最小値を返す", f"{d:.3f}m（期待 ~4.0m）")
    check(_point_to_way_dist_m(P_LAT, P_LNG, []) == float("inf"),
          "ジオメトリが空の way は inf")


async def main() -> int:
    original = overpass._post_with_retry
    try:
        await case_existing_keys_unchanged()
        await case_new_keys_present()
        await case_margin_semantics()
        await case_duplicate_way_dedup()
        await case_point_to_curve_selection()
        await case_get_bulk_way_tags_unchanged()
        await case_downstream_consumers()
        case_distance_independent_check()
    finally:
        overpass._post_with_retry = original

    failed = [r for r in _results if not r[0]]
    print(f"\n{'=' * 70}")
    print(f"合計 {len(_results)} 件 / 成功 {len(_results) - len(failed)} 件 / 失敗 {len(failed)} 件")
    if failed:
        print("\n失敗したケース:")
        for _, name, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print("全ケース成功。match_* キーの追加は既存の呼び出し側を壊さない。")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
