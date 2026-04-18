import hashlib
import json
from urllib.parse import quote
import requests
from . import domain

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20
DIFFICULTY = 3  # leading hex zeros required by the site

API = "/api_validate_pow.php"


def _proof_of_work(challenge):
    target = "0" * DIFFICULTY
    nonce = 0
    while True:
        h = hashlib.sha256((challenge + str(nonce)).encode()).hexdigest()
        if h.startswith(target):
            return nonce
        nonce += 1


def resolve_torrent(content_id, tabla, page_url=None):
    """Run the site's PoW handshake to obtain the actual .torrent URL.

    Returns an absolute https URL to the .torrent file, or raises on error.
    """
    base = domain.base_url()
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json,*/*;q=0.8",
        "Origin": base,
        "Referer": page_url or (base + "/"),
    }

    # 1) generate challenge
    r = requests.post(
        base + API,
        data=json.dumps({
            "action": "generate",
            "content_id": int(content_id),
            "tabla": tabla,
        }),
        headers=headers,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    res = r.json()
    if not res.get("success"):
        raise RuntimeError(res.get("error") or "PoW: no challenge")
    challenge = res["challenge"]

    # 2) compute PoW
    nonce = _proof_of_work(challenge)

    # 3) validate and obtain download URL
    r = requests.post(
        base + API,
        data=json.dumps({
            "action": "validate",
            "challenge": challenge,
            "nonce": nonce,
        }),
        headers=headers,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    res = r.json()
    if res.get("status") == "captcha_required":
        raise RuntimeError("DonTorrent pide captcha (limite alcanzado). Espera unos minutos.")
    if not res.get("success") or not res.get("download_url"):
        raise RuntimeError(res.get("error") or "PoW: validacion fallida")

    url = res["download_url"]
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = base + url
    return url


# --------------------------------------------------------------------------
# Bencode parser - minimal, just enough to read the file list of a .torrent.
# --------------------------------------------------------------------------

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


# Common video extensions Elementum can stream directly without unpacking.
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".ts", ".m2ts", ".webm"}


def _build_magnet(decoded):
    """Build a magnet URI from a parsed .torrent dict, byte-perfect on the
    info hash (SHA1 over the re-encoded info dict, which is identical to
    the original because bencode dicts are sorted by key)."""
    if not isinstance(decoded, dict):
        return None
    info_dict = decoded.get(b"info")
    if not isinstance(info_dict, dict):
        return None
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
    return "&".join(parts).replace("magnet:?&", "magnet:?", 1)


def inspect_torrent(url):
    """Download the .torrent and report whether it ships RAR archives, plus
    the largest video file (if any) and the total size. We use this to warn
    the user clearly before invoking Elementum, and to convert to magnet
    (which Elementum handles more reliably than https .torrent URLs)."""
    info = {
        "is_rar": False,
        "rar_parts": 0,
        "total_size": 0,
        "video_size": 0,
        "video_name": None,
        "name": None,
        "files": 0,
        "magnet": None,
    }
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        r.raise_for_status()
        decoded, _ = _bdecode(r.content)
    except Exception:
        return info

    if not isinstance(decoded, dict):
        return info
    info["magnet"] = _build_magnet(decoded)
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

    info["files"] = len(flat)
    for fname, length in flat:
        info["total_size"] += length
        lower = fname.lower()
        if lower.endswith(".rar") or any(lower.endswith(f".r{n:02d}") for n in range(100)):
            info["is_rar"] = True
            info["rar_parts"] += 1
        else:
            for ext in VIDEO_EXTS:
                if lower.endswith(ext) and length > info["video_size"]:
                    info["video_size"] = length
                    info["video_name"] = fname
                    break
    return info


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
