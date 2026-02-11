"""타임스탬프 번역 로그 파일 모듈.

Mode 2 (번역)에서 원문과 번역문을 일별 로그 파일에 기록한다.
파일 형식: [HH:MM:SS] 원문 - 번역문
파일명: whisper-ko-YYYY-MM-DD.log
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class TranslationLogger:
    """번역 결과를 일별 로그 파일에 기록한다."""

    def __init__(self, log_dir: str) -> None:
        """로거를 초기화한다.

        Args:
            log_dir: 로그 디렉토리 경로. ~ 확장을 지원한다.
        """
        self._log_dir = Path(log_dir).expanduser()

    def _ensure_dir(self) -> None:
        """로그 디렉토리가 없으면 생성한다."""
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def get_log_path(self) -> Path:
        """오늘 날짜의 로그 파일 경로를 반환한다."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self._log_dir / f"whisper-ko-{today}.log"

    def log(self, original: str, translated: str) -> None:
        """원문과 번역문을 로그 파일에 기록한다.

        Args:
            original: 원문 텍스트.
            translated: 번역된 텍스트.
        """
        if not original and not translated:
            return

        try:
            self._ensure_dir()
            timestamp = datetime.now().strftime("%H:%M:%S")
            line = f"[{timestamp}] {original} - {translated}\n"

            log_path = self.get_log_path()
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)

        except Exception:
            logger.exception("번역 로그 기록 실패")
