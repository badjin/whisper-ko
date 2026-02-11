"""PyAudio 디바이스 열거 및 BlackHole 감지."""

from __future__ import annotations

from typing import Optional

import pyaudio


def list_input_devices() -> list[dict]:
    """사용 가능한 모든 입력 디바이스 목록을 반환한다.

    Returns:
        [{"index": int, "name": str, "channels": int, "rate": float}, ...]
    """
    pa = pyaudio.PyAudio()
    try:
        devices: list[dict] = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            # 입력 채널이 있는 디바이스만 포함
            if info.get("maxInputChannels", 0) > 0:
                devices.append(
                    {
                        "index": i,
                        "name": info["name"],
                        "channels": int(info["maxInputChannels"]),
                        "rate": float(info["defaultSampleRate"]),
                    }
                )
        return devices
    finally:
        pa.terminate()


def find_blackhole_device(name: str = "BlackHole 2ch") -> Optional[int]:
    """BlackHole 가상 오디오 디바이스의 인덱스를 찾는다.

    Args:
        name: 검색할 디바이스 이름 (기본값: "BlackHole 2ch")

    Returns:
        디바이스 인덱스 또는 찾지 못한 경우 None
    """
    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if name.lower() in info.get("name", "").lower():
                if info.get("maxInputChannels", 0) > 0:
                    return i
        return None
    finally:
        pa.terminate()


def get_default_input_device() -> int:
    """시스템 기본 입력 디바이스 인덱스를 반환한다.

    Returns:
        기본 입력 디바이스 인덱스

    Raises:
        OSError: 기본 입력 디바이스를 찾을 수 없는 경우
    """
    pa = pyaudio.PyAudio()
    try:
        info = pa.get_default_input_device_info()
        return int(info["index"])
    except IOError as e:
        raise OSError("기본 입력 디바이스를 찾을 수 없습니다") from e
    finally:
        pa.terminate()
