"""Build a single addon zip in the project root, ready to install in Kodi.

Usage:
    python tools/make_local_zip.py                # builds script.elementum.spanish
    python tools/make_local_zip.py plugin.video.dontorrent
"""
import os
import sys
import zipfile
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDE_NAMES = {".git", ".github", "__pycache__", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo")


def _skip(name):
    return name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES)


def build(addon_dir_name):
    addon_dir = os.path.join(ROOT, addon_dir_name)
    xml = ET.parse(os.path.join(addon_dir, "addon.xml")).getroot()
    addon_id = xml.attrib["id"]
    version = xml.attrib["version"]
    out = os.path.join(ROOT, f"{addon_id}-{version}.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for dp, dirs, files in os.walk(addon_dir):
            dirs[:] = [d for d in dirs if not _skip(d)]
            for f in files:
                if _skip(f):
                    continue
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, addon_dir)
                arc = os.path.join(addon_id, rel).replace(os.sep, "/")
                zf.write(full, arc)
    print(f"Built: {out}")
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "script.elementum.spanish"
    build(target)
