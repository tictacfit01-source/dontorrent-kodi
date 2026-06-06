"""MejorTorrent provider.

Domain resolution: privtr.ee/@mejortorrent (single redirect, very reliable)
plus the Telegram channel @MejorTorrentAp as backup. The site's domain is
"www<N>.mejortorrent.eu" where N is a counter that increments on every
ISP block.

Search endpoint has changed across versions of the site. We try several
paths and use permissive anchor matching (any link whose path looks like a
content slug under the same host).
"""
import re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

from .. import util, torrent, quality
from ..resolver import DomainResolver
from .base import BaseProvider, Result

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DETAIL_PATH_RE = re.compile(
    r"^/(serie|series|pelicula|peliculas|capitulo|capitulos|temporada|"
    r"documental|documentales|musica|juego|juegos)/[^/?#]+/?",
    re.IGNORECASE,
)
SKIP_PATH_RE = re.compile(
    r"^/(tag|categoria|category|author|page|wp-|feed|search|busqueda|comments)/",
    re.IGNORECASE,
)


class MejorTorrent(BaseProvider):
    name = "mejortorrent"
    display = "MejorTorrent"

    def __init__(self):
        self.resolver = DomainResolver(
            name="mejortorrent",
            brand_pattern=r"mejortorrent",
            fallbacks=["www43.mejortorrent.eu", "www42.mejortorrent.eu", "mejortorrent.eu"],
            telegram_channel="MejorTorrentAp",
            redirect_url="https://privtr.ee/@mejortorrent",
        )

    def search(self, query, kind="movie"):
        base = self.resolver.base_url()
        if not base:
            util.log(f"[mejortorrent] no base url resolved for query '{query}'")
            return []
        timeout = util.setting("resolve_timeout", 15, int)
        host = urlparse(base).hostname or ""
        q_enc = requests.utils.quote(query)

        urls = [
            f"{base}/busqueda/{q_enc}",
            f"{base}/?s={q_enc}",
            f"{base}/buscar/{q_enc}",
            f"{base}/search/{q_enc}",
        ]
        items, seen = [], set()
        for url in urls:
            try:
                r = util.proxy_get(url, timeout=timeout)
                if r.status_code != 200:
                    util.debug(f"[mejortorrent] {url} -> HTTP {r.status_code}")
                    continue
            except Exception as exc:
                util.debug(f"[mejortorrent] {url} failed: {exc}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                self._maybe_add(a, base, host, items, seen)
            if items:
                break

        max_n = util.setting("max_results_per_provider", 8, int)
        items = items[:max_n]
        util.log(f"[mejortorrent] '{query}' -> {len(items)} candidate(s) at {base}")

        results = []
        for title, page in items:
            magnet, ih, size, name = self._resolve(page)
            if not magnet:
                continue
            q = quality.merge(name or "", title)
            if not q["language"]:
                q["language"] = "CAST"
            results.append(Result(
                name=quality.format_label(self.display, name or title, q),
                uri=magnet,
                info_hash=ih,
                size=size,
                provider=self.display,
                resolution=q["resolution"],
                source=q["source"],
                codec=q["codec"],
                audio_lang=q["language"] or "CAST",
            ))
        util.log(f"[mejortorrent] '{query}' -> {len(results)} resolved magnet(s)")
        return results

    def _maybe_add(self, a, base, host, items, seen):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return
        full = href if href.startswith("http") else urljoin(base + "/", href)
        u = urlparse(full)
        if u.hostname and host and u.hostname != host:
            return
        path = u.path or ""
        if SKIP_PATH_RE.search(path):
            return
        if not DETAIL_PATH_RE.search(path):
            return
        if full in seen:
            return
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if not title or len(title) < 3:
            return
        seen.add(full)
        items.append((title, full))

    def _resolve(self, page_url):
        try:
            r = util.proxy_get(page_url, timeout=util.setting("resolve_timeout", 15, int))
        except Exception as exc:
            util.debug(f"[mejortorrent] detail {page_url} failed: {exc}")
            return None, None, 0, None
        soup = BeautifulSoup(r.text, "html.parser")
        a = soup.select_one("a[href^='magnet:']")
        if a:
            href = a.get("href") or ""
            return href, _hash_from_magnet(href), 0, None
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if ".torrent" not in href.lower() and "/download" not in href.lower():
                continue
            full = href if href.startswith("http") else urljoin(page_url, href)
            info = torrent.inspect_torrent(full, headers={"Referer": page_url})
            if info.get("magnet"):
                return info["magnet"], info["info_hash"], info["size"], info.get("name")
        return None, None, 0, None


def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None
