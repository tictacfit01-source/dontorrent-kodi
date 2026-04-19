"""MejorTorrent provider.

Domain resolution: privtr.ee/@mejortorrent (single redirect, very reliable)
plus the Telegram channel @MejorTorrentAp as backup. The site's domain is
"www<N>.mejortorrent.eu" where N is a counter that increments on every
ISP block.

The site is built on a custom CMS where each result page exposes a
`magnet:?...` link directly (no PoW, no captcha as of April 2026), which
keeps this provider very fast.

NOTE: HTML selectors below were derived from search-result reports.
If MejorTorrent rotates its template the selectors here may need refresh -
enable "Log detallado" in settings to see what we're parsing.
"""
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

from .. import util, torrent
from ..resolver import DomainResolver
from .base import BaseProvider, Result

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
RES_RE = re.compile(r"\b(2160p|1080p|720p|4k|bdremux|blu-?ray)\b", re.IGNORECASE)


class MejorTorrent(BaseProvider):
    name = "mejortorrent"
    display = "MejorTorrent"

    def __init__(self):
        self.resolver = DomainResolver(
            name="mejortorrent",
            brand_pattern=r"mejortorrent",
            fallbacks=["www42.mejortorrent.eu", "mejortorrent.eu"],
            telegram_channel="MejorTorrentAp",
            redirect_url="https://privtr.ee/@mejortorrent",
        )

    def _session(self):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
        return s

    def search(self, query, kind="movie"):
        base = self.resolver.base_url()
        if not base:
            return []
        try:
            # MejorTorrent search endpoint historically: /busqueda/<query>
            # or POST to /buscar - try GET first.
            r = self._session().get(
                f"{base}/busqueda/" + requests.utils.quote(query),
                timeout=util.setting("resolve_timeout", 15, int),
            )
            r.raise_for_status()
        except Exception as exc:
            util.debug(f"mejortorrent: search failed: {exc}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        items, seen = [], set()
        # Result rows: anchors that link to /serie/.. or /pelicula/..
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not re.search(r"/(serie|pelicula|peliculas|series)/", href):
                continue
            if href.endswith(".css") or href.endswith(".js"):
                continue
            if href in seen:
                continue
            seen.add(href)
            title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
            if not title or len(title) < 3:
                continue
            full = href if href.startswith("http") else urljoin(base, href)
            items.append((title, full))
            if len(items) >= util.setting("max_results_per_provider", 8, int):
                break

        results = []
        for title, page in items:
            magnet, ih, size, name = self._resolve(page)
            if not magnet:
                continue
            results.append(Result(
                name=f"[{self.display}] {name or title}",
                uri=magnet,
                info_hash=ih,
                size=size,
                provider=self.display,
                resolution=_detect_res(name or title),
            ))
        return results

    def _resolve(self, page_url):
        try:
            r = self._session().get(page_url, timeout=util.setting("resolve_timeout", 15, int))
            r.raise_for_status()
        except Exception:
            return None, None, 0, None
        soup = BeautifulSoup(r.text, "html.parser")
        # 1) magnet link directly
        a = soup.select_one("a[href^='magnet:']")
        if a:
            href = a.get("href") or ""
            ih = _hash_from_magnet(href)
            return href, ih, 0, None
        # 2) .torrent download
        a = soup.select_one("a[href$='.torrent']") or soup.select_one("a[href*='/download/']")
        if a:
            href = a.get("href") or ""
            if not href.startswith("http"):
                href = urljoin(page_url, href)
            info = torrent.inspect_torrent(href, headers={"Referer": page_url})
            if info.get("magnet"):
                return info["magnet"], info["info_hash"], info["size"], info["name"]
        return None, None, 0, None


def _detect_res(text):
    if not text:
        return ""
    m = RES_RE.search(text)
    if not m:
        return ""
    v = m.group(1).lower()
    if v == "4k":
        return "2160p"
    if v in ("bdremux", "bluray", "blu-ray"):
        return "1080p"
    return v


def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None
