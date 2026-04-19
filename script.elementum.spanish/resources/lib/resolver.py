"""Generic domain resolver.

A single DomainResolver instance per site. Resolution strategies, in order:
    1. Manual override (per-site setting `manual_<name>`).
    2. In-memory + addon-setting cache, valid for `cache_hours`.
    3. `redirect_url` (e.g. privtr.ee/@mejortorrent) - one GET that follows
       redirects and reads the final hostname. Cheapest, most reliable.
    4. Telegram channel scrape (`channel`) for posts marked as available
       (white check) and not censored (red x).
    5. Hardcoded `fallbacks` probed in order.

Each candidate host is verified with `validator(html_text)` before being
accepted, so we never cache a parking page or DNS squatter.
"""
import json
import re
import time
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from . import util

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

DOMAIN_RE = re.compile(
    r'(?:https?://)?(?:www\d*\.)?([a-z0-9\-]+(?:\.[a-z0-9\-]+)+)',
    re.IGNORECASE,
)


def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "es-ES,es;q=0.9",
    })
    return s


def _load_cache():
    raw = util.setting("cached_domains", default="{}")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _save_cache(cache):
    util.ADDON.setSetting("cached_domains", json.dumps(cache))


class DomainResolver:
    def __init__(self, name, brand_pattern, fallbacks=(),
                 telegram_channel=None, redirect_url=None, validator=None):
        self.name = name
        # Regex matching only domains belonging to this brand (e.g. r"don[a-z\-]*torrent")
        self.brand_pattern = re.compile(brand_pattern, re.IGNORECASE)
        self.fallbacks = list(fallbacks)
        self.telegram_channel = telegram_channel
        self.redirect_url = redirect_url
        self.validator = validator or self._default_validator

    @staticmethod
    def _default_validator(html):
        low = html.lower()
        return any(k in low for k in ("torrent", "pelicula", "serie", "magnet"))

    # ------------------------------------------------------------------ probe
    def _probe(self, host):
        try:
            r = _session().get(f"https://{host}/", timeout=10, allow_redirects=True)
        except Exception as exc:
            util.debug(f"{self.name}: probe {host} failed: {exc}")
            return None
        if r.status_code != 200:
            return None
        if not self.validator(r.text):
            return None
        m = re.match(r'https?://([^/]+)', r.url)
        return (m.group(1).lower() if m else host)

    # -------------------------------------------------------------- resolvers
    def _from_redirect(self):
        if not self.redirect_url:
            return None
        try:
            r = _session().get(self.redirect_url, timeout=8, allow_redirects=True)
            host = urlparse(r.url).hostname
            if host and self.brand_pattern.search(host):
                return host.lower()
        except Exception as exc:
            util.debug(f"{self.name}: redirect failed: {exc}")
        return None

    def _from_telegram(self):
        if not self.telegram_channel:
            return [], set()
        url = f"https://t.me/s/{self.telegram_channel}"
        try:
            r = _session().get(url, timeout=12)
            r.raise_for_status()
        except Exception:
            return [], set()
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.select(".tgme_widget_message_text") or soup.select(".tgme_widget_message")
        avail, cens = [], set()
        for msg in posts:
            text = msg.get_text(" ", strip=True)
            low = text.lower()
            hosts = []
            for m in DOMAIN_RE.finditer(text):
                h = m.group(1).lower()
                if self.brand_pattern.search(h):
                    hosts.append(h)
            for a in msg.select("a[href]"):
                for m in DOMAIN_RE.finditer(a.get("href", "")):
                    h = m.group(1).lower()
                    if self.brand_pattern.search(h):
                        hosts.append(h)
            hosts = list(dict.fromkeys(hosts))
            if not hosts:
                continue
            is_avail = ("\u2705" in text) or ("disponible" in low) or ("oficial" in low)
            is_cens = ("\u274c" in text) or ("censurad" in low) or ("caido" in low) or ("caído" in low)
            for h in hosts:
                if is_avail and not is_cens:
                    avail.append(h)
                elif is_cens:
                    cens.add(h)
        # newest first
        avail = list(reversed(avail))
        seen, out = set(), []
        for h in avail:
            if h not in seen:
                seen.add(h)
                out.append(h)
        return out, cens

    # ------------------------------------------------------------------ main
    def resolve(self, force=False):
        manual = util.setting(f"manual_{self.name}", default="").strip()
        if manual:
            return manual.replace("https://", "").replace("http://", "").rstrip("/")

        cache = _load_cache()
        entry = cache.get(self.name) or {}
        cached = entry.get("host")
        cached_at = entry.get("at", 0)
        ttl = util.setting("domain_cache_hours", default=12, cast=int) * 3600

        if not force and cached and (time.time() - cached_at) < ttl:
            return cached

        # 1) cheap redirect check first
        host = self._from_redirect()
        if host:
            confirmed = self._probe(host)
            if confirmed:
                cache[self.name] = {"host": confirmed, "at": int(time.time())}
                _save_cache(cache)
                return confirmed

        # 2) telegram + fallbacks
        tg_avail, tg_cens = self._from_telegram()
        candidates = []
        for h in tg_avail + ([cached] if cached else []) + self.fallbacks:
            if h and h not in tg_cens and h not in candidates:
                candidates.append(h)

        for h in candidates:
            confirmed = self._probe(h)
            if confirmed:
                cache[self.name] = {"host": confirmed, "at": int(time.time())}
                _save_cache(cache)
                return confirmed

        # last resort: return whatever we have, even if not validated
        return cached or (self.fallbacks[0] if self.fallbacks else None)

    def base_url(self, force=False):
        host = self.resolve(force=force)
        return f"https://{host}" if host else None
