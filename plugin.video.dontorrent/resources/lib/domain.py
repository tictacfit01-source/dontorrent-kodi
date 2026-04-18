import re
import time
import requests
from bs4 import BeautifulSoup
import xbmcaddon

ADDON = xbmcaddon.Addon()

DOMAIN_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(don[a-z0-9\-]*torrent[a-z0-9\-]*\.[a-z]{2,8})',
    re.IGNORECASE,
)

FALLBACK_DOMAINS = [
    "dontorrent.reisen",
    "dontorrent.pink",
    "dontorrent.cfd",
    "dontorrent.photos",
    "dontorrent.promo",
    "dontorrent.vin",
]

UA = ("Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")


def _session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
    })
    return s


def parse_telegram(channel):
    """Return (available_hosts, censored_hosts) from a channel preview.

    Each post is analysed individually: domains in posts with the ✅ emoji or
    the word 'Disponible' are considered available; those with ❌ or
    'Censurado' are considered dead.
    """
    url = f"https://t.me/s/{channel}"
    try:
        r = _session().get(url, timeout=12)
        r.raise_for_status()
    except Exception:
        return [], set()

    soup = BeautifulSoup(r.text, "html.parser")
    posts = soup.select(".tgme_widget_message_text") or soup.select(".tgme_widget_message")
    available, censored = [], set()

    for msg in posts:
        text = msg.get_text(" ", strip=True)
        low = text.lower()
        hosts = []
        for m in DOMAIN_RE.finditer(text):
            hosts.append(m.group(1).lower())
        # Also look inside anchor hrefs
        for a in msg.select("a[href]"):
            for m in DOMAIN_RE.finditer(a.get("href", "")):
                hosts.append(m.group(1).lower())
        hosts = list(dict.fromkeys(hosts))
        if not hosts:
            continue

        is_available = ("\u2705" in text) or ("disponible" in low)
        is_censored = ("\u274c" in text) or ("censurad" in low) or ("caido" in low) or ("caído" in low)

        for h in hosts:
            if is_available and not is_censored:
                available.append(h)
            elif is_censored:
                censored.add(h)

    # Telegram preview: oldest first, newest last -> reverse so newest wins
    available = list(reversed(available))
    seen, ordered = set(), []
    for h in available:
        if h in seen:
            continue
        seen.add(h)
        ordered.append(h)
    return ordered, censored


def _probe(host):
    try:
        r = _session().get(f"https://{host}/", timeout=10, allow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    body = r.text.lower()
    if not any(k in body for k in ("torrent", "pelicula", "serie")):
        return None
    m = re.match(r'https?://([^/]+)', r.url)
    return (m.group(1).lower() if m else host)


def resolve(force=False):
    manual = ADDON.getSetting("manual_domain").strip()
    if manual:
        return manual.replace("https://", "").replace("http://", "").rstrip("/")

    cached = ADDON.getSetting("cached_domain").strip()
    try:
        cached_at = int(ADDON.getSetting("cached_at") or "0")
    except ValueError:
        cached_at = 0
    try:
        ttl_hours = int(ADDON.getSetting("cache_hours") or "12")
    except ValueError:
        ttl_hours = 12
    ttl = max(1, ttl_hours) * 3600

    if not force and cached and (time.time() - cached_at) < ttl:
        return cached

    channel = ADDON.getSetting("telegram_channel").strip() or "DonTorrent"
    tg_available, tg_censored = parse_telegram(channel)

    ordered, seen = [], set()
    for h in tg_available + ([cached] if cached else []) + FALLBACK_DOMAINS:
        if h and h not in seen and h not in tg_censored:
            seen.add(h)
            ordered.append(h)

    for host in ordered:
        resolved = _probe(host)
        if resolved:
            ADDON.setSetting("cached_domain", resolved)
            ADDON.setSetting("cached_at", str(int(time.time())))
            return resolved

    return cached or FALLBACK_DOMAINS[0]


def base_url(force=False):
    return f"https://{resolve(force)}"


def diagnose():
    channel = ADDON.getSetting("telegram_channel").strip() or "DonTorrent"
    avail, cens = parse_telegram(channel)
    resolved = resolve(force=True)
    return {
        "channel": channel,
        "telegram_available": avail,
        "telegram_censored": sorted(cens),
        "resolved": resolved,
    }
