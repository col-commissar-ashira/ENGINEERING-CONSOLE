"""The "apple" skin: a complete Apple-style look in light (silver, default) and dark mode.

Everything specific to it lives in this file. It only changes how things are drawn -
never what is measured or how the app behaves. Colours come from the theme (the Apple
preset defines them), so the Appearance dialog still edits them.
"""
import math
import os
import time

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (QPainter, QColor, QPen, QFontMetrics, QPainterPath, QLinearGradient, QImage, QBrush)

from ..platform_info import cache_dir
from .base import Skin

STACK = [".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue", "Inter", "Segoe UI", "Cantarell", "Noto Sans", "DejaVu Sans"]
CSS_FAMILY = '".AppleSystemUIFont", "SF Pro Text", "SF Pro Display", "Helvetica Neue", "Inter", "Segoe UI", "Cantarell", "Noto Sans", sans-serif'


def _qcol(hexstr, alpha=255):
    c = QColor(hexstr)
    c.setAlpha(int(alpha))
    return c


def _asset_dir():
    return os.path.join(cache_dir(), "skin-apple")


def is_dark(T):
    """Dark mode = a dark window background (the Apple Dark preset, or any edited Apple theme with a dark background)."""
    h = T["bg"].lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b < 128


class AppleSkin(Skin):
    name = "apple"

    def font_stack(self):
        return list(STACK)

    def paints_gauges(self):
        return True

    # ------------------------------------------------------------ assets (switch images)
    def prepare(self, win):
        d = _asset_dir()
        try:
            os.makedirs(d, exist_ok=True)
            for dark, on in ((False, False), (False, True), (True, False), (True, True)):
                path = os.path.join(d, ("switch_on" if on else "switch_off") + ("_dark" if dark else "") + ".png")
                if os.path.exists(path):
                    continue
                img = QImage(76, 44, QImage.Format.Format_ARGB32)
                img.fill(Qt.GlobalColor.transparent)
                p = QPainter(img)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_qcol(("#30d158" if dark else "#34c759") if on else ("#39393d" if dark else "#e5e5ea")))
                p.drawRoundedRect(QRectF(0, 0, 76, 44), 22, 22)
                x = 36 if on else 4
                p.setBrush(_qcol("#000000", 40))
                p.drawEllipse(QRectF(x, 6, 36, 36))          # soft shadow under the knob
                p.setBrush(_qcol("#ffffff"))
                p.drawEllipse(QRectF(x, 4, 36, 36))
                p.end()
                img.save(path)
        except Exception:
            pass

    def _switch_css(self, T):
        d = _asset_dir().replace("\\", "/")
        sfx = "_dark" if is_dark(T) else ""
        off, on = os.path.join(d, f"switch_off{sfx}.png"), os.path.join(d, f"switch_on{sfx}.png")
        if not (os.path.exists(off) and os.path.exists(on)):
            return ""
        return (f'QCheckBox::indicator {{ width:38px; height:22px; image:url("{off}"); }} '
                f'QCheckBox::indicator:checked {{ image:url("{on}"); }}')

    # ------------------------------------------------------------ application stylesheet
    def stylesheet(self, T, dp):
        fs = dp["ssize"] + 2
        a = T["accent"]
        tint = "#24" + a[1:]            # accent at ~14% opacity (#AARRGGBB)
        hover = "#14" + T["dim"][1:]
        press = "#26" + T["dim"][1:]
        handle = "#80" + T["dim"][1:]
        pad = 4 if dp["scale"] < 0.7 else 7
        return f"""
        * {{ font-family:{CSS_FAMILY}; }}
        QMainWindow, QDialog, QMessageBox {{ background:{T['bg']}; }}
        QWidget {{ font-size:{fs}px; }}
        QLabel {{ color:{T['text']}; background:transparent; }}
        QCheckBox, QRadioButton {{ color:{T['text']}; spacing:10px; background:transparent; }}
        {self._switch_css(T)}
        QRadioButton::indicator {{ width:18px; height:18px; border-radius:9px; border:1.5px solid {T['edge']}; background:{T['panel']}; }}
        QRadioButton::indicator:checked {{ border:5px solid {a}; background:white; }}
        QMenuBar {{ background:{T['bg']}; color:{T['text']}; border-bottom:1px solid {T['edge']}; }}
        QMenuBar::item {{ padding:4px 10px; border-radius:5px; background:transparent; }}
        QMenuBar::item:selected {{ background:{tint}; color:{a}; }}
        QMenu {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']}; border-radius:8px; padding:5px; }}
        QMenu::item {{ padding:5px 24px 5px 12px; border-radius:5px; }}
        QMenu::item:selected {{ background:{a}; color:white; }}
        QMenu::separator {{ height:1px; background:{T['edge']}; margin:4px 8px; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:none; }}
        QScrollBar:vertical {{ background:transparent; width:12px; margin:2px; }}
        QScrollBar::handle:vertical {{ background:{handle}; border-radius:4px; min-height:36px; }}
        QScrollBar:horizontal {{ background:transparent; height:12px; margin:2px; }}
        QScrollBar::handle:horizontal {{ background:{handle}; border-radius:4px; min-width:36px; }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; width:0; height:0; }}
        QToolTip {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']}; padding:7px 9px; }}
        QPushButton {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']}; border-radius:8px;
            padding:{pad}px 15px; font-weight:600; }}
        QPushButton:hover {{ background:{hover}; }}
        QPushButton:pressed {{ background:{press}; }}
        QPushButton:default, QDialogButtonBox QPushButton:default {{ background:{a}; color:white; border:1px solid {a}; }}
        QPushButton#mode {{ background:{tint}; color:{a}; border:none; border-radius:{12 if dp['scale'] < 0.7 else 15}px;
            padding:{pad}px 18px; font-size:{fs}px; font-weight:600; min-width:{dp['btn']}px; }}
        QPushButton#mode:hover {{ background:#38{a[1:]}; }}
        QLineEdit, QComboBox, QSpinBox {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']}; border-radius:8px;
            padding:{pad}px 10px; selection-background-color:{a}; selection-color:white; }}
        QLineEdit:focus, QComboBox:focus {{ border:1.5px solid {a}; }}
        QSpinBox::up-button, QSpinBox::down-button {{ width:18px; border:none; background:transparent; }}
        QComboBox::drop-down {{ border:none; width:24px; }}
        QComboBox QAbstractItemView {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']};
            border-radius:8px; padding:4px; selection-background-color:{a}; selection-color:white; outline:0; }}
        QListWidget, QTableWidget, QPlainTextEdit {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']};
            border-radius:10px; padding:4px; selection-background-color:{a}; selection-color:white; outline:0; }}
        QListWidget::item {{ padding:{pad}px 8px; border-radius:6px; }}
        QListWidget::item:selected {{ background:{a}; color:white; }}
        QTableWidget {{ gridline-color:{T['edge']}; }}
        QHeaderView::section {{ background:{T['bg']}; color:{T['dim']}; border:0; border-bottom:1px solid {T['edge']};
            padding:6px 8px; font-weight:600; }}
        QSlider::groove:horizontal {{ height:4px; background:{T['track']}; border-radius:2px; }}
        QSlider::sub-page:horizontal {{ background:{a}; border-radius:2px; }}
        QSlider::handle:horizontal {{ width:20px; height:20px; margin:-8px 0; border-radius:10px; background:white;
            border:1px solid {T['edge']}; }}
        QStatusBar {{ background:{T['bg']}; color:{T['dim']}; border-top:1px solid {T['edge']}; }}
        """

    # ------------------------------------------------------------ header / banner
    def title_style(self, T, dp):
        return f"font-size:{dp['tsize'] + 2}px; font-weight:700; color:{T['text']};"

    def subtitle_style(self, T, dp):
        return f"font-size:{dp['ssize'] + 1}px; color:{T['dim']};"

    def clock_style(self, T, dp):
        return f"font-size:{dp['ssize'] + 5}px; font-weight:600; color:{T['dim']};"

    def status_style(self, alert, T, dp):
        if alert:
            return f"font-size:{dp['ssize'] + 4}px; font-weight:700; color:{T['crit']};"
        return f"font-size:{dp['ssize'] + 3}px; font-weight:600; color:{T['dim']};"

    def banner_style(self, bucket, dp):
        bg = "#ff3b30" if bucket else "#d92d20"
        return (f"background:{bg}; color:white; font-size:{dp['ssize'] + 4}px; font-weight:700; "
                "padding:10px 16px; border:none; border-radius:14px;")

    def paint_background(self, panel, p, win):
        T = win.theme
        top, bot = _qcol(T["panel"], win.bg_alpha), _qcol(T["bg"], win.bg_alpha)
        g = QLinearGradient(QPointF(0, 0), QPointF(0, panel.height()))
        g.setColorAt(0, top)
        g.setColorAt(0.35, bot)
        p.fillRect(panel.rect(), QBrush(g))

    # ------------------------------------------------------------ gauge painting
    def _font(self, px, bold=False):
        from ..widgets import mkfont
        return mkfont(px, bold)

    def _state_col(self, g):
        T = g.theme
        return _qcol({"ok": T["accent"], "warn": T["warn"], "crit": T["crit"], "na": T["dim"]}[g.state()])

    def paint_gauge(self, g, p):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        T, st, col, fs = g.theme, g.state(), self._state_col(g), g.fs
        rect = QRectF(g.rect()).adjusted(5, 4, -5, -9)
        if not g.lowres:                                     # soft layered shadow (deeper on dark backgrounds)
            p.setPen(Qt.PenStyle.NoPen)
            for dy, grow, a in (((1, 0, 60), (3, 1, 40), (6, 3, 24), (10, 5, 12)) if is_dark(T) else
                                ((1, 0, 22), (3, 1, 14), (6, 3, 8), (10, 5, 4))):
                p.setBrush(_qcol("#000000", a))
                p.drawRoundedRect(rect.translated(0, dy).adjusted(-grow, -grow, grow, grow), 16, 16)
        p.setBrush(_qcol(T["panel"], g.alpha))
        if st == "crit":
            a = 150 + 105 * (0.5 + 0.5 * math.sin(time.monotonic() * 8))
            p.setPen(QPen(_qcol(T["crit"], a), 2))
        elif st == "warn":
            p.setPen(QPen(_qcol(T["warn"], 220), 1.6))
        else:
            p.setPen(QPen(_qcol(T["edge"]), 1))
        p.drawRoundedRect(rect, 16, 16)
        # title (left aligned, like an Apple widget)
        p.setFont(self._font(12 * fs, True))
        p.setPen(_qcol(T["dim"]))
        title = QFontMetrics(p.font()).elidedText(g.title_text(), Qt.TextElideMode.ElideRight, int(rect.width() - 28))
        p.drawText(QRectF(rect.left() + 14, rect.top() + 7, rect.width() - 28, 18 * fs), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        top = int(30 * fs)
        body = QRectF(rect.left() + 8, rect.top() + top, rect.width() - 16, rect.height() - top - 8)
        if g.m.key == "cpu_cores":
            g._paint_cores(p, body)
        elif g.m.key.endswith("_panel"):
            g._paint_body(p, body, col)
        elif g.mode == "analog":
            self._analog(g, p, body, col)
        else:
            self._digital(g, p, body, col)
        if g.m.key != "cpu_cores" and not g.m.key.endswith("_panel"):
            txt0 = g.reason if g.value is None else g.note
            if txt0:
                p.setFont(self._font(10 * fs))
                p.setPen(_qcol(T["dim"]) if g.value is None else col)
                txt = QFontMetrics(p.font()).elidedText(txt0, Qt.TextElideMode.ElideRight, int(rect.width() - 24))
                p.drawText(QRectF(rect.left() + 12, rect.bottom() - 20, rect.width() - 24, 15), Qt.AlignmentFlag.AlignCenter, txt)

    def _analog(self, g, p, body, col):
        T = g.theme
        side = min(body.width(), body.height())
        r = side / 2 * 0.97
        c = body.center()
        lw = r * 0.10
        ra = r * 0.84
        span = (g.hi - g.lo) or 1
        g._arc(p, c, ra, 0, 1, QPen(_qcol(T["track"]), lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if g.value is not None and g.frac > 0.004:
            g._arc(p, c, ra, 0, g.frac, QPen(col, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # warning / critical zones: slim arcs just outside the ring
        ro = ra + lw * 0.95
        wf = None if g.warn is None else min(1, max(0, (g.warn - g.lo) / span))
        cf = None if g.crit is None else min(1, max(0, (g.crit - g.lo) / span))
        if wf is not None and wf < 1:
            g._arc(p, c, ro, wf, cf if cf is not None else 1, QPen(_qcol(T["warn"], 200), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        if cf is not None and cf < 1:
            g._arc(p, c, ro, cf, 1, QPen(_qcol(T["crit"], 220), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        # fine tick marks inside the ring
        ri = ra - lw * 0.95
        for i in range(41):
            f, major = i / 40, i % 4 == 0
            p.setPen(QPen(_qcol(T["tick"], 150 if major else 70), 1.2 if major else 0.8))
            p.drawLine(g._ang(c, ri, f), g._ang(c, ri - r * (0.06 if major else 0.03), f))
        if r >= 62:                                           # scale ends only: clean, like Apple gauges
            p.setFont(self._font(r * 0.085))
            p.setPen(_qcol(T["dim"]))
            for f, v in ((0, g.lo), (1, g.hi)):
                pt = g._ang(c, ra, f)
                p.drawText(QRectF(pt.x() - 30, pt.y() + lw * 0.8, 60, r * 0.13), Qt.AlignmentFlag.AlignCenter, g._tick_label(v))
        # peak / minimum markers
        if g.value is not None and len(g.hist) > 3:
            for val, cc in ((g.peak, T["warn"]), (g.vmin, T["dim"])):
                f = min(1, max(0, (val - g.lo) / span))
                pt = g._ang(c, ra, f)
                p.setPen(QPen(_qcol(T["panel"]), 1.5))
                p.setBrush(_qcol(cc))
                p.drawEllipse(pt, lw * 0.34, lw * 0.34)
        # slim needle + hub
        p.save()
        p.translate(c)
        p.rotate(135 + 270 * g.frac)
        nc = _qcol(T["needle"]) if g.value is not None else _qcol(T["dim"])
        p.setPen(QPen(nc, max(1.6, r * 0.028), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(r * 0.08, 0), QPointF(r * 0.60, 0))
        p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qcol("#000000", 28))
        p.drawEllipse(QPointF(c.x(), c.y() + r * 0.015), r * 0.075, r * 0.075)
        p.setBrush(_qcol(T["panel"]))
        p.setPen(QPen(_qcol(T["edge"]), 1))
        p.drawEllipse(c, r * 0.065, r * 0.065)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(c, r * 0.025, r * 0.025)
        # value + unit
        txt = g.fmt(g.value)
        p.setPen(_qcol(T["text"]) if g.value is not None else _qcol(T["dim"]))
        p.setFont(self._font(r * (0.27 if len(txt) < 6 else 0.2), True))
        p.drawText(QRectF(c.x() - r * 0.55, c.y() + r * 0.33, r * 1.1, r * 0.3), Qt.AlignmentFlag.AlignCenter, txt)
        p.setPen(_qcol(T["dim"]))
        p.setFont(self._font(r * 0.12))
        p.drawText(QRectF(c.x() - r * 0.5, c.y() + r * 0.64, r, r * 0.17), Qt.AlignmentFlag.AlignCenter, g.unit)

    def _digital(self, g, p, body, col):
        T = g.theme
        txt = g.fmt(g.value)
        size = min(body.height() * 0.34, 120)
        f = self._font(size, True)
        while size > 14 and QFontMetrics(f).horizontalAdvance(txt) > body.width() * 0.8:
            size *= 0.9
            f = self._font(size, True)
        p.setFont(f)
        p.setPen(_qcol(T["text"]) if g.value is not None else _qcol(T["dim"]))
        p.drawText(QRectF(body.left(), body.top(), body.width(), size * 1.25), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, txt)
        y = body.top() + size * 1.25
        p.setFont(self._font(max(11, size * 0.28)))
        p.setPen(_qcol(T["dim"]))
        p.drawText(QRectF(body.left(), y, body.width(), 18), Qt.AlignmentFlag.AlignHCenter, g.unit)
        y += 24
        bar = QRectF(body.left() + 12, y, body.width() - 24, 8)             # capsule progress bar
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qcol(T["track"]))
        p.drawRoundedRect(bar, 4, 4)
        if g.value is not None and g.frac > 0.003:
            p.setBrush(col)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(8, bar.width() * g.frac), bar.height()), 4, 4)
        gtop, gbot = y + 18, body.bottom() - 20
        if gbot - gtop > 24 and len(g.hist) > 2:
            gr = QRectF(body.left() + 12, gtop, body.width() - 24, gbot - gtop)
            span = (g.hi - g.lo) or 1
            data = list(g.hist)
            step = max(1, len(data) // max(1, int(gr.width())))
            data = data[::step]
            pts = [QPointF(gr.left() + gr.width() * i / max(1, len(data) - 1),
                           gr.bottom() - gr.height() * min(1, max(0, (v - g.lo) / span))) for i, v in enumerate(data)]
            path = QPainterPath(pts[0])
            for i in range(1, len(pts)):                                    # smooth curve through midpoints
                mid = QPointF((pts[i - 1].x() + pts[i].x()) / 2, (pts[i - 1].y() + pts[i].y()) / 2)
                path.quadTo(pts[i - 1], mid)
            path.lineTo(pts[-1])
            fill = QPainterPath(path)
            fill.lineTo(gr.right(), gr.bottom())
            fill.lineTo(gr.left(), gr.bottom())
            fill.closeSubpath()
            lg = QLinearGradient(gr.topLeft(), gr.bottomLeft())
            lg.setColorAt(0, _qcol(col.name(), 60))
            lg.setColorAt(1, _qcol(col.name(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(lg))
            p.drawPath(fill)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(pts[-1], 3.2, 3.2)
        if g.value is not None and g.peak is not None:
            p.setFont(self._font(10.5))
            p.setPen(_qcol(T["dim"]))
            p.drawText(QRectF(body.left(), body.bottom() - 16, body.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                       f"Min {g.fmt(g.vmin)}  ·  Peak {g.fmt(g.peak)}  ·  {len(g.hist) * g.sample_s:.0f}s")
