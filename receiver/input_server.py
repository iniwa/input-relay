"""
Input Server - Receives input events via WebSocket, serves overlay + config GUI.
Run on Sub PC.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse

import websockets

import input_injector

browser_clients = set()
_browser_lock = asyncio.Lock()
sender_ws = None
_ws_loop = None  # asyncio event loop, set in main()
_ws_port = 8888  # WebSocket port, set in main()

# Remote control state
remote_control_enabled = False
_rc_lock = threading.Lock()
_rc_pressed_keys = set()  # track pressed keys for stuck-key prevention

OVERLAY_DIR = Path(__file__).parent
CONFIG_PATH = OVERLAY_DIR / "config.json"
PRESETS_PATH = OVERLAY_DIR / "presets.json"
LAYOUT_PRESETS_PATH = OVERLAY_DIR / "layout_presets.json"


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def save_config(data):
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_PRESET_TYPES = {"keyboard", "leverless", "controller"}

def load_presets():
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
        save_presets(migrated)
        return migrated
    return data


def save_presets(data):
    PRESETS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_layout_presets():
    empty = {"keyboard": {}, "leverless": {}, "controller": {}}
    if not LAYOUT_PRESETS_PATH.exists():
        return empty
    data = json.loads(LAYOUT_PRESETS_PATH.read_text(encoding="utf-8"))
    for t in _PRESET_TYPES:
        if t not in data:
            data[t] = {}
    return data


def save_layout_presets(data):
    LAYOUT_PRESETS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def broadcast_to_browsers(message):
    async with _browser_lock:
        clients = list(browser_clients)
    if clients:
        await asyncio.gather(
            *[c.send(message) for c in clients],
            return_exceptions=True,
        )


async def _send_to_sender(data):
    """Send a message back to the sender over the existing WebSocket."""
    if sender_ws:
        try:
            await sender_ws.send(json.dumps(data))
        except Exception:
            pass


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


class OverlayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")

        if path == "api/config":
            self._json_response(load_config())
            return

        if path in ("history", "input", "mouse-trail"):
            self._serve_overlay_with_mode(path)
            return

        if path == "api/presets":
            self._json_response(load_presets())
            return

        if path == "api/layout-presets":
            self._json_response(load_layout_presets())
            return

        if path == "api/remote-control":
            with _rc_lock:
                enabled = remote_control_enabled
            self._json_response({"enabled": enabled})
            return

        if path == "api/sender-config":
            # Read sender config from sender dir (if accessible)
            sender_cfg_path = OVERLAY_DIR.parent / "sender" / "sender_config.json"
            if sender_cfg_path.exists():
                self._json_response(json.loads(sender_cfg_path.read_text(encoding="utf-8")))
            else:
                self._json_response({})
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
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if parsed.path == "/api/config":
            try:
                data = json.loads(body)
                save_config(data)
                msg = json.dumps({"type": "config", "data": data})
                if _ws_loop:
                    future = asyncio.run_coroutine_threadsafe(broadcast_to_browsers(msg), _ws_loop)
                    try:
                        future.result(timeout=2)
                    except Exception as e:
                        print(f"[WS] Broadcast failed: {e}")
                else:
                    print("[WS] Warning: _ws_loop is None, broadcast skipped")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/presets":
            try:
                data = json.loads(body)
                ptype = data.get("type", "keyboard")
                name = data["name"]
                presets = load_presets()
                if ptype not in presets:
                    presets[ptype] = {}
                presets[ptype][name] = {ptype: data[ptype], "layout": data.get("layout", {}), "inputHistory": data.get("inputHistory", {})}
                save_presets(presets)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/layout-presets":
            try:
                data = json.loads(body)
                ptype = data.get("type", "keyboard")
                name = data["name"]
                presets = load_layout_presets()
                if ptype not in presets:
                    presets[ptype] = {}
                presets[ptype][name] = {"layout": data.get("layout", {}), "inputHistory": data.get("inputHistory", {})}
                save_layout_presets(presets)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/sender-config":
            try:
                data = json.loads(body)
                sender_cfg_path = OVERLAY_DIR.parent / "sender" / "sender_config.json"
                sender_cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/refresh":
            try:
                data = load_config()
                msg = json.dumps({"type": "config", "data": data})
                if _ws_loop:
                    future = asyncio.run_coroutine_threadsafe(broadcast_to_browsers(msg), _ws_loop)
                    try:
                        future.result(timeout=2)
                    except Exception as e:
                        print(f"[WS] Refresh broadcast failed: {e}")
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/remote-control":
            try:
                data = json.loads(body)
                enabled = bool(data.get("enabled", False))
                _set_rc_state(enabled)
                # Notify sender to toggle input suppression
                if _ws_loop:
                    asyncio.run_coroutine_threadsafe(
                        _send_to_sender({"type": "remote_control", "enabled": enabled}),
                        _ws_loop,
                    )
                self._json_response({"ok": True, "enabled": enabled})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/mode-switch":
            try:
                data = json.loads(body)
                mode = data.get("mode", "keyboard")
                msg = json.dumps({
                    "type": "mode_switch",
                    "key": mode,
                    "source": "system",
                    "timestamp": time.time(),
                })
                if _ws_loop:
                    asyncio.run_coroutine_threadsafe(
                        broadcast_to_browsers(msg), _ws_loop
                    )
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if parsed.path == "/api/presets":
            try:
                data = json.loads(body)
                ptype = data.get("type", "keyboard")
                presets = load_presets()
                presets.get(ptype, {}).pop(data["name"], None)
                save_presets(presets)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/layout-presets":
            try:
                data = json.loads(body)
                ptype = data.get("type", "keyboard")
                presets = load_layout_presets()
                presets.get(ptype, {}).pop(data["name"], None)
                save_layout_presets(presets)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/restart":
            self._json_response({"ok": True})
            threading.Thread(target=_restart_server, daemon=True).start()
            return

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
    try:
        server = HTTPServer(("0.0.0.0", port), OverlayHandler)
    except OSError as e:
        print(f"[HTTP] ERROR: Failed to bind port {port}: {e}")
        print(f"[HTTP] Another process may be using port {port}.")
        print(f"[HTTP] Run: netstat -ano | findstr :{port}")
        return
    print(f"[HTTP] Config GUI: http://localhost:{port}/")
    print(f"[HTTP] Overlay:    http://localhost:{port}/overlay.html")
    server.serve_forever()


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


async def main(ws_port=8888, http_port=8080):
    global _ws_loop, _ws_port
    _ws_loop = asyncio.get_event_loop()
    _ws_port = ws_port

    http_thread = threading.Thread(
        target=start_http_server, args=(http_port,), daemon=True
    )
    http_thread.start()

    print(f"[WS] Listening on port {ws_port}")

    async with websockets.serve(ws_handler, "0.0.0.0", ws_port):
        await asyncio.Future()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Input Server")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--http-port", type=int, default=8081)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.port, args.http_port))
    except KeyboardInterrupt:
        print("\n[Server] Stopped.")
