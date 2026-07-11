"""
Sender monitor WebSocket.

送受信中の全イベントを read-only でブロードキャストするデバッグ/外部監視用
WebSocket サーバー。キャプチャ用スレッド（keyboard/mouse/gamepad listener）
からは enqueue() で thread-safe に積み、asyncio ループ上のブロードキャスト
タスクが配信する。
"""

from __future__ import annotations

import asyncio
import logging

import websockets

logger = logging.getLogger("monitor_ws")

RECONNECT_BACKOFF = 3.0  # bind 失敗/クラッシュ時の再試行待ち (秒)
_QUEUE_MAXSIZE = 500
_CLIENT_SEND_TIMEOUT = 1.0  # seconds; a stalled client must not delay others


class MonitorServer:
    """monitor WS の状態（queue/clients）と配信ループをまとめて保持する。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, is_running):
        self._loop = loop
        self._is_running = is_running
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._clients = set()  # managed only from asyncio thread

    def enqueue(self, data):
        """Thread-safe: push data to the monitor broadcast queue."""
        try:
            self._loop.call_soon_threadsafe(self._enqueue_on_loop, data)
        except RuntimeError:
            # event loop が close 済み (shutdown 中など) は黙殺
            logger.debug("monitor queue dropped: loop closed", exc_info=True)

    def _enqueue_on_loop(self, data):
        """Loop-thread only: no clients means drop immediately; a full queue
        drops exactly the oldest item before enqueuing the newest."""
        if not self._clients:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(data)

    async def _handler(self, websocket):
        self._clients.add(websocket)
        try:
            async for _ in websocket:
                pass  # monitor is send-only from server side
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _send_to_client(self, ws, data):
        """Bounded send to a single client. Returns the client if it should
        be dropped (timeout/error), else None."""
        try:
            await asyncio.wait_for(ws.send(data), timeout=_CLIENT_SEND_TIMEOUT)
            return None
        except Exception:
            return ws

    async def _close_client(self, ws):
        """Best-effort bounded close for a client already removed from the set."""
        try:
            await asyncio.wait_for(ws.close(), timeout=_CLIENT_SEND_TIMEOUT)
        except Exception:
            logger.debug("monitor client close failed", exc_info=True)

    async def _broadcaster(self):
        """Single task that reads from the queue and fans out to all clients.
        Per-client sends run concurrently so one stalled client cannot
        postpone delivery to the others; failed clients are best-effort
        closed and discarded."""
        while self._is_running():
            data = await self._queue.get()
            if not self._clients:
                continue
            clients = list(self._clients)
            results = await asyncio.gather(
                *(self._send_to_client(ws, data) for ws in clients)
            )
            dead = [ws for ws in results if ws is not None]
            for ws in dead:
                self._clients.discard(ws)
            if dead:
                # Closing failed clients is cleanup, but it is still bounded
                # and concurrent so N broken clients cannot add N seconds of
                # delay before the next queued monitor event.
                await asyncio.gather(*(self._close_client(ws) for ws in dead))

    async def serve(self, port):
        while self._is_running():
            try:
                async with websockets.serve(self._handler, "0.0.0.0", port):
                    print(f"[Monitor] WebSocket at ws://localhost:{port}/")
                    await self._broadcaster()
            except OSError as e:
                logger.warning(
                    "monitor websocket bind failed on port %s: %s; retrying in %.1fs",
                    port, e, RECONNECT_BACKOFF,
                )
                await asyncio.sleep(RECONNECT_BACKOFF)
            except Exception as e:
                logger.exception(
                    "monitor websocket crashed: %s; retrying in %.1fs",
                    e, RECONNECT_BACKOFF,
                )
                await asyncio.sleep(RECONNECT_BACKOFF)
