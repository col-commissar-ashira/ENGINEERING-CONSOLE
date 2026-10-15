"""DEV/TEST ONLY: a fake PySide6 so UI *logic* and paint code can run headless (there is no display or Qt here).
It draws nothing - real rendering and real mouse/drag behaviour must be checked on a real display.

It is deliberately strict about one class of mistake that breaks real Qt 6: using an old flat enum name
(Qt.AlignCenter, QPainter.Antialiasing, QMessageBox.Yes ...). Those raise AttributeError here exactly as they must not
be used in the code base; only the scoped spelling (Qt.AlignmentFlag.AlignCenter) is accepted.
The table of valid names below is written from the Qt 6 documentation; it is not generated from a real Qt."""
import sys
import types

SCOPED = {
    "Qt": {
        "AlignmentFlag": "AlignLeft AlignRight AlignHCenter AlignVCenter AlignCenter AlignTop AlignBottom",
        "PenStyle": "NoPen SolidLine DashLine DotLine",
        "PenCapStyle": "FlatCap SquareCap RoundCap",
        "PenJoinStyle": "MiterJoin BevelJoin RoundJoin",
        "BrushStyle": "NoBrush SolidPattern",
        "TextElideMode": "ElideLeft ElideRight ElideMiddle ElideNone",
        "CheckState": "Unchecked PartiallyChecked Checked",
        "ItemDataRole": "DisplayRole UserRole",
        "ItemFlag": "ItemIsUserCheckable ItemIsEnabled ItemIsSelectable",
        "CursorShape": "PointingHandCursor ArrowCursor",
        "WidgetAttribute": "WA_TranslucentBackground WA_TransparentForMouseEvents",
        "WindowType": "WindowStaysOnTopHint",
        "GlobalColor": "transparent black white",
        "MouseButton": "LeftButton RightButton NoButton",
        "AspectRatioMode": "KeepAspectRatio IgnoreAspectRatio",
        "TransformationMode": "SmoothTransformation FastTransformation",
        "DropAction": "MoveAction CopyAction IgnoreAction",
    },
    "QFont": {"SpacingType": "AbsoluteSpacing PercentageSpacing", "StyleHint": "SansSerif Monospace Serif"},
    "QPainter": {"RenderHint": "Antialiasing TextAntialiasing SmoothPixmapTransform"},
    "QImage": {"Format": "Format_ARGB32 Format_RGB32"},
    "QSizePolicy": {"Policy": "Expanding Fixed Preferred Minimum"},
    "QEvent": {"Type": "ToolTip Paint Resize"},
    "QDialogButtonBox": {"StandardButton": "Ok Cancel Close Yes No"},
    "QMessageBox": {"StandardButton": "Ok Cancel Yes No Close"},
    "QHeaderView": {"ResizeMode": "Stretch Interactive Fixed ResizeToContents"},
    "QAbstractItemView": {"EditTrigger": "NoEditTriggers DoubleClicked", "DragDropMode": "NoDragDrop DragDrop InternalMove"},
}
# every old flat name that Qt 6 no longer provides on these classes
REMOVED_FLAT = set()
for _cls, _groups in SCOPED.items():
    for _members in _groups.values():
        REMOVED_FLAT.update(_members.split())
REMOVED_FLAT |= {"exec_"}


class _Enum:
    def __init__(self, qual, members):
        self._qual, self._m = qual, set(members.split())

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        if n not in self._m:
            raise AttributeError(f"{self._qual} has no member {n!r} (check the Qt 6 name)")
        return _Flag(f"{self._qual}.{n}")


class _Flag:
    def __init__(self, name):
        self.name = name

    def __or__(self, o):
        return _Flag(self.name + "|" + getattr(o, "name", str(o)))
    __ror__ = __and__ = __rand__ = __or__
    __bool__ = lambda self: True
    __hash__ = lambda self: hash(self.name)
    __eq__ = lambda self, o: isinstance(o, _Flag) and o.name == self.name
    __int__ = lambda self: 1
    __index__ = lambda self: 1


class _Meta(type):
    def __getattr__(cls, n):
        if n.startswith("__"):
            raise AttributeError(n)
        groups = SCOPED.get(cls.__name__, {})
        if n in groups:
            return _Enum(f"{cls.__name__}.{n}", groups[n])
        if n in REMOVED_FLAT and cls.__name__ in SCOPED or (n in REMOVED_FLAT and n != "exec_" and cls.__name__.startswith("Q")
                                                            and n in ("InternalMove", "NoEditTriggers", "Stretch")):
            raise AttributeError(f"{cls.__name__}.{n}: flat enum names were removed in Qt 6 - use the scoped name")
        return _Base()


class _Base(metaclass=_Meta):
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        if n == "exec_":
            raise AttributeError("exec_ was removed in Qt 6: use exec()")
        return _Base()

    def __call__(self, *a, **k):
        return _Base()

    def _s(self, *a):
        return _Base()
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _s
    __floordiv__ = __rfloordiv__ = __or__ = __ror__ = __neg__ = __abs__ = _s
    __lt__ = __gt__ = __le__ = __ge__ = lambda self, o: False
    __int__ = lambda self: 10
    __float__ = lambda self: 10.0
    __index__ = lambda self: 10
    __bool__ = lambda self: False
    __len__ = lambda self: 0
    __iter__ = lambda self: iter(())
    __format__ = lambda self, spec: "0"
    __hash__ = lambda self: 1


class _Sig:
    def __init__(self, *a):
        self.fns = []

    def connect(self, fn):
        self.fns.append(fn)

    def emit(self, *a):
        for f in self.fns:
            f(*a)

    def __get__(self, obj, typ=None):      # per-instance signal
        if obj is None:
            return self
        return obj.__dict__.setdefault("_sig_%d" % id(self), _Sig())


class _QtNamespace:
    """`Qt` itself: only the scoped enum groups exist; any flat name (Qt.AlignCenter ...) is an error."""
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        groups = SCOPED["Qt"]
        if n not in groups:
            raise AttributeError(f"Qt.{n}: not a Qt 6 scoped enum group (flat enum names were removed)")
        return _Enum(f"Qt.{n}", groups[n])


def _module(name):
    m = types.ModuleType(name)
    cache = {}

    def getter(n):
        if n.startswith("__"):
            raise AttributeError(n)
        if n not in cache:
            cache[n] = _Meta(n, (_Base,), {})
        return cache[n]
    m.__getattr__ = getter
    return m


for sub in ("QtCore", "QtGui", "QtWidgets", "QtMultimedia"):
    mod = _module("PySide6." + sub)
    sys.modules["PySide6." + sub] = mod
    setattr(sys.modules[__name__], sub, mod)
sys.modules["PySide6.QtCore"].Signal = _Sig
sys.modules["PySide6.QtCore"].Qt = _QtNamespace()
