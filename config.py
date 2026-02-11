"""설정 로드/저장 모듈.

경로: ~/.config/whisper-ko/config.json
"""

from pathlib import Path
import json
import copy

CONFIG_DIR = Path.home() / ".config" / "whisper-ko"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 기본 설정값
DEFAULTS: dict = {
    "dictation_hotkey": "ctrl+shift+a",
    "translation_hotkey": "ctrl+shift+s",
    "model": "mlx-community/whisper-large-v3-turbo",
    "translation_output": "overlay",
    "google_translate_api_key": "",
    "overlay": {
        "font_size": 28,
        "max_lines": 4,
        "fade_seconds": 10,
        "opacity": 0.85,
    },
    "audio": {
        "blackhole_device_name": "BlackHole 2ch",
        "silence_threshold_db": -40,
        "silence_duration_sec": 0.8,
        "max_chunk_sec": 8,
    },
    "log_dir": "~/Documents/whisper-ko-logs",
}


def _deep_merge(base: dict, override: dict) -> dict:
    """중첩 딕셔너리를 깊은 병합한다.

    override 값이 base 위에 덮어쓰기되며,
    양쪽 모두 dict인 경우 재귀적으로 병합한다.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config() -> dict:
    """설정 파일을 로드한다.

    파일이 없거나 JSON이 손상된 경우 기본값을 반환한다.
    사용자 설정은 기본값과 깊은 병합되므로 일부 키만 지정해도 동작한다.
    """
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return _deep_merge(DEFAULTS, user)
        except (json.JSONDecodeError, ValueError):
            # 손상된 JSON → 기본값 사용
            return copy.deepcopy(DEFAULTS)
    return copy.deepcopy(DEFAULTS)


def save_config(config: dict) -> None:
    """설정을 파일에 저장한다.

    부모 디렉토리가 없으면 자동 생성한다.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_log_dir(config: dict) -> Path:
    """설정에서 로그 디렉토리 경로를 반환한다. ~ 확장 포함."""
    return Path(config.get("log_dir", DEFAULTS["log_dir"])).expanduser()
