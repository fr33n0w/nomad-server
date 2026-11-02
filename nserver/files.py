"""Page manager: list, read, write, upload, rename and delete inside a node's
pages/ and files/. Every path is resolved inside those two folders; deleted
items go to the node's .trash/ folder, never removed."""
import os
import shutil
import sys
import time

ROOTS = ("pages", "files")
MAX_TEXT = 2 * 1024 * 1024
MAX_UPLOAD = 64 * 1024 * 1024
TEXT_EXT = {".mu", ".txt", ".md", ".py", ".sh", ".json", ".csv", ".allowed", ".html", ".css", ".js",
            ".cfg", ".ini", ".xml", ".svg", ".log", ".conf", ".toml", ".yaml", ".yml"}


def resolve(node, root, rel, must_exist=False):
    if root not in ROOTS:
        raise ValueError("unknown folder")
    base = os.path.normpath(os.path.abspath(os.path.join(node.folder, root)))
    rel = str(rel or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") or p.startswith(".") for p in parts):
        raise ValueError("names cannot start with a dot")
    path = os.path.normpath(os.path.join(base, *parts)) if parts else base
    if path != base and not path.startswith(base + os.sep):
        raise ValueError("path outside the node folder")
    if must_exist and not os.path.exists(path):
        raise ValueError("not found")
    return path


def tree(node):
    out = []
    for root in ROOTS:
        base = resolve(node, root, "")
        os.makedirs(base, exist_ok=True)
        for cur, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            rel_dir = os.path.relpath(cur, base).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            for d in dirs:
                out.append({"root": root, "path": (rel_dir + "/" + d).strip("/"), "dir": True})
            for n in sorted(names):
                if n.startswith("."):
                    continue
                p = os.path.join(cur, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append({"root": root, "path": (rel_dir + "/" + n).strip("/"), "dir": False,
                            "size": st.st_size, "mtime": st.st_mtime, "text": is_text_name(n)})
    return out


def is_text_name(name):
    return os.path.splitext(name)[1].lower() in TEXT_EXT


def read(node, root, rel):
    path = resolve(node, root, rel, must_exist=True)
    if os.path.isdir(path):
        raise ValueError("this is a folder")
    size = os.path.getsize(path)
    info = {"root": root, "path": rel, "size": size, "mtime": os.path.getmtime(path)}
    if size <= MAX_TEXT:
        with open(path, "rb") as f:
            raw = f.read()
        if b"\0" not in raw[:8192]:
            try:
                info["text"] = raw.decode("utf-8")
                info["crlf"] = b"\r\n" in raw
                return info
            except UnicodeDecodeError:
                pass
    info["binary"] = True
    return info


def write_text(node, root, rel, text, mtime=None):
    """Saves a text file with \\n line ends. mtime: the version the editor
    loaded; if the file changed on disk since then, nothing is written."""
    path = resolve(node, root, rel)
    if not os.path.basename(path) or path == resolve(node, root, ""):
        raise ValueError("missing file name")
    if os.path.isdir(path):
        raise ValueError("a folder has this name")
    if mtime is not None and os.path.isfile(path) and abs(os.path.getmtime(path) - float(mtime)) > 0.001:
        raise ValueError("changed on disk since you opened it: reload it first")
    data = str(text).replace("\r\n", "\n").encode("utf-8")
    if len(data) > MAX_TEXT:
        raise ValueError("too big for the editor (2 MB)")
    _write(path, data)
    return os.path.getmtime(path)


def upload(node, root, rel, data):
    path = resolve(node, root, rel)
    if os.path.isdir(path):
        raise ValueError("a folder has this name")
    if len(data) > MAX_UPLOAD:
        raise ValueError("too big (64 MB max)")
    _write(path, data)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existed = os.path.exists(path)
    mode = os.stat(path).st_mode if existed else None
    tmp = path + ".tmp-upload"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    if sys.platform.startswith("win"):
        return
    if mode is not None:
        os.chmod(path, mode & 0o7777)
    elif data.startswith(b"#!"):
        # a new script page runs like in NomadNet
        os.chmod(path, 0o755)


def mkdir(node, root, rel):
    path = resolve(node, root, rel)
    if os.path.exists(path):
        raise ValueError("already exists")
    os.makedirs(path)


def rename(node, root, rel, new_root, new_rel):
    src = resolve(node, root, rel, must_exist=True)
    dst = resolve(node, new_root, new_rel)
    if src in (resolve(node, root, ""),):
        raise ValueError("cannot move the main folder")
    if os.path.exists(dst):
        raise ValueError("something with that name already exists")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)


def delete(node, root, rel):
    """Moves to <node>/.trash/<time>-<root>-<name>: nothing is lost."""
    src = resolve(node, root, rel, must_exist=True)
    if src == resolve(node, root, ""):
        raise ValueError("cannot delete the main folder")
    trash = os.path.join(node.folder, ".trash")
    os.makedirs(trash, exist_ok=True)
    dst = os.path.join(trash, "%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), root, rel.replace("/", "_")))
    shutil.move(src, dst)
    return dst
