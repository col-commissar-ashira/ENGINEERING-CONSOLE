"""Why-is-it-N/A helpers (pure Python, no Qt): turn a sensor's reason text into a short headline for the overlay, and
wrap text to a width. Nothing here invents information - the reason text always comes from the sensor backend."""
import re

KINDS = (                                    # first match wins; order matters
    ("error", "SENSOR ERROR", ("failed", "error", "exception", "crash", "unreadable", "keeps exiting", "exited")),
    ("missing", "SETUP NEEDED", ("install", "missing", "not found", "not running", "run libre", "stereo mix",
                                 "loopback", "not integrated")),
    ("permission", "PERMISSION NEEDED", ("administrator", "permission", "password", "privacy", "allow it",
                                         "restricts")),
    ("waiting", "WAITING FOR DATA", ("starting", "waiting", "no test yet", "sampling", "first sample", "warming",
                                     "meter off", "testing")),
)
FALLBACK = ("unsupported", "NOT EXPOSED BY THIS SYSTEM")
NO_REASON = "The sensor backend gave no reason."


def classify(reason):
    """-> (kind, headline). kind in error | permission | missing | waiting | unsupported."""
    low = (reason or "").lower()
    for kind, title, words in KINDS:
        if any(w in low for w in words):
            return kind, title
    return FALLBACK


def wrap_by_width(text, measure, width, max_lines):
    """Greedy word wrap. `measure(s)` -> pixel width of s. The last line is cut with '...' if the text does not fit."""
    words = re.sub(r"\s+", " ", text or "").strip().split(" ")
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if cur and measure(trial) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
        while measure(cur) > width and len(cur) > 1:         # a single word wider than the box: hard-cut it
            k = len(cur)
            while k > 1 and measure(cur[:k]) > width:
                k -= 1
            lines.append(cur[:k])
            cur = cur[k:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and measure(last + "...") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines
