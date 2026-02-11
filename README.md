# 🎤 Whisper Ko

macOS menu bar app for **voice dictation** and **real-time translation** powered by [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) on Apple Silicon.

## Features

- **Dictation Mode**: Speak in Korean → text auto-pasted at cursor position
- **Translation Mode**: System audio (English) → real-time Korean subtitles on overlay
- **Apple Notes Integration**: Translation session summary saved to Notes on stop
- **Menu Bar App**: Runs quietly in your menu bar

## Requirements

- **Apple Silicon Mac** (M1/M2/M3/M4) — MLX requires Apple Silicon
- **macOS 13+** (Ventura or later)
- **BlackHole 2ch** — virtual audio device for system audio capture
- **Google Cloud Translation API key** — for translation mode

## Quick Install

```bash
curl -sSL https://raw.githubusercontent.com/mfgc-bc-team/whisper-ko/main/install.sh | bash
```

Or clone and install manually:

```bash
git clone https://github.com/mfgc-bc-team/whisper-ko.git ~/Applications/whisper-ko
cd ~/Applications/whisper-ko
chmod +x install.sh
./install.sh
```

## BlackHole Setup

Translation mode captures system audio through BlackHole. After installing BlackHole:

1. Open **Audio MIDI Setup** (Spotlight → "Audio MIDI Setup")
2. Click **+** at bottom left → **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and your **speakers/headphones**
4. Right-click the Multi-Output Device → **Use This Device For Sound Output**

This routes system audio to both your speakers and BlackHole simultaneously.

## Usage

| Hotkey | Action |
|--------|--------|
| `Ctrl+Shift+A` | Start/Stop Dictation |
| `Ctrl+Shift+S` | Start/Stop Translation |

### Dictation Mode
1. Press `Ctrl+Shift+A` to start recording
2. Speak in Korean
3. Press `Ctrl+Shift+A` again to stop
4. Text is automatically pasted at your cursor position

### Translation Mode
1. Play English audio/video
2. Press `Ctrl+Shift+S` to start translation
3. Korean subtitles appear on overlay (or pasted at cursor)
4. Press `Ctrl+Shift+S` to stop
5. Full transcript saved to Apple Notes automatically

### Translation Output Options
- **Overlay**: Korean-only subtitles at bottom of screen
- **Cursor**: `[HH:MM:SS] English\nKorean` pasted at cursor position

Both modes also log to `~/Documents/whisper-ko-logs/`.

## Configuration

Config file: `~/.config/whisper-ko/config.json`

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

## Troubleshooting

### Dictation text not pasting
Grant Accessibility permission: **System Settings → Privacy & Security → Accessibility** → add Python or the Whisper Ko app.

### Translation not capturing audio
Make sure your system sound output is set to the Multi-Output Device (not directly to speakers).

### App not launching
Check error log: `cat /tmp/whisper-ko-stderr.log`

## Tech Stack

- **UI**: [rumps](https://github.com/jaredks/rumps) (menu bar) + PyObjC (overlay)
- **Speech Recognition**: [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
- **Translation**: Google Cloud Translation API v2
- **Audio**: PyAudio + BlackHole 2ch
- **Hotkeys**: pynput

## License

MIT
