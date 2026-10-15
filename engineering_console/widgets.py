"""Custom-painted instrument gauges (QPainter, no images, no QML)."""
import math
import time
from collections import deque

from .platform_info import MONO
from .nainfo import classify, wrap_by_width, NO_REASON
from PySide6.QtCore import Qt, QRectF, QPointF, QEvent, QMimeData, Signal
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QDrag,
                           QPainterPath, QPolygonF, QLinearGradient, QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QInputDialog, QToolTip, QSizePolicy

COLOR_KEYS = [("bg", "Background"), ("panel", "Panel"), ("edge", "Border"), ("track", "Gauge track"),
              ("accent", "Main accent"), ("warn", "Warning"), ("crit", "Critical"), ("text", "Text"),
              ("dim", "Secondary text"), ("needle", "Needle"), ("tick", "Tick marks"), ("glow", "Glow")]


def _t(bg, panel, edge, track, accent, warn, crit, text, dim, glow=True, skin="classic"):
    return dict(bg=bg, panel=panel, edge=edge, track=track, accent=accent, warn=warn, crit=crit, text=text,
                dim=dim, needle=text, tick=text, glow=accent, glow_on=glow, skin=skin)


THEMES = {
    "Engineering": _t("#060a10", "#0c131d", "#1b2a3b", "#15212f", "#22d3ee", "#ffb020", "#ff3b3b", "#e6f6ff", "#5d7a93"),
    "Tactical": _t("#050704", "#0b0f08", "#22301a", "#142010", "#46ff6e", "#ffc400", "#ff4040", "#d6ffd6", "#5f7f5a"),
    "Minimal": _t("#121416", "#1a1d20", "#2b3035", "#262a2e", "#cfd8e3", "#e5b567", "#e06c75", "#eceff4", "#7b8591", False),
    "High Contrast": _t("#000000", "#000000", "#ffffff", "#303030", "#ffee00", "#ff9500", "#ff2d2d", "#ffffff", "#c8c8c8", False),
    "Gold": _t("#0b0905", "#15110a", "#3a2f14", "#231c0d", "#ffc83d", "#ff8a1f", "#ff3b30", "#fff3d6", "#9a8650"),
    "Red Alert": _t("#0a0305", "#140508", "#3d0f14", "#240a0d", "#ff2d3d", "#ff9a1f", "#ffffff", "#ffe3e3", "#9a5a5f"),
    "Ice": _t("#050b10", "#0a1620", "#1c3548", "#102331", "#8fe3ff", "#ffd27a", "#ff6b7a", "#eaf8ff", "#6f93a8"),
    # Apple: a full light/silver skin (see engineering_console/skins/apple.py); colours below are editable like any theme
    "Apple": dict(_t("#f2f2f7", "#ffffff", "#d9d9de", "#e5e5ea", "#007aff", "#ff9500", "#ff3b30", "#1d1d1f", "#6e6e73",
                     False, "apple"), needle="#1d1d1f", tick="#8e8e93", glow="#007aff"),
    # Apple Dark: the same Apple skin in dark mode (system dark palette). Apple above stays the light default.
    "Apple Dark": dict(_t("#000000", "#1c1c1e", "#38383a", "#3a3a3c", "#0a84ff", "#ff9f0a", "#ff453a", "#f5f5f7", "#98989d",
                          False, "apple"), needle="#f5f5f7", tick="#8e8e93", glow="#0a84ff"),
}


REMOVED_THEMES = ("Amber", "Monochrome")      # dropped in 3.0; saved settings that name them fall back to Engineering
GAUGE_MIME = "application/x-engineering-console-gauge"
DRAG_START_PX = 8


def add_menu_action(menu, text, fn):
    """menu.addAction(text) + connect - spelled out because the (text, callable) overload differs between Qt bindings."""
    act = menu.addAction(text)
    act.triggered.connect(lambda _checked=False: fn())
    return act


def full_theme(d):
    """Fill any missing keys (older saved themes) from the default."""
    return {**THEMES["Engineering"], **(d or {})}


def nice_ceil(x):
    if x <= 0:
        return 1.0
    base = 10 ** math.floor(math.log10(x))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if x <= m * base:
            return m * base
    return 10 * base


FONT_STACK = [MONO]
SANS = False


def set_font_stack(stack):
    """Called by the app when the skin changes. A single monospace family (the default) behaves
    exactly as before; multi-family stacks are sans-serif skins."""
    global FONT_STACK, SANS
    FONT_STACK, SANS = list(stack), len(stack) > 1


def mkfont(px, bold=False, spacing=0.0):
    f = QFont(FONT_STACK[0])
    if SANS and hasattr(f, "setFamilies"):
        f.setFamilies(FONT_STACK)
    f.setStyleHint(QFont.StyleHint.SansSerif if SANS else QFont.StyleHint.Monospace)
    f.setPixelSize(max(6, int(px)))
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def qcol(hexstr, alpha=255):
    c = QColor(hexstr)
    c.setAlpha(int(alpha))
    return c


class Gauge(QWidget):
    supports_na_overlay = True
    changed = Signal()
    action = Signal(str)
    hide_requested = Signal(str)

    def __init__(self, mdef, theme, hist_len=240, parent=None):
        super().__init__(parent)
        self.m = mdef
        self.theme = theme
        self.mode = "analog"
        self.override = False           # per-gauge mode differs from the global one
        self.value = None
        self.frac = 0.0                 # animated needle position (0..1)
        self.target = 0.0
        self.hist = deque(maxlen=hist_len)
        self.peak = self.vmin = None
        self.lo, self.hi, self.floor = mdef.lo, mdef.hi, mdef.floor
        self.warn, self.crit = mdef.warn, mdef.crit
        self.manual_max = None
        self.pinned = False
        self.unit = mdef.unit
        self.reason = ""
        self.sample_s = 0.5
        self.alpha = 255
        self.cores = None
        self.skin = None                # None = original built-in painting; set by the app for skins that paint gauges
        self.fs = 1.0                   # font/size scale (low-resolution mode)
        self.lowres = False
        self.note = ""                  # short status shown under a live value (e.g. TESTING...)
        self.extra = ""                 # extra tooltip text
        self.tf = False                 # temperature gauge currently showing Fahrenheit
        self.na_overlay = True          # animated "why is this N/A" cover (View > N/A gauges)
        self._na_frame = 0
        self._press_pos = None          # left-button press position, for drag-to-reorder
        self._dragging = False
        self.setMinimumSize(190, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ----- data -----
    def set_hist_len(self, n):
        self.hist = deque(self.hist, maxlen=max(10, int(n)))

    def reset(self):
        self.hist.clear()
        self.peak = self.vmin = None
        self.update()

    def set_temp_unit(self, fahrenheit):
        """Convert a temperature gauge (scale, thresholds, history) between degC and degF."""
        if fahrenheit == self.tf or self.m.unit != "°C":
            return
        if fahrenheit:
            cv = lambda v: None if v is None else v * 9 / 5 + 32
        else:
            cv = lambda v: None if v is None else (v - 32) * 5 / 9
        self.lo, self.hi, self.floor = cv(self.lo), cv(self.hi), cv(self.floor)
        self.warn, self.crit, self.manual_max = cv(self.warn), cv(self.crit), cv(self.manual_max)
        self.peak, self.vmin, self.value = cv(self.peak), cv(self.vmin), cv(self.value)
        self.hist = deque([cv(x) for x in self.hist], maxlen=self.hist.maxlen)
        self.unit = "°F" if fahrenheit else "°C"
        self.tf = fahrenheit
        span = self.hi - self.lo
        if self.value is not None and span > 0:
            self.target = min(1.0, max(0.0, (self.value - self.lo) / span))
        self.update()

    def set_value(self, v):
        self.value = v
        if v is None:
            self.target = 0.0
            return
        self.hist.append(v)
        self.peak = v if self.peak is None else max(self.peak, v)
        self.vmin = v if self.vmin is None else min(self.vmin, v)
        if self.manual_max:
            self.hi = self.manual_max
        elif self.m.auto:
            self.hi = nice_ceil(max(max(self.hist) * 1.15, self.floor, 1e-9))
        span = self.hi - self.lo
        self.target = min(1.0, max(0.0, (v - self.lo) / span)) if span > 0 else 0.0

    def state(self):
        v = self.value
        if v is None:
            return "na"
        if self.crit is not None and v >= self.crit:
            return "crit"
        if self.warn is not None and v >= self.warn:
            return "warn"
        return "ok"

    def na_overlay_active(self):
        return (self.na_overlay and self.value is None and self.supports_na_overlay and self.m.key != "cpu_cores")

    def paint_na_overlay(self, p):
        """Cover for a gauge that has no data: dimmed card, drifting hazard stripes, a slow scan line, a pulsing warning
        triangle, a headline (permission / setup / error / waiting / not exposed) and the sensor's own reason text.
        Drawn only from theme colours, so it matches every theme (including Apple and Apple Dark)."""
        T, now = self.theme, time.monotonic()
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        kind, title = classify(self.reason)
        accent = T["crit"] if kind == "error" else T["warn"]
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(T["panel"], 226))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QPen(qcol(accent, 30), 6))                                  # hazard stripes drifting sideways
        step, off = 22.0, (now * 12.0) % 22.0
        x = r.left() - r.height() + off
        while x < r.right():
            p.drawLine(QPointF(x, r.bottom()), QPointF(x + r.height(), r.top()))
            x += step
        sy = r.top() + ((now * 0.30) % 1.0) * r.height()                     # slow scan line
        p.setPen(QPen(qcol(T["accent"], 80), 2))
        p.drawLine(QPointF(r.left(), sy), QPointF(r.right(), sy))
        p.restore()
        p.setPen(QPen(qcol(accent, 150), 1.4, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 10, 10)
        # the gauge's own name stays readable on top of the cover
        w, h = r.width(), r.height()
        p.setFont(mkfont(11 * self.fs, True, 1.6 * self.fs))
        p.setPen(qcol(T["text"], 230))
        name = QFontMetrics(p.font()).elidedText(self.title_text(), Qt.TextElideMode.ElideRight, int(w - 14))
        p.drawText(QRectF(r.left() + 7, r.top() + 4, w - 14, 18 * self.fs), Qt.AlignmentFlag.AlignCenter, name)
        # warning triangle, pulsing
        s = max(12.0, min(w, h) * 0.12)
        cx, cy = r.center().x(), r.top() + 26 * self.fs + s * 0.75
        pulse = 0.5 + 0.5 * math.sin(now * 3.2)
        tri = QPolygonF([QPointF(cx, cy - s * 0.62), QPointF(cx + s * 0.72, cy + s * 0.5), QPointF(cx - s * 0.72, cy + s * 0.5)])
        p.setPen(QPen(qcol(accent, 150 + 105 * pulse), max(1.6, s * 0.12), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(qcol(accent, 40 + 50 * pulse))
        p.drawPolygon(tri)
        p.setPen(qcol(accent, 150 + 105 * pulse))
        p.setFont(mkfont(s * 0.8, True))
        p.drawText(QRectF(cx - s, cy - s * 0.2, 2 * s, s * 0.8), Qt.AlignmentFlag.AlignCenter, "!")
        # headline + reason
        y = cy + s * 0.75
        p.setPen(qcol(accent))
        hp = max(8.0, min(w * 0.075, h * 0.062))
        p.setFont(mkfont(hp, True, 1.0))
        p.drawText(QRectF(r.left() + 6, y, w - 12, hp * 1.6), Qt.AlignmentFlag.AlignCenter, title)
        y += hp * 1.8
        bp = max(8.0, min(w * 0.058, h * 0.05))
        f = mkfont(bp)
        p.setFont(f)
        p.setPen(qcol(T["text"], 235))
        fm = QFontMetrics(f)
        room = int(max(1, (r.bottom() - 8 - y) // (bp * 1.3)))
        for line in wrap_by_width(self.reason or NO_REASON, fm.horizontalAdvance, w - 18, min(6, room)):
            p.drawText(QRectF(r.left() + 9, y, w - 18, bp * 1.3), Qt.AlignmentFlag.AlignCenter, line)
            y += bp * 1.3

    def tick(self):
        """Advance the needle animation. Returns True if a repaint is needed."""
        if self.na_overlay_active():                    # the N/A cover animates at ~15 fps
            self._na_frame += 1
            return self._na_frame % 2 == 0
        d = self.target - self.frac
        if abs(d) < 0.0004:
            moved = self.frac != self.target
            self.frac = self.target
            return moved or self.state() == "crit"
        self.frac += d * 0.22
        return True

    def fmt(self, v):
        if v is None:
            return "N/A"
        return f"{v:.0f}" if abs(v) >= 1000 else f"{v:.{self.m.dec}f}"

    def glow(self):
        return bool(self.theme.get("glow_on", True)) and not self.lowres

    def glow_hex(self):
        return self.theme["glow"] if self.state() == "ok" else self.color().name()

    def title_text(self):
        return ("★ " if self.pinned else "") + self.m.label

    def color(self):
        T, s = self.theme, self.state()
        return qcol({"ok": T["accent"], "warn": T["warn"], "crit": T["crit"], "na": T["dim"]}[s])

    # ----- interaction -----
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        """Hold the left button and drag: the grid shows where the gauge will land and reorders on release."""
        if (self._press_pos is not None and (e.buttons() & Qt.MouseButton.LeftButton) and not self._dragging
                and (e.position().toPoint() - self._press_pos).manhattanLength() >= max(DRAG_START_PX,
                                                                                         QApplication.startDragDistance())):
            self._dragging = True
            try:
                self._start_drag()
            finally:
                self._dragging = False
                self._press_pos = None
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def _start_drag(self):
        mime = QMimeData()
        mime.setData(GAUGE_MIME, self.m.key.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pix = self.grab()
        small = pix.scaled(max(60, pix.width() // 2), max(60, pix.height() // 2), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(small)
        drag.setHotSpot(small.rect().center())
        drag.exec(Qt.DropAction.MoveAction)

    def mouseDoubleClickEvent(self, e):
        self.mode = "digital" if self.mode == "analog" else "analog"
        self.override = True
        self.update()
        self.changed.emit()

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            QToolTip.showText(e.globalPos(), self.tooltip_text() + ("\n" + self.extra if self.extra else ""), self)
            return True
        return super().event(e)

    def tooltip_text(self):
        m = self.m
        lines = [f"{m.label}  [{m.group}]"]
        if self.value is None:
            lines += ["Current: N/A", self.reason or "Sensor not available"]
            return "\n".join(lines)
        lines.append(f"Current: {self.fmt(self.value)} {self.unit}")
        if self.hist:
            avg = sum(self.hist) / len(self.hist)
            lines.append(f"Min {self.fmt(self.vmin)}  Avg {self.fmt(avg)}  Peak {self.fmt(self.peak)}")
        lines.append(f"Scale: {self.fmt(self.lo)} - {self.fmt(self.hi)} {self.unit}"
                     + (" (auto)" if m.auto and not self.manual_max else ""))
        if self.warn is not None or self.crit is not None:
            lines.append(f"Warn >= {self.warn}   Critical >= {self.crit}")
        lines.append(f"History: {len(self.hist) * self.sample_s:.0f}s    Double-click: analog/digital")
        return "\n".join(lines)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        if self.m.key == "speed_test":
            add_menu_action(menu, "Run speed test now", lambda: self.action.emit("speed"))
            add_menu_action(menu, "Cancel speed test", lambda: self.action.emit("speed_cancel"))
            menu.addSeparator()
        add_menu_action(menu, "Switch to digital" if self.mode == "analog" else "Switch to analog",
                        lambda: self.mouseDoubleClickEvent(None))
        add_menu_action(menu, "Unpin" if self.pinned else "Pin to front", self._toggle_pin)
        menu.addSeparator()
        add_menu_action(menu, "Set warning level...", lambda: self._ask("warn"))
        add_menu_action(menu, "Set critical level...", lambda: self._ask("crit"))
        if self.m.auto:
            add_menu_action(menu, "Set maximum...", lambda: self._ask("max"))
            add_menu_action(menu, "Auto maximum", self._auto_max)
        add_menu_action(menu, "Reset peak / min", self.reset)
        menu.addSeparator()
        add_menu_action(menu, "Hide gauge", lambda: self.hide_requested.emit(self.m.key))
        menu.exec(e.globalPos())

    def _toggle_pin(self):
        self.pinned = not self.pinned
        self.changed.emit()

    def _auto_max(self):
        self.manual_max = None
        self.changed.emit()

    def _ask(self, what):
        cur = {"warn": self.warn, "crit": self.crit, "max": self.hi}[what]
        v, ok = QInputDialog.getDouble(self, self.m.label, {"warn": "Warning level", "crit": "Critical level",
                                                              "max": "Scale maximum"}[what] + f" ({self.unit}):",
                                       cur or 0.0, 0.0, 1e9, 2)
        if ok:
            if what == "max":
                self.manual_max = v if v > 0 else None
            else:
                setattr(self, what, v if v > 0 else None)
            self.changed.emit()
            self.update()

    # ----- painting -----
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.skin is not None:
            self.skin.paint_gauge(self, p)
            if self.na_overlay_active():
                self.paint_na_overlay(p)
            return
        T, st = self.theme, self.state()
        col = self.color()
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        # card
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(T["panel"], self.alpha))
        p.drawRoundedRect(rect, 10, 10)
        if st == "crit":
            a = 140 + 115 * (0.5 + 0.5 * math.sin(time.monotonic() * 8))
            p.setPen(QPen(qcol(T["crit"], a), 2))
        elif st == "warn":
            p.setPen(QPen(qcol(T["warn"], 200), 1.6))
        else:
            p.setPen(QPen(qcol(T["edge"]), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 10, 10)
        # title
        p.setFont(mkfont(11 * self.fs, True, 1.6 * self.fs))
        p.setPen(qcol(T["dim"]))
        title = QFontMetrics(p.font()).elidedText(self.title_text(), Qt.TextElideMode.ElideRight, int(rect.width() - 14))
        p.drawText(QRectF(rect.left() + 7, rect.top() + 4, rect.width() - 14, 18 * self.fs), Qt.AlignmentFlag.AlignCenter, title)
        th = int(24 * self.fs)
        body = QRectF(rect.left() + 6, rect.top() + th, rect.width() - 12, rect.height() - th - 6)
        self._paint_body(p, body, col)
        if self.m.key != "cpu_cores" and not self.m.key.endswith("_panel"):
            txt0 = self.reason if self.value is None else self.note
            if txt0:
                p.setFont(mkfont(9 * self.fs))
                p.setPen(qcol(T["dim"]) if self.value is None else col)
                txt = QFontMetrics(p.font()).elidedText(txt0, Qt.TextElideMode.ElideRight, int(rect.width() - 16))
                p.drawText(QRectF(rect.left() + 8, rect.bottom() - 18, rect.width() - 16, 14), Qt.AlignmentFlag.AlignCenter, txt)
        if self.na_overlay_active():
            self.paint_na_overlay(p)

    def _paint_body(self, p, body, col):
        if self.m.key == "cpu_cores":
            self._paint_cores(p, body)
        elif self.mode == "analog":
            self._paint_analog(p, body, col)
        else:
            self._paint_digital(p, body, col)

    def _arc(self, p, c, r, f0, f1, pen):
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int(-(135 + 270 * f0) * 16),
                  int(-270 * (f1 - f0) * 16))

    def _ang(self, c, r, f):
        a = math.radians(135 + 270 * f)
        return QPointF(c.x() + r * math.cos(a), c.y() + r * math.sin(a))

    def _tick_label(self, v):
        if abs(self.hi) >= 10000:
            return f"{v / 1000:g}k"
        return f"{v:.0f}" if self.hi >= 10 else f"{v:.1f}"

    def _paint_analog(self, p, body, col):
        T = self.theme
        side = min(body.width(), body.height())
        r = side / 2 * 0.97
        c = body.center()
        lw = r * 0.085
        # dial face
        g = QRadialGradient(c, r)
        g.setColorAt(0, qcol(T["bg"], min(255, self.alpha)))
        g.setColorAt(1, qcol(T["panel"], self.alpha))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, r * 0.97, r * 0.97)
        ra = r * 0.93 - lw / 2
        self._arc(p, c, ra, 0, 1, QPen(qcol(T["track"]), lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        # value arc with glow
        if self.value is not None and self.frac > 0.002:
            if self.glow():
                for w, a in ((lw * 2.4, 22), (lw * 1.6, 44)):
                    self._arc(p, c, ra, 0, self.frac, QPen(qcol(self.glow_hex(), a), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            self._arc(p, c, ra, 0, self.frac, QPen(col, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        # warning / critical zone strip
        rz = ra - lw * 0.95
        span = (self.hi - self.lo) or 1
        wf = None if self.warn is None else min(1, max(0, (self.warn - self.lo) / span))
        cf = None if self.crit is None else min(1, max(0, (self.crit - self.lo) / span))
        zp = lambda cc: QPen(qcol(cc, 210), lw * 0.32, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
        if wf is not None and wf < 1:
            self._arc(p, c, rz, wf, cf if cf is not None else 1, zp(T["warn"]))
        if cf is not None and cf < 1:
            self._arc(p, c, rz, cf, 1, zp(T["crit"]))
        # ticks + labels
        ro = rz - lw * 0.55
        for i in range(41):
            f, major = i / 40, i % 4 == 0
            ln = r * (0.10 if major else 0.045)
            p.setPen(QPen(qcol(T["tick"], 230) if major else qcol(T["dim"], 200), 1.6 if major else 1))
            p.drawLine(self._ang(c, ro, f), self._ang(c, ro - ln, f))
        if r >= 70:
            p.setFont(mkfont(r * 0.092))
            p.setPen(qcol(T["dim"]))
            step = 4 if r >= 105 else 8
            for i in range(0, 41, step):
                f = i / 40
                pt = self._ang(c, ro - r * 0.23, f)
                p.drawText(QRectF(pt.x() - 25, pt.y() - 8, 50, 16), Qt.AlignmentFlag.AlignCenter,
                           self._tick_label(self.lo + span * f))
        # peak / min markers
        if self.value is not None and len(self.hist) > 3:
            for val, cc in ((self.peak, T["warn"]), (self.vmin, T["dim"])):
                f = min(1, max(0, (val - self.lo) / span))
                p.save()
                p.translate(c)
                p.rotate(135 + 270 * f)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcol(cc, 230))
                p.drawPolygon(QPolygonF([QPointF(r * 0.995, 0), QPointF(r * 0.915, -r * 0.035),
                                         QPointF(r * 0.915, r * 0.035)]))
                p.restore()
        # needle
        p.save()
        p.translate(c)
        p.rotate(135 + 270 * self.frac)
        nc = qcol(T["needle"]) if self.value is not None else qcol(T["dim"])
        if self.glow() and self.value is not None:
            p.setPen(QPen(qcol(self.glow_hex(), 50), r * 0.04, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(0, 0), QPointF(r * 0.74, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(nc)
        p.drawPolygon(QPolygonF([QPointF(-r * 0.15, -r * 0.022), QPointF(r * 0.76, -r * 0.006),
                                 QPointF(r * 0.76, r * 0.006), QPointF(-r * 0.15, r * 0.022)]))
        p.setBrush(qcol(T["edge"]))
        p.setPen(QPen(col, 1.5))
        p.drawEllipse(QPointF(0, 0), r * 0.075, r * 0.075)
        p.restore()
        # value + unit in the open gap at the bottom of the dial
        txt = self.fmt(self.value)
        p.setPen(qcol(T["text"]) if self.value is not None else qcol(T["dim"]))
        p.setFont(mkfont(r * (0.27 if len(txt) < 6 else 0.2), True))
        p.drawText(QRectF(c.x() - r * 0.55, c.y() + r * 0.33, r * 1.1, r * 0.3), Qt.AlignmentFlag.AlignCenter, txt)
        p.setPen(col)
        p.setFont(mkfont(r * 0.12, True, 1))
        p.drawText(QRectF(c.x() - r * 0.5, c.y() + r * 0.64, r, r * 0.17), Qt.AlignmentFlag.AlignCenter, self.unit)

    def _paint_digital(self, p, body, col):
        T = self.theme
        txt = self.fmt(self.value)
        size = min(body.height() * 0.36, 200)
        f = mkfont(size, True)
        while size > 14 and QFontMetrics(f).horizontalAdvance(txt) > body.width() * 0.88:
            size *= 0.9
            f = mkfont(size, True)
        p.setFont(f)
        tr = QRectF(body.left(), body.top(), body.width(), size * 1.3)
        if self.glow() and self.value is not None:
            p.setPen(qcol(self.glow_hex(), 45))
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                p.drawText(tr.translated(dx, dy), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, txt)
        p.setPen(qcol(T["text"]) if self.value is not None else qcol(T["dim"]))
        p.drawText(tr, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, txt)
        y = body.top() + size * 1.3
        p.setFont(mkfont(13, True, 1.5))
        p.setPen(col)
        p.drawText(QRectF(body.left(), y, body.width(), 18), Qt.AlignmentFlag.AlignHCenter, self.unit)
        # percentage bar
        y += 24
        bar = QRectF(body.left() + 10, y, body.width() - 20, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(T["track"]))
        p.drawRoundedRect(bar, 4, 4)
        if self.value is not None and self.frac > 0.003:
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * self.frac, bar.height()), 4, 4)
        # history graph
        gtop, gbot = y + 16, body.bottom() - 20
        if gbot - gtop > 24 and len(self.hist) > 2:
            gr = QRectF(body.left() + 10, gtop, body.width() - 20, gbot - gtop)
            span = (self.hi - self.lo) or 1
            data = list(self.hist)
            step = max(1, len(data) // max(1, int(gr.width())))
            data = data[::step]
            path = QPainterPath()
            n = len(data)
            for i, v in enumerate(data):
                x = gr.left() + gr.width() * i / max(1, n - 1)
                yy = gr.bottom() - gr.height() * min(1, max(0, (v - self.lo) / span))
                path.moveTo(x, yy) if i == 0 else path.lineTo(x, yy)
            fill = QPainterPath(path)
            fill.lineTo(gr.right(), gr.bottom())
            fill.lineTo(gr.left(), gr.bottom())
            fill.closeSubpath()
            lg = QLinearGradient(gr.topLeft(), gr.bottomLeft())
            lg.setColorAt(0, qcol(col.name(), 70))
            lg.setColorAt(1, qcol(col.name(), 4))
            p.setBrush(QBrush(lg))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(fill)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 1.6))
            p.drawPath(path)
        # footer
        if self.value is not None and self.peak is not None:
            p.setFont(mkfont(10))
            p.setPen(qcol(T["dim"]))
            p.drawText(QRectF(body.left(), body.bottom() - 16, body.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                       f"MIN {self.fmt(self.vmin)}   PEAK {self.fmt(self.peak)}   {len(self.hist) * self.sample_s:.0f}s")

    def _paint_cores(self, p, body):
        T = self.theme
        cores = self.cores or []
        n = len(cores)
        if n == 0:
            return
        lab_h = 16
        area = QRectF(body.left() + 6, body.top() + 4, body.width() - 12, body.height() - lab_h - 22)
        gap = 4
        cw = max(6, (area.width() - gap * (n - 1)) / n)
        segs = 14
        sh = area.height() / segs
        for i, u in enumerate(cores):
            x = area.left() + i * (cw + gap)
            lit = int(round(u / 100 * segs))
            for s in range(segs):
                y = area.bottom() - (s + 1) * sh
                frac = (s + 1) / segs
                cc = T["crit"] if frac > 0.9 else T["warn"] if frac > 0.7 else T["accent"]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcol(cc, 235 if s < lit else 32))
                p.drawRoundedRect(QRectF(x, y + 1, cw, sh - 2), 2, 2)
            p.setFont(mkfont(9))
            p.setPen(qcol(T["dim"]))
            p.drawText(QRectF(x - 4, area.bottom() + 2, cw + 8, lab_h), Qt.AlignmentFlag.AlignCenter, f"{i}")
        p.setFont(mkfont(11, True))
        p.setPen(qcol(T["text"]))
        avg = self.fmt(self.value)
        p.drawText(QRectF(body.left(), body.bottom() - 18, body.width(), 18), Qt.AlignmentFlag.AlignCenter,
                   f"{n} THREADS   AVG {avg}%")


class GpuPanel(Gauge):
    """One compact instrument cluster per GPU: load, temp, VRAM, power, clock, fan.
    Designed so ten of them fit on a single 1024x768 screen."""
    supports_na_overlay = False          # panels already show a reason per field

    def __init__(self, mdef, theme, dev, parent=None):
        super().__init__(mdef, theme, parent=parent)
        self.dev = dev
        self.f, self.rs, self.temp_f = {}, {}, False
        self.pmax, self.cmax = 25.0, 500.0
        self.setMinimumSize(190, 150)

    def title_text(self):
        return f"{'★ ' if self.pinned else ''}GPU {self.dev.index}  {self.dev.model}"

    def set_fields(self, f, rs, temp_f):
        self.f, self.rs, self.temp_f = f, rs, temp_f
        if f.get("power"):
            self.pmax = max(self.pmax, f["power"])
        if f.get("clock"):
            self.cmax = max(self.cmax, f["clock"], f.get("clock_max") or 0)

    def tooltip_text(self):
        d, f = self.dev, self.f
        lines = [f"GPU {d.index}: {d.vendor} {d.model}", f"Driver {d.driver}   PCI {d.pci or '?'}   ({d.kind})"]
        for name, label, unit in (("util", "Load", "%"), ("temp", "Temp", "°C"), ("vram_used", "VRAM used", "GB"),
                                  ("vram_total", "VRAM total", "GB"), ("clock", "GPU clock", "MHz"),
                                  ("mem_clock", "Mem clock", "MHz"), ("power", "Power", "W"), ("fan", "Fan", "%")):
            v = f.get(name)
            if name == "temp" and v is not None and self.temp_f:
                v, unit = v * 9 / 5 + 32, "°F"
            lines.append(f"{label}: " + (f"{v:.1f} {unit}" if v is not None else
                                         "N/A - " + (self.rs.get("_all") or self.rs.get(name, "not exposed"))))
        return "\n".join(lines)

    def _row(self, p, r, label, txt, frac, color):
        T = self.theme
        fpx = max(8, min(r.height() * 0.72, 12 * self.fs + 2))
        lw = fpx * 3.4
        p.setFont(mkfont(fpx, False, 0.5))
        p.setPen(qcol(T["dim"]))
        p.drawText(QRectF(r.left(), r.top(), lw, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        vw = fpx * 6.2
        p.setPen(qcol(T["text"]) if txt != "N/A" else qcol(T["dim"]))
        p.drawText(QRectF(r.right() - vw, r.top(), vw, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)
        bar = QRectF(r.left() + lw, r.center().y() - 3, max(10, r.width() - lw - vw - 6), 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(T["track"]))
        p.drawRoundedRect(bar, 3, 3)
        if frac > 0:
            p.setBrush(color)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(1.0, frac), bar.height()), 3, 3)

    def _paint_body(self, p, body, col):
        T, f = self.theme, self.f
        hb = max(26.0, body.height() * 0.30)
        txt = f"{self.value:.0f}%" if self.value is not None else "N/A"
        size = hb * 0.95
        font = mkfont(size, True)
        while size > 10 and QFontMetrics(font).horizontalAdvance(txt) > body.width() * 0.46:
            size *= 0.9
            font = mkfont(size, True)
        p.setFont(font)
        p.setPen(qcol(T["text"]) if self.value is not None else qcol(T["dim"]))
        p.drawText(QRectF(body.left() + 4, body.top(), body.width() * 0.5, hb), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)
        bar = QRectF(body.left() + body.width() * 0.52, body.top() + hb / 2 - 5, body.width() * 0.46, 10)
        p.setFont(mkfont(9 * self.fs, True, 1))
        p.setPen(qcol(T["dim"]))
        p.drawText(QRectF(bar.left(), bar.top() - 14 * self.fs, bar.width(), 13 * self.fs), Qt.AlignmentFlag.AlignLeft, "LOAD")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcol(T["track"]))
        p.drawRoundedRect(bar, 5, 5)
        if self.value is not None and self.frac > 0.003:
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * self.frac, bar.height()), 5, 5)
        # detail rows: only real values; N/A when the sensor does not exist
        acc, warn, crit = qcol(T["accent"]), qcol(T["warn"]), qcol(T["crit"])
        rows = []
        t = f.get("temp")
        if t is None:
            rows.append(("TEMP", "N/A", 0, qcol(T["dim"])))
        else:
            tc = crit if t >= 85 else warn if t >= 75 else acc
            rows.append(("TEMP", f"{t * 9 / 5 + 32:.0f}°F" if self.temp_f else f"{t:.0f}°C", t / 100, tc))
        u, tot = f.get("vram_used"), f.get("vram_total")
        if u is None or not tot:
            rows.append(("VRAM", "N/A", 0, qcol(T["dim"])))
        else:
            pc = u / tot * 100
            rows.append(("VRAM", f"{u:.1f}/{tot:.0f}G", pc / 100, crit if pc >= 95 else warn if pc >= 85 else acc))
        pw = f.get("power")
        rows.append(("PWR", "N/A" if pw is None else f"{pw:.0f}W", 0 if pw is None else pw / self.pmax,
                     qcol(T["dim"]) if pw is None else acc))
        ck = f.get("clock")
        rows.append(("CLK", "N/A" if ck is None else f"{ck:.0f}M", 0 if ck is None else ck / self.cmax,
                     qcol(T["dim"]) if ck is None else acc))
        fn = f.get("fan")
        rows.append(("FAN", "N/A" if fn is None else f"{fn:.0f}%", 0 if fn is None else fn / 100,
                     qcol(T["dim"]) if fn is None else acc))
        top = body.top() + hb + 4
        avail = body.bottom() - top
        n = max(1, min(len(rows), int(avail // 14)))
        rh = avail / n
        for i in range(n):
            self._row(p, QRectF(body.left() + 4, top + i * rh, body.width() - 8, rh), *rows[i])
