# whisper-ko

macOS 메뉴바 음성인식 앱. 원본 `borinomi/mlx-whisper`를 확장하여 받아쓰기 + 실시간 번역 기능 제공.

## 프로젝트 개요

- **Mode 1 (받아쓰기)**: 마이크 → MLX Whisper → 한국어 텍스트 → 커서 위치에 Cmd+V 붙여넣기
- **Mode 2 (번역)**: 시스템 오디오(ScreenCaptureKit) → Whisper → Google Cloud Translate → `[timestamp] 영어 - 한글` 출력
- **Jarvis Mode (음성 트리거 받아쓰기)**: 웨이크 워드("자비스") → 녹음 → 종료 워드("끝") → 전사 → 붙여넣기

세 모드는 상호배제 (GPU 경합 방지). Jarvis와 PTT는 자동 전환 (PTT 사용 시 Jarvis 일시중지 → PTT 완료 후 복원).

## 프로젝트 구조

```
whisper-ko/
├── app.py                # 메인 rumps 앱, 모드 관리, UI 큐
├── config.py             # 설정 로드/저장 (~/.config/whisper-ko/config.json)
├── hotkeys.py            # 글로벌 핫키 매니저 (pynput)
├── jarvis.py             # Jarvis Mode 컨트롤러 (음성 트리거 상태 머신)
├── menu.py               # rumps 메뉴 빌더
├── audio/
│   ├── __init__.py
│   ├── devices.py        # PyAudio 디바이스 열거
│   ├── mic.py            # 마이크 녹음 (Mode 1)
│   ├── system.py         # ScreenCaptureKit 시스템 오디오 캡처 + 청크 분할 (Mode 2)
│   ├── vad.py            # 음성 활동 감지 (Jarvis Mode용)
│   └── sck_capture.swift # Swift CLI — ScreenCaptureKit → stdout PCM
├── install.sh            # 원클릭 설치 스크립트
├── transcribe.py         # MLX Whisper 래퍼
├── translate.py          # Google Cloud Translation API v2 (requests)
├── output/
│   ├── __init__.py
│   ├── clipboard.py      # 클립보드 복사 + Cmd+V 붙여넣기
│   ├── overlay.py        # PyObjC 자막 오버레이 창
│   └── logfile.py        # 타임스탬프 로그 파일
└── requirements.txt
```

## 기술 스택

- **UI**: rumps (macOS 메뉴바), PyObjC (오버레이 창)
- **오디오**: PyAudio (마이크), ScreenCaptureKit via Swift CLI (시스템 오디오)
- **음성인식**: mlx-whisper (Apple Silicon 최적화)
- **번역**: Google Cloud Translation API v2 (requests로 직접 호출)
- **핫키**: pynput (글로벌 키보드 리스너)
- **클립보드**: pyperclip + pyautogui (Cmd+V)

## 핵심 설계 결정

| 항목 | 결정 | 이유 |
|------|------|------|
| 시스템 오디오 | ScreenCaptureKit (Swift CLI) | BlackHole Multi-Output 문제 해결, PyObjC 바인딩 버그 우회 |
| 무음 감지 | RMS 에너지 기반 | 시스템 오디오에 음악/효과음 포함, webrtcvad보다 범용적 |
| 번역 API | requests + REST v2 | google-cloud-translate는 의존성 50+개, API키 인증엔 requests 충분 |
| 오버레이 | PyObjC (AppKit) | rumps가 이미 NSApplication 사용, 별도 이벤트루프 충돌 방지 |
| 스레딩 | 청크 → 단일 워커 스레드 | Whisper가 병목, 순차 처리가 GPU 안정적 |
| Jarvis VAD | RMS dB + SilenceDetector | 웨이크/종료 워드 감지용 세그먼트 분할, system.py와 동일 패턴 |
| Jarvis 스레딩 | Event.wait() 블로킹 | GPU 순차 접근 보장, PTT와 상호배제 |

## 설정 파일

경로: `~/.config/whisper-ko/config.json`

```json
{
  "dictation_hotkey": "ctrl+shift+a",
  "translation_hotkey": "ctrl+shift+s",
  "jarvis_hotkey": "ctrl+shift+j",
  "model": "mlx-community/whisper-large-v3-turbo",
  "translation_output": "overlay",
  "google_translate_api_key": "",
  "overlay": { "font_size": 28, "max_lines": 4, "fade_seconds": 10, "opacity": 0.85 },
  "audio": { "silence_threshold_db": -40, "silence_duration_sec": 0.8, "max_chunk_sec": 8 },
  "jarvis": {
    "wake_word": "자비스",
    "end_word": "끝",
    "silence_threshold_db": -40,
    "silence_duration_sec": 1.5,
    "end_silence_duration_sec": 2.0,
    "max_listen_sec": 8,
    "max_record_sec": 60
  },
  "log_dir": "~/Documents/whisper-ko-logs"
}
```

## 코드 컨벤션

- Python 3.10+
- 한글 주석 사용 (사용자 대상 한국어)
- 모든 UI 변경은 메인 스레드에서 실행 (rumps 제약)
- 백그라운드 스레드 → UI 큐 패턴: `self._ui(lambda: ...)` 사용
- 에러는 rumps.notification으로 사용자에게 알림
- config 변경 시 즉시 save_config() 호출

## 원본 참조

- `temp-analysis/app.py` - 원본 borinomi/mlx-whisper 앱 (받아쓰기 전용, 단일 파일)

## 설치 (install.sh)

`install.sh`는 다음을 순서대로 수행:

1. Apple Silicon 확인
2. Xcode Command Line Tools 확인 (없으면 설치 안내 후 종료)
3. Homebrew / PortAudio 설치
4. Python 3.10+ 확인/설치
5. Git clone 또는 pull
6. venv 생성 + `pip install -r requirements.txt`
7. Swift 오디오 캡처 바이너리 컴파일 (`audio/sck_capture`)
8. Whisper 모델 다운로드 (~1.5GB)
9. `.app` 번들 생성 (shell script launcher + Info.plist + codesign)
10. `/Applications` 심볼릭 링크
11. Google Translate API 키 입력 (선택)

## 실행

```bash
# .app 번들로 실행
open dist/Whisper\ Ko.app

# 또는 직접 실행 (개발 시)
python app.py
```

## 구현 Phase

1. **Phase 1**: 코어 + 받아쓰기 (config, audio/devices, audio/mic, transcribe, clipboard, hotkeys, menu, app)
2. **Phase 2**: 시스템 오디오 캡처 (audio/system - ScreenCaptureKit + 에너지 기반 청크 분할)
3. **Phase 3**: 번역 파이프라인 (translate, logfile, Mode 2 통합)
4. **Phase 4**: 오버레이 자막 (overlay, 출력 디스패처)
5. **Phase 5**: 마무리 (에러 핸들링, API 키 다이얼로그, 출력 전환 메뉴, 아이콘 상태)
