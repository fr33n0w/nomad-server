"""server.cfg and node.cfg: INI files read with configparser and updated
line by line, so the comments in them stay where they are."""
import configparser
import hashlib
import hmac
import os
import re
import secrets

SERVER_DEFAULTS = {
    "server": {
        "rns_config": "",
        "nodes_dir": "nodes",
        "log_to_file": "no",
    },
    "blocklist": {
        "reasons": "Crawler|Too many requests (flooding)|Spam|Abuse",
    },
    "webui": {
        "enabled": "yes",
        "splash": "yes",
        "host": "127.0.0.1",
        "port": "8484",
        "password": "",
    },
}

NODE_DEFAULTS = {
    "node": {
        "name": "New node",
        "enabled": "yes",
        "announce_at_start": "yes",
        "announce_interval": "360",
        "rescan_interval": "5",
        "plugin": "plugin.py",
        "type": "normal",
        "require_identify": "no",
    },
    "limits": {
        "requests_per_minute": "120",
    },
    "counter": {
        "show_on_pages": "yes",
    },
    "honeypot": {
        "traps": "",
        "mode": "tarpit",
        "reason": "Crawler",
        "to_blocklist": "no",
        "autoblock": "no",
        "crawler_pages": "20",
        "crawler_seconds": "120",
        "hop_links": "6",
        "hop_seconds": "7200",
        "long_pages": "60",
        "long_seconds": "3600",
    },
    "footer": {
        "visits": "yes",
        "watermark": "yes",
        "center": "yes",
    },
}

SERVER_TEMPLATE = """# nomad-server configuration
# The web UI and the console commands read and write this file.

[server]
# Reticulum config folder; empty = the default (~/.reticulum)
rns_config =
# Folder with one sub-folder per node
nodes_dir = nodes
# yes = log to logs/server.log instead of the terminal
log_to_file = no

[blocklist]
# Reasons offered when you block a visitor, separated by |
reasons = Crawler|Too many requests (flooding)|Spam|Abuse

[webui]
# no = no web interface (same as --no-webui); console commands keep working on 127.0.0.1
enabled = yes
# yes = show the logo for a few seconds when the web UI opens
splash = yes
# 127.0.0.1 = this computer only, 0.0.0.0 = the whole network (set a password)
host = 127.0.0.1
port = 8484
# Set it with: nomad_server.py --set-password (stored as a hash)
password =
"""

NODE_TEMPLATE = """# Node configuration: pages/ and files/ next to this file are served.

[node]
# Name shown in NomadNet and in the announces
name = {name}
enabled = yes
announce_at_start = yes
# Minutes between announces, 0 = only at start
announce_interval = 360
# Minutes between scans of pages/ and files/ for new or deleted files, 0 = never
rescan_interval = 5
# Python file with dynamic pages, next to this file (optional)
plugin = plugin.py
# yes = only visitors who identify see the pages (the others get a page asking for it)
require_identify = no

[limits]
# Requests per minute from one visitor (link), 0 = no limit
requests_per_minute = 120

[counter]
# Requests are always counted; yes = also replace {{{{visits}}}},
# {{{{visits_total}}}} and {{{{visits_today}}}} in the pages
show_on_pages = yes

[honeypot]
# Trap pages, comma separated, * at the end = everything below (e.g. /page/trap/*).
# Whoever opens one is caught: by LXMF address if identified, for the rest of its link if not.
traps =
# how caught visitors are answered: page (with the reason), silent, tarpit, or an endless
# maze, never refused and with no rate limit: maze (fake pages), maze_files (fake file
# downloads) or maze_both
mode = tarpit
reason = Crawler
# yes = identified visitors caught here also go into the blocklist;
# no = they stay caught on the honeypots only (listed in the known crawlers)
to_blocklist = no
# yes = also block crawler-like visitors:
#  - one link opening crawler_pages different pages within crawler_seconds (fast crawlers)
#  - one link opening long_pages different pages within long_seconds (slow, on one link)
#  - one identity opening hop_links links with one or two pages each within hop_seconds
#    (slow crawlers: people keep one link open while they browse)
autoblock = no
crawler_pages = 20
crawler_seconds = 120
hop_links = 6
hop_seconds = 7200
long_pages = 60
long_seconds = 3600

[footer]
# Lines added at the bottom of .mu pages (not to partials loaded inside other pages)
# visits: the visit counters, on pages that have no {{{{visits}}}} of their own
visits = yes
# watermark: "served by nomad-server" with the project link
watermark = yes
# center the footer lines (no = left)
center = yes
"""


class Config:
    def __init__(self, path, defaults, template=None):
        self.path = path
        self.defaults = defaults
        if template is not None and not os.path.isfile(path):
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(template)
        self.reload()

    def reload(self):
        self.cp = configparser.ConfigParser(interpolation=None)
        self.cp.read_dict(self.defaults)
        if os.path.isfile(self.path):
            self.cp.read(self.path, encoding="utf-8")

    def get(self, section, key):
        return self.cp.get(section, key, fallback=self.defaults.get(section, {}).get(key, ""))

    def getint(self, section, key):
        try:
            return int(self.get(section, key))
        except ValueError:
            return int(self.defaults[section][key])

    def getbool(self, section, key):
        return self.get(section, key).strip().lower() in ("yes", "true", "on", "1")

    def as_dict(self):
        return {s: dict(self.cp.items(s)) for s in self.defaults}

    def set(self, values):
        """values = {section: {key: value}}; rewrites only those lines."""
        lines = []
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        for section, kv in values.items():
            for key, value in kv.items():
                value = str(value).replace("\n", " ").strip()
                lines = _set_line(lines, section, key, value)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, self.path)
        self.reload()


def _set_line(lines, section, key, value):
    current, start, end = None, None, len(lines)
    for i, raw in enumerate(lines):
        m = re.match(r"\s*\[([^\]]+)\]", raw)
        if m:
            if current == section:
                end = i
                break
            current = m.group(1).strip()
            if current == section:
                start = i
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        return lines + ["[%s]" % section, "%s = %s" % (key, value)]
    for i in range(start + 1, end):
        m = re.match(r"\s*([^#;=\s][^=]*?)\s*=", lines[i])
        if m and m.group(1).strip() == key:
            lines[i] = "%s = %s" % (key, value)
            return lines
    insert = end
    while insert > start + 1 and not lines[insert - 1].strip():
        insert -= 1
    return lines[:insert] + ["%s = %s" % (key, value)] + lines[insert:]


def hash_password(password):
    salt = secrets.token_hex(16)
    rounds = 200000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), rounds).hex()
    return "pbkdf2:%d:%s:%s" % (rounds, salt, digest)


def check_password(stored, password):
    try:
        _, rounds, salt, digest = stored.split(":")
        test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(test, digest)
    except (ValueError, TypeError):
        return False
