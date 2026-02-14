"""
Voice Activity Detection (음성 활동 감지) 모듈.

Jarvis Mode에서 마이크 입력의 음성 구간을 자동으로 감지하여 세그먼트를 분할한다.
RMS dB 기반 에너지 계산 로직을 audio/system.py와 공유.
"""

import math
import numpy as np


def compute_rms_db(data: bytes) -> float:
    """
    원시 Int16 오디오 바이트를 RMS dB 값으로 변환.

    Args:
        data: Int16 PCM 오디오 데이터 (바이트)

    Returns:
        RMS dB 값 (dBFS). 무음이면 -80.0 반환.
    """
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return -80.0
    rms = np.sqrt(np.mean(samples ** 2))
    if rms < 1e-10:
        return -80.0
    return 20 * math.log10(rms / 32768.0)


class SilenceDetector:
    """
    음성 활동 감지 및 세그먼트 분할.

    RMS dB 기반 에너지를 계산하여 음성 구간과 무음 구간을 판별하고,
    무음이 일정 시간 지속되거나 최대 길이에 도달하면 세그먼트를 완료 처리.
    """

    def __init__(
        self,
        silence_threshold_db: float = -40,
        silence_duration_sec: float = 1.5,
        max_segment_sec: float = 8,
        rate: int = 16000,
        chunk: int = 1024,
    ):
        """
        Args:
            silence_threshold_db: 무음 판정 임계값 (dBFS)
            silence_duration_sec: 무음 지속 시간 (초)
            max_segment_sec: 세그먼트 최대 길이 (초)
            rate: 샘플링 레이트 (Hz)
            chunk: 청크 크기 (프레임 수)
        """
        self._silence_threshold_db = silence_threshold_db
        self._silence_duration_sec = silence_duration_sec
        self._max_segment_sec = max_segment_sec
        self._rate = rate
        self._chunk = chunk

        # 프레임 계산
        self._frames_per_sec = rate / chunk
        self._silence_frames_limit = int(silence_duration_sec * self._frames_per_sec)
        self._max_frames = int(max_segment_sec * self._frames_per_sec)

        # 상태 초기화
        self._frames: list[bytes] = []
        self._frame_count = 0
        self._silent_frames = 0
        self._has_audio = False

    def feed(self, data: bytes) -> bool:
        """
        오디오 데이터 청크를 공급하고 세그먼트 완료 여부를 반환.

        Args:
            data: Int16 PCM 오디오 데이터 (바이트)

        Returns:
            세그먼트가 완료되었으면 True, 아니면 False.
            True 반환 후에는 get_frames()를 호출하여 데이터를 가져가야 함.
        """
        self._frames.append(data)
        self._frame_count += 1

        # RMS dB 계산
        rms_db = compute_rms_db(data)

        # 무음 판정
        if rms_db < self._silence_threshold_db:
            self._silent_frames += 1
        else:
            self._silent_frames = 0
            self._has_audio = True

        # 세그먼트 완료 조건
        # 1. 음성이 있었고 무음이 일정 시간 지속
        if self._has_audio and self._silent_frames >= self._silence_frames_limit:
            return True

        # 2. 최대 길이 도달
        if self._frame_count >= self._max_frames:
            return True

        return False

    def get_frames(self) -> list[bytes]:
        """
        누적된 오디오 프레임을 반환하고 상태를 초기화.

        Returns:
            누적된 오디오 프레임 리스트 (복사본)
        """
        frames = self._frames.copy()
        self.reset()
        return frames

    def reset(self):
        """상태를 초기화 (새 세그먼트 시작)."""
        self._frames = []
        self._frame_count = 0
        self._silent_frames = 0
        self._has_audio = False

    @property
    def has_audio(self) -> bool:
        """음성이 감지되었는지 여부."""
        return self._has_audio
