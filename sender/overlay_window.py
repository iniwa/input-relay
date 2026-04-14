"""
Remote-mode overlay window.

リモート操作時に Main PC の画面端へ「{target_name} を操作中」を半透明表示する。
tkinter は単一スレッド前提のため専用スレッドで mainloop を回し、他スレッドからは
queue 経由で show/hide を依頼する。
"""

from __future__ import annotations

import queue
import threading


_OVERLAY_POSITIONS = (
    "top-left", "top-center", "top-right",
    "middle-left", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)


def valid_positions():
    return _OVERLAY_POSITIONS


def _calc_position(position, width, height, screen_w, screen_h):
    margin = 20
    if position not in _OVERLAY_POSITIONS:
        position = "top-left"
    if "left" in position:
        x = margin
    elif "right" in position:
        x = screen_w - width - margin
    else:
        x = (screen_w - width) // 2
    if "top" in position:
        y = margin
    elif "bottom" in position:
        y = screen_h - height - margin
    else:
        y = (screen_h - height) // 2
    return x, y


class OverlayManager:
    """Show/hide the remote-mode overlay from any thread."""

    def __init__(self, get_config):
        # get_config: callable returning the latest config dict
        self._get_config = get_config
        self._thread = None
        self._root = None
        self._window = None
        self._blocker = None
        self._ready = threading.Event()
        self._cmd_queue = queue.Queue()
        self._user_hidden = False

    # --- public API (thread-safe) ---
    def show(self):
        cfg = self._get_config().get("remote_overlay") or {}
        if not cfg.get("enabled", True):
            return
        if self._user_hidden:
            return
        self._ensure_thread()
        if self._root is None:
            return
        self._cmd_queue.put("show")

    def hide(self):
        if self._thread is None or self._root is None:
            return
        self._cmd_queue.put("hide")

    def set_user_hidden(self, hidden: bool):
        self._user_hidden = bool(hidden)

    def is_user_hidden(self) -> bool:
        return self._user_hidden

    def shutdown(self):
        """Schedule overlay destruction and request mainloop to exit.
        Safe to call multiple times / from any thread.
        """
        if self._root is None:
            return
        try:
            self._cmd_queue.put("quit")
        except Exception:
            pass

    # --- internal ---
    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="overlay-gui",
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def _thread_main(self):
        try:
            import tkinter as tk  # noqa: F401
        except ImportError:
            print("[Overlay] tkinter not available; overlay disabled")
            self._ready.set()
            return
        import tkinter as tk
        try:
            self._root = tk.Tk()
            self._root.withdraw()
        except Exception as e:
            print(f"[Overlay] Failed to create Tk root: {e}")
            self._ready.set()
            return
        self._ready.set()
        self._root.after(30, self._poll_queue)
        try:
            self._root.mainloop()
        except Exception as e:
            print(f"[Overlay] mainloop exited: {e}")
        finally:
            try:
                if self._window is not None:
                    self._window.destroy()
            except Exception:
                pass
            try:
                if self._blocker is not None:
                    self._blocker.destroy()
            except Exception:
                pass
            try:
                self._root.destroy()
            except Exception:
                pass
            self._window = None
            self._blocker = None
            self._root = None

    def _poll_queue(self):
        try:
            while True:
                cmd = self._cmd_queue.get_nowait()
                if cmd == "show":
                    self._do_show()
                elif cmd == "hide":
                    self._do_hide()
                elif cmd == "quit":
                    self._do_hide()
                    if self._root is not None:
                        self._root.quit()
                    return
        except queue.Empty:
            pass
        if self._root is not None:
            self._root.after(30, self._poll_queue)

    def _do_show(self):
        if self._window is not None or self._root is None:
            return
        import tkinter as tk

        # 全画面透明ブロッカー: 低レベルフックが LowLevelHooksTimeout で外れても
        # 物理的にクリックが背後へ届かないよう、画面全域を覆う半透明ウィンドウを出す。
        # alpha=0.01 で事実上不可視、WS_EX_TRANSPARENT は付けない（＝クリックを食う）。
        try:
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()
            blocker = tk.Toplevel(self._root)
            blocker.overrideredirect(True)
            blocker.attributes("-topmost", True)
            blocker.attributes("-alpha", 0.01)
            blocker.configure(bg="#000000")
            blocker.geometry(f"{sw}x{sh}+0+0")
            self._blocker = blocker
        except Exception as e:
            print(f"[Overlay] Failed to create blocker: {e}")

        cfg = self._get_config()
        overlay_cfg = cfg.get("remote_overlay") or {}
        position = overlay_cfg.get("position", "top-left")
        target = (cfg.get("target_name") or "Sub PC").strip() or "Sub PC"
        local = (cfg.get("local_name") or "").strip()

        w = tk.Toplevel(self._root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.88)
        w.configure(bg="#1a1a1a")

        accent = tk.Frame(w, width=5, bg="#4A9EFF")
        accent.pack(side="left", fill="y")

        content = tk.Frame(w, bg="#1a1a1a", padx=14, pady=10)
        content.pack(side="left", fill="both", expand=True)

        tk.Label(
            content, text=f"▶ {target} を操作中", fg="#ffffff", bg="#1a1a1a",
            font=("Yu Gothic UI", 13, "bold"), anchor="w",
        ).pack(anchor="w")

        sub_text = (
            f"↑ {local} の入力を転送中  /  Scroll Lock で解除"
            if local else "Scroll Lock で解除"
        )
        tk.Label(
            content, text=sub_text, fg="#bbbbbb", bg="#1a1a1a",
            font=("Yu Gothic UI", 9), anchor="w",
        ).pack(anchor="w")

        w.update_idletasks()
        width = max(280, w.winfo_reqwidth())
        height = w.winfo_reqheight()
        x, y = _calc_position(position, width, height, w.winfo_screenwidth(), w.winfo_screenheight())
        w.geometry(f"{width}x{height}+{x}+{y}")

        # overrideredirect なウィンドウは Windows でフォーカス獲得が不安定なので
        # Win32 API で強制的に最前面へ持ってくる。
        # blocker を先に lift してから label を最前面へ（z-order: blocker < label）
        if self._blocker is not None:
            try:
                self._blocker.lift()
            except Exception:
                pass
        w.lift()
        w.focus_force()
        try:
            import ctypes
            hwnd = w.winfo_id()
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

        self._window = w

    def _do_hide(self):
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        if self._blocker is not None:
            try:
                self._blocker.destroy()
            except Exception:
                pass
            self._blocker = None
