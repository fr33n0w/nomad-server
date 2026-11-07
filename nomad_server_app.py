#!/usr/bin/env python3
"""nomad-server as a desktop app: no terminal, the web UI in a window of its own.
Closing the window stops the server. Any argument works as with nomad_server.py."""
import sys

rest = [a for i, a in enumerate(sys.argv[1:], 1)
        if not (a == "--base" or a.startswith("--base=") or sys.argv[i - 1] == "--base")]
if not rest:
    sys.argv[1:1] = ["--app", "--service"]

import nomad_server  # noqa: E402

if __name__ == "__main__":
    nomad_server.main()
