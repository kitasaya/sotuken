"""Overpass障害を偽の違反0件へ変換しないことの回帰テスト。"""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import score_google_routes
from services import overpass


class _FailingClient:
    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        raise httpx.ConnectError("simulated outage", request=request)


class _FallbackClient:
    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if "overpass-api.de" in url:
            raise httpx.ConnectError("simulated primary outage", request=request)
        return httpx.Response(200, json={"elements": []}, request=request)


async def main() -> int:
    failures = 0

    def check(condition: bool, message: str) -> None:
        nonlocal failures
        print(f"{'PASS' if condition else 'FAIL'}: {message}")
        if not condition:
            failures += 1

    overpass.set_overpass_snapshot_date("2026-08-01T20:21:21Z")
    dated = overpass._query_at_configured_date("[out:json][timeout:30];way(1);out;")
    check(
        '[date:"2026-08-01T20:21:21Z"]' in dated,
        "attic date がクエリのグローバル設定へ入る",
    )

    original_get_client = overpass.get_client
    overpass.get_client = lambda: _FailingClient()
    overpass.reset_overpass_request_state()
    try:
        try:
            await overpass._post_with_retry("[out:json][timeout:1];way(1);out;")
            raised = False
        except overpass.OverpassUnavailableError:
            raised = True
        check(raised, "2系統とも失敗したとき専用例外を送出する")
    finally:
        overpass.get_client = original_get_client
        overpass.reset_overpass_request_state()

    overpass.get_client = lambda: _FallbackClient()
    overpass.reset_overpass_request_state()
    overpass.reset_overpass_usage_stats()
    try:
        data = await overpass.get_bulk_way_data([[139.7, 35.6]])
        stats = overpass.get_overpass_usage_stats()
        check(
            data[0]["overpass_endpoint"] == "maps.mail.ru",
            "フォールバック先を判定点へ記録する",
        )
        check(
            stats["maps.mail.ru"]["decision_units"] == 1
            and stats["overpass-api.de"]["failed_attempts"] == 1,
            "フォールバック件数とprimary失敗を実行統計へ記録する",
        )
    finally:
        overpass.get_client = original_get_client
        overpass.reset_overpass_request_state()

    original_score = score_google_routes.score_external_route

    async def fail_score(*args, **kwargs):
        raise overpass.OverpassUnavailableError("simulated outage")

    score_google_routes.score_external_route = fail_score
    try:
        results, errors = await score_google_routes.score_all([
            {"label": "障害テスト", "polyline": "_p~iF~ps|U_ulLnnqC"}
        ])
        check(results == [], "失敗ペアに採点結果（偽0）を作らない")
        check(
            len(errors) == 1 and errors[0]["status"] == "ERROR",
            "失敗ペアを明示的なERRORとして返す",
        )
    finally:
        score_google_routes.score_external_route = original_score

    print(f"\n{6 - failures}/6 checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
