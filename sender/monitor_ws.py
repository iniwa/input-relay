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


class MonitorServer:
    """monitor WS の状態（queue/clients）と配信ループをまとめて保持する。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, is_running):
        self._loop = loop
        self._is_running = is_running
        self._queue: asyncio.Queue = asyncio.Queue()
        self._clients = set()  # managed only from asyncio thread

    def enqueue(self, data):
        """Thread-safe: push data to the monitor broadcast queue."""
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
        except RuntimeError:
            # event loop が close 済み (shutdown 中など) は黙殺
            logger.debug("monitor queue dropped: loop closed", exc_info=True)

    async def _handler(self, websocket):
        self._clients.add(websocket)
        try:
            async for _ in websocket:
                pass  # monitor is send-only from server side
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _broadcaster(self):
        """Single task that reads from the queue and fans out to all clients."""
        while self._is_running():
            data = await self._queue.get()
            if not self._clients:
                continue
            dead = []
            for ws in list(self._clients):
                try:
                    await ws.send(data)
                except Exception:
                    # Closed / broken / send failed — drop this client
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

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
