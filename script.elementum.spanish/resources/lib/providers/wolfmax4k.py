"""WolfMax4k provider.

Fixed domain (`wolfmax4k.com`), low/no anti-scraping, premium 1080p/4K.
Telegram channel kept as a backup if the domain ever rotates.
"""
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

from .. import util, torrent
from ..resolver import DomainResolver
from .base import BaseProvider, Result


_RES_RE = re.compile(r"\b(2160p|1080p|720p|4k)\b", re.IGNORECASE)


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

    # ------------------------------------------------------------------ http
    def _session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Accept-Language": "es-ES,es;q=0.9",
        })
        return s

    # --------------------------------------------------------------- search
    def search(self, query, kind="movie"):
        base = self.resolver.base_url()
        if not base:
            return []
        try:
            # WolfMax4k uses ?s= search param (WordPress-style).
            r = self._session().get(
                f"{base}/?s=" + requests.utils.quote(query),
                timeout=util.setting("resolve_timeout", 15, int),
            )
            r.raise_for_status()
        except Exception as exc:
            util.debug(f"wolfmax4k: search failed: {exc}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        # Each result is typically an <article> or <h2><a> link to the post.
        items = []
        seen = set()
        for a in soup.select("h2 a, h3 a, article a.post-link, a.titulo"):
            href = a.get("href") or ""
            if not href or href in seen:
                continue
            if not href.startswith("http"):
                href = urljoin(base, href)
            title = (a.get_text(" ", strip=True) or "").strip()
            if not title or len(title) < 3:
                continue
            seen.add(href)
            items.append((title, href))
            if len(items) >= util.setting("max_results_per_provider", 8, int):
                break

        results = []
        for title, page in items:
            magnet, info_hash, size, res = self._resolve_page(page)
            if not magnet:
                continue
            label = f"[{self.display}] {title}"
            if res and res not in title:
                label += f" {res}"
            results.append(Result(
                name=label,
                uri=magnet,
                info_hash=info_hash,
                size=size,
                provider=self.display,
                resolution=res or _detect_res(title),
            ))
        return results

    # --------------------------------------------------------------- detail
    def _resolve_page(self, url):
        """Return (magnet, info_hash, size, resolution) for a post page."""
        try:
            r = self._session().get(url, timeout=util.setting("resolve_timeout", 15, int))
            r.raise_for_status()
        except Exception as exc:
            util.debug(f"wolfmax4k: detail {url} failed: {exc}")
            return None, None, 0, ""

        soup = BeautifulSoup(r.text, "html.parser")

        # 1) Direct magnet link
        for a in soup.select("a[href^='magnet:']"):
            href = a.get("href") or ""
            ih = _hash_from_magnet(href)
            return href, ih, 0, _detect_res(soup.get_text(" ", strip=True))

        # 2) .torrent download link
        for a in soup.select("a[href$='.torrent'], a[href*='.torrent?']"):
            href = a.get("href") or ""
            if not href.startswith("http"):
                href = urljoin(url, href)
            info = torrent.inspect_torrent(href, headers={"Referer": url})
            if info.get("magnet"):
                return (info["magnet"], info["info_hash"], info["size"],
                        _detect_res(info.get("name") or ""))

        return None, None, 0, ""


def _detect_res(text):
    if not text:
        return ""
    m = _RES_RE.search(text)
    if not m:
        return ""
    val = m.group(1).lower()
    return "2160p" if val == "4k" else val


def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None
