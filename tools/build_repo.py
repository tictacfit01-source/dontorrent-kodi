"""Build the Kodi repository tree (repo/) from the addon directories.

Layout produced:
    repo/
        addons.xml
        addons.xml.md5
        <addon_id>/
            <addon_id>-<version>.zip
            addon.xml          (copy, optional but Kodi tolerates it)
            icon.png / fanart.jpg (if present, for nicer browsing)

Run from the project root:
    python tools/build_repo.py
"""
import hashlib
import os
import shutil
import sys
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.join(ROOT, "repo")

# Folders that look like addon sources (must contain addon.xml at the root).
ADDON_DIRS = [
    "plugin.video.dontorrent",
    "repository.dontorrent",
]

# Files/dirs that should never be packaged inside the addon zip.
EXCLUDE_NAMES = {".git", ".github", "__pycache__", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def _should_skip(name):
    if name in EXCLUDE_NAMES:
        return True
    for suf in EXCLUDE_SUFFIXES:
        if name.endswith(suf):
            return True
    return False


def _read_addon_xml(addon_dir):
    path = os.path.join(addon_dir, "addon.xml")
    if not os.path.isfile(path):
        raise SystemExit(f"missing addon.xml in {addon_dir}")
    tree = ET.parse(path)
    root = tree.getroot()
    return root, tree


def _zip_addon(addon_dir, addon_id, version, dest_dir):
    """Create <addon_id>-<version>.zip with addon files at the top level
    inside a folder named <addon_id>/ (Kodi requirement)."""
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"{addon_id}-{version}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.basename(addon_dir.rstrip(os.sep))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(addon_dir):
            # filter directories in-place so os.walk skips them
            dirnames[:] = [d for d in dirnames if not _should_skip(d)]
            for fname in filenames:
                if _should_skip(fname):
                    continue
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, addon_dir)
                arc = os.path.join(addon_id, rel).replace(os.sep, "/")
                zf.write(full, arc)
    return zip_path


def _copy_assets(addon_dir, dest_dir):
    for name in ("icon.png", "fanart.jpg", "addon.xml"):
        src = os.path.join(addon_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_dir, name))


def main():
    if os.path.isdir(REPO_DIR):
        # Wipe so removed addons / old versions don't linger.
        shutil.rmtree(REPO_DIR)
    os.makedirs(REPO_DIR, exist_ok=True)

    combined = ET.Element("addons")
    summary = []

    for rel in ADDON_DIRS:
        addon_dir = os.path.join(ROOT, rel)
        if not os.path.isdir(addon_dir):
            print(f"skip (not found): {rel}")
            continue
        root, _tree = _read_addon_xml(addon_dir)
        addon_id = root.attrib["id"]
        version = root.attrib["version"]

        dest_dir = os.path.join(REPO_DIR, addon_id)
        zip_path = _zip_addon(addon_dir, addon_id, version, dest_dir)
        _copy_assets(addon_dir, dest_dir)

        combined.append(root)
        summary.append((addon_id, version, os.path.relpath(zip_path, ROOT)))

    # Pretty-ish XML; Kodi doesn't care about whitespace but humans do.
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    xml_bytes += ET.tostring(combined, encoding="utf-8")
    addons_xml = os.path.join(REPO_DIR, "addons.xml")
    with open(addons_xml, "wb") as f:
        f.write(xml_bytes)

    md5 = hashlib.md5(xml_bytes).hexdigest()
    with open(os.path.join(REPO_DIR, "addons.xml.md5"), "w", encoding="utf-8") as f:
        f.write(md5 + "\n")

    print("Built repo/:")
    for addon_id, version, path in summary:
        print(f"  - {addon_id} {version}  ->  {path}")
    print(f"  addons.xml md5 = {md5}")


if __name__ == "__main__":
    sys.exit(main() or 0)
