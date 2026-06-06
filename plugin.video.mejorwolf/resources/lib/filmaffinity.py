"""FilmAffinity — nota media para enriquecer descripciones.

FilmAffinity no tiene API, asi que se hace scraping ligero: busqueda -> ficha
-> itemprop="ratingValue". Las notas apenas cambian, asi que se cachean en
disco con TTL largo; tras la 1a vez son instantaneas y no penalizan la
velocidad. Todo va envuelto en try/except: si FA falla, no rompe nada.
"""
import re
import os
import json
import time
import atexit
import unicodedata
import requests

try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
    _PROFILE = xbmcvfs.translatePath(
        "special://profile/addon_data/plugin.video.mejorwolf/")
    _ENABLED_FN = lambda: (xbmcaddon.Addon().getSetting("filmaffinity_enabled")
                           or "true").strip().lower() != "false"
except Exception:
    _PROFILE = ""
    _ENABLED_FN = lambda: True

_BASE = "https://www.filmaffinity.com/es"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
}

# ── Cache en disco ──────────────────────────────────────────────────────────
_CACHE = {}        # sig -> nota(float) | None
_CACHE_TS = {}
_dirty = False
_last_flush = 0.0
_CACHE_FILE = os.path.join(_PROFILE, "fa_cache.json") if _PROFILE else ""
_POS_TTL = 30 * 24 * 3600   # nota encontrada: 30 dias
_NEG_TTL = 5 * 24 * 3600    # sin nota: 5 dias


def _log(msg):
    try:
        xbmc.log(f"[MejorWolf/FA] {msg}", xbmc.LOGINFO)
    except Exception:
        pass


def _sig(title, year):
    t = unicodedata.normalize("NFKD", (title or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t).strip()
    return f"{t}|{year or ''}"


def _cache_load():
    if not _CACHE_FILE or not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        now = time.time()
        for sig, ent in raw.items():
            val = ent.get("v")
            ts = ent.get("t", 0)
            ttl = _POS_TTL if val is not None else _NEG_TTL
            if now - ts < ttl:
                _CACHE[sig] = val
                _CACHE_TS[sig] = ts
    except Exception:
        pass


def _cache_flush(force=False):
    global _dirty, _last_flush
    if not _CACHE_FILE or not _dirty:
        return
    now = time.time()
    if not force and (now - _last_flush) < 1.0:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        snap = list(_CACHE.items())
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({s: {"v": v, "t": _CACHE_TS.get(s, now)}
                       for s, v in snap}, f)
        os.replace(tmp, _CACHE_FILE)
        _dirty = False
        _last_flush = now
    except Exception:
        pass


def _cache_put(sig, val):
    global _dirty
    _CACHE[sig] = val
    _CACHE_TS[sig] = time.time()
    _dirty = True
    _cache_flush()


_cache_load()
atexit.register(lambda: _cache_flush(force=True))


# ── Scraping ────────────────────────────────────────────────────────────────
def _extract_rating(html):
    m = (re.search(r'itemprop="ratingValue"[^>]*content="([0-9.]+)"', html)
         or re.search(r'id="movie-rat-avg"[^>]*>\s*([0-9.,]+)', html))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _pick_film_id(html, year):
    """De una pagina de resultados, elige el id de ficha. Si hay año, intenta
    el resultado cuyo entorno contiene ese año; si no, el primero."""
    ids = re.findall(r"/es/film(\d+)\.html", html)
    if not ids:
        return None
    if year:
        for m in re.finditer(r"/es/film(\d+)\.html", html):
            window = html[m.start():m.start() + 400]
            if str(year) in window:
                return m.group(1)
    return ids[0]


def _fetch(title, year):
    try:
        r = requests.get(f"{_BASE}/search.php", params={"stext": title},
                         headers=_HEADERS, timeout=6, allow_redirects=True)
        if r.status_code != 200:
            return None
        html, url = r.text, r.url
        if "/film" not in url:   # pagina de resultados (varias coincidencias)
            fid = _pick_film_id(html, year)
            if not fid:
                return None
            r2 = requests.get(f"{_BASE}/film{fid}.html",
                              headers=_HEADERS, timeout=6)
            if r2.status_code != 200:
                return None
            html = r2.text
        return _extract_rating(html)
    except Exception as e:
        _log(f"fetch error: {e.__class__.__name__}")
        return None


def rating(title, year=None):
    """Devuelve la nota de FilmAffinity (float 0-10) o None. Cacheado en disco.

    `title` deberia ser el titulo español (idealmente el que resuelve TMDB),
    que es el que mejor casa en FilmAffinity.
    """
    if not title or not _ENABLED_FN():
        return None
    sig = _sig(title, year)
    if sig in _CACHE:
        return _CACHE[sig]
    val = _fetch(title, year)
    _cache_put(sig, val)
    return val


def rating_str(title, year=None):
    """Nota formateada estilo español ('7,4') o None."""
    val = rating(title, year)
    if val is None:
        return None
    s = f"{val:.1f}".replace(".", ",")
    # 9,0 -> 9 (FA muestra enteros sin decimal)
    return s[:-2] if s.endswith(",0") else s
