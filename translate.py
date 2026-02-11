"""Google Cloud Translation API v2 래퍼 모듈.

REST API를 requests로 직접 호출한다.
google-cloud-translate SDK는 의존성이 많아 사용하지 않는다.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

API_URL = "https://translation.googleapis.com/language/translate/v2"


def translate_text(
    text: str,
    target: str = "ko",
    api_key: str = "",
) -> str:
    """텍스트를 대상 언어로 번역한다.

    Args:
        text: 번역할 원문 텍스트.
        target: 대상 언어 코드 (예: "ko", "en").
        api_key: Google Cloud Translation API 키.

    Returns:
        번역된 텍스트. 에러 시 에러 메시지 문자열을 반환한다 (예외 발생 안 함).
    """
    if not text or not text.strip():
        return ""

    if not api_key:
        logger.error("Google Translate API 키가 설정되지 않았습니다")
        return "[번역 오류: API 키 없음]"

    try:
        resp = requests.post(
            API_URL,
            params={
                "key": api_key,
                "q": text,
                "target": target,
                "format": "text",
            },
            timeout=10,
        )

        if resp.status_code != 200:
            # API 에러 응답 처리
            try:
                error_data = resp.json()
                error_msg = error_data.get("error", {}).get("message", resp.text)
            except ValueError:
                error_msg = resp.text
            logger.error("Translation API 에러 (%d): %s", resp.status_code, error_msg)
            return f"[번역 오류: {resp.status_code}]"

        data = resp.json()
        translated = data["data"]["translations"][0]["translatedText"]
        return translated

    except requests.ConnectionError:
        logger.error("Translation API 네트워크 연결 실패")
        return "[번역 오류: 네트워크 연결 실패]"

    except requests.Timeout:
        logger.error("Translation API 요청 타임아웃")
        return "[번역 오류: 요청 타임아웃]"

    except (KeyError, IndexError):
        logger.exception("Translation API 응답 파싱 실패")
        return "[번역 오류: 응답 파싱 실패]"

    except Exception:
        logger.exception("Translation API 알 수 없는 오류")
        return "[번역 오류]"
