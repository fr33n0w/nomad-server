"""Honeypot node: a believable archive with many linked pages and trap links
that people never see or follow, but crawlers always do. Whoever opens a trap
is blocked (identified visitors) or tarpitted for the rest of its link."""
import os
import random

SECTIONS = [
    ("guides", "Guides", "Setting up nodes, radios and interfaces"),
    ("logs", "Network logs", "Notes from the mesh, collected over time"),
    ("maps", "Maps", "Where nodes were heard"),
    ("files", "Shared files", "Configs and scripts from the community"),
    ("people", "Contacts", "Operators and how to reach them"),
    ("archive", "Old archive", "Pages kept from earlier versions"),
]
WORDS = ("relay mesh antenna lora transport beacon gateway router node packet link announce path "
         "battery solar repeater channel frequency config backup mirror index notes draft").split()
TRAPS = ["full-index.mu", "all-pages.mu", "export.mu", "sitemap.mu", "raw-dump.mu"]
# a link people do not see: dark on dark, a single dot
HIDDEN = "`F111`[.`:/page/trap/%s]`f"


def _title(rng):
    return " ".join(rng.choice(WORDS) for _ in range(rng.randint(2, 4))).capitalize()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build(pages_dir, name, items=10, seed=None):
    """Writes the pages of a honeypot node. Returns the trap pattern."""
    rng = random.Random(seed)
    trap = lambda: HIDDEN % rng.choice(TRAPS)
    index = [">%s" % name, "", "An archive of notes, guides and files from the mesh.", ""]
    for slug, title, desc in SECTIONS:
        index.append("`[%s`:/page/%s/index.mu]  `F888%s`f" % (title, slug, desc))
    index += ["", trap(), ""]
    _write(os.path.join(pages_dir, "index.mu"), "\n".join(index) + "\n")
    for slug, title, desc in SECTIONS:
        lines = [">%s" % title, "", desc, "", "`[Back`:/page/index.mu]", ""]
        for i in range(1, items + 1):
            t = _title(rng)
            lines.append("`[%s`:/page/%s/%d.mu]" % (t, slug, i))
            body = [">%s" % t, "", "`[%s`:/page/%s/index.mu]" % (title, slug), ""]
            body += [" ".join(rng.choice(WORDS) for _ in range(rng.randint(10, 18))).capitalize() + "."
                     for _ in range(rng.randint(2, 4))]
            nxt = i + 1 if i < items else 1
            body += ["", "`[Next`:/page/%s/%d.mu]" % (slug, nxt), "", trap(), ""]
            _write(os.path.join(pages_dir, slug, "%d.mu" % i), "\n".join(body) + "\n")
        lines += ["", trap(), ""]
        _write(os.path.join(pages_dir, slug, "index.mu"), "\n".join(lines) + "\n")
    for t in TRAPS:
        # trap pages look like more archive, they lead to more traps
        links = "\n".join("`[%s`:/page/trap/%s]" % (_title(rng), x) for x in TRAPS if x != t)
        _write(os.path.join(pages_dir, "trap", t), ">Index\n\n%s\n" % links)
    return "/page/trap/*"


# bait files: text of a few sizes and two images, to see what crawlers download
WARNING = ("Your crawler opened a honeypot page on this node. You have been identified and listed as a crawler. "
           "The list is shared on the network and between public servers. "
           "You can generate other identities, but you will be caught again.\n")
TEXT_FILES = [("node-list.txt", 1), ("mesh-config-backup.txt", 8), ("contacts-export.txt", 32), ("full-archive.txt", 100)]
IMAGES = ["coverage-map.webp", "antenna-setup.webp"]


def build_files(files_dir, pages_dir):
    """Writes the bait files and a Downloads page linking them, next to the archive."""
    import shutil
    os.makedirs(files_dir, exist_ok=True)
    for name, kb in TEXT_FILES:
        with open(os.path.join(files_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write((WARNING * (kb * 1024 // len(WARNING) + 1))[:kb * 1024])
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bait")
    for name in IMAGES:
        shutil.copyfile(os.path.join(here, name), os.path.join(files_dir, name))
    lines = [">Downloads", "", "Files shared by the community.", "", "`[Back`:/page/index.mu]", ""]
    for name in [n for n, _ in TEXT_FILES] + IMAGES:
        kb = os.path.getsize(os.path.join(files_dir, name)) // 1024 or 1
        lines.append("`[%s`:/file/%s]  `F888%d KB`f" % (name, name, kb))
    lines += ["", HIDDEN % "export.mu", ""]
    _write(os.path.join(pages_dir, "downloads.mu"), "\n".join(lines) + "\n")
    # linked from the home page and from the Shared files section
    for page in ("index.mu", os.path.join("files", "index.mu")):
        path = os.path.join(pages_dir, page)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if "/page/downloads.mu" not in text:
                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    f.write("`[Downloads`:/page/downloads.mu]  `F888Files shared by the community`f\n")
