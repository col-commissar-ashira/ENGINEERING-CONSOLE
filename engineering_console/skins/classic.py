"""The original Engineering Console look ("classic" skin). This is the default for every
built-in theme except Apple. The styles are the ones the app always used (3.0 only added QSpinBox, which the
new speed-test dialog needs)."""
from ..platform_info import MONO
from .base import Skin


class ClassicSkin(Skin):
    name = "classic"

    def font_stack(self):
        return [MONO]

    def paints_gauges(self):
        return False            # gauges use their own built-in (original) painting

    def stylesheet(self, T, dp):
        return f"""
        QMainWindow, QDialog {{ background:{T['bg']}; }}
        QMenuBar {{ background:{T['panel']}; color:{T['text']}; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background:{T['edge']}; }}
        QMenu {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']}; }}
        QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:none; }}
        QLabel, QCheckBox {{ color:{T['text']}; }}
        QToolTip {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['accent']}; padding:6px; }}
        QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox {{ background:{T['panel']}; color:{T['text']};
            border:1px solid {T['edge']}; }}
        QHeaderView::section {{ background:{T['edge']}; color:{T['text']}; border:0; padding:4px; }}
        QPushButton {{ background:{T['panel']}; color:{T['text']}; border:1px solid {T['edge']}; padding:5px 12px; }}
        QPushButton:hover {{ border-color:{T['accent']}; }}
        QPushButton#mode {{ font:bold {max(9, dp['ssize'] + 3)}px '{MONO}'; color:{T['accent']};
            border:2px solid {T['accent']}; padding:{4 if dp['scale'] < 0.7 else 8}px 12px; border-radius:6px;
            min-width:{dp['btn']}px; }}
        QStatusBar {{ background:{T['panel']}; color:{T['dim']}; }}
        """

    def title_style(self, T, dp):
        return f"font:bold {dp['tsize']}px '{MONO}'; color:{T['accent']}; letter-spacing:{max(1, dp['tsize'] // 5)}px;"

    def subtitle_style(self, T, dp):
        return f"font:{dp['ssize']}px '{MONO}'; color:{T['dim']};"

    def clock_style(self, T, dp):
        return f"font:bold {dp['ssize'] + 5}px '{MONO}'; color:{T['dim']};"

    def status_style(self, alert, T, dp):
        if alert:
            return f"font:bold {dp['ssize'] + 5}px '{MONO}'; color:#ff2d2d;"
        return f"font:bold {dp['ssize'] + 3}px '{MONO}'; color:{T['accent']};"

    def banner_style(self, bucket, dp):
        bg = "#c00000" if bucket else "#600000"
        return (f"background:{bg}; color:white; font:bold {dp['ssize'] + 6}px '{MONO}'; "
                "padding:8px; border:2px solid #ff4040; border-radius:6px;")

    def paint_background(self, panel, p, win):
        from PySide6.QtGui import QColor
        c = QColor(win.theme["bg"])
        c.setAlpha(win.bg_alpha)
        p.fillRect(panel.rect(), c)
