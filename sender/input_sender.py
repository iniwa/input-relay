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

pygame = None

# Load config
CONFIG_PATH = Path(__file__).parent / "sender_config.json"
GUI_PATH = Path(__file__).parent / "sender_gui.html"

def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"host": "192.168.1.100", "port": 8888, "toggleKey": "f12"}

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

config = load_config()

ws_connection = None
ws_status = "disconnected"  # "connected", "disconnected", "connecting"
running = True
pressed_keys = set()
_pressed_keys_lock = threading.Lock()
_loop = None  # asyncio event loop, set in main()

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


def load_pygame():
    global pygame
    import pygame as pg
    pygame = pg
    pygame.init()
    pygame.joystick.init()


def make_event(event_type, key, source="keyboard"):
    return json.dumps({
        "type": event_type,
        "key": key,
        "source": source,
        "timestamp": time.time(),
    })


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


def on_press(key):
    key_str = key_to_str(key)
    with _pressed_keys_lock:
        if key_str not in pressed_keys:
            pressed_keys.add(key_str)
            msg = make_event("key_down", key_str)
            event_queue.put(msg)
            enqueue_monitor(msg)


def on_release(key):
    key_str = key_to_str(key)
    with _pressed_keys_lock:
        pressed_keys.discard(key_str)
    msg = make_event("key_up", key_str)
    event_queue.put(msg)
    enqueue_monitor(msg)


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


# --- Mouse movement (60Hz throttled) ---
_last_mouse_pos = [None, None]
_last_mouse_send = [0.0]
_MOUSE_SEND_INTERVAL = 1.0 / 60  # ~16ms

def on_mouse_move(x, y):
    now = time.time()
    if now - _last_mouse_send[0] < _MOUSE_SEND_INTERVAL:
        _last_mouse_pos[0], _last_mouse_pos[1] = x, y
        return
    prev_x, prev_y = _last_mouse_pos
    _last_mouse_pos[0], _last_mouse_pos[1] = x, y
    _last_mouse_send[0] = now
    if prev_x is None:
        return
    dx = x - prev_x
    dy = y - prev_y
    if dx == 0 and dy == 0:
        return
    msg = json.dumps({
        "type": "mouse_move",
        "dx": dx,
        "dy": dy,
        "source": "mouse",
        "timestamp": now,
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

        time.sleep(0.008)


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
            self._send_json({
                "ws_status": ws_status,
                "host": config.get("host", ""),
                "port": config.get("port", 8888),
                "selected_controller": sel,
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
        config["host"] = data.get("host", config.get("host"))
        config["port"] = int(data.get("port", config.get("port", 8888)))
        save_config(config)
        # Signal reconnect
        if _loop is not None:
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
                while running:
                    # Wait for either a message or a reconnect signal
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
                        break
                    msg = get_task.result()
                    try:
                        await ws.send(msg)
                    except websockets.ConnectionClosed:
                        break
        except (ConnectionRefusedError, OSError) as e:
            ws_connection = None
            ws_status = "disconnected"
            print(f"[Sender] Receiver not found ({e}). Waiting for receiver...")
            # Wait for either timeout or reconnect signal
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


async def main():
    global _loop, _monitor_queue
    _loop = asyncio.get_event_loop()
    _monitor_queue = asyncio.Queue()

    kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    kb_listener.start()

    mouse_listener = mouse.Listener(on_click=on_mouse_click, on_move=on_mouse_move)
    mouse_listener.start()

    gp_thread = threading.Thread(target=gamepad_loop, daemon=True)
    gp_thread.start()

    # Start HTTP server for GUI (ThreadingHTTPServer handles concurrent requests)
    http_port = config.get("http_port", 8082)
    http_thread = threading.Thread(target=start_http_server, args=(http_port,), daemon=True)
    http_thread.start()

    # Start monitor WebSocket and sender concurrently
    monitor_port = config.get("monitor_port", 8083)
    await asyncio.gather(
        sender(config["host"], config["port"]),
        start_monitor_ws(monitor_port),
    )


if __name__ == "__main__":
    print(f"[Config] host={config['host']} port={config['port']}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        running = False
        print("\n[Sender] Stopped.")
