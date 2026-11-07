# nomad-server - developer notes

## Layout

```
nomad_server.py      entry point and console commands
nserver/server.py    one RNS instance, loads nodes/<id>/, jobs every 5 s, restart
nserver/node.py      one node: destination, request handlers, plugin, visitors
nserver/stats.py     persistent counters (data/stats.json of each node)
nserver/webui.py     HTTP server + JSON API (standard library)
nserver/web/         the web UI (no external libraries, strict CSP)
nserver/config.py    INI files, edited line by line to keep comments
tests/test_server.py end-to-end test on a private Reticulum (tests/rns)
```

## Why one process

Each node is an `RNS.Destination` with its own identity, registered in the same Reticulum instance. RNS allows any number of IN destinations; `Transport.deregister_destination` lets a node stop and start again without restarting the process. Two nodes with the same identity cannot coexist: the second one fails with "another node uses the same identity".

## Serving, as NomadNet 1.4.4 does

- one request handler per path (RNS keys handlers by the hash of the path), rescanned every `rescan_interval`
- files are returned as `[open handle, {"name": ...}]`: RNS sends them as a resource with metadata, NomadNet moves the temporary file by name inside the callback
- `/media` gets `data = {"path": "/media/x.webp", "key": ...}`; looked up in `pages/` (NomadNet) and then `files/` (nodes made with rns_server_page.py); non-webp images need Pillow
- `.allowed` next to a page or file: only those identity hashes (executable on Linux)
- executable pages only outside Windows, with `link_id`, `remote_identity`, `field_*`, `var_*` in the environment, 30 s timeout

## Counting

Every request counts. `refresh` = same link, same path, within 10 s and without form data: it keeps self-reloading partials (an animated header asks every 1-2 s) from swamping page views, without dropping them. A form sent twice is two views.

`{{visits_total}}` is the sum of page views of the node, so it always matches the Pages table in the stats.

Crawler mark: 20 distinct pages within 120 s on one link. Only a label; people browsing the whole node can trip it too.

Old `data/visits.json` ({path: count}, from rns_server_page.py) is imported once as page views when there is no stats.json yet.

Saving: every 30 s if changed, plus at stop; temp file + fsync + replace. A broken stats.json is renamed `.broken-<time>`, never overwritten.

Bytes are counted after the placeholders are filled, so they match what was sent. Link traffic (`link.rxbytes/txbytes`, headers included) is added when the link closes. The 60-minute activity strip is in memory only.

## Open: local client through rnsd

On the PC, `tests/probe.py` (a client of the local rnsd) sometimes gets the link but its request never reaches the server, while remote visitors and rBrowser are served. Seen after killing the server without a clean stop; an announce fixed it once, then it came back. Not understood yet.

## Rate limit

Per link, not per identity: unidentified visitors all have identity None and would share one bucket.

## Web UI security

- no password: open to whoever can reach host:port (default 127.0.0.1)
- password: pbkdf2-sha256 200k in server.cfg, session cookie HttpOnly + SameSite=Strict, 10 wrong tries in 5 minutes block login
- every POST needs `X-Requested-With: nomad-server` (a cross-site form cannot set it)
- console commands use the token in `data/.token` (0600), rewritten at each start
- all text from the network (announce names) is escaped in the UI

## Restart

From the UI, nodes and web UI restart inside the same process. Only a new Reticulum config folder needs a new process: under systemd (`INVOCATION_ID` set) it exits with 75 and systemd restarts it; otherwise it spawns itself with `--after=<pid>` and the new one waits for the old to exit (the RNS shared instance ports must be free).

Reticulum sets its own SIGINT/SIGTERM handlers when it starts, which exit at once: ours are set after it, so `systemctl stop` and `docker stop` save the counters first.

## Tests

```
python tests/test_server.py
```

Starts rnsd on tests/rns (ports 37490/37491, no interfaces), the server in tests/_run, then a real client: pages, refresh, .allowed, plugin, forms, files, media, identify, rate limit, API, password, restart. The client must sit on rnsd: an RNS client whose shared instance disappears exits the process.

## Building the programs

Nuitka 4.3 `--standalone`, from the files of the published tree (not from a working copy), RNS pinned to the version the release is tested with.

Common options: `--include-package=nserver --include-package=RNS --include-package-data=RNS --include-data-dir=nserver/web=nserver/web --include-data-dir=nserver/bait=nserver/bait --nofollow-import-to=pytest`.

| Program | Entry | Extra |
|---|---|---|
| CLI Windows | `nomad_server.py` | `--enable-plugin=tk-inter` (folder chooser), icon `nserver/web/nomad-server.ico`, version info |
| APP Windows | `nomad_server_app.py` | as CLI + `--windows-console-mode=disable --include-module=nomad_server`; pywebview installed in the build Python, Nuitka's own plugin takes it |
| CLI Linux (built and tested, not released yet) | `nomad_server.py` | `--nofollow-import-to=tkinter`: the folder chooser is zenity or kdialog, tkinter only from source |

Built on Windows with MinGW downloaded by Nuitka, on Linux (Debian 13, Python 3.13) in a venv with `nuitka rns ordered-set zstandard patchelf` from pip: no sudo, no system packages.

After the build: `RNS/Interfaces/__init__.py` builds `__all__` by globbing `*.py` in its folder. Compiled, the folder is empty and no interface is imported, so the build copies RNS's `Interfaces/*.py` into `<dist>/RNS/Interfaces/` (names only, the compiled modules load first).

The base folder is the program's folder (`__compiled__`), or `--base`. The compiled program has no `-c`: the folder chooser runs as `<exe> --pick-folder`, the app window as `<exe> --window URL`.

Checks: `NS_EXE=<cli program> python tests/test_server.py` and `tests/test_modes.py` run the same tests against the program (the `--set-password` checks only from source); the APP window is checked by starting it, capturing only its own window, closing it and checking that the server stopped.

Not done yet: APP for Linux. pywebview on Linux needs GTK + WebKit2 (system libraries, hard to bundle) or Qt (PySide6 + QtWebEngine, ~300 MB), and a desktop to test it. Until then the Linux CLI with `--app` opens Edge, Chrome or Chromium in `--app` mode, else the default browser.
