"""MLX Whisper 래퍼 모듈.

음성 파일을 텍스트로 전사한다.
- Mode 1 (받아쓰기): language="ko" 고정
- Mode 2 (번역): language=None으로 자동 감지
"""

from __future__ import annotations

import logging
import threading

import mlx_whisper

logger = logging.getLogger(__name__)

# 기본 모델 (config에서 오버라이드 가능)
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

# 모델 프리로드 상태
_preload_done = threading.Event()


def preload_model(model: str = DEFAULT_MODEL) -> None:
    """앱 시작 시 백그라운드에서 모델을 미리 로드한다.

    ModelHolder 내부 캐시에 모델을 올려두어
    첫 번째 전사 호출의 지연을 제거한다.
    """
    def _load():
        try:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
            ModelHolder.get_model(model, mx.float16)
            logger.info("Whisper 모델 프리로드 완료: %s", model)
        except Exception:
            logger.exception("Whisper 모델 프리로드 실패")
        finally:
            _preload_done.set()

    threading.Thread(target=_load, daemon=True).start()


def transcribe(
    audio_path: str,
    model: str = DEFAULT_MODEL,
    language: str | None = "ko",
) -> dict:
    """음성 파일을 텍스트로 전사한다.

    Args:
        audio_path: WAV 파일 경로
        model: HuggingFace 모델 경로 또는 로컬 경로
        language: 전사 언어 코드 (예: "ko", "en").
                  None이면 Whisper가 자동 감지 (Mode 2용).

    Returns:
        {"text": str, "language": str}
        text가 비어있으면 인식 실패를 의미한다.
    """
    # 프리로드 완료 대기 (최대 30초)
    _preload_done.wait(timeout=30)

    try:
        kwargs: dict = {
            "path_or_hf_repo": model,
        }
        if language is not None:
            kwargs["language"] = language

        result = mlx_whisper.transcribe(audio_path, **kwargs)

        text = (result.get("text") or "").strip()
        detected_language = result.get("language", language or "unknown")

        return {
            "text": text,
            "language": detected_language,
        }

    except Exception:
        logger.exception("Whisper 전사 실패: %s", audio_path)
        return {
            "text": "",
            "language": language or "unknown",
        }
