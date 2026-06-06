import re
import os
import json
import time
import atexit
import requests
import xbmcaddon

try:
    import xbmcvfs
    _PROFILE = xbmcvfs.translatePath(
        "special://profile/addon_data/plugin.video.mejorwolf/")
except Exception:
    _PROFILE = ""

ADDON = xbmcaddon.Addon()

DEFAULT_KEY = "f090bb54758cabf231fb605d3e3e0468"

# ── Cache TMDB persistente en disco ─────────────────────────────────────────
# Kodi arranca un proceso nuevo en CADA navegacion, asi que una cache solo en
# memoria se perderia al entrar en una carpeta. Persistir en disco hace que
# las caratulas salgan instantaneas la 2a vez (y los dias siguientes) y reduce
# muchisimo las llamadas a la API.
_CACHE = {}        # sig(str) -> data(dict)   (dict vacio = "sin match")
_CACHE_TS = {}     # sig -> timestamp
_dirty = False
_last_flush = 0.0
_CACHE_FILE = os.path.join(_PROFILE, "tmdb_cache.json") if _PROFILE else ""
_POS_TTL = 30 * 24 * 3600   # match positivo: 30 dias
_NEG_TTL = 3 * 24 * 3600    # "sin match": 3 dias (por si TMDB lo añade luego)


def _sig(kind, clean):
    return f"{kind}|{(clean or '').lower()}"


def _cache_load():
    if not _CACHE_FILE or not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        now = time.time()
        for sig, ent in raw.items():
            data = ent.get("d", {})
            ts = ent.get("t", 0)
            ttl = _POS_TTL if data else _NEG_TTL
            if now - ts < ttl:
                _CACHE[sig] = data
                _CACHE_TS[sig] = ts
    except Exception:
        pass


def _cache_flush(force=False):
    global _dirty, _last_flush
    if not _CACHE_FILE or not _dirty:
        return
    now = time.time()
    if not force and (now - _last_flush) < 1.0:
        return   # agrupa rafagas de escrituras (una pagina de ~20 items)
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        # Snapshot (list) para no iterar un dict que otros hilos pueden mutar.
        snap = list(_CACHE.items())
        tmp = _CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({s: {"d": d, "t": _CACHE_TS.get(s, now)}
                       for s, d in snap}, f)
        os.replace(tmp, _CACHE_FILE)
        _dirty = False
        _last_flush = now
    except Exception:
        pass


def _cache_put(sig, data):
    global _dirty
    _CACHE[sig] = data
    _CACHE_TS[sig] = time.time()
    _dirty = True
    _cache_flush()


_cache_load()
atexit.register(lambda: _cache_flush(force=True))


def _key():
    return (ADDON.getSetting("tmdb_api_key") or "").strip() or DEFAULT_KEY


def _clean_title(title):
    clean = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", title)
    clean = re.sub(r"\s*-?\s*\b\d{1,2}\s*[aª]?\s*Temporada\b.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*-?\s*\bTemporada\s+\d{1,2}\b.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bParte\s+\d+\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"\bV\.?\s*Extendida\b|\bVersi[oó]n\s+Extendida\b|\bFullBluRay\b",
        "", clean, flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"\b(1080p|720p|2160p|4K|HDRip|BluRay|BDRip|BDremux|BRRip|WEB-?DL|WEBRip|microHD|HDTV"
        r"|x264|x265|HEVC|DUAL|VOSE|Latino|Castellano|Espa[nN]ol|Remux|HDR|DV|DoVi)\b",
        "", clean, flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", clean).strip(" -.[]()").strip()


def _year(title):
    m = re.search(r"\b(19|20)\d{2}\b", title)
    return m.group(0) if m else None


# Palabras vacias que NO deben contar para la similitud de titulos: si no se
# filtran, "The Thing" casaria con "Sabrina, The Teenage Witch" por la palabra
# "the", dando carátulas equivocadas.
_STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
    "la", "el", "los", "las", "un", "una", "unos", "unas", "de", "del",
    "y", "o", "en", "al", "lo", "le", "les", "su", "se", "con",
}


def _alt_titles(title):
    """Extrae titulos alternativos entre parentesis (p.ej. el titulo ORIGINAL).

    'La cosa (The Thing) (1996) [BluRay-1080p]' -> ['The Thing']
    El titulo original es la mejor pista para acertar en TMDB.
    """
    alts = []
    for m in re.findall(r"\(([^)]+)\)", title or ""):
        m = m.strip()
        if not m:
            continue
        # Saltar años sueltos y marcas tecnicas
        if re.fullmatch(r"(19|20)\d{2}", m):
            continue
        if re.search(r"\b(1080p|720p|2160p|4K|HDRip|BluRay|BDRip|BDremux|"
                     r"BRRip|WEB-?DL|WEBRip|microHD|HDTV|DVDRip|HEVC|x26[45]|"
                     r"DUAL|VOSE|Latino|Castellano|Remux)\b", m, re.IGNORECASE):
            continue
        c = _clean_title(m)
        if c and len(c) >= 3:
            alts.append(c)
    return alts


def _search(clean, kind, year):
    endpoint = "movie" if kind == "movie" else "tv"
    lang = (ADDON.getSetting("tmdb_lang") or "").strip() or "es-ES"
    params = {"api_key": _key(), "query": clean, "language": lang}
    # NO filtramos por año en la API: en DonTorrent el año suele ser el de la
    # edicion/reestreno (p.ej. "The Thing (1996)" cuando es de 1982), asi que
    # filtrar por año descartaria la pelicula correcta. El año se usa solo como
    # bonus suave en el scoring.
    try:
        r = requests.get(
            f"https://api.themoviedb.org/3/search/{endpoint}",
            params=params,
            timeout=8,
        )
        r.raise_for_status()
        return r.json().get("results") or []
    except Exception:
        return []


def _kinds_to_try(kind):
    # Siempre intentamos ambos endpoints (tv + movie). El "kind" sugerido
    # por el scraper es solo una pista: priorizamos el endpoint preferente
    # pero tambien consultamos el otro para poder elegir por popularidad.
    # Esto arregla casos como "Arcane" clasificado como "movie" por la
    # URL /online/<id> — en /search/movie TMDB devuelve un corto de Corto
    # Maltese con "Arcanes" en el titulo, mientras que /search/tv devuelve
    # la serie real de Netflix con popularidad >>>.
    if kind in ("movie", "movie_hd", "movie_4k"):
        return ("movie", "tv")
    # tvshow, tvshow_hd, documentary, etc.
    return ("tv", "movie")


def _sim_one(t, q):
    """Similitud entre un titulo `t` y una query `q` (ambos en minusculas)."""
    if not t or not q:
        return 0.0
    if t == q:
        return 1000.0
    if t.startswith(q) or q.startswith(t):
        return 600.0
    if q in t or t in q:
        return 350.0
    # Solapamiento de palabras IGNORANDO articulos/preposiciones y palabras
    # de <=2 letras. Asi "the thing" NO casa con "sabrina the teenage witch"
    # (solo compartirian "the", que es stopword).
    q_tok = {w for w in re.findall(r"\w+", q) if len(w) > 2 and w not in _STOP}
    t_tok = {w for w in re.findall(r"\w+", t) if len(w) > 2 and w not in _STOP}
    if q_tok and t_tok:
        inter = len(q_tok & t_tok)
        if inter:
            return 220.0 * inter / max(len(q_tok), 1)
    return 0.0


def _best_sim(result, queries):
    """Mejor similitud del resultado contra cualquiera de las queries,
    comparando tanto el titulo localizado como el ORIGINAL."""
    title = (result.get("title") or result.get("name") or "").lower()
    orig = (result.get("original_title")
            or result.get("original_name") or "").lower()
    best = 0.0
    for q in queries:
        q = (q or "").lower().strip()
        if not q:
            continue
        best = max(best, _sim_one(title, q), _sim_one(orig, q))
    return best


def _score(result, preferred_kind, queries, year):
    """Puntua un resultado TMDB. Mayor es mejor.

    REGLA DE ORO: la similitud de titulo manda. Un resultado cuyo titulo no
    comparte NADA con la busqueda esta DESCALIFICADO por muy popular que sea
    (antes la popularidad x40 hacia ganar a 'Sabrina' frente a 'The Thing').
    La popularidad y el año solo desempatan entre titulos que SI casan.
    """
    sim = _best_sim(result, queries)
    if sim <= 0:
        return -1e9   # descalificado: titulo sin relacion con la busqueda

    pop = float(result.get("popularity") or 0.0)
    votes = float(result.get("vote_count") or 0.0)
    # Popularidad SECUNDARIA y acotada (max ~150): desempata, no domina.
    pop_score = min(pop, 60.0) * 2.5
    # Señal de contenido real (no entrada basura).
    real_bonus = 60.0 if votes >= 50 else (20.0 if votes >= 5 else 0.0)
    ghost_penalty = -150.0 if (votes < 5 and pop < 1.0) else 0.0
    # Bonus de año SOLO si ademas hay similitud decente (evita que "cualquier
    # cosa del año X" gane por la fecha).
    y_res = (result.get("release_date") or result.get("first_air_date") or "")[:4]
    year_bonus = 150.0 if (year and y_res == year and sim >= 300) else 0.0
    kind_here = "movie" if "title" in result else "tv"
    kind_bonus = 120.0 if kind_here == preferred_kind else -120.0
    return sim + pop_score + real_bonus + year_bonus + kind_bonus + ghost_penalty


def _best_across_kinds(queries, kinds, year):
    """Consulta todas las queries x kinds, agrega y devuelve el mejor match.

    `queries` es una lista: titulo principal + titulos alternativos (original
    entre parentesis). Devuelve (None, kind) si ningun resultado tiene
    similitud real -> preferimos SIN caratula antes que una equivocada.
    """
    all_results = []
    seen = set()
    for q in queries:
        for k in kinds:
            for r in _search(q, k, year):
                rid = (k, r.get("id"))
                if rid in seen:
                    continue
                seen.add(rid)
                r["_tmdb_kind"] = k
                all_results.append(r)
    if not all_results:
        return None, kinds[0]
    preferred = kinds[0]
    all_results.sort(
        key=lambda r: _score(r, preferred, queries, year),
        reverse=True,
    )
    best = all_results[0]
    if _best_sim(best, queries) <= 0:
        return None, preferred   # nada casa de verdad
    return best, best.get("_tmdb_kind", preferred)


def enrich(title, kind="movie", alt_title_fn=None):
    """TMDB enrichment con busqueda multi-endpoint y ranking por popularidad."""
    if ADDON.getSetting("tmdb_enabled") != "true":
        return {}
    clean = _clean_title(title)
    if not clean:
        return {}
    cache_key = _sig(kind, clean)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    y = _year(title)
    kinds = _kinds_to_try(kind)
    # Queries: titulo principal + titulos alternativos entre parentesis
    # (normalmente el ORIGINAL, p.ej. "(The Thing)"), que es la mejor pista.
    queries = [clean]
    for a in _alt_titles(title):
        if a.lower() != clean.lower() and a not in queries:
            queries.append(a)
    best, matched_kind = _best_across_kinds(queries, kinds, y)

    if best is None and alt_title_fn is not None:
        try:
            alt = alt_title_fn()
        except Exception:
            alt = None
        if alt:
            alt_clean = _clean_title(alt)
            if alt_clean and alt_clean.lower() != clean.lower():
                alt_key = _sig(kind, alt_clean)
                if alt_key in _CACHE:
                    _cache_put(cache_key, _CACHE[alt_key])
                    return _CACHE[cache_key]
                best, matched_kind = _best_across_kinds(
                    [alt_clean], kinds, _year(alt) or y
                )

    if best is None:
        _cache_put(cache_key, {})   # negative cache (persistente, TTL corto)
        return {}

    top = best
    poster = top.get("poster_path")
    backdrop = top.get("backdrop_path")
    out = {
        "plot": top.get("overview"),
        "poster": f"https://image.tmdb.org/t/p/w780{poster}" if poster else None,
        "fanart": f"https://image.tmdb.org/t/p/original{backdrop}" if backdrop else None,
        "year": (top.get("release_date") or top.get("first_air_date") or "")[:4],
        "rating": top.get("vote_average"),
        "title": top.get("title") or top.get("name"),
    }
    _cache_put(cache_key, out)
    return out
