"""Clipboard protocol validation and bounded synchronization primitives.

This module has no Windows or network side effects.  Both processes use it to
keep clipboard text on the dedicated protocol path and to reject stale epochs.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

PROTOCOL_VERSION = 1
MAX_TEXT_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 400 * 1024
ERROR_CODES = {
    "user",
    "disconnected",
    "unavailable",
    "busy",
    "too_large",
    "invalid_data",
    "write_failed",
    "worker_failed",
}


class ProtocolError(ValueError):
    """A dedicated clipboard message violated the v1 contract."""


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError(f"{name} must be a non-negative integer")
    return value


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ProtocolError(f"{name} must be a boolean")
    return value


def _uuid(value, name, *, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(f"{name} must be a UUID string")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ProtocolError(f"{name} must be a UUID string") from None
    return value


def validate_text(value):
    if not isinstance(value, str) or not value:
        raise ProtocolError("text must be a non-empty string")
    if "\x00" in value:
        raise ProtocolError("text contains an embedded NUL")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProtocolError("text contains invalid Unicode") from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise ProtocolError("text exceeds 64 KiB")
    return value


def _require_keys(message, required):
    expected = {"type", *required}
    if set(message) != expected:
        raise ProtocolError("message fields do not match the v1 contract")


def validate_message(message, *, allowed_types=None):
    """Return *message* after strict shape and scalar validation."""
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("message must be an object with a string type")
    msg_type = message["type"]
    if allowed_types is not None and msg_type not in allowed_types:
        raise ProtocolError("message type is not allowed on this path")

    if msg_type == "clipboard_offer":
        _require_keys(message, {"version", "session"})
        if _integer(message["version"], "version") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported clipboard protocol version")
        _uuid(message["session"], "session")
    elif msg_type == "clipboard_bind":
        _require_keys(message, {"version", "session"})
        if _integer(message["version"], "version") != PROTOCOL_VERSION:
            raise ProtocolError("unsupported clipboard protocol version")
        _uuid(message["session"], "session")
    elif msg_type == "clipboard_set":
        _require_keys(message, {"request_id", "enabled"})
        _integer(message["request_id"], "request_id")
        _boolean(message["enabled"], "enabled")
    elif msg_type == "clipboard_state":
        _require_keys(message, {"request_id", "enabled", "epoch", "reason"})
        _integer(message["request_id"], "request_id")
        enabled = _boolean(message["enabled"], "enabled")
        _uuid(message["epoch"], "epoch", nullable=not enabled)
        if enabled and message["epoch"] is None:
            raise ProtocolError("enabled state requires an epoch")
        if not enabled and message["epoch"] is not None:
            raise ProtocolError("disabled state cannot have an epoch")
        if message["reason"] not in ERROR_CODES:
            raise ProtocolError("invalid clipboard state reason")
    elif msg_type == "clipboard_propose":
        _require_keys(message, {"epoch", "origin_seq", "text"})
        _uuid(message["epoch"], "epoch")
        _integer(message["origin_seq"], "origin_seq")
        validate_text(message["text"])
    elif msg_type == "clipboard_update":
        _require_keys(message, {"epoch", "revision", "origin", "origin_seq", "text"})
        _uuid(message["epoch"], "epoch")
        _integer(message["revision"], "revision")
        _integer(message["origin_seq"], "origin_seq")
        if message["origin"] not in {"main", "sub"}:
            raise ProtocolError("origin must be main or sub")
        validate_text(message["text"])
    elif msg_type == "clipboard_error":
        _require_keys(message, {"epoch", "code"})
        _uuid(message["epoch"], "epoch", nullable=True)
        if message["code"] not in ERROR_CODES:
            raise ProtocolError("invalid clipboard error code")
    else:
        raise ProtocolError("unknown clipboard message type")
    return message


def decode_message(raw, *, allowed_types=None):
    if isinstance(raw, str):
        size = len(raw.encode("utf-8"))
    elif isinstance(raw, bytes):
        size = len(raw)
    else:
        raise ProtocolError("clipboard messages must be text")
    if size > MAX_MESSAGE_BYTES:
        raise ProtocolError("clipboard message exceeds 400 KiB")
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProtocolError("invalid clipboard JSON") from None
    return validate_message(message, allowed_types=allowed_types)


def encode_message(message):
    validate_message(message)
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("clipboard message exceeds 400 KiB")
    return raw


class LatestMailbox:
    """An asyncio mailbox containing at most the newest pending value."""

    def __init__(self):
        self._queue = asyncio.Queue(maxsize=1)

    def put_latest(self, value):
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(value)

    async def get(self):
        return await self._queue.get()

    def clear(self):
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def empty(self):
        return self._queue.empty()


@dataclass(frozen=True)
class ConfirmedUpdate:
    epoch: str
    revision: int
    origin: str
    origin_seq: int
    text: str

    def message(self):
        return {
            "type": "clipboard_update",
            "epoch": self.epoch,
            "revision": self.revision,
            "origin": self.origin,
            "origin_seq": self.origin_seq,
            "text": self.text,
        }


class RevisionCoordinator:
    """Receiver-owned ordering for one enabled clipboard epoch."""

    def __init__(self):
        self.epoch = None
        self.revision = 0
        self._origin_seq = {"main": -1, "sub": -1}

    def enable(self):
        self.epoch = str(uuid.uuid4())
        self.revision = 0
        self._origin_seq = {"main": -1, "sub": -1}
        return self.epoch

    def disable(self):
        self.epoch = None
        self.revision = 0
        self._origin_seq = {"main": -1, "sub": -1}

    def propose(self, *, epoch, origin, origin_seq, text):
        if self.epoch is None or epoch != self.epoch:
            return None
        if origin not in self._origin_seq:
            raise ProtocolError("invalid proposal origin")
        _integer(origin_seq, "origin_seq")
        validate_text(text)
        if origin_seq <= self._origin_seq[origin]:
            return None
        self._origin_seq[origin] = origin_seq
        self.revision += 1
        return ConfirmedUpdate(
            epoch=self.epoch,
            revision=self.revision,
            origin=origin,
            origin_seq=origin_seq,
            text=text,
        )
