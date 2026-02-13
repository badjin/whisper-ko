"""rumps menu builder.

Mode 1 (Dictation) + Mode 2 (Translation) menu structure:
  🎤 (idle) / 🔴 (dictating) / 🔵 (translating) / ⏳ (processing)
  ├── Start Dictation (⌃⇧M)
  ├── ──────────
  ├── Start Translation (⌃⇧T)
  ├── ──────────
  ├── Translation Output
  │   ├── ✓ Overlay / Cursor
  ├── Settings
  │   ├── Hotkeys
  │   │   ├── Dictation: ...
  │   │   └── Translation: ...
  │   ├── Google Translate API Key
  │   ├── BlackHole Status
  └── Quit
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import rumps

from hotkeys import format_hotkey

if TYPE_CHECKING:
    from app import WhisperKoApp

# ── Hotkey presets ──────────────────────────────────────
# (hotkey_str, display_label)
DICTATION_HOTKEY_PRESETS: list[tuple[str, str]] = [
    ("ctrl+shift+a", "⌃⇧A"),
    ("ctrl+shift+m", "⌃⇧M"),
    ("cmd+shift+r", "⌘⇧R"),
    ("alt+space", "⌥Space"),
    ("ctrl+shift+space", "⌃⇧Space"),
]

TRANSLATION_HOTKEY_PRESETS: list[tuple[str, str]] = [
    ("ctrl+shift+s", "⌃⇧S"),
    ("ctrl+shift+t", "⌃⇧T"),
    ("cmd+shift+t", "⌘⇧T"),
    ("ctrl+alt+t", "⌃⌥T"),
    ("ctrl+shift+l", "⌃⇧L"),
]

# ── Translation output modes ───────────────────────────
# (mode_key, display_label)
TRANSLATION_OUTPUT_OPTIONS: list[tuple[str, str]] = [
    ("overlay", "Overlay"),
    ("cursor", "Cursor"),
]


def build_menu(app: WhisperKoApp) -> None:
    """Build the menu bar menu.

    Called whenever state changes to rebuild the menu.
    Must be called on the main thread (rumps constraint).
    """
    config = app.cfg
    menu = app.menu
    menu.clear()

    # ── Dictation toggle ────────────────────────────────
    dictation_hk = config.get("dictation_hotkey", "ctrl+shift+m")
    hk_display = format_hotkey(dictation_hk)

    if app.is_dictating:
        label = f"Stop Dictation ({hk_display})"
    else:
        label = f"Start Dictation ({hk_display})"

    dictation_item = rumps.MenuItem(label, callback=app.toggle_dictation)
    menu.add(dictation_item)

    menu.add(rumps.separator)

    # ── Translation toggle ──────────────────────────────
    translation_hk = config.get("translation_hotkey", "ctrl+shift+t")
    thk_display = format_hotkey(translation_hk)
    blackhole_available = getattr(app, "_blackhole_idx", None) is not None

    if not blackhole_available:
        tlabel = "Translation Unavailable (BlackHole required)"
        translation_item = rumps.MenuItem(tlabel)
    elif app.is_translating:
        tlabel = f"Stop Translation ({thk_display})"
        translation_item = rumps.MenuItem(tlabel, callback=app.toggle_translation)
    else:
        tlabel = f"Start Translation ({thk_display})"
        translation_item = rumps.MenuItem(tlabel, callback=app.toggle_translation)

    menu.add(translation_item)

    menu.add(rumps.separator)

    # ── Translation output submenu ──────────────────────
    output_submenu = rumps.MenuItem("Translation Output")
    current_output = config.get("translation_output", "overlay")

    for mode_key, mode_label in TRANSLATION_OUTPUT_OPTIONS:
        check = "\u2713 " if current_output == mode_key else "   "
        item = rumps.MenuItem(
            f"{check}{mode_label}",
            callback=lambda sender, m=mode_key: app.set_translation_output(m),
        )
        output_submenu.add(item)

    menu.add(output_submenu)

    # ── Settings submenu ────────────────────────────────
    settings_submenu = rumps.MenuItem("Settings")

    # Hotkeys (nested submenu)
    hotkey_submenu = rumps.MenuItem("Hotkeys")

    # Dictation hotkey
    dictation_hk_submenu = rumps.MenuItem("Dictation")
    for key, preset_label in DICTATION_HOTKEY_PRESETS:
        check = "\u2713 " if dictation_hk == key else "   "
        item = rumps.MenuItem(
            f"{check}{preset_label}",
            callback=lambda sender, k=key: app.set_dictation_hotkey(k),
        )
        dictation_hk_submenu.add(item)
    hotkey_submenu.add(dictation_hk_submenu)

    # Translation hotkey
    translation_hk_submenu = rumps.MenuItem("Translation")
    for key, preset_label in TRANSLATION_HOTKEY_PRESETS:
        check = "\u2713 " if translation_hk == key else "   "
        item = rumps.MenuItem(
            f"{check}{preset_label}",
            callback=lambda sender, k=key: app.set_translation_hotkey(k),
        )
        translation_hk_submenu.add(item)
    hotkey_submenu.add(translation_hk_submenu)

    settings_submenu.add(hotkey_submenu)

    # Google Translate API Key
    api_key = config.get("google_translate_api_key", "")
    api_status = "Set" if api_key else "Not Set"
    api_item = rumps.MenuItem(
        f"Google Translate API Key ({api_status})",
        callback=app.show_api_key_dialog,
    )
    settings_submenu.add(api_item)

    # BlackHole status
    if blackhole_available:
        bh_item = rumps.MenuItem("BlackHole: Detected")
    else:
        bh_item = rumps.MenuItem(
            "BlackHole: Not Found (brew install blackhole-2ch)"
        )
    settings_submenu.add(bh_item)

    menu.add(settings_submenu)

    menu.add(rumps.separator)

    # ── Restart / Quit ────────────────────────────────────
    menu.add(rumps.MenuItem("Restart", callback=app.restart_app))
    menu.add(rumps.MenuItem("Quit", callback=app.quit_app))
