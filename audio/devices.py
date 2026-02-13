"""PyAudio 디바이스 열거."""

from __future__ import annotations

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
