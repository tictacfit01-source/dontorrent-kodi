"""DivxTotal provider.

Domain rotates: current is www3.divxtotal.lol. Telegram channel is
@divxtotal2 (the 2 is just version, not a counter).

DivxTotal historically exposes torrent files via /download/<id> or directly
embeds magnets in the detail page. We try both.
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
RES_RE = re.compile(r"\b(2160p|1080p|720p|4k|hdrip|bluray|bdrip)\b", re.IGNORECASE)


class DivxTotal(BaseProvider):
    name = "divxtotal"
    display = "DivxTotal"

    def __init__(self):
        self.resolver = DomainResolver(
            name="divxtotal",
            brand_pattern=r"divxtotal",
            fallbacks=["www3.divxtotal.lol", "divxtotal.wf", "divxtotal.mov"],
            telegram_channel="divxtotal2",
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
            r = self._session().get(
                f"{base}/?s=" + requests.utils.quote(query),
                timeout=util.setting("resolve_timeout", 15, int),
            )
            r.raise_for_status()
        except Exception as exc:
            util.debug(f"divxtotal: search failed: {exc}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        items, seen = [], set()
        for a in soup.select("h2 a, h3 a, article a.titulo, a[rel='bookmark']"):
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
        a = soup.select_one("a[href^='magnet:']")
        if a:
            href = a.get("href") or ""
            return href, _hash_from_magnet(href), 0, None
        a = (soup.select_one("a[href$='.torrent']") or
             soup.select_one("a[href*='/download/']") or
             soup.select_one("a.btn-torrent"))
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
    if v in ("bluray", "bdrip"):
        return "1080p"
    if v == "hdrip":
        return "720p"
    return v


def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None
