"""Memory diagnostics (--memory): what the process holds, to find what grows.
NS_TRACEMALLOC=N in the environment at start also records allocations (N frames, slower)."""
import gc
import os
import sys
import threading
import time
import tracemalloc
from collections import Counter

from . import proc

_base = None
_base_t = 0


def start_tracing():
    global _base, _base_t
    frames = os.environ.get("NS_TRACEMALLOC", "")
    if frames and not tracemalloc.is_tracing():
        tracemalloc.start(max(1, int(frames) if frames.isdigit() else 1))
        _base, _base_t = None, 0


def _len(obj):
    try:
        return len(obj)
    except Exception:
        return -1


def rns_tables():
    out = {}
    try:
        import RNS
        for name in ("destinations", "pending_links", "active_links", "receipts", "announce_table", "path_table",
                     "reverse_table", "link_table", "held_announces", "path_requests", "path_states",
                     "pending_local_path_requests", "packet_hashlist", "local_client_rssi_cache", "pr_stamp_cache",
                     "discovery_path_requests", "blackholed_identities", "announce_handlers", "pending_callbacks"):
            if hasattr(RNS.Transport, name):
                out["Transport." + name] = _len(getattr(RNS.Transport, name))
        for name in ("known_destinations", "known_ratchets", "announce_valid_cache", "announce_invalid_cache"):
            if hasattr(RNS.Identity, name):
                out["Identity." + name] = _len(getattr(RNS.Identity, name))
    except Exception as e:
        out["error"] = str(e)
    return out


def ours(srv):
    out = {}
    for nid, n in list(srv.nodes.items()):
        for name in ("links", "closed", "by_who", "flagged", "rate", "multi", "registered", "partials", "_peers"):
            if hasattr(n, name):
                out["%s.%s" % (nid, name)] = _len(getattr(n, name))
        st = getattr(n, "stats", None)
        if st is not None:
            for name, val in vars(st).items():
                if isinstance(val, (dict, list, set)) or hasattr(val, "__len__") and not isinstance(val, (str, bytes)):
                    out["%s.stats.%s" % (nid, name)] = _len(val)
    for name, val in vars(srv).items():
        if isinstance(val, (dict, list, set)):
            out["server." + name] = _len(val)
    ui = getattr(srv, "ui", None)
    if ui is not None:
        out["webui.sessions"] = _len(ui.sessions)
        out["webui.fails"] = _len(ui.fails)
    try:
        from . import log
        for name, val in vars(log).items():
            if isinstance(val, (list, dict)) or type(val).__name__ == "deque":
                out["log." + name] = _len(val)
    except Exception:
        pass
    return out


def report(srv, top=25):
    global _base, _base_t
    gc.collect()
    types = Counter(type(o).__name__ for o in gc.get_objects())
    threads = Counter(t.name.rstrip("0123456789-() ") or "?" for t in threading.enumerate())
    r = {"rss": proc.rss(), "pid": os.getpid(), "python": sys.version.split()[0], "uptime": time.time() - srv.started,
         "threads": sum(threads.values()), "thread_names": dict(threads.most_common(15)),
         "gc_objects": sum(types.values()), "types": dict(types.most_common(top)),
         "rns": rns_tables(), "ours": ours(srv), "tracing": tracemalloc.is_tracing()}
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/status") as f:
                r["status"] = {k: v.strip() for k, v in (l.split(":", 1) for l in f if ":" in l)
                               if k in ("VmRSS", "RssAnon", "RssFile", "VmData", "Threads")}
        except OSError:
            pass
    if tracemalloc.is_tracing():
        snap = tracemalloc.take_snapshot().filter_traces((tracemalloc.Filter(False, tracemalloc.__file__),))
        cur, peak = tracemalloc.get_traced_memory()
        r["traced"] = {"current": cur, "peak": peak}
        r["by_line"] = [{"where": str(s.traceback[0]), "size": s.size, "count": s.count}
                        for s in snap.statistics("lineno")[:top]]
        if _base is None:
            _base, _base_t = snap, time.time()
        else:
            r["growth_since"] = time.time() - _base_t
            r["growth"] = [{"where": " <- ".join(str(f) for f in s.traceback), "size_diff": s.size_diff,
                            "count_diff": s.count_diff, "size": s.size}
                           for s in snap.compare_to(_base, "traceback")[:top] if s.size_diff > 0]
    return r
