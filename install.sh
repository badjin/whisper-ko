#!/bin/bash
set -e

# ─── Whisper Ko Installer ───────────────────────────────
# macOS menu bar app: Dictation + Real-time Translation
# Requires: Apple Silicon Mac (M1/M2/M3/M4)

APP_NAME="Whisper Ko"
INSTALL_DIR="$HOME/Applications/whisper-ko"
REPO_URL="https://github.com/badjin/whisper-ko.git"
MODEL="mlx-community/whisper-large-v3-turbo"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo "══════════════════════════════════════════"
echo "  🎤 Whisper Ko Installer"
echo "  Dictation + Real-time Translation"
echo "══════════════════════════════════════════"
echo ""

# ─── 1. Check Apple Silicon ─────────────────────────────
info "Checking system requirements..."

ARCH=$(uname -m)
if [ "$ARCH" != "arm64" ]; then
    error "Apple Silicon (M1/M2/M3/M4) is required. Detected: $ARCH"
fi
ok "Apple Silicon detected"

# ─── 2. Check/Install Homebrew ──────────────────────────
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv)"
fi
ok "Homebrew ready"

# ─── 3. Install system dependencies ────────────────────
info "Installing system dependencies..."

if ! brew list portaudio &>/dev/null; then
    info "Installing PortAudio..."
    brew install portaudio
fi
ok "PortAudio ready"

if ! brew list blackhole-2ch &>/dev/null; then
    info "Installing BlackHole 2ch (virtual audio device)..."
    brew install blackhole-2ch
    warn "BlackHole installed. You need to set up Multi-Output Device in Audio MIDI Setup."
    warn "See README for instructions."
fi
ok "BlackHole ready"

# ─── 4. Check Python 3.10+ ─────────────────────────────
PYTHON=""
for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$p" &>/dev/null; then
        ver=$("$p" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$p"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    info "Installing Python via Homebrew..."
    brew install python@3.13
    PYTHON="python3.13"
fi
ok "Python ready ($($PYTHON --version))"

# ─── 5. Clone or update repository ─────────────────────
if [ -d "$INSTALL_DIR" ]; then
    info "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull origin main 2>/dev/null || true
else
    info "Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
ok "Source code ready"

# ─── 6. Create virtual environment ─────────────────────
info "Setting up Python virtual environment..."
if [ ! -d ".venv" ]; then
    "$PYTHON" -m venv .venv
fi
source .venv/bin/activate

info "Installing Python packages (this may take a few minutes)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Python packages installed"

# ─── 7. Download Whisper model ──────────────────────────
info "Downloading Whisper model (~1.5GB, first time only)..."
python -c "
from huggingface_hub import snapshot_download
snapshot_download('$MODEL', local_files_only=False)
print('Model downloaded successfully')
" 2>/dev/null || info "Model will be downloaded on first use"
ok "Whisper model ready"

# ─── 8. Create .app bundle ─────────────────────────────
info "Creating app bundle..."

APP_DIR="$INSTALL_DIR/dist/$APP_NAME.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
mkdir -p "$MACOS_DIR"

# Launcher script
cat > "$MACOS_DIR/whisper-ko" << LAUNCHER
#!/bin/bash
PROJECT="$INSTALL_DIR"
VENV="\${PROJECT}/.venv/bin/python3"
cd "\${PROJECT}"
exec arch -arm64 "\${VENV}" "\${PROJECT}/app.py" 2>/tmp/whisper-ko-stderr.log
LAUNCHER
chmod +x "$MACOS_DIR/whisper-ko"

# App icon
RESOURCES_DIR="$APP_DIR/Contents/Resources"
mkdir -p "$RESOURCES_DIR"
ICON_SRC="$INSTALL_DIR/logo_192.png"
if [ -f "$ICON_SRC" ] && command -v iconutil &>/dev/null; then
    ICONSET=$(mktemp -d)/icon.iconset
    mkdir -p "$ICONSET"
    sips -z 16 16 "$ICON_SRC" --out "$ICONSET/icon_16x16.png" &>/dev/null
    sips -z 32 32 "$ICON_SRC" --out "$ICONSET/icon_16x16@2x.png" &>/dev/null
    sips -z 32 32 "$ICON_SRC" --out "$ICONSET/icon_32x32.png" &>/dev/null
    sips -z 64 64 "$ICON_SRC" --out "$ICONSET/icon_32x32@2x.png" &>/dev/null
    sips -z 128 128 "$ICON_SRC" --out "$ICONSET/icon_128x128.png" &>/dev/null
    cp "$ICON_SRC" "$ICONSET/icon_128x128@2x.png"
    iconutil -c icns "$ICONSET" -o "$RESOURCES_DIR/AppIcon.icns" 2>/dev/null
    rm -rf "$(dirname "$ICONSET")"
fi

# Info.plist
cat > "$APP_DIR/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Whisper Ko</string>
    <key>CFBundleDisplayName</key>
    <string>Whisper Ko</string>
    <key>CFBundleIdentifier</key>
    <string>com.jinkim.whisper-ko</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>whisper-ko</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>음성 인식을 위해 마이크 접근이 필요합니다.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>텍스트 붙여넣기를 위해 접근성 권한이 필요합니다.</string>
</dict>
</plist>
PLIST

# Ad-hoc codesign (마이크 권한 등 개인정보 설정에 필요)
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null
ok "App bundle created at: $APP_DIR"

# ─── 9. Create restart script ──────────────────────────
cat > "$INSTALL_DIR/restart.sh" << 'RESTART'
#!/bin/bash
pkill -f "whisper-ko/app.py" 2>/dev/null
sleep 1
open "$(dirname "$0")/dist/Whisper Ko.app"
RESTART
chmod +x "$INSTALL_DIR/restart.sh"

# ─── 10. Symlink to /Applications (optional) ───────────
APPLICATIONS_LINK="/Applications/$APP_NAME.app"
if [ ! -e "$APPLICATIONS_LINK" ]; then
    ln -sf "$APP_DIR" "$APPLICATIONS_LINK" 2>/dev/null && \
        ok "Added to /Applications" || \
        warn "Could not add to /Applications (run with sudo if needed)"
fi

# ─── 11. Google Translate API Key ───────────────────────
echo ""
echo "══════════════════════════════════════════"
echo "  Setup Complete!"
echo "══════════════════════════════════════════"
echo ""

CONFIG_FILE="$HOME/.config/whisper-ko/config.json"
if [ -f "$CONFIG_FILE" ]; then
    HAS_KEY=$(python -c "
import json
c = json.load(open('$CONFIG_FILE'))
print('yes' if c.get('google_translate_api_key') else 'no')
" 2>/dev/null)
fi

if [ "$HAS_KEY" != "yes" ]; then
    echo -e "${YELLOW}Google Translate API key is required for translation mode.${NC}"
    echo "You can set it later in the app menu: Settings > Google Translate API Key"
    echo ""
    read -p "Enter your Google Translate API key (or press Enter to skip): " API_KEY
    if [ -n "$API_KEY" ]; then
        mkdir -p "$(dirname "$CONFIG_FILE")"
        if [ -f "$CONFIG_FILE" ]; then
            python -c "
import json
c = json.load(open('$CONFIG_FILE'))
c['google_translate_api_key'] = '$API_KEY'
json.dump(c, open('$CONFIG_FILE', 'w'), indent=2, ensure_ascii=False)
"
        else
            cat > "$CONFIG_FILE" << CONF
{
  "google_translate_api_key": "$API_KEY"
}
CONF
        fi
        ok "API key saved"
    fi
fi

echo ""
echo "  🎤 Whisper Ko is ready!"
echo ""
echo "  Launch:  open \"$APP_DIR\""
echo "  Or find 'Whisper Ko' in /Applications"
echo ""
echo "  Hotkeys:"
echo "    Ctrl+Shift+A  →  Start/Stop Dictation"
echo "    Ctrl+Shift+S  →  Start/Stop Translation"
echo ""
echo "  ⚠️  First launch: Grant Accessibility permission"
echo "      System Settings > Privacy & Security > Accessibility"
echo ""

# Auto-launch
read -p "Launch Whisper Ko now? (y/n): " LAUNCH
if [ "$LAUNCH" = "y" ] || [ "$LAUNCH" = "Y" ]; then
    open "$APP_DIR"
fi
