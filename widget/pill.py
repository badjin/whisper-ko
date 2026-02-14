"""받아쓰기 상태 표시 Pill 위젯.

레이아웃: [X 버튼] [이퀄라이저 바] [■ 상태 아이콘]

- listening: 바 정적 (흰색 짧은 바) — 대기 상태 (앱 시작 시 항상 표시)
- recording: 바 이퀄라이저 (오디오 레벨 반영), 오른쪽 핑크 ■ — 받아쓰기 녹음 중
- transcribing: 바 로딩 웨이브, 오른쪽 파란 ⋯ — Whisper 전사 중
- idle: 숨김

모든 메서드는 메인 스레드에서 호출해야 한다 (app._ui() 큐 통해).
set_audio_level()만 예외: 녹음 스레드에서 호출 가능 (float 대입은 원자적).
"""

from __future__ import annotations

import logging
import math

import objc
import AppKit
import Foundation
from Quartz import CGColorCreateGenericRGB

logger = logging.getLogger(__name__)

NSBackingStoreBuffered = 2

_NSNonactivatingPanel = getattr(
    AppKit, "NSWindowStyleMaskNonactivatingPanel",
    getattr(AppKit, "NSNonactivatingPanelMask", 1 << 7),
)

# ── 상태별 바 색상 ───────────────────────────────────────

BAR_COLORS = {
    "listening":    (0.85, 0.85, 0.85),    # 흰색 (정적 대기)
    "recording":    (0.2, 0.85, 0.4),       # 녹색 (이퀄라이저)
    "transcribing": (0.3, 0.55, 1.0),       # 파란
    "checking":     (0.85, 0.85, 0.85),     # 흰색
}

# ── 크기 상수 ─────────────────────────────────────────────

PILL_WIDTH = 220
PILL_HEIGHT = 50
BTN_SIZE = 34

# 이퀄라이저 영역 (X 버튼과 상태 버튼 사이)
EQ_LEFT = BTN_SIZE + 16       # X 버튼 오른쪽 여백
EQ_RIGHT = PILL_WIDTH - BTN_SIZE - 16  # 상태 버튼 왼쪽 여백
EQ_WIDTH = EQ_RIGHT - EQ_LEFT
EQ_TOP_PAD = 10               # 상하 패딩
EQ_BOT_PAD = 10

NUM_BARS = 12
BAR_W = 3                     # 바 너비
BAR_CORNER = 1.5              # 바 코너 반경
BAR_H_MIN = 3                 # 바 최소 높이 (정적)
BAR_H_MAX = PILL_HEIGHT - EQ_TOP_PAD - EQ_BOT_PAD  # 바 최대 높이

# ── 버튼 색상 ─────────────────────────────────────────────

CLOSE_BG = (0.40, 0.40, 0.40, 1.0)
CLOSE_FG = (1.0, 1.0, 1.0, 1.0)
STOP_BG = (0.85, 0.45, 0.50, 1.0)
STOP_FG = (1.0, 1.0, 1.0, 1.0)
TRANSCRIBE_BG = (0.3, 0.45, 0.75, 1.0)


# ── 이퀄라이저 커스텀 뷰 (Core Graphics 직접 드로잉) ─────

class _EqualizerView(AppKit.NSView):
    """Core Graphics로 이퀄라이저 바를 직접 그리는 커스텀 뷰.

    개별 NSView 대신 drawRect_에서 사각형을 직접 그려서
    작은 크기에서도 정확한 바 형태가 보장된다.
    """

    # PyObjC에서 인스턴스 변수 선언
    _bar_heights = objc.ivar()
    _bar_color = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(_EqualizerView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._bar_heights = [BAR_H_MIN] * NUM_BARS
        self._bar_color = (0.85, 0.85, 0.85, 1.0)
        return self

    def isFlipped(self):
        return False

    def drawRect_(self, rect):
        """Core Graphics로 이퀄라이저 바를 직접 그린다."""
        bounds = self.bounds()
        w = bounds.size.width
        h = bounds.size.height

        # 바 간격 계산: 전체 영역에 균등 배분
        total_bar_w = NUM_BARS * BAR_W
        total_gap = w - total_bar_w
        gap = total_gap / (NUM_BARS + 1) if NUM_BARS > 0 else 0

        r, g, b, a = self._bar_color
        color = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)
        color.setFill()

        for i in range(NUM_BARS):
            bar_h = self._bar_heights[i] if i < len(self._bar_heights) else BAR_H_MIN
            bar_x = gap + i * (BAR_W + gap)
            bar_y = (h - bar_h) / 2  # 수직 중앙 정렬

            bar_rect = Foundation.NSMakeRect(bar_x, bar_y, BAR_W, bar_h)
            path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, BAR_CORNER, BAR_CORNER,
            )
            path.fill()

    def setBarHeights_(self, heights):
        """바 높이 배열을 설정하고 다시 그린다."""
        self._bar_heights = list(heights)
        self.setNeedsDisplay_(True)

    def setBarColor_(self, color_tuple):
        """바 색상을 설정하고 다시 그린다."""
        self._bar_color = color_tuple
        self.setNeedsDisplay_(True)


# ── ObjC 헬퍼 ────────────────────────────────────────────

class _TimerTarget(AppKit.NSObject):
    _callback = None

    def initWithCallback_(self, callback):
        self = objc.super(_TimerTarget, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def fire_(self, timer):
        if self._callback is not None:
            self._callback()


class _ButtonTarget(AppKit.NSObject):
    _callback = None

    def initWithCallback_(self, callback):
        self = objc.super(_ButtonTarget, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def onClick_(self, sender):
        if self._callback is not None:
            self._callback()


class PillWidget:
    """받아쓰기 상태 표시 Pill 위젯."""

    def __init__(
        self,
        on_close: callable | None = None,
        on_stop: callable | None = None,
    ) -> None:
        self._state: str = "idle"
        self._audio_level: float = -60.0
        self._prev_heights: list[float] = [BAR_H_MIN] * NUM_BARS

        # 애니메이션
        self._anim_tick: int = 0
        self._anim_timer: Foundation.NSTimer | None = None
        self._timer_target = _TimerTarget.alloc().initWithCallback_(self._on_anim_tick)

        # 버튼 핸들러
        self._close_target = _ButtonTarget.alloc().initWithCallback_(on_close)
        self._stop_target = _ButtonTarget.alloc().initWithCallback_(on_stop)

        # UI 요소
        self._panel: AppKit.NSPanel | None = None
        self._close_btn: AppKit.NSButton | None = None
        self._status_btn: AppKit.NSButton | None = None
        self._eq_view: _EqualizerView | None = None

        self._create_panel()

    def _create_panel(self) -> None:
        screen = AppKit.NSScreen.mainScreen()
        if screen is None:
            return

        sf = screen.frame()
        x = sf.origin.x + (sf.size.width - PILL_WIDTH) / 2
        y = sf.origin.y + sf.size.height - 90

        self._panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            Foundation.NSMakeRect(x, y, PILL_WIDTH, PILL_HEIGHT),
            AppKit.NSWindowStyleMaskBorderless | _NSNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )

        self._panel.setLevel_(AppKit.NSStatusWindowLevel + 1)
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self._panel.setHasShadow_(True)
        self._panel.setIgnoresMouseEvents_(False)
        self._panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        self._panel.setHidesOnDeactivate_(False)

        content = self._panel.contentView()
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setCornerRadius_(PILL_HEIGHT / 2)
        layer.setMasksToBounds_(True)
        layer.setBackgroundColor_(CGColorCreateGenericRGB(0.08, 0.08, 0.08, 0.95))

        # ── X 버튼 (왼쪽) ────────────────────────────────
        btn_y = (PILL_HEIGHT - BTN_SIZE) / 2
        self._close_btn = self._make_circle_button(
            frame=Foundation.NSMakeRect(8, btn_y, BTN_SIZE, BTN_SIZE),
            title="\u2715",
            bg_color=CLOSE_BG,
            text_color=CLOSE_FG,
            font_size=16,
            target=self._close_target,
        )
        content.addSubview_(self._close_btn)

        # ── 이퀄라이저 (중앙) — Core Graphics 커스텀 뷰 ──
        eq_frame = Foundation.NSMakeRect(EQ_LEFT, 0, EQ_WIDTH, PILL_HEIGHT)
        self._eq_view = _EqualizerView.alloc().initWithFrame_(eq_frame)
        self._eq_view.setWantsLayer_(True)
        self._eq_view.layer().setBackgroundColor_(CGColorCreateGenericRGB(0, 0, 0, 0))
        content.addSubview_(self._eq_view)

        # ── 상태 아이콘 (오른쪽) — 핑크 원 + 흰색 ■ ─────
        self._status_btn = self._make_circle_button(
            frame=Foundation.NSMakeRect(PILL_WIDTH - BTN_SIZE - 8, btn_y, BTN_SIZE, BTN_SIZE),
            title="\u25A0",
            bg_color=STOP_BG,
            text_color=STOP_FG,
            font_size=14,
            target=self._stop_target,
        )
        content.addSubview_(self._status_btn)

        self._panel.setAlphaValue_(0.0)
        self._panel.orderOut_(None)

    def _make_circle_button(self, frame, title, bg_color, text_color, font_size, target):
        btn = AppKit.NSButton.alloc().initWithFrame_(frame)
        btn.setBezelStyle_(AppKit.NSBezelStyleCircular)
        btn.setBordered_(False)
        btn.setTitle_(title)
        btn.setFont_(AppKit.NSFont.systemFontOfSize_weight_(font_size, AppKit.NSFontWeightBold))
        btn.setWantsLayer_(True)
        btn.layer().setCornerRadius_(frame.size.width / 2)
        btn.layer().setBackgroundColor_(CGColorCreateGenericRGB(*bg_color))
        btn.setContentTintColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*text_color)
        )
        btn.setTarget_(target)
        btn.setAction_(b"onClick:")
        return btn

    # ══════════════════════════════════════════════════════
    # 공개 API
    # ══════════════════════════════════════════════════════

    def set_state(self, state: str) -> None:
        logger.info("pill set_state(%r)", state)
        if self._panel is None:
            return

        self._state = state
        self._stop_animation()

        if state == "idle":
            self._panel.setAlphaValue_(0.0)
            self._panel.orderOut_(None)
            return

        # 이퀄라이저 색상 + 정적 바
        r, g, b = BAR_COLORS.get(state, (0.85, 0.85, 0.85))
        self._eq_view.setBarColor_((r, g, b, 1.0))
        self._eq_view.setBarHeights_([BAR_H_MIN] * NUM_BARS)

        # 오른쪽 상태 아이콘 (상태별 구분)
        if state == "recording":
            # 녹음 중: 핑크 원 + 흰색 ■ (정지 버튼)
            self._status_btn.setTitle_("\u25A0")
            self._status_btn.layer().setBackgroundColor_(
                CGColorCreateGenericRGB(*STOP_BG))
        elif state == "transcribing":
            # 전사 중: 파란 원 + ⋯
            self._status_btn.setTitle_("\u22EF")
            self._status_btn.layer().setBackgroundColor_(
                CGColorCreateGenericRGB(*TRANSCRIBE_BG))
        else:
            # listening, checking: 어두운 원 + ⏸ (대기/일시정지 표시)
            self._status_btn.setTitle_("\u23F8")
            self._status_btn.layer().setBackgroundColor_(
                CGColorCreateGenericRGB(0.25, 0.25, 0.25, 1.0))

        self._panel.setAlphaValue_(1.0)
        self._panel.orderFrontRegardless()

        # 애니메이션
        if state == "recording":
            self._prev_heights = [BAR_H_MIN] * NUM_BARS
            self._start_animation(interval=0.025)  # 40fps
        elif state in ("checking", "transcribing"):
            self._start_animation(interval=0.05)

    def set_audio_level(self, db: float) -> None:
        """오디오 레벨 (녹음 스레드에서 호출 가능)."""
        self._audio_level = db

    def destroy(self) -> None:
        self._stop_animation()
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.close()
            self._panel = None

    # ══════════════════════════════════════════════════════
    # 내부
    # ══════════════════════════════════════════════════════

    def _start_animation(self, interval: float) -> None:
        self._anim_tick = 0
        self._anim_timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval, self._timer_target, b"fire:", None, True,
        )

    def _stop_animation(self) -> None:
        if self._anim_timer is not None:
            self._anim_timer.invalidate()
            self._anim_timer = None
        self._anim_tick = 0

    def _on_anim_tick(self) -> None:
        if self._panel is None or self._eq_view is None:
            return
        self._anim_tick += 1

        if self._state == "recording":
            self._animate_equalizer()
        elif self._state in ("checking", "transcribing"):
            self._animate_loading()

    def _animate_equalizer(self) -> None:
        """이퀄라이저: 바 높이가 실제 오디오 레벨에 직접 반응."""
        import random
        r, g, b = BAR_COLORS["recording"]

        # dB → 0~1 (-50dB=0, -5dB=1) — 더 넓은 감도 범위
        raw = max(0.0, min(1.0, (self._audio_level + 50) / 45))

        heights = []
        for i in range(NUM_BARS):
            # 각 바에 ±30% 랜덤 변동 → 자연스러운 이퀄라이저
            jitter = 0.7 + random.random() * 0.6
            target = BAR_H_MIN + (BAR_H_MAX - BAR_H_MIN) * raw * jitter
            target = max(BAR_H_MIN, min(BAR_H_MAX, target))

            # 빠른 상승, 느린 하강 (스무딩)
            prev = self._prev_heights[i] if i < len(self._prev_heights) else BAR_H_MIN
            if target > prev:
                h = prev + (target - prev) * 0.7   # 상승: 빠르게
            else:
                h = prev + (target - prev) * 0.3   # 하강: 부드럽게
            heights.append(max(BAR_H_MIN, h))

        self._prev_heights = heights
        self._eq_view.setBarColor_((r, g, b, 1.0))
        self._eq_view.setBarHeights_(heights)

    def _animate_loading(self) -> None:
        """로딩: 바가 좌→우로 순차 높아짐."""
        t = self._anim_tick * 0.05
        r, g, b = BAR_COLORS.get(self._state, (0.85, 0.85, 0.85))

        heights = []
        for i in range(NUM_BARS):
            phase = i / NUM_BARS
            wave = (t * 1.5 - phase) % 1.0
            intensity = math.exp(-((wave - 0.3) ** 2) / 0.02)
            h = BAR_H_MIN + (BAR_H_MAX * 0.6 - BAR_H_MIN) * intensity
            heights.append(max(BAR_H_MIN, h))

        self._eq_view.setBarColor_((r, g, b, 1.0))
        self._eq_view.setBarHeights_(heights)
