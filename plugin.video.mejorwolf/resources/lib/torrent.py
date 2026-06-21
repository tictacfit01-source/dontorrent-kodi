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
import re
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


def info_hash_hex(data):
    """info_hash (SHA1 del dict `info` bencodeado) en hex, o '' si los bytes no
    son un .torrent valido. Lo usa el box para que el relay (IP de Render, que no
    puede con el PoW de descarga de DonTorrent) pueda hacer el scrape UDP de
    seeders con el hash que el box deriva desde su IP residencial."""
    if not data or data[:1] != b"d":
        return ""
    try:
        info = bdecode(data).get(b"info")
        if not isinstance(info, dict):
            return ""
        return hashlib.sha1(bencode(info)).hexdigest()
    except Exception:
        return ""


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


# ---------------------------------------------------------------- packed (RAR)

_VIDEO_EXT = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".mpg",
              ".mpeg", ".wmv", ".flv", ".webm")
# .rar, .r00-.r999, .partNN.rar, .zip, .7z, .001 (split)
_PACK_RE = re.compile(r"\.(?:rar|r\d{2,3}|part\d+\.rar|zip|7z|001)$", re.I)


def list_files(data):
    """Lista de (nombre, tamaño) de los ficheros DENTRO del .torrent
    (o [(name, length)] si es single-file)."""
    try:
        meta = bdecode(data)
    except Exception:
        return []
    info = meta.get(b"info") if isinstance(meta, dict) else None
    if not isinstance(info, dict):
        return []
    out = []
    files = info.get(b"files")
    if isinstance(files, list):
        for f in files:
            if not isinstance(f, dict):
                continue
            path = f.get(b"path")
            length = f.get(b"length")
            if isinstance(path, list) and path:
                seg = path[-1]
                if isinstance(seg, (bytes, bytearray)):
                    out.append((bytes(seg).decode("utf-8", "replace"),
                                int(length) if isinstance(length, int) else 0))
    else:
        name = info.get(b"name")
        length = info.get(b"length")
        if isinstance(name, (bytes, bytearray)):
            out.append((bytes(name).decode("utf-8", "replace"),
                        int(length) if isinstance(length, int) else 0))
    return out


def is_packed(data):
    """True si el .torrent trae el video EMPAQUETADO (RAR/zip/7z) y por tanto
    Elementum no podra reproducirlo en streaming. Heuristica: hay ficheros de
    archivo comprimido y NINGUN video reproducible REAL suelto (se ignoran las
    'muestras'/'sample' y los videos diminutos < 50 MB). Sin red: solo lee los
    bytes del .torrent que ya tenemos."""
    files = list_files(data)
    if not files:
        return False
    has_pack = any(_PACK_RE.search(n) for n, _ in files)
    if not has_pack:
        return False

    def _real_video(name, size):
        low = name.lower()
        if not low.endswith(_VIDEO_EXT):
            return False
        if "sample" in low or "muestra" in low:
            return False
        # tamaño 0 = desconocido (no arriesgamos: cuenta como video real);
        # si se conoce, exigimos > 50 MB para no confundir con una muestra.
        return size == 0 or size > 50 * 1024 * 1024

    has_real_video = any(_real_video(n, s) for n, s in files)
    return not has_real_video
