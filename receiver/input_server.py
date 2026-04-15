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
sender_ws = None
_ws_loop = None  # asyncio event loop, set in main()
_ws_port = 8888  # WebSocket port, set in main()
_http_server = None  # ThreadingHTTPServer instance, for shutdown

# Standalone mode
_standalone = False
_standalone_queue = None  # asyncio.Queue, set in main() when standalone

# Remote control state
remote_control_enabled = False
_rc_lock = threading.Lock()
_rc_pressed_keys = set()  # track pressed keys for stuck-key prevention

OVERLAY_DIR = Path(__file__).parent
CONFIG_DIR = OVERLAY_DIR.parent / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
PRESETS_PATH = CONFIG_DIR / "presets.json"
LAYOUT_PRESETS_PATH = CONFIG_DIR / "layout_presets.json"

# LAN 公開前提のため、複数クライアントからの同時 POST/DELETE を直列化。
# どの path も同じロックで保護 (頻度が低いので単一ロックで十分)。
_config_io_lock = threading.Lock()


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
            PRESETS_PATH.write_text(
                json.dumps(migrated, indent=2, ensure_ascii=False), encoding="utf-8",
            )
            return migrated
        return data


def save_presets(data):
    with _config_io_lock:
        PRESETS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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
        LAYOUT_PRESETS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


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


async def broadcast_to_browsers(message):
    async with _browser_lock:
        clients = list(browser_clients)
    if clients:
        await asyncio.gather(
            *[c.send(message) for c in clients],
            return_exceptions=True,
        )


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
    """Send a message back to the sender over the existing WebSocket."""
    if sender_ws:
        try:
            await sender_ws.send(json.dumps(data))
        except Exception:
            logger.debug("send to sender failed", exc_info=True)


def _set_rc_state(enabled):
    """Set remote control state and handle cleanup."""
    global remote_control_enabled
    with _rc_lock:
        remote_control_enabled = enabled
    state = "ENABLED" if enabled else "DISABLED"
    print(f"[RemoteControl] {state}")
    if not enabled:
        input_injector.release_all(_rc_pressed_keys)
    # Broadcast to browsers
    msg = json.dumps({"type": "remote_control_state", "enabled": enabled})
    if _ws_loop:
        asyncio.run_coroutine_threadsafe(broadcast_to_browsers(msg), _ws_loop)


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
    data = json.loads(body)
    sender_cfg_path = CONFIG_DIR / "sender_config.json"
    with _config_io_lock:
        sender_cfg_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    print(f"[API] sender-config updated by {_client_label(handler)}")
    _broadcast_change("sender_config", {"data": data})
    return {"ok": True}


def _api_post_refresh(handler, body):
    data = load_config()
    _broadcast_change("config", {"data": data})
    return {"ok": True}


def _api_post_remote_control(handler, body):
    data = json.loads(body)
    enabled = bool(data.get("enabled", False))
    _set_rc_state(enabled)
    if _ws_loop:
        asyncio.run_coroutine_threadsafe(
            _send_to_sender({"type": "remote_control", "enabled": enabled}),
            _ws_loop,
        )
    return {"ok": True, "enabled": enabled}


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
    presets = load_layout_presets()
    presets.get(ptype, {}).pop(name, None)
    save_layout_presets(presets)
    print(f"[API] layout-preset deleted: {ptype}/{name} by {_client_label(handler)}")
    _broadcast_change("layout_presets", {"type": ptype, "name": name, "op": "delete"})
    return {"ok": True}


def _api_delete_restart(handler, body):
    # _restart_server は 0.5s 待ってから execv するため、レスポンス送信が先行する
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
        except Exception as e:
            logger.debug("API %s failed", path, exc_info=True)
            self._json_response({"error": str(e)}, 400)
        return True

    def _read_body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

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
        file_path = OVERLAY_DIR / path
        if file_path.exists() and file_path.is_file():
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
    """Restart the server process after a short delay."""
    time.sleep(0.5)
    print("[Server] Restarting...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


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
    config = load_config()
    try:
        await ws.send(json.dumps({"type": "config", "data": config}))
    except websockets.ConnectionClosed:
        async with _browser_lock:
            browser_clients.discard(ws)
        return
    try:
        async for msg in ws:
            pass
    finally:
        async with _browser_lock:
            browser_clients.discard(ws)
        print(f"[Browser] Client disconnected ({len(browser_clients)} total)")


async def sender_handler(ws):
    global sender_ws
    sender_ws = ws
    print(f"[Sender] Connected from {ws.remote_address}")
    try:
        async for msg in ws:
            try:
                event = json.loads(msg)
            except (json.JSONDecodeError, ValueError):
                continue

            # Handle remote_control toggle from sender
            if event.get("type") == "remote_control":
                _set_rc_state(event.get("enabled", False))
                continue

            # Broadcast to browsers (existing behavior)
            async with _browser_lock:
                clients = list(browser_clients)
            if clients:
                await asyncio.gather(
                    *[client.send(msg) for client in clients],
                    return_exceptions=True,
                )

            # Remote control: inject as OS input
            with _rc_lock:
                rc_active = remote_control_enabled
            if rc_active:
                try:
                    input_injector.replay_event(event)
                    # Track pressed keys for stuck-key prevention
                    if event.get("type") == "key_down":
                        _rc_pressed_keys.add(event.get("key", ""))
                    elif event.get("type") == "key_up":
                        _rc_pressed_keys.discard(event.get("key", ""))
                except Exception as e:
                    print(f"[RemoteControl] Inject error: {e}")
    finally:
        if sender_ws is ws:
            sender_ws = None
        # If remote control was active, disable it
        with _rc_lock:
            was_active = remote_control_enabled
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
    """Callback from standalone_capture - puts event on async queue."""
    if _standalone_queue:
        _standalone_queue.put_nowait(msg)


async def _standalone_broadcaster():
    """Read events from standalone queue and broadcast to browsers."""
    while True:
        msg = await _standalone_queue.get()
        await broadcast_to_browsers(msg)


async def main(ws_port=8888, http_port=8080, standalone=False):
    global _ws_loop, _ws_port, _standalone, _standalone_queue
    _ws_loop = asyncio.get_event_loop()
    _ws_port = ws_port
    _standalone = standalone

    http_thread = threading.Thread(
        target=start_http_server, args=(http_port,), daemon=True
    )
    http_thread.start()

    if standalone:
        import standalone_capture
        _standalone_queue = asyncio.Queue()
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
