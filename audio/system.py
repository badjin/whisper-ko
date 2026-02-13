"""시스템 오디오 캡처 모듈 (Mode 2: 번역).

ScreenCaptureKit Swift 바이너리로 시스템 오디오를 캡처하고,
RMS 에너지 기반 무음 감지로 청크를 분할하여 콜백으로 전달한다.
"""

from __future__ import annotations

import logging
import math
import os
import queue
import subprocess
import tempfile
import threading
import wave
from typing import Callable, Optional

import numpy as np

from config import DEFAULTS

logger = logging.getLogger(__name__)

# 오디오 포맷 상수
CHANNELS = 1
RATE = 16000
CHUNK = 1024
SAMPLE_WIDTH = 2  # Int16 = 2바이트

# paInt16 최대값 (dB 기준점)
MAX_INT16 = 32768.0

# Swift 바이너리 경로
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SWIFT_SRC = os.path.join(_SCRIPT_DIR, "sck_capture.swift")
_SWIFT_BIN = os.path.join(_SCRIPT_DIR, "sck_capture")


def _compute_rms_db(data: bytes) -> float:
    """오디오 프레임의 RMS 에너지를 dB로 계산한다.

    Args:
        data: Int16 형식의 raw 오디오 바이트

    Returns:
        RMS 에너지 (dB). 무음이면 -float('inf') 반환.
    """
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    rms = np.sqrt(np.mean(samples ** 2))
    if rms < 1e-10:
        return -float("inf")
    return 20.0 * math.log10(rms / MAX_INT16)


def _ensure_binary() -> str:
    """Swift 바이너리가 최신인지 확인하고, 필요하면 컴파일한다.

    Returns:
        바이너리 절대 경로

    Raises:
        FileNotFoundError: Swift 소스가 없는 경우
        RuntimeError: 컴파일 실패
    """
    if not os.path.exists(_SWIFT_SRC):
        raise FileNotFoundError(f"Swift 소스를 찾을 수 없습니다: {_SWIFT_SRC}")

    need_compile = False
    if not os.path.exists(_SWIFT_BIN):
        need_compile = True
    else:
        src_mtime = os.path.getmtime(_SWIFT_SRC)
        bin_mtime = os.path.getmtime(_SWIFT_BIN)
        if src_mtime > bin_mtime:
            need_compile = True

    if need_compile:
        logger.info("Swift 바이너리 컴파일 중: %s", _SWIFT_SRC)
        result = subprocess.run(
            [
                "swiftc", "-O",
                "-o", _SWIFT_BIN,
                _SWIFT_SRC,
                "-framework", "ScreenCaptureKit",
                "-framework", "CoreMedia",
                "-framework", "AVFoundation",
                "-framework", "Foundation",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Swift 컴파일 실패 (code {result.returncode}):\n{result.stderr}"
            )
        logger.info("Swift 바이너리 컴파일 완료")

    return _SWIFT_BIN


class SystemAudioCapture:
    """ScreenCaptureKit 시스템 오디오 캡처 + 에너지 기반 청크 분할.

    사용 예시::

        capture = SystemAudioCapture(config=config)
        capture.start(on_chunk_ready=lambda path: print(f"청크: {path}"))
        # ... 캡처 중 ...
        capture.stop()
    """

    def __init__(
        self,
        rate: int = RATE,
        channels: int = CHANNELS,
        chunk: int = CHUNK,
        config: Optional[dict] = None,
    ) -> None:
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
        self._process: Optional[subprocess.Popen] = None
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
            FileNotFoundError: Swift 소스가 없는 경우
            PermissionError: Screen Recording 권한이 거부된 경우
            OSError: 캡처 시작 실패
        """
        with self._lock:
            if self._capturing:
                raise RuntimeError("이미 캡처 중입니다")

            self._on_chunk_ready = on_chunk_ready

            # Swift 바이너리 확인/컴파일
            binary = _ensure_binary()

            # subprocess 시작
            try:
                self._process = subprocess.Popen(
                    [
                        binary,
                        "--sample-rate", str(self._rate),
                        "--channels", str(self._channels),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
            except Exception as e:
                raise OSError(f"오디오 캡처 프로세스를 시작할 수 없습니다: {e}") from e

            # 프로세스가 즉시 종료되었는지 확인 (권한 에러 등)
            try:
                retcode = self._process.wait(timeout=1.0)
                stderr_out = self._process.stderr.read().decode(errors="replace")
                self._process = None
                if retcode == 1:
                    raise PermissionError(
                        "Screen Recording 권한이 필요합니다. "
                        "시스템 설정 > 개인정보 보호 및 보안 > 화면 녹화에서 허용해주세요."
                    )
                raise OSError(f"오디오 캡처 실패 (code {retcode}): {stderr_out}")
            except subprocess.TimeoutExpired:
                # 정상 — 프로세스가 실행 중
                pass

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

        # Swift 프로세스 종료
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            try:
                self._process.wait(timeout=5.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        # 캡처 스레드 종료 대기
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        # 워커 스레드 종료 (None 센티넬)
        self._chunk_queue.put(None)
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=10.0)
            self._worker_thread = None

    def _capture_loop(self) -> None:
        """캡처 루프 (백그라운드 스레드).

        Swift 프로세스의 stdout에서 PCM 프레임을 읽으면서 에너지를 모니터링하고,
        무음 감지 또는 최대 길이 초과 시 청크를 분할한다.

        파이프에서 read()가 가변 크기를 반환하므로,
        내부 버퍼로 정확히 CHUNK 샘플 단위의 프레임을 조립한다.
        """
        frames: list[bytes] = []
        frame_count = 0
        silent_frames = 0
        has_audio = False  # 실제 소리가 있었는지 추적
        frame_bytes = self._chunk * SAMPLE_WIDTH * self._channels  # 한 프레임 바이트
        read_size = frame_bytes * 4  # 파이프에서 큰 단위로 읽기

        _buf = b""  # 내부 버퍼

        while self._capturing:
            # 버퍼에 한 프레임 이상 쌓일 때까지 읽기
            while len(_buf) < frame_bytes:
                try:
                    chunk = self._process.stdout.read(read_size)
                    if not chunk:
                        logger.info("오디오 캡처 프로세스 종료됨")
                        self._capturing = False
                        break
                except Exception:
                    logger.exception("오디오 스트림 읽기 실패, 캡처 중단")
                    self._capturing = False
                    break
                _buf += chunk

            if not self._capturing:
                break

            # 버퍼에서 정확히 frame_bytes 단위로 꺼내서 처리
            while len(_buf) >= frame_bytes and self._capturing:
                data = _buf[:frame_bytes]
                _buf = _buf[frame_bytes:]

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
                    if has_audio:
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
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(self._rate)
            wf.writeframes(b"".join(frames))
            wf.close()

            return path
        except Exception:
            logger.exception("WAV 파일 저장 실패")
            return None
