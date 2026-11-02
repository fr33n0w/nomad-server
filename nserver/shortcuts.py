"""Settings > Server > Shortcuts: an nS icon on the desktop and in the app menu
that opens the web UI in its own window. If the server already runs, the icon
only opens the window; otherwise it starts the server hidden (--service).

- Windows: "nomad-server.lnk" on the Desktop and in the Start menu. A program
  cannot pin to the taskbar on Windows 10/11: right-click the Start menu entry
  > Pin to taskbar.
- Linux desktop: a .desktop file in ~/.local/share/applications and on the Desktop.
- macOS: nomad-server.command on the Desktop (drag it to the Dock).
Servers without a desktop, Docker and Termux: not offered.
"""
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "web", "nomad-server.ico")
PNG = os.path.join(HERE, "web", "icon-256.png")
KINDS = ("desktop", "menu")
HINT = {
    "windows": "To pin it to the taskbar: right-click nomad-server in the Start menu > Pin to taskbar.",
    "linux": "From the app menu you can add it to the panel or the favourites.",
    "mac": "Drag it from the Desktop to the Dock to keep it there.",
}


def system():
    if os.name == "nt":
        return "windows"
    if os.path.exists("/.dockerenv") or "com.termux" in os.environ.get("PREFIX", ""):
        return None
    if sys.platform == "darwin":
        return "mac"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_CURRENT_DESKTOP"):
        return "linux"
    return None


def command(base, script):
    """What the icon runs: the window, and the server hidden if it is not running yet."""
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return [sys.executable, "--app", "--service", "--base", base]
    py = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(py), "pythonw.exe")
        py = w if os.path.isfile(w) else py
    return [py, script, "--app", "--service", "--base", base]


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def _windows_folder(csidl):
    """Desktop (0x10) or Start menu Programs (0x02), also when moved (OneDrive)."""
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) != 0:
        raise OSError("Windows did not say where that folder is")
    return buf.value


def _xdg_desktop():
    try:
        with open(_home(".config", "user-dirs.dirs"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("XDG_DESKTOP_DIR="):
                    value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", _home())
                    if value:
                        return value
    except OSError:
        pass
    return _home("Desktop")


def path(kind, where):
    if where == "windows":
        return os.path.join(_windows_folder(0x10 if kind == "desktop" else 0x02), "nomad-server.lnk")
    if where == "linux":
        folder = _xdg_desktop() if kind == "desktop" else _home(".local", "share", "applications")
        return os.path.join(folder, "nomad-server.desktop")
    if where == "mac" and kind == "desktop":
        return os.path.join(_home("Desktop"), "nomad-server.command")
    return None


def _ours(target, base):
    """The icon starts this server folder (a .lnk keeps paths in UTF-16)."""
    try:
        with open(target, "rb") as f:
            data = f.read(65536)
    except OSError:
        return False
    if os.name == "nt":
        return any(base.lower().encode(enc) in data.lower() for enc in ("utf-8", "utf-16-le"))
    return any(base.encode(enc) in data for enc in ("utf-8", "utf-16-le"))


def _write_lnk(target, cmd, workdir):
    """IShellLinkW + IPersistFile by ctypes: no extra packages."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    def guid(d1, d2, d3, *d4):
        return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

    def method(obj, index, *args):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(table[index])

    clsid_link = guid(0x00021401, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46)
    iid_link = guid(0x000214F9, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46)
    iid_file = guid(0x0000010B, 0, 0, 0xC0, 0, 0, 0, 0, 0, 0, 0x46)
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    link = ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(clsid_link), None, 1, ctypes.byref(iid_link), ctypes.byref(link)) != 0:
        raise OSError("could not create the shortcut (ShellLink)")
    held = [link]
    try:
        # IShellLinkW: SetDescription 7, SetWorkingDirectory 9, SetArguments 11, SetIconLocation 17, SetPath 20
        method(link, 20, ctypes.c_wchar_p)(link, cmd[0])
        method(link, 11, ctypes.c_wchar_p)(link, subprocess.list2cmdline(cmd[1:]))
        method(link, 9, ctypes.c_wchar_p)(link, workdir)
        method(link, 7, ctypes.c_wchar_p)(link, "nomad-server - NomadNet nodes")
        if os.path.isfile(ICO):
            method(link, 17, ctypes.c_wchar_p, ctypes.c_int)(link, ICO, 0)
        persist = ctypes.c_void_p()
        if method(link, 0, ctypes.c_void_p, ctypes.c_void_p)(link, ctypes.byref(iid_file), ctypes.byref(persist)) != 0:
            raise OSError("could not save the shortcut (IPersistFile)")
        held.append(persist)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if method(persist, 6, ctypes.c_wchar_p, wintypes.BOOL)(persist, target, True) != 0:
            raise OSError("could not write %s" % target)
    finally:
        for obj in reversed(held):
            method(obj, 2)(obj)


def _windows_move(source, target):
    """Through cmd: the Microsoft Store Python writes into AppData land in a
    private copy the Start menu never sees; a child process writes the real one."""
    try:
        os.remove(target)
    except OSError:
        pass
    args = ["move", "/y", source, target] if source else ["del", "/f", "/q", target]
    subprocess.run(["cmd", "/c"] + args, capture_output=True, timeout=20,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if source and os.path.exists(source):
        os.remove(source)
        raise OSError("could not write %s" % target)


def _write_text(target, text):
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.chmod(target, 0o755)


def status(base):
    where = system()
    out = {"supported": bool(where), "system": where, "desktop": False, "menu": False,
           "menu_supported": where in ("windows", "linux"), "hint": HINT.get(where, ""), "note": ""}
    if not where:
        out["note"] = "No desktop here (server, Docker or Termux): nothing to put an icon on."
        return out
    try:
        for kind in KINDS:
            target = path(kind, where)
            out[kind] = bool(target) and _ours(target, base)
    except OSError as e:
        out.update(supported=False, note=str(e))
    return out


def set_on(base, script, kind, on):
    if kind not in KINDS:
        raise ValueError("unknown shortcut")
    current = status(base)
    if not current["supported"]:
        raise ValueError(current["note"] or "shortcuts cannot be made here")
    where = current["system"]
    target = path(kind, where)
    if not target:
        raise ValueError("not available on this system")
    if not on:
        if _ours(target, base):
            if where == "windows":
                _windows_move(None, target)
            else:
                os.remove(target)
        return status(base)
    cmd = command(base, script)
    if where == "windows":
        temp = os.path.join(base, "data", "nomad-server.lnk.tmp")
        os.makedirs(os.path.dirname(temp), exist_ok=True)
        _write_lnk(temp, cmd, os.path.dirname(script))
        _windows_move(temp, target)
    elif where == "linux":
        entry = ["[Desktop Entry]", "Type=Application", "Name=nomad-server",
                 "Comment=NomadNet nodes with a web interface",
                 "Exec=" + " ".join(shlex.quote(c) for c in cmd), "Path=" + os.path.dirname(script),
                 "Icon=" + PNG, "Terminal=false", "Categories=Network;", ""]
        _write_text(target, "\n".join(entry))
        if kind == "desktop":
            import shutil
            if shutil.which("gio"):
                subprocess.run(["gio", "set", target, "metadata::trusted", "true"], capture_output=True, timeout=10)
    else:
        _write_text(target, "#!/bin/sh\nexec %s\n" % " ".join(shlex.quote(c) for c in cmd))
    return status(base)
