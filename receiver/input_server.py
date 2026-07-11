"""
Input Server - Receives input events via WebSocket, serves overlay + config GUI.
Run on Sub PC.
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.parse

import websockets

import input_injector

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("INPUT_RELAY_DEBUG") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("receiver")

browser_clients = set()
_browser_lock = asyncio.Lock()
_BROWSER_SEND_TIMEOUT = 1.0  # seconds; a stalled client must not block others/RC injection
sender_ws = None
_ws_loop = None  # asyncio event loop, set in main()
_ws_port = 8888  # WebSocket port, set in main()
_http_server = None  # ThreadingHTTPServer instance, for shutdown

# Standalone mode
_standalone_queue = None  # asyncio.Queue, set in main() when standalone
_STANDALONE_QUEUE_MAXSIZE = 500
_STANDALONE_OVERFLOW_LOG_INTERVAL = 5.0  # seconds; rate-limit overflow warnings
_standalone_last_overflow_log = 0.0

# Restart guard: only the first DELETE /api/restart schedules the restart
# thread; repeated calls while one is pending are no-ops that still return
# {"ok": true} (idempotent from the caller's point of view).
_restart_lock = threading.Lock()
_restart_pending = False

# Remote control state
remote_control_enabled = False
_rc_lock = threading.Lock()
# Exact tracked identities of currently-injected input, as returned by
# input_injector.replay_event(): ("vk", <int>) or ("mouse", <name>).
# Never derived from the display `key` string (see _rc_inject_event).
_rc_active_identities = set()
# True only once the *currently connected* sender has sent its own explicit
# remote_control state message. A fresh connection (and any disconnect)
# resets this to False, so stale/leftover `remote_control_enabled` can never
# by itself cause injection (see _rc_inject_event / sender_handler).
_sender_synchronized = False
_RC_SEND_TIMEOUT = 1.0  # seconds; bounded wait on the control plane only, never the input path

OVERLAY_DIR = Path(__file__).parent
CONFIG_DIR = OVERLAY_DIR.parent / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
PRESETS_PATH = CONFIG_DIR / "presets.json"
LAYOUT_PRESETS_PATH = CONFIG_DIR / "layout_presets.json"

# LAN 公開前提のため、複数クライアントからの同時 POST/DELETE を直列化。
# どの path も同じロックで保護 (頻度が低いので単一ロックで十分)。
# RLock: 1 mutation 全体 (read-modify-write) を outer transaction として
# 保持したまま、内側で public load/save ヘルパーを呼べるようにするため。
_config_io_lock = threading.RLock()


def _atomic_write_json(path, data):
    """Write JSON to `path` atomically: write to a temp file in the same
    directory, then os.replace it into place. Must be called while already
    holding `_config_io_lock`. Best-effort removes the temp file on failure."""
    import tempfile
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def load_config():
    with _config_io_lock:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {}


def save_config(data):
    with _config_io_lock:
        CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_PRESET_TYPES = {"keyboard", "leverless", "controller"}

def load_presets():
    with _config_io_lock:
        empty = {"keyboard": {}, "leverless": {}, "controller": {}}
        if not PRESETS_PATH.exists():
            return empty
        data = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
        # Migrate old flat format: { "name": { keyboard, leverless, controller } }
        if data and not all(k in _PRESET_TYPES for k in data.keys()):
            migrated = {"keyboard": {}, "leverless": {}, "controller": {}}
            for name, p in data.items():
                for t in _PRESET_TYPES:
                    if t in p:
                        migrated[t][name] = {t: p[t]}
            _atomic_write_json(PRESETS_PATH, migrated)
            return migrated
        return data


def save_presets(data):
    with _config_io_lock:
        _atomic_write_json(PRESETS_PATH, data)


def load_layout_presets():
    with _config_io_lock:
        empty = {"keyboard": {}, "leverless": {}, "controller": {}}
        if not LAYOUT_PRESETS_PATH.exists():
            return empty
        data = json.loads(LAYOUT_PRESETS_PATH.read_text(encoding="utf-8"))
        for t in _PRESET_TYPES:
            if t not in data:
                data[t] = {}
        return data


def save_layout_presets(data):
    with _config_io_lock:
        _atomic_write_json(LAYOUT_PRESETS_PATH, data)


def _inject_ws_port(html, ws_port):
    """Inject `window.__WS_PORT__=<int>` into `<head>` so config_gui.html's
    debug WebSocket always connects to this receiver process's actual WS
    port, never a stale/guessed fallback. Pure string transform; does not
    touch any other static-file serving."""
    return html.replace(
        "<head>", f"<head><script>window.__WS_PORT__={int(ws_port)};</script>", 1,
    )


def _client_label(handler):
    """Return a short string identifying the HTTP client for logs."""
    try:
        ip = handler.client_address[0]
    except (AttributeError, IndexError):
        ip = "?"
    origin = handler.headers.get("Origin", "")
    ua = handler.headers.get("User-Agent", "")
    tag = origin or ua[:40]
    return f"{ip} ({tag})" if tag else ip


async def _send_to_browser(client, message):
    """Bounded send to a single browser client. Returns the client if it
    should be dropped (timeout/error), else None."""
    try:
        await asyncio.wait_for(client.send(message), timeout=_BROWSER_SEND_TIMEOUT)
        return None
    except Exception:
        return client


async def broadcast_to_browsers(message):
    """Send message to each browser client independently and concurrently.

    A slow/stalled client must not delay delivery to the others; per-client
    sends are started concurrently and this awaits all of them before
    returning, so callers invoking broadcasts sequentially still get
    per-client message ordering. Failed or timed-out clients are dropped
    from browser_clients rather than retried.
    """
    async with _browser_lock:
        clients = list(browser_clients)
    if not clients:
        return
    results = await asyncio.gather(
        *(_send_to_browser(c, message) for c in clients)
    )
    dead = [c for c in results if c is not None]
    if dead:
        async with _browser_lock:
            for c in dead:
                browser_clients.discard(c)


def _broadcast_change(kind, extra):
    """Push a config-change notification to all browsers via WebSocket.
    kind: 'config', 'sender_config', 'presets', 'layout_presets'
    """
    if _ws_loop is None:
        return
    payload = {"type": "config_change", "kind": kind, "timestamp": time.time()}
    payload.update(extra)
    # Legacy compatibility: send 'config' type too so existing overlay.html
    # listeners continue to refresh on config updates.
    if kind == "config" and "data" in extra:
        asyncio.run_coroutine_threadsafe(
            broadcast_to_browsers(json.dumps({"type": "config", "data": extra["data"]})),
            _ws_loop,
        )
    asyncio.run_coroutine_threadsafe(
        broadcast_to_browsers(json.dumps(payload)), _ws_loop,
    )


async def _send_to_sender(data):
    """Send a message back to the sender over the existing WebSocket.
    Returns whether the send actually succeeded."""
    if sender_ws:
        try:
            await sender_ws.send(json.dumps(data))
            return True
        except Exception:
            logger.debug("send to sender failed", exc_info=True)
    return False


def _send_command_to_sender(data):
    """Send a control message to the sender and wait (bounded) for the send
    itself to complete, so an HTTP handler thread can learn whether the
    command actually reached the sender before deciding success/failure.
    This is the low-frequency control plane only; it must never be used on
    the input event path."""
    if _ws_loop is None:
        return False
    future = asyncio.run_coroutine_threadsafe(_send_to_sender(data), _ws_loop)
    try:
        return future.result(timeout=_RC_SEND_TIMEOUT)
    except Exception:
        return False


def _notify_sender_async(data):
    """Best-effort, fire-and-forget notify: schedules the send but does not
    wait on it, so the caller (disable path) is never blocked by it."""
    if _ws_loop is not None:
        asyncio.run_coroutine_threadsafe(_send_to_sender(data), _ws_loop)


def _sender_ready():
    """True only if a sender is currently connected AND that connection has
    already sent its own explicit remote_control state (see
    _sender_synchronized)."""
    with _rc_lock:
        return sender_ws is not None and _sender_synchronized


def _set_rc_state(enabled, mark_synchronized=None):
    """Set remote control state and handle cleanup.

    Disabling snapshots and clears `_rc_active_identities` while holding
    `_rc_lock`, then releases that exact snapshot outside the lock. Because
    `_rc_inject_event` below performs its enabled-check, injection, and
    tracked-state update inside the same lock, a key-down that is
    in-progress when OFF happens either completes (and is included in this
    snapshot) or is rejected outright by the enabled-check; it can never be
    recorded after this snapshot was taken.

    `mark_synchronized`, when not None, updates `_sender_synchronized` in the
    same critical section: only the sender's own reported state message
    (sender_handler) may pass this, establishing readiness for that
    connection atomically with the state it reports.
    """
    global remote_control_enabled, _sender_synchronized
    release_snapshot = None
    with _rc_lock:
        if mark_synchronized is not None:
            _sender_synchronized = mark_synchronized
        remote_control_enabled = enabled
        if not enabled:
            release_snapshot = set(_rc_active_identities)
            _rc_active_identities.clear()
    state = "ENABLED" if enabled else "DISABLED"
    print(f"[RemoteControl] {state}")
    if release_snapshot:
        input_injector.release_identities(release_snapshot)
    # Broadcast to browsers
    msg = json.dumps({"type": "remote_control_state", "enabled": enabled})
    if _ws_loop:
        asyncio.run_coroutine_threadsafe(broadcast_to_browsers(msg), _ws_loop)


def _rc_inject_event(event):
    """Atomically check RC-enabled, inject via input_injector, and update
    tracked identities under `_rc_lock`. Tracking uses the exact identity
    (VK int / mouse button name) returned by replay_event, never the
    display `key` string, and only for successful injection. Gating also
    requires `_sender_synchronized`, so a stale `remote_control_enabled`
    left over from before this connection can never by itself allow
    injection before the sender's own state message arrives."""
    with _rc_lock:
        if not remote_control_enabled or not _sender_synchronized:
            return
        try:
            identity = input_injector.replay_event(event)
        except Exception as e:
            print(f"[RemoteControl] Inject error: {e}")
            return
        if identity is None:
            return
        etype = event.get("type")
        if etype == "key_down":
            _rc_active_identities.add(identity)
        elif etype == "key_up":
            _rc_active_identities.discard(identity)


class ApiError(Exception):
    """API-layer error carrying an explicit HTTP status, so handlers can
    reject a request without pretending success (`{ok: false}` + 200)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _api_get_config(handler, body):
    return load_config()


def _api_get_presets(handler, body):
    return load_presets()


def _api_get_layout_presets(handler, body):
    return load_layout_presets()


def _api_get_remote_control(handler, body):
    with _rc_lock:
        enabled = remote_control_enabled
    return {"enabled": enabled}


def _api_get_sender_config(handler, body):
    sender_cfg_path = CONFIG_DIR / "sender_config.json"
    with _config_io_lock:
        if sender_cfg_path.exists():
            return json.loads(sender_cfg_path.read_text(encoding="utf-8"))
    return {}


def _api_post_config(handler, body):
    data = json.loads(body)
    save_config(data)
    print(f"[API] config updated by {_client_label(handler)}")
    _broadcast_change("config", {"data": data})
    return {"ok": True}


def _api_post_presets(handler, body):
    data = json.loads(body)
    ptype = data.get("type", "keyboard")
    name = data["name"]
    with _config_io_lock:
        presets = load_presets()
        if ptype not in presets:
            presets[ptype] = {}
        presets[ptype][name] = {
            ptype: data[ptype],
            "layout": data.get("layout", {}),
            "inputHistory": data.get("inputHistory", {}),
        }
        save_presets(presets)
    print(f"[API] preset saved: {ptype}/{name} by {_client_label(handler)}")
    _broadcast_change("presets", {"type": ptype, "name": name, "op": "save"})
    return {"ok": True}


def _api_post_layout_presets(handler, body):
    data = json.loads(body)
    ptype = data.get("type", "keyboard")
    name = data["name"]
    with _config_io_lock:
        presets = load_layout_presets()
        if ptype not in presets:
            presets[ptype] = {}
        presets[ptype][name] = {
            "layout": data.get("layout", {}),
            "inputHistory": data.get("inputHistory", {}),
        }
        save_layout_presets(presets)
    print(f"[API] layout-preset saved: {ptype}/{name} by {_client_label(handler)}")
    _broadcast_change("layout_presets", {"type": ptype, "name": name, "op": "save"})
    return {"ok": True}


def _api_post_sender_config(handler, body):
    """Receiver-local compatibility endpoint: merges only `host`/`port` into
    the existing receiver-local sender_config.json copy, preserving all other
    keys untouched. This never reaches the resident Main PC sender process
    (see the sender GUI/API on port 8082 for that)."""
    data = json.loads(body)
    sender_cfg_path = CONFIG_DIR / "sender_config.json"
    with _config_io_lock:
        if sender_cfg_path.exists():
            merged = json.loads(sender_cfg_path.read_text(encoding="utf-8"))
        else:
            merged = {}
        for key in ("host", "port"):
            if key in data:
                merged[key] = data[key]
        _atomic_write_json(sender_cfg_path, merged)
    print(f"[API] sender-config (receiver-local copy) updated by {_client_label(handler)}")
    _broadcast_change("sender_config", {"data": merged})
    return {"ok": True}


def _api_post_refresh(handler, body):
    data = load_config()
    _broadcast_change("config", {"data": data})
    return {"ok": True}


def _api_post_remote_control(handler, body):
    data = json.loads(body)
    enabled = bool(data.get("enabled", False))

    if not enabled:
        # Safety-first: disable local injection immediately regardless of
        # sender presence, then best-effort (non-blocking) notify it.
        _set_rc_state(False)
        _notify_sender_async({"type": "remote_control", "enabled": False})
        return {"ok": True, "enabled": False}

    if not _sender_ready():
        raise ApiError(
            "Remote Control requires a connected, synchronized sender", status=409,
        )
    if not _send_command_to_sender({"type": "remote_control", "enabled": True}):
        raise ApiError("Failed to notify sender", status=502)

    # Do not enable locally yet: the sender must report back its own
    # engaged state (handled in sender_handler) before injection may start.
    return {"ok": True, "enabled": True}


def _api_post_mode_switch(handler, body):
    data = json.loads(body)
    mode = data.get("mode", "keyboard")
    msg = json.dumps({
        "type": "mode_switch",
        "key": mode,
        "source": "system",
        "timestamp": time.time(),
    })
    if _ws_loop:
        asyncio.run_coroutine_threadsafe(broadcast_to_browsers(msg), _ws_loop)
    return {"ok": True}


def _api_delete_presets(handler, body):
    data = json.loads(body)
    ptype = data.get("type", "keyboard")
    name = data["name"]
    with _config_io_lock:
        presets = load_presets()
        presets.get(ptype, {}).pop(name, None)
        save_presets(presets)
    print(f"[API] preset deleted: {ptype}/{name} by {_client_label(handler)}")
    _broadcast_change("presets", {"type": ptype, "name": name, "op": "delete"})
    return {"ok": True}


def _api_delete_layout_presets(handler, body):
    data = json.loads(body)
    ptype = data.get("type", "keyboard")
    name = data["name"]
    with _config_io_lock:
        presets = load_layout_presets()
        presets.get(ptype, {}).pop(name, None)
        save_layout_presets(presets)
    print(f"[API] layout-preset deleted: {ptype}/{name} by {_client_label(handler)}")
    _broadcast_change("layout_presets", {"type": ptype, "name": name, "op": "delete"})
    return {"ok": True}


def _api_delete_restart(handler, body):
    # _restart_server は 0.5s 待ってから execv するため、レスポンス送信が先行する。
    # 連打された DELETE は同じ再起動スレッドに相乗りさせ、二重起動を防ぐ。
    global _restart_pending
    with _restart_lock:
        if not _restart_pending:
            _restart_pending = True
            threading.Thread(target=_restart_server, daemon=True).start()
    return {"ok": True}


# path → handler(handler_obj, body) -> dict のディスパッチ表。
# 例外は呼び出し側で一括 400 化、戻り dict を 200 で JSON 応答する。
_GET_ROUTES = {
    "/api/config":         _api_get_config,
    "/api/presets":        _api_get_presets,
    "/api/layout-presets": _api_get_layout_presets,
    "/api/remote-control": _api_get_remote_control,
    "/api/sender-config":  _api_get_sender_config,
}

_POST_ROUTES = {
    "/api/config":         _api_post_config,
    "/api/presets":        _api_post_presets,
    "/api/layout-presets": _api_post_layout_presets,
    "/api/sender-config":  _api_post_sender_config,
    "/api/refresh":        _api_post_refresh,
    "/api/remote-control": _api_post_remote_control,
    "/api/mode-switch":    _api_post_mode_switch,
}

_DELETE_ROUTES = {
    "/api/presets":        _api_delete_presets,
    "/api/layout-presets": _api_delete_layout_presets,
    "/api/restart":        _api_delete_restart,
}

_OVERLAY_MODES = ("history", "input", "mouse-trail")


class OverlayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _dispatch(self, routes, body=b""):
        """共通ディスパッチ: 該当ハンドラを引いて JSON で応答。
        ハンドラが例外を投げたら 400、404 は呼び出し側で処理。"""
        path = urllib.parse.urlparse(self.path).path
        handler = routes.get(path)
        if handler is None:
            return False
        try:
            self._json_response(handler(self, body))
        except ApiError as e:
            self._json_response({"error": str(e)}, e.status)
        except Exception as e:
            logger.debug("API %s failed", path, exc_info=True)
            self._json_response({"error": str(e)}, 400)
        return True

    def _read_body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def _resolve_static_path(self, path):
        """OVERLAY_DIR 配下に解決できないパス（相対脱出・絶対パス・不正パス）は
        None を返す。呼び出し側はこれを 404 として扱う。"""
        try:
            base = OVERLAY_DIR.resolve()
            candidate = (OVERLAY_DIR / path).resolve()
        except (OSError, ValueError):
            return None
        if not candidate.is_relative_to(base):
            return None
        return candidate

    def do_GET(self):
        if self._dispatch(_GET_ROUTES):
            return

        path = urllib.parse.urlparse(self.path).path.lstrip("/")
        if path in _OVERLAY_MODES:
            self._serve_overlay_with_mode(path)
            return

        # Serve static files
        if not path:
            path = "config_gui.html"
        file_path = self._resolve_static_path(path)
        if file_path is not None and file_path.exists() and file_path.is_file():
            if path == "config_gui.html":
                content = _inject_ws_port(
                    file_path.read_text(encoding="utf-8"), _ws_port,
                ).encode("utf-8")
            else:
                content = file_path.read_bytes()
            ct = "text/html"
            if path.endswith(".json"):
                ct = "application/json"
            elif path.endswith(".js"):
                ct = "application/javascript"
            elif path.endswith(".css"):
                ct = "text/css"
            self.send_response(200)
            self.send_header("Content-Type", f"{ct}; charset=utf-8")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._dispatch(_POST_ROUTES, self._read_body()):
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if not self._dispatch(_DELETE_ROUTES, self._read_body()):
            self.send_response(404)
            self.end_headers()

    def _serve_overlay_with_mode(self, mode):
        if mode == "mouse-trail":
            hide = "#key-display,#history"
        elif mode == "history":
            hide = "#key-display"
        else:
            hide = "#history"
        file_path = OVERLAY_DIR / "overlay.html"
        content = file_path.read_text(encoding="utf-8")
        inject = (
            f'<script>window.__DISPLAY_MODE__="{mode}";window.__WS_PORT__="{_ws_port}";</script>'
            f'<style>{hide}{{display:none!important}}</style>'
        )
        content = content.replace("<head>", f"<head>{inject}", 1)
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _restart_server():
    """Restart the server process after a short delay.

    Runs on its own daemon thread, started only once by
    `_api_delete_restart`'s guard: the 0.5s sleep lets the HTTP response go
    out first, then tracked Remote Control input is released and standalone
    capture is stopped (each best-effort, independent of the other) before
    `os.execv`. If `os.execv` itself fails, the restart guard is reset so a
    later DELETE can retry.
    """
    global _restart_pending
    time.sleep(0.5)
    print("[Server] Restarting...")
    try:
        _set_rc_state(False)
    except Exception:
        logger.error("restart: failed to release remote-control input", exc_info=True)
    if _standalone_queue is not None:
        try:
            import standalone_capture
            standalone_capture.stop()
        except Exception:
            logger.error("restart: failed to stop standalone capture", exc_info=True)
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        logger.error("restart: os.execv failed", exc_info=True)
        with _restart_lock:
            _restart_pending = False


def start_http_server(port):
    global _http_server
    try:
        _http_server = ThreadingHTTPServer(("0.0.0.0", port), OverlayHandler)
    except OSError as e:
        print(f"[HTTP] ERROR: Failed to bind port {port}: {e}")
        print(f"[HTTP] Another process may be using port {port}.")
        print(f"[HTTP] Run: netstat -ano | findstr :{port}")
        return
    print(f"[HTTP] Config GUI: http://localhost:{port}/")
    print(f"[HTTP] Overlay:    http://localhost:{port}/overlay.html")
    try:
        _http_server.serve_forever()
    finally:
        # shutdown() 経由で抜けてきた場合もここで後始末する
        try:
            _http_server.server_close()
        except Exception:
            logger.debug("http server_close failed", exc_info=True)


def shutdown_http_server():
    """Gracefully stop the HTTP server. Safe to call from any thread."""
    global _http_server
    srv = _http_server
    _http_server = None
    if srv is None:
        return
    try:
        srv.shutdown()
    except Exception:
        logger.debug("http shutdown failed", exc_info=True)


async def browser_handler(ws):
    async with _browser_lock:
        browser_clients.add(ws)
    print(f"[Browser] Client connected ({len(browser_clients)} total)")
    try:
        config = load_config()
        await asyncio.wait_for(
            ws.send(json.dumps({"type": "config", "data": config})),
            timeout=_BROWSER_SEND_TIMEOUT,
        )
        async for msg in ws:
            pass
    except websockets.ConnectionClosed:
        pass
    finally:
        async with _browser_lock:
            browser_clients.discard(ws)
        print(f"[Browser] Client disconnected ({len(browser_clients)} total)")


async def sender_handler(ws):
    global sender_ws, _sender_synchronized
    # A newly accepted connection starts unsynchronized: fail-closed even if
    # remote_control_enabled happens to still be True from before (see
    # _rc_inject_event / _sender_ready).
    with _rc_lock:
        sender_ws = ws
        _sender_synchronized = False
    print(f"[Sender] Connected from {ws.remote_address}")
    try:
        async for msg in ws:
            try:
                event = json.loads(msg)
            except (json.JSONDecodeError, ValueError):
                continue

            # A replacement sender may already have connected while this
            # older handler was unwinding.  Its late messages must not change
            # the current connection's synchronization/RC state.
            with _rc_lock:
                is_current_sender = sender_ws is ws
            if not is_current_sender:
                continue

            # Handle remote_control toggle from sender: this is the only
            # message that may establish synchronized/ready state for this
            # connection, and it sets receiver RC state to exactly what the
            # sender reports (an explicit false keeps injection off).
            if event.get("type") == "remote_control":
                _set_rc_state(event.get("enabled", False), mark_synchronized=True)
                continue

            # Remote control: inject as OS input first (state check + inject
            # + tracked-state update happen atomically inside
            # _rc_inject_event), so a stalled browser send can never delay
            # injection.
            _rc_inject_event(event)

            # Broadcast to browsers (existing behavior)
            await broadcast_to_browsers(msg)
    finally:
        with _rc_lock:
            if sender_ws is ws:
                sender_ws = None
                _sender_synchronized = False
                was_active = remote_control_enabled
            else:
                was_active = False
        # Reset displayed input state on every sender disconnect, regardless
        # of Remote Control state, so a missing key-up/neutral axis does not
        # remain stuck on browser overlays. Broadcast this before the RC
        # disable notification below (a separate, independent state).
        await broadcast_to_browsers(json.dumps({"type": "input_reset"}))
        # If remote control was active, disable it
        if was_active:
            _set_rc_state(False)
        print("[Sender] Disconnected")


async def ws_handler(ws):
    path = ws.request.path if hasattr(ws, 'request') else (ws.path if hasattr(ws, 'path') else "/")
    if path == "/browser":
        await browser_handler(ws)
    else:
        await sender_handler(ws)


def _standalone_on_event(msg):
    """Callback from standalone_capture - puts event on the bounded async
    queue. Runs on the asyncio loop thread (standalone_capture._emit uses
    loop.call_soon_threadsafe to invoke this), so queue mutation here never
    races with the consumer in _standalone_broadcaster.

    On overflow, the queued backlog is sacrificed in favor of one
    `input_reset` message plus the newest event: a missing key-up/neutral
    axis left stuck on the overlay is worse than dropping stale history.
    """
    global _standalone_last_overflow_log
    if not _standalone_queue:
        return
    if _standalone_queue.full():
        now = time.time()
        if now - _standalone_last_overflow_log >= _STANDALONE_OVERFLOW_LOG_INTERVAL:
            logger.warning("standalone queue overflow (maxsize=%d): dropping backlog", _STANDALONE_QUEUE_MAXSIZE)
            _standalone_last_overflow_log = now
        while not _standalone_queue.empty():
            _standalone_queue.get_nowait()
        _standalone_queue.put_nowait(json.dumps({"type": "input_reset"}))
    _standalone_queue.put_nowait(msg)


async def _standalone_broadcaster():
    """Read events from standalone queue and broadcast to browsers."""
    while True:
        msg = await _standalone_queue.get()
        await broadcast_to_browsers(msg)


async def main(ws_port=8888, http_port=8080, standalone=False):
    global _ws_loop, _ws_port, _standalone_queue
    _ws_loop = asyncio.get_event_loop()
    _ws_port = ws_port

    http_thread = threading.Thread(
        target=start_http_server, args=(http_port,), daemon=True
    )
    http_thread.start()

    if standalone:
        import standalone_capture
        _standalone_queue = asyncio.Queue(maxsize=_STANDALONE_QUEUE_MAXSIZE)
        standalone_capture.start(_ws_loop, _standalone_on_event)

    print(f"[WS] Listening on port {ws_port}")

    async with websockets.serve(ws_handler, "0.0.0.0", ws_port):
        if standalone:
            await _standalone_broadcaster()
        else:
            await asyncio.Future()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Input Server")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--http-port", type=int, default=8081)
    parser.add_argument("--standalone", action="store_true",
                        help="Standalone mode: capture local input without sender")
    args = parser.parse_args()

    if args.standalone:
        print("[Mode] Standalone - local input capture")
    else:
        print("[Mode] Receiver - waiting for sender connection")

    try:
        asyncio.run(main(args.port, args.http_port, args.standalone))
    except KeyboardInterrupt:
        print("\n[Server] Stopping...")
    finally:
        shutdown_http_server()
        if args.standalone:
            try:
                import standalone_capture
                standalone_capture.stop()
            except Exception:
                logger.debug("standalone stop failed", exc_info=True)
        print("[Server] Stopped.")
