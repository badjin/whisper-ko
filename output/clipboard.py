"""클립보드 복사 + Cmd+V 붙여넣기 모듈.

Mode 1 (받아쓰기)에서 전사된 텍스트를 커서 위치에 삽입한다.
NSPasteboard(클립보드) + pyautogui(키 입력) 방식.

push-to-talk 핫키의 modifier 키(Ctrl+Shift 등)가 잔류하는 문제를 방지하기 위해
Cmd+V 전송 전에 모든 modifier 키를 명시적으로 릴리즈한다.
"""

from __future__ import annotations

import logging
import time

import AppKit
import pyautogui

logger = logging.getLogger(__name__)


def _set_clipboard(text: str) -> None:
    """NSPasteboard를 사용하여 클립보드에 텍스트를 설정한다."""
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)


def _release_modifiers() -> None:
    """잔류하는 modifier 키를 모두 릴리즈한다.

    push-to-talk 핫키(예: Ctrl+Shift+A) 릴리즈 후에도
    OS 레벨에서 modifier가 남아있을 수 있어 Cmd+V에 간섭한다.
    """
    for key in ("ctrl", "shift", "alt", "command"):
        pyautogui.keyUp(key)


def _cmd_v() -> None:
    """Cmd+V를 명시적 keyDown/keyUp으로 수행한다.

    pyautogui.hotkey("command", "v")보다 안정적:
    각 단계 사이에 짧은 딜레이를 두어 CGEvent 전달을 보장한다.
    """
    pyautogui.keyDown("command")
    time.sleep(0.05)
    pyautogui.press("v")
    time.sleep(0.05)
    pyautogui.keyUp("command")


def copy_and_paste(text: str) -> None:
    """텍스트를 클립보드에 복사하고 Cmd+V로 붙여넣기한다."""
    if not text:
        return

    try:
        _set_clipboard(text)
        _release_modifiers()
        time.sleep(0.05)
        _cmd_v()
    except Exception:
        logger.exception("클립보드 붙여넣기 실패")


def paste_and_enter(text: str) -> None:
    """텍스트를 클립보드에 복사하고 Cmd+V 붙여넣기 + Enter를 수행한다."""
    if not text:
        return

    try:
        _set_clipboard(text)
        _release_modifiers()
        time.sleep(0.05)
        _cmd_v()
        time.sleep(0.05)
        pyautogui.press("enter")
    except Exception:
        logger.exception("클립보드 붙여넣기 실패")


def copy_only(text: str) -> None:
    """텍스트를 클립보드에만 복사한다 (붙여넣기 안 함)."""
    if not text:
        return

    try:
        _set_clipboard(text)
    except Exception:
        logger.exception("클립보드 복사 실패")
