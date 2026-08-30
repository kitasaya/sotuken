"""_trim_geometry の閉ループ修正に対する非破壊回帰テスト。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import types


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# このテストはHTTPを使わない。httpx未導入の最小分析環境でもimport可能にする。
try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    module = types.ModuleType("httpx")
    module.AsyncClient = object
    module.HTTPError = RuntimeError
    module.TimeoutException = RuntimeError
    module.Limits = object
    sys.modules["httpx"] = module

from services.law_checker import _check_direction
from services.route_analyzer import _trim_geometry


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main() -> None:
    def before_fix(geom: list, p_start: list, p_end: list) -> list:
        def dist_sq(a, b):
            return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
        i_s = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_start))
        i_e = min(range(len(geom)), key=lambda i: dist_sq(geom[i], p_end))
        lo, hi = min(i_s, i_e), max(i_s, i_e)
        trimmed = geom[lo:hi + 1]
        return trimmed if len(trimmed) >= 2 else geom

    # 非閉ループは修正前の min/max スライスと完全一致する。
    open_geom = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
    probes = [[-0.1, 0.0], [0.9, 0.0], [2.1, 0.0], [3.1, 0.0]]
    for p_start in probes:
        for p_end in probes:
            actual = _trim_geometry(open_geom, p_start, p_end, [p_start, p_end])
            check(actual == before_fix(open_geom, p_start, p_end),
                  f"非閉ループ修正前後一致: {p_start}→{p_end}")

    loop = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]

    # OSM順方向で閉ループ後半を走る。旧実装は [0,0]→[1,0]→[1,1] を選んでいた。
    forward_route = [[1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
    forward_trimmed = _trim_geometry(loop, forward_route[0], forward_route[-1], forward_route)
    check(
        forward_trimmed == [[1.0, 1.0], [0.0, 1.0], [0.0, 0.0]],
        "閉ループ順走は実際に通った後半弧を選ぶ",
    )
    check(
        _check_direction(forward_trimmed, [-1.0, -1.0], "yes") is False,
        "閉ループ順走を違反にしない",
    )

    # 同じ端点間を逆方向の弧で走れば、OSM順の候補弧とtravel vectorが反対になる。
    reverse_route = [[1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]
    reverse_trimmed = _trim_geometry(loop, reverse_route[0], reverse_route[-1], reverse_route)
    check(
        reverse_trimmed == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        "閉ループ逆走も実際に通った弧をOSM順で選ぶ",
    )
    check(
        _check_direction(reverse_trimmed, [-1.0, -1.0], "yes") is True,
        "閉ループ逆走は違反として残す",
    )

    # バッチ20で記録した実way 28413951も、修正後は後半弧を選び順走になる。
    evidence_path = BACKEND / "data" / "batch20_oneway_direction.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    target = evidence["target_way"]
    route = evidence["system_original_route_reproduction"]
    real_trimmed = _trim_geometry(
        target["geometry"], route["p_start"], route["p_end"],
        route["route_coordinates_on_detail_inclusive"],
    )
    check(real_trimmed[0] == target["geometry"][7] and real_trimmed[-1] == target["geometry"][0],
          "実way 28413951はノード7から閉路終端へ進む後半弧を選ぶ")
    check(_check_direction(real_trimmed, route["travel_vector"], "yes") is False,
          "実way 28413951の偽陽性が消える")


if __name__ == "__main__":
    main()
