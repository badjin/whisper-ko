"""클립보드 복사 + Cmd+V 붙여넣기 모듈.

Mode 1 (받아쓰기)에서 전사된 텍스트를 커서 위치에 삽입한다.
원본 borinomi/mlx-whisper 앱과 동일하게 pyperclip + pyautogui 사용.
"""

from __future__ import annotations

import logging
import time

import pyperclip
import pyautogui

logger = logging.getLogger(__name__)


def copy_and_paste(text: str) -> None:
    """텍스트를 클립보드에 복사하고 Cmd+V로 붙여넣기한다."""
    if not text:
        return

    try:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("command", "v")
    except Exception:
        logger.exception("클립보드 붙여넣기 실패")


def copy_only(text: str) -> None:
    """텍스트를 클립보드에만 복사한다 (붙여넣기 안 함)."""
    if not text:
        return

    try:
        pyperclip.copy(text)
    except Exception:
        logger.exception("클립보드 복사 실패")
