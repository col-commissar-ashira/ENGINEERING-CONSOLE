"""Engineering Console - main window, layout, profiles, appearance, red alert."""
import copy
import csv
import json
import logging
import math
import os
import sys
import time

import psutil
from PySide6.QtCore import Qt, QTimer, QByteArray, QProcess, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QIcon, QFont, QAction, QActionGroup
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
                               QLabel, QPushButton, QScrollArea, QDialog, QListWidget, QListWidgetItem,
                               QLineEdit, QDialogButtonBox, QPlainTextEdit, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QFileDialog, QMessageBox, QHeaderView, QColorDialog,
                               QComboBox, QInputDialog, QCheckBox, QSpinBox)

from . import __version__, logsetup
from .ordering import display_order, full_order, reorder
from .sensors import Telemetry, hardware_report
from . import widgets as widgets_mod
from .platform_info import (APP_NAME, APP_SLUG, config_dir, data_dir, migrate_legacy_config, MONO, IS_MAC, IS_WIN)
from .skins import skin_for
from .speedtest import SIZES, ContinuousConfig, fmt_bytes
from .widgets import (Gauge, GpuPanel, THEMES, REMOVED_THEMES, COLOR_KEYS, GAUGE_MIME, full_theme, add_menu_action)

APP_TITLE_SUFFIX = ""
CFG_VERSION = 3
NA_MODES = ("overlay", "hide", "plain")
NA_HIDE_AFTER_S = 20.0                   # a gauge must be N/A this long before "auto-hide" removes it (sensors start N/A)
NA_NEVER_HIDE = {"audio_in", "audio_out", "speed_test"}     # these only start/work while visible, so they must stay
CONF_DIR = config_dir()
CONF = os.path.join(CONF_DIR, "config.json")
LOG_DIR = os.path.join(data_dir(), "logs")
ICON = os.path.join(os.path.dirname(__file__), "icon.svg")
ALARM = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"

GAUGE_BASE = {"S": 150, "M": 215, "L": 300}
DENS = {   # density: size scale, font scale, margins, header fonts, mode-button width
    "normal": dict(scale=1.0, fs=1.0, margin=8, gap=8, tsize=22, ssize=11, btn=260),
    "compact": dict(scale=0.82, fs=0.92, margin=6, gap=6, tsize=18, ssize=10, btn=200),
    "lowres": dict(scale=0.62, fs=0.8, margin=3, gap=3, tsize=14, ssize=9, btn=140),
}
LEGACY = {"gpu_util": "gpu0_util", "gpu_temp": "gpu0_temp", "vram_pct": "gpu0_vram_pct", "vram_used": "gpu0_vram_used",
          "gpu_clock": "gpu0_clock", "gpu_mem_clock": "gpu0_mem_clock", "gpu_power": "gpu0_power", "gpu_fan": "gpu0_fan"}

MAC_ADMIN_TEXT = (
    "Engineering Console will now ask macOS for your administrator password.\n\n"
    "WHY: Apple only lets administrators run its built-in 'powermetrics' tool. It provides CPU and GPU power "
    "(watts), GPU clock and exact GPU load, CPU clocks, Apple's thermal-pressure level and, on Intel Macs, CPU/GPU "
    "temperature and fan speed. Without it those gauges show N/A.\n\n"
    "WHAT HAPPENS: powermetrics runs once per second and writes to a private file in your Caches folder. It stops "
    "automatically when you quit Engineering Console. Nothing is installed and no password is stored.")
MAC_INTRO_TEXT = (
    "Engineering Console can read most Mac sensors with no permission at all. Three features need your OK:\n\n"
    "1. Administrator password (Tools > Enable advanced Mac sensors): CPU/GPU power, GPU clock, thermal pressure.\n"
    "2. Microphone (macOS asks the first time the MIC INPUT gauge starts): it only measures the volume level; "
    "nothing is recorded.\n"
    "3. AUDIO OUT needs a free loopback input such as BlackHole 2ch, because macOS does not let apps listen to "
    "speaker output directly.\n\n"
    "Enable the administrator sensors now?")

DEFAULT_VISIBLE = ["cpu_util", "cpu_temp", "cpu_power", "ram_pct", "gpu0_util", "gpu0_temp", "gpu0_vram_pct",
                   "gpu0_power", "net_down", "net_up", "disk_read", "disk_write", "fan_rpm", "audio_out", "speed_test"]
WAR_THUNDER = ["gpu0_util", "gpu0_temp", "gpu0_power", "gpu0_vram_pct", "cpu_util", "cpu_temp", "ram_pct", "fps",
               "net_down", "net_up"]

DEFAULT_CFG = {
    "version": CFG_VERSION, "mode": "analog", "modes": {}, "visible": DEFAULT_VISIBLE, "order": [],
    "pinned": [], "thresholds": {}, "manual_max": {}, "theme": "Engineering", "theme_colors": None,
    "custom_themes": {}, "profile": "Default", "profiles": {}, "refresh_ms": 500, "history_s": 60,
    "net_bits": True, "temp_f": True, "cols": 0, "gauge_size": "M", "density": "auto", "geometry": "",
    "fullscreen": False, "always_on_top": False, "transparent": False, "compact": False,
    "first_run_done": False, "speed_auto": "manual", "mac_helper_auto": False, "mac_asked": False,
    "na_mode": "overlay", "speed_size": "quick", "speed_endpoints": [],
    "speed_continuous": {"interval_s": 60, "duration_min": 20, "max_tests": 0, "data_limit_mb": 500},
    "speed_traffic_total": {"down": 0, "up": 0},
    "alert": {"enabled": True, "sound": True, "sustain_s": 3, "soft_needed": 3,
              "cpu_temp_hard": 98, "gpu_temp_hard": 93, "ram_hard": 97, "vram_hard": 98,
              "cpu_util_soft": 95, "gpu_util_soft": 95, "ram_soft": 90, "vram_soft": 92,
              "cpu_temp_soft": 90, "gpu_temp_soft": 83, "swap_soft": 60, "disk_util_soft": 95},
}


def migrate_removed_themes(cfg):
    """Two themes were retired in 3.0 (see REMOVED_THEMES). Settings that still name them fall back to Engineering
    (a custom theme the user saved under one of those names is theirs and is left alone)."""
    gone = lambda name: name in REMOVED_THEMES and name not in cfg["custom_themes"]
    if gone(cfg["theme"]):
        cfg["theme"], cfg["theme_colors"] = "Engineering", None
    for p in cfg["profiles"].values():
        if isinstance(p, dict) and gone(p.get("theme")):
            p["theme"] = "Engineering"
            p.pop("theme_colors", None)


def sanitize_cfg(cfg):
    """A hand-edited or damaged config must not crash the app: wrong-typed values fall back to defaults."""
    for k, default in DEFAULT_CFG.items():
        if k not in cfg or type(cfg[k]) is not type(default) and not (isinstance(default, (int, float)) and isinstance(cfg[k], (int, float)) and not isinstance(cfg[k], bool)):
            if k in ("theme_colors", "geometry"):
                continue
            cfg[k] = json.loads(json.dumps(default))
    for k in ("visible", "order", "pinned"):
        cfg[k] = [x for x in cfg[k] if isinstance(x, str)]
    if cfg["speed_size"] not in SIZES:
        cfg["speed_size"] = "quick"
    if cfg["na_mode"] not in NA_MODES:
        cfg["na_mode"] = "overlay"
    sc, d = cfg["speed_continuous"], DEFAULT_CFG["speed_continuous"]
    for k, v in d.items():
        if not isinstance(sc.get(k), (int, float)) or sc[k] < 0:
            sc[k] = v
    return cfg


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    migrate_legacy_config()
    saved, existed = {}, os.path.exists(CONF)
    try:
        with open(CONF) as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            saved = {}
        for k, v in saved.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict) and k in ("alert", "speed_continuous", "speed_traffic_total"):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except Exception as e:
        if existed:
            logsetup.event(logging.getLogger("engineering_console.config"), "config.unreadable", logging.WARNING, error=repr(e))
    sanitize_cfg(cfg)
    for k in ("visible", "order", "pinned"):
        cfg[k] = [LEGACY.get(x, x) for x in cfg[k]]
    for k in ("modes", "thresholds", "manual_max"):
        cfg[k] = {LEGACY.get(x, x): v for x, v in cfg[k].items()}
    if existed and saved.get("version", 1) < 2:      # upgrade from v1: the new gauges the user asked for
        cfg["speed_auto"] = "launch"
        for k in ("audio_out", "audio_in", "speed_test"):
            if k not in cfg["visible"]:
                cfg["visible"].append(k)
    migrate_removed_themes(cfg)
    cfg["version"] = CFG_VERSION
    return cfg, existed


def balanced_cols(n):
    """Columns for n tiles: 1->1, 2->2, 4->2x2, 8->4x2, 10->5x2, 12->4x3 ..."""
    if n <= 3:
        return max(1, n)
    if n == 4:
        return 2
    if n <= 6:
        return 3
    if n <= 8:
        return 4
    if n <= 10:
        return 5
    if n <= 12:
        return 4
    return min(8, math.ceil(n / 3))


class ClickLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


class Panel(QWidget):
    """Central widget: themed background + pulsing red border during RED ALERT."""
    def __init__(self, win):
        super().__init__()
        self.win = win

    def paintEvent(self, e):
        p = QPainter(self)
        self.win.skin.paint_background(self, p, self.win)
        if self.win.alert_active:
            a = int(100 + 130 * (0.5 + 0.5 * math.sin(time.monotonic() * 7)))
            p.setPen(QPen(QColor(255, 25, 25, a), 12))
            p.drawRect(self.rect().adjusted(6, 6, -6, -6))


class DropMarker(QWidget):
    """Translucent frame shown over the cell where a dragged gauge will land (skin-independent)."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.color = "#22d3ee"
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        c.setAlpha(46)
        p.setBrush(c)
        c.setAlpha(230)
        p.setPen(QPen(c, 3))
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 12, 12)


class GaugeGrid(QWidget):
    reorder_requested = Signal(str, int)        # gauge key, target index in the displayed order

    def __init__(self):
        super().__init__()
        self.setObjectName("grid")
        self.setAcceptDrops(True)
        self.marker = DropMarker(self)
        self.scroll = None                       # the QScrollArea holding this grid (auto-scroll while dragging)
        self.lay = QGridLayout(self)
        self.items, self._sig = [], None
        self.cell_w = self.cell_h = 215
        self.margin = self.gap = 8
        self.cols_cfg = 0          # 0 = by width, -1 = balanced by tile count, >0 = fixed
        self.set_metrics(8, 8)

    def set_metrics(self, margin, gap):
        self.margin, self.gap = margin, gap
        self.lay.setContentsMargins(margin, margin, margin, margin)
        self.lay.setSpacing(gap)
        self._sig = None

    def set_items(self, widgets):
        self.items, self._sig = widgets, None
        self.relayout()

    # ---- drag & drop reordering (hold the left button on a gauge, drag, release)
    def target_index(self, pos):
        """Index of the cell under `pos`, or the nearest one when the pointer is in a gap."""
        best, bd = None, None
        for i, w in enumerate(self.items):
            g = w.geometry()
            if g.contains(pos):
                return i
            c = g.center()
            d = (c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2
            if bd is None or d < bd:
                best, bd = i, d
        return best

    def _autoscroll(self, pos):
        if self.scroll is None:
            return
        bar, vp = self.scroll.verticalScrollBar(), self.scroll.viewport()
        y = self.mapTo(vp, pos).y()
        edge = 36
        if y < edge:
            bar.setValue(bar.value() - 24)
        elif y > vp.height() - edge:
            bar.setValue(bar.value() + 24)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(GAUGE_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(GAUGE_MIME) or not self.items:
            return
        pos = e.position().toPoint()
        i = self.target_index(pos)
        if i is not None:
            self.marker.setGeometry(self.items[i].geometry().adjusted(-2, -2, 2, 2))
            self.marker.show()
            self.marker.raise_()
        self._autoscroll(pos)
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.marker.hide()

    def dropEvent(self, e):
        self.marker.hide()
        if not e.mimeData().hasFormat(GAUGE_MIME):
            return
        i = self.target_index(e.position().toPoint())
        if i is not None:
            self.reorder_requested.emit(bytes(e.mimeData().data(GAUGE_MIME).data()).decode("utf-8"), i)
            e.acceptProposedAction()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def relayout(self):
        n = len(self.items)
        maxc = max(1, (self.width() - 2 * self.margin + self.gap) // (self.cell_w + self.gap))
        if self.cols_cfg > 0:
            cols = self.cols_cfg
        elif self.cols_cfg < 0:
            cols = min(balanced_cols(n), maxc)
        else:
            cols = min(max(n, 1), maxc)
        cols = max(1, min(cols, max(n, 1)))
        sig = (cols, tuple(id(w) for w in self.items), self.cell_w, self.gap)
        if sig == self._sig:
            return
        self._sig = sig
        while self.lay.count():
            self.lay.takeAt(0)
        for i, w in enumerate(self.items):
            self.lay.addWidget(w, i // cols, i % cols)
            w.show()
        rows = -(-n // cols) if n else 0
        for c in range(24):
            self.lay.setColumnStretch(c, 1 if c < cols else 0)
            self.lay.setRowStretch(c, 1 if c < rows else 0)
        self.setMinimumHeight(rows * (self.cell_h + self.gap) + 2 * self.margin)


class GaugeChooser(QDialog):
    """Choose which gauges are shown, drag to reorder, search by name."""
    def __init__(self, parent, order, visible, mby):
        super().__init__(parent)
        self.setWindowTitle("Gauges - tick to show, drag to reorder")
        self.resize(440, 640)
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sensors (e.g. temp, gpu 2, net, audio)...")
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for k in order:
            m = mby[k]
            it = QListWidgetItem(f"{m.label}    [{m.group}]")
            it.setData(Qt.ItemDataRole.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if k in visible else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        self.search.textChanged.connect(self._filter)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _filter(self, t):
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(t.lower() not in it.text().lower())

    def result_cfg(self):
        order, vis = [], []
        for i in range(self.list.count()):
            it = self.list.item(i)
            k = it.data(Qt.ItemDataRole.UserRole)
            order.append(k)
            if it.checkState() == Qt.CheckState.Checked:
                vis.append(k)
        return order, vis


class InfoDialog(QDialog):
    def __init__(self, parent, text):
        super().__init__(parent)
        self.setWindowTitle("Hardware information & sensor report")
        self.resize(780, 660)
        self.text = text.splitlines()
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter lines (e.g. nvme, fan, gpu 3, temp)...")
        self.view = QPlainTextEdit(readOnly=True)
        self.view.setFont(QFont(MONO, 10))
        self.view.setPlainText(text)
        lay.addWidget(self.search)
        lay.addWidget(self.view)
        self.search.textChanged.connect(
            lambda t: self.view.setPlainText("\n".join(l for l in self.text if t.lower() in l.lower())))


class ProcDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Process monitor - top 20")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["PID", "Name", "CPU %", "MEM %"])
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.tbl)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        self.timer.start(2000)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    def refresh(self):
        rows = []
        for pr in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            i = pr.info
            rows.append((i["cpu_percent"] or 0.0, i["memory_percent"] or 0.0, i["pid"], i["name"] or "?"))
        rows.sort(reverse=True)
        rows = rows[:20]
        self.tbl.setRowCount(len(rows))
        for r, (c, m, pid, name) in enumerate(rows):
            for col, v in enumerate((pid, name, f"{c:.1f}", f"{m:.1f}")):
                self.tbl.setItem(r, col, QTableWidgetItem(str(v)))


class AppearanceDialog(QDialog):
    """Live colour editor: pick a preset or a custom theme, change any colour, save your own."""
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Appearance - colors & themes")
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.activated.connect(lambda _=0: self.win.set_theme(self.combo.currentText()) or self.refresh())
        b_save = QPushButton("Save as custom theme...")
        b_save.clicked.connect(self.save_as)
        b_del = QPushButton("Delete")
        b_del.clicked.connect(self.delete)
        row.addWidget(QLabel("Theme:"))
        row.addWidget(self.combo, 1)
        row.addWidget(b_save)
        row.addWidget(b_del)
        lay.addLayout(row)
        grid = QGridLayout()
        self.btns = {}
        for i, (key, label) in enumerate(COLOR_KEYS):
            b = QPushButton()
            b.clicked.connect(lambda _=False, k=key, l=label: self.pick(k, l))
            self.btns[key] = b
            grid.addWidget(QLabel(label), i % 6, (i // 6) * 2)
            grid.addWidget(b, i % 6, (i // 6) * 2 + 1)
        lay.addLayout(grid)
        self.glow = QCheckBox("Glow effects (always off in low-resolution mode)")
        self.glow.toggled.connect(self.toggle_glow)
        lay.addWidget(self.glow)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        lay.addWidget(close)
        self.refresh()

    def refresh(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(list(self.win.themes_all()))
        self.combo.setCurrentText(self.win.cfg["theme"])
        self.combo.blockSignals(False)
        for key, b in self.btns.items():
            c = self.win.theme[key]
            lum = QColor(c).lightness()
            b.setText(c)
            b.setStyleSheet(f"background:{c}; color:{'#000' if lum > 128 else '#fff'}; border:1px solid #888; "
                            "min-width:90px; min-height:22px;")
        self.glow.blockSignals(True)
        self.glow.setChecked(bool(self.win.theme.get("glow_on", True)))
        self.glow.blockSignals(False)

    def pick(self, key, label):
        c = QColorDialog.getColor(QColor(self.win.theme[key]), self, label)
        if c.isValid():
            self.win.theme[key] = c.name()
            self.win.theme_edited()
            self.refresh()

    def toggle_glow(self, on):
        self.win.theme["glow_on"] = on
        self.win.theme_edited()

    def save_as(self):
        name, ok = QInputDialog.getText(self, "Save theme", "Theme name:")
        if ok and name.strip():
            self.win.save_custom_theme(name.strip())
            self.refresh()

    def delete(self):
        self.win.delete_custom_theme(self.combo.currentText())
        self.refresh()


class SpeedDialog(QDialog):
    """Speed-test size, continuous testing (interval / duration / test count / total-traffic limit) and data use."""
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Speed test - continuous testing & data use")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.size = QComboBox()
        for k, v in SIZES.items():
            self.size.addItem(v.describe(), k)
        self.size.activated.connect(lambda _=0: self.win.set_speed_size(self.size.currentData()) or self.refresh())
        form.addRow("Test size:", self.size)
        sc = win.cfg["speed_continuous"]
        self.interval = self._spin(2, 86400, sc["interval_s"], " s")
        self.duration = self._spin(0, 1440, sc["duration_min"], " min  (0 = no time limit)")
        self.max_tests = self._spin(0, 100000, sc["max_tests"], "  (0 = no limit)")
        self.limit = self._spin(0, 1000000, sc["data_limit_mb"], " MB total traffic  (0 = no limit)")
        form.addRow("Start a test every:", self.interval)
        form.addRow("Stop after:", self.duration)
        form.addRow("Stop after tests:", self.max_tests)
        form.addRow("Data limit (down + up):", self.limit)
        lay.addLayout(form)
        self.estimate = QLabel("")
        self.estimate.setWordWrap(True)
        lay.addWidget(self.estimate)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.b_start, self.b_stop, self.b_reset, b_close = (QPushButton(t) for t in (
            "Start continuous test", "Stop", "Reset session counters", "Close"))
        self.b_start.clicked.connect(self.start)
        self.b_stop.clicked.connect(self.win.tele.speed.stop_continuous)
        self.b_reset.clicked.connect(lambda: self.win.tele.speed.engine.reset_traffic() or self.win._account_speed_traffic()
                                     or self.refresh())
        b_close.clicked.connect(self.close)
        for b in (self.b_start, self.b_stop, self.b_reset, b_close):
            row.addWidget(b)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        for w in (self.interval, self.duration, self.max_tests, self.limit):
            w.valueChanged.connect(self.store)

    @staticmethod
    def _spin(lo, hi, val, suffix):
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setValue(int(val))
        w.setSuffix(suffix)
        return w

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start(1000)

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    def store(self):
        sc = self.win.cfg["speed_continuous"]
        sc.update(interval_s=self.interval.value(), duration_min=self.duration.value(),
                  max_tests=self.max_tests.value(), data_limit_mb=self.limit.value())
        self.win.save_later()
        self.refresh()

    def start(self):
        self.store()
        if not self.win.tele.speed.start_continuous(self.win.continuous_config()):
            self.status.setText("A continuous test is already running.")
        self.refresh()

    def refresh(self):
        w = self.win
        i = self.size.findData(w.cfg["speed_size"])
        if i >= 0:
            self.size.setCurrentIndex(i)
        sp = w.tele.speed
        cfg, size = w.continuous_config(), SIZES[w.cfg["speed_size"]]
        per = size.max_bytes
        n = None
        if cfg.duration_s:
            n = int(cfg.duration_s // max(2.0, cfg.interval_s)) + 1
        if cfg.max_tests:
            n = cfg.max_tests if n is None else min(n, cfg.max_tests)
        worst = n * per if n else None
        if cfg.data_limit_bytes and (worst is None or worst > cfg.data_limit_bytes):
            worst = cfg.data_limit_bytes
        self.estimate.setText("Worst case for one run: " + (fmt_bytes(worst) if worst else "unbounded (set a time, test or data limit)")
                              + ". Each test moves at most " + fmt_bytes(per) + ". The limit counts download AND upload.")
        c, s = sp.continuous_status(), sp.snapshot()
        life = w.cfg["speed_traffic_total"]
        lines = [f"This session: down {fmt_bytes(s.traffic.down_bytes)}  up {fmt_bytes(s.traffic.up_bytes)}  total {fmt_bytes(s.traffic.total)}",
                 f"All time (saved): down {fmt_bytes(life['down'])}  up {fmt_bytes(life['up'])}  total {fmt_bytes(life['down'] + life['up'])}"]
        if c.running:
            lines.append(f"Continuous test running: {c.tests} tests ({c.ok} ok, {c.failed} failed), next in {c.next_in:.0f} s")
        elif c.finish_reason:
            lines.append(f"Last continuous run {c.finish_reason}: {c.tests} tests ({c.ok} ok, {c.failed} failed)")
        self.status.setText("\n".join(lines))
        self.b_start.setEnabled(not c.running)
        self.b_stop.setEnabled(c.running)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg, existed = load_cfg()
        self.first_run = not self.cfg["first_run_done"]
        self.bg_alpha = 175 if self.cfg["transparent"] else 255
        if self.cfg["transparent"]:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(APP_NAME + APP_TITLE_SUFFIX)
        self.setWindowIcon(QIcon(ICON))
        self.resize(1400, 860)
        self.tele = Telemetry(max(1000, self.cfg["refresh_ms"]), speed_endpoints=self.cfg["speed_endpoints"] or None,
                              speed_size=self.cfg["speed_size"])
        self._speed_seen = (0, 0)
        self.auto_hidden, self._na_since, self.na_hide_after = set(), {}, NA_HIDE_AFTER_S
        self.metrics, self.mby = self.tele.metrics, self.tele.mby
        self.temp_keys = [m.key for m in self.metrics if m.unit == "°C"]
        self.theme = self._initial_theme()
        self.skin = skin_for(self.theme)
        self.dens = "normal"
        self.alert_active = self.alert_test = self.alert_muted = False
        self._a_since = self._a_clear = None
        self._last_beep = 0.0
        self._banner_bucket = -1
        self._speed_next = None
        self.last = {}
        self.log_f = self.log_w = None
        self._log_n = 0
        self._clock_s = ""
        self.vis_keys = set()
        self._groups = []
        self.appearance = None
        self._build_ui()
        self._build_menus()
        self.load_gauge_state()
        if self.first_run:                       # zero-config: pick a sensible profile for this machine
            self._load_profile_into_cfg(self.builtin_profiles()[
                "AI / Multi-GPU" if len(self.tele.gpus) >= 2 else "Default"])
        self.apply_all()
        if self.cfg["geometry"]:
            self.restoreGeometry(QByteArray.fromHex(self.cfg["geometry"].encode()))
        if self.cfg["always_on_top"]:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.on_poll)
        self.poll_timer.start(self.cfg["refresh_ms"])
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.on_anim)
        self.anim_timer.start(33)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_cfg)
        self.on_poll()
        if self.first_run:
            QTimer.singleShot(1800, self.show_first_run)
        if IS_MAC:
            if not self.cfg["mac_asked"]:
                QTimer.singleShot(3500, self.mac_intro)
            elif self.cfg.get("mac_helper_auto"):
                QTimer.singleShot(1500, lambda: self.mac_enable(ask=False))

    # ---------------- themes ----------------
    def themes_all(self):
        d = {k: dict(v) for k, v in THEMES.items()}
        d.update({k: full_theme(v) for k, v in self.cfg["custom_themes"].items()})
        return d

    def _initial_theme(self):
        if self.cfg.get("theme_colors"):
            return full_theme(self.cfg["theme_colors"])
        return dict(self.themes_all().get(self.cfg["theme"], THEMES["Engineering"]))

    def set_theme(self, name):
        t = self.themes_all().get(name)
        if not t:
            return
        self.cfg["theme"] = name
        self.theme = dict(t)
        self.cfg["theme_colors"] = dict(self.theme)
        self.apply_all()
        self.save_later()

    def theme_edited(self):
        self.cfg["theme_colors"] = dict(self.theme)
        self.apply_all()
        self.save_later()

    def save_custom_theme(self, name):
        if name in THEMES:
            name += " (custom)"
        self.cfg["custom_themes"][name] = dict(self.theme)
        self.cfg["theme"] = name
        self.rebuild_theme_menu()
        self.save_later()

    def delete_custom_theme(self, name):
        if name in self.cfg["custom_themes"]:
            del self.cfg["custom_themes"][name]
            self.cfg["theme"] = "Engineering"
            self.set_theme("Engineering")
            self.rebuild_theme_menu()

    def rebuild_theme_menu(self):
        self.theme_menu.clear()
        grp = QActionGroup(self)
        self._groups.append(grp)
        for name in self.themes_all():
            a = QAction(name, self, checkable=True)
            a.setChecked(name == self.cfg["theme"])
            a.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            grp.addAction(a)
            self.theme_menu.addAction(a)

    def show_appearance(self):
        if self.appearance is None:
            self.appearance = AppearanceDialog(self)
        self.appearance.refresh()
        self.appearance.show()
        self.appearance.raise_()

    # ---------------- UI ----------------
    def _build_ui(self):
        self.panel = Panel(self)
        self.setCentralWidget(self.panel)
        v = QVBoxLayout(self.panel)
        v.setContentsMargins(10, 8, 10, 6)
        v.setSpacing(6)
        self.header = QWidget()
        h = QHBoxLayout(self.header)
        h.setContentsMargins(4, 0, 4, 0)
        left = QVBoxLayout()
        self.title = QLabel(APP_NAME.upper())
        self.subtitle = QLabel("")
        left.addWidget(self.title)
        left.addWidget(self.subtitle)
        h.addLayout(left, 1)
        self.status = QLabel("● NOMINAL")
        self.clock = QLabel("")
        h.addWidget(self.status)
        h.addSpacing(14)
        self.mode_btn = QPushButton()
        self.mode_btn.setObjectName("mode")
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.clicked.connect(self.toggle_mode)
        h.addWidget(self.mode_btn)
        h.addSpacing(14)
        h.addWidget(self.clock)
        v.addWidget(self.header)
        self.banner = ClickLabel("")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.banner.clicked.connect(self.ack_alert)
        v.addWidget(self.banner)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid = GaugeGrid()
        self.scroll.setWidget(self.grid)
        self.grid.scroll = self.scroll
        self.grid.reorder_requested.connect(self.on_reorder)
        v.addWidget(self.scroll, 1)
        self.gauges = {}
        for m in self.metrics:
            if m.key.endswith("_panel"):
                dev = self.tele.gpus[int(m.key.split("_")[0][3:])]
                g = GpuPanel(m, self.theme, dev, parent=self.grid)
            else:
                g = Gauge(m, self.theme, parent=self.grid)
            g.hide()
            g.changed.connect(self.on_gauge_changed)
            g.hide_requested.connect(self.hide_gauge)
            g.action.connect(self.on_gauge_action)
            self.gauges[m.key] = g
        self.procs = ProcDialog(self)

    def _act(self, menu, text, slot, key=None, checkable=False, checked=False):
        a = QAction(text, self, checkable=checkable)
        if checkable:
            a.setChecked(checked)
            a.toggled.connect(slot)
        else:
            a.triggered.connect(lambda _=False: slot())
        if key:
            a.setShortcut(key)
        menu.addAction(a)
        self.addAction(a)      # keeps shortcuts working when the menu bar is hidden
        return a

    def _radio(self, menu, items, current, slot):
        grp = QActionGroup(self)
        self._groups.append(grp)
        acts = {}
        for val, text in items:
            a = QAction(text, self, checkable=True)
            a.setChecked(val == current)
            a.triggered.connect(lambda _=False, v=val: slot(v))
            grp.addAction(a)
            menu.addAction(a)
            acts[val] = a
        return acts

    def _build_menus(self):
        mb = self.menuBar()
        c = self.cfg
        v = mb.addMenu("&View")
        self._act(v, "Toggle Analog / Digital", self.toggle_mode, "M")
        self._act(v, "Toggle temperature °F / °C", self.toggle_temp, "T")
        self.act_full = self._act(v, "Fullscreen", self.set_fullscreen, "F11", True, c["fullscreen"])
        self.act_chrome = self._act(v, "Hide menu && header (kiosk)", self.set_chrome_hidden, "C", True, c["compact"])
        self._act(v, "Always on top", self.set_on_top, None, True, c["always_on_top"])
        self._act(v, "Transparent background (restart)", self.set_transparent, None, True, c["transparent"])
        v.addSeparator()
        self._act(v, "Appearance - colors && themes...", self.show_appearance, "Ctrl+E")
        self.theme_menu = v.addMenu("Theme")
        self.rebuild_theme_menu()
        self.dens_acts = self._radio(v.addMenu("Display density"), [
            ("auto", "Auto (by window size)"), ("normal", "Normal"), ("compact", "Compact"),
            ("lowres", "Low resolution (800x600 / 1024x768)")], c["density"], self.set_density)
        self.na_acts = self._radio(v.addMenu("N/A gauges"), [
            ("overlay", "Show why (animated overlay)"), ("hide", "Auto-hide until data returns"), ("plain", "Plain N/A")],
            c["na_mode"], self.set_na_mode)
        self.cols_acts = self._radio(v.addMenu("Columns"), [(0, "Auto (fit to width)"), (-1, "Balanced (by gauge count)")]
                                     + [(n, f"{n} columns") for n in (1, 2, 3, 4, 5, 6, 8)], c["cols"], self.set_cols)
        self.size_acts = self._radio(v.addMenu("Gauge size"), [("S", "Small"), ("M", "Medium"), ("L", "Large")],
                                     c["gauge_size"], self.set_gauge_size)
        esc = QAction(self)
        esc.setShortcut("Esc")
        esc.triggered.connect(lambda: self.act_full.setChecked(False))
        self.addAction(esc)

        g = mb.addMenu("&Gauges")
        self._act(g, "Choose / reorder gauges...", self.choose_gauges, "Ctrl+G")
        self.units_acts = self._radio(g.addMenu("Network units"), [(True, "Bits  (Mbps)"), (False, "Bytes (MB/s)")],
                                      c["net_bits"], self.set_units)
        self.temp_acts = self._radio(g.addMenu("Temperature units"), [(True, "Fahrenheit (°F)"), (False, "Celsius (°C)")],
                                     c["temp_f"], self.set_temp_f)
        self._radio(g.addMenu("Refresh rate"), [(ms, f"{ms} ms") for ms in (250, 500, 1000, 2000)], c["refresh_ms"], self.set_refresh)
        self._radio(g.addMenu("History length"), [(30, "30 seconds"), (60, "1 minute"), (300, "5 minutes")],
                    c["history_s"], self.set_history)
        g.addSeparator()
        self._act(g, "Reset all peaks / minimums", lambda: [x.reset() for x in self.gauges.values()], "Ctrl+R")

        self.prof_menu = mb.addMenu("&Profiles")
        self.rebuild_profile_menu()

        t = mb.addMenu("&Tools")
        self._act(t, "Process monitor", self.procs.show, "Ctrl+P")
        self._act(t, "Hardware info && sensor report", self.show_info, "Ctrl+I")
        t.addSeparator()
        if IS_MAC:
            self._act(t, "Enable advanced Mac sensors (administrator)...", self.mac_enable)
            self._act(t, "Mac sensors && permissions status...", self.mac_status)
            self._act(t, "Open Microphone privacy settings", lambda: QProcess.startDetached(
                "open", ["x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"]))
            self._act(t, "Enable advanced sensors automatically at launch",
                      lambda on: self.cfg.__setitem__("mac_helper_auto", on) or self.save_later(), None, True,
                      self.cfg.get("mac_helper_auto", False))
            t.addSeparator()
        self._act(t, "Run internet speed test now", self.run_speed, "Ctrl+T")
        self._act(t, "Cancel speed test", self.cancel_speed, "Ctrl+Shift+T")
        self.speed_size_acts = self._radio(t.addMenu("Speed test size"), [(k, v.describe()) for k, v in SIZES.items()],
                                           c["speed_size"], self.set_speed_size)
        self._act(t, "Continuous speed test and data use...", self.show_speed_dialog)
        self._radio(t.addMenu("Automatic speed test"), [("manual", "Manual only"), ("launch", "Once at launch"),
                    ("15", "Every 15 minutes"), ("60", "Every hour")], c["speed_auto"], self.set_speed_auto)
        t.addSeparator()
        self._act(t, "Log telemetry to CSV", self.toggle_logging, None, True, False)
        self._act(t, "Export gauge history to CSV...", self.export_csv)
        t.addSeparator()
        a = c["alert"]
        self._act(t, "Red alert enabled", lambda on: a.__setitem__("enabled", on) or self.save_later(), None, True, a["enabled"])
        self._act(t, "Red alert sound", lambda on: a.__setitem__("sound", on) or self.save_later(), None, True, a["sound"])
        self._act(t, "Red alert: critical limits only (servers)",
                  lambda on: a.__setitem__("soft_needed", 99 if on else 3) or self.save_later(), None, True, a["soft_needed"] >= 99)
        self._act(t, "Acknowledge / mute alert", self.ack_alert, "Space")
        self._act(t, "Test red alert", self.test_alert, "Ctrl+Shift+R")
        self.statusBar().showMessage("Double-click a gauge: analog/digital  |  right-click: options  |  hover: details")

    # ---------------- profiles ----------------
    def builtin_profiles(self):
        n = len(self.tele.gpus)
        gp = [f"gpu{i}_panel" for i in range(n)]
        ai = gp + ["cpu_util", "cpu_temp", "ram_pct", "net_down", "net_up", "disk_read", "disk_write"]
        srv = gp + ["cpu_util", "cpu_cores", "cpu_temp", "load1", "ram_pct", "swap_pct", "disk_util", "disk_pct",
                    "net_down", "net_up"]
        gaming = ["gpu0_util", "gpu0_temp", "gpu0_vram_pct", "gpu0_power", "cpu_util", "cpu_temp", "ram_pct",
                  "fps", "net_down", "net_up"]
        P = {"Default": dict(visible=DEFAULT_VISIBLE + (["thermal_state"] if IS_MAC else []), cols=0),
             "Gaming": dict(visible=gaming, cols=0),
             "War Thunder": dict(visible=WAR_THUNDER, cols=0),
             "AI / Multi-GPU": dict(visible=ai, cols=-1),
             "Server": dict(visible=srv, cols=-1),
             "Minimal": dict(visible=["cpu_util", "ram_pct", "gpu0_util", "gpu0_temp"], cols=0)}
        for p in P.values():
            p["visible"] = [k for k in p["visible"] if k in self.gauges]
        return P

    def _load_profile_into_cfg(self, p):
        c = self.cfg
        for k in ("visible", "pinned", "modes", "cols", "gauge_size", "density", "net_bits", "temp_f", "mode"):
            if k in p:
                c[k] = copy.deepcopy(p[k])
        c["visible"] = [k for k in c["visible"] if k in self.gauges]
        c["order"] = list(c["visible"]) + [k for k in c.get("order", []) + list(self.gauges) if k not in c["visible"]]
        if p.get("theme_colors"):
            self.theme = full_theme(p["theme_colors"])
            c["theme_colors"], c["theme"] = dict(self.theme), p.get("theme", c["theme"])
        if "modes" not in p:
            c["modes"] = {}

    def apply_profile(self, name):
        p = self.cfg["profiles"].get(name) or self.builtin_profiles().get(name)
        if not p:
            return
        self._load_profile_into_cfg(p)
        self.cfg["profile"] = name
        self.apply_all()
        self._sync_radios()
        self.save_later()
        self.statusBar().showMessage(f"Profile: {name}", 5000)

    def save_profile(self):
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:")
        if not ok or not name.strip():
            return
        c = self.cfg
        c["profiles"][name.strip()] = {k: copy.deepcopy(c[k]) for k in (
            "visible", "pinned", "modes", "mode", "cols", "gauge_size", "density", "net_bits", "temp_f")}
        c["profiles"][name.strip()]["visible"] = self.ordered_visible()      # keeps your dragged order
        c["profiles"][name.strip()].update(theme=c["theme"], theme_colors=dict(self.theme))
        c["profile"] = name.strip()
        self.rebuild_profile_menu()
        self.save_later()

    def delete_profile(self, name):
        self.cfg["profiles"].pop(name, None)
        self.rebuild_profile_menu()
        self.save_later()

    def rebuild_profile_menu(self):
        m = self.prof_menu
        m.clear()
        for name in self.builtin_profiles():
            add_menu_action(m, name, lambda n=name: self.apply_profile(n))
        if self.cfg["profiles"]:
            m.addSeparator()
            for name in self.cfg["profiles"]:
                add_menu_action(m, name, lambda n=name: self.apply_profile(n))
        m.addSeparator()
        add_menu_action(m, "Save current layout as profile...", self.save_profile)
        if self.cfg["profiles"]:
            dm = m.addMenu("Delete profile")
            for name in self.cfg["profiles"]:
                add_menu_action(dm, name, lambda n=name: self.delete_profile(n))

    def _sync_radios(self):
        c = self.cfg
        for acts, val in ((self.dens_acts, c["density"]), (self.cols_acts, c["cols"]), (self.size_acts, c["gauge_size"]),
                          (self.units_acts, c["net_bits"]), (self.temp_acts, c["temp_f"])):
            if val in acts:
                acts[val].setChecked(True)
        self.act_chrome.blockSignals(True)
        self.act_chrome.setChecked(c["compact"])
        self.act_chrome.blockSignals(False)

    # ---------------- apply state ----------------
    def load_gauge_state(self):
        """Saved thresholds / manual maxima are stored in Celsius."""
        for k, g in self.gauges.items():
            th = self.cfg["thresholds"].get(k)
            if th:
                g.warn, g.crit = th
            g.manual_max = self.cfg["manual_max"].get(k)
            if g.manual_max:
                g.hi = g.manual_max

    def apply_all(self):
        T = self.theme
        self.skin = skin_for(T)                         # one skin at a time; everything below is re-applied from it
        self.grid.marker.color = T["accent"]
        widgets_mod.set_font_stack(self.skin.font_stack())
        self.skin.prepare(self)
        for k, g in self.gauges.items():
            g.skin = self.skin if self.skin.paints_gauges() else None
            g.theme, g.alpha = T, self.bg_alpha
            g.mode = self.cfg["modes"].get(k, self.cfg["mode"])
            g.override = k in self.cfg["modes"]
            g.pinned = k in self.cfg["pinned"]
        self.apply_units(reset=False)
        self.apply_temp_unit()
        self.apply_history()
        self.apply_density(force=True)
        self.apply_layout()
        self.update_subtitle()
        self.set_chrome_hidden(self.cfg["compact"], save=False)
        if self.cfg["fullscreen"]:
            self.showFullScreen()
        self.update_mode_button()
        self.set_status(self.alert_active)
        self.panel.update()

    def eff_density(self):
        d = self.cfg["density"]
        if d != "auto":
            return d
        w, h = self.width(), self.height()
        if w <= 1100 or h <= 640:
            return "lowres"
        if w <= 1500 or h <= 800:
            return "compact"
        return "normal"

    def apply_density(self, force=False):
        eff = self.eff_density()
        if eff == self.dens and not force:
            return
        self.dens = eff
        dp = DENS[eff]
        T, sk = self.theme, self.skin
        self.setStyleSheet(sk.stylesheet(T, dp))
        self.title.setStyleSheet(sk.title_style(T, dp))
        self.subtitle.setStyleSheet(sk.subtitle_style(T, dp))
        self.clock.setStyleSheet(sk.clock_style(T, dp))
        cell = int(GAUGE_BASE[self.cfg["gauge_size"]] * dp["scale"])
        self.grid.cell_w = self.grid.cell_h = cell
        self.grid.set_metrics(dp["margin"], dp["gap"])
        self.grid.cols_cfg = self.cfg["cols"]
        mn = max(100, int(cell * 0.88))
        for g in self.gauges.values():
            g.fs, g.lowres = dp["fs"], eff == "lowres"
            g.setMinimumSize(mn, int(mn * 0.78) if isinstance(g, GpuPanel) else mn)
            g.update()
        self.panel.layout().setContentsMargins(dp["margin"] + 2, dp["margin"], dp["margin"] + 2, dp["margin"] - 2)
        self.update_mode_button()
        self.grid.relayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "grid") and self.cfg["density"] == "auto":
            self.apply_density()

    def apply_units(self, reset=True):
        bits = self.cfg["net_bits"]
        for k in ("net_down", "net_up", "speed_test"):
            g = self.gauges[k]
            g.unit = "Mbps" if bits else "MB/s"
            g.floor = self.mby[k].floor * (8 if bits else 1)
            if reset:
                g.reset()
                g.hi = g.floor

    def apply_temp_unit(self):
        for k in self.temp_keys:
            self.gauges[k].set_temp_unit(self.cfg["temp_f"])

    def apply_history(self):
        n = self.cfg["history_s"] * 1000 / self.cfg["refresh_ms"]
        for g in self.gauges.values():
            g.set_hist_len(n)
            g.sample_s = self.cfg["refresh_ms"] / 1000

    def ordered_visible(self, hide_na=False):
        """Gauges the user wants, in display order. With hide_na=True also drop the ones auto-hidden for being N/A
        (that is only what is SHOWN; the saved visible list and profiles are never changed by auto-hide)."""
        order = full_order(self.cfg["order"], list(self.gauges))
        keys = display_order(order, set(self.cfg["visible"]), set(self.cfg["pinned"]))
        return [k for k in keys if k not in self.auto_hidden] if hide_na else keys

    def apply_layout(self):
        self.vis_keys = set(self.ordered_visible())              # what the user chose (values keep updating for all)
        keys = self.ordered_visible(hide_na=True)                # what is on screen right now
        shown = set(keys)
        for k, g in self.gauges.items():
            g.na_overlay = self.cfg["na_mode"] == "overlay"
            if k not in shown:
                g.hide()
        self.grid.set_items([self.gauges[k] for k in keys])
        self.tele.set_audio("audio_out" in self.vis_keys, "audio_in" in self.vis_keys)

    def update_subtitle(self):
        gpus = self.tele.gpus
        parts = [self.tele.info["cpu"], f"{self.tele.info['ram_gb']:.0f} GB RAM"]
        if len(gpus) == 1:
            parts.append(f"{gpus[0].vendor} {gpus[0].model}  drv {gpus[0].driver}")
        elif gpus:
            parts.append(f"{len(gpus)} GPUs: " + ", ".join(f"{g.index}={g.model}" for g in gpus[:3])
                         + (f" +{len(gpus) - 3} more" if len(gpus) > 3 else ""))
        else:
            parts.append("no GPU detected")
        self.subtitle.setText("  •  ".join(parts))

    def update_mode_button(self):
        m = self.cfg["mode"].upper()
        self.mode_btn.setText(f"MODE: {m} (M)" if self.dens == "lowres" else f"◉  MODE: {m}   ⇄   (M)")

    def set_status(self, alert):
        dp = DENS[self.dens]
        self.status.setText("● RED ALERT" if alert else "● NOMINAL")
        self.status.setStyleSheet(self.skin.status_style(alert, self.theme, dp))

    # ---------------- actions ----------------
    def toggle_mode(self):
        self.cfg["mode"] = "digital" if self.cfg["mode"] == "analog" else "analog"
        self.cfg["modes"] = {}
        for g in self.gauges.values():
            g.mode, g.override = self.cfg["mode"], False
            g.update()
        self.update_mode_button()
        self.save_later()

    def on_gauge_changed(self):
        def c(g, x):
            return x if (x is None or not g.tf) else (x - 32) * 5 / 9

        def differs(a, b):
            return (a is None) != (b is None) or (a is not None and abs(a - b) > 0.01)

        self.cfg["modes"] = {k: g.mode for k, g in self.gauges.items() if g.override}
        self.cfg["pinned"] = [k for k, g in self.gauges.items() if g.pinned]
        self.cfg["thresholds"] = {k: [c(g, g.warn), c(g, g.crit)] for k, g in self.gauges.items()
                                  if differs(c(g, g.warn), g.m.warn) or differs(c(g, g.crit), g.m.crit)}
        self.cfg["manual_max"] = {k: c(g, g.manual_max) for k, g in self.gauges.items() if g.manual_max}
        self.apply_layout()
        self.save_later()

    def on_gauge_action(self, what):
        if what == "speed":
            self.run_speed()
        elif what == "speed_cancel":
            self.cancel_speed()

    def on_reorder(self, key, target_index):
        """A gauge was dragged onto another cell: move it there and remember the order."""
        order = full_order(self.cfg["order"], list(self.gauges))
        displayed = self.ordered_visible(hide_na=True)
        new, clamped = reorder(order, displayed, set(self.cfg["pinned"]), key, target_index)
        if clamped:
            self.statusBar().showMessage("Pinned gauges stay in front - unpin a gauge to move it across.", 4000)
        if new is None:
            return
        self.cfg["order"] = new
        self.apply_layout()
        self.save_later()
        if not clamped:
            self.statusBar().showMessage(f"Moved {self.mby[key].label}", 2500)

    def hide_gauge(self, key):
        self.cfg["visible"] = [k for k in self.cfg["visible"] if k != key]
        self.apply_layout()
        self.save_later()

    def choose_gauges(self):
        order = full_order(self.cfg["order"], list(self.gauges))
        d = GaugeChooser(self, order, self.cfg["visible"], self.mby)
        if d.exec():
            self.cfg["order"], self.cfg["visible"] = d.result_cfg()
            self.apply_layout()
            self.save_later()

    def set_units(self, bits):
        self.cfg["net_bits"] = bits
        self.apply_units()
        self.save_later()

    def set_temp_f(self, fahrenheit):
        self.cfg["temp_f"] = fahrenheit
        self.apply_temp_unit()
        self.save_later()

    def toggle_temp(self):
        new = not self.cfg["temp_f"]
        self.temp_acts[new].setChecked(True)
        self.set_temp_f(new)

    def set_refresh(self, ms):
        self.cfg["refresh_ms"] = ms
        self.poll_timer.setInterval(ms)
        self.tele.set_nv_interval(max(1000, ms))
        self.apply_history()
        self.save_later()

    def set_history(self, s):
        self.cfg["history_s"] = s
        self.apply_history()
        self.save_later()

    def set_density(self, d):
        self.cfg["density"] = d
        self.apply_density(force=True)
        self.save_later()

    def set_cols(self, n):
        self.cfg["cols"] = n
        self.grid.cols_cfg = n
        self.grid._sig = None
        self.grid.relayout()
        self.save_later()

    def set_gauge_size(self, s):
        self.cfg["gauge_size"] = s
        self.apply_density(force=True)
        self.save_later()

    def set_fullscreen(self, on):
        self.cfg["fullscreen"] = on
        self.showFullScreen() if on else self.showNormal()
        self.save_later()

    def set_chrome_hidden(self, on, save=True):
        self.cfg["compact"] = on
        self.header.setVisible(not on)
        self.menuBar().setVisible(not on)
        self.statusBar().setVisible(not on)
        if save:
            self.save_later()

    def set_on_top(self, on):
        self.cfg["always_on_top"] = on
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()
        self.save_later()

    def set_transparent(self, on):
        self.cfg["transparent"] = on
        self.save_cfg()
        QMessageBox.information(self, "Transparent background", f"Restart {APP_NAME} to apply this change.")

    def show_info(self):
        InfoDialog(self, hardware_report(self.tele)).exec()

    def show_first_run(self):
        ok = [self.mby[k].label for k in sorted(self.vis_keys) if self.last.get(k) is not None]
        na = [f"{self.mby[k].label}: {self.tele.reasons.get(k, '')}" for k in sorted(self.vis_keys)
              if self.last.get(k) is None]
        QMessageBox.information(
            self, f"{APP_NAME} - first start",
            f"Hardware detected ({len(self.tele.gpus)} GPU(s)) and the dashboard was populated automatically.\n\n"
            "Live sensors:\n  " + ", ".join(ok) +
            ("\n\nNot exposed on this machine (shown as N/A):\n  " + "\n  ".join(na) if na else "") +
            "\n\nFull report: Tools > Hardware info.  Layouts: Profiles menu.")
        self.cfg["first_run_done"] = True
        self.save_later()

    # ---------------- macOS permissions ----------------
    def mac_enable(self, ask=True):
        if not (IS_MAC and self.tele.mac):
            return
        if ask and QMessageBox.question(self, "Enable advanced Mac sensors", MAC_ADMIN_TEXT,
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.tele.mac.pm.enable()
        self.statusBar().showMessage("Waiting for the macOS administrator prompt...", 8000)

    def mac_status(self):
        QMessageBox.information(self, "Mac sensors & permissions", "\n".join(self.tele.mac.status_lines()))

    def mac_intro(self):
        self.cfg["mac_asked"] = True
        self.save_later()
        if QMessageBox.question(self, "Mac permissions", MAC_INTRO_TEXT, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.mac_enable(ask=False)

    # ---------------- speed test ----------------
    def run_speed(self):
        sp = self.tele.speed
        if sp.start():
            self.statusBar().showMessage("Speed test running - " + SIZES[self.cfg["speed_size"]].describe(), 10000)
        else:
            self.statusBar().showMessage("A speed test is already running (tests never overlap).", 4000)

    def cancel_speed(self):
        if self.tele.speed.running or self.tele.speed.continuous_status().running:
            self.tele.speed.cancel()
            self.statusBar().showMessage("Cancelling speed test...", 4000)

    def set_speed_size(self, key):
        self.cfg["speed_size"] = key
        self.tele.speed.set_size(key)
        if key in self.speed_size_acts:
            self.speed_size_acts[key].setChecked(True)
        self.save_later()

    def continuous_config(self):
        sc = self.cfg["speed_continuous"]
        return ContinuousConfig(interval_s=float(sc["interval_s"]), duration_s=float(sc["duration_min"]) * 60,
                                max_tests=int(sc["max_tests"]), data_limit_bytes=int(float(sc["data_limit_mb"]) * 1e6),
                                size=self.cfg["speed_size"])

    def show_speed_dialog(self):
        if getattr(self, "speed_dialog", None) is None:
            self.speed_dialog = SpeedDialog(self)
        self.speed_dialog.refresh()
        self.speed_dialog.show()
        self.speed_dialog.raise_()

    def set_speed_auto(self, v):
        self.cfg["speed_auto"] = v
        self._speed_next = None
        self.save_later()

    def _speed_schedule(self, now):
        every = {"manual": None, "launch": 0, "15": 900, "60": 3600}.get(self.cfg["speed_auto"])
        sp = self.tele.speed
        if every is None or sp.running or sp.continuous_status().running:
            return
        if self._speed_next is None:
            self._speed_next = now + 4
        if now >= self._speed_next:
            sp.start()
            self._speed_next = now + every if every else float("inf")

    def _account_speed_traffic(self):
        """Add the bytes the speed test moved since the last poll to the lifetime counters (saved in settings)."""
        tr = self.tele.speed.snapshot().traffic
        d0, u0 = self._speed_seen
        self._speed_seen = (tr.down_bytes, tr.up_bytes)
        dd, du = tr.down_bytes - d0, tr.up_bytes - u0
        if dd > 0 or du > 0:
            tot = self.cfg["speed_traffic_total"]
            tot["down"], tot["up"] = int(tot.get("down", 0)) + max(0, dd), int(tot.get("up", 0)) + max(0, du)
            self.save_later()

    # ---------------- logging / export ----------------
    def toggle_logging(self, on):
        if on:
            os.makedirs(LOG_DIR, exist_ok=True)
            path = os.path.join(LOG_DIR, time.strftime("telemetry-%Y%m%d-%H%M%S.csv"))
            self.log_f = open(path, "w", newline="")
            self.log_w = csv.writer(self.log_f)
            self.log_w.writerow(["time"] + [m.key for m in self.metrics])
            self.statusBar().showMessage(f"Logging to {path}  (temperatures in °C)", 8000)
        elif self.log_f:
            self.log_f.close()
            self.log_f = self.log_w = None

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export history", "engineering-console-history.csv", "CSV (*.csv)")
        if not path:
            return
        keys = [k for k in self.ordered_visible() if not k.endswith("_panel")]
        n = max((len(self.gauges[k].hist) for k in keys), default=0)
        dt = self.cfg["refresh_ms"] / 1000
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["seconds_ago"] + [f"{self.mby[k].label} ({self.gauges[k].unit})" for k in keys])
            for i in range(n):
                row = [f"{(n - 1 - i) * dt:.2f}"]
                for k in keys:
                    h = list(self.gauges[k].hist)
                    j = i - (n - len(h))
                    row.append(f"{h[j]:.3f}" if j >= 0 else "")
                w.writerow(row)

    # ---------------- polling ----------------
    def on_poll(self):
        t = self.tele.poll()
        self.last = t
        now = time.monotonic()
        v = dict(t)
        bits, tf = self.cfg["net_bits"], self.cfg["temp_f"]
        for k in ("net_down", "net_up", "speed_test"):
            if v[k] is not None:
                v[k] = v[k] * 8 / 1e6 if bits else v[k] / 1e6
        for k in ("disk_read", "disk_write"):
            if v[k] is not None:
                v[k] /= 1e6
        if tf:
            for k in self.temp_keys:
                if v[k] is not None:
                    v[k] = v[k] * 9 / 5 + 32
        for k, fl in self.tele.floors.items():
            if k in self.gauges and not (k in self.temp_keys):
                self.gauges[k].floor = fl
        reasons = self.tele.reasons
        for k in self.vis_keys:
            g = self.gauges[k]
            g.reason = reasons.get(k, "")
            if k == "cpu_cores":
                g.cores = self.tele.cores
            elif isinstance(g, GpuPanel):
                f, rs = self.tele.gpu_fields(g.dev.index)
                g.set_fields(f, rs, tf)
            elif k == "thermal_state":
                g.extra = f"Apple thermal pressure: {(self.tele.mac.pressure if self.tele.mac else None) or 'unknown'}"
            elif k == "speed_test":
                g.note = self.tele.speed.note()
                g.extra = self.tele.speed.summary()
            g.set_value(v[k])
        self._update_auto_hide(v, now)
        if "speed_test" in self.vis_keys:
            self._speed_schedule(now)
        self._account_speed_traffic()
        if self.log_w:
            self.log_w.writerow([time.strftime("%H:%M:%S")] + [("" if t[m.key] is None else f"{t[m.key]:.3f}") for m in self.metrics])
            self._log_n += 1
            if self._log_n % 10 == 0:
                self.log_f.flush()
        self.check_alert(t, now)

    def _update_auto_hide(self, v, now):
        """View > N/A gauges > Auto-hide: a gauge that has had no data for NA_HIDE_AFTER_S seconds steps aside, and comes
        back by itself the moment data returns. Nothing is saved: your gauge selection is never touched."""
        if self.cfg["na_mode"] != "hide":
            if self.auto_hidden:
                self.auto_hidden.clear()
                self.apply_layout()
            self._na_since.clear()
            return
        changed = False
        for k in self.vis_keys:
            g = self.gauges[k]
            if k in NA_NEVER_HIDE or k == "cpu_cores" or isinstance(g, GpuPanel):
                continue
            if v[k] is None:
                since = self._na_since.setdefault(k, now)
                if k not in self.auto_hidden and now - since >= self.na_hide_after:
                    self.auto_hidden.add(k)
                    changed = True
                    self.statusBar().showMessage(f"{g.m.label} hidden - no data ({g.reason or 'no reason reported'}). "
                                                 "It comes back by itself when data returns.", 8000)
            else:
                self._na_since.pop(k, None)
                if k in self.auto_hidden:
                    self.auto_hidden.discard(k)
                    changed = True
                    self.statusBar().showMessage(f"{g.m.label} is back - data available again.", 5000)
        if changed:
            self.apply_layout()

    def set_na_mode(self, mode):
        self.cfg["na_mode"] = mode if mode in NA_MODES else "overlay"
        self._na_since.clear()
        self.auto_hidden.clear()
        self.apply_layout()
        self.save_later()

    def on_anim(self):
        if self.isMinimized() or not self.isVisible():
            return
        for k in self.vis_keys - self.auto_hidden:
            g = self.gauges[k]
            if g.tick():
                g.update()
        s = time.strftime("%H:%M:%S")
        if s != self._clock_s:
            self._clock_s = s
            self.clock.setText(s)
        if self.alert_active:
            self.panel.update()
            bucket = int(time.monotonic() * 3) % 2
            if bucket != self._banner_bucket:
                self._banner_bucket = bucket
                self.banner.setStyleSheet(self.skin.banner_style(bucket, DENS[self.dens]))
            if self.cfg["alert"]["sound"] and not self.alert_muted and time.monotonic() - self._last_beep > 2.5:
                self._last_beep = time.monotonic()
                if IS_MAC and os.path.exists("/System/Library/Sounds/Sosumi.aiff"):
                    QProcess.startDetached("afplay", ["/System/Library/Sounds/Sosumi.aiff"])
                elif os.path.exists(ALARM):
                    QProcess.startDetached("paplay", [ALARM])
                else:
                    QApplication.beep()

    # ---------------- RED ALERT ----------------
    def _worst_gpu(self, t, suffix):
        best = idx = None
        for g in self.tele.gpus:
            x = t.get(f"gpu{g.index}_{suffix}")
            if x is not None and (best is None or x > best):
                best, idx = x, g.index
        return best, idx

    def check_alert(self, t, now):
        c = self.cfg["alert"]
        if not c["enabled"] and not self.alert_test:
            if self.alert_active:
                self.set_alert(False, [])
            return
        hard, soft = [], []
        rules = [(t.get("cpu_temp"), "cpu_temp_hard", "cpu_temp_soft", "CPU {:.0f}°C"),
                 (t.get("ram_pct"), "ram_hard", "ram_soft", "RAM {:.0f}%"),
                 (t.get("cpu_util"), None, "cpu_util_soft", "CPU LOAD {:.0f}%"),
                 (t.get("swap_pct"), None, "swap_soft", "SWAP {:.0f}%"),
                 (t.get("disk_util"), None, "disk_util_soft", "DISK {:.0f}%")]
        for suffix, hk, sk, fmt in (("temp", "gpu_temp_hard", "gpu_temp_soft", "GPU{} {:.0f}°C"),
                                    ("vram_pct", "vram_hard", "vram_soft", "GPU{} VRAM {:.0f}%"),
                                    ("util", None, "gpu_util_soft", "GPU{} LOAD {:.0f}%")):
            x, i = self._worst_gpu(t, suffix)          # worst GPU counts once per condition
            rules.append((x, hk, sk, fmt.replace("{}", str(i)) if i is not None else fmt))
        for x, hk, sk, fmt in rules:
            if x is None:
                continue
            if hk and x >= c[hk]:
                hard.append(fmt.format(x))
            elif x >= c[sk]:
                soft.append(fmt.format(x))
        ts = t.get("thermal_state")                       # macOS only: Apple reports it is about to throttle hard
        if ts is not None and ts >= 3:
            hard.append("THERMAL " + str((self.tele.mac.pressure if self.tele.mac else None) or "PRESSURE").upper())
        la = t.get("load1")
        if la is not None and la >= 2 * self.tele.cores_n:
            soft.append(f"LOAD AVG {la:.1f}")
        cond = bool(hard) or len(soft) >= c["soft_needed"]
        reasons = hard + soft
        if self.alert_test:
            cond, reasons = True, ["TEST MODE"]
        if cond:
            self._a_clear = None
            if self._a_since is None:
                self._a_since = now
            need = 0 if self.alert_test else (1.0 if hard else c["sustain_s"])
            if now - self._a_since >= need:
                self.set_alert(True, reasons)
        else:
            self._a_since = None
            if self.alert_active:
                if self._a_clear is None:
                    self._a_clear = now
                elif now - self._a_clear >= 5:
                    self.set_alert(False, [])

    def set_alert(self, on, reasons):
        if on:
            self.banner.setText("⚠  RED ALERT  ⚠   " + "  •  ".join(reasons[:8]) +
                                "   —   SYSTEM MAY CRASH: REDUCE LOAD   (click / Space = mute)")
            if not self.alert_active:
                self.alert_muted = False
                self._last_beep = 0.0
                self.alert_active = True
                self.banner.show()
                self.setWindowTitle(f"⚠ RED ALERT - {APP_NAME}" + APP_TITLE_SUFFIX)
                self.set_status(True)
        elif self.alert_active:
            self.alert_active = False
            self.banner.hide()
            self.setWindowTitle(APP_NAME + APP_TITLE_SUFFIX)
            self.set_status(False)
            self.panel.update()

    def ack_alert(self):
        self.alert_muted = True

    def test_alert(self):
        self.alert_test = not self.alert_test
        if not self.alert_test:
            self._a_since = None

    # ---------------- persistence ----------------
    def save_later(self):
        self.save_timer.start(800)

    def save_cfg(self):
        try:
            self.cfg["geometry"] = bytes(self.saveGeometry().toHex().data()).decode("ascii")
            os.makedirs(CONF_DIR, exist_ok=True)
            tmp = CONF + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.cfg, f, indent=1)
            os.replace(tmp, CONF)
        except Exception as e:
            print("could not save config:", e, file=sys.stderr)

    def closeEvent(self, e):
        self.save_cfg()
        if self.log_f:
            self.log_f.close()
        self.tele.close()
        super().closeEvent(e)


def main():
    log = logsetup.setup()
    logsetup.event(log, "app.start", version=__version__, platform=sys.platform, python=sys.version.split()[0])

    def hook(tp, val, tb):                                   # unexpected errors go to the log file, not a dialog dump
        log.error("uncaught.exception", exc_info=(tp, val, tb), extra={"fields": {}})
        sys.__excepthook__(tp, val, tb)
    sys.excepthook = hook
    if IS_WIN:
        try:                                                 # lets Windows group the taskbar icon under this app
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("EngineeringConsole.EngineeringConsole")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(os.environ.get("CONSOLE_DESKTOP_ID", APP_SLUG))
    app.setWindowIcon(QIcon(ICON))                   # Dock / taskbar icon when run from source
    w = MainWindow()
    w.show()
    code = app.exec()
    logsetup.event(log, "app.exit", code=code)
    sys.exit(code)
