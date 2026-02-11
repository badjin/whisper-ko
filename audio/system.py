"""시스템 오디오 캡처 모듈 (Mode 2: 번역).

BlackHole 가상 디바이스로 시스템 오디오를 캡처하고,
RMS 에너지 기반 무음 감지로 청크를 분할하여 콜백으로 전달한다.
"""

from __future__ import annotations

import logging
import math
import os
import queue
import tempfile
import threading
import wave
from typing import Callable, Optional

import numpy as np
import pyaudio

from config import DEFAULTS

logger = logging.getLogger(__name__)

# 오디오 포맷 상수 (mic.py와 동일)
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024

# paInt16 최대값 (dB 기준점)
MAX_INT16 = 32768.0


def _compute_rms_db(data: bytes) -> float:
    """오디오 프레임의 RMS 에너지를 dB로 계산한다.

    Args:
        data: paInt16 형식의 raw 오디오 바이트

    Returns:
        RMS 에너지 (dB). 무음이면 -float('inf') 반환.
    """
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    rms = np.sqrt(np.mean(samples ** 2))
    if rms < 1e-10:
        return -float("inf")
    return 20.0 * math.log10(rms / MAX_INT16)


class SystemAudioCapture:
    """BlackHole 시스템 오디오 캡처 + 에너지 기반 청크 분할.

    사용 예시::

        from audio.devices import find_blackhole_device

        dev_idx = find_blackhole_device()
        capture = SystemAudioCapture(device_index=dev_idx, config=config)
        capture.start(on_chunk_ready=lambda path: print(f"청크: {path}"))
        # ... 캡처 중 ...
        capture.stop()
    """

    def __init__(
        self,
        device_index: int,
        rate: int = RATE,
        channels: int = CHANNELS,
        chunk: int = CHUNK,
        config: Optional[dict] = None,
    ) -> None:
        self._device_index = device_index
        self._rate = rate
        self._channels = channels
        self._chunk = chunk

        # 설정에서 무음 감지 파라미터 로드
        audio_cfg = (config or {}).get("audio", DEFAULTS["audio"])
        self._silence_threshold_db: float = audio_cfg.get(
            "silence_threshold_db",
            DEFAULTS["audio"]["silence_threshold_db"],
        )
        self._silence_duration_sec: float = audio_cfg.get(
            "silence_duration_sec",
            DEFAULTS["audio"]["silence_duration_sec"],
        )
        self._max_chunk_sec: float = audio_cfg.get(
            "max_chunk_sec",
            DEFAULTS["audio"]["max_chunk_sec"],
        )

        # 프레임 수 기반 타이머 계산
        self._frames_per_sec = self._rate / self._chunk
        self._silence_frames_limit = int(
            self._silence_duration_sec * self._frames_per_sec
        )
        self._max_chunk_frames = int(
            self._max_chunk_sec * self._frames_per_sec
        )

        # 상태
        self._capturing = False
        self._on_chunk_ready: Optional[Callable[[str], None]] = None
        self._audio: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._chunk_queue: queue.Queue[Optional[str]] = queue.Queue()
        self._lock = threading.Lock()

    @property
    def is_capturing(self) -> bool:
        """현재 캡처 중인지 여부."""
        return self._capturing

    def start(self, on_chunk_ready: Callable[[str], None]) -> None:
        """백그라운드 스레드에서 시스템 오디오 캡처를 시작한다.

        Args:
            on_chunk_ready: 청크가 준비되면 호출되는 콜백. WAV 파일 경로를 인자로 받는다.
                호출자가 사용 후 파일을 삭제해야 한다.

        Raises:
            RuntimeError: 이미 캡처 중인 경우
            OSError: 오디오 스트림을 열 수 없는 경우
        """
        with self._lock:
            if self._capturing:
                raise RuntimeError("이미 캡처 중입니다")

            self._on_chunk_ready = on_chunk_ready
            self._audio = pyaudio.PyAudio()

            try:
                self._stream = self._audio.open(
                    format=FORMAT,
                    channels=self._channels,
                    rate=self._rate,
                    input=True,
                    frames_per_buffer=self._chunk,
                    input_device_index=self._device_index,
                )
            except Exception as e:
                self._cleanup()
                raise OSError(
                    f"BlackHole 오디오 스트림을 열 수 없습니다: {e}"
                ) from e

            self._capturing = True

            # 처리 워커 스레드 (순차 처리로 동시성 충돌 방지)
            self._chunk_queue = queue.Queue()
            self._worker_thread = threading.Thread(
                target=self._process_loop, daemon=True
            )
            self._worker_thread.start()

            # 캡처 스레드
            self._thread = threading.Thread(
                target=self._capture_loop, daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """캡처를 중지하고 남은 버퍼를 flush한다.

        남은 프레임이 있으면 마지막 청크로 저장하여 콜백을 호출한다.
        """
        with self._lock:
            if not self._capturing:
                return
            self._capturing = False

        # 캡처 스레드 종료 대기
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        # 워커 스레드 종료 (None 센티넬)
        self._chunk_queue.put(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=10.0)
            self._worker_thread = None

        # 스트림 및 PyAudio 정리
        self._close_stream()
        self._cleanup()

    def _capture_loop(self) -> None:
        """캡처 루프 (백그라운드 스레드).

        오디오 프레임을 읽으면서 에너지를 모니터링하고,
        무음 감지 또는 최대 길이 초과 시 청크를 분할한다.
        """
        frames: list[bytes] = []
        frame_count = 0
        silent_frames = 0
        has_audio = False  # 실제 소리가 있었는지 추적

        while self._capturing:
            try:
                data = self._stream.read(self._chunk, exception_on_overflow=False)
            except Exception:
                logger.exception("오디오 스트림 읽기 실패, 캡처 중단")
                self._capturing = False
                break

            frames.append(data)
            frame_count += 1

            # RMS 에너지 계산
            rms_db = _compute_rms_db(data)

            if rms_db <= self._silence_threshold_db:
                silent_frames += 1
            else:
                silent_frames = 0
                has_audio = True

            # 청크 분할 조건 확인
            should_split = False

            # 조건 1: 무음 구간이 임계값을 초과하고 실제 오디오가 있었던 경우
            if (
                silent_frames >= self._silence_frames_limit
                and has_audio
                and frame_count > self._silence_frames_limit
            ):
                should_split = True

            # 조건 2: 최대 청크 길이 초과 (강제 분할)
            if frame_count >= self._max_chunk_frames:
                should_split = True

            if should_split:
                self._flush_chunk(frames)
                frames = []
                frame_count = 0
                silent_frames = 0
                has_audio = False

        # 루프 종료 시 남은 프레임 flush
        if frames and has_audio:
            self._flush_chunk(frames)

    def _flush_chunk(self, frames: list[bytes]) -> None:
        """수집된 프레임을 WAV 파일로 저장하고 처리 큐에 넣는다.

        캡처 루프가 멈추지 않도록 처리는 워커 스레드에서 순차 실행한다.

        Args:
            frames: raw 오디오 프레임 리스트
        """
        if not frames:
            return

        wav_path = self._save_wav(frames)
        if wav_path:
            self._chunk_queue.put(wav_path)

    def _process_loop(self) -> None:
        """처리 워커 루프. 큐에서 청크를 순차적으로 모두 처리한다."""
        while True:
            wav_path = self._chunk_queue.get()
            if wav_path is None:
                break

            if self._on_chunk_ready:
                try:
                    self._on_chunk_ready(wav_path)
                except Exception:
                    logger.exception("on_chunk_ready 콜백 실행 중 에러")

    def _save_wav(self, frames: list[bytes]) -> Optional[str]:
        """프레임을 WAV 임시 파일로 저장한다.

        Args:
            frames: raw 오디오 프레임 리스트

        Returns:
            저장된 WAV 파일의 절대 경로. 에러 시 None.
        """
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="whisper-ko-chunk-")
            os.close(fd)

            wf = wave.open(path, "wb")
            wf.setnchannels(self._channels)
            wf.setsampwidth(pyaudio.get_sample_size(FORMAT))
            wf.setframerate(self._rate)
            wf.writeframes(b"".join(frames))
            wf.close()

            return path
        except Exception:
            logger.exception("WAV 파일 저장 실패")
            return None

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
