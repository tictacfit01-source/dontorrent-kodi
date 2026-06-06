"""DivxTotal provider.

Domain rotates: current is www3.divxtotal.lol. Telegram channel is
@divxtotal2 (the 2 is just version, not a counter).

Search results are rendered in a `<table>` whose first cell links to the
detail page (e.g. /peliculas/<slug>/, /series/<slug>/). Detail pages do
NOT expose direct magnets - they expose a redirector
`download_tt.php?u=<absolute .torrent URL>` (sometimes wrapped in
`https://short-info.link/s.php?i=<base64>` which decodes to that same
download_tt URL). We extract the .torrent URL and bencode it locally to
build a magnet (no PoW, no captcha).
"""
import base64
import re
from urllib.parse import urljoin, urlparse, parse_qs, unquote
import requests
from bs4 import BeautifulSoup

from .. import util, torrent, quality
from ..resolver import DomainResolver
from .base import BaseProvider, Result

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DETAIL_PATH_RE = re.compile(
    r"/(peliculas|series|series-vk|musica|programas|juegos)/[^/?#]+/?$",
    re.IGNORECASE,
)


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

    # --------------------------------------------------------------- search
    def search(self, query, kind="movie"):
        base = self.resolver.base_url()
        if not base:
            util.log(f"[divxtotal] no base url resolved for query '{query}'")
            return []
        try:
            r = util.proxy_get(
                f"{base}/?s=" + requests.utils.quote(query),
                timeout=util.setting("resolve_timeout", 15, int),
            )
        except Exception as exc:
            util.log(f"[divxtotal] search failed at {base}: {exc}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        items, seen = [], set()

        # Strategy 1: table rows (current layout, April 2026)
        for a in soup.select("table tr td a[href]"):
            self._maybe_add(a, base, items, seen)

        # Strategy 2: any anchor whose path looks like a detail slug
        if not items:
            for a in soup.select("a[href]"):
                self._maybe_add(a, base, items, seen)

        max_n = util.setting("max_results_per_provider", 8, int)
        items = items[:max_n]
        util.log(f"[divxtotal] '{query}' -> {len(items)} candidate(s) at {base}")

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
        util.log(f"[divxtotal] '{query}' -> {len(results)} resolved magnet(s)")
        return results

    def _maybe_add(self, a, base, items, seen):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            return
        full = href if href.startswith("http") else urljoin(base + "/", href)
        path = urlparse(full).path or ""
        if not DETAIL_PATH_RE.search(path):
            return
        if full in seen:
            return
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if not title or len(title) < 3:
            return
        seen.add(full)
        items.append((title, full))

    # --------------------------------------------------------------- detail
    def _resolve(self, page_url):
        try:
            r = util.proxy_get(page_url, timeout=util.setting("resolve_timeout", 15, int))
        except Exception as exc:
            util.debug(f"[divxtotal] detail {page_url} failed: {exc}")
            return None, None, 0, None
        soup = BeautifulSoup(r.text, "html.parser")

        # 1) direct magnet (rare but possible)
        a = soup.select_one("a[href^='magnet:']")
        if a:
            href = a.get("href") or ""
            return href, _hash_from_magnet(href), 0, None

        # 2) collect all torrent-related anchors; prefer the largest video file
        torrent_urls = []
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            tu = self._extract_torrent_url(href)
            if tu and tu not in torrent_urls:
                torrent_urls.append(tu)

        for tu in torrent_urls:
            info = torrent.inspect_torrent(tu, headers={"Referer": page_url})
            if info.get("magnet"):
                return info["magnet"], info["info_hash"], info["size"], info.get("name")
        return None, None, 0, None

    @staticmethod
    def _extract_torrent_url(href):
        if not href:
            return None
        # Direct .torrent
        if ".torrent" in href.lower():
            # download_tt.php?u=<url>
            qs = parse_qs(urlparse(href).query)
            if "u" in qs and qs["u"]:
                return unquote(qs["u"][0])
            return href
        # short-info.link/s.php?i=<base64>
        if "short-info.link" in href and "i=" in href:
            qs = parse_qs(urlparse(href).query)
            raw = qs.get("i", [""])[0]
            try:
                # padding-safe base64
                pad = "=" * (-len(raw) % 4)
                dec = base64.b64decode(raw + pad).decode("utf-8", "replace")
                if ".torrent" in dec.lower():
                    return dec
            except Exception:
                pass
        return None


def _hash_from_magnet(magnet):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None
