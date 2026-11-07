"""The web UI in a window of its own (pywebview), for --app.

A process of its own ("nomad_server.py --window URL"): pywebview wants the main
thread, the server keeps it for Reticulum. With --owner (the server started it)
closing the window stops the server, also one started again by a restart; the
window closes when the server is gone (Shut down in the UI). Without --owner
(the server was already running) closing it only closes the window.
"""
import base64
import json
import os
import sys
import threading
import time
import urllib.request

from nserver import VERSION

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TITLE = "nS: Nomadnet Server v%s" % VERSION
BACKGROUND = "#0b140a"
APP_ID = "nomad-server.App"


def available():
    import importlib.util
    return importlib.util.find_spec("webview") is not None


def splash_html():
    """Shown at once, until the server answers (Reticulum can take seconds)."""
    try:
        with open(os.path.join(WEB, "splash-dark.svg"), "rb") as f:
            logo = "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        logo = ""
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; height: 100%%; background: %s; overflow: hidden; }
body { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px;
       font-family: "Segoe UI", Helvetica, Arial, sans-serif; }
img { width: 420px; max-width: 80vw; animation: pulse 2.4s ease-in-out infinite; }
@keyframes pulse { 0%%, 100%% { opacity: 1; } 50%% { opacity: .75; } }
div { color: #8b949e; font-size: 13px; }
</style></head><body><img src="%s" alt=""><div>starting v%s</div></body></html>""" % (BACKGROUND, logo, VERSION)


def set_window_app_id(hwnd):
    """Its own taskbar button and icon, not Python's group (the Store Python too)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                    ("value", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    def guid(d1, d2, d3, *d4):
        return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

    iid_store = guid(0x886D8EEB, 0x8CF2, 0x4446, 0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99)
    key = PROPERTYKEY(guid(0x9F4C2855, 0x9F79, 0x4B39, 0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3), 5)
    ctypes.windll.ole32.CoInitialize(None)
    store = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(iid_store),
                                                          ctypes.byref(store)) != 0 or not store:
        return
    table = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                   ctypes.POINTER(PROPVARIANT))(table[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(table[7])
    value = PROPVARIANT()
    value.vt = 31  # VT_LPWSTR
    value.value = APP_ID
    try:
        set_value(store, ctypes.byref(key), ctypes.byref(value))
        commit(store)
    finally:
        release(store)


def set_dark_title_bar(hwnd):
    import ctypes
    on = ctypes.c_int(1)
    for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on old Windows 10)
        if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(on), ctypes.sizeof(on)) == 0:
            break

    def redraw():
        user32 = ctypes.windll.user32
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x20)
        # only WM_NCACTIVATE repaints the whole bar
        active = 1 if user32.GetForegroundWindow() == hwnd else 0
        user32.SendMessageW(hwnd, 0x0086, 1 - active, 0)
        user32.SendMessageW(hwnd, 0x0086, active, 0)
    redraw()
    for delay in (0.5, 2.0):
        threading.Timer(delay, redraw).start()


def answers(url):
    try:
        urllib.request.urlopen(url + "api/session", timeout=3).close()
        return True
    except Exception:
        return False


def shutdown(url, base):
    """The owner window closed: stop the server, by its console token."""
    try:
        with open(os.path.join(base, "data", ".token")) as f:
            token = f.read().strip()
        req = urllib.request.Request(url + "api/shutdown", data=json.dumps({}).encode(),
                                     headers={"X-Token": token, "X-Requested-With": "nomad-server",
                                              "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass


def run(url, base, owner):
    import webview
    if not url.endswith("/"):
        url += "/"
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    window = webview.create_window(TITLE, html=splash_html(), width=1360, height=860, min_size=(480, 360),
                                   background_color=BACKGROUND, text_select=True)
    state = {"closing": False}

    def shown():
        if os.name != "nt":
            return
        try:
            hwnd = int(window.native.Handle.ToInt64())
        except Exception:
            return
        for apply in (set_window_app_id, set_dark_title_bar):
            try:
                apply(hwnd)
            except Exception:
                pass

    def closed():
        state["closing"] = True

    def watch():
        end = time.time() + 120
        while not state["closing"] and not answers(url):
            if time.time() > end:
                break
            time.sleep(0.4)
        if state["closing"]:
            return
        window.load_url(url)
        # gone for ~10 s (Shut down, a crash): nothing left to show; a restart is shorter
        misses = 0
        while not state["closing"]:
            time.sleep(2)
            misses = 0 if answers(url) else misses + 1
            if misses >= 5:
                state["closing"] = True
                try:
                    window.destroy()
                except Exception:
                    pass
                return

    window.events.shown += shown
    window.events.closed += closed
    threading.Thread(target=watch, daemon=True).start()
    icon = os.path.join(WEB, "nomad-server.ico")
    webview.start(private_mode=False, storage_path=os.path.join(base, "data", "webview"),
                  icon=icon if os.path.isfile(icon) else None)
    if owner and answers(url):
        shutdown(url, base)
    return 0
