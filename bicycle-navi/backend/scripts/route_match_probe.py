"""外部ルートの各判定点について、最近傍 way のマッチ候補を診断的に取り出す。

`external_route_scorer.score_external_route` と **同一のパイプライン**
（40m 等間隔サンプリング → 半径20mのUnionクエリ → 最近傍way → 向き整合チェック
→ check_oneway_violation）を再現しつつ、採点器が捨てている中間情報
（rank1/rank2 の way_id・垂線距離・マージン・ノード距離での rank1）を返す。

`scripts/dryrun_nonroad_filter.py` と `scripts/verify_match_margin.py` の
共通基盤。採点ロジックそのものは `services/` 側の関数をそのまま呼び、
このモジュールでは複製しない。

**方向情報（travel_vector との角度）は診断列として記録するだけで、候補の選択には
一切使わない。** 測定対象が「進行方向が逆か（＝逆走）」であるため、方向で候補を
選ぶと循環論法になる（RESEARCH.md 21.11節）。
"""

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import overpass  # モジュール経由で参照（テスト時に差し替え可能にするため）
from services.overpass import (
    MATCH_AMBIGUOUS_MARGIN_M,
    _elem_geometry,
    _rank_way_candidates,
)
from services.external_route_scorer import (
    _is_way_misaligned,
    _resample_by_distance,
    _travel_vector_at,
    _turn_angle_deg,
    _way_axis_vector,
    decode_polyline,
)
from services.law_checker import check_oneway_violation

DEFAULT_RADIUS = 20
DEFAULT_SAMPLE_INTERVAL_M = 40.0


@dataclass
class Candidate:
    """判定点に対する way 候補1本ぶんの診断情報。"""
    way_id: int | None
    dist_m: float
    tags: dict
    geometry: list

    @property
    def highway(self) -> str:
        return self.tags.get("highway", "")

    @property
    def oneway(self) -> str:
        return self.tags.get("oneway", "")

    @property
    def name(self) -> str:
        return self.tags.get("name", "")

    @property
    def bicycle(self) -> str:
        return self.tags.get("bicycle", "")


@dataclass
class PointProbe:
    """判定点1つぶんの診断結果。"""
    label: str
    sample_idx: int          # サンプリング後の連番（0始まり）
    route_idx: int           # 元 coords 上のインデックス
    lat: float
    lng: float
    travel_vector: list
    candidates: list[Candidate] = field(default_factory=list)
    node_rank1_way_id: int | None = None   # ノード距離ベース（垂線距離修正前）の rank1

    @property
    def rank1(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def rank2(self) -> Candidate | None:
        return self.candidates[1] if len(self.candidates) >= 2 else None

    @property
    def match_dist_m(self) -> float | None:
        return None if self.rank1 is None else round(self.rank1.dist_m, 3)

    @property
    def match_margin_m(self) -> float | None:
        if self.rank1 is None or self.rank2 is None:
            return None
        return round(self.rank2.dist_m - self.rank1.dist_m, 3)

    @property
    def match_ambiguous(self) -> bool:
        m = self.match_margin_m
        return m is not None and m < MATCH_AMBIGUOUS_MARGIN_M

    def angle_to_travel_deg(self, cand: Candidate | None) -> float | None:
        """way 軸と travel_vector のなす角（度）。**診断専用**。

        180度に近いほど逆走、90度前後なら交差道路。候補の選択には使わない。
        """
        if cand is None:
            return None
        axis = _way_axis_vector(cand.geometry)
        tv = self.travel_vector
        if axis is None or (tv[0] == 0 and tv[1] == 0):
            return None
        return round(_turn_angle_deg(axis, tv), 1)


def _node_dist_rank1(lat: float, lng: float, elements: list) -> int | None:
    """ノード距離ベース（垂線距離修正**前**の実装）で選ばれる way id を返す。

    想定外4地点は垂線距離修正後の rank1 が別の地物に変わっているため、
    群の同定にはこの「修正前に何を選んでいたか」が必要になる。
    修正前の実装（度単位の二乗距離）をそのまま再現する。
    """
    best_id, best_dist = None, float("inf")
    for elem in elements:
        for node in elem.get("geometry", []):
            d = (node["lat"] - lat) ** 2 + (node["lon"] - lng) ** 2
            if d < best_dist:
                best_dist = d
                best_id = elem.get("id")
    return best_id


async def fetch_union_elements(points: list, radius: int = DEFAULT_RADIUS) -> list:
    """`get_bulk_way_data` と同一の Union クエリで候補 way を一括取得する。

    points: [[lng, lat], ...]
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
    return await overpass._post_with_retry(query)


async def probe_route(
    label: str,
    polyline: str,
    *,
    radius: int = DEFAULT_RADIUS,
    sample_interval_m: float = DEFAULT_SAMPLE_INTERVAL_M,
) -> tuple[list[PointProbe], list]:
    """1ルートを採点パイプラインと同じ手順で走らせ、判定点ごとの診断情報を返す。

    戻り値: (判定点のリスト, 元の coords)
    """
    coords = decode_polyline(polyline)
    if len(coords) < 2:
        return [], coords

    sampled_idx = _resample_by_distance(coords, sample_interval_m)
    sampled_points = [coords[i] for i in sampled_idx]
    travel_vectors = [_travel_vector_at(coords, i) for i in sampled_idx]

    elements = await fetch_union_elements(sampled_points, radius)

    probes: list[PointProbe] = []
    for n, (route_idx, point, tv) in enumerate(zip(sampled_idx, sampled_points, travel_vectors)):
        lng, lat = point[0], point[1]
        ranked = _rank_way_candidates(lat, lng, elements)
        probes.append(PointProbe(
            label=label,
            sample_idx=n,
            route_idx=route_idx,
            lat=lat,
            lng=lng,
            travel_vector=tv,
            candidates=[
                Candidate(
                    way_id=elem.get("id"),
                    dist_m=dist,
                    tags=elem.get("tags", {}),
                    geometry=_elem_geometry(elem),
                )
                for dist, elem in ranked
            ],
            node_rank1_way_id=_node_dist_rank1(lat, lng, elements),
        ))
    return probes, coords


async def is_oneway_violation(probe: PointProbe, cand: Candidate | None) -> tuple[bool, bool, float]:
    """指定候補を採用した場合に oneway 違反として検出されるかを判定する。

    `score_external_route` と同一の手順（向き整合チェック → check_oneway_violation）
    をそのまま辿る。戻り値: (違反として検出されたか, 向き整合で弾かれたか, confidence)
    """
    if cand is None:
        return False, False, 0.0

    misaligned = _is_way_misaligned(cand.geometry, probe.travel_vector)
    tags = {} if misaligned else cand.tags

    violations = await check_oneway_violation(
        [[probe.lng, probe.lat]],
        tags_list=[tags],
        geometries=[cand.geometry],
        travel_vectors=[probe.travel_vector],
    )
    detected = len(violations) > 0
    confidence = violations[0]["confidence"] if detected else 0.0
    return detected, misaligned, confidence


def fmt(value, nd: int = 2, dash: str = "—") -> str:
    """Markdown 表向けの値整形。None は dash。"""
    if value is None or value == "":
        return dash
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, float):
        if math.isinf(value):
            return "∞"
        return f"{value:.{nd}f}"
    return str(value)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
