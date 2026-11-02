#!/bin/bash
# nomad-server installer for Debian/Ubuntu/Raspberry Pi OS (systemd).
# Run it from the nomad-server folder:  bash install.sh
set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
U="$(id -un)"
PY="$(command -v python3 || true)"

[ -n "$PY" ] || { echo "python3 not found: sudo apt install python3 python3-pip"; exit 1; }

echo "== Reticulum (rns)"
if ! "$PY" -c "import RNS" 2>/dev/null; then
    "$PY" -m pip install --user rns --break-system-packages || "$PY" -m pip install --user rns
fi
"$PY" -c "import RNS; print('rns', RNS.__version__ if hasattr(RNS, '__version__') else 'ok')"

[ -f server.cfg ] || "$PY" -c "import sys; sys.path.insert(0, '.'); from nserver import config; config.Config('server.cfg', config.SERVER_DEFAULTS, config.SERVER_TEMPLATE)"

echo
read -r -p "Open the web UI to the local network (0.0.0.0)? [y/N] " OPEN || true
if [ "$OPEN" = y ] || [ "$OPEN" = Y ]; then
    "$PY" -c "import sys; sys.path.insert(0, '.'); from nserver import config; c = config.Config('server.cfg', config.SERVER_DEFAULTS); c.set({'webui': {'host': '0.0.0.0'}})"
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

sudo tee /etc/systemd/system/nomad-server.service >/dev/null <<EOF
[Unit]
Description=nomad-server (NomadNet nodes)
After=$AFTER
Wants=$AFTER

[Service]
Type=simple
User=$U
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
$PRE
TimeoutStartSec=5min
ExecStart=$PY -u $DIR/nomad_server.py --service
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now nomad-server
sleep 3
systemctl --no-pager --lines=5 status nomad-server || true
echo
echo "Done. Log: journalctl -u nomad-server -f   State: $PY nomad_server.py --status"
