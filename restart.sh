#!/bin/bash
# Whisper Ko 앱 재시작
pkill -f "whisper-ko/app.py" 2>/dev/null
sleep 1
open "$(dirname "$0")/dist/Whisper Ko.app"
