"""
Input Sender - Captures keyboard/gamepad/mouse input and sends to Sub PC via WebSocket.
Run on Main PC. Includes local HTTP server for configuration GUI.
"""

import asyncio
import ctypes
import json
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

import gamepad as gamepad_mod
import http_api
import ll_mouse_hook
import monitor_ws
import overlay_window
import raw_mouse
import websockets
from pynput import keyboard, mouse

from input_common.input_events import get_vk as _get_vk
from input_common.input_events import key_to_str, make_event

# Logging — silent except のトレースを掴めるよう default は INFO、
# INPUT_RELAY_DEBUG=1 で DEBUG (silenced exception を表示)。
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("INPUT_RELAY_DEBUG") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sender")
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# --- Tunables ---
DEFAULT_HTTP_PORT = 8082
DEFAULT_MONITOR_PORT = 8083
RECONNECT_BACKOFF = 3.0           # 接続失敗時の待機 (秒)

# Load config
CONFIG_PATH = Path(__file__).parent.parent / "config" / "sender_config.json"
GUI_PATH = Path(__file__).parent / "sender_gui.html"

_CONFIG_DEFAULTS = {
    "host": "localhost",
    "port": 8888,
    "gamepad_enabled": True,
    "raw_mouse_enabled": True,
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

# Remote control mode — RemoteState で mode/lock/toggle_event をまとめて管理
class RemoteState:
    """リモート操作モードのオン/オフと、receiver/monitor 通知用イベント。

    .mode の bool 読み出しは GIL 下で原子的なので頻出経路 (on_press 等)
    では lock を取らない。書き込みは swap() を経由し前値を返す。"""

    def __init__(self):
        self.mode = False
        self.lock = threading.Lock()
        self.toggle_event = None  # asyncio.Event, set in main()

    def swap(self, new):
        with self.lock:
            prev = self.mode
            self.mode = new
        return prev


remote = RemoteState()
_kb_listener = None
_mouse_listener = None

# Remote overlay window — tkinter を別スレッドで管理するマネージャー
_overlay_manager = overlay_window.OverlayManager(lambda: config)
_OVERLAY_POSITIONS = overlay_window.valid_positions()

# 低レベルマウスフック（pynput suppress のタイムアウト冗長化）
_ll_mouse_blocker = ll_mouse_hook.LowLevelMouseBlocker()

# Gamepad — capture loop と HTTP API の両方からアクセスされるためクラスで保護
_gamepad = None  # gamepad_mod.Gamepad, created in main()

# Monitor: WebSocket broadcaster, created in main()
_monitor: monitor_ws.MonitorServer | None = None


def _freeze_cursor():
    """Lock the cursor to its current position using ClipCursor."""
    pos = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pos))
    rect = wintypes.RECT(pos.x, pos.y, pos.x + 1, pos.y + 1)
    ctypes.windll.user32.ClipCursor(ctypes.byref(rect))


def _unfreeze_cursor():
    """Release cursor lock."""
    ctypes.windll.user32.ClipCursor(None)


def _set_remote_mode(enabled):
    """Toggle remote control mode. Restarts listeners synchronously so the
    suppress hook is active before the next OS input is dispatched."""
    was = remote.swap(enabled)
    if was == enabled:
        return
    print(f"[Remote] {'ENABLED' if enabled else 'DISABLED'}")
    if enabled:
        _overlay_manager.set_user_hidden(False)
        _freeze_cursor()
        _overlay_manager.show()
        _ll_mouse_blocker.set_suppress(True)
    else:
        _ll_mouse_blocker.set_suppress(False)
        _overlay_manager.hide()
        _unfreeze_cursor()
    # 新 listener を先に立ち上げてから旧 listener を stop する（ラグ窓を消す）。
    # この関数は pynput の on_press 内から呼ばれ得るが、stop() はフラグを立てる
    # だけなので自スレッドから呼んでもデッドロックしない。
    _restart_listeners(suppress=enabled)
    # receiver / monitor への通知のみ非同期で
    if _loop is not None and remote.toggle_event is not None:
        _loop.call_soon_threadsafe(remote.toggle_event.set)


# receiver 向けイベントキュー。切断中に溜まった古いイベントは表示価値が
# ほぼ無いため、上限を設けて常駐メモリを有界に保つ（満杯時は最古を破棄）。
_EVENT_QUEUE_MAXSIZE = 500
event_queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAXSIZE)


def _enqueue_event_on_loop(data):
    """asyncio loop スレッドで実行: 満杯なら最古を捨ててから最新を積む。"""
    if event_queue.full():
        try:
            event_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    event_queue.put_nowait(data)


def _post_event(data):
    """Thread-safe: enqueue an event for the sender ws send loop.
    receiver 未接続中は表示できないイベントなので積まずに捨てる。"""
    if _loop is not None and ws_status == "connected":
        _loop.call_soon_threadsafe(_enqueue_event_on_loop, data)


def enqueue_monitor(data):
    """Thread-safe: push data to the monitor broadcast queue."""
    if _monitor is not None:
        _monitor.enqueue(data)


def _emit(msg, monitor=True):
    """Send msg to the receiver queue and (optionally) to monitor clients."""
    _post_event(msg)
    if monitor:
        enqueue_monitor(msg)


# --- Input source timestamps (for Main/Sub active detection by secretary-bot) ---
# kbd/mouse と gamepad を独立に追跡して、/api/status で公開する。
# キーボード/マウス: リモートモード中は Sub PC 側で消費される
# ゲームパッド: 物理的に Main PC 接続なので常に Main 側操作
_last_kbd_mouse_ts: float = 0.0
_last_gamepad_ts: float = 0.0
_input_ts_lock = threading.Lock()


def _touch_kbd_mouse():
    with _input_ts_lock:
        global _last_kbd_mouse_ts
        _last_kbd_mouse_ts = time.time()


def _touch_gamepad():
    with _input_ts_lock:
        global _last_gamepad_ts
        _last_gamepad_ts = time.time()


def _emit_gamepad(msg, monitor=True):
    """Gamepad 経路の _emit ラッパー。emit と同時に gamepad タイムスタンプを更新する。"""
    _touch_gamepad()
    _emit(msg, monitor=monitor)


def on_press(key):
    _touch_kbd_mouse()
    # Scroll Lock toggles remote control mode
    if key == keyboard.Key.scroll_lock:
        _set_remote_mode(not remote.mode)
        return  # Don't send Scroll Lock to receiver

    # Pause はリモート中にオーバーレイの表示/非表示を切り替える
    if key == keyboard.Key.pause:
        if remote.mode:
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
    if not is_repeat or remote.mode:
        msg = make_event("key_down", key_str, vk=vk)
        _emit(msg, monitor=not is_repeat)


def on_release(key):
    _touch_kbd_mouse()
    key_str = key_to_str(key)
    vk = _get_vk(key)
    with _pressed_keys_lock:
        pressed_keys.discard(key_str)
    _emit(make_event("key_up", key_str, vk=vk))


# --- Mouse movement (Raw Input API, 60Hz throttled) ---
def _on_raw_mouse_delta(dx, dy):
    """raw_mouse モジュールから 16ms 間隔で呼ばれる。"""
    _touch_kbd_mouse()
    # リモートモード中はマウスを Sub PC 側の物理マウスで操作するため
    # マウス関連イベントは転送しない。
    if remote.mode:
        return
    _emit(json.dumps({
        "type": "mouse_move",
        "dx": dx,
        "dy": dy,
        "source": "mouse",
        "timestamp": time.time(),
    }))


def raw_mouse_loop():
    raw_mouse.run(lambda: running, _on_raw_mouse_delta)


# --- Mouse listener ---
def on_mouse_click(x, y, button, pressed):
    _touch_kbd_mouse()
    # リモートモード中はマウスイベントを Sub PC へ転送しない（Sub PC 側は
    # 物理マウスで操作する運用のため）。OS レベルの suppress は pynput /
    # ll_mouse_hook 側で引き続き掛かる。
    if remote.mode:
        return
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
    _emit(make_event(etype, key_str, "mouse"))


def on_mouse_scroll(x, y, dx, dy):
    _touch_kbd_mouse()
    if remote.mode:
        return
    _emit(json.dumps({
        "type": "mouse_scroll",
        "dx": dx,
        "dy": dy,
        "source": "mouse",
        "timestamp": time.time(),
    }))


# --- HTTP API context (accessors for sender/http_api.py) ---
def _get_config():
    return config


def _get_gamepad():
    return _gamepad


def _get_ws_status():
    return ws_status


def _get_remote_mode():
    return remote.mode


def _get_input_timestamps():
    with _input_ts_lock:
        return _last_kbd_mouse_ts, _last_gamepad_ts


def _schedule_reconnect():
    if _loop is not None:
        _loop.call_soon_threadsafe(_trigger_reconnect)


def _build_http_context():
    return http_api.SenderContext(
        gui_path=GUI_PATH,
        get_config=_get_config,
        save_config=save_config,
        trigger_reconnect=_schedule_reconnect,
        get_gamepad=_get_gamepad,
        valid_overlay_positions=_OVERLAY_POSITIONS,
        get_ws_status=_get_ws_status,
        get_remote_mode=_get_remote_mode,
        get_input_timestamps=_get_input_timestamps,
    )


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

                # Always report our explicit current remote mode state before
                # any queued input is sent, so the receiver never assumes ON
                # by default (an explicit False keeps receiver injection off).
                await ws.send(json.dumps({"type": "remote_control", "enabled": remote.mode}))

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
                    logger.exception("send/recv tasks failed; cancelling")
                    send_task.cancel()
                    recv_task.cancel()

        except (ConnectionRefusedError, OSError) as e:
            ws_connection = None
            ws_status = "disconnected"
            print(f"[Sender] Receiver not found ({e}). Waiting for receiver...")
            try:
                await asyncio.wait_for(_reconnect_event.wait(), timeout=RECONNECT_BACKOFF)
                _reconnect_event.clear()
            except TimeoutError:
                pass
        except websockets.ConnectionClosed:
            ws_connection = None
            ws_status = "disconnected"
            print("[Sender] Connection lost. Reconnecting...")
            await asyncio.sleep(1)
        except Exception as e:
            ws_connection = None
            ws_status = "disconnected"
            logger.exception("sender loop unexpected error: %s", e)
            print(f"[Sender] Unexpected error: {e}. Retrying in {RECONNECT_BACKOFF}s...")
            try:
                await asyncio.wait_for(_reconnect_event.wait(), timeout=RECONNECT_BACKOFF)
                _reconnect_event.clear()
            except TimeoutError:
                pass

        # Safety: if disconnected while remote mode is on, disable it
        if remote.mode:
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
                logger.debug("listener stop failed", exc_info=True)
    mode = "suppress" if suppress else "normal"
    print(f"[Listeners] Restarted ({mode})")


async def _remote_toggle_handler():
    """Watch for remote mode toggles and notify receiver/monitor.
    Listener 再起動は _set_remote_mode 側で同期実行済み。"""
    while running:
        await remote.toggle_event.wait()
        remote.toggle_event.clear()
        enabled = remote.mode
        if ws_connection:
            try:
                await ws_connection.send(json.dumps({
                    "type": "remote_control", "enabled": enabled,
                }))
            except Exception:
                logger.debug("notify receiver of remote toggle failed", exc_info=True)
        enqueue_monitor(json.dumps({
            "type": "remote_control_state", "enabled": enabled,
            "source": "system", "timestamp": time.time(),
        }))


async def _run_forever(name, coro_factory):
    """Run a long-lived task and restart it if it exits unexpectedly."""
    while running:
        try:
            await coro_factory()
            if running:
                logger.warning("%s task exited unexpectedly; restarting in %.1fs",
                               name, RECONNECT_BACKOFF)
                await asyncio.sleep(RECONNECT_BACKOFF)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("%s task crashed: %s; restarting in %.1fs",
                             name, e, RECONNECT_BACKOFF)
            await asyncio.sleep(RECONNECT_BACKOFF)


async def main():
    global _loop, _monitor
    global _kb_listener, _mouse_listener, _gamepad
    _loop = asyncio.get_event_loop()
    _monitor = monitor_ws.MonitorServer(_loop, lambda: running)
    remote.toggle_event = asyncio.Event()

    _kb_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    _kb_listener.start()

    _mouse_listener = mouse.Listener(on_click=on_mouse_click)
    _mouse_listener.start()

    # 低レベルマウスフックを起動（suppress フラグで動作切替）
    _ll_mouse_blocker.start()

    if config.get("raw_mouse_enabled", False):
        raw_mouse_thread = threading.Thread(target=raw_mouse_loop, daemon=True)
        raw_mouse_thread.start()
    else:
        print("[RawMouse] Disabled by config")

    if config.get("gamepad_enabled", False):
        _gamepad = gamepad_mod.Gamepad(emit_callback=_emit_gamepad, is_running=lambda: running)
        gp_thread = threading.Thread(target=_gamepad.run, daemon=True)
        gp_thread.start()
    else:
        print("[Gamepad] Disabled by config")

    # Start HTTP server for GUI (ThreadingHTTPServer handles concurrent requests)
    http_port = config.get("http_port", DEFAULT_HTTP_PORT)
    http_ctx = _build_http_context()
    http_thread = threading.Thread(
        target=http_api.start_http_server, args=(http_ctx, http_port), daemon=True,
    )
    http_thread.start()

    # Start monitor WebSocket, sender, and remote toggle handler concurrently
    monitor_port = config.get("monitor_port", DEFAULT_MONITOR_PORT)
    await asyncio.gather(
        _run_forever("sender", lambda: sender(config["host"], config["port"])),
        _run_forever("monitor_ws", lambda: _monitor.serve(monitor_port)),
        _run_forever("remote_toggle", _remote_toggle_handler),
    )


def _shutdown_local_resources():
    """Stop listeners and tear down overlay/blocker on process exit."""
    global running
    running = False
    for listener in (_kb_listener, _mouse_listener):
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                logger.debug("listener stop failed during shutdown", exc_info=True)
    try:
        _overlay_manager.shutdown()
    except Exception:
        logger.debug("overlay shutdown failed", exc_info=True)
    try:
        _ll_mouse_blocker.stop()
    except Exception:
        logger.debug("ll mouse blocker stop failed", exc_info=True)


if __name__ == "__main__":
    print(f"[Config] host={config['host']} port={config['port']}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Sender] Stopping...")
    except BaseException:
        logger.exception("sender process crashed")
        raise
    finally:
        _shutdown_local_resources()
        print("[Sender] Stopped.")
