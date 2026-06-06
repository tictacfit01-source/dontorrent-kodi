"""Small helpers: logging + settings + parent-addon access + proxy HTTP."""
import xbmc
import xbmcaddon
import requests
from urllib.parse import quote as urlquote

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")

PARENT = ADDON  # within the main addon, "parent" is ourselves

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {msg}", level)


def debug(msg):
    if ADDON.getSetting("debug_log") == "true":
        log(msg, xbmc.LOGINFO)


def setting(key, default=None, cast=str):
    val = ADDON.getSetting(key)
    if val == "" or val is None:
        return default
    if cast is bool:
        return val == "true"
    if cast is int:
        try:
            return int(val)
        except ValueError:
            return default
    return val


def parent_setting(key, default=None):
    """Read a setting from plugin.video.dontorrent if installed."""
    if not PARENT:
        return default
    try:
        v = PARENT.getSetting(key)
        return v if v else default
    except Exception:
        return default


def is_provider_enabled(name):
    return setting(f"provider_{name}", default=True, cast=bool)


# ---------------------------------------------------------------------------
# Proxy-aware HTTP for providers and shared modules
# ---------------------------------------------------------------------------

def _proxy_base():
    raw = (ADDON.getSetting("proxy_url") or "").strip().rstrip("/")
    return raw or None


def _proxy_force():
    return (ADDON.getSetting("proxy_force") or "").lower() == "true"


def proxy_session():
    """Return a requests.Session pre-configured with browser-like headers."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "identity",
    })
    return s


def proxy_get(url, session=None, timeout=15, headers=None, **kwargs):
    """GET that routes through the Cloudflare Worker proxy when configured.

    This is the function that providers (WolfMax4K, MejorTorrent, DivxTotal)
    and shared modules (torrent.py, resolver.py) should use instead of
    direct requests.get(). On ISP-blocked Spanish networks, direct requests
    fail with ConnectionResetError because of SNI/DPI blocking.
    """
    base = _proxy_base()
    s = session or proxy_session()
    h = dict(s.headers)
    if headers:
        h.update(headers)
    h["Accept-Encoding"] = "identity"

    if base and _proxy_force():
        proxied = f"{base}/?u={urlquote(url, safe='')}"
        r = s.get(proxied, timeout=timeout, headers=h, **kwargs)
        # Fix r.url: when proxied, r.url is the worker URL. Callers that
        # do urljoin(r.url, href) would build URLs pointing at the worker
        # instead of the upstream site. The worker sets x-mw-relay-final
        # to the real upstream URL.
        final = r.headers.get("x-mw-relay-final")
        if final:
            try:
                r.url = final
            except Exception:
                pass
    else:
        r = s.get(url, timeout=timeout, headers=h, **kwargs)
    r.raise_for_status()
    return r
