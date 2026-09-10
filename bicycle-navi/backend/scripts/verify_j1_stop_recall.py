"""3m基準の再現率 33/40 を検算する。分母「way_id一致」が過大でないかを確かめる。

## 問い

way_id 一致は「経路がその way を使った」を意味するだけで、「経路がそのノードを
通過した」を意味しない。自システムの経路ジオメトリは OSM のジオメトリそのものなので、
経路が実際に通過した地点のノードは垂線距離がほぼ0になるはずである。3m を超えた
ノードは、way_id は一致するが**経路が走った区間の外**にある可能性が高い。

## やること

コリドー20m内の一時停止ノードのうち、親wayが経路の使用way集合に含まれ、かつ
垂線距離が3mを超えるものを列挙し、各ノードが**経路がその way を走った区間の
内側か外側か**を判定する。

区間の求め方：GraphHopper の `osm_way_id` detail は [from, to, way_id] の
半開区間で返る。当該 way の区間に対応する経路座標を way のノード列に突き合わせ、
経路が触れたノード index の範囲を取る。ノードの index がその範囲内なら「区間内」。

## 制約

判定器・経路探索には触れない。CSVは出力しない（結果は md / CHANGELOG に記録する）。
Overpass が失敗したら例外で中断する。

## 使い方

  python scripts/verify_j1_stop_recall.py
"""

import asyncio
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR.parent))
sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_j1_thresholds as j1
import extract_point_features as epf
import extract_system_point_features as sysfeat

from services.experiment_settings import activate_experiment_overpass_date
from services.external_route_scorer import _haversine_m
from services.http_clients import close_client

import csv

NODE_MATCH_M = 1.0        # 経路座標と way ノードを同一とみなす距離
INTERVAL_S = 3.0


def _way_geometry(way: dict) -> list:
    return [[n["lon"], n["lat"]] for n in way.get("geometry", [])]


def _way_length_m(way: dict) -> float:
    geometry = _way_geometry(way)
    return sum(
        _haversine_m(geometry[i], geometry[i + 1])
        for i in range(len(geometry) - 1)
    )


def traversed_index_range(way: dict, coords: list,
                          intervals: list, way_id: int) -> tuple:
    """経路が当該 way を走った区間を、way のノード index 範囲として返す。

    戻り値: (min_index, max_index, 触れたノード数)。触れていなければ (None, None, 0)。
    """
    geometry = _way_geometry(way)
    if not geometry:
        return None, None, 0
    touched = set()
    for start, end, interval_way_id in intervals:
        if interval_way_id != way_id:
            continue
        for route_index in range(start, min(end, len(coords) - 1) + 1):
            point = coords[route_index]
            best_index, best_dist = None, float("inf")
            for index, node_point in enumerate(geometry):
                distance = _haversine_m(point, node_point)
                if distance < best_dist:
                    best_index, best_dist = index, distance
            if best_index is not None and best_dist <= NODE_MATCH_M:
                touched.add(best_index)
    if not touched:
        return None, None, 0
    return min(touched), max(touched), len(touched)


async def main() -> int:
    settings = activate_experiment_overpass_date()
    print(f"実験用Overpass取得時点: {settings['overpass_snapshot_date']}")

    with open(sysfeat.OD_PAIRS_CSV, encoding="utf-8-sig", newline="") as f:
        od_rows = list(csv.DictReader(f))

    misses: list[dict] = []
    totals = {"corridor": 0, "way_match": 0, "within_3m": 0}

    for index, od_row in enumerate(od_rows):
        if index > 0:
            await asyncio.sleep(INTERVAL_S)
        label = od_row["label"]
        route = await sysfeat.fetch_system_route(od_row)
        coords = route["coords"]
        intervals = route["way_intervals"]
        route_way_ids = sysfeat._route_way_ids(intervals)
        cumulative = j1._route_cumulative_m(coords)

        elements = await sysfeat._with_retry(
            label, lambda: epf.fetch_point_feature_nodes(
                coords, epf.CORRIDOR_RADIUS_M,
            ),
        )
        corridor = []
        for element in elements:
            tags = element.get("tags") or {}
            if tags.get("highway") not in sysfeat.STOP_VALUES:
                continue
            lat, lng = element.get("lat"), element.get("lon")
            if lat is None or lng is None:
                continue
            dist_m, along_m = epf._locate_on_route(lat, lng, coords, cumulative)
            corridor.append({
                "label": label, "node_id": int(element["id"]),
                "lat": lat, "lng": lng, "tags": tags,
                "dist_to_route_m": round(dist_m, 2),
                "route_position_m": round(along_m, 1),
            })
        totals["corridor"] += len(corridor)
        if not corridor:
            print(f"  {label}: コリドー0件", flush=True)
            continue

        await asyncio.sleep(INTERVAL_S)
        parents = await sysfeat._with_retry(
            label,
            lambda: epf.fetch_parent_ways([r["node_id"] for r in corridor]),
        )
        pair_misses = []
        for row in corridor:
            on_path = [
                w for w in parents.get(row["node_id"], [])
                if w["way_id"] in route_way_ids
            ]
            if not on_path:
                continue
            totals["way_match"] += 1
            if row["dist_to_route_m"] <= j1.STRICT_ON_ROUTE_M:
                totals["within_3m"] += 1
            row["on_path_ways"] = on_path
            row["coords"] = coords
            row["intervals"] = intervals
            pair_misses.append(row)
        misses.extend(pair_misses)
        print(
            f"  {label}: コリドー{len(corridor)} way一致"
            f"{sum(1 for r in corridor if any(w['way_id'] in route_way_ids for w in parents.get(r['node_id'], [])))}"
            f" うち3m超={sum(1 for r in pair_misses if r['dist_to_route_m'] > j1.STRICT_ON_ROUTE_M)}",
            flush=True,
        )

    print(
        f"\n[集計] コリドー{totals['corridor']} / way一致{totals['way_match']} / "
        f"3m以内{totals['within_3m']} / 検算対象{len(misses)}"
    )

    if not misses:
        print("way一致ノードは0件。検算対象なし。")
        return 0

    way_ids = sorted({
        w["way_id"] for row in misses for w in row["on_path_ways"]
    })
    ways = await sysfeat._with_retry("way取得", lambda: j1.fetch_ways(way_ids))

    results = []
    print("\n[検算] way一致ノード全件（3m以内・3m超の両方）")
    for row in sorted(misses, key=lambda r: r["dist_to_route_m"]):
        verdicts = []
        lines = []
        for way_info in row["on_path_ways"]:
            way_id = way_info["way_id"]
            way = ways.get(way_id)
            if way is None:
                lines.append(f"    way={way_id}: 取得できず")
                verdicts.append("判定不能")
                continue
            node_ids = [int(n) for n in way.get("nodes", [])]
            length_m = _way_length_m(way)
            low, high, touched = traversed_index_range(
                way, row["coords"], row["intervals"], way_id,
            )
            position = node_ids.index(row["node_id"]) if row["node_id"] in node_ids else None
            if low is None or position is None:
                verdict = "判定不能"
            elif low <= position <= high:
                verdict = "区間内"
            else:
                verdict = "区間外"
            verdicts.append(verdict)
            lines.append(
                f"    way={way_id}({way_info['highway']}) 全長={length_m:.1f}m "
                f"ノード数={len(node_ids)} / 経路が走った index 範囲="
                + (f"{low}〜{high}（照合{touched}ノード）" if low is not None else "なし")
                + f" / 当該ノード index={position} → {verdict}"
                + (f" [境界から{min(abs(position - low), abs(position - high))}]"
                   if verdict == "区間外" and position is not None else "")
            )
        final = "区間内" if "区間内" in verdicts else (
            "区間外" if "区間外" in verdicts else "判定不能"
        )
        results.append({**row, "verdict": final})
        marker = "3m以内" if row["dist_to_route_m"] <= j1.STRICT_ON_ROUTE_M else "3m超"
        print(f"\n  node_id={row['node_id']} / {row['label']} / "
              f"垂線距離={row['dist_to_route_m']}m（{marker}） / "
              f"direction={row['tags'].get('direction', 'none')} → {final}")
        for line in lines:
            print(line)
        print(
            "    https://www.google.com/maps/@?api=1&map_action=pano"
            f"&viewpoint={row['lat']:.7f},{row['lng']:.7f}"
        )

    from collections import Counter
    cross = Counter(
        (
            "3m以内" if r["dist_to_route_m"] <= j1.STRICT_ON_ROUTE_M else "3m超",
            r["verdict"],
        )
        for r in results
    )
    print("\n[クロス集計] 3m基準 × 区間判定")
    for key in sorted(cross):
        print(f"  {key[0]:6s} × {key[1]:6s} = {cross[key]}件")

    inside = sum(1 for r in results if r["verdict"] == "区間内")
    inside_within3 = cross.get(("3m以内", "区間内"), 0)
    print(f"\n[結果] way一致{len(results)}件 → 区間内{inside} / "
          f"区間外{sum(1 for r in results if r['verdict'] == '区間外')} / "
          f"判定不能{sum(1 for r in results if r['verdict'] == '判定不能')}")
    print(f"  正しい分母（経路が実際に通過した区間上のノード数）= {inside}")
    if inside:
        print(
            f"  再現率 = {inside_within3}/{inside} = "
            f"{inside_within3 / inside * 100:.1f}%"
        )
    within = [r["dist_to_route_m"] for r in results if r["verdict"] == "区間内"]
    outside = [r["dist_to_route_m"] for r in results if r["verdict"] == "区間外"]
    if within:
        print(f"  区間内の垂線距離: {min(within):.2f}〜{max(within):.2f}m")
    if outside:
        print(f"  区間外の垂線距離: {min(outside):.2f}〜{max(outside):.2f}m")
    return 0


if __name__ == "__main__":
    async def _run() -> int:
        try:
            return await main()
        finally:
            await close_client()

    sys.exit(asyncio.run(_run()))
