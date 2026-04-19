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
    resolution: str = ""            # "1080p", "720p", "2160p", ""
    is_rar: bool = False            # filtered out by addon for streaming

    def to_elementum(self) -> dict:
        d = asdict(self)
        # Elementum wants string size with units; convert if non-zero
        if self.size:
            d["size"] = _human_size(self.size)
        else:
            d["size"] = ""
        return d


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
