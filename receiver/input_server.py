"""
Input Server - Receives input events via WebSocket, serves overlay + config GUI.
Run on Sub PC.
"""

import asyncio
import json
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse

import websockets

browser_clients = set()
sender_ws = None

OVERLAY_DIR = Path(__file__).parent
CONFIG_PATH = OVERLAY_DIR / "config.json"
PRESETS_PATH = OVERLAY_DIR / "presets.json"


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def save_config(data):
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_presets():
    if PRESETS_PATH.exists():
        return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    return {}


def save_presets(data):
    PRESETS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class OverlayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")

        if path == "api/config":
            self._json_response(load_config())
            return

        if path == "api/presets":
            self._json_response(load_presets())
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
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        if parsed.path == "/api/presets":
            try:
                data = json.loads(body)
                presets = load_presets()
                preset = {"keyboard": data.get("keyboard", {}), "leverless": data.get("leverless", {})}
                if "controller" in data:
                    preset["controller"] = data["controller"]
                presets[data["name"]] = preset
                save_presets(presets)
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

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if parsed.path == "/api/presets":
            try:
                data = json.loads(body)
                presets = load_presets()
                presets.pop(data["name"], None)
                save_presets(presets)
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
            return

        self.send_response(404)
        self.end_headers()

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_http_server(port):
    server = HTTPServer(("0.0.0.0", port), OverlayHandler)
    print(f"[HTTP] Config GUI: http://localhost:{port}/")
    print(f"[HTTP] Overlay:    http://localhost:{port}/overlay.html")
    server.serve_forever()


async def browser_handler(ws):
    browser_clients.add(ws)
    print(f"[Browser] Client connected ({len(browser_clients)} total)")
    config = load_config()
    await ws.send(json.dumps({"type": "config", "data": config}))
    try:
        async for msg in ws:
            pass
    finally:
        browser_clients.discard(ws)
        print(f"[Browser] Client disconnected ({len(browser_clients)} total)")


async def sender_handler(ws):
    global sender_ws
    sender_ws = ws
    print(f"[Sender] Connected from {ws.remote_address}")
    try:
        async for msg in ws:
            if browser_clients:
                await asyncio.gather(
                    *[client.send(msg) for client in browser_clients],
                    return_exceptions=True,
                )
    finally:
        sender_ws = None
        print("[Sender] Disconnected")


async def ws_handler(ws):
    path = ws.request.path if hasattr(ws, 'request') else (ws.path if hasattr(ws, 'path') else "/")
    if path == "/browser":
        await browser_handler(ws)
    else:
        await sender_handler(ws)


async def main(ws_port=8765, http_port=8080):
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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--http-port", type=int, default=8080)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.port, args.http_port))
    except KeyboardInterrupt:
        print("\n[Server] Stopped.")
