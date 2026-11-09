#!/usr/bin/env python3
"""nomad-server - many NomadNet nodes in one process, with a web UI.

  python nomad_server.py                 run the server (web UI on 127.0.0.1:8484)
  python nomad_server.py --app           run it and open the web UI in an app window
  python nomad_server.py --status        state of the running server
  python nomad_server.py --log [-f]      last log lines (-f to follow)
  python nomad_server.py --new-node NAME create a node folder
  python nomad_server.py --set-password  password for the web UI (empty = none)
  python nomad_server.py --no-webui      no web interface (console commands still work)
  python nomad_server.py --service       quiet, for systemd or a hidden start (full log in logs/server.log)
  python nomad_server.py --stop          stop the running server
  python nomad_server.py --block LXMF [--reason TEXT] [--mode page|silent|tarpit]
  python nomad_server.py --unblock LXMF | --blocklist | --export-blocklist FILE
"""
import argparse
import getpass
import json
import re
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


def base_dir():
    # --base DIR, else next to the exe when compiled, else next to this file
    for i, a in enumerate(sys.argv):
        if a == "--base" and i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
        if a.startswith("--base="):
            return os.path.abspath(a[len("--base="):])
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) in ("site-packages", "dist-packages"):
        # installed with pip or pipx: the data must not live inside the package
        return os.path.join(os.path.expanduser("~"), ".nomad-server")
    return here


if len(sys.argv) >= 2 and sys.argv[1] == "--pick-folder":
    # used by the compiled exe for Import existing node: the system's folder window
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    picked = filedialog.askdirectory(parent=root, initialdir=(sys.argv[2] if len(sys.argv) > 2 else None) or None,
                                     mustexist=True, title="nomad-server: choose the folder of the old node")
    sys.stdout.buffer.write((picked or "").encode("utf-8"))
    sys.exit(0)

BASE = base_dir()
# restarts and desktop icons run this file, also when started from a pip entry point
os.environ["NOMAD_SERVER_SCRIPT"] = os.path.abspath(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nserver import config, VERSION  # noqa: E402

if len(sys.argv) >= 3 and sys.argv[1] == "--window":
    # the app window, a process of its own (nserver/appwindow.py)
    from nserver import appwindow
    sys.exit(appwindow.run(sys.argv[2], BASE, "--owner" in sys.argv))


def api_text(path):
    return api(path, raw=True).decode("utf-8", "replace")


def api(path, data=None, raw=False):
    cfg = config.Config(os.path.join(BASE, "server.cfg"), config.SERVER_DEFAULTS)
    host = cfg.get("webui", "host")
    host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:
        with open(os.path.join(BASE, "data", ".token")) as f:
            token = f.read().strip()
    except OSError:
        sys.exit("The server is not running here (no data/.token).")
    where = "%s:%d" % (host, cfg.getint("webui", "port"))
    try:
        with open(os.path.join(BASE, "data", ".address")) as f:
            where = f.read().strip() or where
    except OSError:
        pass
    req = urllib.request.Request("http://%s%s" % (where, path),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"X-Token": token, "X-Requested-With": "nomad-server",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            return body if raw else json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            sys.exit("Refused: %s" % json.loads(e.read()).get("error", e))
        except ValueError:
            sys.exit("Refused: %s" % e)
    except OSError as e:
        sys.exit("No answer from the server: %s" % e)


def ago(t):
    from nserver.log import span
    return (span(time.time() - t) + " ago") if t else "never"


def show_status(as_json):
    o = api("/api/overview")
    if as_json:
        print(json.dumps(o, indent=1))
        return
    from nserver.log import size, span
    rns = o["rns"]
    print("nomad-server %s, up %s, Reticulum: %s" % (o["version"], span(o["now"] - o["started"]),
                                                     "shared instance" if rns["shared"] else "own instance"))
    if rns.get("error"):
        print("  " + rns["error"])
    for i in rns["interfaces"]:
        # "TCPInterface[hub/host:4242]" -> "TCP hub/host:4242", as in the web UI
        m = re.match(r"^(\w+?)(?:Interface)?\[(.*)\]$", i["name"])
        name = ("%s %s" % m.groups()) if m else i["name"]
        print("  %-4s %-40s rx %s  tx %s" % ("up" if i["up"] else "DOWN", name[:40], size(i["rx"]), size(i["tx"])))
    for n in o["nodes"]:
        s = n["stats"]
        print("\n%s  [%s]  id %s  %s" % (n["name"], n["state"], n["id"], n["address"]))
        if n["error"]:
            print("  error: " + n["error"])
        print("  %d online, views %d (today %d), refreshes %d, files %d, images %d, sent %s" % (
            len(n["online"]), s["views"], s["today_views"], s["refreshes"], s["files"], s["media"], size(s["bytes"])))
        print("  announced %s, %d pages, %d files%s" % (ago(n["last_announce"]), n["pages"], n["files"],
                                                         ", plugin" if n["plugin"] else ""))
        for v in n["online"]:
            print("    link %s %s%s%s" % (v["link"], ("LXMF " + v["who"]) if v["who"] else "not identified",
                                          (' "%s"' % v["name"]) if v["name"] else "", " (crawler?)" if v["crawler"] else ""))


def show_memory(as_json):
    from nserver.log import size, span
    m = api("/api/debug/memory")
    if as_json:
        print(json.dumps(m, indent=1))
        return
    print("pid %d, RAM %s, up %s, python %s, %d threads, %d objects" % (
        m["pid"], size(m["rss"]), span(m["uptime"]), m["python"], m["threads"], m["gc_objects"]))
    if m.get("status"):
        print("  " + ", ".join("%s %s" % kv for kv in m["status"].items()))
    for title, d in (("threads", m["thread_names"]), ("objects by type", m["types"]),
                     ("Reticulum tables", m["rns"]), ("nomad-server tables", m["ours"])):
        print("\n" + title)
        for k, v in sorted(d.items(), key=lambda kv: -kv[1] if isinstance(kv[1], int) else 0):
            print("  %10s  %s" % (v, k))
    if m.get("traced"):
        print("\ntraced %s (peak %s), biggest by line" % (size(m["traced"]["current"]), size(m["traced"]["peak"])))
        for s in m["by_line"]:
            print("  %10s %8d  %s" % (size(s["size"]), s["count"], s["where"]))
        if "growth" in m:
            print("\ngrowth in the last %s (since the first --memory)" % span(m["growth_since"]))
            for s in m["growth"]:
                print("  %10s %+8d  %s" % ("+" + size(s["size_diff"]), s["count_diff"], s["where"]))
        else:
            print("\nbaseline taken: run --memory again later to see what grew")
    else:
        print("\n(start the server with NS_TRACEMALLOC=1 in the environment to see allocations by line)")


def node_action(action, wanted):
    nodes = api("/api/overview")["nodes"]
    match = [n for n in nodes if n["id"] == wanted] or [n for n in nodes if n["name"].lower() == wanted.lower()]
    if len(match) != 1:
        sys.exit(("More than one node is called %r, use the id: " % wanted if match else "No node %r. Nodes: " % wanted)
                 + ", ".join("%s (%s)" % (n["id"], n["name"]) for n in (match or nodes)))
    n = match[0]
    r = api("/api/node/%s/%s" % (n["id"], action), {})
    if not r.get("ok"):
        sys.exit(r.get("error", r))
    done = {"start": "started", "stop": "stopped", "restart": "restarted", "announce": "announced"}[action]
    state = (r.get("info") or {}).get("state", "")
    print("%s %s%s." % (n["name"], done, (" (now %s)" % state) if state and action != "announce" else ""))


def show_log(follow, node, lines):
    after = 0
    out = api("/api/log?limit=%d%s" % (lines, ("&node=" + node) if node else ""))["lines"]
    while True:
        for r in out:
            print("%s  %-14s %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"])),
                                    "[%s]" % (r["node"] or "server"), r["text"]), flush=True)
            after = r["seq"]
        if not follow:
            return
        time.sleep(1)
        out = api("/api/log?after=%d%s" % (after, ("&node=" + node) if node else ""))["lines"]


def running_at():
    """host:port of a server already running on this folder, or None."""
    try:
        with open(os.path.join(BASE, "data", ".address")) as f:
            where = f.read().strip()
        with open(os.path.join(BASE, "data", ".token")) as f:
            token = f.read().strip()
        req = urllib.request.Request("http://%s/api/overview" % where, headers={"X-Token": token})
        with urllib.request.urlopen(req, timeout=3) as r:
            return where if r.status == 200 else None
    except (OSError, ValueError):
        return None


def open_app(url, profile=None):
    """A pywebview window if installed, else Edge or Chrome in --app mode, else the
    normal browser. With its own profile (started by this server) the returned
    process ends when the window is closed, and closing it stops the server."""
    from nserver import appwindow
    if appwindow.available():
        compiled = "__compiled__" in globals() or getattr(sys, "frozen", False)
        cmd = ([sys.executable] if compiled else [sys.executable, os.path.abspath(__file__)])
        cmd += ["--window", url, "--base", BASE] + (["--owner"] if profile else [])
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        return subprocess.Popen(cmd, **kw)
    candidates = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
    if sys.platform.startswith("win"):
        pf = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData")]
        candidates = [os.path.join(p, sub) for p in pf if p for sub in (
            r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")] + candidates
    for c in candidates:
        exe = c if os.path.isfile(c) else shutil.which(c)
        if exe:
            cmd = [exe, "--app=" + url, "--window-size=1200,820"]
            if profile:
                # a small profile: no component downloads, almost no cache
                cmd += ["--user-data-dir=" + profile, "--no-first-run", "--no-default-browser-check",
                        "--disable-component-update", "--disable-background-networking", "--disable-sync",
                        "--disk-cache-size=1048576", "--disable-features=OptimizationHints,MediaRouter,Translate"]
            return subprocess.Popen(cmd)
    import webbrowser
    webbrowser.open(url)
    return None


def message(text):
    """For the app, which has no terminal: a message box on Windows, else stderr."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "nomad-server", 0x10)
            return
        except Exception:
            pass
    print(text, file=sys.stderr)


def wait_for(pid):
    """After a restart from the web UI: wait until the old process has gone."""
    for _ in range(60):
        try:
            if sys.platform.startswith("win"):
                out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, text=True).stdout
                if str(pid) not in out:
                    return
            else:
                os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)


def run(args):
    from nserver.server import Server
    from nserver.webui import WebUI
    from nserver import log
    if args.after:
        wait_for(args.after)
    running = running_at()
    if running:
        # Never two servers on the same folder (same identities): the icon only opens the window
        if args.app:
            open_app("http://%s/" % running)
            return
        sys.exit("nomad-server already runs on this folder (http://%s/). Stop it first with --stop." % running)
    from nserver import memdiag
    memdiag.start_tracing()
    srv = Server(BASE)
    stop = {"now": False}

    def on_signal(*_):
        stop["now"] = True
    srv.start(args.verbose, args.service)
    # after Reticulum starts: it sets its own handlers, which exit at once and lose
    # the counters not yet saved (docker stop, systemctl stop)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    ui = WebUI(srv, console_only=args.no_webui or not srv.cfg.getbool("webui", "enabled"))
    if not ui.start():
        # without its port nothing can reach this server, not even --status or --stop
        log.line("Stopping: free the port or change it, then start again", kind="error")
        stop["now"] = stop["failed"] = True
        if args.app:
            message("The web UI port %s is in use: close the program using it, or change the port in %s"
                    % (srv.cfg.get("webui", "port"), os.path.join(BASE, "server.cfg")))
    elif args.app and not ui.console_only:
        window = open_app(ui.url(), os.path.join(BASE, "data", "app-window"))
        if window:
            # started as an app: closing the window stops the server
            def watch():
                window.wait()
                if not stop["now"]:
                    log.line("App window closed: stopping")
                    stop["now"] = True
            threading.Thread(target=watch, daemon=True).start()
    try:
        while not stop["now"] and not srv.restart_requested and not srv.stop_requested:
            time.sleep(0.5)
    finally:
        srv.stop()
        ui.stop()
        for f in (ui.token_file, os.path.join(os.path.dirname(ui.token_file), ".address")):
            try:
                os.remove(f)
            except OSError:
                pass
        # an own Reticulum instance keeps paths and known peers on disk: os._exit below
        # skips atexit (a client of rnsd has nothing to save)
        try:
            if srv.reticulum and not srv.reticulum.is_connected_to_shared_instance:
                import RNS
                RNS.Reticulum.exit_handler()
        except Exception:
            pass
    if srv.restart_requested:
        log.line("Restarting")
        if os.environ.get("INVOCATION_ID"):
            os._exit(75)
        srv.respawn()
    os._exit(1 if stop.get("failed") else 0)


def main():
    ap = argparse.ArgumentParser(description="nomad-server %s: NomadNet nodes with a web UI" % VERSION)
    ap.add_argument("--app", action="store_true", help="open the web UI in an app window")
    ap.add_argument("--verbose", action="store_true", help="Reticulum debug log")
    ap.add_argument("--no-webui", action="store_true", help="no web interface (console commands still work)")
    ap.add_argument("--service", action="store_true",
                    help="quiet: only warnings and errors on the terminal/journal, full log in logs/server.log")
    ap.add_argument("--status", action="store_true", help="state of the running server")
    ap.add_argument("--json", action="store_true", help="with --status: raw JSON")
    ap.add_argument("--log", nargs="?", const=60, type=int, metavar="N", help="last N log lines")
    ap.add_argument("-f", "--follow", action="store_true", help="with --log: keep following")
    ap.add_argument("--node", help="with --log: only this node")
    ap.add_argument("--new-node", metavar="NAME", help="create a node folder")
    ap.add_argument("--stop", action="store_true", help="stop the running server (also a hidden one)")
    ap.add_argument("--start-node", metavar="NODE", help="start one node and keep it enabled; NODE = its folder name in nodes/ (shown as id in --status) or its name")
    ap.add_argument("--stop-node", metavar="NODE", help="stop one node (id or name) and keep it disabled")
    ap.add_argument("--restart-node", metavar="NODE", help="restart one node (id or name)")
    ap.add_argument("--memory", action="store_true", help="what the running server holds in memory (with --json: raw)")
    ap.add_argument("--announce", metavar="NODE", help="announce one node (id or name) now")
    ap.add_argument("--block", metavar="LXMF", help="block a peer on every node of the running server")
    ap.add_argument("--reason", help="with --block: the reason shown to it")
    ap.add_argument("--mode", default="page", choices=("page", "silent", "tarpit", "maze"), help="with --block: how (default page)")
    ap.add_argument("--unblock", metavar="LXMF", help="remove a peer from the blocklist")
    ap.add_argument("--blocklist", action="store_true", help="show the blocklist")
    ap.add_argument("--export-blocklist", metavar="FILE", help="save the blocklist as a text file")
    ap.add_argument("--set-password", action="store_true", help="password for the web UI")
    ap.add_argument("--base", metavar="DIR", help="folder with server.cfg and nodes/ (default: next to the program, ~/.nomad-server if installed with pip)")
    ap.add_argument("--after", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args()
    if args.status:
        return show_status(args.json)
    if args.log is not None:
        return show_log(args.follow, args.node, args.log)
    if args.stop:
        r = api("/api/shutdown", {})
        print("Stopping." if r.get("ok") else r.get("error", r))
        return
    if args.memory:
        return show_memory(args.json)
    for action, value in (("start", args.start_node), ("stop", args.stop_node),
                          ("restart", args.restart_node), ("announce", args.announce)):
        if value:
            return node_action(action, value)
    if args.block:
        r = api("/api/blocklist/add", {"who": args.block, "reason": args.reason or "", "mode": args.mode, "node": "console"})
        print("Blocked %s (%s)." % (args.block, args.mode) if r.get("ok") else r.get("error", r))
        return
    if args.unblock:
        r = api("/api/blocklist/remove", {"who": args.unblock})
        print("Unblocked %s." % args.unblock if r.get("ok") else r.get("error", r))
        return
    if args.blocklist or args.export_blocklist:
        text = api_text("/api/blocklist.txt")
        if args.export_blocklist:
            with open(args.export_blocklist, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            print("Saved %s (%d blocked)." % (args.export_blocklist, sum(1 for l in text.splitlines() if l and not l.startswith("#"))))
        else:
            print(text, end="")
        return
    if args.set_password:
        pw = getpass.getpass("New web UI password (empty = no password): ")
        if pw and pw != getpass.getpass("Again: "):
            sys.exit("They do not match.")
        if pw and len(pw) < 6:
            sys.exit("At least 6 characters.")
        if os.path.exists(os.path.join(BASE, "data", ".token")):
            # running: the server takes it at once (and writes server.cfg itself), no restart
            try:
                api("/api/password", {"password": pw})
                print("Password %s, applied now." % ("set" if pw else "removed"))
                return
            except SystemExit as e:
                if not str(e).startswith("No answer"):
                    raise
                # stale token from a server that is not running: write the file as below
        cfg = config.Config(os.path.join(BASE, "server.cfg"), config.SERVER_DEFAULTS, config.SERVER_TEMPLATE)
        cfg.set({"webui": {"password": config.hash_password(pw) if pw else ""}})
        print("Password %s. It applies when the server starts." % ("set" if pw else "removed"))
        return
    if args.new_node:
        from nserver.server import Server
        node = Server(BASE).new_node(args.new_node)
        print("Created %s, address %s" % (node.folder, node.address_from_identity()))
        print("If the server is running, restart it to start the new node.")
        return
    run(args)


if __name__ == "__main__":
    main()
