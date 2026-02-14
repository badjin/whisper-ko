"""whisper-ko 메인 rumps 앱.

macOS 메뉴바 음성인식 앱의 진입점.
Mode 1 (받아쓰기): 마이크 → Whisper → 텍스트 → Cmd+V 붙여넣기
Mode 2 (번역): 시스템 오디오(ScreenCaptureKit) → Whisper → Google Translate → 출력
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import traceback

import AppKit
import rumps

# ── Dock 아이콘 숨기기 ──────────────────────────────────────────
# .app 번들의 LSUIElement 대신 Python 프로세스 자체에서 설정
# (exec으로 Python에 교체되면 .app의 Info.plist가 적용되지 않음)
AppKit.NSApplication.sharedApplication().setActivationPolicy_(
    AppKit.NSApplicationActivationPolicyAccessory  # 1 = Dock 아이콘 없음
)

from config import load_config, save_config
from audio.mic import MicRecorder
from audio.system import SystemAudioCapture
from transcribe import transcribe, preload_model
from translate import translate_text
from output.clipboard import copy_and_paste, paste_and_enter
from output.logfile import TranslationLogger
from output.overlay import SubtitleOverlay
from hotkeys import HotkeyManager, format_hotkey
from menu import build_menu
from widget.pill import PillWidget

logger = logging.getLogger(__name__)

# ── 아이콘 상수 ──────────────────────────────────────────────

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_192.png")
ICON_IDLE = ""
ICON_TRANSLATING = "🔵"


class WhisperKoApp(rumps.App):
    """whisper-ko 메뉴바 앱.

    Mode 1 (받아쓰기) 플로우:
        핫키 → toggle_dictation → start/stop →
        MicRecorder → WAV → transcribe → copy_and_paste

    Mode 2 (번역) 플로우:
        핫키 → toggle_translation → start/stop →
        SystemAudioCapture → 청크 WAV → transcribe → translate →
        출력 디스패처 (overlay / cursor / logfile / all)

    두 모드는 상호배제 (GPU 경합 방지).
    모든 UI 변경은 메인 스레드의 UI 큐를 통해 실행한다.
    """

    def __init__(self) -> None:
        super().__init__("Whisper Ko", icon=ICON_PATH, quit_button=None)
        self.title = ICON_IDLE

        # ── 설정 로드 ────────────────────────────────────
        self.cfg: dict = load_config()

        # ── Whisper 모델 프리로드 (백그라운드) ──────────
        preload_model(self.cfg.get("model", "mlx-community/whisper-large-v3-turbo"))

        # ── 상태 ─────────────────────────────────────────
        self.is_dictating: bool = False
        self.is_translating: bool = False
        self._last_translation: str = ""  # 중복 감지용
        self._translation_pairs: list[tuple[str, str]] = []  # 세션 누적 (Notes용)

        # ── Pill 위젯 (받아쓰기 상태 표시) ─────────────
        self._pill = PillWidget(
            on_close=None,
            on_stop=lambda: self._ui(self._stop_dictation),
        )

        # ── 오디오 (Mode 1: 마이크) ──────────────────────
        self._recorder = MicRecorder(
            on_audio_level=lambda db: self._pill.set_audio_level(db),
        )

        # ── 오디오 (Mode 2: 시스템 오디오) ────────────────
        self._sys_capture = SystemAudioCapture(config=self.cfg)

        # ── 번역 출력 모듈 ───────────────────────────────
        self._overlay = SubtitleOverlay(self.cfg.get("overlay", {}))
        self._translation_logger = TranslationLogger(
            self.cfg.get("log_dir", "~/Documents/whisper-ko-logs")
        )

        # ── UI 작업 큐 (메인 스레드에서만 UI 변경) ──────
        self._uiq: queue.Queue[callable] = queue.Queue()

        # ── 핫키 이벤트 (pynput 스레드 → Event → 메인 타이머) ──
        self._dictation_start_event = threading.Event()
        self._dictation_stop_event = threading.Event()
        self._translation_event = threading.Event()

        # ── 타이머: UI 큐 drain + 이벤트 처리 (50ms) ────
        self._ui_timer = rumps.Timer(self._drain_mainloop, 0.05)
        self._ui_timer.start()

        # ── 핫키 매니저 ──────────────────────────────────
        self._hotkey_mgr = HotkeyManager()
        self._register_hotkeys()
        self._hotkey_mgr.start()

        # ── 메뉴 구성 ───────────────────────────────────
        build_menu(self)

        # ── Pill 위젯 표시 (앱 시작 시 항상 보임) ────────
        self._pill.position_near_focused_input()
        self._pill.set_state("listening")

    # ══════════════════════════════════════════════════════
    # UI 큐 (메인 스레드 전용)
    # ══════════════════════════════════════════════════════

    def _ui(self, fn: callable) -> None:
        """메인 루프에서 실행할 UI 작업을 큐에 등록한다."""
        self._uiq.put(fn)

    def _notify(self, title: str, subtitle: str, message: str) -> None:
        """rumps.notification을 메인 루프에서 안전하게 실행한다."""
        def _do():
            try:
                rumps.notification(title, subtitle, message)
            except Exception:
                pass
        self._ui(_do)

    def _drain_mainloop(self, _) -> None:
        """50ms마다 호출: 핫키 이벤트 처리 + UI 큐 drain."""
        # 1) 핫키 이벤트 처리 (받아쓰기: push-to-talk)
        if self._dictation_start_event.is_set():
            self._dictation_start_event.clear()
            if not self.is_dictating:
                self._start_dictation()

        if self._dictation_stop_event.is_set():
            self._dictation_stop_event.clear()
            if self.is_dictating:
                self._stop_dictation()

        if self._translation_event.is_set():
            self._translation_event.clear()
            self.toggle_translation(None)

        # 2) UI 큐 drain (한 tick에 최대 50개)
        for _ in range(50):
            try:
                fn = self._uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                traceback.print_exc()

    # ══════════════════════════════════════════════════════
    # 핫키 등록
    # ══════════════════════════════════════════════════════

    def _register_hotkeys(self) -> None:
        """설정에 따라 핫키를 등록한다."""
        dictation_hk = self.cfg.get("dictation_hotkey", "ctrl+shift+a")
        self._hotkey_mgr.register(
            dictation_hk,
            self._dictation_start_event.set,
            on_release=self._dictation_stop_event.set,
        )

        translation_hk = self.cfg.get("translation_hotkey", "ctrl+shift+t")
        self._hotkey_mgr.register(translation_hk, self._translation_event.set)

    def _rebind_hotkeys(self) -> None:
        """핫키를 재등록한다 (단축키 변경 시)."""
        self._hotkey_mgr.stop()
        self._hotkey_mgr = HotkeyManager()
        self._register_hotkeys()
        self._hotkey_mgr.start()

    # ══════════════════════════════════════════════════════
    # Mode 1: 받아쓰기
    # ══════════════════════════════════════════════════════

    def toggle_dictation(self, sender) -> None:
        """받아쓰기 시작/중지 토글 (메뉴 콜백 + 핫키 이벤트에서 호출)."""
        if self.is_dictating:
            self._stop_dictation()
        else:
            self._start_dictation()

    def _start_dictation(self) -> None:
        """마이크 녹음을 시작한다. 번역 중이면 먼저 중지한다."""
        if self.is_dictating:
            return

        # 모드 상호배제: 번역 중이면 중지
        if self.is_translating:
            self._stop_translation()

        try:
            self._recorder.start()
        except OSError as e:
            logger.error("마이크 오류: %s", e)
            return
        except Exception as e:
            logger.error("오디오 오류: %s", e)
            return

        self.is_dictating = True
        self._pill.position_near_focused_input()
        self._pill.set_state("recording")
        build_menu(self)

    def _stop_dictation(self) -> None:
        """녹음을 중지하고 백그라운드에서 전사를 시작한다."""
        if not self.is_dictating:
            return

        self.is_dictating = False
        self._pill.set_state("transcribing")
        build_menu(self)

        # MicRecorder.stop()은 스레드 join + WAV 저장까지 수행
        wav_path = self._recorder.stop()

        if not wav_path:
            self._pill.set_state("listening")
            build_menu(self)
            return

        # 전사는 백그라운드에서 실행 (Whisper가 병목)
        threading.Thread(
            target=self._transcribe_and_paste,
            args=(wav_path,),
            daemon=True,
        ).start()

    def _transcribe_and_paste(self, wav_path: str) -> None:
        """전사 및 붙여넣기 (백그라운드 스레드).

        완료 후 UI 복귀와 임시 파일 정리를 수행한다.
        """
        try:
            model = self.cfg.get("model", "mlx-community/whisper-large-v3-turbo")
            result = transcribe(wav_path, model=model, language="ko")
            text = result.get("text", "")

            if text:
                # modifier 키 릴리즈 + 명시적 keyDown/keyUp으로 Cmd+V 수행
                # (push-to-talk 핫키 modifier 잔류 간섭 방지)
                self._ui(lambda: paste_and_enter(text))
            else:
                logger.info("인식된 텍스트가 없습니다.")

        except Exception as e:
            logger.exception("전사 오류: %s", e)

        finally:
            # 임시 WAV 파일 삭제
            try:
                os.unlink(wav_path)
            except Exception:
                pass

            # UI 복귀: pill → listening (대기)
            def _restore():
                if not self.is_translating and not self.is_dictating:
                    self._pill.set_state("listening")
                    build_menu(self)
            self._ui(_restore)

    # ══════════════════════════════════════════════════════
    # Mode 2: 번역
    # ══════════════════════════════════════════════════════

    def toggle_translation(self, sender) -> None:
        """번역 시작/중지 토글 (메뉴 콜백 + 핫키 이벤트에서 호출)."""
        if self.is_translating:
            self._stop_translation()
        else:
            self._start_translation()

    def _start_translation(self) -> None:
        """시스템 오디오 캡처를 시작하여 실시간 번역을 시작한다."""
        if self.is_translating:
            return

        # 모드 상호배제: 받아쓰기 중이면 중지
        if self.is_dictating:
            self._stop_dictation()

        # API 키 확인 — 없으면 설정 다이얼로그 자동 표시
        api_key = self.cfg.get("google_translate_api_key", "")
        if not api_key:
            self.show_api_key_dialog(None)
            api_key = self.cfg.get("google_translate_api_key", "")
            if not api_key:
                return

        # 세션 초기화
        self._translation_pairs.clear()
        self._last_translation = ""

        try:
            self._sys_capture.start(on_chunk_ready=self._on_chunk)
        except PermissionError as e:
            logger.error("권한 오류: %s", e)
            self._notify(
                "Whisper Ko",
                "화면 녹화 권한 필요",
                "시스템 설정 > 개인정보 보호 및 보안 > 화면 녹화에서 허용해주세요.",
            )
            # System Settings 열기
            try:
                subprocess.Popen([
                    "open",
                    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
                ])
            except Exception:
                pass
            return
        except Exception as e:
            logger.error("오디오 오류: %s", e)
            return

        self.is_translating = True
        self.title = ICON_TRANSLATING
        build_menu(self)

    def _stop_translation(self) -> None:
        """시스템 오디오 캡처를 중지하고 결과를 Notes에 저장한다."""
        if not self.is_translating:
            return

        self.is_translating = False

        if self._sys_capture is not None:
            try:
                self._sys_capture.stop()
            except Exception:
                logger.exception("시스템 오디오 캡처 중지 실패")

        # 오버레이 모드면 숨기기
        if self.cfg.get("translation_output", "overlay") == "overlay":
            self._ui(lambda: self._overlay.clear())

        # Notes에 세션 결과 저장 (백그라운드)
        if self._translation_pairs:
            pairs = list(self._translation_pairs)
            self._translation_pairs.clear()
            threading.Thread(
                target=self._create_notes_summary,
                args=(pairs,),
                daemon=True,
            ).start()

        self.title = ICON_IDLE
        build_menu(self)

    def _on_chunk(self, wav_path: str) -> None:
        """시스템 오디오 청크 콜백 (백그라운드 스레드에서 호출).

        전사 → 번역 → 오버레이(한글) + 로그(영어) + 세션 누적.
        """
        self._ui(lambda: setattr(self, "title", ICON_TRANSLATING))

        try:
            model = self.cfg.get("model", "mlx-community/whisper-large-v3-turbo")
            result = transcribe(wav_path, model=model, language=None)
            original = result.get("text", "").strip()

            if not original:
                return

            # 중복 텍스트 감지 (Whisper hallucination 방지)
            if original == self._last_translation:
                return
            self._last_translation = original

            # 번역
            api_key = self.cfg.get("google_translate_api_key", "")
            translated = translate_text(original, target="ko", api_key=api_key)

            if translated.startswith("[번역 오류"):
                logger.warning("번역 실패: %s", translated)
                return

            # 출력 모드에 따라 실시간 표시
            output_mode = self.cfg.get("translation_output", "overlay")

            if output_mode == "overlay":
                # 오버레이: 한글만 실시간 표시 (메인 스레드)
                self._ui(lambda: self._overlay.show(original, translated))
            else:
                # 커서 위치: [HH:MM:SS] 영어\n한글\n\n 붙여넣기
                from datetime import datetime
                ts = datetime.now().strftime("[%H:%M:%S]")
                text = f"{ts} {original}\n{translated}\n\n"
                self._ui(lambda: copy_and_paste(text))

            # 로그: 영어 원문 + 한글 번역 기록 (항상)
            self._translation_logger.log(original, translated)

            # 세션 누적 (종료 시 Notes에 기록)
            self._translation_pairs.append((original, translated))

        except Exception:
            logger.exception("번역 청크 처리 실패")

        finally:
            try:
                os.unlink(wav_path)
            except Exception:
                pass

            if self.is_translating:
                self._ui(lambda: setattr(self, "title", ICON_TRANSLATING))
            else:
                self._ui(lambda: setattr(self, "title", ICON_IDLE))

    # ══════════════════════════════════════════════════════
    # Notes 세션 요약
    # ══════════════════════════════════════════════════════

    def _create_notes_summary(self, pairs: list[tuple[str, str]]) -> None:
        """번역 세션 결과를 Apple Notes에 새 노트로 생성한다."""
        import html as html_mod
        import subprocess
        import tempfile
        from datetime import datetime

        title = f"Whisper Ko - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        # HTML 본문 구성: 영어 → 한글 번역 쌍
        body_parts = []
        for original, translated in pairs:
            orig_safe = html_mod.escape(original)
            trans_safe = html_mod.escape(translated)
            body_parts.append(
                f"{orig_safe}<br><b>{trans_safe}</b><br><br>"
            )

        body_html = "\n".join(body_parts)

        # 임시 파일에 HTML 작성
        fd, tmp_path = tempfile.mkstemp(
            suffix=".html", prefix="whisper-ko-note-"
        )
        os.close(fd)

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(body_html)

            title_escaped = title.replace('\\', '\\\\').replace('"', '\\"')
            script = (
                f'set noteBody to do shell script "cat " '
                f'& quoted form of "{tmp_path}"\n'
                f'tell application "Notes"\n'
                f'    make new note with properties '
                f'{{name:"{title_escaped}", body:noteBody}}\n'
                f'    activate\n'
                f'end tell'
            )
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=15,
            )
        except Exception:
            logger.exception("Apple Notes 노트 생성 실패")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ══════════════════════════════════════════════════════
    # 설정 변경 (메뉴 콜백)
    # ══════════════════════════════════════════════════════

    def set_dictation_hotkey(self, hotkey: str) -> None:
        """받아쓰기 단축키를 변경하고 저장한다."""
        self.cfg["dictation_hotkey"] = hotkey
        save_config(self.cfg)
        self._rebind_hotkeys()
        build_menu(self)

    def set_translation_hotkey(self, hotkey: str) -> None:
        """번역 단축키를 변경하고 저장한다."""
        self.cfg["translation_hotkey"] = hotkey
        save_config(self.cfg)
        self._rebind_hotkeys()
        build_menu(self)

    def set_translation_output(self, mode: str) -> None:
        """번역 출력 대상을 변경하고 저장한다.

        Args:
            mode: "overlay", "cursor", "logfile", "all" 중 하나.
        """
        self.cfg["translation_output"] = mode
        save_config(self.cfg)
        build_menu(self)

    def set_api_key(self, api_key: str) -> None:
        """Google 번역 API 키를 설정하고 저장한다."""
        self.cfg["google_translate_api_key"] = api_key
        save_config(self.cfg)
        build_menu(self)

    def show_api_key_dialog(self, sender) -> None:
        """API 키 입력 다이얼로그를 표시한다.

        rumps.Window를 사용하여 텍스트 입력을 받는다.
        """
        current_key = self.cfg.get("google_translate_api_key", "")
        masked = current_key[:8] + "..." if len(current_key) > 8 else current_key

        window = rumps.Window(
            title="Google 번역 API 키",
            message=f"현재: {masked}" if current_key else "API 키를 입력하세요.",
            default_text=current_key,
            ok="저장",
            cancel="취소",
            dimensions=(320, 24),
        )
        resp = window.run()
        if resp.clicked:
            new_key = resp.text.strip()
            if new_key:
                self.set_api_key(new_key)

    # ══════════════════════════════════════════════════════
    # 종료
    # ══════════════════════════════════════════════════════

    def restart_app(self, sender) -> None:
        """앱을 재시작한다. 현재 프로세스를 종료하고 동일 명령으로 재실행."""
        # 현재 실행 명령어 보존
        exe = sys.executable
        args = sys.argv

        # 리소스 정리 (quit_app과 동일)
        try:
            self._hotkey_mgr.stop()
        except Exception:
            pass
        try:
            self._ui_timer.stop()
        except Exception:
            pass
        try:
            if self._recorder.is_recording:
                self._recorder.stop()
        except Exception:
            pass
        try:
            if self._sys_capture and self._sys_capture.is_capturing:
                self._sys_capture.stop()
        except Exception:
            pass
        try:
            self._pill.destroy()
        except Exception:
            pass
        try:
            self._overlay.destroy()
        except Exception:
            pass

        rumps.quit_application()

        # 새 프로세스로 재시작 후 현재 프로세스 종료
        subprocess.Popen([exe] + args)
        os._exit(0)

    def quit_app(self, sender) -> None:
        """앱을 안전하게 종료한다."""
        # 핫키 리스너 중지
        try:
            self._hotkey_mgr.stop()
        except Exception:
            pass

        # 타이머 중지
        try:
            self._ui_timer.stop()
        except Exception:
            pass

        # 녹음 중이면 중지
        try:
            if self._recorder.is_recording:
                self._recorder.stop()
        except Exception:
            pass

        # 번역 캡처 중이면 중지
        try:
            if self._sys_capture and self._sys_capture.is_capturing:
                self._sys_capture.stop()
        except Exception:
            pass

        # Pill 위젯 정리
        try:
            self._pill.destroy()
        except Exception:
            pass

        # 오버레이 정리
        try:
            self._overlay.destroy()
        except Exception:
            pass

        rumps.quit_application()

        # rumps.quit_application() 이후에도 pynput 리스너 등
        # non-daemon 스레드가 남아 프로세스가 종료되지 않는 문제 방지
        os._exit(0)


# ── 엔트리 포인트 ────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    WhisperKoApp().run()
