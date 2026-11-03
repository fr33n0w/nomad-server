#!/bin/bash
# nomad-server installer for Debian/Ubuntu/Raspberry Pi OS (systemd).
# Run it from the nomad-server folder:  bash install.sh
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
U="$(id -un)"
PY="$(command -v python3 || true)"
# A venv in this folder is used even when not activated (a later run must not switch the service to the system python)
if [ -z "$VIRTUAL_ENV" ]; then
    for V in venv .venv; do
        if [ -x "$DIR/$V/bin/python3" ]; then
            PY="$DIR/$V/bin/python3"
            VIRTUAL_ENV="$DIR/$V"
            echo "Using the venv in $VIRTUAL_ENV"
            break
        fi
    done
fi

[ -n "$PY" ] || { echo "python3 not found: sudo apt install python3 python3-pip"; exit 1; }

echo "== Reticulum (rns)"
if ! "$PY" -c "import RNS" 2>/dev/null; then
    if [ -n "$VIRTUAL_ENV" ]; then
        "$PY" -m pip install rns
    else
        "$PY" -m pip install --user rns --break-system-packages || "$PY" -m pip install --user rns
    fi
fi
"$PY" -c "import RNS; print('rns', RNS.__version__ if hasattr(RNS, '__version__') else 'ok')"

CFG="import sys; sys.path.insert(0, '.'); from nserver import config; c = config.Config('server.cfg', config.SERVER_DEFAULTS)"
[ -f server.cfg ] || "$PY" -c "import sys; sys.path.insert(0, '.'); from nserver import config; config.Config('server.cfg', config.SERVER_DEFAULTS, config.SERVER_TEMPLATE)"

# Web UI port: another program (or another nomad-server) may already use it
if ! "$PY" nomad_server.py --status >/dev/null 2>&1; then
    while true; do
        PORT="$("$PY" -c "$CFG; print(c.getint('webui', 'port'))")"
        "$PY" -c "import socket, sys; s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('', int(sys.argv[1])))" "$PORT" 2>/dev/null && break
        echo
        read -r -p "Port $PORT is already in use. Web UI port for this server [$((PORT + 1))]: " NEWPORT || NEWPORT=
        NEWPORT="${NEWPORT:-$((PORT + 1))}"
        case "$NEWPORT" in ''|*[!0-9]*) echo "Not a number."; continue ;; esac
        "$PY" -c "$CFG; c.set({'webui': {'port': sys.argv[1]}})" "$NEWPORT"
    done
fi

echo
read -r -p "Open the web UI to the local network (0.0.0.0)? [y/N] " OPEN || true
if [ "$OPEN" = y ] || [ "$OPEN" = Y ]; then
    "$PY" -c "$CFG; c.set({'webui': {'host': '0.0.0.0'}})"
    echo "A password is strongly suggested for a web UI open to the network."
    "$PY" nomad_server.py --set-password
fi

if [ -z "$(ls -A nodes 2>/dev/null)" ]; then
    read -r -p "Name of your first node (empty = skip): " NAME || true
    [ -n "$NAME" ] && "$PY" nomad_server.py --new-node "$NAME"
fi

echo
read -r -p "Install the systemd service (starts at boot)? [Y/n] " SVC || SVC=n
if [ "$SVC" = n ] || [ "$SVC" = N ]; then
    echo "Start it by hand with: $PY nomad_server.py"
    exit 0
fi

# One service per folder: a name already used by another folder is never replaced silently
NAME=nomad-server
MINE="$(grep -lxF "WorkingDirectory=$DIR" /etc/systemd/system/*.service 2>/dev/null | head -1 || true)"
[ -n "$MINE" ] && NAME="$(basename "$MINE" .service)"
while true; do
    OLD="$(sed -n 's/^WorkingDirectory=//p' "/etc/systemd/system/$NAME.service" 2>/dev/null || true)"
    [ -z "$OLD" ] || [ "$OLD" = "$DIR" ] && break
    echo "The service $NAME already runs nomad-server from $OLD"
    read -r -p "Service name for this folder (e.g. nomad-server-2, $NAME = replace it, empty = no service): " NEW || NEW=
    if [ -z "$NEW" ]; then
        echo "No service installed. Start this one by hand with: $PY nomad_server.py"
        exit 0
    fi
    case "$NEW" in *[!A-Za-z0-9_.-]*) echo "Use only letters, digits and - _ ."; continue ;; esac
    [ "$NEW" = "$NAME" ] && break
    NAME="$NEW"
done
UNIT="/etc/systemd/system/$NAME.service"

AFTER="network-online.target"
PRE=""
if systemctl list-unit-files rnsd.service >/dev/null 2>&1 && systemctl list-unit-files rnsd.service | grep -q rnsd; then
    # Wait for rnsd: its control socket opens last, when transport is ready
    AFTER="$AFTER rnsd.service"
    PRE='ExecStartPre=/bin/bash -c "for i in $$(seq 120); do ss -xlH | grep -q @rns/default/rpc && exit 0; sleep 2; done; exit 0"'
    echo "rnsd.service found: nomad-server will use it."
else
    echo "No rnsd.service: nomad-server runs its own Reticulum instance."
fi

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=nomad-server (NomadNet nodes) in $DIR
After=$AFTER
Wants=$AFTER

[Service]
Type=simple
User=$U
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
$PRE
TimeoutStartSec=5min
ExecStart="$PY" -u "$DIR/nomad_server.py" --service
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$NAME"
sudo systemctl restart "$NAME"
sleep 3
systemctl --no-pager --lines=5 status "$NAME" || true
echo
echo "Done. Log: journalctl -u $NAME -f   State: $PY nomad_server.py --status"
