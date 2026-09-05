"""Sender-side clipboard capability client and hotkey state machine."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

import websockets

from input_common.clipboard_sync import (
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    LatestMailbox,
    ProtocolError,
    decode_message,
    encode_message,
)
from input_common.clipboard_win32 import ClipboardWorker

logger = logging.getLogger("clipboard_client")

_CONTROL_TIMEOUT = 3.0
_SEND_TIMEOUT = 3.0
_RECONNECT_BACKOFF = 3.0
_MIN_BODY_INTERVAL = 0.1

_NOTICE = {
    "on": "クリップボード共有 ON",
    "off": "クリップボード共有 OFF",
    "unsupported": "共有できません：相手側の更新が必要です",
    "disconnected": "共有できません：Sub PC 未接続",
    "lost": "クリップボード共有 OFF：接続が切れました",
    "stopping_failed": "共有停止：接続を確認してください",
    "worker_failed": "共有できません：クリップボードを利用できません",
    "too_large": "共有できません：テキストが64 KiBを超えています",
}


class ClipboardClient:
    """One capability-gated `/clipboard` connection and one Win32 worker."""

    def __init__(
        self,
        loop,
        notification,
        *,
        worker_factory=ClipboardWorker,
        connect=websockets.connect,
    ):
        self._loop = loop
        self._notification = notification
        self._worker_factory = worker_factory
        self._connect = connect
        self._lock = threading.Lock()
        self._state = "unavailable"
        self._reason = "unavailable"
        self._session = None
        self._generation = 0
        self._task = None
        self._worker = None
        self._commands = asyncio.Queue(maxsize=4)
        self._local = LatestMailbox()
        self._errors = LatestMailbox()
        self._request_id = 0
        self._epoch = None
        self._last_revision = 0
        self._last_local = None
        self._last_body_send = 0.0

    def snapshot(self):
        with self._lock:
            state = self._state
            reason = self._reason
        return {"state": state, "enabled": state == "on", "reason": reason}

    def mark_input_connected(self):
        # Until an offer arrives, the input path is usable but the peer is an
        # older receiver as far as clipboard capability is concerned.
        self._set_state("unavailable", "unavailable")

    async def accept_offer(self, session, host, port):
        await self._cancel_task()
        self._generation += 1
        generation = self._generation
        self._session = session
        self._task = asyncio.create_task(
            self._supervise(generation, session, host, port),
            name="clipboard-client",
        )

    async def input_disconnected(self):
        was_enabled = self.snapshot()["state"] in {"on", "enabling", "disabling"}
        self._generation += 1
        self._session = None
        await self._cancel_task()
        await self._stop_worker()
        self._clear_epoch()
        self._set_state("unavailable", "disconnected")
        if was_enabled:
            self._notification.show(_NOTICE["lost"])

    async def shutdown(self):
        await self.input_disconnected()
        await asyncio.to_thread(self._notification.shutdown)

    def toggle(self):
        """Thread-safe hotkey entry point; never calls Win32 clipboard APIs."""
        state = self.snapshot()["state"]
        if state == "unavailable":
            reason = self.snapshot()["reason"]
            if reason == "disconnected":
                self._notification.show(_NOTICE["disconnected"])
            elif reason == "worker_failed":
                self._notification.show(_NOTICE["worker_failed"])
            else:
                self._notification.show(_NOTICE["unsupported"])
            return
        if state == "disabling":
            return
        try:
            self._loop.call_soon_threadsafe(self._enqueue_toggle)
        except RuntimeError:
            pass

    async def _supervise(self, generation, session, host, port):
        if not self._notification.available:
            self._set_state("unavailable", "worker_failed")
            return
        uri = f"ws://{host}:{port}/clipboard"
        try:
            while generation == self._generation and self._session == session:
                if not await self._start_worker():
                    self._set_state("unavailable", "worker_failed")
                    return
                try:
                    async with self._connect(uri, max_size=MAX_MESSAGE_BYTES) as ws:
                        await self._run_connection(ws, generation, session)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("clipboard connection unavailable", exc_info=True)
                if generation != self._generation or self._session != session:
                    break
                failed_state = self.snapshot()["state"]
                if failed_state == "disabling":
                    self._notification.show(_NOTICE["stopping_failed"])
                elif failed_state in {"on", "enabling"}:
                    self._notification.show(_NOTICE["lost"])
                await self._stop_worker()
                self._clear_epoch()
                self._set_state("unavailable", "disconnected")
                await asyncio.sleep(_RECONNECT_BACKOFF)
        finally:
            await self._stop_worker()
            if generation == self._generation:
                self._clear_epoch()
                self._set_state("unavailable", "disconnected")

    async def _run_connection(self, ws, generation, session):
        await self._send(ws, {
            "type": "clipboard_bind", "version": PROTOCOL_VERSION, "session": session,
        })
        raw = await asyncio.wait_for(ws.recv(), timeout=_CONTROL_TIMEOUT)
        initial = decode_message(
            raw, allowed_types={"clipboard_state", "clipboard_error"},
        )
        if initial["type"] != "clipboard_state" or initial["request_id"] != 0:
            raise ProtocolError("missing initial clipboard state")
        if initial["enabled"] or initial["epoch"] is not None:
            raise ProtocolError("initial clipboard state must be off")
        self._clear_commands()
        self._local.clear()
        self._errors.clear()
        self._clear_epoch()
        self._set_state("off", initial["reason"])
        pending_deadline = None

        while generation == self._generation and self._session == session:
            recv_task = asyncio.create_task(ws.recv())
            command_task = asyncio.create_task(self._commands.get())
            local_task = asyncio.create_task(self._local.get())
            error_task = asyncio.create_task(self._errors.get())
            tasks = (recv_task, command_task, local_task, error_task)
            timeout = None
            if pending_deadline is not None:
                timeout = max(0, pending_deadline - self._loop.time())
            try:
                done, _ = await asyncio.wait(
                    tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    if self.snapshot()["state"] == "disabling":
                        self._notification.show(_NOTICE["stopping_failed"])
                    raise TimeoutError("clipboard state response timed out")
                if error_task in done:
                    epoch, code = error_task.result()
                    if epoch is None or epoch == self._epoch:
                        await self._send(ws, {
                            "type": "clipboard_error", "epoch": self._epoch, "code": code,
                        })
                        if code == "too_large":
                            self._notification.show(_NOTICE["too_large"])
                        else:
                            raise RuntimeError("clipboard worker failed")
                if recv_task in done:
                    message = decode_message(
                        recv_task.result(),
                        allowed_types={
                            "clipboard_state", "clipboard_update", "clipboard_error",
                        },
                    )
                    pending_deadline = await self._handle_message(ws, message, pending_deadline)
                if command_task in done:
                    pending_deadline = await self._handle_toggle(ws)
                if local_task in done:
                    epoch, origin_seq, sequence, text = local_task.result()
                    if self.snapshot()["state"] == "on" and epoch == self._epoch:
                        self._last_local = (origin_seq, sequence)
                        await self._send_body(ws, {
                            "type": "clipboard_propose",
                            "epoch": epoch,
                            "origin_seq": origin_seq,
                            "text": text,
                        })
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_toggle(self, ws):
        state = self.snapshot()["state"]
        if state == "off":
            self._request_id += 1
            if not await self._enable_worker(f"pending:{self._request_id}"):
                raise RuntimeError("clipboard baseline timed out")
            self._set_state("enabling", "user")
            await self._send(ws, {
                "type": "clipboard_set",
                "request_id": self._request_id,
                "enabled": True,
            })
            return self._loop.time() + _CONTROL_TIMEOUT
        if state in {"on", "enabling"}:
            self._request_id += 1
            self._worker.disable()
            self._clear_epoch()
            self._set_state("disabling", "user")
            await self._send(ws, {
                "type": "clipboard_set",
                "request_id": self._request_id,
                "enabled": False,
            })
            return self._loop.time() + _CONTROL_TIMEOUT
        return None

    async def _handle_message(self, ws, message, pending_deadline):
        msg_type = message["type"]
        if msg_type == "clipboard_state":
            if message["request_id"] != self._request_id:
                return pending_deadline
            state = self.snapshot()["state"]
            if state == "enabling" and message["enabled"]:
                self._epoch = message["epoch"]
                self._last_revision = 0
                self._last_local = None
                self._local.clear()
                if not await self._enable_worker(self._epoch):
                    raise RuntimeError("clipboard baseline timed out")
                self._set_state("on", message["reason"])
                self._notification.show(_NOTICE["on"])
                return None
            if state == "disabling" and not message["enabled"]:
                self._clear_epoch()
                self._set_state("off", message["reason"])
                self._notification.show(_NOTICE["off"])
                return None
            raise ProtocolError("clipboard state did not match pending request")
        if msg_type == "clipboard_update":
            if self.snapshot()["state"] != "on" or message["epoch"] != self._epoch:
                return pending_deadline
            if message["revision"] <= self._last_revision:
                return pending_deadline
            self._last_revision = message["revision"]
            skip_sequence = None
            if (
                message["origin"] == "main"
                and self._last_local is not None
                and message["origin_seq"] == self._last_local[0]
            ):
                skip_sequence = self._last_local[1]
            self._worker.submit_write(
                self._epoch,
                self._last_revision,
                message["text"],
                skip_sequence=skip_sequence,
            )
            return pending_deadline
        if message["epoch"] not in {None, self._epoch}:
            return pending_deadline
        if message["code"] == "too_large":
            self._notification.show(_NOTICE["too_large"])
            return pending_deadline
        raise RuntimeError("peer clipboard failure")

    async def _start_worker(self):
        if self._worker is None:
            self._worker = self._worker_factory(
                self._on_local_text,
                self._on_worker_error,
            )
        return await asyncio.to_thread(self._worker.start)

    async def _enable_worker(self, epoch):
        ready = self._worker.enable(epoch)
        if ready is None:
            return True
        completed = await asyncio.to_thread(ready.wait, _CONTROL_TIMEOUT)
        return completed and bool(getattr(ready, "baselined", True))

    async def _stop_worker(self):
        worker = self._worker
        if worker is not None:
            worker.disable()
            clean = await asyncio.to_thread(worker.stop)
            if clean is not False:
                self._worker = None

    async def _cancel_task(self):
        task = self._task
        self._task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _on_local_text(self, epoch, origin_seq, sequence, text):
        try:
            self._loop.call_soon_threadsafe(
                self._local.put_latest, (epoch, origin_seq, sequence, text),
            )
        except RuntimeError:
            pass

    def _on_worker_error(self, epoch, code):
        try:
            self._loop.call_soon_threadsafe(self._errors.put_latest, (epoch, code))
        except RuntimeError:
            pass

    def _enqueue_toggle(self):
        try:
            self._commands.put_nowait("toggle")
        except asyncio.QueueFull:
            pass

    def _clear_commands(self):
        while True:
            try:
                self._commands.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _clear_epoch(self):
        self._epoch = None
        self._last_revision = 0
        self._last_local = None
        self._local.clear()
        self._errors.clear()
        if self._worker is not None:
            self._worker.disable()

    def _set_state(self, state, reason):
        with self._lock:
            self._state = state
            self._reason = reason

    async def _send_body(self, ws, message):
        wait = _MIN_BODY_INTERVAL - (self._loop.time() - self._last_body_send)
        if wait > 0:
            await asyncio.sleep(wait)
        await self._send(ws, message)
        self._last_body_send = self._loop.time()

    async def _send(self, ws, message):
        await asyncio.wait_for(ws.send(encode_message(message)), timeout=_SEND_TIMEOUT)
