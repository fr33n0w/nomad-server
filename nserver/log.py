"""Server log: one line per event, kept in memory for the web UI and
written to the terminal or to logs/server.log (rotated at 5 MB)."""
import collections
import os
import threading
import time

RECENT_MAX = 3000
MAXSIZE = 5 * 1024 * 1024

_lock = threading.Lock()
_recent = collections.deque(maxlen=RECENT_MAX)
_seq = 0
_file = None
_quiet = False
LOUD = ("warn", "error")


def to_file(path):
    global _file
    _file = path


def quiet(enabled=True):
    """Terminal (or journal) gets only warnings and errors; the file and the web UI get everything."""
    global _quiet
    _quiet = enabled


def line(text, node="", kind="info"):
    """kind: info, connect, page, file, media, refresh, announce, warn, error."""
    global _seq
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    out = "%s  %-14s %s" % (stamp, ("[%s]" % node) if node else "[server]", text)
    with _lock:
        _seq += 1
        _recent.append({"seq": _seq, "t": now, "node": node, "kind": kind, "text": text})
        if _file:
            try:
                os.makedirs(os.path.dirname(_file), exist_ok=True)
                with open(_file, "a", encoding="utf-8") as f:
                    f.write(out + "\n")
                if os.path.getsize(_file) > MAXSIZE:
                    os.replace(_file, _file + ".1")
            except OSError:
                pass
            if not _quiet:
                return
        if _quiet and kind not in LOUD and not (kind == "info" and not node):
            return
        try:
            print(out, flush=True)
        except UnicodeEncodeError:
            # Windows console in cp1252 and names with emoji
            print(out.encode("ascii", "replace").decode("ascii"), flush=True)
        except (AttributeError, OSError):
            pass  # pythonw: no console at all


def recent(after=0, node=None, limit=500):
    with _lock:
        rows = [r for r in _recent if r["seq"] > after and (node is None or r["node"] == node)]
    return rows[-limit:]


def size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024


def plural(n, one, many=None):
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def span(seconds):
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return "%dd %dh" % (d, h)
    if h:
        return "%dh %02dm" % (h, m)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s
