"""External desktop translators invoked by a global hotkey.

Current adapter: Baidu Translate. Register additional software in TRANSLATORS
(and later expose a selector in Settings) without changing the subtitle editor.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalTranslator:
    id: str
    name: str
    process_name_needles: tuple[str, ...]
    default_hotkey: str
    not_running_title: str
    not_running_message: str
    help_title: str
    help_html: str


BAIDU_TRANSLATOR = ExternalTranslator(
    id="baidu",
    name="百度翻译",
    process_name_needles=("百度翻译",),
    default_hotkey="Ctrl+Alt+C",
    not_running_title="未打开百度翻译",
    not_running_message="请先打开百度翻译软件，然后再使用翻译功能。",
    help_title="翻译快捷键说明",
    help_html="""
<p>本软件通过向系统发送「快捷键发起翻译」热键，把选中的字幕交给外部翻译软件。
当前适配的是<strong>百度翻译电脑版</strong>。</p>
<p><strong>一、在百度翻译中设置快捷键</strong></p>
<ol>
<li>打开百度翻译电脑版，并保持运行（可以最小化到托盘）。</li>
<li>点击软件中的设置（齿轮）图标。</li>
<li>找到「快捷键发起翻译」或「快捷键」设置项。</li>
<li>将快捷键改成与本软件「设置 → 翻译热键」中<strong>完全相同</strong>的组合。
百度翻译默认多为 <code>Ctrl+Alt+C</code>。</li>
</ol>
<p><strong>二、在本软件中使用</strong></p>
<ol>
<li>在「编辑字幕」对话框的「字幕内容」中选中要翻译的文字。</li>
<li>右键选择「翻译」。本软件会发送上面配置的热键。</li>
<li>若未打开百度翻译，会提示先启动，以免热键被当前输入框接收、把选中内容改成字母。</li>
</ol>
<p>请确保两边快捷键一致。以后若适配其他翻译软件，也会在本说明中列出对应的设置方法。</p>
""",
)

# 后续适配有道、网易等时，在此注册即可。
TRANSLATORS: dict[str, ExternalTranslator] = {
    BAIDU_TRANSLATOR.id: BAIDU_TRANSLATOR,
}

DEFAULT_TRANSLATOR_ID = BAIDU_TRANSLATOR.id

_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")
_MODIFIER_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "ctl": "Ctrl",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "win": "Win",
    "meta": "Win",
    "super": "Win",
    "cmd": "Win",
}
_MODIFIER_VK = {
    "Ctrl": 0x11,
    "Alt": 0x12,
    "Shift": 0x10,
    "Win": 0x5B,
}
_NAMED_KEY_VK = {
    "space": 0x20,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "enter": 0x0D,
    "return": 0x0D,
    "backspace": 0x08,
    "del": 0x2E,
    "delete": 0x2E,
    "ins": 0x2D,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pgup": 0x21,
    "pageup": 0x21,
    "pgdown": 0x22,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "plus": 0xBB,
    "minus": 0xBD,
}


def get_translator(app_id: str | None) -> ExternalTranslator:
    key = (app_id or "").strip().lower()
    return TRANSLATORS.get(key, BAIDU_TRANSLATOR)


def normalize_hotkey_text(text: str, default: str = BAIDU_TRANSLATOR.default_hotkey) -> str:
    parsed = _parse_hotkey_parts(text)
    if parsed is None:
        return default
    modifiers, key_name = parsed
    return "+".join((*modifiers, key_name))


def is_translator_running(translator: ExternalTranslator) -> bool:
    if sys.platform != "win32" or not translator.process_name_needles:
        return False
    for exe_name in _iter_process_names():
        if any(needle in exe_name for needle in translator.process_name_needles):
            return True
    return False


def send_hotkey(hotkey: str) -> bool:
    vks = _hotkey_to_vks(hotkey)
    if not vks:
        return False
    return _send_virtual_keys(vks)


def _parse_hotkey_parts(text: str) -> tuple[list[str], str] | None:
    parts = [item.strip() for item in re.split(r"\+", text.strip()) if item.strip()]
    if len(parts) < 2:
        return None
    modifiers: list[str] = []
    key_name = ""
    for part in parts:
        alias = _MODIFIER_ALIASES.get(part.lower())
        if alias:
            if alias not in modifiers:
                modifiers.append(alias)
            continue
        if key_name:
            return None
        key_name = _canonical_key_name(part)
        if not key_name:
            return None
    if not modifiers or not key_name:
        return None
    ordered = [name for name in _MODIFIER_ORDER if name in modifiers]
    return ordered, key_name


def _canonical_key_name(part: str) -> str:
    lowered = part.strip().lower()
    if len(lowered) == 1 and (lowered.isalnum()):
        return lowered.upper()
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", lowered):
        return f"F{int(lowered[1:])}"
    named = {
        "space": "Space",
        "tab": "Tab",
        "esc": "Esc",
        "escape": "Esc",
        "enter": "Enter",
        "return": "Enter",
        "backspace": "Backspace",
        "del": "Delete",
        "delete": "Delete",
        "ins": "Insert",
        "insert": "Insert",
        "home": "Home",
        "end": "End",
        "pgup": "PgUp",
        "pageup": "PgUp",
        "pgdown": "PgDown",
        "pagedown": "PgDown",
        "left": "Left",
        "up": "Up",
        "right": "Right",
        "down": "Down",
        "plus": "Plus",
        "minus": "Minus",
    }
    return named.get(lowered, "")


def _hotkey_to_vks(hotkey: str) -> list[int]:
    parsed = _parse_hotkey_parts(hotkey)
    if parsed is None:
        return []
    modifiers, key_name = parsed
    vks = [_MODIFIER_VK[name] for name in modifiers]
    key_vk = _key_name_to_vk(key_name)
    if key_vk is None:
        return []
    vks.append(key_vk)
    return vks


def _key_name_to_vk(name: str) -> int | None:
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    if name.startswith("F") and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    return _NAMED_KEY_VK.get(name.lower())


def _iter_process_names():
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    th32cs_snapprocess = 0x00000002
    max_path = 260

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * max_path),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
    if snapshot in (0, wintypes.HANDLE(-1).value):
        return
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            yield entry.szExeFile
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def _send_virtual_keys(vks: list[int]) -> bool:
    if sys.platform != "win32" or not vks:
        return False

    import ctypes
    from ctypes import wintypes

    input_keyboard = 1
    keyeventf_keyup = 0x0002
    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KeyBdInput(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class MouseInput(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class HardwareInput(ctypes.Structure):
        _fields_ = (
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        )

    class InputUnion(ctypes.Union):
        _fields_ = (("mi", MouseInput), ("ki", KeyBdInput), ("hi", HardwareInput))

    class Input(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", InputUnion))

    def make_key(vk: int, flags: int = 0) -> Input:
        event = Input()
        event.type = input_keyboard
        event.union.ki = KeyBdInput(vk, 0, flags, 0, 0)
        return event

    sequence = [make_key(vk) for vk in vks] + [make_key(vk, keyeventf_keyup) for vk in reversed(vks)]
    events = (Input * len(sequence))(*sequence)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    sent = user32.SendInput(len(events), events, ctypes.sizeof(Input))
    return sent == len(events)
