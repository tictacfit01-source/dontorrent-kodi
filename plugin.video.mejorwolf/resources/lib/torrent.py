"""Tiny bencode parser + .torrent -> magnet converter.

Why we need this:
    Elementum's `?uri=` parameter does NOT reliably accept file:// URIs on
    all platforms, and on the user's network http(s):// URIs to MejorTorrent
    are blocked at the ISP level (Elementum uses its own libtorrent fetcher,
    not our worker proxy). Magnet URIs sidestep both: libtorrent can find
    peers via DHT/trackers using only the info_hash and start streaming.

Flow:
    .torrent bytes  -> bdecode  -> bencode(info)  -> sha1 = info_hash
                    -> trackers from "announce" / "announce-list"
                    -> name from info["name"]
                    -> "magnet:?xt=urn:btih:<hex>&dn=...&tr=..."
"""

import hashlib
from urllib.parse import quote


# ---------------------------------------------------------------- bencode

class BencodeError(ValueError):
    pass


def bdecode(data):
    """Decode a bencoded bytes object. Returns the parsed structure."""
    if not isinstance(data, (bytes, bytearray)):
        raise BencodeError("bdecode expects bytes")
    val, pos = _decode(bytes(data), 0)
    return val


def _decode(data, pos):
    if pos >= len(data):
        raise BencodeError("unexpected EOF")
    c = data[pos:pos + 1]
    if c == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if c == b"l":
        pos += 1
        out = []
        while data[pos:pos + 1] != b"e":
            v, pos = _decode(data, pos)
            out.append(v)
        return out, pos + 1
    if c == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            k, pos = _decode(data, pos)
            v, pos = _decode(data, pos)
            out[k] = v
        return out, pos + 1
    if c.isdigit():
        colon = data.index(b":", pos)
        n = int(data[pos:colon])
        start = colon + 1
        return data[start:start + n], start + n
    raise BencodeError(f"bad char {c!r} at {pos}")


def bencode(obj):
    """Encode a structure (dict / list / int / bytes / str) to bencode bytes."""
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    if isinstance(obj, (bytes, bytearray)):
        b = bytes(obj)
        return str(len(b)).encode() + b":" + b
    if isinstance(obj, str):
        b = obj.encode("utf-8")
        return str(len(b)).encode() + b":" + b
    if isinstance(obj, list):
        return b"l" + b"".join(bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        # Bencode dicts MUST be sorted by raw key bytes.
        items = sorted(
            ((k if isinstance(k, (bytes, bytearray)) else str(k).encode()), v)
            for k, v in obj.items()
        )
        out = b"d"
        for k, v in items:
            out += bencode(k) + bencode(v)
        return out + b"e"
    raise BencodeError(f"can't encode {type(obj).__name__}")


# ----------------------------------------------------- .torrent -> magnet

def torrent_to_magnet(data):
    """Build a magnet URI from raw .torrent bytes.

    Returns None if `data` doesn't look like a valid bencoded torrent.
    """
    if not data or data[:1] != b"d":
        return None
    try:
        meta = bdecode(data)
    except Exception:
        return None
    info = meta.get(b"info")
    if not isinstance(info, dict):
        return None
    info_hash = hashlib.sha1(bencode(info)).hexdigest()

    name_b = info.get(b"name") or b""
    if isinstance(name_b, (bytes, bytearray)):
        try:
            name = bytes(name_b).decode("utf-8")
        except UnicodeDecodeError:
            name = bytes(name_b).decode("latin-1", "replace")
    else:
        name = str(name_b)

    trackers = []
    a = meta.get(b"announce")
    if isinstance(a, (bytes, bytearray)):
        trackers.append(bytes(a).decode("utf-8", "replace"))
    al = meta.get(b"announce-list")
    if isinstance(al, list):
        for tier in al:
            if isinstance(tier, list):
                for tr in tier:
                    if isinstance(tr, (bytes, bytearray)):
                        trackers.append(bytes(tr).decode("utf-8", "replace"))

    parts = [f"magnet:?xt=urn:btih:{info_hash}"]
    if name:
        parts.append("dn=" + quote(name))
    seen = set()
    for tr in trackers:
        if tr and tr not in seen:
            seen.add(tr)
            parts.append("tr=" + quote(tr, safe=""))
    return parts[0] + ("&" + "&".join(parts[1:]) if len(parts) > 1 else "")
