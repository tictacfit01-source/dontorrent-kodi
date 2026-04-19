"""DonTorrent provider (port of plugin.video.dontorrent's scraper).

Two-stage flow per result: search page -> detail page (for download buttons)
-> PoW handshake -> .torrent URL -> bencode -> magnet.

Detail+resolve+inspect runs in parallel across results to keep latency sane.
"""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

from .. import util, torrent
from ..resolver import DomainResolver
from .base import BaseProvider, Result


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}
DIFFICULTY = 3
API = "/api_validate_pow.php"
ITEM_RE = re.compile(r"^/(?:pelicula|serie|documental)/\d+/")
RES_RE = re.compile(r"\b(2160p|1080p|720p|4k|bdremux|blu-?ray)\b", re.IGNORECASE)


class DonTorrent(BaseProvider):
    name = "dontorrent"
    display = "DonTorrent"

    def __init__(self):
        # Reuse parent addon's cached domain when present (cheap shortcut).
        self.resolver = DomainResolver(
            name="dontorrent",
            brand_pattern=r"don[a-z0-9\-]*torrent",
            fallbacks=[
                "dontorrent.reisen", "dontorrent.pink", "dontorrent.cfd",
                "dontorrent.photos", "dontorrent.promo", "dontorrent.vin",
            ],
            telegram_channel="DonTorrent",
        )

    # --------------------------------------------------------------- search
    def search(self, query, kind="movie"):
        base = self.resolver.base_url()
        if not base:
            return []
        try:
            r = requests.post(
                urljoin(base + "/", "buscar"),
                data={"valor": query, "Buscar": "Buscar"},
                headers=HEADERS,
                timeout=util.setting("resolve_timeout", 15, int),
            )
            r.raise_for_status()
        except Exception as exc:
            util.debug(f"dontorrent: search failed: {exc}")
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        items, seen = [], set()
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not ITEM_RE.match(href):
                continue
            if href in seen:
                continue
            seen.add(href)
            title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
            if not title:
                continue
            items.append((title, urljoin(base, href)))
            if len(items) >= util.setting("max_results_per_provider", 8, int):
                break

        results = []
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(items)))) as ex:
            futs = {ex.submit(self._resolve_first, base, t, u): (t, u) for t, u in items}
            for fut in as_completed(futs):
                title, _ = futs[fut]
                try:
                    magnet, ih, size, name = fut.result()
                except Exception as exc:
                    util.debug(f"dontorrent: resolve failed: {exc}")
                    continue
                if not magnet:
                    continue
                res = _detect_res(name or title)
                results.append(Result(
                    name=f"[{self.display}] {name or title}",
                    uri=magnet,
                    info_hash=ih,
                    size=size,
                    provider=self.display,
                    resolution=res,
                ))
        return results

    # --------------------------------------------------------------- helpers
    def _resolve_first(self, base, title, page_url):
        """Pick the first download from the detail page and resolve it."""
        try:
            r = requests.get(page_url, timeout=util.setting("resolve_timeout", 15, int),
                             headers=HEADERS)
            r.raise_for_status()
        except Exception:
            return None, None, 0, None
        soup = BeautifulSoup(r.text, "html.parser")
        a = soup.select_one("a.protected-download")
        if not a:
            return None, None, 0, None
        cid = a.get("data-content-id")
        tabla = a.get("data-tabla")
        if not cid or not tabla:
            return None, None, 0, None
        try:
            t_url = self._pow(base, cid, tabla, page_url)
        except Exception as exc:
            util.debug(f"dontorrent: pow failed for {title}: {exc}")
            return None, None, 0, None
        info = torrent.inspect_torrent(t_url, headers={"Referer": page_url})
        if info.get("is_rar"):
            # Skip RAR-packed torrents - Elementum can't stream them.
            return None, None, 0, None
        return info.get("magnet"), info.get("info_hash"), info.get("size", 0), info.get("name")

    def _pow(self, base, content_id, tabla, page_url):
        h = dict(HEADERS)
        h.update({"Content-Type": "application/json", "Origin": base, "Referer": page_url})
        r = requests.post(
            base + API,
            data=json.dumps({"action": "generate", "content_id": int(content_id), "tabla": tabla}),
            headers=h, timeout=util.setting("resolve_timeout", 15, int),
        )
        r.raise_for_status()
        res = r.json()
        if not res.get("success"):
            raise RuntimeError(res.get("error") or "no challenge")
        challenge = res["challenge"]
        nonce = _solve(challenge)
        r = requests.post(
            base + API,
            data=json.dumps({"action": "validate", "challenge": challenge, "nonce": nonce}),
            headers=h, timeout=util.setting("resolve_timeout", 15, int),
        )
        r.raise_for_status()
        res = r.json()
        if not res.get("success") or not res.get("download_url"):
            raise RuntimeError(res.get("error") or "validation failed")
        url = res["download_url"]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = base + url
        return url


def _solve(challenge):
    target = "0" * DIFFICULTY
    nonce = 0
    while True:
        if hashlib.sha256((challenge + str(nonce)).encode()).hexdigest().startswith(target):
            return nonce
        nonce += 1


def _detect_res(text):
    if not text:
        return ""
    m = RES_RE.search(text)
    if not m:
        return ""
    val = m.group(1).lower()
    if val == "4k":
        return "2160p"
    if val in ("bdremux", "bluray", "blu-ray"):
        return "1080p"
    return val
