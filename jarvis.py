"""Jarvis Mode 컨트롤러.

음성 웨이크 워드("자비스")로 녹음을 시작하고,
종료 워드("끝")로 녹음을 멈추는 Voice-Triggered Dictation.
"""

from __future__ import annotations

import enum
import logging
import os
import re
import tempfile
import threading
import wave
from typing import Callable, Optional

import pyaudio

from audio.vad import SilenceDetector, compute_rms_db

logger = logging.getLogger(__name__)

# 오디오 포맷 상수
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024
SAMPLE_WIDTH = 2

# ── 퍼지 매칭 패턴 ───────────────────────────────────────

# "위스퍼" 변형: Whisper가 다양하게 인식할 수 있음
_WAKE_PATTERNS = [
    "위스퍼", "위스 퍼", "위스퍼!", "위스패", "위스파",
    "위스피", "위스포", "위쓰퍼", "위쓰패", "웨스퍼",
    "위스뻐", "위스퍼야", "위스펴", "위스프",
    "whisper", "wisper", "whispa", "wispah", "wesper",
]

# "완료" 변형
_END_PATTERNS = [
    "완료", "완료!", "완료.", "완뇨", "완노",
    "왈료", "완로", "환료",
    # Whisper 오인식 패턴
    "와이오", "양파", "완리오", "왈리오",
]


def _is_hallucination(text: str) -> bool:
    """Whisper 환각(반복 텍스트)을 감지한다.

    무음 구간에서 Whisper가 "자 자 자 자..." 같은 반복 텍스트를 생성하는 경우를 필터링.
    """
    text = text.strip()
    if not text:
        return True

    # 1) 같은 글자가 공백으로 반복: "자 자 자 자" → ["자","자","자","자"]
    tokens = text.split()
    if len(tokens) >= 3:
        unique = set(tokens)
        if len(unique) <= 2:
            return True

    # 2) 같은 문자만 반복: "자자자자자" or "아아아아"
    chars = set(text.replace(" ", "").replace(",", "").replace(".", ""))
    if len(chars) <= 2 and len(text.replace(" ", "")) >= 3:
        return True

    # 3) 빈 텍스트 (구두점만 있는 경우)
    cleaned = text.replace(" ", "").replace(".", "").replace(",", "")
    if len(cleaned) == 0:
        return True

    return False


def _fuzzy_match(text: str, word: str, patterns: list[str]) -> bool:
    """텍스트에 웨이크/종료 워드가 포함되어 있는지 퍼지 매칭."""
    text_lower = text.lower().strip()
    # 정확한 매칭
    if word in text_lower:
        return True
    # 패턴 매칭
    for pat in patterns:
        if pat in text_lower:
            return True
    # regex: "위스퍼" 변형
    if word == "위스퍼":
        # 한글: 위/웨 + 스/쓰 + 퍼/패/파/피
        if re.search(r"[위웨].{0,1}[스쓰].{0,1}[퍼패파피포프뻐]", text_lower):
            return True
        # 영어: whisper / wisper 변형
        if re.search(r"wh?is?[sp]\w{0,2}[eaio]r?", text_lower):
            return True
    return False


class JarvisState(enum.Enum):
    """Jarvis 모드 상태."""
    INACTIVE = "inactive"
    LISTENING = "listening"
    CHECKING = "checking"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class JarvisListener:
    """Jarvis Mode 리스너.

    단일 데몬 스레드에서 상태 머신을 구동한다.
    Whisper 결과는 threading.Event로 블로킹 대기하여 GPU 순차 접근을 보장한다.
    """

    def __init__(
        self,
        wake_word: str = "자비스",
        end_word: str = "끝",
        silence_threshold_db: float = -35,
        silence_duration_sec: float = 0.8,
        end_silence_duration_sec: float = 1.5,
        max_listen_sec: float = 5,
        max_record_sec: float = 60,
    ) -> None:
        self._wake_word = wake_word.lower()
        self._end_word = end_word.lower()
        self._silence_threshold_db = silence_threshold_db
        self._silence_duration_sec = silence_duration_sec
        self._end_silence_duration_sec = end_silence_duration_sec
        self._max_listen_sec = max_listen_sec
        self._max_record_sec = max_record_sec

        # 상태
        self._state = JarvisState.INACTIVE
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # PyAudio
        self._audio: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None

        # Whisper 결과 전달용
        self._whisper_event = threading.Event()
        self._whisper_result: str = ""

        # 취소 플래그
        self._cancel_requested = False

        # 콜백 (app.py에서 설정)
        self.on_state_change: Optional[Callable[[JarvisState], None]] = None
        self.on_transcribe_request: Optional[Callable[[str, bool], None]] = None
        self.on_audio_level: Optional[Callable[[float], None]] = None

    @property
    def state(self) -> JarvisState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state != JarvisState.INACTIVE

    def start(self) -> None:
        with self._lock:
            if self._state != JarvisState.INACTIVE:
                return
            self._stop_event.clear()
            self._set_state(JarvisState.LISTENING)

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="jarvis-listener"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._whisper_event.set()

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        self._set_state(JarvisState.INACTIVE)

    def cancel_recording(self) -> None:
        """녹음을 취소하고 LISTENING으로 복귀."""
        if self._state == JarvisState.RECORDING:
            self._cancel_requested = True

    # ── Whisper 결과 전달 (app.py에서 호출) ──────────────────

    def on_wake_check_result(self, text: str) -> None:
        self._whisper_result = text
        self._whisper_event.set()

    def on_transcribe_result(self, text: str) -> None:
        self._whisper_result = text
        self._whisper_event.set()

    # ── 내부 상태 관리 ──────────────────────────────────────

    def _set_state(self, state: JarvisState) -> None:
        logger.info("Jarvis 상태 전환: %s", state.value)
        self._state = state
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception:
                logger.exception("on_state_change 콜백 오류")

    # ── 메인 루프 ──────────────────────────────────────────

    def _run(self) -> None:
        try:
            self._open_mic()
            self._cancel_requested = False

            while not self._stop_event.is_set():
                state = self._state
                if state == JarvisState.LISTENING:
                    self._do_listening()
                elif state == JarvisState.CHECKING:
                    self._do_checking()
                elif state == JarvisState.RECORDING:
                    self._do_recording()
                elif state == JarvisState.TRANSCRIBING:
                    self._do_transcribing()
                else:
                    break

        except Exception:
            logger.exception("Jarvis 스레드 오류")
        finally:
            self._close_mic()

    # ── 마이크 관리 ────────────────────────────────────────

    def _open_mic(self) -> None:
        try:
            self._audio = pyaudio.PyAudio()
            self._stream = self._audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK,
            )
        except Exception:
            self._close_mic()
            raise

    def _close_mic(self) -> None:
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
        if self._audio is not None:
            try:
                self._audio.terminate()
            except Exception:
                pass
            self._audio = None

    def _flush_mic(self) -> None:
        """마이크 버퍼에 쌓인 오래된 데이터를 비운다."""
        if self._stream is None:
            return
        try:
            avail = self._stream.get_read_available()
            while avail > 0:
                self._stream.read(min(avail, CHUNK), exception_on_overflow=False)
                avail = self._stream.get_read_available()
        except Exception:
            pass

    # ── LISTENING: 웨이크 워드 대기 ───────────────────────

    def _do_listening(self) -> None:
        # 이전 상태(TRANSCRIBING 등)에서 쌓인 마이크 버퍼 비우기
        self._flush_mic()

        detector = SilenceDetector(
            silence_threshold_db=self._silence_threshold_db,
            silence_duration_sec=self._silence_duration_sec,
            max_segment_sec=self._max_listen_sec,
            rate=RATE,
            chunk=CHUNK,
        )

        while not self._stop_event.is_set() and self._state == JarvisState.LISTENING:
            try:
                data = self._stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                break

            if self.on_audio_level:
                try:
                    self.on_audio_level(compute_rms_db(data))
                except Exception:
                    pass

            if detector.feed(data):
                if detector.has_audio:
                    frames = detector.get_frames()
                    logger.debug("음성 세그먼트: %d프레임 (%.1f초)", len(frames), len(frames) * CHUNK / RATE)
                    wav_path = self._save_wav(frames)
                    if wav_path:
                        self._set_state(JarvisState.CHECKING)
                        self._pending_wav = wav_path
                        return
                else:
                    detector.reset()

    # ── CHECKING: 웨이크 워드 확인 ───────────────────────

    def _do_checking(self) -> None:
        wav_path = getattr(self, "_pending_wav", None)
        if not wav_path:
            self._set_state(JarvisState.LISTENING)
            return

        self._whisper_event.clear()
        self._whisper_result = ""

        if self.on_transcribe_request:
            self.on_transcribe_request(wav_path, True)
        else:
            self._set_state(JarvisState.LISTENING)
            return

        if not self._whisper_event.wait(timeout=30):
            logger.warning("웨이크 체크 타임아웃")
            self._set_state(JarvisState.LISTENING)
            return

        if self._stop_event.is_set():
            return

        text = self._whisper_result
        logger.info("웨이크 체크: %r", text)

        # 환각 필터: 반복 텍스트 무시
        if _is_hallucination(text):
            logger.debug("환각 텍스트 무시: %r", text)
            self._set_state(JarvisState.LISTENING)
            return

        if _fuzzy_match(text, self._wake_word, _WAKE_PATTERNS):
            logger.info(">>> 웨이크 워드 감지! 녹음 시작")
            self._set_state(JarvisState.RECORDING)
        else:
            self._set_state(JarvisState.LISTENING)

    # ── RECORDING: 받아쓰기 녹음 ────────────────────────

    def _do_recording(self) -> None:
        all_frames: list[bytes] = []
        self._cancel_requested = False

        end_detector = SilenceDetector(
            silence_threshold_db=self._silence_threshold_db,
            silence_duration_sec=self._end_silence_duration_sec,
            max_segment_sec=self._max_record_sec,
            rate=RATE,
            chunk=CHUNK,
        )

        max_frames = int(self._max_record_sec * RATE / CHUNK)

        # 자동 종료: 연속 무음 1.5초 감지
        auto_stop_sec = 1.5
        auto_stop_frames = int(auto_stop_sec * RATE / CHUNK)
        consecutive_silent = 0
        has_speech = False  # 한 번이라도 음성이 있었는지

        while not self._stop_event.is_set() and self._state == JarvisState.RECORDING:
            if self._cancel_requested:
                logger.info("Jarvis 녹음 취소됨")
                self._cancel_requested = False
                self._set_state(JarvisState.LISTENING)
                return

            try:
                data = self._stream.read(CHUNK, exception_on_overflow=False)
            except Exception:
                break

            all_frames.append(data)

            # 자동 종료: 음성이 있었고, 2초 연속 무음이면 자동 전사
            rms_db = compute_rms_db(data)
            if rms_db < self._silence_threshold_db:
                consecutive_silent += 1
            else:
                consecutive_silent = 0
                has_speech = True

            if has_speech and consecutive_silent >= auto_stop_frames:
                logger.info("자동 종료: %.1f초 무음 감지", auto_stop_sec)
                break

            if self.on_audio_level:
                try:
                    self.on_audio_level(compute_rms_db(data))
                except Exception:
                    pass

            if len(all_frames) >= max_frames:
                logger.info("최대 녹음 시간 초과")
                break

            if end_detector.feed(data):
                if end_detector.has_audio:
                    segment_frames = end_detector.get_frames()
                    wav_path = self._save_wav(segment_frames)
                    if wav_path:
                        self._whisper_event.clear()
                        self._whisper_result = ""
                        if self.on_transcribe_request:
                            self.on_transcribe_request(wav_path, True)
                        if not self._whisper_event.wait(timeout=30):
                            logger.warning("종료워드 체크 타임아웃")
                            end_detector.reset()
                            continue

                        if self._stop_event.is_set():
                            return

                        text = self._whisper_result
                        logger.info("종료워드 체크: %r", text)

                        # 환각 필터: 반복 텍스트 무시
                        if _is_hallucination(text):
                            logger.debug("환각 텍스트 무시 (녹음중): %r", text)
                            end_detector.reset()
                            continue

                        if _fuzzy_match(text, self._end_word, _END_PATTERNS):
                            logger.info(">>> 종료 워드 감지! 전사 시작")
                            break
                else:
                    end_detector.reset()

        if all_frames and not self._stop_event.is_set():
            wav_path = self._save_wav(all_frames)
            if wav_path:
                self._set_state(JarvisState.TRANSCRIBING)
                self._pending_full_wav = wav_path

    # ── TRANSCRIBING: 전체 전사 ─────────────────────────

    def _do_transcribing(self) -> None:
        wav_path = getattr(self, "_pending_full_wav", None)
        if not wav_path:
            self._set_state(JarvisState.LISTENING)
            return

        self._whisper_event.clear()
        self._whisper_result = ""

        if self.on_transcribe_request:
            self.on_transcribe_request(wav_path, False)
        else:
            self._set_state(JarvisState.LISTENING)
            return

        if not self._whisper_event.wait(timeout=60):
            logger.warning("전사 타임아웃")

        if self._stop_event.is_set():
            return

        self._set_state(JarvisState.LISTENING)

    # ── WAV 저장 ──────────────────────────────────────────

    def _save_wav(self, frames: list[bytes]) -> Optional[str]:
        if not frames:
            return None
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-")
            os.close(fd)
            wf = wave.open(path, "wb")
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(RATE)
            wf.writeframes(b"".join(frames))
            wf.close()
            return path
        except Exception:
            logger.exception("WAV 저장 실패")
            return None
