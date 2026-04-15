"""
Remote-mode overlay window.

リモート操作時に Main PC の画面端へ「{target_name} を操作中」を半透明表示する。
tkinter は単一スレッド前提のため専用スレッドで mainloop を回し、他スレッドからは
queue 経由で show/hide を依頼する。
"""

from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger("overlay")


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
            logger.debug("overlay shutdown enqueue failed", exc_info=True)

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
            for name, w in (("window", self._window), ("blocker", self._blocker), ("root", self._root)):
                if w is None:
                    continue
                try:
                    w.destroy()
                except Exception:
                    logger.debug("overlay %s.destroy failed", name, exc_info=True)
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
        # 物理的にクリックが背後へ届かないよう、仮想画面全域を覆う半透明ウィンドウを出す。
        # WS_EX_TRANSPARENT は付けない（＝クリックを食う）。
        # overrideredirect + -topmost だけでは Windows で Z-order が不安定なので
        # SetWindowPos(HWND_TOPMOST) で強制的に最前面へ置く。
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            SM_XVIRTUALSCREEN = 76
            SM_YVIRTUALSCREEN = 77
            SM_CXVIRTUALSCREEN = 78
            SM_CYVIRTUALSCREEN = 79
            vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            if vw <= 0 or vh <= 0:
                vx, vy = 0, 0
                vw = self._root.winfo_screenwidth()
                vh = self._root.winfo_screenheight()

            blocker = tk.Toplevel(self._root)
            blocker.overrideredirect(True)
            blocker.attributes("-topmost", True)
            # LWA_ALPHA の丸め誤差やドライバ差で click-through 化されないよう
            # alpha は 0.05 まで上げる（視覚的にはほぼ不可視）。
            blocker.attributes("-alpha", 0.05)
            blocker.configure(bg="#000000", cursor="arrow")
            blocker.geometry(f"{vw}x{vh}+{vx}+{vy}")
            # Tk レベルでもマウス/ホイールを明示的に消化してイベント伝播を止める。
            for seq in ("<Button-1>", "<Button-2>", "<Button-3>",
                        "<ButtonRelease-1>", "<ButtonRelease-2>", "<ButtonRelease-3>",
                        "<Double-Button-1>", "<Double-Button-3>", "<MouseWheel>"):
                blocker.bind(seq, lambda e: "break")
            blocker.update_idletasks()

            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            hwnd = blocker.winfo_id()
            # foreground は奪わない（奪うと raw_mouse の Raw Input 配送経路に
            # 影響し Sub PC 側のポインタ移動が止まるため）。topmost + 通常の
            # hit-test だけで、メッセージ経由のクリックはここで消化される。
            # Raw Input で直接読むゲーム（SF6 の右クリック等）は原理的に
            # ウィンドウ遮蔽では止められないので諦める。
            user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
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
                logger.debug("blocker.lift failed", exc_info=True)
        w.lift()
        w.focus_force()
        try:
            import ctypes
            hwnd = w.winfo_id()
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            logger.debug("BringWindowToTop/SetForegroundWindow failed", exc_info=True)

        self._window = w

    def _do_hide(self):
        for attr in ("_window", "_blocker"):
            w = getattr(self, attr)
            if w is None:
                continue
            try:
                w.destroy()
            except Exception:
                logger.debug("hide %s.destroy failed", attr, exc_info=True)
            setattr(self, attr, None)
