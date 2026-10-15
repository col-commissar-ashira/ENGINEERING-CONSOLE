"""Gauge ordering rules (pure functions, no Qt) - used by drag-to-reorder and tested on their own."""


def full_order(saved_order, all_keys):
    """Every known gauge exactly once: saved order first (ignoring unknown keys), new gauges appended."""
    seen, out = set(), []
    for k in list(saved_order) + list(all_keys):
        if k in all_keys and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def display_order(order, visible, pinned):
    """What the grid shows: visible gauges in saved order, pinned ones first."""
    vis = [k for k in order if k in visible]
    pin = [k for k in vis if k in pinned]
    return pin + [k for k in vis if k not in pinned]


def reorder(order, displayed, pinned, key, target_index):
    """Move `key` to `target_index` of the displayed list. Pinned gauges always stay in front, so a gauge cannot
    be dropped into the other group (the target is clamped to its own group).
    Returns (new_order, clamped) or (None, False) when nothing changes. Hidden gauges keep their slots."""
    if key not in displayed:
        return None, False
    n_pin = sum(1 for k in displayed if k in pinned)
    lo, hi = (0, n_pin - 1) if key in pinned else (n_pin, len(displayed) - 1)
    tgt = max(lo, min(hi, int(target_index)))
    clamped = tgt != int(target_index) and not (0 <= int(target_index) < len(displayed) and lo <= int(target_index) <= hi)
    src = displayed.index(key)
    if tgt == src:
        return None, clamped
    new = list(displayed)
    new.pop(src)
    new.insert(tgt, key)
    shown, it = set(displayed), iter(new)
    out = [next(it) if k in shown else k for k in order]
    out += [k for k in new if k not in out]                         # safety: never lose a gauge
    return out, clamped
