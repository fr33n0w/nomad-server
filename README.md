# nomad-server

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="nserver/web/splash-dark.svg">
  <img src="nserver/web/splash-light.svg" alt="nomad-server" width="420">
</picture>

Many [NomadNet](https://github.com/markqvist/NomadNet) nodes in one process, with a web UI to watch and manage them.

[![License](https://img.shields.io/badge/license-Reticulum%20License-blue.svg)](LICENSE) · [Italiano](README.it.md)

**Install guide (PDF):** [English](docs/nomad-server_Install_Guide_EN.pdf) · [Italiano](docs/nomad-server_Guida_Installazione_IT.pdf) - Windows, Linux, Raspberry Pi, venv and Docker, the first node, updates and common errors.

![Overview](docs/overview.png)

## Features

- **Many nodes, one process**: each node has its own folder, identity, pages, files and counters
- **Serves like NomadNet**: `.mu` pages, executable pages, `.allowed` lists, `/file/` downloads, inline images
- **Web UI**: overview, stats per hour and day, live log, settings, page editor, light and dark theme
- **Counters**: every request and every byte, `{{visits}}` placeholders in the pages, probable crawlers marked
- **Visitors**: who is connected, who identified, block a peer or a single link, blackhole an identity in Reticulum
- **Plugins**: dynamic pages in Python inside the server (guestbooks, forms, live data), also on Windows
- **Backup**: one zip per node, identity export and import, removed nodes kept in a trash
- **Honeypot nodes** (advanced): bait pages and hidden traps that catch crawlers
- Runs on Windows, Linux, macOS, Raspberry Pi and Docker; only needs `rns`

## Quick start

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
pip install rns
python nomad_server.py
```

Open http://127.0.0.1:8484, press **Create new node**, put your pages in `nodes/<node>/pages/` (`index.mu` is the home page), then press **Start**. New nodes start stopped, so nothing goes online before it is ready.

If `rnsd` is running, the nodes use it; otherwise nomad-server runs its own Reticulum instance with your `~/.reticulum` config.

## Ways to run it

| How | Command | Notes |
|---|---|---|
| Terminal | `python nomad_server.py` | log on screen, Ctrl+C stops it |
| App window | `python nomad_server.py --app` | the UI in its own window (pywebview if installed, else Edge or Chrome); closing the window stops a server it started |
| Windows | `start.bat`, `start_hidden.vbs` | the second one runs with no window at all |
| Windows, no Python | `NomadServer-CLI-v0.1.3-win.exe`, `NomadServer-APP-v0.1.3-win.exe` | from [Releases](https://github.com/fr33n0w/nomad-server/releases), see [Programs without Python](#programs-without-python) |
| Desktop icons | Settings > Shortcuts | desktop and app menu, Windows and Linux desktops |
| Debian, Ubuntu, Raspberry Pi | `bash install.sh` | systemd service at boot, see [Installer](#installer-debian-ubuntu-raspberry-pi) |
| pipx | `pipx install git+https://github.com/fr33n0w/nomad-server` | then `nomad-server`; data in `~/.nomad-server` |
| Docker | `mkdir nomad-data && docker compose up -d` | see [Docker](#docker) |

Only one server runs on a folder: the same identities can never be online twice. Started again with `--app`, it just opens the window.

### Programs without Python

No Python needed, for Windows for now: unzip and double-click.

| Program | What it is |
|---|---|
| `NomadServer-CLI-v0.1.3-win.exe` | the server with its terminal, like `python nomad_server.py` |
| `NomadServer-APP-v0.1.3-win.exe` | no terminal, the UI in its own window; closing it stops the server |

**Your data stays next to the program**: `server.cfg`, `nodes\` and `data\` in the program's folder. A copy unzipped somewhere else is a new, empty server with new node addresses. To always run the same server, give its folder with `--base`, also in desktop shortcuts (Properties > Target):

```
NomadServer-APP-v0.1.3-win.exe --base D:\nomad-data
```

`--base` also takes the folder of an existing installation, with its nodes and settings: stop that server first. Icons made by Settings > Shortcuts already carry their folder.

**Updating**: the version is in the file name, so a new zip unzipped elsewhere is a new, empty server. Unzip it over the old folder (`server.cfg`, `nodes\` and `data\` stay), or keep your data in a folder of its own with `--base`. Then press Settings > Shortcuts again: the icons still point to the old exe.

### Installer (Debian, Ubuntu, Raspberry Pi)

```
sudo apt install git python3-pip
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
bash install.sh
```

Run it as your normal user (not root): it uses `sudo` only to install the service. It:

1. installs `rns` if missing: in the venv when there is one (see [venv](#in-a-virtual-environment-venv)), otherwise it asks before installing it for your user (`pip --user`)
2. if the web UI port (8484) is already in use, asks for another one
3. asks whether to open the web UI to the local network (`0.0.0.0`); if yes, it asks for a password (empty = none)
4. asks the name of your first node (only if there are no nodes yet)
5. asks whether to install the systemd service `nomad-server`: it starts at boot, as your user, from this folder, after `rnsd` when `rnsd.service` exists. If `nomad-server` is already used by another folder, it asks a name for this one (e.g. `nomad-server-2`), so several servers can run side by side.

Running it again is safe: it keeps your nodes, settings and service name, and restarts the service. Then (use your service name):

| What | Command |
|---|---|
| state | `python3 nomad_server.py --status` (in a venv: `venv/bin/python3 nomad_server.py --status`) |
| log | `journalctl -u nomad-server -f` |
| stop, start, restart | `sudo systemctl stop nomad-server` (`start`, `restart`) |
| update | `git pull`, then `sudo systemctl restart nomad-server` |
| remove the service | `sudo systemctl disable --now nomad-server && sudo rm /etc/systemd/system/nomad-server.service && sudo systemctl daemon-reload` |

Under systemd `--stop` is refused (systemd would start it again): use `systemctl`. Restart from the web UI works.

**rnsd under another user.** Pages are served anyway, but `rnsd` refuses the status requests of other users: no Reticulum status in the web UI and no visitor names (the log says so). Give nomad-server the same RPC key as `rnsd`. As the user of `rnsd`:

```
python3 -c "import RNS, os; print(RNS.Identity.full_hash(RNS.Identity.from_file(os.path.expanduser('~/.reticulum/storage/transport_identity')).get_private_key()).hex())"
```

then, in `~/.reticulum/config` of the nomad-server user, under `[reticulum]`, add `rpc_key = <that key>` and restart nomad-server. The key gives control of `rnsd`: keep it private.

### In a virtual environment (venv)

A venv keeps `rns` and its libraries inside the nomad-server folder, apart from the system Python. On Debian, Ubuntu and Raspberry Pi OS first: `sudo apt install git python3-venv`.

**With the installer** (Debian, Ubuntu, Raspberry Pi), for a server that starts at boot:

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
python3 -m venv venv
bash install.sh
```

The installer finds `venv` (or `.venv`) in the folder by itself, also when it is not activated: it installs `rns` in it and the service runs with the venv python. Running it again later keeps using the venv. A venv kept elsewhere (e.g. `~/venvs/nomad`) works too: activate it before `bash install.sh`. Nothing is installed outside a venv without asking: if no venv is in use, answer `n` and the installer stops with nothing changed. Then everything is as in [Installer](#installer-debian-ubuntu-raspberry-pi), with the venv python for the commands:

| What | Command |
|---|---|
| state | `venv/bin/python nomad_server.py --status` |
| log | `journalctl -u nomad-server -f` |
| update | `git pull`, `venv/bin/pip install -U rns`, then `sudo systemctl restart nomad-server` |

**By hand** (Linux, macOS, Raspberry Pi):

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
python3 -m venv venv
venv/bin/pip install rns
venv/bin/python nomad_server.py
```

Windows:

```
git clone https://github.com/fr33n0w/nomad-server
cd nomad-server
python -m venv venv
venv\Scripts\pip install rns
venv\Scripts\python nomad_server.py
```

Then as in [Quick start](#quick-start): open http://127.0.0.1:8484, press **Create new node**, then **Start**. Ctrl+C stops the server.

Use the venv python for every command: `venv/bin/python nomad_server.py --status` (also `--stop`, `--new-node NAME`, `--app`, ...), or run `source venv/bin/activate` (Windows `venv\Scripts\activate`) once in the terminal and then plain `python`. To update: `git pull` and `venv/bin/pip install -U rns`. Desktop icons made from Settings > Shortcuts use the venv by themselves.

**As a command**: `venv/bin/pip install git+https://github.com/fr33n0w/nomad-server`, then `venv/bin/nomad-server` (data in `~/.nomad-server`).

**Where Reticulum runs.** The venv always needs its own `rns`: it is the library nomad-server is made of. Who runs the Reticulum network is up to you:

- *rnsd already installed for your user, nomad-server in a venv*: nothing else to do. nomad-server connects to the running `rnsd` and the installer starts it after `rnsd.service`; when `rnsd` restarts, nomad-server reconnects by itself. Run nomad-server as the same user as `rnsd` (otherwise see *rnsd under another user* above) and keep the two `rns` close in version (`pip install -U rns` on both).
- *Everything in the venv, no rnsd*: nomad-server runs Reticulum by itself, with the interfaces of `~/.reticulum/config`. Nothing is installed outside the venv. While it runs, other programs (NomadNet, rBrowser) can use it as their shared instance; they are cut off while nomad-server restarts.
- *Everything in the venv, rnsd from the venv*: `rns` in the venv also has `venv/bin/rnsd`. Make it a service, then run the installer (it finds `rnsd.service` and starts after it). From the nomad-server folder:

```
python3 -m venv venv
venv/bin/pip install rns
sudo tee /etc/systemd/system/rnsd.service >/dev/null <<EOF
[Unit]
Description=Reticulum rnsd (venv)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
ExecStart=$PWD/venv/bin/rnsd --service
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now rnsd
bash install.sh
```

If the web UI port is already in use (for example another nomad-server), the server stops with a message: change `port` in the `[webui]` section of `server.cfg` and start it again.

## Command line

`python nomad_server.py [options]`, or `nomad-server [options]` when installed with pip or pipx.

**Starting the server**

| Option | |
|---|---|
| *(none)* | run, log on the terminal |
| `--app` | also open the web UI in an app window (pywebview if installed, else Edge or Chrome); closing it stops a server it started. If the server already runs, only opens the window |
| `--service` | quiet: only warnings and errors on the terminal or journal, full log in `logs/server.log` |
| `--no-webui` | no web interface; the console commands below still work |
| `--verbose` | Reticulum debug log |
| `--base DIR` | folder with `server.cfg`, `nodes/` and `data/`. Default: next to the program, `~/.nomad-server` when installed with pip or pipx |

**Commands to the running server** (on the same folder, so with the same `--base`)

| Option | |
|---|---|
| `--status` | nodes, interfaces, visitors connected now |
| `--json` | with `--status`: the raw data |
| `--log [N]` | last N lines of the log (60 if N is left out) |
| `-f`, `--follow` | with `--log`: keep showing new lines (Ctrl+C to stop) |
| `--node ID` | with `--log`: only this node |
| `--stop` | stop it, also a hidden one (refused under systemd: use `systemctl stop nomad-server`) |
| `--block LXMF` | block a peer on every node |
| `--reason TEXT` | with `--block`: the reason shown to it |
| `--mode MODE` | with `--block`: `page` (default, a page with the reason), `silent`, `tarpit` or `maze` |
| `--unblock LXMF` | remove a peer from the blocklist |
| `--blocklist` | print the blocklist (identity hash, LXMF address, reason) |
| `--export-blocklist FILE` | save it to a text file |

**Working on the files** (no running server needed; restart it afterwards)

| Option | |
|---|---|
| `--new-node NAME` | create a node folder, with a new identity and a sample `index.mu` |
| `--set-password` | set the web UI password (empty = no password); with the server running it applies at once, no restart |

**Other**: `--version`, `-h` / `--help`.

## Web UI

| | |
|---|---|
| ![Stats](docs/stats.png) | ![Logs](docs/logs.png) |
| **Stats**: requests and traffic per node, or all nodes added up, over 1 hour (per minute), 24 hours, 7, 14, 30 or 90 days, top pages and files, identified visitors | **Logs**: live, one color per node, filters for pages, visitors, files and warnings |

- **Overview**: nodes with a requests chart, Reticulum interfaces, the clients connected now (a closed link stays there greyed for a minute), the incoming connections history (newest first, 20 at a time, pages visited per link; the last 1000 per node, in `data/history.jsonl`)
- **Settings**: server, web UI and node settings, identity, backup and restore, blocklist, removed nodes, shortcuts
- **Edit pages**: a file manager with an editor on each node; a saved page is served at once
- The address shown in the UI can be copied with one click; on a phone the layout follows

The web UI listens on 127.0.0.1:8484. To reach it from other devices set `host = 0.0.0.0` in `server.cfg` (or answer yes in `install.sh`) and set a password: whoever reaches the UI can manage the nodes. Over the network, editing pages and handling identities need the password.

### Password and access

A safety rule: the UI can be reached from the network, so what touches files and identities is never open to a stranger.

| | From the server's computer | From another device (LAN, VPN) |
|---|---|---|
| **No password set** | everything | everything except the actions below marked *password* |
| **Password set** | login, then everything | login, then everything except *this computer only* |

| Needs the password over the network | Only from the server's computer |
|---|---|
| Edit pages and files (save, new, rename, delete, upload) | Setting the first password (when none is set yet), unless *Trust local network* is on |
| | Turning *Trust local network* on or off (or with the password) |
| | Settings > Shortcuts (desktop icons) |
| Import existing node (autoscan, folders, upload) | Open folder (the system's file window) |
| Download a node's identity or backup | Browse folders on this computer (import) |
| Restore from nS backup, Trash restore and delete for good | |
| New or imported identity of a node | |

Everything else (start, stop, settings, stats, logs, blocklist, honeypots) works from any device that reaches the UI. Set the password on the server's computer, from Settings > Web UI password or with `--set-password` (from another device it can only be changed once one is set, after login: otherwise a stranger could set one and lock you out); once set, every browser has to log in, the server's own included. Console commands (`--status`, `--stop`, ...) keep working on the server's computer.

On a headless server (ssh): `--set-password` on the running server applies at once, no restart. Alone at home? Settings > Server > **Trust local network** (`[webui] trust_lan = yes`) lets the first password be set from any device on your LAN (192.168.x, 10.x, 172.16-31.x): only if you trust everyone on that network and Wi-Fi.

## Nodes

```
nodes/my-node/
  node.cfg      settings (also from the web UI)
  identity      the node's private key: keep it private
  pages/        .mu pages, index.mu is the home page
  files/        downloads, served as /file/...
  data/         counters
  plugin.py     optional, dynamic pages
```

Pages are read at each request, so a changed page is served at once. New and deleted files are picked up every 5 minutes (`rescan_interval`), at once with **Rescan pages** or when saved in the editor.

| Setting | Default | |
|---|---|---|
| `name` | | name in NomadNet and in the announces |
| `enabled` | yes | start with the server |
| `announce_at_start` | yes | after a restart a node is reachable only once it announces again |
| `announce_interval` | 360 | minutes, 0 = only at start |
| `require_identify` | no | only visitors who identify see the pages |
| `requests_per_minute` | 120 | per visitor, 0 = no limit |
| `show_on_pages` | yes | replace `{{visits}}`, `{{visits_total}}`, `{{visits_today}}` |
| `footer.visits` | yes | a visits line at the bottom of pages without their own counter |
| `footer.watermark` | yes | a "served by nomad-server" line |

**Backup node** saves a zip with identity, settings, pages, files and counters; **Restore from nS backup** brings it back with the same address. **Import existing node** brings in a node of NomadNet or another server (see [Moving an existing node here](#moving-an-existing-node-here)). **Import identity** gives a node the address of an existing one. Two nodes of a server can never share an identity.

## Counters

Every request is counted, reloads and identified visitors included. A page asked again by the same visitor within 10 seconds is a *refresh* (partials that reload themselves), shown apart. Links that open many pages fast are marked as probable crawlers, and still counted. Traffic is counted apart: content sent per page and file, link traffic in and out. Counters are saved every 30 seconds and when the server stops.

## Dynamic pages

- **Executable pages**, as in NomadNet: a `.mu` with a shebang (`#!/usr/bin/env python3`) and `chmod +x` runs at each request and what it prints is the page. Form fields and the visitor arrive as environment variables (`field_*`, `var_*`, `link_id`, `remote_identity`). On Windows pages with a Python shebang run too.
- **Plugins**: a `plugin.py` in the node folder, loaded inside the server when the node starts. No new process per request, also on Windows, and it gets the visitor's LXMF address and name.

```python
PATHS = ["/page/guestbook.mu"]           # the pages this plugin makes

def start(node):                         # optional: when the node starts
    node.log("guestbook ready")

def page(path, data, who=None, who_name="", link_id=None):
    return ">Guestbook\n\nHello " + (who_name or "stranger") + "\n"

def stop():                              # optional: when the node stops
    pass
```

| | |
|---|---|
| `PATHS` | the pages the plugin answers. They show in the page list and win over a file with the same name |
| `paths()` + `handles(path)` | instead of `PATHS`, for pages that change: `paths()` lists them, `handles(path)` says True for each path the plugin answers (for example everything under `/page/forum/`) |
| `start(node)` | runs once. `node.folder`, `node.pages_path`, `node.files_path`; `node.log(text, "info"/"warn"/"error")` writes to the node's log. Start threads or background programs here |
| `page(...)` | `path` as asked (`/page/guestbook.mu`); `data` the form fields and variables (`field_*`, `var_*`) or None; `who` the visitor's LXMF address if they identified, `who_name` their announced name, `link_id` the link. Returns micron as text or bytes |
| `stop()` | at node stop or restart: stop what `start` started |

- A change to `plugin.py` counts after **Restart** of the node. Errors go to the node's log: an error in `page()` shows the visitor "Error while making this page, try again later."
- Plugin pages get the footer and the `{{visits}}` counters like the others.
- Helper modules (`import board`) are found in the node folder. Plugins of all nodes share one Python process: give their helper modules distinct names.
- Another file name: `plugin = myfile.py` in the `[node]` section of `node.cfg`.

## Moving an existing node here

A node's address comes from its `identity` file (the private key). Give that file to a node of nomad-server and the node keeps its address: visitors, bookmarks and links keep working.

**What you need from the old node**

| | NomadNet | `rns_server_page.py` and similar |
|---|---|---|
| identity | `~/.nomadnetwork/storage/identity` | the `identity` file in its folder |
| pages | `~/.nomadnetwork/storage/pages/` | its `pages/` |
| files | `~/.nomadnetwork/storage/files/` | its `files/` |
| counters | - | `data/visits.json`, if any |

NomadNet may use `~/.config/nomadnetwork/` instead of `~/.nomadnetwork/`; `pages_path` and `files_path` in the `[node]` section of its `config` can point elsewhere. On Windows `~` is `C:\Users\<you>`.

**With Import existing node**

Overview > **Import existing node** does steps 1-4 below for you:

> **From another device** (LAN, VPN) import needs a web UI password: set it on the server's computer (Settings > Web UI password, or `--set-password`), log in, then import. Without it the dialog says so. It is a safety rule: import lists the server's folders and copies identities, so over the network only someone who knows the password can do it.

- **Autoscan import** searches the computer running the server for folders with an `identity` and `pages/` with `.mu` pages (NomadNet, `rns_server_page.py` and similar, also inside venvs like `~/.venvs/<name>/`) and lists them with name, path, pages and address.
- **Manual import** lets you choose the old folder yourself: **Browse folders** opens the system's folder window (on the server's own computer), or move through the folders in the window (for NomadNet, `.nomadnetwork` in your home folder). A folder without an identity is imported with a new address.

The name comes from the old config (NomadNet `node_name`, `display_name` of `config.cfg.owr` or `config.cfg`), and you can change it. Identity, pages, files and `data/visits.json` are copied into a new node; the old folder is not touched. Without `.mu` pages there is nothing to import and no node is made. The new node stays stopped: do steps 5 and 6. Like editing pages, it works from the server's own computer or with a password set.

**By hand**

1. **Create new node**, with the name the old node had. It starts stopped, so its new identity never goes on the network.
2. Settings > Node > **Import identity**, choose the old `identity` file. The address shown under the node changes to the old one: check it is the one you know.
3. Copy the old pages into `nodes/<node>/pages/` and the files into `nodes/<node>/files/` (Settings shows where they are, **Open folder** opens them). The old `index.mu` replaces the sample one. On Linux copy with `cp -a`, so executable pages stay executable; `.allowed` lists work as in NomadNet.
4. Optional: copy `data/visits.json` into `nodes/<node>/data/`. The old counts are added to the new ones, once.
5. Stop the old node:
   - NomadNet: in its `config`, section `[node]`, set `enable_node = no` and restart it. NomadNet keeps working as a messenger with the same LXMF address: only the node moves.
   - another server: stop it and disable its service (`sudo systemctl disable --now NAME`).
6. Press **Start** on the node, then **Announce**. Open it from another computer (NomadNet, MeshChat, rBrowser) to check.

Never serve the same node from two places at once: the network would see two nodes with one address.

**Pages that a program keeps writing**

If a script regenerates the pages, point it to `nodes/<node>/pages/`, or replace the `pages/` folder with a link to the old one:

```
rm -r nodes/my-node/pages && ln -s /path/old/pages nodes/my-node/pages        # Linux, macOS
rmdir /s /q nodes\my-node\pages & mklink /J nodes\my-node\pages C:\path\old\pages  # Windows
```

**Code that made pages inside the old server**

It becomes the node's `plugin.py` (see [Dynamic pages](#dynamic-pages)). A program that runs in the background, like a page generator, can be started by the plugin with the node and stopped with it:

```python
import subprocess, sys
proc = None

def start(node):
    global proc
    proc = subprocess.Popen([sys.executable, "/path/generator.py", "--loop"])

def stop():
    if proc:
        proc.terminate()
```

## Visitors and blocking

Peers that identify are listed with their LXMF address and, when they announced one, their name. **Block** keeps a peer out of one node or all of them, with a reason, in four ways: a page with the reason, *silent* (dropped), *tarpit* (connected, no answers: time lost for a crawler, no bytes on the mesh) or *maze* (honeypots only). A link that did not identify can be blocked until it disconnects. **Export as text** writes the blocklist with identity hashes.

**Blackhole** adds a peer's identity to Reticulum's blackhole, by hand only: the local Reticulum instance drops its announces and the paths to its destinations, for every program using it. To share it, Reticulum transport nodes can publish their blackhole (`publish_blackhole`) and others can follow it (`blackhole_sources`).

## Honeypot nodes

![Honeypot](docs/honeypot.png)

Hidden behind advanced mode: click the logo in **About** five times (saved in server.cfg, `[webui] advanced`, the same in every browser). A honeypot is a node full of believable bait pages with trap links that people never see and crawlers follow. Whoever opens a trap, hops to a new link for every page, or reads too many pages too fast is caught: by its LXMF address if it identified, otherwise for the rest of its link. Caught peers can get a tarpit or a maze that never runs out and is never refused (fake pages, fake file downloads or both), so a crawler walks it until it disconnects, and keep getting it when they come back. They go into the blocklist only if you turn on *Put caught visitors in the blocklist*. The Honeypot tab keeps the connection history, the known crawlers and whether they come back, even on new honeypots. Both lists have **Export as text**: the peer connections as shown (filter and order), the known crawlers with every honeypot they visited and what they took from it (requests, bytes sent, maze answers). **Clear list** empties the known crawlers in the web UI only: nothing is deleted, they stay caught, and one that comes back shows again with all its data. A peer caught by several honeypots is one row that names them all, with the maze pages and files it took listed by name. The stats add up all the honeypots, or show one, like the Stats tab: requests, traffic and a chart, with the maze counted apart from the bait pages. **Slow maze** (honeypot settings): every fake page or file is answered late, so a crawler's page budget costs it more time. *Adaptive* answers just before the visitor's request times out (worked out from the link's round trip, about 10 s with Reticulum's default timeout); *fixed* waits the seconds you choose. Longer than the client's timeout and the crawler gets nothing to follow. The time held shows in the peer list. **Release from maze** takes a link that did not identify out of the maze: it gets the real pages again until it opens a trap again; a new link starts fresh. **Addresses** (honeypot settings, 1 to 3): the same pages on more addresses, announced as "<name> N1", "N2", "N3" two seconds apart; crawlers keep a page budget per node, so each address catches more. An identity that connects to every address within *multi_seconds* (600 by default) is caught like a trap: people pick one node from a list, crawlers take them all. New honeypots answer with the maze and keep caught peers out of the blocklist. Live clients and the peer list show how many hops away a link came from, and its interface when Reticulum tells it (with rnsd, for identified peers: the interface rnsd uses to reach them). Turning advanced mode off stops every honeypot, which stays stopped until started again, and hides them from all tabs; the blocklist stays as it is.

## Docker

```
mkdir nomad-data
NOMAD_PASSWORD=yourpassword docker compose up -d
```

The UI is on port 8484, everything else in `nomad-data/`: settings, nodes, identities, counters, logs and the Reticulum config (`nomad-data/reticulum/config`, created at the first start: add an interface, Docker networks have no peers for AutoInterface). The compose file runs as user 1000:1000 so the files stay yours, and comments show how to use the host's `rnsd` instead. Environment variables: `NOMAD_PASSWORD` (web UI password), `NOMAD_PORT` (UI port inside the container, default 8484), `NOMAD_RNS_CONFIG` (Reticulum config folder, default `/data/reticulum`). Console commands: `docker compose exec nomad-server python /app/nomad_server.py --base /data --status`. **Stop server** in the UI is restarted by Docker; use `docker compose stop`.

## Resources

On a Debian server with three busy nodes and a plugin, the process uses about 95 MB: about 36 MB are Python and Reticulum, the rest the nodes, counters and the plugin's data. An empty server uses about 47 MB.

## What's new in 0.1.3

Every version: [CHANGELOG.md](CHANGELOG.md).

- Programs without Python: CLI and APP for Windows, see [Programs without Python](#programs-without-python).
- App window with pywebview (`--app`, and the APP exe).
- Dark theme by default.
- Advanced mode saved in `server.cfg`, the same in every browser.
- Import from another device: the reason shown when refused.
- Autoscan finds nodes inside venvs, skips nomad-server's own folder.
- The first web UI password can only be set on the server's computer. See [Password and access](#password-and-access).
- Live clients keep a closed link for a minute: short visits no longer slip by unseen.
- Incoming connections history in the Overview, with the pages visited.
- `--set-password` applies at once on a running server; optional *Trust local network* for the first password.

## What's new in 0.1.2

- Import existing node: autoscan or folder explorer.
- Import from a remote browser: folder upload.
- Restore button renamed Restore from nS backup.
- Stopped nodes show their pages and files.
- Nodes announced again when rnsd comes back.
- Honeypots: up to 3 addresses, N1 N2 N3.
- Same peer on every address: caught.
- New honeypots answer with the maze.
- Hops and interface of incoming links.
- Stats: All nodes added up.
- Reload button in the header.

## What's new in 0.1.1

- Maze answers: pages, files or both.
- Maze never refused, no rate limit.
- Trap pages shown as traps, not blocked.
- Peer list: newest or oldest first, rows stay still.
- Up to 60 pages listed per connection.
- Honeypot lists: Export as text.
- One row per peer on all the honeypots.
- Maze pages and files listed by name.
- Honeypot stats: one block with a chart.
- Slow maze: fixed or adaptive delay, pages and files.
- Install guide PDF, English and Italian.
- Stats periods: 1 hour, 24 hours, 7 to 90 days.
- Known crawlers: what each one took.
- Release from maze, for links.
- Honeypot tab stays still while rows arrive.
- Known crawlers: Clear list (nothing deleted).

## Roadmap

- Programs for Linux too: CLI, and APP with its own window like on Windows.
- Integration with rBrowser2: host nodes from the browser.
- Experimental: a fingerprint of links that do not identify (hops, interface, link settings, timing) to group the ones that probably come from the same client. An estimate, never a proof: shown, not used to block.

## Credits

- [Reticulum](https://github.com/markqvist/Reticulum) and [NomadNet](https://github.com/markqvist/NomadNet) by Mark Qvist: nomad-server is built on Reticulum and serves pages the way NomadNet does. It uses the Reticulum License, as Reticulum does.
- [rns_server_page](https://github.com/SebastianObi/RNS-Tools/tree/main/rns_server_page) by Sebastian Obele: the page server this project grew out of: it began as counters, stats and a web UI added to it, then became a server of its own. It still reads its node folders and imports its `visits.json`. Its MIT notice is in [LICENSE](LICENSE).

## License

[Reticulum License](LICENSE), as Reticulum itself.
