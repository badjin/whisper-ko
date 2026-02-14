"""MLX Whisper 래퍼 모듈.

음성 파일을 텍스트로 전사한다.
- Mode 1 (받아쓰기): language="ko" 고정
- Mode 2 (번역): language=None으로 자동 감지
"""

from __future__ import annotations

import logging
import re
import threading

import mlx_whisper

logger = logging.getLogger(__name__)

# 기본 모델 (config에서 오버라이드 가능)
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

# 모델 프리로드 상태
_preload_done = threading.Event()

# ── Whisper hallucination 필터 ────────────────────────────────
# 무음/저음량 구간에서 Whisper가 반복 생성하는 환각 패턴
_HALLUCINATION_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(감사합니다\.?\s*)+$"),
    re.compile(r"^(thank\s*you\.?\s*)+$", re.IGNORECASE),
    re.compile(r"^(thanks?\s*(for\s+watching)?\.?\s*)+$", re.IGNORECASE),
    re.compile(r"^(please\s+subscribe\.?\s*)+$", re.IGNORECASE),
    re.compile(r"^(구독과\s*좋아요.*)+$"),
    re.compile(r"^(시청해\s*주셔서\s*감사합니다\.?\s*)+$"),
    re.compile(r"^(좋아요.*구독.*)+$"),
    re.compile(r"^[\s.…。,，!！?？]+$"),  # 구두점만
]


def _is_hallucination(text: str) -> bool:
    """Whisper hallucination 패턴인지 확인한다."""
    text = text.strip()
    if not text:
        return True
    for pattern in _HALLUCINATION_PATTERNS:
        if pattern.match(text):
            return True
    return False


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
    raw: bool = False,
    initial_prompt: str | None = None,
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
            # hallucination 억제: 이전 텍스트 컨텍스트 전파 차단
            "condition_on_previous_text": False,
        }

        if raw:
            # raw 모드: 웨이크/종료 워드 체크용 — 최대한 관대하게
            kwargs["no_speech_threshold"] = 0.8
            kwargs["compression_ratio_threshold"] = 3.0
        else:
            # 일반 모드: 환각 억제 강화
            kwargs["hallucination_silence_threshold"] = 0.5
            kwargs["no_speech_threshold"] = 0.4
            kwargs["compression_ratio_threshold"] = 2.0

        if language is not None:
            kwargs["language"] = language
        if initial_prompt is not None:
            kwargs["initial_prompt"] = initial_prompt

        result = mlx_whisper.transcribe(audio_path, **kwargs)

        text = (result.get("text") or "").strip()
        detected_language = result.get("language", language or "unknown")

        # hallucination 필터링 (raw=True이면 건너뜀, 웨이크 워드 체크용)
        if not raw and _is_hallucination(text):
            logger.debug("Hallucination 필터됨: %r", text)
            text = ""

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

    finally:
        # MLX GPU 메모리 캐시 해제 (전사마다 누적되는 것 방지)
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass
