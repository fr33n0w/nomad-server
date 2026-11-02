"""RAM and CPU of this process, without psutil."""
import os
import sys
import threading
import time

_lock = threading.Lock()
_last = None


def rss():
    """Resident memory in bytes, or 0 if unknown."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                   "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                   "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
            return 0
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak if sys.platform == "darwin" else peak * 1024
    except Exception:
        return 0


def cpu():
    """CPU used since the previous call, in % of one core (like top)."""
    global _last
    t = os.times()
    now = (time.monotonic(), t.user + t.system)
    with _lock:
        prev, _last = _last, now
    if not prev or now[0] - prev[0] <= 0:
        return 0.0
    return round(100.0 * (now[1] - prev[1]) / (now[0] - prev[0]), 1)


def usage():
    return {"rss": rss(), "cpu": cpu(), "cores": os.cpu_count() or 1}
