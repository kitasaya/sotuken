"""
プロセス内で共有する httpx.AsyncClient を提供する。

毎リクエストで AsyncClient を生成する従来実装では、ホストごとに毎回
TCP/TLS ハンドシェイク（特に localhost の GraphHopper でも数十 ms、
HTTPS の Overpass/Nominatim では数百 ms）を支払っていた。
共有クライアントを使うとコネクションプールが再利用され、同一ホストへの
連続リクエストでハンドシェイクが省ける。

設計方針：
- 共有クライアントを 1 つだけ持つ（複数ホスト混在でも httpx 側でホストごとに
  プール管理される）
- per-request の headers / timeout は呼び出し側で指定する
- 起動時にプリ初期化、終了時にクローズ（main.py の lifespan で wire-up）
"""

import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    """共有 httpx.AsyncClient を返す。未初期化なら遅延生成する。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=30.0,  # デフォルトタイムアウト。per-call で上書き可能
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
        )
        logger.info("Shared httpx.AsyncClient 初期化")
    return _client


async def close_client() -> None:
    """共有 httpx.AsyncClient を閉じる（アプリ終了時）。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Shared httpx.AsyncClient クローズ")
