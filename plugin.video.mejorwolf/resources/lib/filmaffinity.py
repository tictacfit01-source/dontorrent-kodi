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


import threading

# Sesion reutilizable + cortesia: limitamos concurrencia y espaciamos las
# peticiones para parecer humano y no provocar el anti-bot de FilmAffinity
# (que con rafagas devuelve 200 con cuerpo VACIO). Como casi todo va a cache,
# el volumen real es bajo.
_SESSION = requests.Session()
_SEM = threading.Semaphore(4)
_RATE_LOCK = threading.Lock()
_last_req = [0.0]
_MIN_INTERVAL = 0.05


def _polite_get(url, params=None):
    with _RATE_LOCK:
        dt = time.time() - _last_req[0]
        if dt < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - dt)
        _last_req[0] = time.time()
    return _SESSION.get(url, params=params, headers=_HEADERS, timeout=8,
                        allow_redirects=True)


def _norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return [w for w in re.findall(r"\w+", s) if len(w) > 1]


def _title_sim(a, b):
    ta, tb = set(_norm(a)), set(_norm(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


def _result_blocks(html):
    """Extrae (film_id, titulo_visible, año) de una pagina de resultados."""
    blocks = []
    for m in re.finditer(r"/es/film(\d+)\.html", html):
        fid = m.group(1)
        window = html[m.start():m.start() + 500]
        # titulo: texto del propio enlace o atributo title=
        tm = (re.search(r'>([^<>]{2,120})</a>', window)
              or re.search(r'title="([^"]{2,120})"', window))
        title = tm.group(1).strip() if tm else ""
        ym = re.search(r"\b(19|20)\d{2}\b", window)
        yr = ym.group(0) if ym else ""
        blocks.append((fid, title, yr))
    return blocks


def _pick_film_id(html, query, year):
    """Elige la mejor ficha de la pagina de resultados por titulo + año."""
    blocks = _result_blocks(html)
    if not blocks:
        return None
    best, best_score = None, -1.0
    for fid, title, yr in blocks:
        score = _title_sim(query, title) * 2.0
        if year and yr == str(year):
            score += 1.0
        if score > best_score:
            best, best_score = fid, score
    return best or blocks[0][0]


# Estados de _fetch: ("ok", nota) | ("none", None) | ("err", None)
# 'err' = respuesta vacia/bloqueada/red: NO se cachea para reintentar luego.
def _fetch(title, year):
    try:
        r = _polite_get(f"{_BASE}/search.php", params={"stext": title})
    except Exception as e:
        _log(f"fetch error: {e.__class__.__name__}")
        return "err", None
    if r.status_code != 200 or len(r.text) < 500:
        return "err", None   # vacio/bloqueado -> no envenenar la cache
    html, url = r.text, r.url
    if "/film" not in url:   # pagina de resultados (varias coincidencias)
        fid = _pick_film_id(html, title, year)
        if not fid:
            return "none", None
        try:
            r2 = _polite_get(f"{_BASE}/film{fid}.html")
        except Exception:
            return "err", None
        if r2.status_code != 200 or len(r2.text) < 500:
            return "err", None
        html = r2.text
    val = _extract_rating(html)
    return ("ok", val) if val is not None else ("none", None)


# Presupuesto de peticiones de red POR NAVEGACION (proceso). Acota el tiempo
# de la 1a visita en paginas grandes y el riesgo de bloqueo: lo que no de
# tiempo a resolver, se queda para la proxima visita (no se cachea como fallo).
_MAX_FETCHES = 30
_fetch_count = [0]


def rating(title, year=None):
    """Nota de FilmAffinity (float 0-10) o None. Cacheado en disco.

    Solo cachea resultados REALES (encontrado o 'no existe'); las respuestas
    vacias/bloqueadas NO se cachean para poder reintentar en otra sesion.
    """
    if not title or not _ENABLED_FN():
        return None
    sig = _sig(title, year)
    if sig in _CACHE:
        return _CACHE[sig]
    with _SEM:
        # Re-chequeo dentro del semaforo (otro hilo pudo cachearlo)
        if sig in _CACHE:
            return _CACHE[sig]
        if _fetch_count[0] >= _MAX_FETCHES:
            return None      # presupuesto agotado: se reintenta en otra visita
        _fetch_count[0] += 1
        status, val = _fetch(title, year)
    if status == "err":
        return None          # no cacheamos -> se reintenta mas adelante
    _cache_put(sig, val)     # 'ok' (float) o 'none' (None)
    return val


def rating_best(titles, year=None):
    """Prueba varios titulos candidatos (español, original, limpio) y devuelve
    la primera nota encontrada. Maximiza la cobertura."""
    seen = set()
    for t in titles:
        t = (t or "").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        val = rating(t, year)
        if val is not None:
            return val
    return None


def _fmt(val):
    if val is None:
        return None
    s = f"{val:.1f}".replace(".", ",")
    return s[:-2] if s.endswith(",0") else s   # 9,0 -> 9


def rating_str(title, year=None):
    """Nota formateada estilo español ('7,4') o None."""
    return _fmt(rating(title, year))


def rating_str_best(titles, year=None):
    """Como rating_str pero probando varios titulos candidatos (BLOQUEA)."""
    return _fmt(rating_best(titles, year))


# ── API cache-only + warm en segundo plano ─────────────────────────────────
# Para NO penalizar la velocidad de navegacion: al pintar la lista solo leemos
# de cache (instantaneo); si falta, lanzamos un fetch en segundo plano para que
# este disponible la proxima vez. Asi la cobertura crece con el uso.
_inflight = set()
_inflight_lock = threading.Lock()


def _candidates_clean(titles):
    out, seen = [], set()
    for t in titles:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def cached_best(titles, year=None):
    """Nota desde cache SOLO (sin red). float | None."""
    if not _ENABLED_FN():
        return None
    for t in _candidates_clean(titles):
        sig = _sig(t, year)
        if sig in _CACHE and _CACHE[sig] is not None:
            return _CACHE[sig]
    return None


def cached_str_best(titles, year=None):
    """Nota formateada desde cache SOLO (instantaneo)."""
    return _fmt(cached_best(titles, year))


def warm_best(titles, year=None):
    """Si no hay nota cacheada, la busca en SEGUNDO PLANO (no bloquea) para
    tenerla disponible la proxima vez. Evita duplicar trabajo en vuelo."""
    if not _ENABLED_FN():
        return
    cands = _candidates_clean(titles)
    if not cands:
        return
    # Si todos los candidatos ya estan resueltos en cache (positivo o
    # negativo), no hay nada que calentar.
    if all(_sig(t, year) in _CACHE for t in cands):
        return
    key = "|".join(c.lower() for c in cands) + f"#{year or ''}"
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)

    def _job():
        try:
            rating_best(cands, year)
        finally:
            with _inflight_lock:
                _inflight.discard(key)

    try:
        threading.Thread(target=_job, daemon=True).start()
    except Exception:
        with _inflight_lock:
            _inflight.discard(key)
