"""Show, hide, and recover the Windows console attached to this process.

The .bat launcher starts a hidden cmd that waits for the GUI. Closing the
program therefore also closes that console. This module can unhide it on
demand (「查看后台」) or when an error is printed.
"""

from __future__ import annotations

import atexit
import os
import re
import sys
import threading
from collections.abc import Callable
from typing import TextIO

SW_HIDE = 0
SW_RESTORE = 9
_ATTACH_PARENT_PROCESS = 0xFFFFFFFF

_ERROR_RE = re.compile(
    r"(失败|错误|无效|异常|Traceback|Exception|Error:|\bERROR\b|\bFATAL\b|"
    r"ModuleNotFound|无法启动|无法播放)",
    re.I,
)

_installed = False
_wait_on_exit = False
_orig_stderr: TextIO | None = None
_title = ""
_visibility_listeners: list[Callable[[bool], None]] = []


def _dlls():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.AllocConsole.restype = wintypes.BOOL
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.AttachConsole.restype = wintypes.BOOL
    kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetConsoleOutputCP.argtypes = [wintypes.UINT]
    kernel32.SetConsoleCP.argtypes = [wintypes.UINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    return kernel32, user32


def console_hwnd() -> int:
    pair = _dlls()
    if not pair:
        return 0
    kernel32, _user32 = pair
    return int(kernel32.GetConsoleWindow() or 0)


def is_console_visible() -> bool:
    hwnd = console_hwnd()
    if not hwnd:
        return False
    pair = _dlls()
    if not pair:
        return False
    _kernel32, user32 = pair
    return bool(user32.IsWindowVisible(hwnd))


def _apply_title() -> None:
    if not _title:
        return
    pair = _dlls()
    if not pair:
        return
    kernel32, _user32 = pair
    kernel32.SetConsoleTitleW(_title)
    kernel32.SetConsoleOutputCP(65001)
    kernel32.SetConsoleCP(65001)


def _bind_stdio_to_console() -> None:
    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    global _orig_stderr
    _orig_stderr = sys.stderr
    sys.stderr = _ErrorStream(sys.stderr)


def _ensure_console() -> int:
    hwnd = console_hwnd()
    if hwnd:
        return hwnd
    pair = _dlls()
    if not pair:
        return 0
    kernel32, _user32 = pair
    if kernel32.AttachConsole(_ATTACH_PARENT_PROCESS):
        hwnd = console_hwnd()
        if hwnd:
            _apply_title()
            return hwnd
    if kernel32.AllocConsole():
        _bind_stdio_to_console()
        _apply_title()
        return console_hwnd()
    return 0


def hide_console() -> None:
    hwnd = console_hwnd()
    if hwnd:
        pair = _dlls()
        if pair:
            _kernel32, user32 = pair
            user32.ShowWindow(hwnd, SW_HIDE)
    _notify_visibility()


def show_console() -> None:
    hwnd = _ensure_console()
    if hwnd:
        pair = _dlls()
        if pair:
            _kernel32, user32 = pair
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            _apply_title()
    _notify_visibility()


def toggle_console() -> bool:
    """Show or hide the console. Returns True if it is visible afterwards."""
    if is_console_visible():
        hide_console()
        return False
    show_console()
    return True


def console_button_label() -> str:
    return "隐藏后台" if is_console_visible() else "查看后台"


def add_console_visibility_listener(callback: Callable[[bool], None]) -> None:
    _visibility_listeners.append(callback)
    try:
        callback(is_console_visible())
    except Exception:
        pass


def _notify_visibility() -> None:
    visible = is_console_visible()
    for callback in list(_visibility_listeners):
        try:
            callback(visible)
        except Exception:
            pass


def looks_like_error(text: str) -> bool:
    return bool(text and _ERROR_RE.search(text))


def mirror_log(text: str) -> None:
    """Echo GUI/backend logs to the console and unhide it on errors."""
    if not text:
        return
    try:
        print(text, flush=True)
    except OSError:
        pass
    if looks_like_error(text):
        reveal_console_on_error(text, echo=False)


def reveal_console_on_error(text: str = "", *, echo: bool = True) -> None:
    if echo and text:
        try:
            print(text, file=sys.stderr, flush=True)
        except OSError:
            pass
    show_console()


def reveal_console_and_wait(text: str = "") -> None:
    """Show the console and pause so startup/crash errors can be read."""
    global _wait_on_exit
    if text:
        try:
            print(text, file=sys.stderr, flush=True)
        except OSError:
            pass
    show_console()
    _wait_on_exit = True
    try:
        input("\n出错了。按回车键关闭窗口...")
        _wait_on_exit = False
    except (EOFError, KeyboardInterrupt, OSError):
        pass


class _ErrorStream:
    def __init__(self, wrapped: TextIO) -> None:
        self._wrapped = wrapped
        self._buf = ""

    def write(self, data: str) -> int:
        written = self._wrapped.write(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if looks_like_error(line):
                show_console()
        return written

    def flush(self) -> None:
        self._wrapped.flush()

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)


def _excepthook(exc_type, exc, tb) -> None:
    show_console()
    sys.__excepthook__(exc_type, exc, tb)
    global _wait_on_exit
    _wait_on_exit = True


def _thread_excepthook(args) -> None:
    show_console()
    sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)


def _atexit_wait() -> None:
    if not _wait_on_exit:
        return
    show_console()
    try:
        input("\n出错了。按回车键关闭窗口...")
    except (EOFError, KeyboardInterrupt, OSError):
        pass


def install_console_host(*, hide: bool | None = None, title: str = "") -> None:
    """Hide the bat/cmd window after launch; reveal it on errors.

    When *hide* is None, hide only if launched by our .bat (SUBTITLE_HIDE_CONSOLE=1).
    """
    global _installed, _orig_stderr, _title
    _title = title
    _apply_title()
    if hide is None:
        hide = os.environ.get("SUBTITLE_HIDE_CONSOLE") == "1"
    if hide:
        hide_console()
    if _installed:
        return
    _installed = True
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    if sys.stderr is not None:
        _orig_stderr = sys.stderr
        sys.stderr = _ErrorStream(sys.stderr)
    atexit.register(_atexit_wait)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError, ValueError):
        pass


def spawn_hidden_console_process(args: list[str], cwd: str | os.PathLike[str] | None = None):
    """Start a GUI helper with its own hidden console (independent of this process)."""
    import subprocess

    popen_kwargs: dict = {}
    if cwd is not None:
        popen_kwargs["cwd"] = str(cwd)
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
        popen_kwargs["startupinfo"] = startupinfo
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        env = os.environ.copy()
        env["SUBTITLE_HIDE_CONSOLE"] = "1"
        popen_kwargs["env"] = env
    return subprocess.Popen(args, **popen_kwargs)


def focus_window_by_title(title: str) -> bool:
    """Bring an existing top-level window to the front. Windows only."""
    if sys.platform != "win32" or not title:
        return False
    pair = _dlls()
    if not pair:
        return False
    _kernel32, user32 = pair
    hwnd = int(user32.FindWindowW(None, title) or 0)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True
