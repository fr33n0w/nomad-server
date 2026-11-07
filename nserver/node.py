"""One NomadNet node: its identity, pages/, files/, optional plugin and
counters. Serves requests the same way NomadNet's own node does (static and
executable pages, .allowed lists, /file/ downloads, /media/ images)."""
import collections
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time

import RNS
import RNS.vendor.umsgpack as msgpack

from . import config, log, PROJECT_URL
from .stats import Stats, REFRESH_SECONDS

IS_WINDOWS = sys.platform.startswith("win")
MEDIA_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff")
EXEC_TIMEOUT = 30

DEFAULT_INDEX = """>Default Home Page

This node is serving pages, but the home page file (index.mu) was not found in the page storage directory.
"""
IDENTIFY_PAGE = """>Identification required

This node shows its pages only to visitors who identify themselves.

Identify to this node, then load the page again:
 - NomadNet: Identify, in the browser menu of this node
 - rBrowser: the Auto ID or Identify button

Your identity tells the node who you are; it is not sent anywhere else.
"""
BLOCKED_PAGE = """>Access blocked

`c
`Ff55Your access to this node is blocked.`f

Reason: `!{reason}`!
`a

This LXMF address is blocked by the node operator: `F888{who}`f
"""
DEFAULT_NOTALLOWED = """>Request Not Allowed

You are not authorised to carry out the request.
"""
# Alignment in micron holds for the whole line, so `c and `a go on lines of their own
VISITS_LINE = (b"`F888visits  `Ffb4{{visits}}`F888 this page  \xc2\xb7  `Ffb4{{visits_total}}`F888 total"
               b"  \xc2\xb7  `Ffb4{{visits_today}}`F888 today`f\n")
WATERMARK_LINE = ("`F777served by `F5cdnomad-server`F777  ·  `[%s`%s]`f\n"
                  % (PROJECT_URL.replace("https://", ""), PROJECT_URL)).encode("utf-8")
# Partials: `{:/page/x.mu`...} or `{<node hash>:/page/x.mu`...}
PARTIAL_RE = re.compile(rb"\{[0-9a-fA-F]{0,32}:(/page/[^`}\s]+)")
SAMPLE_INDEX = """>{name}

Welcome! This page is served by nomad-server.

Edit `*pages/index.mu`* in the node folder to change it.

-
`F888Visits: {{{{visits}}}} this page - {{{{visits_total}}}} total`f
"""


def exec_command(path):
    """How to run an executable page or .allowed list, or None to read it as text.
    Outside Windows NomadNet's rule: the executable bit. On Windows, which has no
    such bit, a file whose first line is a Python shebang runs with Python."""
    if not IS_WINDOWS:
        return [path] if os.access(path, os.X_OK) else None
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except OSError:
        return None
    if first.startswith(b"#!") and b"python" in first:
        return [python_exe(), path]
    return None


def python_exe():
    # Compiled into an exe, sys.executable is the exe: use the Python on the PATH
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return shutil.which("python") or shutil.which("py") or "python"
    return sys.executable


MAZE_PATH = "/page/trap/maze.mu"
MAZE_FILE = "/file/trap/maze"
# honeypot answer modes that serve the maze: fake pages, fake files, or both
MAZE_MODES = ("maze", "maze_files", "maze_both")
MAZE_EXTS = ("txt", "log", "csv", "cfg", "json")
# distinct pages kept per honeypot peer record
PAGES_KEPT = 60


def maze_of(entry):
    """How long a link has been walking the maze: fake pages, bytes, first and last."""
    if not entry.get("maze"):
        return {}
    return {"maze": entry["maze"], "maze_files": entry.get("maze_files", 0), "maze_bytes": entry.get("maze_bytes", 0),
            "maze_first": entry["maze_first"], "maze_last": entry["maze_last"], "maze_got": list(entry.get("maze_got", [])),
            "maze_wait": round(entry.get("maze_wait", 0), 1)}
MAZE_WORDS = ("relay mesh antenna lora transport beacon gateway router node packet link announce path "
              "battery solar repeater channel frequency config backup mirror index notes draft archive").split()
_names = {}
NAME_RETRY = 60


def name_of_identity(identity):
    """What a peer calls itself: its LXMF name, else 'node <name>' of the NomadNet
    node with the same identity (crawlers usually run on a node)."""
    name = announced_name(RNS.Destination.hash_from_name_and_identity("lxmf.delivery", identity))
    if not name:
        node = announced_name(RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", identity))
        name = ("node " + node) if node else ""
    return name


_asked = {}
ASK_AGAIN = 1800


def ask_network_for_name(identity):
    """Path requests for the peer's LXMF address and NomadNet node: whoever knows
    them answers with their announce, name included, and the peer becomes known
    here. Once per identity every 30 minutes; nothing comes back if it has neither."""
    key = RNS.hexrep(identity.hash, delimit=False)
    if time.time() - _asked.get(key, 0) < ASK_AGAIN:
        return
    _asked[key] = time.time()
    _names.pop(key, None)  # look again as soon as an answer may be in
    for aspect in ("lxmf.delivery", "nomadnetwork.node"):
        dest = RNS.Destination.hash_from_name_and_identity(aspect, identity)
        try:
            if not RNS.Transport.has_path(dest):
                RNS.Transport.request_path(dest)
        except Exception:
            pass


def peer_name(identity_hex):
    """Name for a peer known only by its identity hash, looked up again later
    (its announces may arrive after it identified). Cached: every recall asks rnsd."""
    if not identity_hex:
        return ""
    hit = _names.get(identity_hex)
    if hit and (hit[0] or time.time() - hit[1] < NAME_RETRY):
        return hit[0]
    name = ""
    try:
        ident = RNS.Identity.recall(bytes.fromhex(identity_hex), from_identity_hash=True)
        if ident:
            name = name_of_identity(ident)
    except Exception:
        pass
    _names[identity_hex] = (name, time.time())
    return name


def link_via(link):
    """Hops of an incoming link and its interface ('' behind rnsd: only rnsd knows it)."""
    try:
        hops = max(1, round((link.establishment_timeout - RNS.Link.KEEPALIVE) / RNS.Link.ESTABLISHMENT_TIMEOUT_PER_HOP))
    except Exception:
        hops = 0
    iface = link.attached_interface
    if iface is None or type(iface).__name__ == "LocalClientInterface":
        return "", hops
    return str(getattr(iface, "name", "") or iface), hops


def path_back(entry, dest_hash):
    try:
        r = RNS.Reticulum.get_instance()
        name = r.get_next_hop_if_name(dest_hash) if r else None
        if name:
            entry["via"], entry["via_path"] = str(name), True
    except Exception:
        pass


def via_of(e):
    return {"via": e.get("via", ""), "via_path": bool(e.get("via_path")), "hops": e.get("hops", 0), "addr": e.get("addr", 0)}


def announced_name(dest):
    """The name in the last announce heard from a destination (LXMF sends
    msgpack [name, stamp cost], NomadNet nodes the bare name)."""
    try:
        data = RNS.Identity.recall_app_data(dest)
    except Exception:
        return ""
    if not data:
        return ""
    try:
        if data[0] in range(0x90, 0xa0) or data[0] == 0xdc:
            data = msgpack.unpackb(data)[0] or b""
        name = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    except Exception:
        return ""
    return " ".join(name.split())[:40]


def short(h):
    return RNS.hexrep(h, delimit=False)[:8] if h else "-"


class Node:
    def __init__(self, server, folder):
        self.server = server
        self.folder = folder
        self.id = os.path.basename(folder)
        self.cfg = config.Config(os.path.join(folder, "node.cfg"), config.NODE_DEFAULTS)
        self.pages_path = os.path.join(folder, "pages")
        self.files_path = os.path.join(folder, "files")
        self.data_path = os.path.join(folder, "data")
        self.stats = Stats(self.data_path)
        self.lock = threading.RLock()
        self.state = "stopped"
        self.error = ""
        self.identity = None
        self.destination = None
        self.extra = []  # honeypot: more addresses (N2, N3) serving the same pages
        self.multi = {}  # honeypot: LXMF -> {address number: last time}
        self.plugin = None
        self.registered = set()
        self.partials = set()
        self.by_who = {}
        self.flagged = set()
        self.links = {}
        self.rate = {}
        self.slow_pending = 0
        self.started = None
        self.last_announce = None
        self.last_rescan = 0
        self.announce_due = None
        self.pages_count = 0
        self.files_count = 0
        self.rescan()

    # ---- settings ----

    @property
    def name(self):
        return self.cfg.get("node", "name") or self.id

    @property
    def address(self):
        return RNS.hexrep(self.destination.hash, delimit=False) if self.destination else self.address_from_identity()

    def address_from_identity(self):
        try:
            ident = self.identity or RNS.Identity.from_file(os.path.join(self.folder, "identity"))
            return RNS.hexrep(RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", ident), delimit=False)
        except Exception:
            return ""

    def log(self, text, kind="info"):
        log.line(text, node=self.id, kind=kind)

    # ---- start / stop ----

    def start(self):
        with self.lock:
            if self.state == "running":
                return
            self.cfg.reload()
            self.error = ""
            try:
                self.load_identity()
                self.destination = RNS.Destination(self.identity, RNS.Destination.IN, RNS.Destination.SINGLE,
                                                   "nomadnetwork", "node")
            except KeyError:
                self.fail("another node uses the same identity")
                return
            except Exception as e:
                self.fail("could not start: %s" % e)
                return
            self.destination.set_link_established_callback(lambda link: self.link_open(link, 1))
            self.extra = []
            try:
                for n in range(2, self.address_count() + 1):
                    d = RNS.Destination(self.load_identity(n), RNS.Destination.IN, RNS.Destination.SINGLE, "nomadnetwork", "node")
                    d.set_link_established_callback(lambda link, n=n: self.link_open(link, n))
                    self.extra.append(d)
            except Exception as e:
                self.log("Extra honeypot addresses not started: %s" % e, "error")
            os.makedirs(self.pages_path, exist_ok=True)
            os.makedirs(self.files_path, exist_ok=True)
            self.load_plugin()
            self.registered = set()
            self.rescan()
            self.started = time.time()
            self.state = "running"
            if self.cfg.getbool("node", "announce_at_start"):
                self.announce_due = time.time() + random.randint(5, 15)
            self.log("Started, address %s, %d pages, %d files" % (self.address, self.pages_count, self.files_count))
            for n, d in enumerate(self.extra, 2):
                self.log("Also on address N%d %s" % (n, RNS.hexrep(d.hash, delimit=False)))
            if self.is_honeypot:
                self.preload_crawlers()

    def preload_crawlers(self):
        """Link hopping keeps counting across restarts: refill it from the saved
        peer history, inside the hopping window."""
        window = max(60, self.cfg.getint("honeypot", "hop_seconds"))
        since = time.time() - window
        for rec in self.peers():
            if rec.get("who") and (rec.get("until") or 0) >= since:
                links = self.by_who.setdefault(rec["who"], {})
                links[rec["link"]] = [rec["since"], rec.get("requests", 0)]

    def check_hopping(self, entry, now):
        """Crawlers open a new link for every page; people keep one link open while
        they browse (NomadNet, rBrowser). Many links with one or two pages each from
        the same identity is a crawler, however slow it goes."""
        need = max(2, self.cfg.getint("honeypot", "hop_links"))
        window = max(60, self.cfg.getint("honeypot", "hop_seconds"))
        links = self.by_who.setdefault(entry["who"], {})
        links[short(entry["link"].link_id)] = [entry["since"], entry["requests"]]
        for k in [k for k, v in links.items() if v[0] < now - window]:
            del links[k]
        hops = sum(1 for v in links.values() if v[1] <= 2)
        if hops >= need and entry["who"] not in self.flagged:
            self.flagged.add(entry["who"])
            entry["crawler"] = True
            self.stats.bump("crawler_links")
            why = "%d links with one or two pages each in %d min" % (hops, window // 60)
            self.log("LXMF %s looks like a crawler: %s" % (entry["who"], why), "warn")
            if self.is_honeypot and self.cfg.getbool("honeypot", "autoblock"):
                self.catch(entry, why)

    def check_multi(self, entry, who):
        """The same LXMF on every address of this honeypot within multi_seconds:
        people pick one node from a list, crawlers take them all."""
        window = self.cfg.getint("honeypot", "multi_seconds")
        if window <= 0:
            return
        now = time.time()
        seen = self.multi.setdefault(who, {})
        seen[entry["addr"]] = now
        for k in [k for k, t in seen.items() if t < now - window]:
            del seen[k]
        total = len(self.extra) + 1
        if len(seen) >= total:
            why = "on all %d addresses of the honeypot in %s" % (total, log.span(now - min(seen.values())))
            self.log("LXMF %s connected %s" % (who, why), "warn")
            self.multi.pop(who, None)
            self.catch(entry, why)

    def fail(self, why):
        self.state = "error"
        self.error = why
        self.destination = None
        self.extra = []
        self.log("Not started: " + why, "error")

    def stop(self):
        with self.lock:
            if self.state != "running":
                self.state = "stopped"
                return
            self.state = "stopped"
            for entry in list(self.links.values()):
                try:
                    entry["link"].teardown()
                except Exception:
                    pass
            self.links = {}
            for d in self.dests():
                for path in list(self.registered):
                    d.deregister_request_handler(path)
                try:
                    RNS.Transport.deregister_destination(d)
                except Exception:
                    pass
            self.registered = set()
            self.destination = None
            self.extra = []
            self.stop_plugin()
            self.stats.flush(force=True)
            self.log("Stopped")

    def restart(self):
        self.stop()
        self.start()

    def address_count(self):
        if not self.is_honeypot:
            return 1
        return min(3, max(1, self.cfg.getint("honeypot", "addresses")))

    def dests(self):
        return ([self.destination] if self.destination else []) + self.extra

    def extra_addresses(self):
        """Addresses N2, N3 of a honeypot, also when stopped (from identity2, identity3)."""
        if self.extra:
            return [RNS.hexrep(d.hash, delimit=False) for d in self.extra]
        out = []
        for n in range(2, self.address_count() + 1):
            path = os.path.join(self.folder, "identity%d" % n)
            if not os.path.isfile(path):
                out.append("")  # made at the first start
                continue
            try:
                ident = RNS.Identity.from_file(path)
                out.append(RNS.hexrep(RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", ident), delimit=False))
            except Exception:
                out.append("")
        return out

    def load_identity(self, n=1):
        if n > 1:
            # honeypot addresses N2, N3: their own identity, made the first time
            path = os.path.join(self.folder, "identity%d" % n)
            if os.path.isfile(path):
                ident = RNS.Identity.from_file(path)
                if ident is not None:
                    return ident
            ident = RNS.Identity()
            ident.to_file(path)
            return ident
        path = os.path.join(self.folder, "identity")
        if os.path.isfile(path):
            self.identity = RNS.Identity.from_file(path)
            if self.identity is None:
                raise ValueError("the identity file is not valid")
        else:
            self.identity = RNS.Identity()
            self.identity.to_file(path)
            self.log("Created a new identity")

    # ---- plugin ----

    def load_plugin(self):
        self.plugin = None
        name = self.cfg.get("node", "plugin").strip()
        path = os.path.join(self.folder, name) if name else ""
        if not path or not os.path.isfile(path):
            return
        # Its own modules (import board, ...) come from the node folder
        if self.folder not in sys.path:
            sys.path.insert(0, self.folder)
        try:
            spec = importlib.util.spec_from_file_location("nsplugin_" + self.id, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "start"):
                try:
                    module.start(self)
                except TypeError:
                    module.start()
            self.plugin = module
            self.log("Plugin %s loaded" % name)
        except Exception as e:
            self.log("Plugin %s not loaded: %s" % (name, e), "error")

    def stop_plugin(self):
        if self.plugin and hasattr(self.plugin, "stop"):
            try:
                self.plugin.stop()
            except Exception as e:
                self.log("Plugin stop: %s" % e, "warn")
        self.plugin = None

    def plugin_paths(self):
        if not self.plugin:
            return []
        try:
            if hasattr(self.plugin, "paths"):
                return list(self.plugin.paths())
            return list(getattr(self.plugin, "PATHS", []))
        except Exception as e:
            self.log("Plugin paths: %s" % e, "error")
            return []

    def plugin_handles(self, path):
        if not self.plugin:
            return False
        try:
            if hasattr(self.plugin, "handles"):
                return bool(self.plugin.handles(path))
            return path in getattr(self.plugin, "PATHS", [])
        except Exception:
            return False

    # ---- pages and files ----

    @staticmethod
    def scan(base, prefix):
        out = []
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in files:
                if f.startswith(".") or f.endswith(".allowed"):
                    continue
                rel = os.path.relpath(os.path.join(root, f), base).replace(os.sep, "/")
                out.append(prefix + "/" + rel)
        return out

    def rescan(self):
        with self.lock:
            if not self.destination:
                # stopped: only the counts, so the card shows what is on disk
                self.pages_count = len(self.scan(self.pages_path, "/page"))
                self.files_count = len(self.scan(self.files_path, "/file"))
                return
            pages = set(self.scan(self.pages_path, "/page")) | set(self.plugin_paths())
            if self.is_honeypot:
                pages.add(MAZE_PATH)
            files = set(self.scan(self.files_path, "/file"))
            self.pages_count, self.files_count = len(pages), len(files)
            if self.is_honeypot:
                files.add(MAZE_FILE)
            if "/page/index.mu" not in pages:
                pages.add("/page/index.mu")
            want = pages | files | {"/media"}
            for path in self.registered - want:
                for d in self.dests():
                    d.deregister_request_handler(path)
            for path in sorted(want - self.registered):
                if path == "/media":
                    gen, compress = self.serve_media, True
                elif path.startswith("/file/"):
                    gen, compress = self.serve_file, 32_000_000
                else:
                    gen, compress = self.serve_page, True
                for d in self.dests():
                    d.register_request_handler(path, response_generator=gen,
                                               allow=RNS.Destination.ALLOW_ALL, auto_compress=compress)
            self.registered = want
            self.partials = self.find_partials(pages)
            self.last_rescan = time.time()

    def find_partials(self, pages):
        """Pages loaded inside other pages: they get no footer."""
        found = set()
        for path in pages:
            if not path.endswith(".mu"):
                continue
            try:
                with open(os.path.join(self.pages_path, *path[len("/page/"):].split("/")), "rb") as f:
                    found.update(m.decode("utf-8", "replace") for m in PARTIAL_RE.findall(f.read(1_000_000)))
            except OSError:
                pass
        return found

    def request_allowed(self, file_path, remote_identity):
        """NomadNet rule: with a <file>.allowed list only those identities may ask."""
        allowed_path = file_path + ".allowed"
        if not os.path.isfile(allowed_path):
            return True
        allowed = []
        try:
            cmd = exec_command(allowed_path)
            if cmd:
                raw = subprocess.run(cmd, stdout=subprocess.PIPE, timeout=EXEC_TIMEOUT,
                                     cwd=os.path.dirname(allowed_path)).stdout
            else:
                with open(allowed_path, "rb") as f:
                    raw = f.read()
            for h in raw.splitlines():
                h = h.strip()
                if len(h) == RNS.Identity.TRUNCATED_HASHLENGTH // 8 * 2:
                    try:
                        allowed.append(bytes.fromhex(h.decode("utf-8")))
                    except ValueError:
                        pass
        except Exception as e:
            self.log("Could not read %s: %s" % (os.path.basename(allowed_path), e), "warn")
        return hasattr(remote_identity, "hash") and remote_identity.hash in allowed

    def rate_ok(self, link_id):
        limit = self.cfg.getint("limits", "requests_per_minute")
        # a maze honeypot never refuses: crawlers walk it until they disconnect
        if limit <= 0 or link_id is None or (self.is_honeypot and self.cfg.get("honeypot", "mode") in MAZE_MODES):
            return True
        now = time.time()
        q = self.rate.setdefault(link_id, collections.deque())
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def refused(self, path, link_id):
        self.stats.bump("limited")
        self.log("Refused %s to link %s: too many requests" % (path, short(link_id)), "warn")

    def serve_page(self, path, data, request_id, link_id, remote_identity, requested_at):
        if not self.rate_ok(link_id):
            self.refused(path, link_id)
            return None
        file_path = os.path.join(self.pages_path, *path[len("/page/"):].split("/"))
        if not self.request_allowed(file_path, remote_identity):
            self.stats.bump("denied")
            self.log("Denied %s to link %s: not in the .allowed list" % (path, short(link_id)), "warn")
            return DEFAULT_NOTALLOWED.encode("utf-8")
        entry = self.links.get(link_id)
        why = self.gate(entry, path)
        if why:
            return self.answer(why, entry, data, request_id, path=path)
        try:
            if self.plugin_handles(path):
                content = self.plugin_page(path, data, link_id, entry)
            elif os.path.isfile(file_path):
                cmd = exec_command(file_path)
                if cmd:
                    content = self.run_page(cmd, file_path, data, link_id, remote_identity)
                else:
                    with open(file_path, "rb") as f:
                        content = f.read()
            elif path == "/page/index.mu":
                content = DEFAULT_INDEX.encode("utf-8")
            else:
                return None
        except Exception as e:
            self.log("Error serving %s: %s" % (path, e), "error")
            return None
        kind = self.count_kind(entry, path, data)
        who, name = (entry.get("who"), entry.get("name", "")) if entry else (None, "")
        if path.endswith(".mu") and path not in self.partials:
            extra = b""
            if self.cfg.getbool("footer", "visits") and b"{{visits" not in content:
                extra += VISITS_LINE
            if self.cfg.getbool("footer", "watermark"):
                extra += WATERMARK_LINE
            if extra:
                if self.cfg.getbool("footer", "center"):
                    extra = b"`c\n" + extra + b"`a\n"
                content = content.rstrip(b"\n") + b"\n\n-\n" + extra
        if self.cfg.getbool("counter", "show_on_pages") and b"{{visits" in content:
            # The numbers include this visit; bytes are counted as sent
            for k, v in self.stats.placeholders(path, kind == "page").items():
                content = content.replace(k, str(v).encode())
        self.stats.request(kind, path, len(content), who, name, entry.get("identity", "") if entry else "")
        self.track(entry, kind, path, len(content))
        return content

    def plugin_page(self, path, data, link_id, entry):
        who = entry.get("who") if entry else None
        try:
            out = self.plugin.page(path, data, who=who, who_name=entry.get("name", "") if entry else "",
                                   link_id=RNS.hexrep(link_id, delimit=False) if link_id else None)
        except Exception as e:
            self.log("Plugin error in %s: %s" % (path, e), "error")
            out = "Error while making this page, try again later."
        return out.encode("utf-8") if isinstance(out, str) else (out or b"")

    def run_page(self, cmd, file_path, data, link_id, remote_identity):
        env = {}
        # SYSTEMROOT: without it Python on Windows does not even start
        for k in ("PATH", "SYSTEMROOT"):
            if k in os.environ:
                env[k] = os.environ[k]
        if IS_WINDOWS:
            env["PYTHONUTF8"] = "1"
        if link_id is not None:
            env["link_id"] = RNS.hexrep(link_id, delimit=False)
        if remote_identity is not None:
            env["remote_identity"] = RNS.hexrep(remote_identity.hash, delimit=False)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and (k.startswith("field_") or k.startswith("var_")):
                    env[k] = str(v)
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=env, timeout=EXEC_TIMEOUT, cwd=os.path.dirname(file_path))
        if r.returncode != 0:
            last = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["no error output"])[-1]
            self.log("%s exited with %d: %s" % (os.path.basename(file_path), r.returncode, last[:200]), "warn")
        # print() on Windows writes \r\n: the page leaves as it would on Linux
        return r.stdout.replace(b"\r\n", b"\n") if IS_WINDOWS else r.stdout

    def serve_file(self, path, data, request_id, link_id, remote_identity, requested_at):
        if not self.rate_ok(link_id):
            self.refused(path, link_id)
            return None
        file_path = os.path.join(self.files_path, *path[len("/file/"):].split("/"))
        if not self.request_allowed(file_path, remote_identity):
            self.stats.bump("denied")
            self.log("Denied %s to link %s: not in the .allowed list" % (path, short(link_id)), "warn")
            return DEFAULT_NOTALLOWED.encode("utf-8")
        entry = self.links.get(link_id)
        why = self.gate(entry, path)
        if why:
            return self.answer(why, entry, data, request_id, file=True, path=path)
        try:
            size = os.path.getsize(file_path)
            handle = open(file_path, "rb")
        except OSError as e:
            self.log("Error serving %s: %s" % (path, e), "error")
            return None
        who, name = (entry.get("who"), entry.get("name", "")) if entry else (None, "")
        self.stats.request("file", path, size, who, name, entry.get("identity", "") if entry else "")
        self.track(entry, "file", path, size)
        return [handle, {"name": os.path.basename(file_path).encode("utf-8")}]

    def serve_media(self, path, data, request_id, link_id, remote_identity, requested_at):
        if not isinstance(data, dict) or not isinstance(data.get("path"), str):
            return None
        if not self.rate_ok(link_id):
            self.refused(data["path"], link_id)
            return None
        rel = data["path"].replace("/media/", "", 1).lstrip("/")
        found = None
        # NomadNet looks in pages/; files/ too, for nodes made with rns_server_page.py
        for base in (self.pages_path, self.files_path):
            jail = os.path.normpath(os.path.abspath(base))
            p = os.path.normpath(os.path.join(jail, rel))
            if p.startswith(jail + os.sep) and len(p) <= 512 and os.path.isfile(p):
                found = p
                break
        if not found or os.path.splitext(found)[1].lower() not in MEDIA_EXTS:
            return False
        if not self.request_allowed(found, remote_identity):
            self.stats.bump("denied")
            return False
        if self.gate(self.links.get(link_id), data["path"]):
            return False
        if os.path.splitext(found)[1].lower() != ".webp":
            found = self.to_webp(found)
            if not found:
                return False
        entry = self.links.get(link_id)
        size = os.path.getsize(found)
        who, name = (entry.get("who"), entry.get("name", "")) if entry else (None, "")
        self.stats.request("media", data["path"], size, who, name, entry.get("identity", "") if entry else "")
        self.track(entry, "media", data["path"], size)
        return [open(found, "rb"), {"name": os.path.basename(found).encode("utf-8")}]

    def to_webp(self, src):
        """png/jpg/... converted once with Pillow, kept in data/media_cache."""
        try:
            from PIL import Image
        except ImportError:
            self.log("%s needs Pillow to be sent (pip install pillow), or save it as .webp" % os.path.basename(src), "warn")
            return None
        import hashlib
        with open(src, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:24]
        out = os.path.join(self.data_path, "media_cache", digest + ".webp")
        if not os.path.isfile(out):
            try:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                img = Image.open(src)
                img.thumbnail((1200, 1200))
                img.save(out + ".tmp", "WEBP", quality=85)
                os.replace(out + ".tmp", out)
            except Exception as e:
                self.log("Could not convert %s: %s" % (os.path.basename(src), e), "warn")
                return None
        return out

    # ---- visitors ----

    def link_open(self, link, addr=1):
        via, hops = link_via(link)
        with self.lock:
            self.links[link.link_id] = {"link": link, "since": time.time(), "who": None, "name": "",
                                        "requests": 0, "refreshes": 0, "bytes": 0, "last": {},
                                        "recent": collections.deque(), "crawler": False, "via": via, "hops": hops,
                                        "addr": addr if self.extra else 0}
            online = len(self.links)
        self.stats.bump("connects")
        link.set_link_closed_callback(self.link_closed)
        link.set_remote_identified_callback(self.link_identified)
        self.log("Connected link %s%s, %s%s, %d online" % (short(link.link_id), (" on N%d" % addr) if self.extra else "",
                                                       log.plural(hops, "hop"),
                                                       (" via " + via) if via else "", online), "connect")

    def link_identified(self, link, identity):
        who = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", identity)
        name = name_of_identity(identity)
        if name:
            _names[RNS.hexrep(identity.hash, delimit=False)] = (name, time.time())
        else:
            ask_network_for_name(identity)
        who_hex = RNS.hexrep(who, delimit=False)
        with self.lock:
            entry = self.links.get(link.link_id)
            if entry:
                # the identity hash too: Reticulum's blackhole works on identities
                entry["who"], entry["name"] = who_hex, name
                entry["identity"] = RNS.hexrep(identity.hash, delimit=False)
                if not entry.get("via"):
                    # behind rnsd: the interface rnsd uses to reach this peer, where it came from
                    threading.Thread(target=path_back, args=(entry, who), daemon=True).start()
        self.log("Identified link %s as LXMF %s%s" % (short(link.link_id), who_hex, (' "%s"' % name) if name else ""), "connect")
        blocked = self.server.blocked_entry(who_hex, self)
        if entry and entry.get("addr") and not entry.get("trapped"):
            self.check_multi(entry, who_hex)
        if self.is_honeypot and who_hex in self.server.crawlers():
            # a crawler seen before, back on a honeypot
            self.server.crawler_seen(who_hex, self, name, RNS.hexrep(identity.hash, delimit=False))
            self.log("LXMF %s is a known crawler, back again" % who_hex, "warn")
        if blocked and blocked.get("mode") == "silent":
            self.stats.bump("blocked")
            self.log("Disconnected link %s: LXMF %s is blocked" % (short(link.link_id), who_hex), "warn")
            link.teardown()

    def gate_answer(self, why, entry, data=None, file=False):
        """What a gated visitor gets: nothing (silent, tarpit), the identify page,
        the blocked page or, on a honeypot, an endless maze of fake pages and files."""
        if why == "maze":
            if not self.is_honeypot or data is False:
                why = "tarpit"
            elif file:
                return self.maze_file(entry, data)
            else:
                return self.maze_page(entry, data)
        if why in ("silent", "tarpit"):
            if why == "tarpit" and entry and not entry.get("tarpit_logged"):
                entry["tarpit_logged"] = True
                who = ("LXMF " + entry["who"]) if entry.get("who") else "link " + short(entry["link"].link_id)
                self.log("Tarpit: %s stays connected, its requests get no answer" % who, "warn")
            return None
        if why == "identify":
            return IDENTIFY_PAGE.encode("utf-8")
        b = self.server.blocked_entry(entry.get("who"), self) or {}
        reason = b.get("reason") or entry.get("trap_reason") or "not given"
        self.log("Sent the blocked page to link %s" % short(entry["link"].link_id), "warn")
        return BLOCKED_PAGE.format(reason=reason, who=entry.get("who") or "not identified").encode("utf-8")

    SLOW_PENDING_MAX = 32

    def answer(self, why, entry, data, request_id, file=False, path=""):
        """gate_answer, with the slow maze: the fake page or file goes out later from a timer."""
        if entry is not None:
            entry["maze_path"] = path     # what was asked, for the maze list
        resp = self.gate_answer(why, entry, data, file=file)
        if why == "maze" and resp is not None and entry and self.is_honeypot:
            delay = self.maze_delay(entry["link"])
            if delay > 0 and self.send_later(entry, request_id, resp, delay):
                return None
        return resp

    def maze_delay(self, link):
        """Seconds to hold a maze answer: 0 (off), fixed, or adaptive = just before the
        visitor's request times out (Reticulum's default: rtt * factor + 11.25 s)."""
        how = self.cfg.get("honeypot", "slow_maze")
        if how == "fixed":
            return max(0.0, min(float(self.cfg.getint("honeypot", "slow_seconds")), 300.0))
        if how == "adaptive":
            rtt = getattr(link, "rtt", None) or 0.5
            factor = getattr(link, "traffic_timeout_factor", None) or RNS.Link.TRAFFIC_TIMEOUT_FACTOR
            timeout = rtt * factor + RNS.Resource.RESPONSE_MAX_GRACE_TIME * 1.125
            return max(0.0, timeout - rtt - 2.5 - random.random())
        return 0.0

    def send_later(self, entry, request_id, resp, delay):
        """Sends a request's response after delay seconds, as RNS would have sent it at once."""
        link = entry["link"]
        with self.lock:
            if self.slow_pending >= self.SLOW_PENDING_MAX:
                return False
            self.slow_pending += 1
        entry["maze_wait"] = entry.get("maze_wait", 0) + delay

        def send():
            try:
                if link.status != RNS.Link.ACTIVE:
                    return
                packed = msgpack.packb([request_id, resp])
                if len(packed) <= link.mdu:
                    RNS.Packet(link, packed, RNS.Packet.DATA, context=RNS.Packet.RESPONSE).send()
                else:
                    RNS.Resource(packed, link, request_id=request_id, is_response=True, auto_compress=False)
            except Exception as e:
                self.log("Slow maze: could not send a late answer: %s" % e, "warn")
            finally:
                with self.lock:
                    self.slow_pending -= 1

        timer = threading.Timer(delay, send)
        timer.daemon = True
        timer.start()
        return True

    def block_link(self, link_short, mode, reason=""):
        """Blocks one connection (a visitor that did not identify has nothing else):
        it lasts until that link closes."""
        if mode not in ("page", "silent", "tarpit"):
            raise ValueError("mode is page, silent or tarpit")
        with self.lock:
            found = [e for h, e in self.links.items() if short(h) == link_short]
        if not found:
            raise ValueError("that link is not connected any more")
        entry = found[0]
        entry["trapped"] = mode
        entry["trap_reason"] = " ".join(str(reason).replace("`", "'").split())[:80]
        entry["caught_why"] = "blocked by hand"
        self.stats.bump("blocked")
        self.log("Link %s blocked by hand (%s%s), until it disconnects" % (
            link_short, mode, (": " + entry["trap_reason"]) if entry["trap_reason"] else ""), "warn")
        if mode == "silent":
            entry["link"].teardown()

    def unblock_link(self, link_short):
        with self.lock:
            found = [e for h, e in self.links.items() if short(h) == link_short]
        if not found:
            raise ValueError("that link is not connected any more")
        for k in ("trapped", "trap_reason", "tarpit_logged", "caught_why"):
            found[0].pop(k, None)
        self.log("Link %s unblocked by hand" % link_short)

    def maze_style(self):
        mode = self.cfg.get("honeypot", "mode")
        return mode if mode in MAZE_MODES else "maze"

    def maze_rng(self, entry, data, kind):
        """Counts one maze answer and gives the random source for it: the same id, the same content."""
        now = time.time()
        if not entry.get("maze"):
            entry.setdefault("maze_first", now)
            self.log("Maze: %s gets endless fake %s" % (("LXMF " + entry["who"]) if entry.get("who") else "link " + short(entry["link"].link_id),
                                                        {"maze": "pages", "maze_files": "files"}.get(self.maze_style(), "pages and files")), "warn")
        entry["maze"] = entry.get("maze", 0) + 1
        if kind == "file":
            entry["maze_files"] = entry.get("maze_files", 0) + 1
        entry["maze_last"] = now
        entry["requests"] += 1
        seed = str((data or {}).get("var_id", "")) if isinstance(data, dict) else ""
        return random.Random("%s-%s" % (kind, seed) if seed else "%s-%s-%d" % (kind, entry.get("who") or short(entry["link"].link_id), entry["maze"]))

    def maze_page(self, entry, data):
        """A fake archive page with links to more fake pages and/or fake files, all on
        one registered path each (with a different id): a crawler never runs out of them.
        Small, and never refused: the crawler keeps walking until it disconnects."""
        rng = self.maze_rng(entry, data, "page")
        word = lambda: rng.choice(MAZE_WORDS)
        title = " ".join(word() for _ in range(rng.randint(2, 4))).capitalize()
        text = [" ".join(word() for _ in range(rng.randint(8, 14))).capitalize() + "." for _ in range(2)]
        n_pages, n_files = {"maze": (8, 0), "maze_files": (2, 6), "maze_both": (5, 4)}[self.maze_style()]
        links = ["`[%s`:%s`id=%d]" % (" ".join(word() for _ in range(2)).capitalize(), MAZE_PATH, rng.randint(10**6, 10**9))
                 for _ in range(n_pages)]
        for _ in range(n_files):
            fid = rng.randint(10**6, 10**9)
            name, size = self.maze_file_name(fid)
            links.append("`[%s`:%s`id=%d]  `F888%d KB`f" % (name, MAZE_FILE, fid, size // 1024))
        page = (">%s\n\n%s\n\n%s\n" % (title, "\n".join(text), "\n".join(links))).encode("utf-8")
        entry["maze_bytes"] = entry.get("maze_bytes", 0) + len(page)
        vid = (data or {}).get("var_id") if isinstance(data, dict) else None
        asked = (entry.get("maze_path") or MAZE_PATH).replace("/page/", "", 1)
        self.maze_got(entry, "page", asked + (" id=%s" % vid if vid else ""), len(page))
        return page

    def maze_got(self, entry, kind, label, nbytes):
        """What the maze gave this link, by name (the first PAGES_KEPT), and the counters."""
        got = entry.setdefault("maze_got", [])
        if len(got) < PAGES_KEPT:
            got.append(("file " if kind == "file" else "") + label)
        self.stats.maze(kind, nbytes)

    @staticmethod
    def maze_file_name(fid):
        rng = random.Random("name-%d" % fid)
        name = "%s-%s-%d.%s" % (rng.choice(MAZE_WORDS), rng.choice(MAZE_WORDS), rng.randint(1, 99), rng.choice(MAZE_EXTS))
        return name, rng.randint(2, 12) * 1024

    def maze_file(self, entry, data):
        """A small fake file for a maze file link: name and size as the page listed them."""
        from .honeypot import WARNING
        rng = self.maze_rng(entry, data, "file")
        try:
            fid = int((data or {}).get("var_id", ""))
        except (TypeError, ValueError, AttributeError):
            fid = rng.randint(10**6, 10**9)
        name, size = self.maze_file_name(fid)
        lines = [WARNING]
        while sum(len(x) + 1 for x in lines) < size:
            lines.append(" ".join(rng.choice(MAZE_WORDS) for _ in range(rng.randint(6, 12))))
        body = "\n".join(lines).encode("utf-8")[:size]
        entry["maze_bytes"] = entry.get("maze_bytes", 0) + len(body)
        self.maze_got(entry, "file", name, len(body))
        return [name, body]

    def drop_visitor(self, who):
        """Disconnects every link of a visitor that was just blocked."""
        with self.lock:
            links = [e["link"] for e in self.links.values() if e.get("who") == who]
        for link in links:
            try:
                link.teardown()
            except Exception:
                pass

    def gate(self, entry, path):
        """None when the visitor may have the page, otherwise why not (blocked, or must identify)."""
        who = entry.get("who") if entry else None
        blocked = self.server.blocked_entry(who, self)
        mode = blocked.get("mode") if blocked else (entry.get("trapped") if entry else None)
        if blocked and blocked.get("scope") == "honeypots" and self.is_honeypot:
            # honeypot blocks follow the mode this honeypot uses now
            mode = self.cfg.get("honeypot", "mode") or mode
        if (not mode and who and self.is_honeypot and not self.cfg.getbool("honeypot", "to_blocklist")
                and self.server.is_caught(who)):
            # caught before and kept out of the blocklist: the honeypots keep treating it as caught
            mode = self.cfg.get("honeypot", "mode") or "tarpit"
            if entry:
                entry["trapped"], entry["caught_why"] = mode, "caught before"
                entry["trap_reason"] = self.cfg.get("honeypot", "reason") or "Crawler"
        if mode in MAZE_MODES and self.is_honeypot:
            # not refused: it gets fake pages or files, counted by maze_rng
            return "maze"
        if mode:
            self.stats.bump("blocked")
            if entry:
                # what a blocked peer still tries is the interesting part: count it
                entry["tries"] = entry.get("tries", 0) + 1
                entry.setdefault("tried", [])
                if path not in entry["tried"] and len(entry["tried"]) < 12:
                    entry["tried"].append(path)
            return "maze" if mode in MAZE_MODES else {"silent": "silent", "tarpit": "tarpit"}.get(mode, "blocked")
        if entry and self.is_trap(path):
            # the trap page is answered as the honeypot mode says, so it is not a blocked request
            trap = entry.setdefault("trap", [])
            if path not in trap and len(trap) < 12:
                trap.append(path)
            return self.catch(entry, "opened the trap page %s" % path)
        if self.cfg.getbool("node", "require_identify") and not who:
            self.stats.bump("unidentified")
            self.log("Asked to identify for %s, link %s" % (path, short(entry["link"].link_id) if entry else "-"), "warn")
            return "identify"
        return None

    # ---- honeypot peer history (kept on disk: the live list scrolls away) ----

    PEERS_KEPT = 500

    def peers(self):
        if not hasattr(self, "_peers"):
            try:
                with open(os.path.join(self.data_path, "peers.json"), encoding="utf-8") as f:
                    self._peers = json.load(f)
            except (OSError, ValueError):
                self._peers = []
        return self._peers

    def clear_peers(self):
        """Empties the peer connections history; blocks and live links stay."""
        with self.lock:
            self._peers = []
            self.by_who.clear()
        try:
            os.remove(os.path.join(self.data_path, "peers.json"))
        except OSError:
            pass
        self.log("Peer connections history cleared")

    def record_peer(self, entry, until):
        if entry.get("maze"):
            self.log("Maze: %s left after %s in %s" % (
                ("LXMF " + entry["who"]) if entry.get("who") else "link " + short(entry["link"].link_id),
                log.plural(entry["maze"], "fake page"), log.span(entry["maze_last"] - entry["maze_first"])), "warn")
        rec = {"since": entry["since"], "until": until, "link": short(entry["link"].link_id),
               "who": entry.get("who"), "identity": entry.get("identity", ""), "name": entry.get("name", ""),
               "requests": entry["requests"], "bytes": entry["bytes"],
               "pages": list(dict.fromkeys(p for _, p in entry["recent"]))[:PAGES_KEPT],
               "tries": entry.get("tries", 0), "tried": entry.get("tried", [])[:12], "trap": entry.get("trap", []), "files": entry.get("files", []),
               "caught": entry.get("caught_why", ""), "mode": entry.get("trapped", ""), **maze_of(entry), **via_of(entry)}
        with self.lock:
            peers = self.peers()
            peers.append(rec)
            del peers[:-self.PEERS_KEPT]
            text = json.dumps(peers)
        path = os.path.join(self.data_path, "peers.json")
        try:
            os.makedirs(self.data_path, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    def live_peers(self):
        now = time.time()
        with self.lock:
            return [{"since": e["since"], "until": None, "link": short(h), "who": e.get("who"),
                     "identity": e.get("identity", ""), "name": e.get("name", ""), "requests": e["requests"],
                     "bytes": e["bytes"], "pages": list(dict.fromkeys(p for _, p in e["recent"]))[:PAGES_KEPT],
                     "tries": e.get("tries", 0), "tried": e.get("tried", [])[:12], "trap": e.get("trap", []), "files": e.get("files", []),
                     "caught": e.get("caught_why", ""), "mode": e.get("trapped", ""), "online": round(now - e["since"]),
                     **maze_of(e), **via_of(e)}
                    for h, e in self.links.items()]

    def caught_count(self):
        """Peers caught here, counted like the peer list: one per LXMF address, one per link otherwise."""
        with self.lock:
            recs = self.peers() + [{"who": e.get("who"), "link": short(h), "caught": e.get("caught_why")}
                                   for h, e in self.links.items()]
        return len({r.get("who") or r.get("link") for r in recs if r.get("caught") not in (None, "", "blocked by hand")})

    @property
    def is_honeypot(self):
        return self.cfg.get("node", "type") == "honeypot"

    def is_trap(self, path):
        # traps and autoblock belong to honeypot nodes only: normal nodes stay normal
        if not self.is_honeypot:
            return False
        if path in (MAZE_PATH, MAZE_FILE):
            return True
        for t in self.cfg.get("honeypot", "traps").split(","):
            t = t.strip()
            if t and (path == t or (t.endswith("*") and path.startswith(t[:-1]))):
                return True
        return False

    def catch(self, entry, why):
        """A trap or the crawler detector caught this link: block the LXMF address
        if it identified, otherwise only this link (it has nothing else to block)."""
        mode = self.cfg.get("honeypot", "mode") or "tarpit"
        mode = mode if mode in ("page", "silent", "tarpit") + MAZE_MODES else "tarpit"
        reason = self.cfg.get("honeypot", "reason") or "Crawler"
        self.stats.bump("trapped")
        entry["trapped"], entry["trap_reason"], entry["caught_why"] = mode, reason, why
        if entry.get("who"):
            if self.cfg.getbool("honeypot", "to_blocklist"):
                # blocked on the honeypots; "Block on all nodes" widens it
                self.server.block(entry["who"], entry.get("name", ""), self.id, reason, mode, scope="honeypots",
                                  identity=entry.get("identity", ""), via="auto: " + why)
            else:
                self.log("Caught LXMF %s (%s): %s on the honeypots" % (entry["who"], why, mode), "warn")
            self.server.crawler_seen(entry["who"], self, entry.get("name", ""), entry.get("identity", ""), caught=True)
        else:
            self.log("Caught link %s (%s): %s for the rest of the link" % (short(entry["link"].link_id), why, mode), "warn")
        return "maze" if mode in MAZE_MODES else {"silent": "silent", "tarpit": "tarpit"}.get(mode, "blocked")

    def link_closed(self, link):
        with self.lock:
            entry = self.links.pop(link.link_id, None)
            self.rate.pop(link.link_id, None)
            online = len(self.links)
        self.stats.link_traffic(link.rxbytes, link.txbytes)
        if entry and self.is_honeypot:
            self.record_peer(entry, time.time())
            if entry.get("who") and entry["who"] in self.server.crawlers():
                self.server.crawler_took(entry["who"], self, entry["requests"],
                                         entry["bytes"] + entry.get("maze_bytes", 0), entry.get("maze", 0))
        if entry:
            who = (" (LXMF %s)" % entry["who"]) if entry["who"] else ""
            self.log("Disconnected link %s after %s: %s, %s, %s sent%s, %d online" % (
                short(link.link_id), log.span(time.time() - entry["since"]),
                log.plural(entry["requests"], "request"), log.plural(entry["refreshes"], "refresh", "refreshes"),
                log.size(entry["bytes"]), who, online), "connect")

    @staticmethod
    def count_kind(entry, path, data=None):
        # A form sent again is a new view, not a refresh
        if entry and not data:
            last = entry["last"].get(path)
            if last and time.time() - last < REFRESH_SECONDS:
                return "refresh"
        return "page"

    def track(self, entry, kind, path, nbytes):
        now = time.time()
        if entry is None:
            self.log("%s %s, %s" % (kind.capitalize(), path, log.size(nbytes)), kind)
            return
        entry["last"][path] = now
        entry["bytes"] += nbytes
        if kind == "refresh":
            entry["refreshes"] += 1
            return
        entry["requests"] += 1
        if kind in ("file", "media"):
            got = entry.setdefault("files", [])
            if path not in got and len(got) < 12:
                got.append(path)
        if kind == "page":
            rules = [(max(2, self.cfg.getint("honeypot", k1)), max(5, self.cfg.getint("honeypot", k2)))
                     for k1, k2 in (("crawler_pages", "crawler_seconds"), ("long_pages", "long_seconds"))]
            entry["recent"].append((now, path))
            while entry["recent"] and entry["recent"][0][0] < now - max(w for _, w in rules):
                entry["recent"].popleft()
            hit = None if entry["crawler"] else next(((n, w) for n, w in rules
                                                     if len({p for t, p in entry["recent"] if t >= now - w}) >= n), None)
            if hit:
                entry["crawler"] = True
                self.stats.bump("crawler_links")
                why = "%d pages in %s" % (hit[0], ("%d s" % hit[1]) if hit[1] < 600 else ("%d min" % (hit[1] // 60)))
                self.log("Link %s looks like a crawler: %s" % (short(entry["link"].link_id), why), "warn")
                if self.is_honeypot and self.cfg.getbool("honeypot", "autoblock"):
                    self.catch(entry, why)
            elif entry.get("who") and not entry["crawler"]:
                self.check_hopping(entry, now)
        who = (" (LXMF %s%s)" % (entry["who"], (' "%s"' % entry["name"]) if entry["name"] else "")) if entry["who"] else ""
        what = {"page": "Page", "file": "File", "media": "Image"}[kind]
        self.log("%s %s, %s, to link %s%s" % (what, path, log.size(nbytes), short(entry["link"].link_id), who), kind)

    # ---- announce and jobs ----

    def announce(self):
        with self.lock:
            if not self.destination:
                return False
            name = self.name + (" N1" if self.extra else "")
            self.destination.announce(app_data=name.encode("utf-8"))
            self.last_announce = time.time()
            self.announce_due = None
            extra = list(self.extra)
        self.log("Announced as \"%s\"" % name, "announce")
        for n, d in enumerate(extra, 2):
            # not all at once on the mesh
            threading.Timer(2 * (n - 1), self.announce_extra, (d, n)).start()
        return True

    def announce_extra(self, dest, n):
        if dest not in self.extra:
            return
        name = "%s N%d" % (self.name, n)
        dest.announce(app_data=name.encode("utf-8"))
        self.log("Announced address N%d as \"%s\"" % (n, name), "announce")

    def job(self, now):
        if self.state != "running":
            return
        if self.announce_due and now >= self.announce_due:
            self.announce()
        interval = self.cfg.getint("node", "announce_interval")
        if interval > 0 and self.last_announce and now - self.last_announce >= interval * 60:
            self.announce()
        rescan = self.cfg.getint("node", "rescan_interval")
        if rescan > 0 and now - self.last_rescan >= rescan * 60:
            try:
                self.rescan()
            except Exception as e:
                self.log("Rescan failed: %s" % e, "error")
        self.stats.flush()

    def info(self):
        with self.lock:
            online = [{"link": short(h), "since": e["since"], "who": e["who"], "name": e["name"],
                       "requests": e["requests"], "refreshes": e["refreshes"], "bytes": e["bytes"],
                       "rx": e["link"].rxbytes, "tx": e["link"].txbytes,
                       "crawler": e["crawler"], "blocked": e.get("trapped", ""), **via_of(e)} for h, e in self.links.items()]
        return {"id": self.id, "name": self.name, "address": self.address, "state": self.state,
                "addresses": self.extra_addresses() if self.is_honeypot else [],
                "error": self.error, "started": self.started, "last_announce": self.last_announce,
                "announce_interval": self.cfg.getint("node", "announce_interval"),
                "pages": self.pages_count, "files": self.files_count,
                "plugin": bool(self.plugin), "online": online,
                "type": "honeypot" if self.is_honeypot else "normal",
                "caught": self.caught_count() if self.is_honeypot else 0, "stats": self.stats.summary(),
                "spark": self.stats.spark(),
                "settings": self.cfg.as_dict()}
