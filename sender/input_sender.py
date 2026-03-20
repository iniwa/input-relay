"""
Input Sender - Captures keyboard/gamepad/mouse input and sends to Sub PC via WebSocket.
Run on Main PC.
"""

import asyncio
import json
import time
import threading
from pathlib import Path

import websockets
from pynput import keyboard, mouse

pygame = None

# Load config
CONFIG_PATH = Path(__file__).parent / "sender_config.json"
def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"host": "192.168.1.100", "port": 8765, "toggleKey": "f12"}

config = load_config()

ws_connection = None
running = True
pressed_keys = set()
_pressed_keys_lock = threading.Lock()
_loop = None  # asyncio event loop, set in main()

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


def on_press(key):
    key_str = key_to_str(key)
    with _pressed_keys_lock:
        if key_str not in pressed_keys:
            pressed_keys.add(key_str)
            event_queue.put(make_event("key_down", key_str))


def on_release(key):
    key_str = key_to_str(key)
    with _pressed_keys_lock:
        pressed_keys.discard(key_str)
    event_queue.put(make_event("key_up", key_str))


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
    event_queue.put(make_event(etype, key_str, "mouse"))


def gamepad_loop():
    load_pygame()
    joy = None
    prev_buttons = {}
    prev_axes = {}       # leverless: threshold-based axis state
    prev_axes_raw = {}   # controller: raw float values
    last_reinit = 0

    while running:
        pygame.event.pump()

        if joy is None and time.time() - last_reinit > 2.0:
            pygame.joystick.quit()
            pygame.joystick.init()
            last_reinit = time.time()

        if pygame.joystick.get_count() > 0:
            if joy is None:
                joy = pygame.joystick.Joystick(0)
                joy.init()
                print(f"[Gamepad] Connected: {joy.get_name()}")
        else:
            if joy is not None:
                joy = None
                prev_buttons.clear()
                prev_axes.clear()
                prev_axes_raw.clear()
            time.sleep(0.1)
            continue

        # Buttons — always send regardless of mode
        for i in range(joy.get_numbuttons()):
            val = joy.get_button(i)
            if val != prev_buttons.get(i, 0):
                prev_buttons[i] = val
                etype = "key_down" if val else "key_up"
                event_queue.put(make_event(etype, f"btn_{i}", "gamepad"))

        # Hats — send in both leverless and controller modes
        for i in range(joy.get_numhats()):
            hat = joy.get_hat(i)
            prev_hat = prev_axes.get(f"hat_{i}", (0, 0))
            if hat != prev_hat:
                if prev_hat[0] != 0:
                    event_queue.put(make_event("key_up", f"hat_{i}_{'left' if prev_hat[0] < 0 else 'right'}", "gamepad"))
                if prev_hat[1] != 0:
                    event_queue.put(make_event("key_up", f"hat_{i}_{'down' if prev_hat[1] < 0 else 'up'}", "gamepad"))
                if hat[0] != 0:
                    event_queue.put(make_event("key_down", f"hat_{i}_{'left' if hat[0] < 0 else 'right'}", "gamepad"))
                if hat[1] != 0:
                    event_queue.put(make_event("key_down", f"hat_{i}_{'down' if hat[1] < 0 else 'up'}", "gamepad"))
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
                    event_queue.put(make_event("key_up", f"axis_{i}_{'neg' if prev < 0 else 'pos'}", "gamepad"))
                if val != 0:
                    event_queue.put(make_event("key_down", f"axis_{i}_{'neg' if val < 0 else 'pos'}", "gamepad"))
                prev_axes[i] = val
            # Continuous (controller style)
            if abs(raw - prev_axes_raw.get(i, 2.0)) > 0.01:
                prev_axes_raw[i] = raw
                event_queue.put(json.dumps({
                    "type": "axis_update",
                    "axis": i,
                    "value": round(raw, 3),
                    "source": "gamepad",
                    "timestamp": time.time(),
                }))

        time.sleep(0.008)


async def sender(host, port):
    global ws_connection, running
    uri = f"ws://{host}:{port}"
    print(f"[Sender] Connecting to {uri} ...")

    while running:
        try:
            async with websockets.connect(uri) as ws:
                ws_connection = ws
                print("[Sender] Connected!")
                while running:
                    msg = await event_queue.get()
                    try:
                        await ws.send(msg)
                    except websockets.ConnectionClosed:
                        break
        except (ConnectionRefusedError, OSError) as e:
            ws_connection = None
            print(f"[Sender] Connection failed: {e}. Retrying in 2s...")
            await asyncio.sleep(2)
        except websockets.ConnectionClosed:
            ws_connection = None
            print("[Sender] Connection lost. Reconnecting...")
            await asyncio.sleep(1)


async def main():
    global _loop
    _loop = asyncio.get_event_loop()

    kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    kb_listener.start()

    mouse_listener = mouse.Listener(on_click=on_mouse_click)
    mouse_listener.start()

    gp_thread = threading.Thread(target=gamepad_loop, daemon=True)
    gp_thread.start()

    await sender(config["host"], config["port"])


if __name__ == "__main__":
    print(f"[Config] host={config['host']} port={config['port']}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        running = False
        print("\n[Sender] Stopped.")
