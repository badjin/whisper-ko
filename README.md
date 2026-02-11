# 🎤 Whisper Ko

Apple Silicon Mac 전용 **음성 받아쓰기** + **실시간 영한 번역** 메뉴바 앱.

[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)를 사용하여 로컬에서 음성인식을 수행하며, 외부 서버에 음성 데이터를 전송하지 않습니다.

## 주요 기능

- **받아쓰기 모드**: 한국어로 말하면 → 커서 위치에 자동 붙여넣기
- **번역 모드**: 시스템 오디오(영어) → 실시간 한국어 자막 오버레이
- **Apple Notes 연동**: 번역 종료 시 영어/한글 정리 노트 자동 생성
- **메뉴바 앱**: 백그라운드에서 조용히 실행

## 필수 조건

- **Apple Silicon Mac** (M1/M2/M3/M4) — MLX는 Apple Silicon만 지원
- **macOS 13+** (Ventura 이상)
- **BlackHole 2ch** — 시스템 오디오 캡처용 가상 오디오 디바이스
- **Google Cloud Translation API 키** — 번역 모드에 필요

## 설치

터미널에서 한 줄로 설치:

```bash
curl -sSL https://raw.githubusercontent.com/badjin/whisper-ko/main/install.sh | bash
```

또는 수동 설치:

```bash
git clone https://github.com/badjin/whisper-ko.git ~/Applications/whisper-ko
cd ~/Applications/whisper-ko
chmod +x install.sh
./install.sh
```

설치 스크립트가 자동으로 처리하는 항목:
- Homebrew / Python / PortAudio / BlackHole 설치 확인
- Python 가상환경 생성 및 패키지 설치
- Whisper 모델 다운로드 (~1.5GB, 최초 1회)
- .app 번들 생성 및 /Applications 등록
- Google Translate API 키 입력 안내

## BlackHole 설정 (번역 모드용)

번역 모드는 BlackHole을 통해 시스템 오디오를 캡처합니다. 설치 후 아래 설정이 필요합니다:

1. **Audio MIDI Setup** 열기 (Spotlight → "Audio MIDI Setup" 검색)
2. 좌측 하단 **+** 클릭 → **다중 출력 기기 생성**
3. **BlackHole 2ch**와 사용 중인 **스피커/이어폰** 모두 체크
4. 다중 출력 기기를 우클릭 → **이 기기를 사운드 출력에 사용**

이렇게 하면 시스템 오디오가 스피커와 BlackHole에 동시에 전달됩니다.

## 사용법

| 단축키 | 동작 |
|--------|------|
| `Ctrl+Shift+A` | 받아쓰기 시작/중지 |
| `Ctrl+Shift+S` | 번역 시작/중지 |

### 받아쓰기 모드
1. `Ctrl+Shift+A`를 눌러 녹음 시작
2. 한국어로 말하기
3. `Ctrl+Shift+A`를 다시 눌러 녹음 중지
4. 인식된 텍스트가 커서 위치에 자동 붙여넣기

### 번역 모드
1. 영어 영상/오디오 재생
2. `Ctrl+Shift+S`를 눌러 번역 시작
3. 화면 하단에 한국어 자막 오버레이 표시 (또는 커서 위치에 붙여넣기)
4. `Ctrl+Shift+S`를 다시 눌러 번역 중지
5. Apple Notes에 영어/한글 정리 노트 자동 생성

### 번역 출력 옵션
메뉴바 → **Translation Output**에서 선택:
- **Overlay**: 화면 하단에 한국어 자막만 표시
- **Cursor**: `[HH:MM:SS] English\nKorean` 형식으로 커서 위치에 붙여넣기

두 옵션 모두 `~/Documents/whisper-ko-logs/`에 로그가 자동 기록됩니다.

## 설정

설정 파일: `~/.config/whisper-ko/config.json`

```json
{
  "dictation_hotkey": "ctrl+shift+a",
  "translation_hotkey": "ctrl+shift+s",
  "model": "mlx-community/whisper-large-v3-turbo",
  "translation_output": "overlay",
  "google_translate_api_key": "YOUR_API_KEY",
  "overlay": {
    "font_size": 28,
    "max_lines": 4,
    "fade_seconds": 10,
    "opacity": 0.85
  },
  "audio": {
    "blackhole_device_name": "BlackHole 2ch",
    "silence_threshold_db": -40,
    "silence_duration_sec": 0.8,
    "max_chunk_sec": 8
  }
}
```

## 문제 해결

### 받아쓰기 후 텍스트가 붙여넣기 안 됨
손쉬운 사용 권한을 부여해야 합니다: **시스템 설정 → 개인정보 보호 및 보안 → 손쉬운 사용** → Python 또는 Whisper Ko 앱 추가

### 번역 모드에서 오디오가 캡처 안 됨
시스템 사운드 출력이 **다중 출력 기기**로 설정되어 있는지 확인하세요 (스피커 직접 출력이 아닌).

### 앱이 실행되지 않음
에러 로그 확인: `cat /tmp/whisper-ko-stderr.log`

### 첫 실행 시 오래 걸림
Whisper 모델(~1.5GB)을 처음 로드하는 데 시간이 걸릴 수 있습니다. 두 번째 실행부터는 빠릅니다.

## 기술 스택

- **UI**: [rumps](https://github.com/jaredks/rumps) (메뉴바) + PyObjC (오버레이)
- **음성인식**: [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) (Apple Silicon 최적화)
- **번역**: Google Cloud Translation API v2
- **오디오**: PyAudio + BlackHole 2ch
- **단축키**: pynput

## 라이선스

MIT
