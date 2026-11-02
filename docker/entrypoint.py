"""Container start: settings that only make sense in Docker, then the server.

  NOMAD_PASSWORD    web UI password (empty = keep the one in server.cfg)
  NOMAD_RNS_CONFIG  Reticulum config folder (default /data/reticulum: own instance)
  NOMAD_PORT        web UI port inside the container (default 8484)
Extra arguments go to nomad_server.py (e.g. --verbose).
"""
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
from nserver import config  # noqa: E402

BASE = os.environ.get("NOMAD_BASE") or "/data"

cfg = config.Config(os.path.join(BASE, "server.cfg"), config.SERVER_DEFAULTS, config.SERVER_TEMPLATE)
values = {"webui": {"host": "0.0.0.0", "port": os.environ.get("NOMAD_PORT") or "8484"},
          "server": {"rns_config": os.environ.get("NOMAD_RNS_CONFIG") or os.path.join(BASE, "reticulum")}}
if os.environ.get("NOMAD_PASSWORD"):
    values["webui"]["password"] = config.hash_password(os.environ["NOMAD_PASSWORD"])
cfg.set(values)
if not cfg.get("webui", "password"):
    print("No web UI password: anyone reaching port %s can manage the server. Set NOMAD_PASSWORD." % values["webui"]["port"])

script = os.path.join(APP, "nomad_server.py")
os.execv(sys.executable, [sys.executable, script, "--base", BASE, "--service"] + sys.argv[1:])
