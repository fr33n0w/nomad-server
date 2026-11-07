"""Node backup (zip with identity, pages, files, settings and counters),
restore into a new node, identity export and import. The identity file is
the node's private key: two nodes can never share one."""
import io
import os
import shutil
import time
import zipfile

import RNS

from . import log

MAX_ZIP = 256 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024
SKIP_DIRS = {".trash", "__pycache__", "media_cache"}


def identity_from_bytes(data):
    ident = RNS.Identity.from_bytes(bytes(data)) if data and len(data) == 64 else None
    if ident is None:
        raise ValueError("not a Reticulum identity file (64 bytes)")
    return ident


def node_address(ident):
    return RNS.hexrep(RNS.Destination.hash_from_name_and_identity("nomadnetwork.node", ident), delimit=False)


def check_unused(server, ident, except_id=None):
    """Refuses an identity that another node of this server already has."""
    for node in server.nodes.values():
        if node.id == except_id:
            continue
        try:
            other = RNS.Identity.from_file(os.path.join(node.folder, "identity"))
        except Exception:
            continue
        if other is not None and other.hash == ident.hash:
            raise ValueError("node %s already uses this identity: two nodes cannot share one" % node.id)


def make_zip(node):
    node.stats.flush(force=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cur, dirs, names in os.walk(node.folder):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for n in sorted(names):
                if ".tmp" in n:
                    continue
                path = os.path.join(cur, n)
                rel = os.path.relpath(path, node.folder).replace(os.sep, "/")
                z.write(path, node.id + "/" + rel)
        z.writestr(node.id + "/BACKUP.txt",
                   "nomad-server backup of node %s (%s), address %s, made %s.\n"
                   "The identity file is the node's private key: keep this zip private.\n"
                   "Restore it with Overview > Restore from nS backup.\n"
                   % (node.id, node.name, node.address, time.strftime("%Y-%m-%d %H:%M")))
    return buf.getvalue()


def restore(server, data):
    if len(data) > MAX_ZIP:
        raise ValueError("backup too big (256 MB max)")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a zip file")
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if not infos or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise ValueError("empty or too big once unpacked")
        names = [i.filename.replace("\\", "/") for i in infos]
        # backups have one top folder (the node id): strip it
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        rels = []
        for n in names:
            rel = n.split("/", 1)[1] if strip else n
            parts = rel.split("/")
            # ":" anywhere: on Windows "a/C:b" would jump to another drive
            if rel.startswith("/") or any(p in ("", ".", "..") or ":" in p for p in parts):
                raise ValueError("unsafe path in the zip: %s" % n)
            rels.append(rel)
        if "node.cfg" not in rels or "identity" not in rels:
            raise ValueError("this zip is not a node backup (node.cfg and identity missing)")
        ident = identity_from_bytes(z.read(infos[rels.index("identity")]))
        check_unused(server, ident)
        cfg_text = z.read(infos[rels.index("node.cfg")]).decode("utf-8", "replace")
        name = next((l.split("=", 1)[1].strip() for l in cfg_text.splitlines()
                     if l.strip().startswith("name") and "=" in l), "") or (tops.pop() if strip else "restored")
        folder = server.free_folder(name)
        try:
            for info, rel in zip(infos, rels):
                if rel == "BACKUP.txt":
                    continue
                dest = os.path.normpath(os.path.join(folder, *rel.split("/")))
                if not dest.startswith(os.path.normpath(folder) + os.sep):
                    raise ValueError("unsafe path in the zip: %s" % rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    for sub in ("pages", "files"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
    node = server.add_node(folder)
    log.line("Node %s restored from a backup, address %s" % (node.id, node_address(ident)))
    return node


def set_identity(server, node, data):
    ident = identity_from_bytes(data)
    check_unused(server, ident, except_id=node.id)
    was_running = node.state == "running"
    node.stop()
    path = os.path.join(node.folder, "identity")
    if os.path.isfile(path):
        trash = os.path.join(node.folder, ".trash")
        os.makedirs(trash, exist_ok=True)
        shutil.move(path, os.path.join(trash, "identity-%s" % time.strftime("%Y%m%d-%H%M%S")))
    with open(path, "wb") as f:
        f.write(bytes(data))
    node.identity = None
    node.log("Identity replaced from the web UI: new address %s (the old one is in .trash)" % node_address(ident), "warn")
    if was_running:
        node.start()
    return node_address(ident)


def read_identity(node):
    with open(os.path.join(node.folder, "identity"), "rb") as f:
        return f.read()

