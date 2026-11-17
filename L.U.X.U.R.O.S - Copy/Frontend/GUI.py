"""Luxur desktop UI.

Obsidian-style graphite surfaces with a holographic HUD core, a live Pulse Hub
brain graph, and a red Focus Mode theme. The UI talks to the assistant thread
through small state files in Frontend/Files, polled by `StatePoller`.
"""
import json
import math
import random
import sys
import time

import numpy as np
from PyQt5.QtCore import QLineF, QObject, QPointF, QRectF, QSize, Qt, QTime, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QKeySequence, QPainter, QPen, QPixmap, QRadialGradient,
)
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QScrollArea, QShortcut, QSizeGrip, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from Backend.Common import (
    Assistantname, InputLanguage, Username,
    AnswerConfirmation, GetAssistantStatus, GetMicrophoneStatus, GraphicsDirectoryPath, PendingConfirmation,
    SetMicrophoneStatus, SubmitTypedQuery, TempDirectoryPath, _read,
)

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------
BG = "#121218"          # obsidian base
SURFACE = "#18181f"     # bars
SURFACE_2 = "#22222c"   # raised elements
BORDER = "#2c2c38"
TEXT = "#dcdde3"
MUTED = "#7d7f8c"
VIOLET = "#8b7cf6"      # primary accent (red in Focus Mode)
CYAN = "#4fd1ff"        # HUD / listening (red in Focus Mode)
AMBER = "#ffb547"       # thinking
RED = "#ff5c6c"         # error / close

GREEN = "#5be49b"
STATE_COLORS = {"idle": VIOLET, "listening": CYAN, "thinking": AMBER, "speaking": "#a99bff", "error": RED}

THEMES = {
    "normal": {"accent": "#8b7cf6", "accent2": "#4fd1ff", "idle": "#8b7cf6", "listening": "#4fd1ff",
               "thinking": "#ffb547", "speaking": "#a99bff", "error": "#ff5c6c"},
    "focus": {"accent": "#ff4d5e", "accent2": "#ff8a8a", "idle": "#ff4d5e", "listening": "#ff8a8a",
              "thinking": "#ff7a45", "speaking": "#ff6b7a", "error": "#ffb547"},
}
CURRENT_THEME = "normal"


def SetTheme(name):
    """Swap the accent palette. Painters read these globals at paint time."""
    global VIOLET, CYAN, CURRENT_THEME
    theme = THEMES[name]
    VIOLET, CYAN, CURRENT_THEME = theme["accent"], theme["accent2"], name
    STATE_COLORS.update({k: theme[k] for k in STATE_COLORS})
STATE_SPEED = {"idle": 1.0, "listening": 1.6, "thinking": 3.2, "speaking": 1.4, "error": 0.4}


def StateOf(status):
    s = status.strip().lower()
    if s.startswith("listening"):
        return "listening"
    if s.startswith(("thinking", "searching", "translating", "planning", "working", "reading screen", "waiting")):
        return "thinking"
    if s.startswith("answering"):
        return "speaking"
    if s.startswith("error"):
        return "error"
    return "idle"


def SplitStatus(status):
    """'Listening ...' -> ('LISTENING', ''); 'Error: details' -> ('ERROR', 'details')."""
    status = (status or "Available ...").strip()
    if ":" in status:
        label, detail = status.split(":", 1)
        return label.strip().upper(), detail.strip()
    return status.replace("...", "").strip().upper(), ""


def MonoFont(size, bold=False):
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    if sys.platform.startswith("win"):
        font.setFamily("Consolas")
    font.setPointSizeF(size)
    font.setBold(bold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
    return font


_icon_cache = {}


def TintedPixmap(name, color, size):
    """Render one of the white SVG icons in `color` at the screen's pixel density."""
    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    key = (name, color, size, dpr)
    if key not in _icon_cache:
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        QSvgRenderer(str(GraphicsDirectoryPath(f"{name}.svg"))).render(p, QRectF(0, 0, size, size))
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(QRectF(0, 0, size, size), QColor(color))
        p.end()
        _icon_cache[key] = pm
    return _icon_cache[key]


def Icon(name, color=MUTED, checked_color=None, size=18):
    icon = QIcon()
    icon.addPixmap(TintedPixmap(name, color, size), QIcon.Normal, QIcon.Off)
    if checked_color:
        icon.addPixmap(TintedPixmap(name, checked_color, size), QIcon.Normal, QIcon.On)
    return icon


# --------------------------------------------------------------------------
# State polling
# --------------------------------------------------------------------------

class StatePoller(QObject):
    statusChanged = pyqtSignal(str)
    micChanged = pyqtSignal(bool)
    responseChanged = pyqtSignal(str)
    wakeRequested = pyqtSignal()
    restRequested = pyqtSignal()
    focusChanged = pyqtSignal(dict)
    confirmRequested = pyqtSignal(dict)
    confirmCleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = None
        self._mic = None
        self._response = ""
        self._focus = None
        self._confirm_id = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(100)

    def poll(self):
        status = GetAssistantStatus().strip()
        if status != self._status:
            self._status = status
            self.statusChanged.emit(status)

        mic = GetMicrophoneStatus().strip() == "True"
        if mic != self._mic:
            self._mic = mic
            self.micChanged.emit(mic)

        try:
            if TempDirectoryPath("Wake.data").read_text(encoding="utf-8").strip() == "True":
                TempDirectoryPath("Wake.data").write_text("False", encoding="utf-8")
                self.wakeRequested.emit()
        except (FileNotFoundError, OSError):
            pass

        try:
            if TempDirectoryPath("Rest.data").read_text(encoding="utf-8").strip() == "True":
                TempDirectoryPath("Rest.data").write_text("False", encoding="utf-8")
                self.restRequested.emit()
        except (FileNotFoundError, OSError):
            pass

        try:
            response = TempDirectoryPath("Response.data").read_text(encoding="utf-8")
        except FileNotFoundError:
            response = ""
        if len(response.strip()) > 1 and response != self._response:
            self._response = response
            self.responseChanged.emit(response)

        focus = _read("Focus.data")
        if focus != self._focus:
            self._focus = focus
            try:
                state = json.loads(focus or "{}")
            except json.JSONDecodeError:
                state = {}
            self.focusChanged.emit(state if isinstance(state, dict) else {})

        request = PendingConfirmation()
        request_id = request["id"] if request else None
        if request_id != self._confirm_id:
            self._confirm_id = request_id
            self.confirmRequested.emit(request) if request else self.confirmCleared.emit()

    @property
    def status(self):
        return self._status or ""

    @staticmethod
    def toggleMic():
        mic_on = GetMicrophoneStatus().strip() == "True"
        busy = ("Answering", "Working", "Planning", "Reading screen", "Waiting for approval", "Searching", "Thinking")
        if mic_on and GetAssistantStatus().startswith(busy):
            # Interrupt speech or a running agent task, but keep the mic on so the
            # assistant loop starts listening again right away.
            TempDirectoryPath("Interrupt.data").write_text("True", encoding="utf-8")
            return
        SetMicrophoneStatus("False" if mic_on else "True")


# --------------------------------------------------------------------------
# HUD core
# --------------------------------------------------------------------------

class HudCore(QWidget):
    """Animated reactor-style core whose colour and motion follow the assistant state."""

    def __init__(self, parent=None, detailed=True):
        super().__init__(parent)
        self.detailed = detailed
        self.state = "idle"
        self.color = QColor(STATE_COLORS["idle"])
        self.phase = 0.0
        self._last = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._ticks = {}  # radius -> (minor lines, major lines)
        self._glow = {}   # (rgb, radius) -> pixmap
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def setState(self, state):
        self.state = state

    def showEvent(self, event):
        self._last = time.monotonic()
        self._timer.start(33)  # ~30 fps keeps idle CPU low
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self):
        now = time.monotonic()
        dt, self._last = min(now - self._last, 0.1), now
        self.phase += dt * STATE_SPEED[self.state]
        # Ease the colour towards the target so state changes blend instead of snapping.
        target = QColor(STATE_COLORS[self.state])
        k = min(1.0, dt * 6)
        self.color = QColor(
            int(self.color.red() + (target.red() - self.color.red()) * k),
            int(self.color.green() + (target.green() - self.color.green()) * k),
            int(self.color.blue() + (target.blue() - self.color.blue()) * k),
        )
        self.update()

    def _c(self, alpha):
        c = QColor(self.color)
        c.setAlpha(max(0, min(255, int(alpha))))
        return c

    def _tickLines(self, r):
        key = round(r)
        if key not in self._ticks:
            minor, major = [], []
            for i in range(120):
                a = math.radians(i * 3)
                dx, dy = math.sin(a), -math.cos(a)
                inner = 0.94 if i % 10 == 0 else 0.965
                (major if i % 10 == 0 else minor).append(QLineF(r * dx, r * dy, r * inner * dx, r * inner * dy))
            self._ticks = {key: (minor, major)}
        return self._ticks[key]

    def _drawGlow(self, p, c, r):
        """The ambient glow is the costliest layer, so cache it once the colour has settled."""
        def paint(painter, center):
            glow = QRadialGradient(center, r)
            glow.setColorAt(0.0, self._c(60))
            glow.setColorAt(0.45, self._c(18))
            glow.setColorAt(1.0, self._c(0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, r, r)
            painter.setBrush(Qt.NoBrush)

        if self.color != QColor(STATE_COLORS[self.state]):
            paint(p, c)  # mid-transition: draw live
            return
        key = (self.color.rgb(), round(r))
        if key not in self._glow:
            dpr = self.devicePixelRatioF()
            size = int(math.ceil(r * 2))
            pm = QPixmap(int(size * dpr), int(size * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            paint(painter, QPointF(size / 2, size / 2))
            painter.end()
            if len(self._glow) > 8:
                self._glow.clear()
            self._glow[key] = pm
        pm = self._glow[key]
        p.drawPixmap(QPointF(c.x() - r, c.y() - r), pm)

    @staticmethod
    def _arc(p, center, radius, start_deg, span_deg):
        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        p.drawArc(rect, int(start_deg * 16), int(span_deg * 16))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 * 0.94
        c = QPointF(w / 2, h / 2)
        t, ph = time.monotonic(), self.phase

        self._drawGlow(p, c, r)

        if self.detailed:
            # Outer tick ring
            minor, major = self._tickLines(r)
            p.save()
            p.translate(c)
            p.rotate(ph * 6)
            p.setPen(QPen(self._c(70), 1))
            p.drawLines(minor)
            p.setPen(QPen(self._c(170), 1.6))
            p.drawLines(major)
            p.restore()

            p.setPen(QPen(self._c(50), 1))
            p.drawEllipse(c, r * 0.87, r * 0.87)

            # Segmented ring
            bright = QPen(self._c(220), max(2.0, r * 0.03), Qt.SolidLine, Qt.FlatCap)
            dim = QPen(self._c(110), max(2.0, r * 0.03), Qt.SolidLine, Qt.FlatCap)
            for i in range(24):
                if i % 6 == 5:
                    continue
                p.setPen(bright if i % 4 == 0 else dim)
                self._arc(p, c, r * 0.78, -ph * 14 + i * 15, 10)

            # Fast inner arcs
            pen = QPen(self._c(200), max(1.5, r * 0.012), Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            for start, span, radius, speed in ((0, 110, 0.66, 40), (150, 60, 0.66, 40),
                                               (40, 80, 0.6, -55), (230, 35, 0.6, -55)):
                self._arc(p, c, r * radius, start + ph * speed, span)

            # Inner ring with markers
            p.setPen(QPen(self._c(80), 1))
            p.drawEllipse(c, r * 0.5, r * 0.5)
            p.save()
            p.translate(c)
            p.rotate(-ph * 20)
            p.setPen(Qt.NoPen)
            p.setBrush(self._c(230))
            for _ in range(4):
                p.drawEllipse(QPointF(0, -r * 0.5), r * 0.012 + 1, r * 0.012 + 1)
                p.rotate(90)
            p.restore()
            p.setBrush(Qt.NoBrush)

        core_r = r * (0.24 if self.detailed else 0.42)

        if self.state == "listening":
            for k in range(3):
                f = ((t / 1.6) + k / 3) % 1.0
                p.setPen(QPen(self._c(200 * (1 - f)), 1.5))
                rad = core_r * (1.1 + f * (1.6 if self.detailed else 1.0))
                p.drawEllipse(c, rad, rad)
        elif self.state == "speaking":
            bars = 56 if self.detailed else 24
            p.save()
            p.translate(c)
            p.setPen(QPen(self._c(210), max(1.5, r * 0.01), Qt.SolidLine, Qt.RoundCap))
            for i in range(bars):
                amp = (math.sin(t * 9 + i * 0.7) + math.sin(t * 5.3 + i * 1.9) + 2) / 4
                p.drawLine(QPointF(0, -core_r * 1.15), QPointF(0, -core_r * (1.2 + amp * 0.55)))
                p.rotate(360 / bars)
            p.restore()

        # Core
        pulse = 1 + (0.06 if self.state == "listening" else 0.025) * math.sin(t * 3)
        cr = core_r * pulse
        core = QRadialGradient(c, cr)
        core.setColorAt(0.0, QColor(255, 255, 255, 235))
        core.setColorAt(0.35, self._c(235))
        core.setColorAt(0.75, self._c(90))
        core.setColorAt(1.0, self._c(0))
        p.setPen(Qt.NoPen)
        p.setBrush(core)
        p.drawEllipse(c, cr, cr)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._c(200), 1.6))
        p.drawEllipse(c, core_r * 1.08, core_r * 1.08)
        p.end()


class LogoMark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(9, 9)
        p.setPen(QPen(QColor(VIOLET), 1.6))
        p.drawEllipse(c, 7.5, 7.5)
        g = QRadialGradient(c, 4)
        g.setColorAt(0, QColor("#ffffff"))
        g.setColorAt(1, QColor(CYAN))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, 3.5, 3.5)
        p.end()


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------

class MicButton(QAbstractButton):
    def __init__(self, size=68, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(StatePoller.toggleMic)
        self.toggled.connect(self._updateTip)
        self._updateTip(False)

    def _updateTip(self, on):
        self.setToolTip("Stop listening (Ctrl+M)" if on else "Start listening (Ctrl+M)")

    def setMic(self, on):
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._updateTip(on)
        self.update()

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        c = QPointF(s / 2, s / 2)
        r = s / 2 - 3
        on, hover = self.isChecked(), self.underMouse()

        if on:
            glow = QRadialGradient(c, s / 2)
            glow.setColorAt(0.6, QColor(79, 209, 255, 90))
            glow.setColorAt(1.0, QColor(79, 209, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, s / 2, s / 2)
            p.setBrush(QColor(CYAN))
            p.drawEllipse(c, r - 2, r - 2)
        else:
            p.setBrush(QColor(SURFACE_2))
            p.setPen(QPen(QColor(VIOLET if hover else BORDER), 1.5))
            p.drawEllipse(c, r - 2, r - 2)

        icon = int(s * 0.4)
        pm = TintedPixmap("mic" if on else "mic_off", BG if on else (TEXT if hover else MUTED), icon)
        p.drawPixmap(int((s - icon) / 2), int((s - icon) / 2), pm)
        p.end()


class TopBar(QWidget):
    def __init__(self, window, stacked):
        super().__init__(window)
        self.window_ = window
        self.stacked = stacked
        self.setFixedHeight(46)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setObjectName("TopBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 6, 0)
        layout.setSpacing(0)

        brand = QWidget()
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        bl.addWidget(LogoMark())
        name = QLabel(Assistantname.upper())
        name.setFont(MonoFont(10, bold=True))
        name.setStyleSheet(f"color: {TEXT}; letter-spacing: 4px;")
        bl.addWidget(name)
        version = QLabel("v1 β")
        version.setFont(MonoFont(8))
        version.setStyleSheet(f"color: {MUTED};")
        bl.addWidget(version)
        bl.addStretch(1)

        tabs = QFrame()
        tabs.setObjectName("Tabs")
        tl = QHBoxLayout(tabs)
        tl.setContentsMargins(3, 3, 3, 3)
        tl.setSpacing(2)
        self.group = QButtonGroup(self)
        for index, (label, icon) in enumerate((("Home", "home"), ("Chat", "chat"), ("Pulse Hub", "pulse"))):
            button = QPushButton(f" {label}")
            button.setObjectName("Tab")
            button.setCheckable(True)
            button.setIcon(Icon(icon, MUTED, TEXT, 16))
            button.setIconSize(QSize(16, 16))
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(f"{label} (Ctrl+{index + 1})")
            self.group.addButton(button, index)
            tl.addWidget(button)
        self.group.button(0).setChecked(True)
        self.group.buttonClicked[int].connect(stacked.setCurrentIndex)
        stacked.currentChanged.connect(self._tabChanged)

        controls = QWidget()
        cl = QHBoxLayout(controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)
        cl.addStretch(1)
        self.focus_pill = QLabel()
        self.focus_pill.setObjectName("FocusPill")
        self.focus_pill.setFont(MonoFont(7, bold=True))
        self.focus_pill.setFixedHeight(22)
        self.focus_pill.hide()
        cl.addWidget(self.focus_pill, 0, Qt.AlignVCenter)
        cl.addSpacing(10)
        self.maximize_button = None
        for icon, slot, name in (("minimize", window.showMinimized, "WinButton"),
                                 ("maximize", self.toggleMaximize, "WinButton"),
                                 ("close", window.close, "CloseButton")):
            button = QPushButton()
            button.setObjectName(name)
            button.setIcon(Icon(icon, MUTED, size=14))
            button.setIconSize(QSize(14, 14))
            button.setFixedSize(42, 32)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            cl.addWidget(button)
            if icon == "maximize":
                self.maximize_button = button

        layout.addWidget(brand, 1)
        layout.addWidget(tabs, 0, Qt.AlignVCenter)
        layout.addWidget(controls, 1)

    def _tabChanged(self, index):
        self.group.button(index).setChecked(True)
        if index == 2:
            self.setPulseBadge(False)

    def setPulseBadge(self, on):
        self.group.button(2).setText(" Pulse Hub  ●" if on else " Pulse Hub")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setFocusInfo(getattr(self, "_focus_state", {}))

    def setFocusInfo(self, state):
        self._focus_state = state
        if not state.get("active"):
            self.focus_pill.hide()
            return
        minutes = int((time.time() - state.get("started", time.time())) / 60)
        goal = state.get("goal") or ""
        goal = (goal[:18] + "…") if len(goal) > 19 else goal
        self.focus_pill.setToolTip(f"Focus Mode: {state.get('goal') or 'on'}. Say 'focus off' to end it.")
        roomy = self.width() >= 1100   # drop the goal text when the window is narrow
        parts = ["● FOCUS"] + ([goal.upper()] if goal and roomy else []) + [f"{minutes} MIN", f"{state.get('distractions', 0)} ✕"]
        self.focus_pill.setText("  ·  ".join(parts))
        self.focus_pill.show()

    def toggleMaximize(self):
        if self.window_.isMaximized():
            self.window_.showNormal()
        else:
            self.window_.showMaximized()

    def syncMaximizeIcon(self):
        name = "restore" if self.window_.isMaximized() else "maximize"
        self.maximize_button.setIcon(Icon(name, MUTED, size=14))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window_.windowHandle()
            # startSystemMove works on Wayland too, where move() is ignored.
            if handle is None or not handle.startSystemMove():
                self._drag = event.globalPos() - self.window_.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_drag", None) is not None and event.buttons() & Qt.LeftButton:
            self.window_.move(event.globalPos() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        self.toggleMaximize()


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

class InitialScreen(QWidget):
    HINTS = {
        "idle": "Tap the mic to start a conversation",
        "listening": "Go ahead, I'm listening",
        "thinking": "Working on it",
        "speaking": "Tap the mic to interrupt",
        "error": "Something went wrong, check the console",
    }

    def __init__(self, poller, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.mic_on = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 48)
        layout.setSpacing(0)

        self.hud = HudCore()
        self.hud.setMinimumSize(220, 220)
        self.hud.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.hud, 1)
        layout.addSpacing(18)

        self.status = QLabel()
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setFont(MonoFont(11, bold=True))
        layout.addWidget(self.status)
        layout.addSpacing(6)

        self.hint = QLabel()
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        layout.addWidget(self.hint)
        layout.addSpacing(22)

        self.mic = MicButton(72)
        layout.addWidget(self.mic, 0, Qt.AlignHCenter)

        poller.statusChanged.connect(self.setStatus)
        poller.micChanged.connect(self.setMic)

        self._backdrop = None
        self._clock = QTimer(self)
        self._clock.timeout.connect(self.update)
        self._clock.start(1000)

    def setStatus(self, status):
        self.state = StateOf(status)
        self.hud.setState(self.state)
        label, detail = SplitStatus(status)
        self.status.setText(label)
        self.status.setStyleSheet(f"color: {STATE_COLORS[self.state]}; letter-spacing: 6px;")
        self.hint.setText(detail or self.HINTS[self.state])
        self.update()

    def setMic(self, on):
        self.mic_on = on
        self.mic.setMic(on)
        self.update()

    def resizeEvent(self, event):
        self._backdrop = None
        super().resizeEvent(event)

    def Backdrop(self):
        """Dot grid, rendered once per size (repainting it every HUD frame is costly)."""
        if self._backdrop is None or self._backdrop.size() != self.size() * self.devicePixelRatioF():
            dpr = self.devicePixelRatioF()
            pm = QPixmap(self.size() * dpr)
            pm.setDevicePixelRatio(dpr)
            pm.fill(QColor(BG))
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            step = 28
            for x in range(step // 2, self.width(), step):
                for y in range(step // 2, self.height(), step):
                    p.drawEllipse(QPointF(x, y), 1, 1)
            p.end()
            self._backdrop = pm
        return self._backdrop

    def paintEvent(self, event):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.Backdrop())
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # HUD corner brackets
        m, L = 18, 26
        p.setPen(QPen(QColor(VIOLET), 1.5))
        for x, y, dx, dy in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))

        # Telemetry
        p.setFont(MonoFont(8))
        p.setPen(QColor(MUTED))
        pad = 30
        p.drawText(QRectF(pad, pad - 6, 400, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{Assistantname.upper()} // CORE INTERFACE")
        p.drawText(QRectF(w - pad - 400, pad - 6, 400, 20), Qt.AlignRight | Qt.AlignVCenter,
                   QTime.currentTime().toString("HH:mm:ss"))
        p.drawText(QRectF(pad, h - pad - 14, 400, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   f"VOICE INPUT · {InputLanguage.upper()}")
        p.setPen(QColor(CYAN if self.mic_on else MUTED))
        p.drawText(QRectF(w - pad - 400, h - pad - 14, 400, 20), Qt.AlignRight | Qt.AlignVCenter,
                   "● MIC ARMED" if self.mic_on else "○ MIC STANDBY")
        p.end()


class MessageWidget(QWidget):
    def __init__(self, role, text, stamp=""):
        super().__init__()
        self.role = role
        user = role == "user"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = QLabel(f"{(Username if user else Assistantname).upper()}" + (f"  ·  {stamp}" if stamp else ""))
        header.setFont(MonoFont(8, bold=True))
        header.setObjectName("UserHeader" if user else "AssistantHeader")
        header.setAlignment(Qt.AlignRight if user else Qt.AlignLeft)
        layout.addWidget(header)

        self.body = QLabel(text)
        self.body.setTextFormat(Qt.PlainText)
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setCursor(Qt.IBeamCursor)
        self.body.setObjectName("UserBubble" if user else "AssistantNote")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if user:
            row.addStretch(1)
            row.addWidget(self.body)
        else:
            accent = QFrame()
            accent.setFixedWidth(2)
            accent.setObjectName("AccentBar")
            row.setSpacing(14)
            row.addWidget(accent)
            row.addWidget(self.body, 1)
        layout.addLayout(row)

    def fitWidth(self, column_width):
        """Size user bubbles to their text, capped at 78% of the column."""
        if self.role != "user" or column_width <= 0:
            return
        self.body.ensurePolished()
        self.body.setWordWrap(False)
        natural = self.body.sizeHint().width()  # single-line width incl. stylesheet padding
        self.body.setWordWrap(True)
        self.body.setFixedWidth(min(int(column_width * 0.78), natural + 2))


class ChatSection(QWidget):
    def __init__(self, poller, parent=None):
        super().__init__(parent)
        self.messages = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        canvas = QWidget()
        canvas.setObjectName("ChatCanvas")
        outer = QHBoxLayout(canvas)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.addStretch(1)
        self.column = QWidget()
        self.column.setMaximumWidth(820)
        self.column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.list = QVBoxLayout(self.column)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(22)
        self.list.addStretch(1)
        outer.addWidget(self.column, 100)
        outer.addStretch(1)
        self.scroll.setWidget(canvas)
        layout.addWidget(self.scroll, 1)

        self.empty = QLabel("No messages yet. Switch the mic on and say hello.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {MUTED}; font-size: 13px;")
        self.list.insertWidget(0, self.empty)

        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _min, _max: bar.setValue(_max))

        # Bottom status bar
        footer = QFrame()
        footer.setObjectName("Footer")
        footer.setFixedHeight(76)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(24, 0, 24, 0)
        fl.setSpacing(14)
        self.hud = HudCore(detailed=False)
        self.hud.setFixedSize(44, 44)
        fl.addWidget(self.hud)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.status = QLabel()
        self.status.setFont(MonoFont(9, bold=True))
        self.hint = QLabel(f"Voice transcript with {Assistantname}")
        self.hint.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        text.addStretch(1)
        text.addWidget(self.status)
        text.addWidget(self.hint)
        text.addStretch(1)
        status_box = QWidget()
        status_box.setLayout(text)
        status_box.setFixedWidth(230)
        fl.addWidget(status_box)
        self.input = QLineEdit()
        self.input.setObjectName("CommandInput")
        self.input.setPlaceholderText("Type a command or question and press Enter")
        self.input.returnPressed.connect(self.submitTyped)
        fl.addWidget(self.input, 1)
        self.mic = MicButton(52)
        fl.addWidget(self.mic)
        layout.addWidget(footer)

        poller.responseChanged.connect(self.loadMessages)
        poller.statusChanged.connect(self.setStatus)
        poller.micChanged.connect(self.mic.setMic)

    def submitTyped(self):
        text = self.input.text().strip()
        if text:
            SubmitTypedQuery(text)
            self.input.clear()
            self.input.clearFocus()  # keystrokes the agent sends must never land back in this box

    def setStatus(self, status):
        state = StateOf(status)
        self.hud.setState(state)
        label, detail = SplitStatus(status)
        self.status.setText(label)
        self.status.setStyleSheet(f"color: {STATE_COLORS[state]}; letter-spacing: 4px;")
        self.hint.setText(detail or f"Voice transcript with {Assistantname}")

    @staticmethod
    def ParseMessages(text):
        """Split 'Name : message' blocks into (role, text) pairs; continuation lines join the previous one."""
        prefixes = (("user", f"{Username} :"), ("assistant", f"{Assistantname} :"))
        result = []
        for line in text.split("\n"):
            for role, prefix in prefixes:
                if line.startswith(prefix):
                    result.append([role, line[len(prefix):].strip()])
                    break
            else:
                if result:
                    result[-1][1] += "\n" + line
                elif line.strip():
                    result.append(["assistant", line.strip()])
        return [(role, body.strip()) for role, body in result if body.strip()]

    def loadMessages(self, text):
        stamp = QTime.currentTime().toString("HH:mm") if self.messages else ""
        for role, body in self.ParseMessages(text):
            self.addMessage(role, body, stamp)

    def addMessage(self, role, text, stamp=""):
        self.empty.hide()
        widget = MessageWidget(role, text, stamp)
        widget.fitWidth(self.column.width())
        self.list.insertWidget(self.list.count() - 1, widget)
        self.messages.append(widget)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for widget in self.messages:
            widget.fitWidth(self.column.width())



# --------------------------------------------------------------------------
# Pulse Hub: the brain graph
# --------------------------------------------------------------------------

KIND_COLORS = {"semantic": lambda: VIOLET, "episodic": lambda: CYAN, "procedural": lambda: AMBER,
               "project": lambda: GREEN}
TASK_COLORS = {"done": lambda: GREEN, "failed": lambda: RED, "cancelled": lambda: MUTED, "executing": lambda: AMBER,
               "reminder": lambda: CYAN, "note": lambda: CYAN}
PULSE_SECONDS = 4.0


class BrainGraph(QWidget):
    """Force-directed memory graph (Obsidian-style). Simulates only while visible and unsettled."""
    nodeSelected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 260)
        self.ids, self.index, self.meta = [], {}, []
        self.pos = np.zeros((0, 2))
        self.vel = np.zeros((0, 2))
        self.edges = np.zeros((0, 2), dtype=int)
        self.edge_ids, self.edge_born = [], {}
        self.born = {}
        self.neighbors = {}
        self.alpha = 0.0
        self.zoom, self.offset = 1.0, QPointF(0, 0)
        self.auto_fit = True   # keep the whole graph in view until the user pans or zooms
        self.selected = self.hover = None
        self.filter = ""
        self._drag_node = None
        self._pan_from = None
        self._press_pos = None
        self._loaded = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)

    # ---------------------------------------------------------------- data

    def setGraph(self, graph):
        now = time.monotonic()
        old_pos = {nid: self.pos[i] for i, nid in enumerate(self.ids)}
        old_vel = {nid: self.vel[i] for i, nid in enumerate(self.ids)}
        nodes = graph.get("nodes", [])
        ids = [n["id"] for n in nodes]
        index = {nid: i for i, nid in enumerate(ids)}
        edges = [(e["a"], e["b"]) for e in graph.get("edges", []) if e["a"] in index and e["b"] in index]
        neighbors = {nid: set() for nid in ids}
        for a, b in edges:
            neighbors[a].add(b)
            neighbors[b].add(a)

        pos, vel = np.zeros((len(ids), 2)), np.zeros((len(ids), 2))
        for i, nid in enumerate(ids):
            if nid in old_pos:
                pos[i], vel[i] = old_pos[nid], old_vel[nid]
                continue
            anchor = next((old_pos[n] for n in neighbors[nid] if n in old_pos), None)
            if anchor is not None:
                pos[i] = anchor + np.random.uniform(-30, 30, 2)
            else:
                angle, radius = random.uniform(0, math.tau), random.uniform(40, 220)
                pos[i] = (radius * math.cos(angle), radius * math.sin(angle))
            if self._loaded:
                self.born[nid] = now   # new while watching: pulse it
        new_edges = {f"{a}|{b}" for a, b in edges} - set(self.edge_ids)
        if self._loaded:
            for key in new_edges:
                self.edge_born[key] = now

        self.ids, self.index, self.meta, self.neighbors = ids, index, nodes, neighbors
        self.pos, self.vel = pos, vel
        self.edge_ids = [f"{a}|{b}" for a, b in edges]
        self.edges = np.array([(index[a], index[b]) for a, b in edges], dtype=int).reshape(-1, 2)
        if self.selected not in index:
            self.selected = None
        self.kick(1.0 if not self._loaded else 0.5)
        self._loaded = True

    def kick(self, alpha=0.5):
        self.alpha = max(self.alpha, alpha)
        if self.isVisible() and not self._timer.isActive():
            self._timer.start(33)

    def showEvent(self, event):
        if self.alpha > 0.02 or self.born:
            self._timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # ---------------------------------------------------------------- physics

    def _step(self):
        n = len(self.ids)
        pulsing = any(time.monotonic() - t < PULSE_SECONDS for t in list(self.born.values()) + list(self.edge_born.values()))
        if n and self.alpha > 0.02:
            delta = self.pos[:, None, :] - self.pos[None, :, :]
            dist2 = (delta ** 2).sum(-1) + 25.0
            force = (delta * (2200.0 / dist2)[..., None]).sum(1)          # repulsion
            if len(self.edges):
                a, b = self.edges[:, 0], self.edges[:, 1]
                d = self.pos[b] - self.pos[a]
                length = np.sqrt((d ** 2).sum(-1, keepdims=True)) + 1e-6
                spring = d / length * (length - 90.0) * 0.06               # links pull to ~90 px
                np.add.at(force, a, spring)
                np.add.at(force, b, -spring)
            force -= self.pos * 0.012                                      # gentle gravity
            self.vel = (self.vel + force * self.alpha) * 0.55
            speed = np.sqrt((self.vel ** 2).sum(-1, keepdims=True))
            self.vel *= np.minimum(1.0, 12.0 / (speed + 1e-6))
            if self._drag_node is not None:
                self.vel[self._drag_node] = 0
            self.pos += self.vel
            self.alpha *= 0.985
        if self.alpha <= 0.02 and not pulsing and self._drag_node is None:
            self._timer.stop()
        self.update()

    # ---------------------------------------------------------------- view helpers

    def _fit(self):
        if not self.auto_fit or not len(self.pos):
            return
        lo, hi = self.pos.min(0), self.pos.max(0)
        span = np.maximum(hi - lo, 1.0)
        zoom = min((self.width() - 140) / span[0], (self.height() - 120) / span[1], 1.6)
        self.zoom = max(0.3, zoom)
        center = (lo + hi) / 2
        self.offset = QPointF(-center[0] * self.zoom, -center[1] * self.zoom + 10)

    def _to_screen(self, xy):
        return QPointF(self.width() / 2 + self.offset.x() + xy[0] * self.zoom,
                       self.height() / 2 + self.offset.y() + xy[1] * self.zoom)

    def _to_world(self, point):
        return np.array([(point.x() - self.width() / 2 - self.offset.x()) / self.zoom,
                         (point.y() - self.height() / 2 - self.offset.y()) / self.zoom])

    def _radius(self, i):
        node = self.meta[i]
        return 5.0 if node["type"] == "task" else 4.0 + (node.get("importance") or 5) * 0.75

    def _node_at(self, point):
        if not self.ids:
            return None
        world = self._to_world(point)
        d = np.sqrt(((self.pos - world) ** 2).sum(-1))
        i = int(np.argmin(d))
        return i if d[i] * self.zoom <= self._radius(i) * self.zoom + 6 else None

    def _color(self, i):
        node = self.meta[i]
        table = TASK_COLORS if node["type"] == "task" else KIND_COLORS
        return QColor(table.get(node["kind"], lambda: MUTED)())

    def focusNode(self, node_id):
        if node_id in self.index:
            i = self.index[node_id]
            self.selected = node_id
            if not self.auto_fit:
                self.offset = QPointF(-self.pos[i][0] * self.zoom, -self.pos[i][1] * self.zoom)
            self.update()

    def setFilter(self, text):
        self.filter = text.lower().strip()
        self.update()

    # ---------------------------------------------------------------- painting

    def paintEvent(self, event):
        self._fit()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        glow = QRadialGradient(QPointF(self.width() / 2, self.height() / 2), max(self.width(), self.height()) / 1.4)
        tint = QColor(VIOLET)
        tint.setAlpha(18)
        glow.setColorAt(0, tint)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        if not self.ids:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Pulse Hub is empty.\nTalk to LUXUR: memories and tasks appear here as it learns.")
            p.end()
            return

        now = time.monotonic()
        focus = self.hover or self.selected
        highlight = ({focus} | self.neighbors.get(focus, set())) if focus else set()
        matches = {nid for i, nid in enumerate(self.ids) if self.filter and self.filter in self.meta[i]["label"].lower()}
        dim = bool(self.filter)

        for (a, b), key in zip(self.edges, self.edge_ids):
            na, nb = self.ids[a], self.ids[b]
            fresh = now - self.edge_born.get(key, -99) < PULSE_SECONDS
            lit = na in highlight and nb in highlight
            color = QColor(VIOLET if (lit or fresh) else MUTED)
            color.setAlpha(220 if fresh else 170 if lit else 45 if not dim else 20)
            p.setPen(QPen(color, 2.2 if fresh else 1.4 if lit else 1.0))
            p.drawLine(self._to_screen(self.pos[a]), self._to_screen(self.pos[b]))

        label_font = QFont(self.font())
        label_font.setPointSizeF(8.5)
        p.setFont(label_font)
        metrics = QFontMetrics(label_font)
        for i, nid in enumerate(self.ids):
            node = self.meta[i]
            center = self._to_screen(self.pos[i])
            r = self._radius(i) * max(0.6, min(self.zoom, 1.6))
            color = self._color(i)
            faded = dim and nid not in matches
            if faded:
                color.setAlpha(50)

            age = now - self.born.get(nid, -99)
            if age < PULSE_SECONDS:
                for k in range(2):
                    f = ((age * 0.8) + k / 2) % 1.0
                    ring = QColor(color)
                    ring.setAlpha(int(200 * (1 - f)))
                    p.setPen(QPen(ring, 1.6))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(center, r + f * 26, r + f * 26)

            if nid == self.selected:
                halo = QColor(color)
                halo.setAlpha(70)
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(center, r + 7, r + 7)

            if node["type"] == "task":
                p.setPen(QPen(color, 1.8))
                p.setBrush(QColor(SURFACE_2))
                p.save()
                p.translate(center)
                p.rotate(45)
                p.drawRect(QRectF(-r, -r, 2 * r, 2 * r))
                p.restore()
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(center, r, r)

            show_label = (nid in highlight or nid in matches or self.zoom >= 1.4
                          or (node.get("importance") or 0) >= 8 or age < PULSE_SECONDS)
            if show_label and not faded:
                text = metrics.elidedText(node["label"], Qt.ElideRight, 220)
                p.setPen(QColor(TEXT if nid in highlight else MUTED))
                p.drawText(QPointF(center.x() + r + 6, center.y() + 4), text)

        # Legend
        p.setFont(MonoFont(7.5))
        x, y = 16, self.height() - 16
        for label, color, diamond in (("FACT", VIOLET, False), ("EVENT", CYAN, False), ("PATTERN", AMBER, False),
                                      ("TASK", GREEN, True)):
            p.setPen(Qt.NoPen if not diamond else QPen(QColor(color), 1.5))
            p.setBrush(QColor(color) if not diamond else QColor(SURFACE_2))
            if diamond:
                p.save()
                p.translate(x + 4, y - 4)
                p.rotate(45)
                p.drawRect(QRectF(-3.5, -3.5, 7, 7))
                p.restore()
            else:
                p.drawEllipse(QPointF(x + 4, y - 4), 4, 4)
            p.setPen(QColor(MUTED))
            p.drawText(QPointF(x + 13, y), label)
            x += 26 + QFontMetrics(p.font()).horizontalAdvance(label)
        p.end()

    # ---------------------------------------------------------------- interaction

    def mousePressEvent(self, event):
        self._press_pos = event.pos()
        i = self._node_at(event.pos())
        if i is not None and event.button() == Qt.LeftButton:
            self._drag_node = i
            self.kick(0.3)
        else:
            self._pan_from = event.pos()

    def mouseMoveEvent(self, event):
        if self._drag_node is not None:
            self.pos[self._drag_node] = self._to_world(event.pos())
            self.kick(0.3)
        elif self._pan_from is not None:
            self.auto_fit = False
            self.offset += QPointF(event.pos() - self._pan_from)
            self._pan_from = event.pos()
            self.update()
        else:
            i = self._node_at(event.pos())
            hover = self.ids[i] if i is not None else None
            if hover != self.hover:
                self.hover = hover
                self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
                self.setToolTip(self.meta[i]["label"] if i is not None else "")
                self.update()

    def mouseReleaseEvent(self, event):
        clicked = self._press_pos is not None and (event.pos() - self._press_pos).manhattanLength() < 5
        if clicked:
            i = self._node_at(event.pos())
            self.selected = self.ids[i] if i is not None else None
            if self.selected:
                self.nodeSelected.emit(self.selected)
            self.update()
        self._drag_node = self._pan_from = self._press_pos = None

    def mouseDoubleClickEvent(self, event):
        if self._node_at(event.pos()) is None:
            self.auto_fit = True   # double-click empty space: back to the whole graph
            self.update()

    def wheelEvent(self, event):
        self.auto_fit = False
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = max(0.3, min(3.5, self.zoom * factor))
        cursor = QPointF(event.pos())
        center = QPointF(self.width() / 2, self.height() / 2) + self.offset
        self.offset += (cursor - center) * (1 - new_zoom / self.zoom)
        self.zoom = new_zoom
        self.update()


class DetailsPanel(QFrame):
    nodeRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Details")
        self.setFixedWidth(330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(6)
        self.title = QLabel("Select a node")
        self.title.setObjectName("DetailsTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel("Click any memory or task in the graph to see its details, sources and connections.")
        self.meta.setObjectName("DetailsMeta")
        self.meta.setWordWrap(True)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.PlainText)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.title)
        layout.addWidget(self.meta)
        layout.addWidget(self.body)
        self.lists = {}
        for key, label in (("related", "CONNECTED MEMORIES"), ("tasks", "CONNECTED TASKS")):
            section = QLabel(label)
            section.setObjectName("SectionLabel")
            section.setFont(MonoFont(7.5, bold=True))
            items = QListWidget()
            items.setWordWrap(True)
            items.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            items.setTextElideMode(Qt.ElideNone)
            items.itemClicked.connect(lambda item: self.nodeRequested.emit(item.data(Qt.UserRole)))
            layout.addWidget(section)
            layout.addWidget(items, 1)
            self.lists[key] = (section, items)
        self.show_details(None)

    def show_details(self, details):
        for section, items in self.lists.values():
            items.clear()
            section.setVisible(details is not None)
            items.setVisible(details is not None)
        if not details:
            self.body.clear()
            return
        self.title.setText(details["title"])
        meta = [details["kind"].upper(), f"source: {details['source']}"]
        if details.get("importance") is not None:
            meta.append(f"importance {details['importance']}/10")
        meta.append((details.get("created_at") or "").replace("T", " ")[:16])
        self.meta.setText("  ·  ".join(m for m in meta if m))
        body = details.get("summary") or ""
        if details.get("steps"):
            body += ("\n\n" if body else "") + "Steps:\n" + "\n".join(f"• {s}" for s in details["steps"][:12])
        self.body.setText(body)
        self.body.setVisible(bool(body))
        for key in ("related", "tasks"):
            section, items = self.lists[key]
            for entry in details.get(key, []):
                item = QListWidgetItem(f"{entry['label'][:120]}  ·  {entry['relation']}")
                item.setData(Qt.UserRole, entry["id"])
                items.addItem(item)
            if not details.get(key):
                items.addItem(QListWidgetItem("None yet"))


class PulseHubScreen(QWidget):
    """Pulse Hub tab: the live brain graph plus a details panel."""
    activity = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hub = None
        self._version = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(24, 18, 24, 0)
        ll.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("PULSE HUB")
        title.setFont(MonoFont(10, bold=True))
        title.setStyleSheet("letter-spacing: 4px;")
        self.stats = QLabel()
        self.stats.setFont(MonoFont(8))
        self.stats.setStyleSheet(f"color: {MUTED};")
        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setPlaceholderText("Search memories")
        self.search.setFixedWidth(220)
        header.addWidget(title)
        header.addSpacing(14)
        header.addWidget(self.stats)
        header.addStretch(1)
        header.addWidget(self.search)
        ll.addLayout(header)
        self.graph = BrainGraph()
        ll.addWidget(self.graph, 1)
        layout.addWidget(left, 1)

        self.details = DetailsPanel()
        layout.addWidget(self.details)

        self.search.textChanged.connect(self.graph.setFilter)
        self.graph.nodeSelected.connect(self.select)
        self.details.nodeRequested.connect(self.select)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1500)

    def hub(self):
        if self._hub is None:
            from Backend.PulseHub import PulseHub
            self._hub = PulseHub()
        return self._hub

    def refresh(self, force=False):
        try:
            version = self.hub().graph_version()
        except Exception as e:
            print(f"[GUI] Pulse Hub unavailable: {e}")
            return
        if version == self._version and not force:
            return
        first = self._version is None
        self._version = version
        if not self.isVisible() and not first:
            self.activity.emit()   # badge the tab; graph refreshes when opened
            return
        graph = self.hub().graph()
        self.graph.setGraph(graph)
        memories = sum(1 for n in graph["nodes"] if n["type"] == "memory")
        self.stats.setText(f"{memories} MEMORIES  ·  {len(graph['nodes']) - memories} TASKS  ·  {len(graph['edges'])} LINKS")

    def select(self, node_id):
        if not node_id:
            return
        self.graph.focusNode(node_id)
        self.details.show_details(self.hub().node_details(node_id))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh(force=True)


# --------------------------------------------------------------------------
# Confirmation dialog (the agent's safety gate)
# --------------------------------------------------------------------------

class ConfirmDialog(QDialog):
    def __init__(self, request, parent=None):
        # Parentless and always on top: the agent is usually working in another app,
        # with LUXUR's window behind it.
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.request = request
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("ConfirmDialog")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel("LUXUR NEEDS YOUR OK")
        title.setObjectName("ConfirmTitle")
        title.setFont(MonoFont(9, bold=True))
        question = QLabel(request.get("question", ""))
        question.setWordWrap(True)
        question.setTextFormat(Qt.PlainText)
        question.setMinimumWidth(380)
        self.countdown = QLabel()
        self.countdown.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        buttons = QHBoxLayout()
        buttons.addWidget(self.countdown)
        buttons.addStretch(1)
        deny = QPushButton("Deny")
        deny.setObjectName("DenyButton")
        allow = QPushButton("Allow")
        allow.setObjectName("AllowButton")
        deny.setDefault(True)   # Enter = the safe choice
        deny.clicked.connect(lambda: self.answer(False))
        allow.clicked.connect(lambda: self.answer(True))
        buttons.addWidget(deny)
        buttons.addWidget(allow)
        for w in (title, question):
            layout.addWidget(w)
        layout.addLayout(buttons)
        self._deadline = request.get("ts", time.time()) + 60
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_countdown)
        self._tick.start(500)
        self._update_countdown()

    def _update_countdown(self):
        left = int(self._deadline - time.time())
        self.countdown.setText(f"Auto-deny in {max(0, left)}s")
        if left <= 0:
            self.answer(False)

    def answer(self, approved):
        AnswerConfirmation(self.request["id"], approved)
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.answer(False)
        else:
            super().keyPressEvent(event)


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------

def BuildStylesheet():
    return f"""
QWidget {{ color: {TEXT}; font-size: 14px; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 8px; }}
#Root {{ background: {BG}; border: 1px solid {VIOLET if CURRENT_THEME == "focus" else BORDER}; }}
#TopBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
#Tabs {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; }}
#Tab {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
        padding: 5px 16px; font-size: 13px; }}
#Tab:hover {{ color: {TEXT}; }}
#Tab:checked {{ background: {SURFACE_2}; color: {TEXT}; }}
#WinButton, #CloseButton {{ background: transparent; border: none; border-radius: 6px; }}
#WinButton:hover {{ background: {SURFACE_2}; }}
#CloseButton:hover {{ background: {RED}; }}
#ChatCanvas {{ background: {BG}; }}
#Footer {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
#UserBubble {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 10px;
               padding: 10px 14px; font-size: 14px; }}
#AssistantNote {{ padding: 2px 0; font-size: 14px; }}
QScrollArea {{ background: {BG}; }}
#UserHeader {{ color: {VIOLET}; }}
#AssistantHeader {{ color: {CYAN}; }}
#AccentBar {{ background: {CYAN}; border: none; }}
#CommandInput, #SearchInput {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 12px;
                 font-size: 13px; selection-background-color: {VIOLET}; }}
#CommandInput:focus, #SearchInput:focus {{ border: 1px solid {VIOLET}; }}
#FocusPill {{ background: {VIOLET}; color: #ffffff; border-radius: 11px; padding: 0 10px; }}
#Details {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}
#DetailsTitle {{ font-size: 15px; font-weight: 600; }}
#DetailsMeta, #SectionLabel {{ color: {MUTED}; }}
#SectionLabel {{ margin-top: 10px; }}
QListWidget {{ background: transparent; border: none; outline: none; font-size: 13px; }}
QListWidget::item {{ padding: 6px 4px; border-bottom: 1px solid {BORDER}; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: {SURFACE_2}; color: {TEXT}; }}
#ConfirmDialog {{ background: {SURFACE}; border: 1px solid {VIOLET}; border-radius: 12px; }}
#ConfirmTitle {{ color: {VIOLET}; }}
#AllowButton {{ background: {VIOLET}; color: #ffffff; border: none; border-radius: 8px; padding: 8px 18px; font-weight: 600; }}
#DenyButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 18px; }}
#AllowButton:hover {{ background: {CYAN}; }}
#DenyButton:hover {{ border: 1px solid {MUTED}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {VIOLET}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle(Assistantname)
        self.setWindowIcon(QIcon(str(GraphicsDirectoryPath("jarvis_animated.gif"))))
        self.setMinimumSize(760, 540)

        self.poller = StatePoller(self)
        self.poller.wakeRequested.connect(self.wakeUp)
        self.poller.restRequested.connect(self.restNow)
        self.poller.focusChanged.connect(self.applyFocus)
        self.poller.confirmRequested.connect(self.showConfirm)
        self.poller.confirmCleared.connect(self.closeConfirm)
        self._confirm = None
        self._focus_state = {}
        root = QWidget()
        root.setObjectName("Root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.stacked = QStackedWidget()
        self.stacked.addWidget(InitialScreen(self.poller))
        self.stacked.addWidget(ChatSection(self.poller))
        self.pulse = PulseHubScreen()
        self.stacked.addWidget(self.pulse)
        self.top_bar = TopBar(self, self.stacked)
        self.pulse.activity.connect(lambda: self.top_bar.setPulseBadge(True))
        layout.addWidget(self.top_bar)
        layout.addWidget(self.stacked, 1)
        self.setCentralWidget(root)

        self.grip = QSizeGrip(root)
        self.grip.setFixedSize(16, 16)
        self.grip.setStyleSheet("background: transparent;")

        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.stacked.setCurrentIndex(0))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.stacked.setCurrentIndex(1))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.stacked.setCurrentIndex(2))
        self._focus_clock = QTimer(self)
        self._focus_clock.timeout.connect(lambda: self.top_bar.setFocusInfo(self._focus_state))
        self._focus_clock.start(15000)
        QShortcut(QKeySequence("Ctrl+M"), self, StatePoller.toggleMic)

        available = QApplication.primaryScreen().availableGeometry()
        width = min(1280, int(available.width() * 0.9))
        height = min(820, int(available.height() * 0.9))
        self.resize(width, height)
        self.move(available.center().x() - width // 2, available.center().y() - height // 2)
        self.poller.poll()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.grip.move(self.width() - self.grip.width() - 2, self.height() - self.grip.height() - 2)
        self.grip.raise_()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.WindowStateChange:
            self.top_bar.syncMaximizeIcon()
            self.grip.setVisible(not self.isMaximized())
            try:
                TempDirectoryPath("Minimized.data").write_text(
                    "True" if self.isMinimized() else "False", encoding="utf-8")
            except OSError:
                pass

    def wakeUp(self):
        # Called (via the poller) when the wake listener hears "LUXUR",
        # "wake up", or detects three claps while minimized.
        self.setWindowState((self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def applyFocus(self, state):
        """Focus Mode turns the whole UI red (and back)."""
        self._focus_state = state
        wanted = "focus" if state.get("active") else "normal"
        if wanted != CURRENT_THEME:
            SetTheme(wanted)
            QApplication.instance().setStyleSheet(BuildStylesheet())
            for i in range(self.stacked.count()):
                screen = self.stacked.widget(i)
                if hasattr(screen, "setStatus"):
                    screen.setStatus(self.poller.status)
            self.update()
            for widget in self.findChildren(QWidget):
                widget.update()
        self.top_bar.setFocusInfo(state)

    def showConfirm(self, request):
        self.closeConfirm()
        self._confirm = ConfirmDialog(request)
        self._confirm.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        self._confirm.move(screen.center() - self._confirm.rect().center())
        self._confirm.show()
        self._confirm.raise_()
        self._confirm.activateWindow()

    def closeConfirm(self):
        if self._confirm is not None:
            self._confirm.close()
            self._confirm = None

    def restNow(self):
        # Luxur heard "rest": stop listening and minimize. The wake listener
        # brings the window back on "LUXUR" / "wake up" / three claps.
        SetMicrophoneStatus("False")
        self.showMinimized()


def GraphicalUserInterface():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(BuildStylesheet())
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    GraphicalUserInterface()
