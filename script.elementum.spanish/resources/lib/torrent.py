"""Bencode parser + magnet builder. Site-agnostic."""
import hashlib
from urllib.parse import quote
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv",
              ".ts", ".m2ts", ".webm"}


def _bencode(o):
    if isinstance(o, int):
        return f"i{o}e".encode()
    if isinstance(o, bytes):
        return f"{len(o)}:".encode() + o
    if isinstance(o, str):
        return _bencode(o.encode())
    if isinstance(o, list):
        return b"l" + b"".join(_bencode(x) for x in o) + b"e"
    if isinstance(o, dict):
        keys = sorted(o.keys())
        return b"d" + b"".join(_bencode(k) + _bencode(o[k]) for k in keys) + b"e"
    raise TypeError(type(o))


def _bdecode(data, i=0):
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c.isdigit():
        colon = data.index(b":", i)
        n = int(data[i:colon])
        start = colon + 1
        return data[start:start + n], start + n
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    raise ValueError(f"bad bencode at {i}: {c!r}")


def _build_magnet(decoded):
    if not isinstance(decoded, dict):
        return None, None
    info_dict = decoded.get(b"info")
    if not isinstance(info_dict, dict):
        return None, None
    info_hash = hashlib.sha1(_bencode(info_dict)).hexdigest()
    name = info_dict.get(b"name", b"")
    name = name.decode("utf-8", "replace") if isinstance(name, bytes) else ""
    trackers = []
    if isinstance(decoded.get(b"announce"), bytes):
        trackers.append(decoded[b"announce"].decode("utf-8", "replace"))
    al = decoded.get(b"announce-list")
    if isinstance(al, list):
        for tier in al:
            if isinstance(tier, list):
                for t in tier:
                    if isinstance(t, bytes):
                        trackers.append(t.decode("utf-8", "replace"))
    parts = [f"magnet:?xt=urn:btih:{info_hash}"]
    if name:
        parts.append("dn=" + quote(name, safe=""))
    seen = set()
    for t in trackers:
        if t in seen:
            continue
        seen.add(t)
        parts.append("tr=" + quote(t, safe=""))
    magnet = "&".join(parts).replace("magnet:?&", "magnet:?", 1)
    return magnet, info_hash


def inspect_torrent(url, headers=None):
    """Download a .torrent and return {magnet, info_hash, size, name, is_rar}."""
    info = {"magnet": None, "info_hash": None, "size": 0, "name": None,
            "is_rar": False}
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=h)
        r.raise_for_status()
        decoded, _ = _bdecode(r.content)
    except Exception:
        return info
    magnet, info_hash = _build_magnet(decoded)
    info["magnet"] = magnet
    info["info_hash"] = info_hash
    if not isinstance(decoded, dict):
        return info
    meta = decoded.get(b"info") or {}
    name = meta.get(b"name")
    if isinstance(name, bytes):
        info["name"] = name.decode("utf-8", "replace")
    files = meta.get(b"files")
    flat = []
    if isinstance(files, list):
        for f in files:
            length = f.get(b"length", 0) or 0
            path_parts = f.get(b"path") or []
            fname = b"/".join(p for p in path_parts if isinstance(p, bytes)).decode("utf-8", "replace")
            flat.append((fname, length))
    elif b"length" in meta:
        flat.append((info["name"] or "", meta.get(b"length", 0) or 0))
    for fname, length in flat:
        info["size"] += length
        lower = fname.lower()
        if lower.endswith(".rar") or any(lower.endswith(f".r{n:02d}") for n in range(100)):
            info["is_rar"] = True
    return info


def magnet_from_info_hash(info_hash, name=None, trackers=None):
    parts = [f"magnet:?xt=urn:btih:{info_hash}"]
    if name:
        parts.append("dn=" + quote(name, safe=""))
    for t in (trackers or []):
        parts.append("tr=" + quote(t, safe=""))
    return "&".join(parts).replace("magnet:?&", "magnet:?", 1)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
