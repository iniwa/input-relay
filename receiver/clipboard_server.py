"""Receiver-side dedicated clipboard WebSocket coordination."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from input_common.clipboard_sync import (
    PROTOCOL_VERSION,
    LatestMailbox,
    ProtocolError,
    RevisionCoordinator,
    decode_message,
    encode_message,
)
from input_common.clipboard_win32 import ClipboardWorker

logger = logging.getLogger("clipboard_server")

_BIND_TIMEOUT = 3.0
_SEND_TIMEOUT = 3.0
_MIN_BODY_INTERVAL = 0.1


class ClipboardServerHub:
    """Own the one receiver clipboard worker and current input-session bind."""

    def __init__(self, loop, *, worker_factory=ClipboardWorker):
        self._loop = loop
        self._worker_factory = worker_factory
        self._lock = asyncio.Lock()
        self._sender_ws = None
        self._session = None
        self._session_owner = None
        self._clipboard_ws = None
        self._worker = None
        self._worker_owner = None
        self._coordinator = RevisionCoordinator()
        self._local = LatestMailbox()
        self._errors = LatestMailbox()
        self._last_body_send = 0.0

    async def register_sender(self, ws):
        """Create and return a capability offer tied to this exact input WS."""
        old_clipboard = None
        old_worker = None
        async with self._lock:
            old_clipboard = self._clipboard_ws
            old_worker = self._detach_worker_locked()
            self._sender_ws = ws
            self._session = str(uuid.uuid4())
            self._session_owner = object()
            self._clipboard_ws = None
            self._coordinator.disable()
            self._local.clear()
            self._errors.clear()
            session = self._session
        if old_clipboard is not None:
            await self._bounded_close(old_clipboard)
        await self._stop_worker(old_worker)
        return {
            "type": "clipboard_offer",
            "version": PROTOCOL_VERSION,
            "session": session,
        }

    async def unregister_sender(self, ws, session):
        old_clipboard = None
        old_worker = None
        async with self._lock:
            if self._sender_ws is not ws or self._session != session:
                return
            owner = self._session_owner
            old_clipboard = self._clipboard_ws
            old_worker = self._detach_worker_locked(owner)
            self._sender_ws = None
            self._session = None
            self._session_owner = None
            self._clipboard_ws = None
            self._coordinator.disable()
            self._local.clear()
            self._errors.clear()
        if old_clipboard is not None:
            await self._bounded_close(old_clipboard)
        await self._stop_worker(old_worker)

    async def handle(self, ws):
        """Handle one `/clipboard` connection; duplicate/stale binds fail shut."""
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=_BIND_TIMEOUT)
            bind = decode_message(raw, allowed_types={"clipboard_bind"})
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._bounded_close(ws)
            return

        session = bind["session"]
        async with self._lock:
            if (
                self._session != session
                or self._sender_ws is None
                or self._clipboard_ws is not None
            ):
                accepted = False
            else:
                self._clipboard_ws = ws
                owner = self._session_owner
                accepted = True
        if not accepted:
            await self._bounded_close(ws)
            return

        try:
            # Serialize native worker startup with input-session replacement.
            # Otherwise a disconnect between bind acceptance and start could
            # leave an unowned clipboard thread behind.
            async with self._lock:
                if (
                    self._session != session
                    or self._session_owner is not owner
                    or self._clipboard_ws is not ws
                ):
                    return
                worker = self._worker_for_owner_locked(owner)
                worker_ready = await asyncio.to_thread(worker.start)
            if not worker_ready:
                await self._send(ws, {
                    "type": "clipboard_error", "epoch": None, "code": "worker_failed",
                })
                return
            await self._send_state(ws, 0, False, None, "user")
            await self._run_bound(ws, session, owner, worker)
        finally:
            old_worker = None
            async with self._lock:
                is_current = (
                    self._session == session
                    and self._session_owner is owner
                    and self._clipboard_ws is ws
                )
                if is_current:
                    old_worker = self._detach_worker_locked(owner)
                    self._clipboard_ws = None
                    self._coordinator.disable()
                    self._local.clear()
                    self._errors.clear()
            if is_current:
                await self._stop_worker(old_worker)

    async def _run_bound(self, ws, session, owner, worker):
        while await self._is_current(ws, session, owner):
            recv_task = asyncio.create_task(ws.recv())
            local_task = asyncio.create_task(self._local.get())
            error_task = asyncio.create_task(self._errors.get())
            tasks = (recv_task, local_task, error_task)
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if error_task in done:
                    if not await self._is_current(ws, session, owner):
                        return
                    epoch, code = error_task.result()
                    current = self._coordinator.epoch
                    if epoch is None or epoch == current:
                        await self._send(ws, {
                            "type": "clipboard_error", "epoch": current, "code": code,
                        })
                        if code != "too_large":
                            return
                if recv_task in done:
                    if not await self._is_current(ws, session, owner):
                        return
                    try:
                        message = decode_message(
                            recv_task.result(),
                            allowed_types={"clipboard_set", "clipboard_propose", "clipboard_error"},
                        )
                    except ProtocolError:
                        await self._send(ws, {
                            "type": "clipboard_error",
                            "epoch": self._coordinator.epoch,
                            "code": "invalid_data",
                        })
                        return
                    if not await self._handle_message(ws, message, worker):
                        return
                if local_task in done:
                    if not await self._is_current(ws, session, owner):
                        return
                    epoch, origin_seq, _sequence, text = local_task.result()
                    update = self._coordinator.propose(
                        epoch=epoch, origin="sub", origin_seq=origin_seq, text=text,
                    )
                    if update is not None:
                        await self._send_body(ws, update.message())
            except Exception:
                return
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, ws, message, worker):
        msg_type = message["type"]
        if msg_type == "clipboard_set":
            request_id = message["request_id"]
            if message["enabled"]:
                epoch = self._coordinator.enable()
                self._local.clear()
                self._errors.clear()
                if not await self._enable_worker(worker, epoch):
                    await self._send(ws, {
                        "type": "clipboard_error", "epoch": epoch,
                        "code": "worker_failed",
                    })
                    return False
                await self._send_state(ws, request_id, True, epoch, "user")
            else:
                self._coordinator.disable()
                self._local.clear()
                self._errors.clear()
                worker.disable()
                await self._send_state(ws, request_id, False, None, "user")
            return True
        if msg_type == "clipboard_propose":
            update = self._coordinator.propose(
                epoch=message["epoch"],
                origin="main",
                origin_seq=message["origin_seq"],
                text=message["text"],
            )
            if update is not None:
                worker.submit_write(
                    update.epoch, update.revision, update.text,
                )
                await self._send_body(ws, update.message())
            return True
        # A peer-side oversize notification is non-fatal; all other worker
        # errors close only the clipboard connection.
        if message["epoch"] not in {None, self._coordinator.epoch}:
            return True
        return message["code"] == "too_large"

    def _worker_for_owner_locked(self, owner):
        if self._worker is None:
            self._worker = self._worker_factory(
                lambda *value: self._on_local_text(owner, *value),
                lambda *value: self._on_worker_error(owner, *value),
            )
            self._worker_owner = owner
        if self._worker_owner is not owner:
            raise RuntimeError("clipboard worker belongs to another session")
        return self._worker

    def _detach_worker_locked(self, owner=None):
        if owner is not None and self._worker_owner is not owner:
            return None
        worker = self._worker
        self._worker = None
        self._worker_owner = None
        return worker

    async def _enable_worker(self, worker, epoch):
        ready = worker.enable(epoch)
        if ready is None:
            return True
        completed = await asyncio.to_thread(ready.wait, _BIND_TIMEOUT)
        return completed and bool(getattr(ready, "baselined", True))

    async def _stop_worker(self, worker):
        if worker is not None:
            worker.disable()
            await asyncio.to_thread(worker.stop)

    def _on_local_text(self, owner, epoch, origin_seq, sequence, text):
        def deliver():
            if self._session_owner is owner and self._worker_owner is owner:
                self._local.put_latest((epoch, origin_seq, sequence, text))

        try:
            self._loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass

    def _on_worker_error(self, owner, epoch, code):
        def deliver():
            if self._session_owner is owner and self._worker_owner is owner:
                self._errors.put_latest((epoch, code))

        try:
            self._loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass

    async def _is_current(self, ws, session, owner):
        async with self._lock:
            return (
                self._clipboard_ws is ws
                and self._session == session
                and self._session_owner is owner
                and self._worker_owner is owner
            )

    async def _send_state(self, ws, request_id, enabled, epoch, reason):
        await self._send(ws, {
            "type": "clipboard_state",
            "request_id": request_id,
            "enabled": enabled,
            "epoch": epoch,
            "reason": reason,
        })

    async def _send_body(self, ws, message):
        now = self._loop.time()
        wait = _MIN_BODY_INTERVAL - (now - self._last_body_send)
        if wait > 0:
            await asyncio.sleep(wait)
        await self._send(ws, message)
        self._last_body_send = self._loop.time()

    async def _send(self, ws, message):
        await asyncio.wait_for(ws.send(encode_message(message)), timeout=_SEND_TIMEOUT)

    async def _bounded_close(self, ws):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(), timeout=1.0)
