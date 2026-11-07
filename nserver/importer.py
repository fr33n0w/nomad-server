"""Import of a node served by another program (NomadNet, rns_server_page.py
and similar): finds folders with an identity and pages/*.mu, then copies
identity, pages, files and old counters into a new, stopped node."""
import collections
import os
import re
import shutil
import string
import sys
import time

import RNS

from . import backup, config, log
from .node import Node

SCAN_SECONDS = 25
SCAN_DEPTH = 7
COUNT_CAP = 5000
# never worth walking into, on any system
SKIP = {"node_modules", "__pycache__", "site-packages", "dist-packages", "venv", ".venv", "AppData",
        "Library", "Windows", "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin",
        "System Volume Information", "proc", "sys", "dev", "run", "snap", "tmp", "boot", "lost+found",
        "media_cache", ".trash", ".git"}
DOT_OK = {".nomadnetwork", ".config", "nomadnetwork"}
CFG_NAMES = ("config", "config.cfg", "node.cfg", "config.ini", "server.cfg", "settings.cfg")


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _read_cfg(path):
    """Very small ini reader: {section: {key: value}}, comments and quotes stripped."""
    out, sec = {}, ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s[0] in "#;":
                    continue
                m = re.match(r"^\[+\s*([^\]]+?)\s*\]+$", s)
                if m:
                    sec = m.group(1).lower()
                    continue
                if "=" in s:
                    k, v = s.split("=", 1)
                    out.setdefault(sec, {})[k.strip().lower()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _config_files(root, cfg_dir):
    seen = []
    for d in dict.fromkeys([cfg_dir, root]):
        for n in CFG_NAMES:
            # rns_server_page: config.cfg.owr overrides config.cfg, so it is read first
            for p in (os.path.join(d, n + ".owr"), os.path.join(d, n)):
                if os.path.isfile(p) and os.path.getsize(p) < 512 * 1024:
                    seen.append(p)
    return seen


def _guess_name(cfgs, root):
    for p in cfgs:
        c = _read_cfg(p)
        for sec, key in (("node", "node_name"), ("rns_server", "display_name"), ("node", "name"), ("", "display_name")):
            v = c.get(sec, {}).get(key, "")
            if v and v.lower() not in ("none", "null"):
                return " ".join(v.split())[:60]
        for sec in c.values():
            v = sec.get("display_name") or sec.get("node_name")
            if v:
                return " ".join(v.split())[:60]
    base = os.path.basename(os.path.normpath(root))
    if base.lower() in ("storage", "data", "node") or base.startswith("."):
        base = os.path.basename(os.path.dirname(os.path.normpath(root))).lstrip(".") or base
    return base[:60] or "Imported node"


def _cfg_path(cfgs, key, root):
    """pages_path / files_path set in the old config, relative to the node folder."""
    for p in cfgs:
        for sec in _read_cfg(p).values():
            v = sec.get(key)
            if v:
                v = os.path.expanduser(v)
                return v if os.path.isabs(v) else os.path.join(root, v)
    return None


def _count(folder, ext=None):
    n, size = 0, 0
    for cur, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in names:
            if ext and not f.endswith(ext):
                continue
            n += 1
            try:
                size += os.path.getsize(os.path.join(cur, f))
            except OSError:
                pass
            if n >= COUNT_CAP:
                return n, size
    return n, size


def detect(path, server=None):
    """What an old node folder holds, or None if there are no pages. Accepts
    the node folder, NomadNet's ~/.nomadnetwork or its storage/."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return None
    root = path
    # NomadNet: ~/.nomadnetwork/storage holds identity, pages and files
    if not os.path.isfile(os.path.join(root, "identity")) and os.path.isdir(os.path.join(root, "storage", "pages")):
        root = os.path.join(root, "storage")
    # NomadNet keeps its config one level above storage/
    cfg_dir = os.path.dirname(root) if os.path.basename(root) == "storage" else root
    cfgs = _config_files(root, cfg_dir)
    pages = _cfg_path(cfgs, "pages_path", root) or os.path.join(root, "pages")
    files = _cfg_path(cfgs, "files_path", root) or os.path.join(root, "files")
    if not os.path.isdir(pages):
        return None
    kind = "nomadnet" if os.path.isfile(os.path.join(cfg_dir, "config")) and os.path.basename(root) == "storage" \
        else "nomad-server" if os.path.isfile(os.path.join(root, "node.cfg")) else "server"
    mu, mu_size = _count(pages, ".mu")
    all_pages, pages_size = _count(pages)
    nfiles, files_size = _count(files) if os.path.isdir(files) else (0, 0)
    info = {"path": path, "root": root, "kind": kind, "name": _guess_name(cfgs, root),
            "pages_path": pages, "files_path": files if os.path.isdir(files) else "",
            "mu": mu, "pages": all_pages, "files": nfiles, "size": pages_size + files_size,
            "index": os.path.isfile(os.path.join(pages, "index.mu")),
            "identity": "", "address": "", "used_by": "", "visits": ""}
    for v in (os.path.join(root, "data", "visits.json"), os.path.join(root, "visits.json")):
        if os.path.isfile(v):
            info["visits"] = v
            break
    ident_file = os.path.join(root, "identity")
    if os.path.isfile(ident_file) and os.path.getsize(ident_file) == 64:
        try:
            with open(ident_file, "rb") as f:
                ident = backup.identity_from_bytes(f.read())
            info["identity"], info["address"] = ident_file, backup.node_address(ident)
            if server:
                try:
                    backup.check_unused(server, ident)
                except ValueError:
                    info["used_by"] = next((n.id for n in server.nodes.values() if n.address == info["address"]), "?")
        except ValueError:
            pass
    return info


def _roots():
    home = os.path.expanduser("~")
    roots = [home]
    if sys.platform == "win32":
        for d in string.ascii_uppercase:
            r = d + ":\\"
            if os.path.isdir(r) and _norm(r) not in (_norm(x) for x in roots):
                roots.append(r)
    else:
        roots += [p for p in ("/srv", "/opt", "/home", "/root", "/var/lib", "/usr/local") if os.path.isdir(p)]
    return roots


def scan(server, seconds=SCAN_SECONDS):
    """Walks the machine for old nodes. Stops after `seconds`: complete=False."""
    t0, found, seen = time.time(), [], set()
    own = _norm(server.nodes_dir)
    own_up = _norm(upload_dir(server))
    known = [os.path.join(os.path.expanduser("~"), ".nomadnetwork"),
             os.path.join(os.path.expanduser("~"), ".config", "nomadnetwork")]
    if sys.platform != "win32" and os.path.isdir("/home"):
        try:
            for u in os.listdir("/home"):
                known += [os.path.join("/home", u, ".nomadnetwork"), os.path.join("/home", u, ".config", "nomadnetwork")]
        except OSError:
            pass

    def add(path):
        try:
            info = detect(path, server)
        except OSError:
            return
        # an identity is required here: pages without one are mostly browser caches of other nodes
        if info and info["mu"] and info["identity"] and _norm(info["root"]) not in seen:
            seen.add(_norm(info["root"]))
            found.append(info)

    for k in known:
        if os.path.isdir(k):
            add(k)
    complete, dirs_seen = True, 0
    for root in _roots():
        # breadth first: shallow nodes are found before the time limit
        queue = collections.deque([(root, 0)])
        while queue:
            if time.time() - t0 > seconds:
                complete = False
                break
            cur, depth = queue.popleft()
            n = _norm(cur)
            if n in (own, own_up) or n.startswith(own + os.sep) or n.startswith(own_up + os.sep) or n in seen:
                continue
            try:
                entries = [e for e in os.scandir(cur) if e.is_dir()]
            except OSError:
                continue
            dirs_seen += 1
            names = {e.name for e in entries}
            # a linked pages/ counts, but links are never walked (loops)
            entries = [e for e in entries if not e.is_symlink()]
            if "pages" in names or "storage" in names:
                add(cur)
                # a real node (identity next to pages/) is not walked further
                if os.path.isfile(os.path.join(cur, "identity")):
                    continue
            if depth >= SCAN_DEPTH:
                continue
            for e in entries:
                if e.name in SKIP or (e.name.startswith(".") and e.name not in DOT_OK):
                    continue
                queue.append((e.path, depth + 1))
        if not complete:
            break
    order = {"nomadnet": 0, "server": 1, "nomad-server": 2}
    found.sort(key=lambda i: (bool(i["used_by"]), order.get(i["kind"], 1), i["name"].lower(), i["root"].lower()))
    return {"items": found, "complete": complete, "seconds": round(time.time() - t0, 1), "dirs": dirs_seen}


def _node_hint(path):
    """Cheap look: does this folder hold a node? (pages/ with a .mu, here or in storage/)"""
    for pages in (os.path.join(path, "pages"), os.path.join(path, "storage", "pages")):
        try:
            if any(n.endswith(".mu") for n in os.listdir(pages)):
                return True
        except OSError:
            pass
    return False


def places(server):
    home = os.path.expanduser("~")
    out = [{"name": "Home", "path": home}]
    for p in (os.path.join(home, ".nomadnetwork"), os.path.join(home, ".config", "nomadnetwork")):
        if os.path.isdir(p):
            out.append({"name": "NomadNet", "path": p})
            break
    out.append({"name": "nomad-server", "path": server.base})
    if sys.platform == "win32":
        out += [{"name": d + ":", "path": d + ":\\"} for d in string.ascii_uppercase if os.path.isdir(d + ":\\")]
    else:
        out.append({"name": "/", "path": "/"})
    return out


def _crumbs(path):
    out, cur = [], path
    while True:
        parent = os.path.dirname(cur)
        out.append({"name": os.path.basename(cur) or cur, "path": cur})
        if parent == cur:
            break
        cur = parent
    return list(reversed(out))


def browse(path, server=None):
    """Folders of one directory for the manual picker ('' = home; on Windows '/' = the drives)."""
    import socket
    host = socket.gethostname()
    if not path:
        path = os.path.expanduser("~")
    if sys.platform == "win32" and path in ("/", "\\"):
        drives = [d + ":\\" for d in string.ascii_uppercase if os.path.isdir(d + ":\\")]
        return {"path": "", "parent": None, "host": host, "crumbs": [], "places": places(server) if server else [],
                "dirs": [{"name": d, "path": d, "node": False} for d in drives]}
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise ValueError("not a folder: %s" % path)
    dirs = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": e.path, "node": len(dirs) < 2000 and _node_hint(e.path)})
            except OSError:
                pass
    except PermissionError:
        raise ValueError("no permission to read %s" % path)
    parent = os.path.dirname(path)
    if parent == path:
        parent = "/" if sys.platform == "win32" else None
    return {"path": path, "parent": parent, "host": host, "crumbs": _crumbs(path),
            "places": places(server) if server else [], "dirs": dirs[:2000]}


PICK_TK = """import sys, tkinter as tk
from tkinter import filedialog
r = tk.Tk(); r.withdraw(); r.attributes("-topmost", True); r.update()
p = filedialog.askdirectory(parent=r, initialdir=sys.argv[1] or None, mustexist=True,
                            title="nomad-server: choose the folder of the old node")
sys.stdout.write(p or "")
"""


def pick_folder(start=""):
    """Opens the system's folder chooser on the server's desktop; '' if cancelled."""
    import subprocess
    start = start if start and os.path.isdir(start) else os.path.expanduser("~")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose the folder of the old node" '
               'default location (POSIX file "%s"))' % start.replace('"', "")]
    elif sys.platform != "win32" and shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", "--filename=" + start.rstrip("/") + "/",
               "--title=Choose the folder of the old node"]
    elif sys.platform != "win32" and shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", start]
    else:
        py = sys.executable
        if sys.platform == "win32":
            # pythonw: no console window flashing behind the chooser
            w = os.path.join(os.path.dirname(py), "pythonw.exe")
            py = w if os.path.isfile(w) else py
        cmd = [py, "-c", PICK_TK, start]
    r = subprocess.run(cmd, capture_output=True, env=env)
    path = r.stdout.decode("utf-8", "replace").strip()
    return os.path.abspath(path) if path else ""


def upload_dir(server):
    return os.path.join(server.base, "data", "import-upload")


def upload_part(server, session, rel, body):
    """One file of a folder sent by a remote browser; returns the folder it lands in."""
    if not re.fullmatch(r"[a-z0-9]{8,32}", session or ""):
        raise ValueError("bad upload session")
    parts = str(rel).replace("\\", "/").split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise ValueError("unsafe path: %s" % rel)
    stage = os.path.join(upload_dir(server), session)
    if not os.path.isdir(stage):
        # a new upload: older ones left by a closed window go away
        os.makedirs(stage)
        for old in os.listdir(upload_dir(server)):
            p = os.path.join(upload_dir(server), old)
            if old != session and time.time() - os.path.getmtime(p) > 3600:
                shutil.rmtree(p, ignore_errors=True)
    dest = os.path.normpath(os.path.join(stage, *parts))
    if not dest.startswith(os.path.normpath(stage) + os.sep):
        raise ValueError("unsafe path: %s" % rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(body)
    return os.path.join(stage, parts[0])


def _ignore(cur, names):
    return [n for n in names if n in ("__pycache__",) or n.endswith((".pyc", ".tmp"))]


def import_node(server, path, name=""):
    info = detect(path, server)
    if not info or not info["mu"]:
        raise ValueError("nothing to import: no .mu pages in %s" % path)
    if _norm(info["root"]).startswith(_norm(server.nodes_dir) + os.sep):
        raise ValueError("this folder is already a node of this server")
    if info["used_by"]:
        raise ValueError("node %s already uses this identity: two nodes cannot share one" % info["used_by"])
    name = " ".join(str(name or info["name"]).split())[:60] or "Imported node"
    folder = server.free_folder(name)
    try:
        os.makedirs(os.path.join(folder, "data"))
        with open(os.path.join(folder, "node.cfg"), "w", encoding="utf-8", newline="\n") as f:
            f.write(config.NODE_TEMPLATE.format(name=name))
        shutil.copytree(info["pages_path"], os.path.join(folder, "pages"), ignore=_ignore, ignore_dangling_symlinks=True)
        if info["files_path"]:
            shutil.copytree(info["files_path"], os.path.join(folder, "files"), ignore=_ignore, ignore_dangling_symlinks=True)
        else:
            os.makedirs(os.path.join(folder, "files"))
        if info["identity"]:
            shutil.copyfile(info["identity"], os.path.join(folder, "identity"))
        else:
            RNS.Identity().to_file(os.path.join(folder, "identity"))
        if info["visits"]:
            shutil.copyfile(info["visits"], os.path.join(folder, "data", "visits.json"))
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    node = Node(server, folder)
    # stopped like every new node: the old server may still be serving this address
    node.cfg.set({"node": {"enabled": "no"}})
    server.nodes[node.id] = node
    staged = _norm(info["root"]).startswith(_norm(upload_dir(server)) + os.sep)
    log.line("Node %s imported from %s (%s, %d pages, %d files), address %s: stop the old server, then press Start"
             % (node.id, "a folder sent by the browser" if staged else info["root"], info["kind"],
                info["pages"], info["files"], node.address))
    if staged:
        rel = os.path.relpath(info["root"], upload_dir(server))
        shutil.rmtree(os.path.join(upload_dir(server), rel.split(os.sep)[0]), ignore_errors=True)
    return node, info
