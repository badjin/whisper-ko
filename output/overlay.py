"""PyObjC 자막 오버레이 창.

Mode 2 (번역)에서 원문과 번역을 화면 하단 중앙에 표시한다.
rumps의 NSApplication과 공존하며, 별도 이벤트 루프를 생성하지 않는다.

모든 메서드는 메인 스레드에서 호출해야 한다 (app._ui() 큐 통해).
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

import objc
import AppKit
import Foundation

logger = logging.getLogger(__name__)

# ── PyObjC 상수 (버전 호환성 대비 fallback) ─────────────────

NSBackingStoreBuffered = 2

# NSPanel non-activating 스타일마스크 (1 << 7 = 128)
_NSNonactivatingPanel = getattr(
    AppKit, "NSWindowStyleMaskNonactivatingPanel",
    getattr(AppKit, "NSNonactivatingPanelMask", 1 << 7),
)


# ── NSTimer 콜백용 ObjC 헬퍼 ───────────────────────────────

class _FadeTarget(AppKit.NSObject):
    """NSTimer의 target으로 사용할 ObjC 객체.

    Python 메서드를 NSTimer 콜백으로 연결하려면
    NSObject 서브클래스가 필요하다.
    """

    _callback = None

    def initWithCallback_(self, callback):
        self = objc.super(_FadeTarget, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    @objc.python_method
    def set_callback(self, callback):
        self._callback = callback

    def fire_(self, timer):
        """NSTimer가 호출하는 셀렉터."""
        if self._callback is not None:
            self._callback()


class SubtitleOverlay:
    """화면 하단 중앙에 자막을 표시하는 오버레이 창.

    Args:
        config: overlay 설정 딕셔너리.
            - font_size (int): 폰트 크기 (기본 16)
            - max_lines (int): 최대 표시 줄 수 (기본 5)
            - fade_seconds (float): 자동 페이드 시간 (기본 10)
            - opacity (float): 배경 불투명도 0.0~1.0 (기본 0.7)
    """

    def __init__(self, config: dict) -> None:
        self._font_size: int = config.get("font_size", 28)
        self._max_lines: int = config.get("max_lines", 5)
        self._fade_seconds: float = config.get("fade_seconds", 10)
        self._opacity: float = config.get("opacity", 0.7)

        # 자막 라인 버퍼 (최대 max_lines 개)
        self._lines: deque[str] = deque(maxlen=self._max_lines)

        # 페이드 타이머 추적
        self._fade_timer: Foundation.NSTimer | None = None
        self._fade_target: _FadeTarget = _FadeTarget.alloc().initWithCallback_(
            self._do_fade
        )

        # 패널 및 텍스트뷰 생성
        self._panel: AppKit.NSPanel | None = None
        self._text_view: AppKit.NSTextView | None = None
        self._create_panel()

    # ══════════════════════════════════════════════════════
    # 패널 생성
    # ══════════════════════════════════════════════════════

    def _create_panel(self) -> None:
        """NSPanel과 NSTextView를 생성한다."""
        screen = AppKit.NSScreen.mainScreen()
        if screen is None:
            logger.warning("메인 화면을 감지할 수 없음, 오버레이 비활성화")
            return

        screen_frame = screen.visibleFrame()

        # 패널 크기: 화면 너비의 60%, 높이는 줄 수에 따라 결정
        panel_width = screen_frame.size.width * 0.6
        line_height = self._font_size * 1.6
        panel_height = line_height * self._max_lines + 20  # 패딩 포함

        # 화면 하단 중앙 위치
        x = screen_frame.origin.x + (screen_frame.size.width - panel_width) / 2
        y = screen_frame.origin.y + 40  # 하단에서 40pt 위

        panel_rect = Foundation.NSMakeRect(x, y, panel_width, panel_height)

        # NSPanel 생성 (borderless, non-activating)
        self._panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            panel_rect,
            AppKit.NSWindowStyleMaskBorderless | _NSNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )

        # 패널 속성 설정
        self._panel.setLevel_(AppKit.NSStatusWindowLevel + 1)  # 항상 최상위
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.0, 0.0, 0.0, self._opacity
            )
        )
        self._panel.setHasShadow_(True)
        self._panel.setIgnoresMouseEvents_(True)  # 클릭 통과
        self._panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        # 앱 활성화 안 해도 표시
        self._panel.setHidesOnDeactivate_(False)

        # 모서리 둥글게
        self._panel.contentView().setWantsLayer_(True)
        self._panel.contentView().layer().setCornerRadius_(10.0)
        self._panel.contentView().layer().setMasksToBounds_(True)

        # NSTextView 생성 (읽기 전용, 투명 배경)
        content_frame = self._panel.contentView().bounds()
        inset_rect = Foundation.NSInsetRect(content_frame, 12, 8)

        self._text_view = AppKit.NSTextView.alloc().initWithFrame_(inset_rect)
        self._text_view.setEditable_(False)
        self._text_view.setSelectable_(False)
        self._text_view.setDrawsBackground_(False)
        self._text_view.setTextContainerInset_(Foundation.NSMakeSize(0, 0))

        # 텍스트 속성 (흰색, 시스템 폰트)
        self._text_view.setFont_(
            AppKit.NSFont.systemFontOfSize_weight_(
                self._font_size, AppKit.NSFontWeightMedium
            )
        )
        self._text_view.setTextColor_(AppKit.NSColor.whiteColor())
        self._text_view.setAlignment_(AppKit.NSTextAlignmentCenter)

        # autoresizing으로 패널 크기 변경에 따라 텍스트뷰 조정
        self._text_view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )

        self._panel.contentView().addSubview_(self._text_view)

        # 초기 상태: 숨김 (alpha 0이지만 orderFront 상태)
        self._panel.setAlphaValue_(0.0)
        self._panel.orderFront_(None)

    # ══════════════════════════════════════════════════════
    # 공개 API
    # ══════════════════════════════════════════════════════

    def show(self, original: str, translated: str) -> None:
        """자막 한 줄을 추가하고 오버레이를 표시한다.

        표시 형식: [HH:MM:SS] original - translated

        Args:
            original: 원문 텍스트 (영어).
            translated: 번역 텍스트 (한국어).
        """
        if self._panel is None:
            return

        line = translated
        self._lines.append(line)

        # NSAttributedString으로 색상 구분: 이전 줄은 회색, 최신 줄은 흰색
        font = AppKit.NSFont.systemFontOfSize_weight_(
            self._font_size, AppKit.NSFontWeightMedium
        )
        gray = AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
            0.7, 0.7, 0.7, 1.0
        )
        white = AppKit.NSColor.whiteColor()
        paragraph = AppKit.NSMutableParagraphStyle.alloc().init()
        paragraph.setAlignment_(AppKit.NSTextAlignmentCenter)

        attributed = AppKit.NSMutableAttributedString.alloc().init()
        lines = list(self._lines)
        for i, l in enumerate(lines):
            is_last = (i == len(lines) - 1)
            color = white if is_last else gray
            attrs = {
                AppKit.NSFontAttributeName: font,
                AppKit.NSForegroundColorAttributeName: color,
                AppKit.NSParagraphStyleAttributeName: paragraph,
            }
            part = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                l + ("" if is_last else "\n"), attrs
            )
            attributed.appendAttributedString_(part)

        self._text_view.textStorage().setAttributedString_(attributed)

        # 패널 표시 (즉시 불투명)
        self._cancel_fade_timer()
        self._panel.setAlphaValue_(1.0)
        self._panel.orderFront_(None)

        # 자동 페이드 타이머 시작
        self._start_fade_timer()

    def hide(self) -> None:
        """오버레이를 즉시 숨긴다."""
        if self._panel is None:
            return

        self._cancel_fade_timer()
        self._panel.setAlphaValue_(0.0)

    def clear(self) -> None:
        """자막 버퍼를 비우고 오버레이를 숨긴다."""
        self._lines.clear()
        if self._text_view is not None:
            self._text_view.setString_("")
        self.hide()

    def destroy(self) -> None:
        """오버레이 리소스를 정리한다. 앱 종료 시 호출."""
        self._cancel_fade_timer()
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.close()
            self._panel = None
            self._text_view = None

    # ══════════════════════════════════════════════════════
    # 페이드 타이머
    # ══════════════════════════════════════════════════════

    def _start_fade_timer(self) -> None:
        """fade_seconds 후에 페이드 아웃을 시작하는 타이머를 설정한다."""
        if self._fade_seconds <= 0:
            return

        self._fade_timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self._fade_seconds,
            self._fade_target,
            b"fire:",
            None,
            False,
        )

    def _cancel_fade_timer(self) -> None:
        """진행 중인 페이드 타이머를 취소한다."""
        if self._fade_timer is not None:
            self._fade_timer.invalidate()
            self._fade_timer = None

    def _do_fade(self) -> None:
        """NSAnimationContext를 사용하여 2초에 걸쳐 페이드 아웃한다."""
        self._fade_timer = None

        if self._panel is None:
            return

        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(2.0)
        self._panel.animator().setAlphaValue_(0.0)
        AppKit.NSAnimationContext.endGrouping()
