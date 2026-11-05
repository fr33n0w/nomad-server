"""The server: one Reticulum instance, many nodes (one sub-folder each)."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import RNS

from . import config, log, proc, VERSION
from .node import Node, SAMPLE_INDEX

JOB_SECONDS = 5
# Taken at start, before anything can change the working folder
SCRIPT = os.environ.get("NOMAD_SERVER_SCRIPT") or os.path.abspath(sys.argv[0])
RPC_REJECTED = ("rnsd runs as another user and refused our requests: pages are served, but Reticulum status "
                "and visitor names are not available. Fix: same rpc_key in [reticulum] (see README)")


def systemd_unit():
    """Name of our systemd service, from the cgroup (install.sh may name it otherwise)."""
    try:
        with open("/proc/self/cgroup") as f:
            m = re.search(r"/([^/]+)\.service\s*$", f.read(), re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "nomad-server"


class Server:
    def __init__(self, base):
        self.base = base
        self.cfg = config.Config(os.path.join(base, "server.cfg"), config.SERVER_DEFAULTS, config.SERVER_TEMPLATE)
        self.nodes = {}
        self.reticulum = None
        self.started = time.time()
        self.running = False
        self.restart_requested = False
        self.stop_requested = False
        self.ui = None
        self.service = False
        self.rns_dir = None
        self.lock = threading.Lock()

    @property
    def nodes_dir(self):
        d = self.cfg.get("server", "nodes_dir") or "nodes"
        return d if os.path.isabs(d) else os.path.join(self.base, d)

    def start(self, verbose=False, service=False):
        if service or self.cfg.getbool("server", "log_to_file"):
            log.to_file(os.path.join(self.base, "logs", "server.log"))
        if service:
            log.quiet()
        log.line("nomad-server %s starting" % VERSION)
        self.service = service
        rns_dir = self.rns_dir = self.cfg.get("server", "rns_config").strip() or None
        self.reticulum = RNS.Reticulum(configdir=os.path.expanduser(rns_dir) if rns_dir else None,
                                       loglevel=RNS.LOG_DEBUG if verbose else RNS.LOG_WARNING)
        if self.reticulum.is_connected_to_shared_instance:
            log.line("Connected to the shared Reticulum instance")
        else:
            log.line("Running its own Reticulum instance (no rnsd found)")
        self.running = True
        self.load_nodes()
        threading.Thread(target=self.jobs, daemon=True).start()

    def load_nodes(self):
        os.makedirs(self.nodes_dir, exist_ok=True)
        for name in sorted(os.listdir(self.nodes_dir)):
            folder = os.path.join(self.nodes_dir, name)
            if name.startswith(".") or not os.path.isfile(os.path.join(folder, "node.cfg")):
                continue
            node = Node(self, folder)
            self.nodes[node.id] = node
            if node.cfg.getbool("node", "enabled"):
                node.start()
            else:
                log.line("Node %s is disabled" % node.id)
        if not self.nodes:
            log.line("No nodes yet: add one from the web UI or with --new-node NAME", kind="warn")

    def jobs(self):
        while self.running:
            now = time.time()
            for node in list(self.nodes.values()):
                try:
                    node.job(now)
                except Exception as e:
                    log.line("Job error: %s" % e, node=node.id, kind="error")
            time.sleep(JOB_SECONDS)

    def stop(self):
        self.running = False
        for node in list(self.nodes.values()):
            try:
                node.stop()
            except Exception as e:
                log.line("Stop error: %s" % e, node=node.id, kind="error")
            node.stats.flush(force=True)
        log.line("Stopped")

    # ---- nodes ----

    def free_folder(self, name):
        """A new folder under nodes/ named after the node, never an existing one."""
        slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "node"
        folder, n = os.path.join(self.nodes_dir, slug), 2
        while os.path.exists(folder):
            folder = os.path.join(self.nodes_dir, "%s-%d" % (slug, n))
            n += 1
        return folder

    def add_node(self, folder):
        node = Node(self, folder)
        self.nodes[node.id] = node
        if self.running and node.cfg.getbool("node", "enabled"):
            node.start()
        return node

    def new_node(self, name):
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("the name is empty")
        folder = self.free_folder(name)
        os.makedirs(os.path.join(folder, "pages"))
        os.makedirs(os.path.join(folder, "files"))
        with open(os.path.join(folder, "node.cfg"), "w", encoding="utf-8", newline="\n") as f:
            f.write(config.NODE_TEMPLATE.format(name=name))
        with open(os.path.join(folder, "pages", "index.mu"), "w", encoding="utf-8", newline="\n") as f:
            f.write(SAMPLE_INDEX.format(name=name))
        RNS.Identity().to_file(os.path.join(folder, "identity"))
        node = Node(self, folder)
        # A new node starts stopped: only the sample page yet, nothing to announce
        node.cfg.set({"node": {"enabled": "no"}})
        self.nodes[node.id] = node
        log.line("Node %s created in %s: add its pages, then press Start" % (node.id, folder))
        return node

    def new_honeypot(self, name):
        """A stopped node full of bait pages and hidden trap links, traps and autoblock on."""
        from . import honeypot
        node = self.new_node(name)
        for f in os.listdir(node.pages_path):
            p = os.path.join(node.pages_path, f)
            if os.path.isfile(p):
                os.remove(p)
        traps = honeypot.build(node.pages_path, node.name)
        honeypot.build_files(node.files_path, node.pages_path)
        node.cfg.set({"node": {"require_identify": "no", "type": "honeypot"},
                      "honeypot": {"traps": traps, "mode": "tarpit", "reason": "Crawler (honeypot)",
                                   "autoblock": "yes", "crawler_pages": "30", "crawler_seconds": "600",
                                   "hop_links": "6", "hop_seconds": "7200",
                                   "long_pages": "60", "long_seconds": "3600"},
                      "footer": {"visits": "no", "watermark": "no"}})
        log.line("Honeypot node %s created: start it to catch crawlers" % node.id)
        return node

    def delete_node(self, node_id):
        """Moves the folder to nodes/.trash (identity included), never deletes it."""
        node = self.nodes.pop(node_id)
        node.stop()
        trash = os.path.join(self.nodes_dir, ".trash")
        os.makedirs(trash, exist_ok=True)
        dest = os.path.join(trash, "%s-%s" % (node_id, time.strftime("%Y%m%d-%H%M%S")))
        shutil.move(node.folder, dest)
        log.line("Node %s moved to %s" % (node_id, dest))

    # ---- blocked visitors (all nodes) ----

    @property
    def block_file(self):
        return os.path.join(self.base, "data", "blocklist.json")

    def blocklist(self):
        if not hasattr(self, "_blocked"):
            try:
                with open(self.block_file, encoding="utf-8") as f:
                    self._blocked = json.load(f)
            except (OSError, ValueError):
                self._blocked = {}
            # older entries: honeypot catches hold on the honeypots, the rest everywhere
            for e in self._blocked.values():
                e.setdefault("scope", "honeypots" if str(e.get("via", "")).startswith("auto") else "all")
        return self._blocked


    def _save_blocklist(self):
        os.makedirs(os.path.dirname(self.block_file), exist_ok=True)
        tmp = self.block_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._blocked, f, indent=1, sort_keys=True)
        os.replace(tmp, self.block_file)

    def block_reasons(self):
        return [r.strip() for r in self.cfg.get("blocklist", "reasons").split("|") if r.strip()]

    @staticmethod
    def applies(entry, node):
        """Where a block holds: scope all, honeypots (the honeypot nodes) or one node id."""
        scope = entry.get("scope") or "all"
        if scope == "all" or node is None:
            return True
        if scope == "honeypots":
            return node.is_honeypot
        return scope == node.id

    def blocked_entry(self, who, node=None):
        """The block on this peer that holds on this node, or None."""
        e = self.blocklist().get(who) if who else None
        return e if e and self.applies(e, node) else None

    def set_scope(self, who, scope):
        e = self.blocklist().get(str(who))
        if not e:
            raise ValueError("not in the blocklist")
        if scope not in ("all", "honeypots") and scope not in self.nodes:
            raise ValueError("scope is all, honeypots or a node id")
        e["scope"] = scope
        self._save_blocklist()
        log.line("Block on LXMF %s now holds on %s" % (who, "every node" if scope == "all" else scope), kind="warn")
        if e.get("mode") == "silent":
            for n in list(self.nodes.values()):
                if self.applies(e, n):
                    n.drop_visitor(who)

    def block(self, who, name="", node="", reason="", mode="page", identity="", via="manual", scope="all"):
        """mode page: the visitor gets a page with the reason; silent: dropped, no answer;
        tarpit: kept connected, its requests never get an answer (each one waits for its
        own timeout: time lost for the crawler, not a byte sent over the mesh)."""
        who = str(who).strip().lower()
        if len(who) != 32 or any(c not in "0123456789abcdef" for c in who):
            raise ValueError("not an LXMF address (32 hex characters)")
        if mode not in ("page", "silent", "tarpit", "maze"):
            raise ValueError("mode is page, silent, tarpit or maze")
        if scope not in ("all", "honeypots") and scope not in self.nodes:
            raise ValueError("scope is all, honeypots or a node id")
        # it goes into a micron page: no backticks, they are markup
        reason = " ".join(str(reason).replace("`", "'").split())[:80]
        identity = str(identity or "") or self.identity_of(who)
        self.blocklist()[who] = {"name": str(name)[:60], "node": str(node)[:60], "at": time.time(),
                                 "reason": reason, "mode": mode, "identity": identity, "via": str(via)[:80],
                                 "scope": scope}
        self._save_blocklist()
        log.line("Blocked visitor LXMF %s%s (%s, %s%s)" % (who, (' "%s"' % name) if name else "", mode,
                                                           "every node" if scope == "all" else scope,
                                                           (": " + reason) if reason else ""), kind="warn")
        if mode == "silent":
            # disconnect it wherever the block holds
            for n in list(self.nodes.values()):
                if self.applies(self._blocked[who], n):
                    n.drop_visitor(who)

    def identity_of(self, who):
        """The identity hash behind an LXMF address, if a node has seen it identify."""
        for n in list(self.nodes.values()):
            for e in list(n.links.values()):
                if e.get("who") == who and e.get("identity"):
                    return e["identity"]
            v = n.stats.d.get("visitors", {}).get(who) or {}
            if v.get("identity"):
                return v["identity"]
        return ""

    def blocklist_text(self):
        """One peer per line, for sharing or for Reticulum's blackhole (identity hashes)."""
        out = ["# nomad-server blocklist, %s" % time.strftime("%Y-%m-%d %H:%M"),
               "# identity hash (for the blackhole)   LXMF address   # reason, name"]
        for who, b in sorted(self.blocklist().items(), key=lambda kv: kv[1].get("at", 0)):
            out.append("%s  %s  # %s%s" % (b.get("identity") or "-" * 32, who, b.get("reason") or "no reason",
                                         (", " + b["name"]) if b.get("name") else ""))
        return "\n".join(out) + "\n"

    def unblock(self, who):
        if self.blocklist().pop(str(who), None) is None:
            raise ValueError("not in the blocklist")
        self._save_blocklist()
        for n in list(self.nodes.values()):
            n.flagged.discard(who)
            n.by_who.pop(who, None)
        log.line("Unblocked visitor LXMF %s" % who)

    # ---- Reticulum blackhole: by hand only, it acts on the whole Reticulum instance ----

    def blackholed(self):
        """Identity hashes (hex) the Reticulum instance blackholes now."""
        try:
            got = self.reticulum.get_blackholed_identities() or {}
        except Exception:
            return {}
        return {RNS.hexrep(k, delimit=False): v for k, v in got.items() if isinstance(k, bytes)}

    def blackhole(self, who="", identity="", on=True):
        """Blackholes (or lifts) the identity behind a blocked LXMF address in Reticulum:
        its announces are dropped and paths to its destinations removed."""
        e = self.blocklist().get(str(who)) or {}
        identity = str(identity or e.get("identity") or self.identity_of(str(who))).lower()
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("identity hash not known: the peer has to identify once")
        h = bytes.fromhex(identity)
        if on:
            reason = "nomad-server: %s" % (e.get("reason") or "by hand")
            ok = self.reticulum.blackhole_identity(h, reason=reason[:80])
        else:
            ok = self.reticulum.unblackhole_identity(h)
        if ok is False:
            raise RuntimeError("Reticulum refused it, see the rnsd log")
        log.line("%s identity %s%s in Reticulum" % ("Blackholed" if on else "Lifted the blackhole of", identity,
                                                     (" (LXMF %s)" % who) if who else ""), kind="warn")
        return identity

    # ---- crawlers seen by the honeypots, kept to see if they come back ----

    @property
    def crawler_file(self):
        return os.path.join(self.base, "data", "crawlers.json")

    def crawlers(self):
        if not hasattr(self, "_crawlers"):
            try:
                with open(self.crawler_file, encoding="utf-8") as f:
                    self._crawlers = json.load(f)
            except (OSError, ValueError):
                self._crawlers = {}
                # first run: start from what the honeypots caught before
                for who, b in self.blocklist().items():
                    if str(b.get("via", "")).startswith("auto"):
                        at = b.get("at") or time.time()
                        self._crawlers[who] = {"first": at, "last": at, "name": b.get("name", ""),
                                               "identity": b.get("identity", ""), "visits": 1, "caught": 1,
                                               "honeypots": {b.get("node") or "?": {"name": b.get("node") or "?",
                                                                                     "first": at, "last": at, "visits": 1}}}
        return self._crawlers

    def is_caught(self, who):
        c = self.crawlers().get(who) if who else None
        return bool(c and c.get("caught"))

    def forget_crawler(self, who):
        with self.lock:
            if self.crawlers().pop(str(who), None) is None:
                raise ValueError("not a known crawler")
            text = json.dumps(self._crawlers, indent=1, sort_keys=True)
        self._save_crawlers(text)
        log.line("Forgot crawler LXMF %s" % who)

    def hide_crawlers(self):
        """Clear list: the known crawlers leave the list in the web UI only. Nothing is deleted,
        they stay caught, and one that comes back shows again with all its data."""
        with self.lock:
            n = 0
            for c in self.crawlers().values():
                if not c.get("hidden"):
                    c["hidden"] = True
                    n += 1
            text = json.dumps(self._crawlers, indent=1, sort_keys=True)
        self._save_crawlers(text)
        log.line("Known crawlers list cleared in the web UI (%d hidden, kept in the file)" % n)
        return n

    def crawler_seen(self, who, node, name="", identity="", caught=False):
        """A crawler identified on a honeypot (caught now, or caught before and back)."""
        now = time.time()
        with self.lock:
            c = self.crawlers().setdefault(who, {"first": now, "visits": 0, "caught": 0, "honeypots": {}})
            c["last"] = now
            c.pop("hidden", None)          # back again: shown again, with everything it did before
            c["name"] = name or c.get("name", "")
            c["identity"] = identity or c.get("identity", "")
            # honeypots by address: a new honeypot is a new address
            hp = c["honeypots"].setdefault(node.address, {"first": now, "visits": 0})
            hp.update(name=node.name, last=now)
            if caught:
                c["caught"] += 1
            if not caught or not hp["visits"]:
                # caught on its first visit here: that visit counts too
                c["visits"] += 1
                hp["visits"] += 1
            text = json.dumps(self._crawlers, indent=1, sort_keys=True)
        self._save_crawlers(text)

    def crawler_took(self, who, node, requests, nbytes, maze):
        """What a known crawler took from a honeypot on one link: requests, bytes sent
        (bait pages and maze together), fake maze answers. Added when the link closes."""
        with self.lock:
            c = self.crawlers().get(who)
            if not c:
                return
            hp = c["honeypots"].setdefault(node.address, {"first": time.time(), "visits": 0, "name": node.name, "last": time.time()})
            for k, v in (("requests", requests), ("sent", nbytes), ("maze", maze)):
                hp[k] = hp.get(k, 0) + v
            text = json.dumps(self._crawlers, indent=1, sort_keys=True)
        self._save_crawlers(text)

    def backfill_crawler_traffic(self):
        """Once: crawlers known before the traffic was counted get it from the honeypots' saved history."""
        if getattr(self, "_backfilled", False):
            return
        self._backfilled = True
        history = {n.address: n.peers() for n in self.nodes.values() if n.is_honeypot and n.address}
        # removed honeypots keep their history in the trash: their address comes from their identity
        try:
            removed = os.listdir(self.trash_dir)
        except OSError:
            removed = []
        for entry in removed:
            folder = os.path.join(self.trash_dir, entry)
            try:
                ident = RNS.Identity.from_file(os.path.join(folder, "identity"))
                addr = RNS.hexrep(RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", ident), delimit=False)
                with open(os.path.join(folder, "data", "peers.json"), encoding="utf-8") as f:
                    history.setdefault(addr, json.load(f))
            except Exception:
                continue
        changed = False
        with self.lock:
            for who, c in self.crawlers().items():
                for addr, hp in c.get("honeypots", {}).items():
                    if "sent" in hp or addr not in history:
                        continue
                    recs = [r for r in history[addr] if r.get("who") == who]
                    hp["requests"] = sum(r.get("requests", 0) for r in recs)
                    hp["sent"] = sum(r.get("bytes", 0) + r.get("maze_bytes", 0) for r in recs)
                    hp["maze"] = sum(r.get("maze", 0) for r in recs)
                    changed = True
            text = json.dumps(self._crawlers, indent=1, sort_keys=True) if changed else None
        if text:
            self._save_crawlers(text)

    def _save_crawlers(self, text):
        try:
            os.makedirs(os.path.dirname(self.crawler_file), exist_ok=True)
            with open(self.crawler_file + ".tmp", "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(self.crawler_file + ".tmp", self.crawler_file)
        except OSError:
            pass

    # ---- removed nodes (nodes/.trash) ----

    @property
    def trash_dir(self):
        return os.path.join(self.nodes_dir, ".trash")

    def trash_entry(self, entry):
        entry = str(entry)
        if not entry or entry != os.path.basename(entry) or entry.startswith(".") or ":" in entry:
            raise ValueError("unknown entry")
        path = os.path.join(self.trash_dir, entry)
        if not os.path.isdir(path):
            raise ValueError("unknown entry")
        return path

    def trash_list(self):
        out = []
        if not os.path.isdir(self.trash_dir):
            return out
        for entry in sorted(os.listdir(self.trash_dir), reverse=True):
            path = os.path.join(self.trash_dir, entry)
            if entry.startswith(".") or not os.path.isdir(path):
                continue
            cfg = config.Config(os.path.join(path, "node.cfg"), config.NODE_DEFAULTS)
            address = ""
            try:
                ident = RNS.Identity.from_file(os.path.join(path, "identity"))
                address = RNS.hexrep(RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", ident), delimit=False)
            except Exception:
                pass
            m = re.match(r"(.+)-(\d{8}-\d{6})$", entry)
            size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)
            out.append({"entry": entry, "id": m.group(1) if m else entry, "name": cfg.get("node", "name"),
                        "address": address, "removed": time.mktime(time.strptime(m.group(2), "%Y%m%d-%H%M%S")) if m else None,
                        "size": size, "type": cfg.get("node", "type")})
        return out

    def trash_restore(self, entry):
        from .backup import check_unused
        path = self.trash_entry(entry)
        ident = RNS.Identity.from_file(os.path.join(path, "identity"))
        if ident is None:
            raise ValueError("this node has no valid identity")
        check_unused(self, ident)
        m = re.match(r"(.+)-\d{8}-\d{6}$", entry)
        folder = os.path.join(self.nodes_dir, m.group(1) if m else entry)
        if os.path.exists(folder):
            folder = self.free_folder(os.path.basename(folder))
        shutil.move(path, folder)
        node = self.add_node(folder)
        log.line("Node %s restored from the trash" % node.id)
        return node

    def trash_delete(self, entry):
        path = self.trash_entry(entry)
        shutil.rmtree(path)
        log.line("Removed node %s deleted for good" % entry, kind="warn")

    # ---- status ----

    def rns_info(self):
        r = self.reticulum
        info = {"shared": bool(r and r.is_connected_to_shared_instance), "interfaces": [], "error": ""}
        if not r:
            return info
        try:
            stats = r.get_interface_stats() or {}
            for i in stats.get("interfaces", []):
                name = i.get("name", "")
                if name.startswith("LocalInterface") or name.startswith("Shared Instance"):
                    continue
                info["interfaces"].append({"name": name, "type": i.get("type", ""), "up": bool(i.get("status")),
                                           "rx": i.get("rxb", 0), "tx": i.get("txb", 0),
                                           "clients": i.get("clients")})
            info["rx"], info["tx"] = stats.get("rxb", 0), stats.get("txb", 0)
            info["transport"] = stats.get("transport_id") is not None
        except Exception as e:
            info["error"] = str(e)
            if "digest" in str(e):
                # the shared instance belongs to another user: its RPC key is not ours
                info["error"] = RPC_REJECTED
                if not getattr(self, "rpc_warned", False):
                    self.rpc_warned = True
                    log.line(RPC_REJECTED, kind="warn")
        return info

    def overview(self):
        return {"version": VERSION, "started": self.started, "now": time.time(), "pid": os.getpid(),
                "proc": proc.usage(),
                "platform": sys.platform, "rns": self.rns_info(),
                "nodes": [n.info() for n in self.nodes.values()]}

    # ---- restart ----

    def request_restart(self):
        """In the same process (no new window, same terminal): nodes, web UI and
        settings start again. Only a new Reticulum config folder needs a new
        process (systemd restarts it, otherwise it spawns itself)."""
        self.cfg.reload()
        if (self.cfg.get("server", "rns_config").strip() or None) != self.rns_dir:
            threading.Timer(0.5, lambda: setattr(self, "restart_requested", True)).start()
            return
        threading.Timer(0.5, self.soft_restart).start()

    def soft_restart(self):
        log.line("Restarting nodes and web UI")
        for node in list(self.nodes.values()):
            try:
                node.stop()
            except Exception as e:
                log.line("Stop error: %s" % e, node=node.id, kind="error")
            node.stats.flush(force=True)
        self.nodes = {}
        self.cfg.reload()
        if self.service or self.cfg.getbool("server", "log_to_file"):
            log.to_file(os.path.join(self.base, "logs", "server.log"))
        else:
            log.to_file(None)
        self.load_nodes()
        if self.ui:
            self.ui.rebind()
        self.started = time.time()
        log.line("Restarted")

    def request_stop(self):
        if os.environ.get("INVOCATION_ID"):
            raise ValueError("running as a systemd service: stop it with  sudo systemctl stop %s" % systemd_unit())
        log.line("Stop asked from the web UI or the console")
        threading.Timer(0.5, lambda: setattr(self, "stop_requested", True)).start()

    def respawn(self):
        if os.environ.get("INVOCATION_ID"):
            return
        # Same options, but the script by absolute path and --base absolute:
        # the new process starts in the data folder, not where we were launched
        args, skip = [], False
        for a in sys.argv[1:]:
            if skip:
                skip = False
            elif a == "--base":
                skip = True
            elif not (a.startswith("--after=") or a.startswith("--base=") or a == "--app"):
                args.append(a)
        args += ["--base", self.base, "--after=%d" % os.getpid()]
        if "__compiled__" in globals() or getattr(sys, "frozen", False):
            cmd = [sys.executable] + args
        else:
            cmd = [sys.executable, SCRIPT] + args
        if not sys.platform.startswith("win"):
            kw = {"start_new_session": True}
        elif "--service" in sys.argv or os.path.basename(sys.executable).lower().startswith("pythonw"):
            # running hidden: the new process stays hidden too
            kw = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        else:
            kw = {"creationflags": subprocess.CREATE_NEW_CONSOLE}
        subprocess.Popen(cmd, cwd=self.base, **kw)
