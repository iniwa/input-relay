"""
Input Injector - Replays input events as OS-level input on Windows via SendInput.
Used by input_server.py when remote control mode is active.
"""

import ctypes
from ctypes import wintypes, sizeof, byref

user32 = ctypes.windll.user32

# --- Constants ---
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002


# --- Structures ---
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]


# --- Key name -> VK code mapping ---
# Matches key names produced by sender's key_to_str()
_KEY_TO_VK = {
    **{chr(c).lower(): c for c in range(0x41, 0x5B)},  # a-z
    **{str(i): 0x30 + i for i in range(10)},            # 0-9
    # Modifiers
    "shift": 0xA0, "ctrl": 0xA2, "alt": 0xA4,
    # Common keys
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "backspace": 0x08,
    "esc": 0x1B, "escape": 0x1B,
    "caps_lock": 0x14, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    # Arrow keys
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    # F-keys
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    # Misc
    "scroll_lock": 0x91, "pause": 0x13, "print_screen": 0x2C,
    "num_lock": 0x90,
    # Punctuation (common on US layout)
    "minus": 0xBD, "equal": 0xBB,
    "bracket_left": 0xDB, "bracket_right": 0xDD,
    "semicolon": 0xBA, "apostrophe": 0xDE,
    "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "backslash": 0xDC, "grave": 0xC0,
}

# Mouse button -> (down_flag, up_flag, mouseData)
_MOUSE_BUTTONS = {
    "mouse_left":   (MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP,   0),
    "mouse_right":  (MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP,  0),
    "mouse_middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "mouse_x1":     (MOUSEEVENTF_XDOWN,      MOUSEEVENTF_XUP,      XBUTTON1),
    "mouse_x2":     (MOUSEEVENTF_XDOWN,      MOUSEEVENTF_XUP,      XBUTTON2),
}


def _send_input(inp):
    user32.SendInput(1, byref(inp), sizeof(INPUT))


def inject_key(vk, key_up=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.dwFlags = KEYEVENTF_KEYUP if key_up else 0
    _send_input(inp)


def inject_mouse_move(dx, dy):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dx = dx
    inp.union.mi.dy = dy
    inp.union.mi.dwFlags = MOUSEEVENTF_MOVE
    _send_input(inp)


def inject_mouse_button(button, is_down):
    info = _MOUSE_BUTTONS.get(button)
    if not info:
        return
    down_flag, up_flag, mouse_data = info
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dwFlags = down_flag if is_down else up_flag
    inp.union.mi.mouseData = mouse_data
    _send_input(inp)


def replay_event(event):
    """Parse an input event dict and inject it as OS input."""
    etype = event.get("type")
    key = event.get("key", "")

    if etype == "mouse_move":
        dx = event.get("dx", 0)
        dy = event.get("dy", 0)
        if dx or dy:
            inject_mouse_move(dx, dy)
        return

    if etype in ("key_down", "key_up"):
        is_down = etype == "key_down"
        # Mouse button
        if key.startswith("mouse_"):
            inject_mouse_button(key, is_down)
            return
        # Keyboard
        vk = _KEY_TO_VK.get(key)
        if vk is not None:
            inject_key(vk, key_up=not is_down)
        return

    # axis_update, mode_switch, remote_control, etc. -> skip


def release_all(pressed_keys):
    """Release all currently pressed keys to prevent stuck keys."""
    for key in list(pressed_keys):
        if key.startswith("mouse_"):
            inject_mouse_button(key, is_down=False)
        else:
            vk = _KEY_TO_VK.get(key)
            if vk is not None:
                inject_key(vk, key_up=True)
    pressed_keys.clear()
