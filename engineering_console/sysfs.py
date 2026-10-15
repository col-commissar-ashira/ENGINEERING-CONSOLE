"""Tiny helpers for reading /sys and /proc files safely."""


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def _num(path, scale=1.0):
    v = _read(path)
    try:
        return float(v) * scale
    except (TypeError, ValueError):
        return None


def first(*vals):
    """First value that is not None (0.0 counts as a real reading)."""
    return next((v for v in vals if v is not None), None)
