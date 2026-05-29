import re
import requests
import xbmcaddon

ADDON = xbmcaddon.Addon()

DEFAULT_KEY = "f090bb54758cabf231fb605d3e3e0468"

_CACHE = {}


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


def _search(clean, kind, year):
    endpoint = "movie" if kind == "movie" else "tv"
    lang = (ADDON.getSetting("tmdb_lang") or "").strip() or "es-ES"
    params = {"api_key": _key(), "query": clean, "language": lang}
    if year and kind == "movie":
        params["year"] = year
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


def _score(result, preferred_kind, query_clean, year):
    """Puntua un resultado TMDB. Mayor es mejor.

    Factores:
      - similitud titulo (exacto > empieza-por > contiene)
      - popularidad
      - coincidencia de anio
      - bonus leve por kind preferente (solo desempate)
    """
    title = (result.get("title") or result.get("name") or "").lower()
    q = (query_clean or "").lower().strip()
    if not title or not q:
        return 0.0
    # Similitud
    sim = 0.0
    if title == q:
        sim = 1000.0
    elif title.startswith(q) or q.startswith(title):
        sim = 500.0
    elif q in title or title in q:
        sim = 250.0
    else:
        # tokens compartidos
        q_tok = set(re.findall(r"\w+", q))
        t_tok = set(re.findall(r"\w+", title))
        if q_tok and t_tok:
            inter = len(q_tok & t_tok)
            sim = 100.0 * inter / max(len(q_tok), 1)
    pop = float(result.get("popularity") or 0.0)
    y_res = (result.get("release_date") or result.get("first_air_date") or "")[:4]
    year_bonus = 50.0 if (year and y_res == year) else 0.0
    kind_here = "movie" if "title" in result else "tv"
    # Bonus fuerte por kind correcto: si el scraper dice "movie" y TMDB
    # devuelve una serie con el mismo nombre, la pelicula debe ganar.
    # Ejemplo: "The Game" pelicula (1997, Fincher) vs serie (2006).
    kind_bonus = 200.0 if kind_here == preferred_kind else 0.0
    # Penalizar resultados del kind equivocado con exacto titulo match
    # (evita que una serie popular gane a una pelicula correcta)
    kind_penalty = -150.0 if kind_here != preferred_kind else 0.0
    return sim + pop + year_bonus + kind_bonus + kind_penalty


def _best_across_kinds(clean, kinds, year):
    """Consulta todos los kinds, agrega resultados, devuelve el mejor."""
    all_results = []
    for k in kinds:
        for r in _search(clean, k, year):
            # anotamos el kind para saber de que endpoint vino
            r["_tmdb_kind"] = k
            all_results.append(r)
    if not all_results:
        return None, kinds[0]
    preferred = kinds[0]
    all_results.sort(
        key=lambda r: _score(r, preferred, clean, year),
        reverse=True,
    )
    best = all_results[0]
    return best, best.get("_tmdb_kind", preferred)


def enrich(title, kind="movie", alt_title_fn=None):
    """TMDB enrichment con busqueda multi-endpoint y ranking por popularidad."""
    if ADDON.getSetting("tmdb_enabled") != "true":
        return {}
    clean = _clean_title(title)
    if not clean:
        return {}
    cache_key = (kind, clean)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    y = _year(title)
    kinds = _kinds_to_try(kind)
    best, matched_kind = _best_across_kinds(clean, kinds, y)

    if best is None and alt_title_fn is not None:
        try:
            alt = alt_title_fn()
        except Exception:
            alt = None
        if alt:
            alt_clean = _clean_title(alt)
            if alt_clean and alt_clean.lower() != clean.lower():
                alt_key = (kind, alt_clean)
                if alt_key in _CACHE:
                    _CACHE[cache_key] = _CACHE[alt_key]
                    return _CACHE[cache_key]
                best, matched_kind = _best_across_kinds(
                    alt_clean, kinds, _year(alt) or y
                )

    results = [best] if best is not None else []

    if not results:
        _CACHE[cache_key] = {}
        return {}

    top = results[0]
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
    _CACHE[cache_key] = out
    return out
