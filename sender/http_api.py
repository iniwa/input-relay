"""
Sender HTTP config API.

sender_gui.html 向けの設定 API・ステータス API・コントローラ操作 API を提供する。
input_sender.py のグローバル状態には直接触れず、SenderContext 経由で必要な
アクセサだけを受け取る（循環 import を避けるため）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("http_api")

REFRESH_WAIT = 0.3   # GUI からの refresh 後 gamepad 反映待ち (秒)
RESTART_DELAY = 0.5  # GUI からの restart 要求遅延 (秒)


@dataclass
class SenderContext:
    """HTTP ハンドラが必要とする sender 側状態への薄いアクセサ集合。"""

    gui_path: Path
    get_config: Callable[[], dict]
    save_config: Callable[[dict], None]
    trigger_reconnect: Callable[[], None]
    get_gamepad: Callable[[], Optional[object]]
    valid_overlay_positions: tuple
    get_ws_status: Callable[[], str]
    get_remote_mode: Callable[[], bool]
    get_input_timestamps: Callable[[], Tuple[float, float]]


def make_handler(ctx: SenderContext):
    """SenderHTTPHandler を ctx にバインドして生成する。"""

    class SenderHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # suppress access logs

        def _send_json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", len(body))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                logger.debug("HTTP client disconnected before JSON response completed")

        def _send_html(self, path):
            try:
                content = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", len(content))
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                logger.debug("HTTP client disconnected before HTML response completed")
            except FileNotFoundError:
                self.send_error(404)

        def _read_body(self):
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/" or path == "/index.html":
                self._send_html(ctx.gui_path)
            elif path == "/api/config":
                self._send_json(ctx.get_config())
            elif path == "/api/controllers":
                config = ctx.get_config()
                gamepad = ctx.get_gamepad()
                self._send_json({
                    "enabled": bool(config.get("gamepad_enabled", False)),
                    "controllers": gamepad.info() if gamepad else [],
                    "selected": gamepad.selected_id() if gamepad else 0,
                })
            elif path == "/api/status":
                config = ctx.get_config()
                gamepad = ctx.get_gamepad()
                kbd_ts, gp_ts = ctx.get_input_timestamps()
                self._send_json({
                    "ws_status": ctx.get_ws_status(),
                    "host": config.get("host", ""),
                    "port": config.get("port", 8888),
                    "selected_controller": gamepad.selected_id() if gamepad else 0,
                    "remote_mode": ctx.get_remote_mode(),
                    # Multi-PC activity detection 用フィールド（未観測は 0.0）
                    "last_kbd_mouse_ts": kbd_ts,
                    "last_gamepad_ts": gp_ts,
                    "server_time": time.time(),
                })
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/config":
                self._handle_save_config()
            elif path == "/api/select-controller":
                self._handle_select_controller()
            elif path == "/api/refresh-controllers":
                self._handle_refresh_controllers()
            elif path == "/api/restart":
                self._handle_restart()
            else:
                self.send_error(404)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def _handle_save_config(self):
            config = ctx.get_config()
            data = self._read_body()
            prev_host = config.get("host")
            prev_port = config.get("port")
            config["host"] = data.get("host", prev_host)
            config["port"] = int(data.get("port", prev_port or 8888))
            if "gamepad_enabled" in data:
                config["gamepad_enabled"] = bool(data["gamepad_enabled"])
            if "raw_mouse_enabled" in data:
                config["raw_mouse_enabled"] = bool(data["raw_mouse_enabled"])
            # Remote overlay 関連項目の更新
            if "local_name" in data:
                config["local_name"] = str(data.get("local_name") or "")
            if "target_name" in data:
                config["target_name"] = str(data.get("target_name") or "")
            if "remote_overlay" in data and isinstance(data["remote_overlay"], dict):
                overlay = config.get("remote_overlay") or {}
                if not isinstance(overlay, dict):
                    overlay = {}
                incoming = data["remote_overlay"]
                if "enabled" in incoming:
                    overlay["enabled"] = bool(incoming["enabled"])
                if "position" in incoming:
                    pos = str(incoming["position"])
                    if pos in ctx.valid_overlay_positions:
                        overlay["position"] = pos
                config["remote_overlay"] = overlay
            ctx.save_config(config)
            # host/port が変化したときだけ再接続をトリガ
            if config["host"] != prev_host or config["port"] != prev_port:
                ctx.trigger_reconnect()
            self._send_json({"ok": True})

        def _handle_select_controller(self):
            data = self._read_body()
            cid = int(data.get("id", 0))
            gamepad = ctx.get_gamepad()
            if gamepad is not None:
                gamepad.select(cid)
                name = next(
                    (c["name"] for c in gamepad.info() if c["id"] == cid),
                    "Unknown",
                )
            else:
                name = "Unknown"
            print(f"[GUI] Controller selected: {name} (ID: {cid})")
            self._send_json({"ok": True, "id": cid, "name": name})

        def _handle_refresh_controllers(self):
            gamepad = ctx.get_gamepad()
            if gamepad is not None:
                gamepad.request_refresh()
                time.sleep(REFRESH_WAIT)
                controllers = gamepad.info()
                sel = gamepad.selected_id()
            else:
                controllers, sel = [], 0
            self._send_json({
                "controllers": controllers,
                "selected": sel,
                "count": len(controllers),
            })

        def _handle_restart(self):
            self._send_json({"ok": True, "message": "Restarting..."})
            print("[Sender] Restart requested via GUI. Restarting process...")
            # Use os.execv to replace current process with a fresh instance
            threading.Timer(
                RESTART_DELAY,
                lambda: os.execv(sys.executable, [sys.executable] + sys.argv),
            ).start()

    return SenderHTTPHandler


def start_http_server(ctx: SenderContext, port):
    handler = make_handler(ctx)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"[HTTP] GUI server at http://localhost:{port}/")
    server.serve_forever()
