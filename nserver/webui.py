"""Web UI and JSON API (standard library only).

Login only when a password is set. The console commands (--status, --log)
use a token in data/.token, readable only by the user running the server.
POST requests need the X-Requested-With header, which a form or a link on
another site cannot add.
"""
import http.cookies
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import RNS

from . import backup, config, files, log, shortcuts, VERSION
from .node import peer_name

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
NO_EDIT = "Editing pages over the network needs a web UI password (Settings > Web UI password)"


def web_version():
    stamp = max((os.path.getmtime(os.path.join(WEB, f)) for f in os.listdir(WEB)), default=0)
    return "%s-%d" % (VERSION, int(stamp))


def lan_ip():
    """This computer's address on the local network (no packet is sent)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return "" if ip.startswith("127.") else ip
    except OSError:
        return ""


def desktop():
    """A screen where a folder window can open."""
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_folder(path):
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
SESSION_DAYS = 7
CSRF_HEADER = "nomad-server"


class WebUI:
    def __init__(self, server, console_only=False):
        self.server = server
        server.ui = self
        # console_only: no pages, 127.0.0.1, token only (--status, --log, --stop keep working)
        self.forced_console = console_only
        self.console_only = console_only
        self.bound = None
        self.sessions = {}
        self.fails = []
        self.httpd = None
        self.token_file = os.path.join(server.base, "data", ".token")
        self.token = secrets.token_hex(24)

    def write_token(self):
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file, "w") as f:
            f.write(self.token)
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            pass

    def wanted(self):
        cfg = self.server.cfg
        console = self.forced_console or not cfg.getbool("webui", "enabled")
        host = "127.0.0.1" if console else (cfg.get("webui", "host") or "127.0.0.1")
        return host, cfg.getint("webui", "port"), console

    def start(self):
        cfg = self.server.cfg
        host, port, self.console_only = self.wanted()
        handler = type("Handler", (Handler,), {"ui": self})
        try:
            self.httpd = ThreadingHTTPServer((host, port), handler)
        except OSError as e:
            log.line("Web UI not started on %s:%d: %s (port in use? change port in [webui] of %s)" % (
                host, port, e, os.path.join(self.server.base, "server.cfg")), kind="error")
            return False
        self.httpd.daemon_threads = True
        self.bound = (host, port, self.console_only)
        self.write_token()
        # where the console commands find this process, whatever server.cfg says now
        with open(os.path.join(os.path.dirname(self.token_file), ".address"), "w") as f:
            f.write("%s:%d" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, port))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if self.console_only:
            log.line("Web UI off; console commands (--status, --log, --stop) on 127.0.0.1:%d" % port)
            return True
        nopw = "" if cfg.get("webui", "password") else " (no password)"
        if host in ("0.0.0.0", "", "::"):
            lan = lan_ip()
            log.line("Web UI on all interfaces, port %d%s: http://127.0.0.1:%d here%s" % (
                port, nopw, port, (", http://%s:%d from the network" % (lan, port)) if lan else ""))
        else:
            log.line("Web UI on http://%s:%d%s" % (host, port, nopw))
        return True

    def rebind(self):
        """After a restart: listen again only if address, port or mode changed."""
        if self.httpd and self.wanted() == self.bound:
            return
        self.stop()
        self.start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def url(self):
        cfg = self.server.cfg
        host = cfg.get("webui", "host")
        return "http://%s:%d/" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, cfg.getint("webui", "port"))

    # ---- auth ----

    def password_set(self):
        return bool(self.server.cfg.get("webui", "password"))

    def new_session(self):
        token = secrets.token_hex(24)
        self.sessions[token] = time.time() + SESSION_DAYS * 86400
        return token

    def valid(self, token):
        exp = self.sessions.get(token or "")
        if exp and exp > time.time():
            return True
        self.sessions.pop(token or "", None)
        return False

    def login(self, password):
        now = time.time()
        self.fails = [t for t in self.fails if t > now - 300]
        if len(self.fails) >= 10:
            return None
        if config.check_password(self.server.cfg.get("webui", "password"), password or ""):
            return self.new_session()
        self.fails.append(now)
        time.sleep(1)
        return None


class Handler(BaseHTTPRequestHandler):
    ui = None
    server_version = "nomad-server"

    def log_message(self, fmt, *args):
        pass

    # ---- helpers ----

    def send(self, code, body, ctype="application/json", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def cookie(self):
        try:
            c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return c["ns_session"].value if "ns_session" in c else None
        except http.cookies.CookieError:
            return None

    def has_token(self):
        t = self.headers.get("X-Token")
        return bool(t) and secrets.compare_digest(t, self.ui.token)

    def authorized(self):
        if self.has_token():
            return True
        if self.ui.console_only:
            return False
        if not self.ui.password_set():
            return True
        return self.ui.valid(self.cookie())

    def local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def edit_ok(self):
        # Pages can be scripts the server runs: over the network only with a password
        return self.ui.password_set() or self.local()

    def node_arg(self, q):
        node = self.ui.server.nodes.get(q.get("node", ""))
        if not node:
            raise KeyError("no such node")
        return node

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    # ---- GET ----

    def do_GET(self):
        if self.ui.console_only and not self.has_token():
            return self.send(404, b"not found", "text/plain")
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        if p == "/" or p.startswith("/static/"):
            return self.static("index.html" if p == "/" else p[len("/static/"):])
        if p == "/api/session":
            return self.send(200, {"password": self.ui.password_set(), "ok": self.authorized(),
                                   "splash": self.ui.server.cfg.getbool("webui", "splash")})
        if not self.authorized():
            return self.send(401, {"error": "login"})
        srv = self.ui.server
        if p == "/api/overview":
            return self.send(200, srv.overview())
        if p == "/api/stats":
            node = srv.nodes.get(q.get("node", ""))
            if not node:
                return self.send(404, {"error": "no such node"})
            snap = node.stats.snapshot()
            for v in snap.get("visitors", {}).values():
                if not v.get("name") and v.get("identity"):
                    v["name"] = peer_name(v["identity"])
            return self.send(200, {"node": node.info(), "stats": snap})
        if p == "/api/log":
            node = q.get("node") or None
            return self.send(200, {"lines": log.recent(int(q.get("after", 0) or 0), node, int(q.get("limit", 500) or 500))})
        if p == "/api/settings":
            d = srv.cfg.as_dict()
            d["webui"]["password"] = ""
            return self.send(200, {"server": d, "has_password": self.ui.password_set(), "base": srv.base,
                                   "nodes_dir": srv.nodes_dir, "can_open": self.local() and desktop()})
        try:
            if p == "/api/shortcuts":
                st = shortcuts.status(srv.base) if self.local() else {"supported": False, "note": "only from the computer running the server"}
                return self.send(200, st)
            if p == "/api/honeypot/peers":
                # live links first, then the history, newest first, of every honeypot node
                out = []
                for n in srv.nodes.values():
                    if n.is_honeypot:
                        for rec in n.live_peers() + list(reversed(n.peers())):
                            row = dict(rec, node=n.id, node_name=n.name, node_address=n.address)
                            if "trap" not in row and not row.get("tries") and row.get("tried"):
                                # older records kept the trap page with the blocked requests
                                row["trap"], row["tried"] = row["tried"], []
                            if not row.get("name") and row.get("identity"):
                                row["name"] = peer_name(row["identity"])
                            out.append(row)
                out.sort(key=lambda r: (r.get("until") is not None, -(r.get("until") or r["since"])))
                return self.send(200, {"items": out[:int(q.get("limit", 300) or 300)]})
            if p == "/api/blocklist.txt":
                return self.send(200, srv.blocklist_text().encode("utf-8"), "text/plain; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="nomad-server-blocklist-%s.txt"' % time.strftime("%Y%m%d")})
            if p == "/api/blocklist":
                items = srv.blocklist()
                for e in items.values():
                    if not e.get("name") and e.get("identity"):
                        e["name"] = peer_name(e["identity"])
                return self.send(200, {"items": items, "reasons": srv.block_reasons(), "blackholed": sorted(srv.blackholed())})
            if p == "/api/honeypot/crawlers":
                srv.backfill_crawler_traffic()
                items = json.loads(json.dumps(srv.crawlers()))
                for c in items.values():
                    if not c.get("name") and c.get("identity"):
                        c["name"] = peer_name(c["identity"])
                return self.send(200, {"items": items})
            if p == "/api/trash":
                return self.send(200, {"items": srv.trash_list(), "edit": self.edit_ok()})
            if p == "/api/files":
                node = self.node_arg(q)
                return self.send(200, {"items": files.tree(node), "edit": self.edit_ok(),
                                       "can_open": self.local() and desktop()})
            if p == "/api/file":
                return self.send(200, files.read(self.node_arg(q), q.get("root", ""), q.get("path", "")))
            parts = p.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "node"] and parts[3] in ("backup", "identity"):
                # The identity is the node's private key: same rule as editing
                if not self.edit_ok():
                    raise PermissionError(NO_EDIT.replace("Editing pages", "Downloading identities and backups"))
                node = self.node_arg({"node": parts[2]})
                stamp = time.strftime("%Y%m%d-%H%M")
                if parts[3] == "backup":
                    data, name, ctype = backup.make_zip(node), "%s-backup-%s.zip" % (node.id, stamp), "application/zip"
                    node.log("Backup downloaded from the web UI")
                else:
                    data, name, ctype = backup.read_identity(node), "%s.identity" % node.id, "application/octet-stream"
                    node.log("Identity exported from the web UI", "warn")
                return self.send(200, data, ctype, {"Content-Disposition": 'attachment; filename="%s"' % name})
        except PermissionError as e:
            return self.send(403, {"error": str(e)})
        except (KeyError, ValueError) as e:
            return self.send(400, {"error": str(e).strip("'")})
        self.send(404, {"error": "not found"})

    def static(self, name):
        path = os.path.normpath(os.path.join(WEB, name))
        if not path.startswith(WEB + os.sep) or not os.path.isfile(path):
            return self.send(404, b"not found", "text/plain")
        with open(path, "rb") as f:
            data = f.read()
        if name == "index.html":
            # new files get a new address: no browser keeps an old icon or script
            data = data.replace(b"{{v}}", web_version().encode())
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send(200, data, ctype, {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"})

    # ---- POST ----

    def do_POST(self):
        if self.ui.console_only and not self.has_token():
            return self.send(404, b"not found", "text/plain")
        if self.headers.get("X-Requested-With") != CSRF_HEADER:
            return self.send(403, {"error": "missing header"})
        url = urlparse(self.path)
        p = url.path
        parts = p.strip("/").split("/")
        if p in ("/api/files/upload", "/api/nodes/restore") or (len(parts) == 4 and parts[:2] == ["api", "node"] and parts[3] == "identity"):
            return self.raw(p, parts, {k: v[0] for k, v in parse_qs(url.query).items()})
        data = self.body()
        if p == "/api/login":
            token = self.ui.login(data.get("password", ""))
            if not token:
                return self.send(403, {"error": "wrong password"})
            return self.send(200, {"ok": True}, headers={
                "Set-Cookie": "ns_session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (token, SESSION_DAYS * 86400)})
        if p == "/api/logout":
            self.ui.sessions.pop(self.cookie() or "", None)
            return self.send(200, {"ok": True}, headers={"Set-Cookie": "ns_session=; Path=/; Max-Age=0"})
        if not self.authorized():
            return self.send(401, {"error": "login"})
        try:
            out = self.action(p, data) or {"ok": True}
            headers = {}
            if "_session" in out:
                headers["Set-Cookie"] = "ns_session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (out.pop("_session"), SESSION_DAYS * 86400)
            return self.send(200, out, headers=headers)
        except PermissionError as e:
            return self.send(403, {"error": str(e)})
        except (KeyError, ValueError) as e:
            return self.send(400, {"error": str(e).strip("'")})
        except Exception as e:
            log.line("Web UI error on %s: %s" % (p, e), kind="error")
            return self.send(500, {"error": str(e)})

    def raw(self, p, parts, q):
        """POSTs whose body is a file: upload, restore of a node backup, identity import."""
        if not self.authorized():
            return self.send(401, {"error": "login"})
        try:
            if not self.edit_ok():
                raise PermissionError(NO_EDIT)
            n = int(self.headers.get("Content-Length") or 0)
            limit = backup.MAX_ZIP if p == "/api/nodes/restore" else files.MAX_UPLOAD
            if n > limit:
                raise ValueError("too big (%d MB max)" % (limit // 1048576))
            body = self.rfile.read(n)
            if p == "/api/files/upload":
                node = self.node_arg(q)
                files.upload(node, q.get("root", ""), q.get("path", ""), body)
                node.log("Uploaded %s/%s from the web UI" % (q.get("root"), q.get("path")))
                node.rescan()
                return self.send(200, {"ok": True})
            if p == "/api/nodes/restore":
                node = backup.restore(self.ui.server, body)
                return self.send(200, {"ok": True, "id": node.id, "address": node.address})
            node = self.node_arg({"node": parts[2]})
            return self.send(200, {"ok": True, "address": backup.set_identity(self.ui.server, node, body), "info": node.info()})
        except PermissionError as e:
            return self.send(403, {"error": str(e)})
        except (KeyError, ValueError) as e:
            return self.send(400, {"error": str(e).strip("'")})
        except Exception as e:
            log.line("Web UI error on %s: %s" % (p, e), kind="error")
            return self.send(500, {"error": str(e)})

    def file_action(self, what, data):
        if not self.edit_ok():
            raise PermissionError(NO_EDIT)
        node = self.node_arg(data)
        root, path = data.get("root", ""), data.get("path", "")
        out = {"ok": True}
        if what == "save":
            out["mtime"] = files.write_text(node, root, path, data.get("text", ""), data.get("mtime"))
            node.log("Saved %s/%s from the web UI" % (root, path))
        elif what == "mkdir":
            files.mkdir(node, root, path)
        elif what == "rename":
            files.rename(node, root, path, data.get("new_root") or root, data.get("new_path", ""))
            node.log("Renamed %s/%s to %s/%s from the web UI" % (root, path, data.get("new_root") or root, data.get("new_path")))
        elif what == "delete":
            files.delete(node, root, path)
            node.log("Moved %s/%s to the node's .trash from the web UI" % (root, path), "warn")
        else:
            raise KeyError("unknown action")
        node.rescan()
        return out

    def action(self, p, data):
        srv = self.ui.server
        parts = p.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "files"]:
            return self.file_action(parts[2], data)
        if p == "/api/blocklist/add":
            srv.block(data.get("who", ""), data.get("name", ""), data.get("node", ""),
                      data.get("reason", ""), data.get("mode", "page"), scope=data.get("scope") or "all")
            return {"ok": True}
        if p == "/api/honeypot/crawlers/hide":
            return {"ok": True, "hidden": srv.hide_crawlers()}
        if p == "/api/honeypot/forget":
            srv.forget_crawler(data.get("who", ""))
            return {"ok": True}
        if p == "/api/blackhole":
            identity = srv.blackhole(data.get("who", ""), data.get("identity", ""), bool(data.get("on", True)))
            return {"ok": True, "identity": identity}
        if p == "/api/honeypot/clear":
            for n in srv.nodes.values():
                if n.is_honeypot:
                    n.clear_peers()
            return {"ok": True}
        if p == "/api/blocklist/scope":
            srv.set_scope(data.get("who", ""), data.get("scope", "all"))
            return {"ok": True}
        if p == "/api/blocklist/remove":
            srv.unblock(data.get("who", ""))
            return {"ok": True}
        if p == "/api/shortcuts":
            if not self.local():
                raise PermissionError("Shortcuts can be made only from the computer running the server")
            from .server import SCRIPT
            return shortcuts.set_on(srv.base, SCRIPT, data.get("kind", ""), bool(data.get("on")))
        if p in ("/api/trash/restore", "/api/trash/delete"):
            # removed nodes keep their identity: same rule as identities
            if not self.edit_ok():
                raise PermissionError(NO_EDIT.replace("Editing pages", "Restoring or deleting nodes"))
            if p.endswith("restore"):
                node = srv.trash_restore(data.get("entry", ""))
                return {"ok": True, "id": node.id, "address": node.address}
            srv.trash_delete(data.get("entry", ""))
            return {"ok": True}
        if p == "/api/open_folder":
            if not (self.local() and desktop()):
                raise PermissionError("Folders open only on the computer running the server")
            target = data.get("target")
            if target == "node":
                path = self.node_arg(data).folder
            elif target == "nodes":
                path = srv.nodes_dir
            else:
                path = srv.base
            open_folder(path)
            return {"ok": True}
        if p == "/api/nodes/honeypot":
            node = srv.new_honeypot(data.get("name", "") or "Archive")
            return {"ok": True, "id": node.id}
        if p == "/api/nodes/new":
            node = srv.new_node(data.get("name", ""))
            return {"ok": True, "id": node.id}
        if p == "/api/shutdown":
            srv.request_stop()
            return {"ok": True}
        if p == "/api/restart":
            srv.request_restart()
            return {"ok": True}
        if p == "/api/settings":
            allowed = {"server": ("rns_config", "nodes_dir", "log_to_file"), "webui": ("host", "port", "splash"), "blocklist": ("reasons",)}
            values = {s: {k: v for k, v in (data.get(s) or {}).items() if k in keys} for s, keys in allowed.items()}
            if "port" in values["webui"]:
                port = int(values["webui"]["port"])
                if not 1 <= port <= 65535:
                    raise ValueError("port out of range")
            srv.cfg.set(values)
            log.line("Server settings changed: restart to apply")
            return {"ok": True, "restart": True}
        if p == "/api/password":
            pw = data.get("password", "")
            if pw and len(pw) < 6:
                raise ValueError("at least 6 characters")
            srv.cfg.set({"webui": {"password": config.hash_password(pw) if pw else ""}})
            self.ui.sessions.clear()
            log.line("Web UI password %s" % ("changed" if pw else "removed"))
            return {"ok": True, "_session": self.ui.new_session()} if pw else {"ok": True}
        if len(parts) == 4 and parts[:2] == ["api", "node"]:
            node = srv.nodes.get(parts[2])
            if not node:
                raise KeyError("no such node")
            what = parts[3]
            if what == "announce":
                if not node.announce():
                    raise ValueError("the node is not running")
            elif what == "restart":
                node.restart()
            elif what == "start":
                # remembered: a node started here starts again with the server
                node.cfg.set({"node": {"enabled": "yes"}})
                node.start()
            elif what == "stop":
                node.cfg.set({"node": {"enabled": "no"}})
                node.stop()
            elif what == "rescan":
                node.rescan()
            elif what == "reset_stats":
                node.stats.reset()
                node.log("Counters reset from the web UI", "warn")
            elif what == "delete":
                srv.delete_node(node.id)
            elif what == "unblock_link":
                node.unblock_link(str(data.get("link", "")))
            elif what == "block_link":
                node.block_link(str(data.get("link", "")), data.get("mode", "page"), data.get("reason", ""))
            elif what == "new_identity":
                if not self.edit_ok():
                    raise PermissionError(NO_EDIT.replace("Editing pages", "Changing identities"))
                address = backup.set_identity(srv, node, RNS.Identity().get_private_key())
                return {"ok": True, "address": address, "info": node.info()}
            elif what == "settings":
                keys = {s: set(v) for s, v in config.NODE_DEFAULTS.items()}
                values = {s: {k: v for k, v in (data.get(s) or {}).items() if k in keys.get(s, ())} for s in keys}
                node.cfg.set({s: v for s, v in values.items() if v})
                node.log("Settings changed from the web UI")
                return {"ok": True, "info": node.info()}
            else:
                raise KeyError("unknown action")
            return {"ok": True, "info": node.info() if node.id in srv.nodes else None}
        raise KeyError("not found")
