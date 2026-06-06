"""WolfMax4k provider.

Fixed domain (`wolfmax4k.com`) behind Cloudflare. From a residential Spanish
IP the scrape works; from datacenter/VPN it may be challenged. We try both
the WordPress-style `?s=` endpoint and the alt `/buscar/` path, and we use
permissive selectors (any anchor whose path looks like a content slug) so
template tweaks don't break the addon.
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

# Slug-looking paths that aren't site chrome. WolfMax4k uses
# /pelicula/<slug>/, /serie/<slug>/, /capitulo/<slug>/ etc.
DETAIL_PATH_RE = re.compile(
    r"^/(pelicula|peliculas|serie|series|capitulo|capitulos|temporada|"
    r"documental|documentales|anime|animes|4k|uhd)/[^/?#]+/?",
    re.IGNORECASE,
)
SKIP_PATH_RE = re.compile(
    r"^/(tag|category|categoria|author|page|wp-|feed|comments|search)/",
    re.IGNORECASE,
)


class WolfMax4k(BaseProvider):
    name = "wolfmax4k"
    display = "WolfMax4k"

    def __init__(self):
        self.resolver = DomainResolver(
            name="wolfmax4k",
            brand_pattern=r"wolfmax4k",
            fallbacks=["wolfmax4k.com", "wolfmax4k.net", "wolfmax4k.it.com"],
            telegram_channel="WolfMax4k",
        )

    def search(self, query, kind="movie"):
        base = self.resolver.base_url()
        if not base:
            util.log(f"[wolfmax4k] no base url resolved for query '{query}'")
            return []
        timeout = util.setting("resolve_timeout", 15, int)
        host = urlparse(base).hostname or ""

        urls = [
            f"{base}/?s=" + requests.utils.quote(query),
            f"{base}/buscar/" + requests.utils.quote(query),
            f"{base}/search/" + requests.utils.quote(query),
        ]
        items, seen = [], set()
        for url in urls:
            try:
                r = util.proxy_get(url, timeout=timeout)
                if r.status_code != 200:
                    util.debug(f"[wolfmax4k] {url} -> HTTP {r.status_code}")
                    continue
            except Exception as exc:
                util.debug(f"[wolfmax4k] {url} failed: {exc}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                self._maybe_add(a, base, host, items, seen)
            if items:
                break

        max_n = util.setting("max_results_per_provider", 8, int)
        items = items[:max_n]
        util.log(f"[wolfmax4k] '{query}' -> {len(items)} candidate(s) at {base}")

        results = []
        for title, page in items:
            magnet, info_hash, size, name, page_text = self._resolve_page(page)
            if not magnet:
                continue
            q = quality.merge(name or "", title, page_text or "")
            if not q["language"]:
                q["language"] = "CAST"
            results.append(Result(
                name=quality.format_label(self.display, name or title, q),
                uri=magnet,
                info_hash=info_hash,
                size=size,
                provider=self.display,
                resolution=q["resolution"],
                source=q["source"],
                codec=q["codec"],
                audio_lang=q["language"] or "CAST",
            ))
        util.log(f"[wolfmax4k] '{query}' -> {len(results)} resolved magnet(s)")
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

    def _resolve_page(self, url):
        try:
            r = util.proxy_get(url, timeout=util.setting("resolve_timeout", 15, int))
        except Exception as exc:
            util.debug(f"[wolfmax4k] detail {url} failed: {exc}")
            return None, None, 0, None, ""
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)[:1000]

        for a in soup.select("a[href^='magnet:']"):
            href = a.get("href") or ""
            return href, _hash_from_magnet(href), 0, None, page_text

        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if ".torrent" not in href.lower():
                continue
            full = href if href.startswith("http") else urljoin(url, href)
            info = torrent.inspect_torrent(full, headers={"Referer": url})
            if info.get("magnet"):
                return info["magnet"], info["info_hash"], info["size"], info.get("name"), page_text

        return None, None, 0, None, page_text


def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None
