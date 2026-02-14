"""마이크 녹음 모듈 (Mode 1: 받아쓰기)."""

from __future__ import annotations

import math
import struct
import tempfile
import threading
import wave
from typing import Callable, Optional

import pyaudio


def compute_rms_db(data: bytes) -> float:
    """PCM16 오디오 데이터의 RMS 에너지를 dB로 변환한다."""
    n = len(data) // 2
    if n == 0:
        return -100.0
    samples = struct.unpack(f"<{n}h", data)
    ms = sum(s * s for s in samples) / n
    if ms <= 0:
        return -100.0
    return 10 * math.log10(ms / (32768 * 32768))

# 오디오 포맷 상수
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024


class MicRecorder:
    """마이크 입력을 녹음하여 WAV 파일로 저장하는 레코더.

    사용 예시::

        recorder = MicRecorder()
        recorder.start()
        # ... 녹음 중 ...
        wav_path = recorder.stop()  # WAV 임시 파일 경로 반환
    """

    def __init__(
        self,
        rate: int = RATE,
        channels: int = CHANNELS,
        chunk: int = CHUNK,
        device_index: Optional[int] = None,
        on_audio_level: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._rate = rate
        self._channels = channels
        self._chunk = chunk
        self._device_index = device_index
        self.on_audio_level = on_audio_level

        self._recording = False
        self._frames: list[bytes] = []
        self._audio: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        """현재 녹음 중인지 여부."""
        return self._recording

    def start(self) -> None:
        """백그라운드 스레드에서 녹음을 시작한다.

        Raises:
            RuntimeError: 이미 녹음 중인 경우
            OSError: 오디오 스트림을 열 수 없는 경우
        """
        with self._lock:
            if self._recording:
                raise RuntimeError("이미 녹음 중입니다")

            self._frames = []
            self._audio = pyaudio.PyAudio()

            # 스트림 열기
            kwargs = {
                "format": FORMAT,
                "channels": self._channels,
                "rate": self._rate,
                "input": True,
                "frames_per_buffer": self._chunk,
            }
            if self._device_index is not None:
                kwargs["input_device_index"] = self._device_index

            try:
                self._stream = self._audio.open(**kwargs)
            except Exception as e:
                self._cleanup()
                raise OSError(f"오디오 스트림을 열 수 없습니다: {e}") from e

            self._recording = True
            self._thread = threading.Thread(target=self._record_loop, daemon=True)
            self._thread.start()

    def stop(self) -> Optional[str]:
        """녹음을 중지하고 WAV 임시 파일 경로를 반환한다.

        녹음된 프레임이 없으면 None을 반환한다.
        호출자가 반환된 파일을 사용 후 삭제해야 한다.

        Returns:
            WAV 파일 경로 또는 None
        """
        with self._lock:
            if not self._recording:
                return None
            self._recording = False

        # 녹음 스레드 종료 대기
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        # 스트림 정리
        self._close_stream()

        # 녹음 데이터가 없으면 정리 후 반환
        if not self._frames:
            self._cleanup()
            return None

        # WAV 파일로 저장
        wav_path = self._save_wav()
        self._frames = []
        self._cleanup()
        return wav_path

    def _record_loop(self) -> None:
        """녹음 루프 (백그라운드 스레드)."""
        while self._recording:
            try:
                data = self._stream.read(self._chunk, exception_on_overflow=False)
                self._frames.append(data)
                if self.on_audio_level is not None:
                    try:
                        self.on_audio_level(compute_rms_db(data))
                    except Exception:
                        pass
            except Exception:
                # 스트림 에러 시 녹음 중단
                self._recording = False
                break

    def _save_wav(self) -> str:
        """녹음된 프레임을 WAV 임시 파일로 저장한다.

        Returns:
            저장된 WAV 파일의 절대 경로
        """
        fd, path = tempfile.mkstemp(suffix=".wav")
        # mkstemp가 열어둔 fd 닫기 (wave.open이 파일을 새로 열기 때문)
        import os
        os.close(fd)

        wf = wave.open(path, "wb")
        wf.setnchannels(self._channels)
        wf.setsampwidth(self._audio.get_sample_size(FORMAT))
        wf.setframerate(self._rate)
        wf.writeframes(b"".join(self._frames))
        wf.close()

        return path

    def _close_stream(self) -> None:
        """오디오 스트림을 안전하게 닫는다."""
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _cleanup(self) -> None:
        """PyAudio 인스턴스를 정리한다."""
        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None
