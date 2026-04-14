"""
Input Sender - Captures keyboard/gamepad/mouse input and sends to Sub PC via WebSocket.
Run on Main PC. Includes local HTTP server for configuration GUI.
"""

import asyncio
import json
import os
import sys
import time
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import websockets
from pynput import keyboard, mouse

import overlay_window
import raw_mouse

pygame = None

# Load config
CONFIG_PATH = Path(__file__).parent.parent / "config" / "sender_config.json"
GUI_PATH = Path(__file__).parent / "sender_gui.html"

_CONFIG_DEFAULTS = {
    "host": "localhost",
    "port": 8888,
    "toggleKey": "f12",
    "local_name": "",
    "target_name": "Sub PC",
    "remote_overlay": {
        "enabled": True,
        "position": "top-left",
    },
}


def _merge_defaults(loaded, defaults):
    """Recursively fill missing keys in loaded dict with defaults."""
    for k, v in defaults.items():
        if k not in loaded:
            loaded[k] = v
        elif isinstance(v, dict) and isinstance(loaded.get(k), dict):
            _merge_defaults(loaded[k], v)
    return loaded


def load_config():
    if CONFIG_PATH.exists():
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return _merge_defaults(loaded, _CONFIG_DEFAULTS)
    return json.loads(json.dumps(_CONFIG_DEFAULTS))  # deep copy

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

config = load_config()

ws_connection = None
ws_status = "disconnected"  # "connected", "disconnected", "connecting"
running = True
pressed_keys = set()
_pressed_keys_lock = threading.Lock()
_loop = None  # asyncio event loop, set in main()

# Remote control mode
_remote_mode = False
_remote_lock = threading.Lock()
_remote_toggle_event = None  # asyncio.Event, signals listener restart
_kb_listener = None
_mouse_listener = None

# Remote overlay window — tkinter を別スレッドで管理するマネージャー
_overlay_manager = overlay_window.OverlayManager(lambda: config)
_OVERLAY_POSITIONS = overlay_window.valid_positions()

# Controller selection state
selected_controller_id = 0
_controller_lock = threading.Lock()
_request_refresh = False  # signal gamepad_loop to re-scan
_controller_info = []  # cached list of controller dicts

# Monitor: async queue for broadcasting to WebSocket clients
_monitor_queue = None  # asyncio.Queue, created in main()
_monitor_clients = set()  # managed only from asyncio thread

# Modifier key mapping: normalize left/right variants to base name
_MODIFIER_MAP = {
    keyboard.Key.shift: 'shift',
    keyboard.Key.shift_l: 'shift',
    keyboard.Key.shift_r: 'shift',
    keyboard.Key.ctrl: 'ctrl',
    keyboard.Key.ctrl_l: 'ctrl',
    keyboard.Key.ctrl_r: 'ctrl',
    keyboard.Key.alt: 'alt',
    keyboard.Key.alt_l: 'alt',
    keyboard.Key.alt_r: 'alt',
}


def _freeze_cursor():
    """Lock the cursor to its current position using ClipCursor."""
    import ctypes
    from ctypes import wintypes
    pos = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pos))
    rect = wintypes.RECT(pos.x, pos.y, pos.x + 1, pos.y + 1)
    ctypes.windll.user32.ClipCursor(ctypes.byref(rect))


def _unfreeze_cursor():
    """Release cursor lock."""
    import ctypes
    ctypes.windll.user32.ClipCursor(None)


def _set_remote_mode(enabled):
    """Toggle remote control mode. Restarts listeners synchronously so the
    suppress hook is active before the next OS input is dispatched."""
    global _remote_mode
    with _remote_lock:
        was = _remote_mode
        _remote_mode = enabled
    if was == enabled:
        return
    print(f"[Remote] {'ENABLED' if enabled else 'DISABLED'}")
    if enabled:
        _overlay_manager.set_user_hidden(False)
        _freeze_cursor()
        _overlay_manager.show()
    else:
        _overlay_manager.hide()
        _unfreeze_cursor()
    # 新 listener を先に立ち上げてから旧 listener を stop する（ラグ窓を消す）。
    # この関数は pynput の on_press 内から呼ばれ得るが、stop() はフラグを立てる
    # だけなので自スレッドから呼んでもデッドロックしない。
    _restart_listeners(suppress=enabled)
    # receiver / monitor への通知のみ非同期で
    if _loop is not None and _remote_toggle_event is not None:
        _loop.call_soon_threadsafe(_remote_toggle_event.set)


def load_pygame():
    global pygame
    import pygame as pg
    pygame = pg
    pygame.init()
    pygame.joystick.init()


def make_event(event_type, key, source="keyboard", vk=None):
    ev = {
        "type": event_type,
        "key": key,
        "source": source,
        "timestamp": time.time(),
    }
    if vk is not None:
        ev["vk"] = vk
    return json.dumps(ev)


def key_to_str(key):
    # Check modifier map first
    if key in _MODIFIER_MAP:
        return _MODIFIER_MAP[key]
    # Use vk (virtual key code) to get modifier-independent key name
    # This prevents Shift+1 becoming '!' or Ctrl+C becoming '\x03'
    vk = getattr(key, 'vk', None)
    if vk is not None:
        if 0x30 <= vk <= 0x39:  # 0-9
            return chr(vk)
        if 0x41 <= vk <= 0x5A:  # A-Z
            return chr(vk).lower()
    if hasattr(key, "char") and key.char is not None:
        return key.char.lower()
    if hasattr(key, "name"):
        return key.name
    # Fallback: use VK code as identifier (e.g. Japanese IME keys)
    if vk is not None:
        return f"vk_{vk}"
    return str(key)


class JsonQueue:
    def __init__(self):
        self._queue = asyncio.Queue()

    def put(self, data):
        # Must use call_soon_threadsafe when called from non-asyncio threads
        if _loop is not None:
            _loop.call_soon_threadsafe(self._queue.put_nowait, data)

    async def get(self):
        return await self._queue.get()


event_queue = JsonQueue()


def enqueue_monitor(data):
    """Thread-safe: push data to the monitor broadcast queue."""
    if _loop is not None and _monitor_queue is not None:
        try:
            _loop.call_soon_threadsafe(_monitor_queue.put_nowait, data)
        except Exception:
            pass


def _get_vk(key):
    """Extract Windows virtual key code from a pynput key."""
    vk = getattr(key, 'vk', None)
    if vk is not None:
        return vk
    # pynput Key enum members have a value with vk
    value = getattr(key, 'value', None)
    if value is not None:
        return getattr(value, 'vk', None)
    return None


def on_press(key):
    # Scroll Lock toggles remote control mode
    if key == keyboard.Key.scroll_lock:
        with _remote_lock:
            new_state = not _remote_mode
        _set_remote_mode(new_state)
        return  # Don't send Scroll Lock to receiver

    # Pause はリモート中にオーバーレイの表示/非表示を切り替える
    if key == keyboard.Key.pause:
        with _remote_lock:
            in_remote = _remote_mode
        if in_remote:
            now_hidden = not _overlay_manager.is_user_hidden()
            _overlay_manager.set_user_hidden(now_hidden)
            if now_hidden:
                _overlay_manager.hide()
            else:
                _overlay_manager.show()
            return  # Don't send Pause to receiver

    key_str = key_to_str(key)
    vk = _get_vk(key)
    with _pressed_keys_lock:
        is_repeat = key_str in pressed_keys
        if not is_repeat:
            pressed_keys.add(key_str)
    # リモートモード中はキーリピートも転送（長押し対応）
    if not is_repeat or _remote_mode:
        msg = make_event("key_down", key_str, vk=vk)
        event_queue.put(msg)
        if not is_repeat:
            enqueue_monitor(msg)


def on_release(key):
    key_str = key_to_str(key)
    vk = _get_vk(key)
    with _pressed_keys_lock:
        pressed_keys.discard(key_str)
    msg = make_event("key_up", key_str, vk=vk)
    event_queue.put(msg)
    enqueue_monitor(msg)


# --- Mouse movement (Raw Input API, 60Hz throttled) ---
def _on_raw_mouse_delta(dx, dy):
    """raw_mouse モジュールから 16ms 間隔で呼ばれる。"""
    msg = json.dumps({
        "type": "mouse_move",
        "dx": dx,
        "dy": dy,
        "source": "mouse",
        "timestamp": time.time(),
    })
    event_queue.put(msg)
    enqueue_monitor(msg)


def raw_mouse_loop():
    raw_mouse.run(lambda: running, _on_raw_mouse_delta)


# --- Mouse listener ---
def on_mouse_click(x, y, button, pressed):
    btn_map = {
        mouse.Button.left: 'mouse_left',
        mouse.Button.right: 'mouse_right',
        mouse.Button.middle: 'mouse_middle',
        mouse.Button.x1: 'mouse_x1',
        mouse.Button.x2: 'mouse_x2',
    }
    key_str = btn_map.get(button)
    if not key_str:
        return
    etype = "key_down" if pressed else "key_up"
    msg = make_event(etype, key_str, "mouse")
    event_queue.put(msg)
    enqueue_monitor(msg)


def on_mouse_scroll(x, y, dx, dy):
    msg = json.dumps({
        "type": "mouse_scroll",
        "dx": dx,
        "dy": dy,
        "source": "mouse",
        "timestamp": time.time(),
    })
    event_queue.put(msg)
    enqueue_monitor(msg)


def scan_controllers():
    """Scan for available controllers and return list of info dicts."""
    global _controller_info
    if pygame is None:
        return []
    pygame.joystick.quit()
    pygame.joystick.init()
    controllers = []
    for i in range(pygame.joystick.get_count()):
        try:
            j = pygame.joystick.Joystick(i)
            j.init()
            controllers.append({
                "id": i,
                "name": j.get_name(),
                "buttons": j.get_numbuttons(),
                "axes": j.get_numaxes(),
                "hats": j.get_numhats(),
            })
        except Exception:
            pass
    _controller_info = controllers
    return controllers


_GAMEPAD_POLL_INTERVAL = 1.0 / 60  # 60Hz


def gamepad_loop():
    global selected_controller_id, _request_refresh
    load_pygame()
    joy = None
    joy_id = -1
    prev_buttons = {}
    prev_axes = {}       # leverless: threshold-based axis state
    prev_axes_raw = {}   # controller: raw float values
    last_reinit = 0

    # Initial scan
    scan_controllers()

    try:
      _run_gamepad_main(joy, joy_id, prev_buttons, prev_axes, prev_axes_raw, last_reinit)
    finally:
        if pygame is not None:
            try:
                pygame.joystick.quit()
                pygame.quit()
            except Exception:
                pass


def _run_gamepad_main(joy, joy_id, prev_buttons, prev_axes, prev_axes_raw, last_reinit):
    global selected_controller_id, _request_refresh
    while running:
        pygame.event.pump()

        # Handle refresh request from API
        if _request_refresh:
            _request_refresh = False
            scan_controllers()
            # If current joy is no longer valid, disconnect
            with _controller_lock:
                target_id = selected_controller_id
            if joy is not None and joy_id != target_id:
                joy = None
                prev_buttons.clear()
                prev_axes.clear()
                prev_axes_raw.clear()

        with _controller_lock:
            target_id = selected_controller_id

        if joy is None and time.time() - last_reinit > 2.0:
            pygame.joystick.quit()
            pygame.joystick.init()
            last_reinit = time.time()
            scan_controllers()

        if pygame.joystick.get_count() > 0:
            if joy is None:
                # Use selected controller ID, fallback to 0
                use_id = target_id if target_id < pygame.joystick.get_count() else 0
                joy = pygame.joystick.Joystick(use_id)
                joy.init()
                joy_id = use_id
                with _controller_lock:
                    selected_controller_id = use_id
                print(f"[Gamepad] Connected: {joy.get_name()} (ID: {use_id})")
        else:
            if joy is not None:
                joy = None
                joy_id = -1
                prev_buttons.clear()
                prev_axes.clear()
                prev_axes_raw.clear()
            time.sleep(0.1)
            continue

        # If user selected a different controller, switch
        if target_id != joy_id and target_id < pygame.joystick.get_count():
            joy = pygame.joystick.Joystick(target_id)
            joy.init()
            joy_id = target_id
            prev_buttons.clear()
            prev_axes.clear()
            prev_axes_raw.clear()
            print(f"[Gamepad] Switched to: {joy.get_name()} (ID: {target_id})")

        # Buttons — always send regardless of mode
        for i in range(joy.get_numbuttons()):
            val = joy.get_button(i)
            if val != prev_buttons.get(i, 0):
                prev_buttons[i] = val
                etype = "key_down" if val else "key_up"
                msg = make_event(etype, f"btn_{i}", "gamepad")
                event_queue.put(msg)
                enqueue_monitor(msg)

        # Hats — send in both leverless and controller modes
        for i in range(joy.get_numhats()):
            hat = joy.get_hat(i)
            prev_hat = prev_axes.get(f"hat_{i}", (0, 0))
            if hat != prev_hat:
                if prev_hat[0] != 0:
                    msg = make_event("key_up", f"hat_{i}_{'left' if prev_hat[0] < 0 else 'right'}", "gamepad")
                    event_queue.put(msg)
                    enqueue_monitor(msg)
                if prev_hat[1] != 0:
                    msg = make_event("key_up", f"hat_{i}_{'down' if prev_hat[1] < 0 else 'up'}", "gamepad")
                    event_queue.put(msg)
                    enqueue_monitor(msg)
                if hat[0] != 0:
                    msg = make_event("key_down", f"hat_{i}_{'left' if hat[0] < 0 else 'right'}", "gamepad")
                    event_queue.put(msg)
                    enqueue_monitor(msg)
                if hat[1] != 0:
                    msg = make_event("key_down", f"hat_{i}_{'down' if hat[1] < 0 else 'up'}", "gamepad")
                    event_queue.put(msg)
                    enqueue_monitor(msg)
                prev_axes[f"hat_{i}"] = hat

        # Axes — always send both threshold-based and continuous
        deadzone = 0.5
        for i in range(joy.get_numaxes()):
            raw = joy.get_axis(i)
            # Threshold-based (leverless style)
            if raw < -deadzone:
                val = -1
            elif raw > deadzone:
                val = 1
            else:
                val = 0
            prev = prev_axes.get(i, 0)
            if val != prev:
                if prev != 0:
                    msg = make_event("key_up", f"axis_{i}_{'neg' if prev < 0 else 'pos'}", "gamepad")
                    event_queue.put(msg)
                    enqueue_monitor(msg)
                if val != 0:
                    msg = make_event("key_down", f"axis_{i}_{'neg' if val < 0 else 'pos'}", "gamepad")
                    event_queue.put(msg)
                    enqueue_monitor(msg)
                prev_axes[i] = val
            # Continuous (controller style)
            if abs(raw - prev_axes_raw.get(i, 2.0)) > 0.01:
                prev_axes_raw[i] = raw
                msg = json.dumps({
                    "type": "axis_update",
                    "axis": i,
                    "value": round(raw, 3),
                    "source": "gamepad",
                    "timestamp": time.time(),
                })
                event_queue.put(msg)
                enqueue_monitor(msg)

        time.sleep(_GAMEPAD_POLL_INTERVAL)


# --- HTTP Server for GUI ---
class SenderHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # suppress access logs

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        try:
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_html(GUI_PATH)
        elif path == "/api/config":
            self._send_json(config)
        elif path == "/api/controllers":
            with _controller_lock:
                sel = selected_controller_id
            self._send_json({"controllers": list(_controller_info), "selected": sel})
        elif path == "/api/status":
            with _controller_lock:
                sel = selected_controller_id
            with _remote_lock:
                remote = _remote_mode
            self._send_json({
                "ws_status": ws_status,
                "host": config.get("host", ""),
                "port": config.get("port", 8888),
                "selected_controller": sel,
                "remote_mode": remote,
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
        global config
        data = self._read_body()
        prev_host = config.get("host")
        prev_port = config.get("port")
        config["host"] = data.get("host", prev_host)
        config["port"] = int(data.get("port", prev_port or 8888))
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
                if pos in _OVERLAY_POSITIONS:
                    overlay["position"] = pos
            config["remote_overlay"] = overlay
        save_config(config)
        # host/port が変化したときだけ再接続をトリガ
        if (config["host"] != prev_host or config["port"] != prev_port) and _loop is not None:
            _loop.call_soon_threadsafe(_trigger_reconnect)
        self._send_json({"ok": True})

    def _handle_select_controller(self):
        global selected_controller_id
        data = self._read_body()
        cid = int(data.get("id", 0))
        with _controller_lock:
            selected_controller_id = cid
        # Find name
        name = "Unknown"
        for c in _controller_info:
            if c["id"] == cid:
                name = c["name"]
                break
        print(f"[GUI] Controller selected: {name} (ID: {cid})")
        self._send_json({"ok": True, "id": cid, "name": name})

    def _handle_refresh_controllers(self):
        global _request_refresh
        _request_refresh = True
        # Wait briefly for gamepad_loop to process
        time.sleep(0.3)
        with _controller_lock:
            sel = selected_controller_id
        self._send_json({
            "controllers": list(_controller_info),
            "selected": sel,
            "count": len(_controller_info),
        })

    def _handle_restart(self):
        self._send_json({"ok": True, "message": "Restarting..."})
        print("[Sender] Restart requested via GUI. Restarting process...")
        # Use os.execv to replace current process with a fresh instance
        threading.Timer(0.5, lambda: os.execv(sys.executable, [sys.executable] + sys.argv)).start()


def start_http_server(port=8082):
    server = ThreadingHTTPServer(("0.0.0.0", port), SenderHTTPHandler)
    print(f"[HTTP] GUI server at http://localhost:{port}/")
    server.serve_forever()


# --- Monitor WebSocket: single async broadcaster ---
async def monitor_handler(websocket):
    _monitor_clients.add(websocket)
    try:
        async for _ in websocket:
            pass  # monitor is send-only from server side
    except websockets.ConnectionClosed:
        pass
    finally:
        _monitor_clients.discard(websocket)


async def monitor_broadcaster():
    """Single task that reads from _monitor_queue and fans out to all clients."""
    while running:
        data = await _monitor_queue.get()
        if not _monitor_clients:
            continue
        dead = []
        for ws in list(_monitor_clients):
            try:
                await ws.send(data)
            except (websockets.ConnectionClosed, Exception):
                dead.append(ws)
        for ws in dead:
            _monitor_clients.discard(ws)


async def start_monitor_ws(port=8083):
    async with websockets.serve(monitor_handler, "0.0.0.0", port):
        print(f"[Monitor] WebSocket at ws://localhost:{port}/")
        await monitor_broadcaster()


# --- Reconnect logic ---
_reconnect_event = None

def _trigger_reconnect():
    global _reconnect_event
    if _reconnect_event is not None:
        _reconnect_event.set()


async def _recv_from_receiver(ws):
    """Listen for messages from the receiver (e.g. remote control toggle from GUI)."""
    try:
        async for msg in ws:
            try:
                data = json.loads(msg)
                if data.get("type") == "remote_control":
                    _set_remote_mode(data.get("enabled", False))
            except (json.JSONDecodeError, ValueError):
                pass
    except websockets.ConnectionClosed:
        pass


async def _send_loop(ws):
    """Send queued events to receiver."""
    while running:
        get_task = asyncio.ensure_future(event_queue.get())
        reconnect_task = asyncio.ensure_future(_reconnect_event.wait())
        done, pending = await asyncio.wait(
            [get_task, reconnect_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if reconnect_task in done:
            print("[Sender] Reconnect requested...")
            return
        msg = get_task.result()
        await ws.send(msg)


async def sender(host, port):
    global ws_connection, ws_status, running, _reconnect_event
    _reconnect_event = asyncio.Event()

    while running:
        uri = f"ws://{config['host']}:{config['port']}"
        ws_status = "connecting"
        print(f"[Sender] Connecting to {uri} ...")

        try:
            async with websockets.connect(uri) as ws:
                ws_connection = ws
                ws_status = "connected"
                _reconnect_event.clear()
                print("[Sender] Connected!")

                # Notify receiver of current remote mode state
                with _remote_lock:
                    if _remote_mode:
                        await ws.send(json.dumps({"type": "remote_control", "enabled": True}))

                # Run send loop and receive listener concurrently
                send_task = asyncio.ensure_future(_send_loop(ws))
                recv_task = asyncio.ensure_future(_recv_from_receiver(ws))
                try:
                    done, pending = await asyncio.wait(
                        [send_task, recv_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                except Exception:
                    send_task.cancel()
                    recv_task.cancel()

        except (ConnectionRefusedError, OSError) as e:
            ws_connection = None
            ws_status = "disconnected"
            print(f"[Sender] Receiver not found ({e}). Waiting for receiver...")
            try:
                await asyncio.wait_for(_reconnect_event.wait(), timeout=3.0)
                _reconnect_event.clear()
            except asyncio.TimeoutError:
                pass
        except websockets.ConnectionClosed:
            ws_connection = None
            ws_status = "disconnected"
            print("[Sender] Connection lost. Reconnecting...")
            await asyncio.sleep(1)
        except Exception as e:
            ws_connection = None
            ws_status = "disconnected"
            print(f"[Sender] Unexpected error: {e}. Retrying in 3s...")
            try:
                await asyncio.wait_for(_reconnect_event.wait(), timeout=3.0)
                _reconnect_event.clear()
            except asyncio.TimeoutError:
                pass

        # Safety: if disconnected while remote mode is on, disable it
        with _remote_lock:
            was_remote = _remote_mode
        if was_remote:
            _set_remote_mode(False)
            print("[Remote] Auto-disabled due to disconnection")


_restart_lock = threading.Lock()


def _restart_listeners(suppress=False):
    """Restart keyboard/mouse listeners with optional suppress.
    新 listener を先に start してから旧 listener を stop することで、
    フック不在のラグ窓を作らない。自スレッド（on_press 内）から呼ばれても
    安全なように stop は非ブロッキング前提で扱う。"""
    global _kb_listener, _mouse_listener
    with _restart_lock:
        old_kb = _kb_listener
        old_mouse = _mouse_listener
        new_kb = keyboard.Listener(
            on_press=on_press, on_release=on_release, suppress=suppress,
        )
        new_kb.start()
        new_mouse = mouse.Listener(
            on_click=on_mouse_click, on_scroll=on_mouse_scroll, suppress=suppress,
        )
        new_mouse.start()
        _kb_listener = new_kb
        _mouse_listener = new_mouse
        for old in (old_kb, old_mouse):
            if old is None:
                continue
            try:
                old.stop()
            except Exception:
                pass
    mode = "suppress" if suppress else "normal"
    print(f"[Listeners] Restarted ({mode})")


async def _remote_toggle_handler():
    """Watch for remote mode toggles and notify receiver/monitor.
    Listener 再起動は _set_remote_mode 側で同期実行済み。"""
    while running:
        await _remote_toggle_event.wait()
        _remote_toggle_event.clear()
        with _remote_lock:
            enabled = _remote_mode
        # Notify receiver
        if ws_connection:
            try:
                await ws_connection.send(json.dumps({
                    "type": "remote_control", "enabled": enabled,
                }))
            except Exception:
                pass
        # Broadcast to monitor clients
        msg = json.dumps({
            "type": "remote_control_state", "enabled": enabled,
            "source": "system", "timestamp": __import__("time").time(),
        })
        enqueue_monitor(msg)


async def main():
    global _loop, _monitor_queue, _remote_toggle_event
    global _kb_listener, _mouse_listener
    _loop = asyncio.get_event_loop()
    _monitor_queue = asyncio.Queue()
    _remote_toggle_event = asyncio.Event()

    _kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    _kb_listener.start()

    _mouse_listener = mouse.Listener(on_click=on_mouse_click)
    _mouse_listener.start()

    raw_mouse_thread = threading.Thread(target=raw_mouse_loop, daemon=True)
    raw_mouse_thread.start()

    gp_thread = threading.Thread(target=gamepad_loop, daemon=True)
    gp_thread.start()

    # Start HTTP server for GUI (ThreadingHTTPServer handles concurrent requests)
    http_port = config.get("http_port", 8082)
    http_thread = threading.Thread(target=start_http_server, args=(http_port,), daemon=True)
    http_thread.start()

    # Start monitor WebSocket, sender, and remote toggle handler concurrently
    monitor_port = config.get("monitor_port", 8083)
    await asyncio.gather(
        sender(config["host"], config["port"]),
        start_monitor_ws(monitor_port),
        _remote_toggle_handler(),
    )


def _shutdown_local_resources():
    """Stop listeners and tear down overlay/pygame on process exit."""
    global running
    running = False
    for listener in (_kb_listener, _mouse_listener):
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
    try:
        _overlay_manager.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    print(f"[Config] host={config['host']} port={config['port']}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Sender] Stopping...")
    finally:
        _shutdown_local_resources()
        print("[Sender] Stopped.")
