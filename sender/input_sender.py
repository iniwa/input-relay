"""
Input Sender - Captures keyboard/gamepad input and sends to Sub PC via WebSocket.
Run on Main PC.
"""

import asyncio
import json
import time
import threading
from pathlib import Path

import websockets
from pynput import keyboard

pygame = None

# Load config
CONFIG_PATH = Path(__file__).parent / "sender_config.json"
def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"host": "192.168.1.100", "port": 8765, "toggleKey": "f12"}

config = load_config()

ws_connection = None
gamepad_mode = False
running = True
pressed_keys = set()

TOGGLE_KEY_NAME = config.get("toggleKey", "f12")
TOGGLE_KEY = getattr(keyboard.Key, TOGGLE_KEY_NAME, keyboard.Key.f12)


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
    if hasattr(key, "char") and key.char is not None:
        return key.char.lower()
    if hasattr(key, "name"):
        return key.name
    return str(key)


class JsonQueue:
    def __init__(self):
        self._queue = asyncio.Queue()

    def put(self, data):
        self._queue.put_nowait(data)

    async def get(self):
        return await self._queue.get()


event_queue = JsonQueue()


def on_press(key):
    global gamepad_mode
    if key == TOGGLE_KEY:
        gamepad_mode = not gamepad_mode
        mode = "gamepad" if gamepad_mode else "keyboard"
        print(f"[Mode] Switched to {mode}")
        event_queue.put(make_event("mode_switch", mode, "system"))
        return

    key_str = key_to_str(key)
    if key_str not in pressed_keys:
        pressed_keys.add(key_str)
        if not gamepad_mode:
            event_queue.put(make_event("key_down", key_str))


def on_release(key):
    key_str = key_to_str(key)
    pressed_keys.discard(key_str)
    if not gamepad_mode:
        event_queue.put(make_event("key_up", key_str))


def gamepad_loop():
    load_pygame()
    joy = None
    prev_buttons = {}
    prev_axes = {}

    while running:
        pygame.event.pump()

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
            time.sleep(0.1)
            continue

        if not gamepad_mode:
            time.sleep(0.05)
            continue

        for i in range(joy.get_numbuttons()):
            val = joy.get_button(i)
            if val != prev_buttons.get(i, 0):
                prev_buttons[i] = val
                etype = "key_down" if val else "key_up"
                event_queue.put(make_event(etype, f"btn_{i}", "gamepad"))

        deadzone = 0.5
        for i in range(joy.get_numaxes()):
            raw = joy.get_axis(i)
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

        time.sleep(0.008)


async def sender(host, port):
    global ws_connection, running
    uri = f"ws://{host}:{port}"
    print(f"[Sender] Connecting to {uri} ...")

    while running:
        try:
            async with websockets.connect(uri) as ws:
                ws_connection = ws
                print(f"[Sender] Connected! ({TOGGLE_KEY_NAME.upper()} to toggle keyboard/gamepad)")
                while running:
                    msg = await event_queue.get()
                    await ws.send(msg)
        except (ConnectionRefusedError, OSError) as e:
            print(f"[Sender] Connection failed: {e}. Retrying in 2s...")
            await asyncio.sleep(2)
        except websockets.ConnectionClosed:
            print("[Sender] Connection lost. Reconnecting...")
            await asyncio.sleep(1)


async def main():
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    gp_thread = threading.Thread(target=gamepad_loop, daemon=True)
    gp_thread.start()

    await sender(config["host"], config["port"])


if __name__ == "__main__":
    print(f"[Config] host={config['host']} port={config['port']} toggle={TOGGLE_KEY_NAME}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        running = False
        print("\n[Sender] Stopped.")
