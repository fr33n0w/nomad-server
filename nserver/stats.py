"""Persistent counters of one node, in data/stats.json.

Every request counts, reloads and identified visitors included. Kinds:
  page     a page view
  refresh  the same link asking the same page again within REFRESH_SECONDS
           (partials that reload themselves, like an animated header)
  file     a download from files/
  media    an inline image

The file is written every FLUSH_SECONDS when something changed and when the
node stops: a crash loses at most that much. Writes go to a temporary file,
fsync, then replace, so stats.json is never left half written.
"""
import collections
import json
import os
import threading
import time

REFRESH_SECONDS = 10
FLUSH_SECONDS = 30
DAYS_KEPT = 400
HOURS_KEPT = 72
VISITORS_KEPT = 500
KINDS = ("page", "refresh", "file", "media")


def today():
    return time.strftime("%Y-%m-%d")


class Stats:
    def __init__(self, data_dir):
        self.file = os.path.join(data_dir, "stats.json")
        self.lock = threading.Lock()
        self.dirty = False
        self.last_flush = time.time()
        self.minutes = collections.deque(maxlen=60)
        self.minute_rows = collections.OrderedDict()   # minute -> the same counters as a day, last hour, memory only
        self.d = self.empty()
        self.load(data_dir)

    @staticmethod
    def empty():
        return {"since": time.time(), "paths": {k: {} for k in KINDS}, "bytes": {k: {} for k in KINDS},
                "days": {}, "hours": {},
                "totals": {"connects": 0, "bytes": 0, "link_rx": 0, "link_tx": 0,
                           "denied": 0, "limited": 0, "crawler_links": 0, "blocked": 0, "unidentified": 0, "trapped": 0, "maze": 0,
                           "maze_files": 0, "maze_bytes": 0},
                "visitors": {}}

    def load(self, data_dir):
        try:
            with open(self.file, encoding="utf-8") as f:
                d = json.load(f)
            base = self.empty()
            base.update(d)
            for k in KINDS:
                base["paths"].setdefault(k, {})
                base["bytes"].setdefault(k, {})
            for k, v in self.empty()["totals"].items():
                base["totals"].setdefault(k, v)
            self.d = base
            # visits.json copied in after the node was made: added once to what it counted since
            if not base.get("imported"):
                self.import_old(os.path.join(data_dir, "visits.json"))
            return
        except FileNotFoundError:
            pass
        except Exception as e:
            # A broken file is kept aside, not overwritten
            try:
                os.replace(self.file, self.file + ".broken-%d" % int(time.time()))
            except OSError:
                pass
            self.d["load_error"] = str(e)
        self.import_old(os.path.join(data_dir, "visits.json"))

    def import_old(self, old):
        """visits.json of rns_server_page.py ({path: count}) is added to the page counts, once."""
        try:
            with open(old, encoding="utf-8") as f:
                counts = {k: int(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            return
        pages = self.d["paths"]["page"]
        for k, v in counts.items():
            pages[k] = pages.get(k, 0) + v
        self.d["imported"] = {"from": "visits.json", "views": sum(counts.values()), "at": time.time()}
        self.dirty = True

    def day(self):
        return self.bucket("days", today(), DAYS_KEPT)

    def bucket(self, table, key, kept):
        rows = self.d.setdefault(table, {})
        if key not in rows:
            rows[key] = {"page": 0, "refresh": 0, "file": 0, "media": 0, "connects": 0,
                         "bytes": 0, "link_rx": 0, "link_tx": 0}
            if len(rows) > kept:
                for old in sorted(rows)[:len(rows) - kept]:
                    del rows[old]
        return rows[key]

    def minute_row(self):
        minute = int(time.time() // 60)
        row = self.minute_rows.get(minute)
        if row is None:
            row = self.minute_rows[minute] = {}
            while len(self.minute_rows) > 60:
                self.minute_rows.popitem(last=False)
        return row

    def add(self, **values):
        """Adds to today (server time), to this hour (UTC "YYYY-MM-DD HH": the browser shows its own time)
        and to this minute (the last hour, in memory)."""
        for row in (self.day(), self.bucket("hours", time.strftime("%Y-%m-%d %H", time.gmtime()), HOURS_KEPT), self.minute_row()):
            for k, v in values.items():
                row[k] = row.get(k, 0) + v

    def tick(self, nbytes):
        """Requests and bytes per minute, last hour, in memory only."""
        minute = int(time.time() // 60)
        if not self.minutes or self.minutes[-1][0] != minute:
            self.minutes.append([minute, 0, 0])
        self.minutes[-1][1] += 1
        self.minutes[-1][2] += nbytes

    def spark(self):
        """60 values (oldest first): [requests, bytes] per minute."""
        with self.lock:
            now = int(time.time() // 60)
            got = {m: (n, b) for m, n, b in self.minutes}
        return [list(got.get(m, (0, 0))) for m in range(now - 59, now + 1)]

    def request(self, kind, path, nbytes, who=None, name="", identity=""):
        with self.lock:
            paths = self.d["paths"][kind]
            paths[path] = paths.get(path, 0) + 1
            sizes = self.d["bytes"][kind]
            sizes[path] = sizes.get(path, 0) + nbytes
            self.add(**{kind: 1, "bytes": nbytes})
            self.d["totals"]["bytes"] += nbytes
            self.tick(nbytes)
            if who:
                v = self.d["visitors"].get(who) or {"name": "", "requests": 0, "first": time.time()}
                v["bytes"] = v.get("bytes", 0) + nbytes
                v["requests"] += 1
                v["last"] = time.time()
                if name:
                    v["name"] = name
                if identity:
                    v["identity"] = identity
                self.d["visitors"][who] = v
                if len(self.d["visitors"]) > VISITORS_KEPT:
                    oldest = min(self.d["visitors"], key=lambda k: self.d["visitors"][k].get("last", 0))
                    del self.d["visitors"][oldest]
            self.dirty = True

    def bump(self, key):
        with self.lock:
            self.d["totals"][key] += 1
            if key == "connects":
                self.add(connects=1)
            self.dirty = True

    def maze(self, kind, nbytes):
        """One fake maze page or file sent: counted apart from the real pages, per day and hour too."""
        with self.lock:
            t = self.d["totals"]
            t["maze"] += 1
            t["maze_bytes"] += nbytes
            if kind == "file":
                t["maze_files"] += 1
            self.add(**{"maze_file" if kind == "file" else "maze_page": 1, "maze_bytes": nbytes})
            self.tick(nbytes)
            self.dirty = True

    def link_traffic(self, rx, tx):
        """Bytes in and out on a closed link, headers and proofs included."""
        with self.lock:
            t = self.d["totals"]
            t["link_rx"] += rx
            t["link_tx"] += tx
            self.add(link_rx=rx, link_tx=tx)
            self.dirty = True

    def placeholders(self, path, this_view=False):
        """Numbers for the page about to be sent; this_view adds the view being served."""
        add = 1 if this_view else 0
        with self.lock:
            pages = self.d["paths"]["page"]
            return {b"{{visits}}": pages.get(path, 0) + add,
                    b"{{visits_total}}": sum(pages.values()) + add,
                    b"{{visits_today}}": self.d["days"].get(today(), {}).get("page", 0) + add}

    def snapshot(self):
        with self.lock:
            snap = json.loads(json.dumps(self.d))
            snap["minutes"] = {str(m): dict(r) for m, r in self.minute_rows.items()}
            return snap

    def summary(self):
        with self.lock:
            p = self.d["paths"]
            day = self.d["days"].get(today(), {})
            return {"since": self.d["since"],
                    "views": sum(p["page"].values()), "refreshes": sum(p["refresh"].values()),
                    "files": sum(p["file"].values()), "media": sum(p["media"].values()),
                    "today_views": day.get("page", 0), "today_connects": day.get("connects", 0),
                    "today_bytes": day.get("bytes", 0),
                    "bytes": self.d["totals"]["bytes"], "connects": self.d["totals"]["connects"],
                    "link_rx": self.d["totals"]["link_rx"], "link_tx": self.d["totals"]["link_tx"]}

    def reset(self):
        with self.lock:
            self.d = self.empty()
            self.dirty = True
        self.flush(force=True)

    def flush(self, force=False):
        if not force and (not self.dirty or time.time() - self.last_flush < FLUSH_SECONDS):
            return
        with self.lock:
            text = json.dumps(self.d, indent=1, sort_keys=True)
            self.dirty = False
            self.last_flush = time.time()
        tmp = self.file + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.file)
        except OSError:
            self.dirty = True
