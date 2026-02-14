"""글로벌 핫키 매니저 (pynput 기반).

백그라운드 스레드에서 키보드 이벤트를 감지하고,
등록된 핫키 조합이 눌리면 threading.Event를 통해 메인 스레드에 알린다.
직접 UI 콜백을 호출하지 않는다 (rumps 메인 스레드 제약).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)


# ── 핫키 문자열 → 사람이 읽기 좋은 포맷 ──────────────────────

_SYMBOL_MAP = {
    "cmd": "\u2318",      # ⌘
    "shift": "\u21E7",    # ⇧
    "alt": "\u2325",      # ⌥
    "ctrl": "\u2303",     # ⌃
}


def format_hotkey(hotkey: str) -> str:
    """단축키 문자열을 macOS 스타일 심볼로 변환.

    예: "ctrl+shift+m" → "⌃⇧M"
    """
    if not hotkey:
        return "-"

    parts = [p.strip() for p in hotkey.lower().split("+")]
    result: list[str] = []

    for part in parts:
        if part in _SYMBOL_MAP:
            result.append(_SYMBOL_MAP[part])
        elif part == "space":
            result.append("Space")
        else:
            # 일반 문자 키는 대문자로 표시
            result.append(part.upper())

    return "".join(result)


# ── macOS virtual keycode → 영문자 매핑 ─────────────────────
# 한글 등 비-ASCII 입력기가 활성화되면 key.char가 한글을 반환하므로
# vk(virtual keycode)로 폴백하여 영문자를 판별해야 한다.

_VK_TO_CHAR: dict[int, str] = {
    0x00: "a", 0x01: "s", 0x02: "d", 0x03: "f", 0x04: "h",
    0x05: "g", 0x06: "z", 0x07: "x", 0x08: "c", 0x09: "v",
    0x0B: "b", 0x0C: "q", 0x0D: "w", 0x0E: "e", 0x0F: "r",
    0x10: "y", 0x11: "t", 0x12: "1", 0x13: "2", 0x14: "3",
    0x15: "4", 0x16: "6", 0x17: "5", 0x18: "=", 0x19: "9",
    0x1A: "7", 0x1B: "-", 0x1C: "8", 0x1D: "0", 0x1E: "]",
    0x1F: "o", 0x20: "u", 0x21: "[", 0x22: "i", 0x23: "p",
    0x25: "l", 0x26: "j", 0x27: "'", 0x28: "k", 0x29: ";",
    0x2A: "\\", 0x2B: ",", 0x2C: "/", 0x2D: "n", 0x2E: "m",
    0x2F: ".", 0x32: "`",
}

# ── pynput 키 정규화 ─────────────────────────────────────────

def _norm_key(key) -> object:
    """pynput key 객체를 비교 가능한 표준 형태로 정규화.

    ctrl_l/ctrl_r → ctrl, shift_l/shift_r → shift 등.
    한글 입력기가 활성화되면 key.char 대신 key.vk로 영문자를 판별한다.
    """
    if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
        return keyboard.Key.ctrl
    if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
        return keyboard.Key.shift
    if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
        return keyboard.Key.alt
    if key in (keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r):
        return keyboard.Key.cmd
    if key == keyboard.Key.space:
        return keyboard.Key.space

    # 일반 문자 키
    if isinstance(key, keyboard.KeyCode):
        # 1) key.char가 ASCII 문자면 바로 사용
        if key.char and key.char.isascii() and key.char.isprintable():
            return ("char", key.char.lower())
        # 2) 한글 등 비-ASCII → vk(virtual keycode)로 영문자 판별
        vk = getattr(key, "vk", None)
        if vk is not None and vk in _VK_TO_CHAR:
            return ("char", _VK_TO_CHAR[vk])

    return key


def parse_hotkey(hotkey_str: str) -> frozenset:
    """핫키 문자열을 정규화된 pynput 키 frozenset으로 파싱.

    예: "ctrl+shift+m" → frozenset({Key.ctrl, Key.shift, ("char", "m")})
    """
    parts = (hotkey_str or "").lower().split("+")
    keys: set = set()

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part == "cmd":
            keys.add(keyboard.Key.cmd)
        elif part == "shift":
            keys.add(keyboard.Key.shift)
        elif part == "alt":
            keys.add(keyboard.Key.alt)
        elif part == "ctrl":
            keys.add(keyboard.Key.ctrl)
        elif part == "space":
            keys.add(keyboard.Key.space)
        elif len(part) == 1:
            keys.add(("char", part))

    return frozenset(keys)


# ── HotkeyManager 클래스 ────────────────────────────────────

class HotkeyManager:
    """글로벌 핫키를 등록/해제하고 pynput Listener를 관리.

    사용법:
        mgr = HotkeyManager()
        mgr.register("ctrl+shift+m", on_dictation_event)
        mgr.start()
        ...
        mgr.stop()

    callback은 non-blocking이어야 한다 (예: threading.Event.set).
    pynput 리스너 스레드에서 직접 호출되므로 UI 작업 금지.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, tuple[frozenset, Callable]] = {}
        self._release_bindings: dict[str, tuple[frozenset, Callable]] = {}
        # 핫키별 "이미 발화됨" 플래그 (키를 뗄 때까지 재발화 방지)
        self._fired: dict[str, bool] = {}
        self._current_keys: set = set()
        self._listener: keyboard.Listener | None = None
        self._lock = threading.Lock()

    # ── 등록 / 해제 ──────────────────────────────────────

    def register(
        self,
        hotkey_str: str,
        callback: Callable,
        on_release: Callable | None = None,
    ) -> None:
        """핫키 문자열과 콜백을 등록.

        Args:
            hotkey_str: 핫키 문자열 (예: "ctrl+shift+a")
            callback: 키를 눌렀을 때 호출되는 콜백
            on_release: 키를 뗐을 때 호출되는 콜백 (push-to-talk용)
        """
        keys = parse_hotkey(hotkey_str)
        if not keys:
            return
        with self._lock:
            self._bindings[hotkey_str] = (keys, callback)
            self._fired[hotkey_str] = False
            if on_release is not None:
                self._release_bindings[hotkey_str] = (keys, on_release)

    def unregister(self, hotkey_str: str) -> None:
        """등록된 핫키를 해제."""
        with self._lock:
            self._bindings.pop(hotkey_str, None)
            self._fired.pop(hotkey_str, None)
            self._release_bindings.pop(hotkey_str, None)

    # ── 리스너 시작 / 중지 ───────────────────────────────

    def start(self) -> None:
        """pynput 키보드 리스너를 백그라운드 스레드로 시작."""
        if self._listener is not None:
            self.stop()

        self._current_keys.clear()
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()

    def stop(self) -> None:
        """리스너를 중지하고 정리."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._current_keys.clear()

    # ── 내부 이벤트 핸들러 ───────────────────────────────

    def _on_press(self, key) -> None:
        nk = _norm_key(key)
        self._current_keys.add(nk)
        logger.debug("KEY PRESS: raw=%r  norm=%r  current=%s", key, nk, self._current_keys)

        with self._lock:
            for hk_str, (keyset, callback) in self._bindings.items():
                if not self._fired.get(hk_str) and keyset.issubset(self._current_keys):
                    self._fired[hk_str] = True
                    logger.info("HOTKEY MATCHED: %s", hk_str)
                    try:
                        callback()
                    except Exception:
                        logger.exception("Hotkey callback error: %s", hk_str)

    def _on_release(self, key) -> None:
        nk = _norm_key(key)
        self._current_keys.discard(nk)

        with self._lock:
            for hk_str, (keyset, _callback) in self._bindings.items():
                was_fired = self._fired.get(hk_str, False)
                if not keyset.issubset(self._current_keys):
                    self._fired[hk_str] = False
                    # 키가 눌린 상태에서 릴리즈 시 on_release 콜백 호출
                    if was_fired and hk_str in self._release_bindings:
                        _, release_cb = self._release_bindings[hk_str]
                        try:
                            release_cb()
                        except Exception:
                            pass
