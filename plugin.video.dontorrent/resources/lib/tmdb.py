import re
import requests
import xbmcaddon

ADDON = xbmcaddon.Addon()

# Widely-used public TMDB v3 read key. User can override in settings.
DEFAULT_KEY = "f090bb54758cabf231fb605d3e3e0468"

_CACHE = {}


def _key():
    return (ADDON.getSetting("tmdb_api_key") or "").strip() or DEFAULT_KEY


def _clean_title(title):
    # Strip [brackets] and (parens) blocks.
    clean = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", title)
    # Season/part markers that would confuse the TV/movie search.
    clean = re.sub(r"\s*-?\s*\b\d{1,2}\s*[aª]?\s*Temporada\b.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*-?\s*\bTemporada\s+\d{1,2}\b.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bParte\s+\d+\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"\bV\.?\s*Extendida\b|\bVersi[oó]n\s+Extendida\b|\bFullBluRay\b",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"\b(1080p|720p|2160p|4K|HDRip|BluRay|BDRip|BDremux|BRRip|WEB-?DL|WEBRip|microHD|HDTV|x264|x265|HEVC|DUAL|VOSE|Latino|Castellano|Espa[nN]ol)\b",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", clean).strip(" -.[]()")


def _year(title):
    m = re.search(r"\b(19|20)\d{2}\b", title)
    return m.group(0) if m else None


def _search(clean, kind, year):
    endpoint = "movie" if kind == "movie" else "tv"
    lang = ADDON.getSetting("tmdb_lang") or "es-ES"
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
        return (r.json().get("results") or [])
    except Exception:
        return []


def _kinds_to_try(kind):
    """DonTorrent "documentales" are a mix of feature docs (movies on TMDB)
    and docu-series (TV on TMDB) - e.g. 'Drag Race España', 'Happy Jail'.
    For that kind we try TV first (more hits) and fall back to movie."""
    if kind == "documentary":
        return ("tv", "movie")
    return (kind,)


def enrich(title, kind="movie", alt_title_fn=None):
    """TMDB lookup. If the first (cleaned) title produces no hit and an
    ``alt_title_fn`` callable is given, we call it to obtain a better title
    (e.g. the H1 of the detail page, which keeps Spanish accents lost by the
    URL slug) and retry. Results are cached by every title we try."""
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
    results = []
    matched_kind = kinds[0]
    for k in kinds:
        results = _search(clean, k, y)
        if results:
            matched_kind = k
            break

    # Fallback: slug-derived titles lose accents, which breaks Spanish
    # searches on TMDB. Pull the real title from the detail page and retry.
    if not results and alt_title_fn is not None:
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
                for k in kinds:
                    results = _search(alt_clean, k, _year(alt) or y)
                    if results:
                        matched_kind = k
                        break

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
