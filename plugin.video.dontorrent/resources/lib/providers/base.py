"""Common interface every provider implements.

A provider exposes a single `search(query, kind)` method that returns a list
of `Result` objects. Each Result already contains a magnet URI ready for
Elementum (no further resolution needed at the addon layer).
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Result:
    name: str                       # Display label, includes quality if known
    uri: str                        # magnet:?xt=urn:btih:...
    info_hash: Optional[str] = None
    size: int = 0                   # bytes; Elementum will format
    seeds: int = 0
    peers: int = 0
    language: str = "es"            # always Spanish in this addon
    provider: str = ""
    icon: str = ""
    resolution: str = ""            # "2160p", "1080p", "720p", "480p", ""
    source: str = ""                # "BDRemux", "BluRay", "WEB-DL", ...
    codec: str = ""                 # "x265", "x264", ...
    audio_lang: str = "CAST"        # "CAST", "LAT", "DUAL", "VOSE"
    is_rar: bool = False            # filtered out by addon for streaming

    def to_elementum(self) -> dict:
        d = asdict(self)
        d["size"] = _human_size(self.size) if self.size else ""
        return d

    def quality_score(self) -> int:
        """Higher = better. Used to sort results before sending to Elementum."""
        res_score = {"2160p": 400, "1080p": 300, "720p": 200, "480p": 100}.get(self.resolution, 50)
        src_score = {"BDRemux": 50, "Remux": 50, "BluRay": 40, "WEB-DL": 35,
                     "WEBRip": 25, "HDRip": 15, "HDTV": 15, "DVDRip": 10,
                     "microHD": 5}.get(self.source, 0)
        lang_score = {"CAST": 20, "DUAL": 15, "LAT": 10, "VOSE": 5}.get(self.audio_lang, 0)
        return res_score + src_score + lang_score


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class BaseProvider:
    name: str = ""
    display: str = ""
    icon: str = ""

    def search(self, query: str, kind: str = "movie") -> List[Result]:
        """Return up to N results in <= ~5 seconds.

        kind: "movie", "tvshow", "episode". Episode searches receive a query
        already formatted with the season/episode token (e.g. "Show 02x05").
        """
        raise NotImplementedError
