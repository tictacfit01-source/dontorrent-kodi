"""Quality / source / codec / language tag parsing.

Extracts the kind of info Elementum's own ranking expects to see in the
result name. The richer the parse, the better Elementum sorts our results
above (or below) Burst's noise.
"""
import re

_RESOLUTION = [
    (re.compile(r"\b2160p\b", re.I), "2160p"),
    (re.compile(r"\b4k\b",    re.I), "2160p"),
    (re.compile(r"\buhd\b",   re.I), "2160p"),
    (re.compile(r"\b1080p\b", re.I), "1080p"),
    (re.compile(r"\bfullhd\b",re.I), "1080p"),
    (re.compile(r"\b720p\b",  re.I), "720p"),
    (re.compile(r"\bhd\b",    re.I), "720p"),
    (re.compile(r"\b480p\b",  re.I), "480p"),
    (re.compile(r"\bsd\b",    re.I), "480p"),
]

_SOURCE = [
    (re.compile(r"\bbdremux\b",         re.I), "BDRemux"),
    (re.compile(r"\bremux\b",           re.I), "Remux"),
    (re.compile(r"\bbluray\b|\bblu-ray\b|\bbdrip\b", re.I), "BluRay"),
    (re.compile(r"\bweb[\.\-]?dl\b",    re.I), "WEB-DL"),
    (re.compile(r"\bwebrip\b",          re.I), "WEBRip"),
    (re.compile(r"\bhdtv\b",            re.I), "HDTV"),
    (re.compile(r"\bhdrip\b",           re.I), "HDRip"),
    (re.compile(r"\bdvdrip\b",          re.I), "DVDRip"),
    (re.compile(r"\bmicrohd\b",         re.I), "microHD"),
]

_CODEC = [
    (re.compile(r"\bx265\b|\bh\.?265\b|\bhevc\b", re.I), "x265"),
    (re.compile(r"\bx264\b|\bh\.?264\b|\bavc\b",  re.I), "x264"),
    (re.compile(r"\bav1\b",                       re.I), "AV1"),
    (re.compile(r"\bxvid\b",                      re.I), "XviD"),
]

_LANG = [
    (re.compile(r"\bcastellano\b|\bcast\b|\besp(?:a[nñ]ol)?\b", re.I), "CAST"),
    (re.compile(r"\blatino\b|\blat\b",                                re.I), "LAT"),
    (re.compile(r"\bdual\b",                                          re.I), "DUAL"),
    (re.compile(r"\bvose\b|\bvosee?\b",                               re.I), "VOSE"),
]


def parse(text):
    """Return dict {resolution, source, codec, language} for the strings
    we can detect. Missing fields come back as empty string."""
    if not text:
        return {"resolution": "", "source": "", "codec": "", "language": ""}
    out = {"resolution": "", "source": "", "codec": "", "language": ""}
    for pat, tag in _RESOLUTION:
        if pat.search(text):
            out["resolution"] = tag
            break
    for pat, tag in _SOURCE:
        if pat.search(text):
            out["source"] = tag
            break
    for pat, tag in _CODEC:
        if pat.search(text):
            out["codec"] = tag
            break
    for pat, tag in _LANG:
        if pat.search(text):
            out["language"] = tag
            break
    return out


def format_label(provider, title, q, size_str=""):
    """Produce a consistent label that Elementum can also re-parse:
    `[Provider] Title · 1080p BDRemux x265 · CAST · 8.4 GB`
    """
    bits = []
    quality_bits = [b for b in (q.get("resolution"), q.get("source"), q.get("codec")) if b]
    if quality_bits:
        bits.append(" ".join(quality_bits))
    if q.get("language"):
        bits.append(q["language"])
    if size_str:
        bits.append(size_str)
    suffix = (" \u00b7 " + " \u00b7 ".join(bits)) if bits else ""
    return f"[{provider}] {title}{suffix}"


def merge(*texts):
    """Combine info parsed from several strings, preferring earlier hits."""
    merged = {"resolution": "", "source": "", "codec": "", "language": ""}
    for t in texts:
        q = parse(t)
        for k, v in q.items():
            if v and not merged[k]:
                merged[k] = v
    return merged
