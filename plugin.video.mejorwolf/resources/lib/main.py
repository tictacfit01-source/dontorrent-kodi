"""
MejorWolf - Navegacion Kodi v2.4.0
3 fuentes en castellano: DonTorrent + EliteTorrent + WolfMax4K.
Menu limpio: Estrenos, Cine, Series, Documentales, Buscar.
"""

import sys
import os
import re
import json
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs

from . import scraper_wolfmax as wf
from . import scraper_elitetorrent as et
from . import scraper_dontorrent as dt
from . import supabase_sync as sb
from . import tmdb, player
from . import filmaffinity as fa
from . import http_session as hs
from . import dns_doh
from . import torrent as tparse
from . import enlacito

CloudflareChallengeError = hs.CloudflareChallengeError

ADDON   = xbmcaddon.Addon()
HANDLE  = int(sys.argv[1]) if len(sys.argv) > 1 else -1
BASE    = sys.argv[0] if sys.argv else "plugin://plugin.video.mejorwolf/"

ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ICON       = os.path.join(ADDON_PATH, "icon.png")
FANART     = os.path.join(ADDON_PATH, "fanart.jpg")
DEFAULT_ART = {"fanart": FANART}

_PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
_LAST_SEARCH_FILE = os.path.join(_PROFILE, "last_search.txt")


def _last_search_load():
    try:
        with open(_LAST_SEARCH_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _last_search_save(q):
    try:
        os.makedirs(_PROFILE, exist_ok=True)
        with open(_LAST_SEARCH_FILE, "w", encoding="utf-8") as f:
            f.write(q or "")
    except Exception:
        pass

# Kodi built-in icons — no necesitamos archivos custom
IC = {
    "estrenos":    "DefaultRecentlyAddedMovies.png",
    "movie":       "DefaultMovies.png",
    "tvshow":      "DefaultTVShows.png",
    "documentary": "DefaultVideoPlaylists.png",
    "genre":       "DefaultGenre.png",
    "search":      "DefaultAddonsSearch.png",
    "settings":    "DefaultAddonProgram.png",
    "help":        "DefaultAddonHelp.png",
    "next":        "DefaultFolderBack.png",
}

KIND_LABEL = {
    "estrenos":     "Estrenos",
    "movie":        "Cine",
    "movie_hd":     "Cine HD",
    "movie_4k":     "Cine 4K",
    "movie_720p":   "Cine 720p",
    "movie_hdrip":  "Cine HDRip",
    "movie_micro":  "Cine MicroHD",
    "tvshow":       "Series",
    "tvshow_hd":    "Series HD",
    "tvshow_4k":    "Series 4K",
    "tvshow_720p":  "Series 720p",
    "documentary":  "Documentales",
    "castellano":   "Castellano",
}

SOURCE_LABEL = {"dt": "DonTorrent", "et": "EliteTorrent", "wf": "WolfMax4K"}
_SCRAPERS    = {"wf": wf, "dt": dt}


# --- Supabase: actualizar dominios al arrancar ---
def _supabase_startup():
    try:
        for key, setting in [("wolfmax", "wf_base_url"),
                             ("elitetorrent", "et_base_url"),
                             ("dontorrent", "dt_base_url")]:
            domain = sb.get_domain(key)
            if domain:
                current = (ADDON.getSetting(setting) or "").strip()
                prefix = "https://www." if key == "wolfmax" else "https://"
                if domain not in current:
                    new_url = f"{prefix}{domain}"
                    ADDON.setSetting(setting, new_url)
                    xbmc.log(f"[MejorWolf] Supabase: {key} -> {new_url}", xbmc.LOGINFO)
        my_version = ADDON.getAddonInfo("version")
        sb.check_addon_update("addon_mejorwolf", my_version)
    except Exception as e:
        xbmc.log(f"[MejorWolf] Supabase startup (no critico): {e}", xbmc.LOGWARNING)

try:
    _supabase_startup()
except Exception:
    pass


# ── Helpers ─────────────────────────────────────────────────────────────────

def _u(**kwargs):
    return BASE + "?" + urlencode({k: v for k, v in kwargs.items() if v is not None})


def _li(label, info=None, art=None, playable=False, icon=None):
    it  = xbmcgui.ListItem(label=label)
    mrg = dict(DEFAULT_ART)
    mrg.setdefault("icon",  icon or ICON)
    mrg.setdefault("thumb", icon or ICON)
    if art:
        mrg.update({k: v for k, v in art.items() if v})
    it.setArt(mrg)
    if info:
        data = {}
        for k in ("title", "plot", "year", "rating", "mediatype",
                   "season", "episode", "tvshowtitle"):
            v = info.get(k)
            if v in (None, "", 0):
                continue
            if k in ("year", "season", "episode"):
                try:
                    data[k] = int(v)
                except (TypeError, ValueError):
                    continue
            elif k == "rating":
                try:
                    data[k] = float(v)
                except (TypeError, ValueError):
                    continue
            else:
                data[k] = v
        if data:
            it.setInfo("video", data)
    if playable:
        it.setProperty("IsPlayable", "true")
    return it


def _tmdb_kind(kind):
    if kind == "documentary":
        return "documentary"
    if "movie" in kind or kind in ("estrenos", "castellano"):
        return "movie"
    return "tv"


def _media_type(kind):
    if "movie" in kind or kind in ("documentary", "estrenos", "castellano"):
        return "movie"
    return "tvshow"


def _display_title(it, year=None):
    """Construye titulo limpio: 'Titulo (2001) [1080p]'."""
    base = it.get("title") or ""
    # Año
    yr = year or it.get("year")
    if yr:
        yr_str = str(yr)
        # Solo añadir si no esta ya en el titulo
        if yr_str not in base:
            base = f"{base} ({yr_str})"
    # Calidad
    q = it.get("quality")
    if q and q.lower() not in base.lower():
        base = f"{base} [{q}]"
    return base


# ── Enriquecimiento TMDB ───────────────────────────────────────────────────

def _enrich_one(it):
    source  = it.get("source", "wf")
    scraper = _SCRAPERS.get(source)
    url     = it.get("url")
    alt     = None
    if scraper and url and hasattr(scraper, 'fetch_detail_title'):
        alt = lambda u=url: scraper.fetch_detail_title(u)
    # TMDB para todas las fuentes. Para WolfMax, la portada propia esta
    # bloqueada por Cloudflare (403), asi que dependemos de TMDB. El truco:
    # si el item tiene marca de capitulo (Cap.N / SxxExx) es contenido
    # SERIADO -> forzamos kind="tv". Esto hace que "Rafa" encuentre el
    # documental seriado de TMDB (popularidad 11) en vez de la peli
    # portuguesa de 2012 (popularidad 0.3).
    raw_t = it.get("title", "")
    tmdb_kind = _tmdb_kind(it.get("kind", "movie"))
    if re.search(r"\b[Cc]ap\.?\s*\d+|\b[Ss]\d{1,2}[Ee]\d{1,3}\b", raw_t):
        tmdb_kind = "tv"
    meta = tmdb.enrich(raw_t, kind=tmdb_kind, alt_title_fn=alt)
    # Año: preferir TMDB, fallback al scraper
    year = meta.get("year") or it.get("year")
    # Calidad: asegurar que siempre se propaga
    if not it.get("quality"):
        # Intentar extraer calidad del titulo original del scraper
        raw_title = it.get("title", "")
        qm = re.search(
            r"\b(4K|2160p|1080p|720p|HDRip|BluRay|BDRemux|BDRip|"
            r"WEB-?DL|WEBRip|MicroHD|HDTV|DVDRip|Remux)\b",
            raw_title, re.I,
        )
        if qm:
            it["quality"] = qm.group(1)
    plot = meta.get("plot") or ""
    # Nota de FilmAffinity al principio de la descripcion. Probamos varios
    # titulos candidatos para maximizar la cobertura: el español que resuelve
    # TMDB, el ORIGINAL (ingles) y el titulo limpio del scraper. Cacheado en
    # disco -> tras la 1a vez es instantaneo y no penaliza la velocidad.
    try:
        fa_candidates = [
            meta.get("title"),
            meta.get("original"),
            tmdb._clean_title(raw_t),
        ]
        # Sincrono (el render de Kodi es un proceso efimero: un fetch en
        # segundo plano se mataria al terminar la navegacion). Cacheado en
        # disco -> coste unico por titulo; la 2a vez es instantaneo.
        fa_rt = fa.rating_str_best(fa_candidates, year)
        if fa_rt:
            plot = f"[B]FilmAffinity: {fa_rt}[/B]\n\n{plot}".rstrip()
    except Exception:
        pass
    info = {
        "title":     _display_title(it, year=year),
        "plot":      plot,
        "year":      year,
        "rating":    meta.get("rating"),
        "mediatype": _media_type(it.get("kind", "movie")),
    }
    art = {}
    own_img = it.get("image") or it.get("thumb")
    # TMDB primero (su CDN image.tmdb.org SIEMPRE carga en Kodi). La imagen
    # propia de WolfMax (wolfmax4k.com/assets) esta tras Cloudflare y da 403
    # en Kodi, asi que solo la usamos como ultimo recurso para fuentes que no
    # sean WF (DT/ET cuyas imagenes si cargan).
    if meta.get("poster"):
        art["poster"] = meta["poster"]
        art["thumb"]  = meta["poster"]
    if meta.get("fanart"):
        art["fanart"] = meta["fanart"]
    if own_img and it.get("source") != "wf":
        art.setdefault("thumb",  own_img)
        art.setdefault("poster", own_img)
    return info, art


def _enrich_many(items, workers=6):
    if not items:
        return []
    # Dedupe de consultas TMDB: varias variantes del mismo titulo (p.ej. 4x
    # "La cosa (The Thing)") comparten UNA sola consulta. Pre-calentamos las
    # claves unicas en paralelo; despues _enrich_one va 100% a cache.
    seen, uniq = set(), []
    for it in items:
        raw_t = it.get("title", "")
        kk = _tmdb_kind(it.get("kind", "movie"))
        if re.search(r"\b[Cc]ap\.?\s*\d+|\b[Ss]\d{1,2}[Ee]\d{1,3}\b", raw_t):
            kk = "tv"
        sig = (kk, tmdb._clean_title(raw_t).lower())
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(it)
    if len(uniq) < len(items):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_enrich_one, uniq))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_enrich_one, items))


def _error(msg, level=xbmcgui.NOTIFICATION_ERROR, dur=8000):
    xbmcgui.Dialog().notification("MejorWolf", msg, level, dur)


# ── Navegacion principal ───────────────────────────────────────────────────

def _warm_wf_catalog():
    """Pre-calienta en segundo plano al abrir el addon (sin bloquear):
      - Catalogo WolfMax (cache en disco) -> 1a busqueda WF lista.
      - Sesion Anubis de DonTorrent en el relay -> los listados DT
        (Cine/Series/Documentales) dejan de tardar 30-40s la 1a vez,
        porque el PoW ya esta resuelto y cacheado en el servidor.
    """
    def _bg_wf():
        try:
            wf._build_catalog()
        except Exception:
            pass

    def _bg_dt():
        try:
            # Toca el relay para que resuelva y cachee la sesion Anubis DT.
            import requests as _rq
            base = dt._render_relay_url()
            dom = dt.resolve_domain()
            if base and dom:
                _rq.get(f"{base}/dtfetch",
                        params={"u": f"https://{dom}/documentales"},
                        timeout=60)
        except Exception:
            pass

    try:
        import threading
        threading.Thread(target=_bg_wf, daemon=True).start()
        threading.Thread(target=_bg_dt, daemon=True).start()
    except Exception:
        pass


class _Timer:
    """Dialogo de progreso con cronometro en vivo. Muestra los segundos que
    lleva cargando para que el usuario vea el tiempo real. Se usa como:
        with _Timer("Cargando documentales...") as t:
            ... trabajo ...
            t.tick("Obteniendo listado")   # actualiza el texto
    Corre un hilo que refresca el contador cada 0.5s.
    """
    def __init__(self, heading):
        self.heading = heading
        self.t0 = time.time()
        self.msg = "Conectando..."
        self._stop = False
        self._dlg = None
        self._th = None

    def __enter__(self):
        try:
            self._dlg = xbmcgui.DialogProgressBG()
            self._dlg.create("MejorWolf", self.heading)
            import threading
            self._th = threading.Thread(target=self._run, daemon=True)
            self._th.start()
        except Exception:
            self._dlg = None
        return self

    def _run(self):
        pct = 0
        while not self._stop:
            try:
                el = time.time() - self.t0
                pct = min(95, pct + 3)
                if self._dlg:
                    self._dlg.update(pct, "MejorWolf",
                                     f"{self.msg}  ({el:.0f}s)")
            except Exception:
                pass
            time.sleep(0.5)

    def tick(self, msg):
        self.msg = msg

    def elapsed(self):
        return time.time() - self.t0

    def __exit__(self, *a):
        self._stop = True
        try:
            if self._dlg:
                self._dlg.close()
        except Exception:
            pass


def home():
    xbmcplugin.setPluginCategory(HANDLE, "MejorWolf")
    # Calentar el catalogo WolfMax en background al abrir el addon.
    _warm_wf_catalog()
    entries = [
        ("Estrenos",      _u(action="estrenos"),                 IC["estrenos"]),
        ("Cine",          _u(action="section", kind="movie"),    IC["movie"]),
        ("Series",        _u(action="section", kind="tvshow"),   IC["tvshow"]),
        ("Documentales",  _u(action="documentales_menu"),       IC["documentary"]),
        ("Generos",       _u(action="generos_menu"),             IC["genre"]),
        ("Buscar",        _u(action="search"),                   IC["search"]),
    ]
    for label, url, ic in entries:
        xbmcplugin.addDirectoryItem(HANDLE, url, _li(label, icon=ic), isFolder=True)
    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE)


def estrenos():
    """Estrenos organizados por fuente."""
    xbmcplugin.setPluginCategory(HANDLE, "Estrenos")
    entries = [
        ("DonTorrent",    _u(action="list", src="dt", kind="estrenos", page=1),  IC["estrenos"]),
        ("EliteTorrent",  _u(action="list", src="et", kind="estrenos", page=1),  IC["estrenos"]),
        ("WolfMax4K",     _u(action="list", src="wf", kind="movie", page=1),     IC["estrenos"]),
    ]
    for lab, url, ic in entries:
        xbmcplugin.addDirectoryItem(HANDLE, url, _li(lab, icon=ic), isFolder=True)
    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE)


def section(kind):
    """Submenu por FUENTE (DonTorrent / EliteTorrent / WolfMax4K).
    Igual que Estrenos: primero eliges la web, dentro sus calidades. Asi
    cada fuente luce su catalogo por separado, sin mezclar."""
    label = KIND_LABEL.get(kind, kind)
    xbmcplugin.setPluginCategory(HANDLE, label)

    if kind == "movie":
        ic = IC["movie"]
        entries = [
            ("DonTorrent",    _u(action="src_movie", src="dt"), ic),
            ("EliteTorrent",  _u(action="src_movie", src="et"), ic),
            ("WolfMax4K",     _u(action="src_movie", src="wf"), ic),
            ("Buscar pelicula", _u(action="search", filter_kind="movie"), IC["search"]),
        ]
    elif kind == "tvshow":
        ic = IC["tvshow"]
        entries = [
            ("DonTorrent",    _u(action="src_tvshow", src="dt"), ic),
            ("EliteTorrent",  _u(action="src_tvshow", src="et"), ic),
            ("WolfMax4K",     _u(action="src_tvshow", src="wf"), ic),
            ("Buscar serie",  _u(action="search", filter_kind="tvshow"), IC["search"]),
        ]
    else:
        entries = [
            (f"Ultimas {label}", _u(action="list", src="dt", kind=kind, page=1), IC.get("documentary", IC["movie"])),
        ]

    for lab, url, ic2 in entries:
        xbmcplugin.addDirectoryItem(HANDLE, url, _li(lab, icon=ic2), isFolder=True)
    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE)


# Calidades disponibles por fuente para peliculas y series.
_SRC_MOVIE_QUALITIES = {
    "dt": [("Peliculas", "movie"), ("Peliculas HD", "movie_hd"),
           ("Peliculas 4K", "movie_4k")],
    "et": [("Estrenos", "estrenos"), ("Peliculas 720p", "movie_720p"),
           ("Peliculas HDRip", "movie_hdrip"), ("Peliculas MicroHD", "movie_micro")],
    "wf": [("Peliculas 1080p", "movie_hd"), ("Peliculas 4K", "movie_4k")],
}
_SRC_TVSHOW_QUALITIES = {
    "dt": [("Series", "tvshow"), ("Series HD", "tvshow_hd"),
           ("Series 4K", "tvshow_4k")],
    "et": [("Series", "tvshow")],
    "wf": [("Series 1080p", "tvshow_hd"), ("Series 4K", "tvshow_4k")],
}


def src_section(src, kind):
    """Lista las calidades de una fuente concreta (movie o tvshow)."""
    table = _SRC_MOVIE_QUALITIES if kind == "movie" else _SRC_TVSHOW_QUALITIES
    quals = table.get(src, [])
    ic = IC["movie"] if kind == "movie" else IC["tvshow"]
    xbmcplugin.setPluginCategory(HANDLE, SOURCE_LABEL.get(src, src))
    for lab, k in quals:
        xbmcplugin.addDirectoryItem(
            HANDLE, _u(action="list", src=src, kind=k, page=1),
            _li(lab, icon=ic), isFolder=True)
    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE)


def documentales_menu():
    """Submenu de Documentales por fuente (igual que Cine/Series)."""
    xbmcplugin.setPluginCategory(HANDLE, "Documentales")
    ic = IC["documentary"]
    entries = [
        ("DonTorrent",   _u(action="list", src="dt", kind="documentary", page=1), ic),
        ("WolfMax4K",    _u(action="list", src="wf", kind="documentary", page=1), ic),
        ("EliteTorrent", _u(action="et_genre", genre="documental", page=1),       ic),
    ]
    for lab, url, ic2 in entries:
        xbmcplugin.addDirectoryItem(HANDLE, url, _li(lab, icon=ic2), isFolder=True)
    xbmcplugin.setContent(HANDLE, "files")
    xbmcplugin.addSortMethod(HANDLE, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(HANDLE)


def generos_menu():
    """Generos cinematograficos (EliteTorrent)."""
    xbmcplugin.setPluginCategory(HANDLE, "Generos")
    for key in sorted(et.GENRE_LABELS.keys()):
        label = et.GENRE_LABELS[key]
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="et_genre", genre=key, page=1),
            _li(label, icon=IC["genre"]),
            isFolder=True,
        )
    xbmcplugin.endOfDirectory(HANDLE)


# ── Listados unificados ────────────────────────────────────────────────────

def list_items(src, kind, page=1):
    """Lista items de cualquier fuente."""
    label = KIND_LABEL.get(kind, kind)
    src_name = SOURCE_LABEL.get(src, src)
    xbmcplugin.setPluginCategory(HANDLE, label)

    with _Timer(f"Cargando {label} ({src_name})...") as timer:
        try:
            timer.tick(f"Obteniendo listado de {src_name}")
            if src == "dt":
                items = dt.latest(kind=kind, page=page)
            elif src == "et":
                result = et.latest(kind=kind, page=page)
                items = result[0] if isinstance(result, tuple) else result
            elif src == "wf":
                items = wf.latest(kind=kind, page=page)
            else:
                items = []
        except CloudflareChallengeError:
            _error("Sitio bloqueado temporalmente. Prueba mas tarde.",
                   xbmcgui.NOTIFICATION_WARNING)
            xbmcplugin.endOfDirectory(HANDLE)
            return
        except Exception as e:
            xbmc.log(f"[MejorWolf] list error: {e}", xbmc.LOGERROR)
            _error(f"Error de conexion: {type(e).__name__}")
            xbmcplugin.endOfDirectory(HANDLE)
            return

        if not items:
            _error("Sin resultados.", xbmcgui.NOTIFICATION_WARNING, 5000)
            xbmcplugin.endOfDirectory(HANDLE)
            return

        timer.tick(f"Cargando portadas ({len(items)} resultados)")
        enriched = _enrich_many(items)
        xbmc.log(f"[MejorWolf] list {src}/{kind} cargado en "
                 f"{timer.elapsed():.1f}s ({len(items)} items)", xbmc.LOGINFO)
    for it, (info, art) in zip(items, enriched):
        is_wf_series = it.get("kind", "").startswith("tvshow") and it.get("source") == "wf"
        if is_wf_series:
            url = _u(action="wf_series", q=it["title"], seed_url=it["url"],
                     title=it["title"])
        else:
            url = _u(action="detail", src=it.get("source", src),
                     url=it["url"], kind=it.get("kind", kind),
                     title=it["title"])
        xbmcplugin.addDirectoryItem(HANDLE, url,
                                    _li(info["title"], info=info, art=art),
                                    isFolder=True)

    # Pagina siguiente
    next_page = page + 1
    xbmcplugin.addDirectoryItem(
        HANDLE,
        _u(action="list", src=src, kind=kind, page=next_page),
        _li(f"Pagina siguiente ({next_page}) >>", icon=IC["next"]),
        isFolder=True,
    )

    content = "movies" if _media_type(kind) == "movie" else "tvshows"
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE)


def et_genre(genre_key, page=1):
    """Lista items por genero (EliteTorrent)."""
    label = et.GENRE_LABELS.get(genre_key, genre_key)
    xbmcplugin.setPluginCategory(HANDLE, label)
    try:
        items, next_url = et.genre(genre_key, page=page)
    except Exception as e:
        xbmc.log(f"[MejorWolf] et_genre error: {e}", xbmc.LOGERROR)
        _error(f"Error: {type(e).__name__}")
        xbmcplugin.endOfDirectory(HANDLE)
        return

    if not items:
        _error("Sin resultados en este genero.", xbmcgui.NOTIFICATION_WARNING, 5000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    enriched = _enrich_many(items)
    for it, (info, art) in zip(items, enriched):
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="detail", src="et", url=it["url"],
               kind=it.get("kind", "movie"), title=it["title"]),
            _li(info["title"], info=info, art=art),
            isFolder=True,
        )

    if next_url:
        next_page = page + 1
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="et_genre", genre=genre_key, page=next_page),
            _li(f"Pagina siguiente ({next_page}) >>", icon=IC["next"]),
            isFolder=True,
        )

    xbmcplugin.setContent(HANDLE, "movies")
    xbmcplugin.endOfDirectory(HANDLE)


# ── Detalle ─────────────────────────────────────────────────────────────────

def detail(src, url, kind, title):
    """Ficha de detalle — dispatcha al scraper correcto."""
    if src == "dt":
        return _detail_dt(url, kind, title)
    if src == "et":
        return _detail_et(url, kind, title)
    return _detail_wf(url, kind, title)


def _detail_dt(url, kind, title):
    """Ficha DonTorrent: descargas con PoW."""
    try:
        d = dt.detail(url)
    except Exception as e:
        xbmc.log(f"[MejorWolf] dt detail error: {e}", xbmc.LOGERROR)
        _error(f"Error DonTorrent: {type(e).__name__}")
        xbmcplugin.endOfDirectory(HANDLE)
        return

    alt = (lambda t=d.get("title"): t) if d.get("title") else None
    meta = tmdb.enrich(title, kind=_tmdb_kind(kind), alt_title_fn=alt)
    art = _build_art(meta, d)
    info_base = _build_info_base(meta, d)

    movie_label = d.get("title") or title or "Pelicula"
    has_episodes = any(dl.get("season") is not None for dl in d["downloads"])
    year_str = f" ({meta.get('year') or d.get('year', '')})" if (meta.get("year") or d.get("year")) else ""

    for dl in d["downloads"]:
        if has_episodes and dl.get("season") is None:
            continue

        quality = dl.get("quality", "")

        if has_episodes:
            label = dl.get("label", "Capitulo")
            if quality and quality.lower() not in label.lower():
                label = f"{label} [{quality}]"
            mtype = "episode"
        else:
            label = movie_label + year_str
            if quality and quality.lower() not in label.lower():
                label = f"{label} [{quality}]"
            mtype = "movie"

        item_info = dict(info_base, title=label, mediatype=mtype)
        if has_episodes and dl.get("season") is not None:
            item_info["season"]  = dl["season"]
            item_info["episode"] = dl.get("episode", 0)

        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="dt_play", content_id=dl["content_id"],
               tabla=dl["tabla"], page_url=url),
            _li(label, info=item_info, art=art, playable=True),
            isFolder=False,
        )

    if not d["downloads"]:
        _error("Sin enlaces de descarga.", xbmcgui.NOTIFICATION_WARNING, 6000)
    xbmcplugin.endOfDirectory(HANDLE)


def _detail_et(url, kind, title):
    """Ficha EliteTorrent: magnets directos."""
    try:
        results, info = et.detail(url)
    except Exception as e:
        xbmc.log(f"[MejorWolf] et detail error: {e}", xbmc.LOGERROR)
        _error(f"Error EliteTorrent: {type(e).__name__}")
        xbmcplugin.endOfDirectory(HANDLE)
        return

    meta = tmdb.enrich(title, kind=_tmdb_kind(kind))
    art = _build_art(meta, info)
    info_base = _build_info_base(meta, info)
    movie_label = info.get("title") or title or "Descarga"
    year_str = f" ({meta.get('year')})" if meta.get("year") else ""

    for dl in results:
        magnet = dl.get("magnet")
        if not magnet:
            continue
        quality = dl.get("quality", "")
        label = movie_label + year_str
        if quality and quality.lower() not in label.lower():
            label = f"{label} [{quality}]"

        item_info = dict(info_base, title=label, mediatype=_media_type(kind))
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="play", torrent=magnet),
            _li(label, info=item_info, art=art, playable=True),
            isFolder=False,
        )

    if not results:
        _error("Sin enlaces de descarga.", xbmcgui.NOTIFICATION_WARNING, 6000)
    xbmcplugin.endOfDirectory(HANDLE)


def _detail_wf(url, kind, title):
    """Ficha WolfMax4K: torrents via enlacito."""
    try:
        d = wf.detail(url)
    except Exception as e:
        xbmc.log(f"[MejorWolf] wf detail error: {e}", xbmc.LOGERROR)
        _error(f"Error WolfMax: {type(e).__name__}")
        xbmcplugin.endOfDirectory(HANDLE)
        return

    alt  = (lambda t=d.get("title"): t) if d.get("title") else None
    # Si el titulo tiene marca de capitulo es contenido seriado -> TMDB tv,
    # asi "Rafa [Cap.101]" encuentra el documental seriado (2026) y no la
    # peli portuguesa de 2012. Mismo criterio que en _enrich_one.
    wf_kind = _tmdb_kind(kind)
    if re.search(r"\b[Cc]ap\.?\s*\d+|\b[Ss]\d{1,2}[Ee]\d{1,3}\b", title or ""):
        wf_kind = "tv"
    meta = tmdb.enrich(title, kind=wf_kind, alt_title_fn=alt)
    # Portada: TMDB (image.tmdb.org carga en Kodi). La imagen propia de
    # WolfMax esta tras Cloudflare (403) -> NO usarla.
    art = {}
    if meta.get("poster"):
        art["poster"] = meta["poster"]; art["thumb"] = meta["poster"]
    if meta.get("fanart"):
        art["fanart"] = meta["fanart"]
    info_base = _build_info_base(meta, d)
    year_str = f" ({meta.get('year') or d.get('year', '')})" if (meta.get("year") or d.get("year")) else ""

    has_episodes = any(x.get("season") is not None for x in d["downloads"])
    movie_label  = (d.get("title") or title or "").strip() or "Descarga"

    for dl in d["downloads"]:
        if has_episodes and dl.get("season") is None:
            continue
        if has_episodes:
            label = dl["label"]
            mtype = "episode"
        else:
            label = movie_label + year_str
            extras = dl.get("label", "")
            if extras and extras.lower() not in ("descargar", "descargar ahora",
                                                  movie_label.lower()):
                label = f"{label} - {extras}"
            mtype = "movie"
        info = dict(info_base, title=label, mediatype=mtype)
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="play", torrent=dl["torrent_url"]),
            _li(label, info=info, art=art, playable=True),
            isFolder=False,
        )

    if not d["downloads"]:
        _error("Sin enlaces de descarga.", xbmcgui.NOTIFICATION_WARNING, 6000)
    xbmcplugin.endOfDirectory(HANDLE)


def _build_art(meta, d):
    art = {}
    if meta.get("poster"):
        art["poster"] = meta["poster"]
        art["thumb"]  = meta["poster"]
    if meta.get("fanart"):
        art["fanart"] = meta["fanart"]
    img = d.get("image") or d.get("thumb")
    if img:
        art.setdefault("thumb",  img)
        art.setdefault("poster", img)
    return art


def _build_info_base(meta, d):
    return {
        "plot":   meta.get("plot") or d.get("plot"),
        "year":   meta.get("year") or d.get("year"),
        "rating": meta.get("rating"),
    }


# ── Reproduccion ────────────────────────────────────────────────────────────

def _elementum_installed():
    try:
        xbmcaddon.Addon("plugin.video.elementum")
        return True
    except Exception:
        return False


def _check_elementum():
    if not _elementum_installed():
        xbmcgui.Dialog().ok(
            "MejorWolf",
            "Elementum no esta instalado o esta deshabilitado.\n"
            "Instala 'plugin.video.elementum' desde su repositorio.",
        )
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return False
    return True


def play(torrent_url):
    """Reproduce un magnet o URL de .torrent via Elementum."""
    if not torrent_url:
        _error("URL de torrent vacia")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    if not _check_elementum():
        return

    xbmc.log(f"[MejorWolf] play: {torrent_url[:160]}", xbmc.LOGINFO)

    progress = xbmcgui.DialogProgressBG()
    try:
        progress.create("MejorWolf", "Obteniendo torrent...")
    except Exception:
        progress = None

    # Enlacito shortener
    if enlacito.is_enlacito_url(torrent_url):
        try:
            if progress:
                progress.update(20, "MejorWolf", "Resolviendo enlacito...")
        except Exception:
            pass
        resolved = enlacito.resolve(torrent_url)
        if resolved:
            torrent_url = resolved
    low = torrent_url.lower()

    # HTTP URL → fetch .torrent → magnet
    if low.startswith(("http://", "https://")):
        try:
            sess = hs.make_session()
            r = hs.get(sess, torrent_url, timeout=30)
            data = r.content or b""
            magnet = tparse.torrent_to_magnet(data)
            if magnet:
                torrent_url = magnet
                low = magnet.lower()
            else:
                text = data.decode("utf-8", "replace")
                mm = re.search(r'(magnet:\?[^"\'<>\s]+)', text)
                if mm:
                    torrent_url = mm.group(1)
                    low = torrent_url.lower()
                else:
                    mm = re.search(r'href="([^"]+\.torrent[^"]*)"', text, re.I)
                    if mm:
                        from urllib.parse import urljoin as _uj
                        inner = _uj(torrent_url, mm.group(1))
                        r2 = hs.get(sess, inner, timeout=30)
                        magnet = tparse.torrent_to_magnet(r2.content or b"")
                        if magnet:
                            torrent_url = magnet
                            low = magnet.lower()
        except Exception as e:
            xbmc.log(f"[MejorWolf] torrent fetch: {e}", xbmc.LOGWARNING)

    if not (low.startswith("magnet:") or low.endswith(".torrent")
            or low.startswith("file://")):
        try:
            if progress:
                progress.close()
        except Exception:
            pass
        _error("No se pudo obtener el torrent.")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    play_url = player.elementum_url(torrent_url)
    try:
        if progress:
            progress.close()
    except Exception:
        pass

    item = xbmcgui.ListItem(path=play_url)
    item.setProperty("IsPlayable", "true")
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def dt_play(content_id, tabla, page_url=""):
    """Resuelve PoW de DonTorrent y reproduce."""
    if not content_id or not tabla:
        _error("Faltan datos de descarga")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return
    if not _check_elementum():
        return

    progress = xbmcgui.DialogProgressBG()
    try:
        progress.create("MejorWolf", "Resolviendo descarga...")
    except Exception:
        progress = None

    try:
        try:
            if progress:
                progress.update(30, "MejorWolf", "Resolviendo PoW...")
        except Exception:
            pass

        torrent_url = dt.resolve_torrent(content_id, tabla, page_url=page_url)
        if not torrent_url:
            raise RuntimeError("No se obtuvo URL del torrent")

        xbmc.log(f"[MejorWolf] dt resolved: {torrent_url[:160]}", xbmc.LOGINFO)

        try:
            if progress:
                progress.update(60, "MejorWolf", "Descargando torrent...")
        except Exception:
            pass

        # Descargar .torrent y guardarlo en temp para que Elementum
        # lo lea directamente (evita timeout resolviendo magnet via DHT)
        try:
            if progress:
                progress.update(60, "MejorWolf", "Descargando torrent...")
        except Exception:
            pass

        torrent_data = None
        try:
            # Intentar descargar via render relay (bypass ISP)
            relay_url = dt._render_relay_url()
            if relay_url:
                from urllib.parse import quote as _q
                fetch_url = f"{relay_url}/dtfetch?u={_q(torrent_url, safe='')}"
                import requests as _rq
                rr = _rq.get(fetch_url, timeout=30)
                if rr.status_code == 200 and len(rr.content) > 100:
                    torrent_data = rr.content
        except Exception:
            pass

        if not torrent_data:
            try:
                sess = hs.make_session()
                r = hs.get(sess, torrent_url, timeout=30)
                torrent_data = r.content
            except Exception:
                pass

        # Guardar .torrent en temp y pasar file:// a Elementum
        # Esto evita el problema de "Expired timeout for resolving magnet"
        # porque Elementum tiene la metadata completa del torrent de inmediato.
        play_uri = None
        if torrent_data and len(torrent_data) > 100:
            try:
                import xbmcvfs
                temp_dir = xbmcvfs.translatePath("special://temp/")
                torrent_file = os.path.join(temp_dir, "mejorwolf_play.torrent")
                with open(torrent_file, "wb") as tf:
                    tf.write(torrent_data)
                play_uri = torrent_file
                xbmc.log(f"[MejorWolf] torrent saved to {torrent_file} "
                         f"({len(torrent_data)} bytes)", xbmc.LOGINFO)
            except Exception as e:
                xbmc.log(f"[MejorWolf] save torrent failed: {e}",
                         xbmc.LOGWARNING)

        # Fallback: magnet URI (puede tardar 60s+ en resolver metadata)
        if not play_uri:
            magnet = tparse.torrent_to_magnet(torrent_data or b"")
            play_uri = magnet or torrent_url

        try:
            if progress:
                progress.close()
        except Exception:
            pass

        play_url = player.elementum_url(play_uri)
        item = xbmcgui.ListItem(path=play_url)
        item.setProperty("IsPlayable", "true")
        xbmcplugin.setResolvedUrl(HANDLE, True, item)

    except Exception as e:
        try:
            if progress:
                progress.close()
        except Exception:
            pass
        xbmc.log(f"[MejorWolf] dt_play error: {e}", xbmc.LOGERROR)
        _error(f"Error: {e}")
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


# ── Busqueda combinada ─────────────────────────────────────────────────────

_SHOW_MARKERS_RE = re.compile(
    r"\[[^\]]*\]"
    r"|\([^)]*\)"
    r"|\bS\d{1,2}E\d{1,3}\b"
    r"|\b\d{1,2}\s*[xX]\s*\d{1,3}\b"
    r"|\bCap[ií]?tulos?\s*\d+\b"
    r"|\bTemporada\s*\d+\b"
    r"|\bEpisodios?\s*\d+\b"
    r"|\b(?:HDTV|WEB-?DL|WEB-?Rip|BluRay|BDRip|BRRip|HEVC|x265|x264|"
    r"4K\s*UHD|4K|2160p|1080p|720p|480p|HDR10\+?|HDR|DV|Dolby\s*Vision|"
    r"Remux|BDRemux|Latino|Castellano|Dual|VOSE|Esp|Ing|MicroHD)\b",
    re.I,
)


def _norm_for_group(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().strip())


def _series_base(title):
    if not title:
        return ""
    t = _SHOW_MARKERS_RE.sub("", title)
    t = re.sub(r"\s+", " ", t).strip(" -.|:")
    return t


def _ep_key(title):
    if not title:
        return (99, 99)
    m = re.search(r"[Cc]ap\.?\s*(\d{1,3})(\d{2})", title)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"\b(\d{1,2})\s*[xX]\s*(\d{1,3})\b", title)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"\bS(\d{1,2})E(\d{1,3})\b", title, re.I)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"[Cc]ap(?:[ií]?tulos?)?\.?\s*(\d{1,3})\b", title)
    if m:
        return (1, int(m.group(1)))
    return (99, 99)


def _ep_label(it):
    title = it.get("title", "")
    s, e = _ep_key(title)
    if s == 99:
        return _display_title(it)
    quality = it.get("quality")
    if not quality:
        m = re.search(r"\[([^\]]+)\]", title)
        if m:
            quality = m.group(1).strip()
    label = f"{s:02d}x{e:02d}"
    if quality:
        label = f"{label} [{quality}]"
    return label


def _group_results(items):
    order = []
    groups = {}
    for it in items:
        title = it.get("title") or ""
        base = _series_base(title)
        if not base:
            base = title.strip() or "Sin titulo"
        src = it.get("source") or "?"
        nb = _norm_for_group(base)
        key = f"{src}|{nb}"
        if key not in groups:
            groups[key] = {
                "key": key, "base": base,
                "kind": it.get("kind") or "movie",
                "source": src, "items": [],
            }
            order.append(key)
        groups[key]["items"].append(it)

    for g in groups.values():
        has_ep = any(_ep_key(it.get("title", ""))[0] != 99 for it in g["items"])
        if has_ep:
            best = next((it.get("kind") for it in g["items"]
                         if (it.get("kind") or "").startswith("tvshow")), "tvshow")
            g["kind"] = best
        g["items"].sort(key=lambda it: (_ep_key(it.get("title", "")),
                                        (it.get("title") or "").lower()))
    return [groups[k] for k in order]


def _extract_series_name(title):
    """Extrae nombre base de serie limpiando marcas de calidad/episodio.

    'Separacion [HDTV 1080p][Cap.201]'       -> 'Separacion'
    'The Last of Us 1x03 [720p]'              -> 'The Last of Us'
    'Arcane [4K 2160p][Cap.101]'              -> 'Arcane'
    'Separacion - Temporada 1 [HDTV 1080p]'   -> 'Separacion'
    'La Casa del Dragon T2 [1080p]'           -> 'La Casa del Dragon'
    """
    t = title or ""
    # Quitar todo entre corchetes
    t = re.sub(r"\[.*?\]", "", t)
    # Quitar todo entre paréntesis con contenido técnico
    t = re.sub(r"\((?:720p|1080p|2160p|4[Kk]|HDRip|HDTV|Bluray|MicroHD|"
               r"WEB-?DL|DVDRip|BDRemux|Remux)[^)]*\)", "", t, flags=re.I)
    # Quitar "- Temporada N", "- Nª Temporada", "Temporada N"
    # Cubre: "Temporada 1", "1ª Temporada", "1a Temporada", "- 2º Temporada"
    t = re.sub(r"\s*-?\s*[Tt]emporada\s+\d+", "", t)
    t = re.sub(r"\s*-?\s*\d+[ªºaAoO]?\s*[Tt]emporada\b", "", t)
    # Quitar "T1", "T2" etc. (abreviatura española de temporada)
    t = re.sub(r"\s+T\d{1,2}\b", "", t)
    # Quitar patrones de episodio: Cap.NNN, SxxExx, NxNN
    t = re.sub(r"\b[Cc]ap\.?\s*\d+", "", t)
    t = re.sub(r"\b[Ss]\d{1,2}[Ee]\d{1,3}\b", "", t)
    t = re.sub(r"\b\d{1,2}x\d{2,3}\b", "", t)
    # Quitar año entre paréntesis
    t = re.sub(r"\(\d{4}\)", "", t)
    # Quitar calidades sueltas
    t = re.sub(r"\b(?:720p|1080p|2160p|4[Kk]|HDRip|HDTV|Bluray|BluRay|"
               r"MicroHD|WEB-?DL|DVDRip|BDRemux|Remux)\b",
               "", t, flags=re.IGNORECASE)
    # Limpiar separadores y espacios residuales
    t = re.sub(r"[\s\-–_.]+$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _cache_series_groups(cache_key, groups):
    """Guarda los grupos de series en Window properties de Kodi.

    Las Window properties persisten entre invocaciones del plugin
    dentro de la misma sesion de Kodi — exactamente lo que necesitamos
    para que el usuario pueda entrar en un grupo desde search().
    """
    data = {}
    for sname, items_list in groups.items():
        data[sname] = [
            {"title": it.get("title", ""), "url": it.get("url", ""),
             "kind": it.get("kind", ""), "source": it.get("source", ""),
             "thumb": it.get("thumb", ""), "image": it.get("image", ""),
             "quality": it.get("quality", "")}
            for it in items_list
        ]
    try:
        win = xbmcgui.Window(10000)
        win.setProperty(f"mw_sg_{cache_key}", json.dumps(data))
    except Exception as e:
        xbmc.log(f"[MejorWolf] cache_series_groups error: {e}",
                 xbmc.LOGWARNING)


def _get_cached_series_group(cache_key, series_name):
    """Recupera items de un grupo de series desde la cache."""
    try:
        win = xbmcgui.Window(10000)
        raw = win.getProperty(f"mw_sg_{cache_key}")
        if not raw:
            return []
        data = json.loads(raw)
        return data.get(series_name, [])
    except Exception:
        return []


def search(filter_kind=None):
    """Busqueda combinada en las 3 fuentes.

    Muestra resultados de DonTorrent, EliteTorrent y WolfMax4K.
    Las series de TODAS las fuentes se agrupan por nombre (como Tacones).
    """
    # Prerellenar con la ultima busqueda (comodidad)
    kb = xbmc.Keyboard(_last_search_load(), "Buscar en MejorWolf")
    kb.doModal()
    if not kb.isConfirmed():
        xbmcplugin.endOfDirectory(HANDLE)
        return
    q = kb.getText().strip()
    if not q:
        xbmcplugin.endOfDirectory(HANDLE)
        return
    _last_search_save(q)

    # Buscar en paralelo en las 3 fuentes con hilos DAEMON.
    # CLAVE: usar hilos daemon (no ThreadPoolExecutor) para que un hilo
    # lento/colgado (p.ej. relay frio) NUNCA bloquee el cierre y congele
    # la busqueda. La busqueda devuelve EXACTAMENTE en el deadline de cada
    # fuente pase lo que pase con los rezagados.
    import threading
    all_items = []
    source_counts = {}
    _results = {}                  # label -> [items]
    _events = {lbl: threading.Event() for lbl in ("DT", "ET", "WF")}

    def _search_src(label, fn):
        try:
            res = fn(q) or []
        except Exception as e:
            res = []
            xbmc.log(f"[MejorWolf] search {label} ERROR: {e}",
                     xbmc.LOGWARNING)
            import traceback
            xbmc.log(f"[MejorWolf] search {label} traceback: "
                     f"{traceback.format_exc()}", xbmc.LOGWARNING)
        _results[label] = res
        source_counts[label] = len(res)
        xbmc.log(f"[MejorWolf] search {label}: {len(res)} resultados",
                 xbmc.LOGINFO)
        _events[label].set()

    # 1) Lanzamos YA los hilos (la propia peticion de DT despierta el relay).
    start = time.time()
    for lbl, fn in (("DT", dt.search), ("ET", et.search), ("WF", wf.search)):
        threading.Thread(target=_search_src, args=(lbl, fn),
                         daemon=True).start()

    # 2) En PARALELO sondeamos el calor del relay para decidir cuanto esperar
    # a DonTorrent. CLAVE: el deadline es un MAXIMO, no una espera fija -> en
    # cuanto DT responde salimos. Con el relay CALIENTE (DT ~2-3s) un deadline
    # alto NO cuesta nada; solo en FRIO (relay dormido, free tier de Render)
    # esperamos mas para que DonTorrent llegue (es innegociable). El sondeo
    # corre solapado con los hilos, asi que no añade latencia en caliente.
    try:
        warmth = dt.relay_warmth(timeout=3.0)
    except Exception:
        warmth = "warm"
    if warmth == "cold":
        dt_to = 55     # relay despertando (~50s): dale tiempo, DT debe salir
    elif warmth == "down":
        dt_to = 10     # relay no disponible: no esperes de balde
    else:
        dt_to = 12

    # Deadline POR FUENTE. DT lleva el margen mayor (adaptativo segun calor);
    # ET y WF no deben hacer esperar.
    PER_SOURCE_TIMEOUT = {"DT": dt_to, "ET": 8, "WF": 7}

    progress_dlg = None
    try:
        progress_dlg = xbmcgui.DialogProgressBG()
        msg = (f"Despertando buscador (1a vez)... '{q}'"
               if warmth == "cold" else f"Buscando '{q}'...")
        progress_dlg.create("MejorWolf", msg)
    except Exception:
        progress_dlg = None

    # Esperamos a cada fuente hasta SU deadline (medido desde el mismo
    # inicio). Como corren en paralelo, el tiempo total queda acotado por
    # el deadline mayor (~9s) y, con todo caliente, por la fuente mas lenta.
    completed_labels = []
    for lbl in ("DT", "ET", "WF"):
        remaining = PER_SOURCE_TIMEOUT[lbl] - (time.time() - start)
        if remaining > 0:
            _events[lbl].wait(remaining)
        if _events[lbl].is_set():
            all_items.extend(_results.get(lbl, []))
            completed_labels.append(lbl)
        else:
            source_counts[lbl] = -1
            xbmc.log(f"[MejorWolf] search {lbl}: TIMEOUT "
                     f"{PER_SOURCE_TIMEOUT[lbl]}s (relay frio?)",
                     xbmc.LOGWARNING)
        if progress_dlg:
            try:
                progress_dlg.update(
                    min(99, 33 * len(completed_labels)), "MejorWolf",
                    f"Completadas: {', '.join(completed_labels) or '...'}",
                )
            except Exception:
                pass

    if progress_dlg:
        try:
            progress_dlg.close()
        except Exception:
            pass

    xbmc.log(f"[MejorWolf] search totals: DT={source_counts.get('DT',0)} "
             f"ET={source_counts.get('ET',0)} WF={source_counts.get('WF',0)} "
             f"total_raw={len(all_items)}", xbmc.LOGINFO)

    # ── Filtrar por tipo (movie / tvshow) ─────────────────────────────
    if filter_kind:
        base_fk = filter_kind.split("_")[0]   # "movie" / "tvshow"
        all_items = [i for i in all_items
                     if (i.get("kind") or "movie").startswith(base_fk)]

    # Deduplicar por URL
    seen, items = set(), []
    for it in all_items:
        u = it.get("url", "")
        if u not in seen:
            seen.add(u)
            items.append(it)

    xbmc.log(f"[MejorWolf] search '{q}' -> {len(items)} resultados unicos",
             xbmc.LOGINFO)

    if not items:
        _error(f"Sin resultados para: {q}",
               xbmcgui.NOTIFICATION_WARNING, 4000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    # ── Resultados organizados por fuente ────────────────────────────
    # Nivel 1: una carpeta por fuente con su contador
    #   DonTorrent (12 resultados)
    #   EliteTorrent (25 resultados)
    #   WolfMax4K (8 resultados)
    # Dentro de cada carpeta: peliculas y series agrupadas por nombre.
    cache_key = re.sub(r"[^a-zA-Z0-9]", "_", q.lower())[:40]
    by_source = {"dt": [], "et": [], "wf": []}
    for it in items:
        src = it.get("source", "dt")
        by_source.setdefault(src, []).append(it)

    # Cache items por fuente para que show_source_results los recupere
    _cache_source_items(cache_key, by_source)

    xbmcplugin.setContent(HANDLE, "videos")
    xbmcplugin.setPluginCategory(HANDLE, f"Busqueda: {q}")

    # Orden: DT primero (mas catalogo), luego ET, luego WF
    order = [("dt", "DonTorrent"), ("et", "EliteTorrent"), ("wf", "WolfMax4K")]
    for src, src_label in order:
        src_items = by_source.get(src, [])
        if not src_items:
            continue
        n = len(src_items)
        label = f"{src_label}  ({n} resultados)"
        action_url = _u(action="source_results", src=src,
                       cache_key=cache_key, q=q)
        # Usar icono basado en tipo dominante
        movies = sum(1 for i in src_items
                     if not (i.get("kind", "movie").startswith("tvshow")))
        is_movies_dominant = movies > n / 2
        icon = IC["movie"] if is_movies_dominant else IC["tvshow"]
        li = _li(label, info={"title": label, "plot": f"{n} resultados de "
                              f"'{q}' en {src_label}"}, icon=icon)
        xbmcplugin.addDirectoryItem(HANDLE, action_url, li, isFolder=True)

    xbmc.log(f"[MejorWolf] search display: {sum(len(v) for v in by_source.values())} "
             f"items en {sum(1 for v in by_source.values() if v)} fuentes",
             xbmc.LOGINFO)
    xbmcplugin.endOfDirectory(HANDLE)


def _cache_source_items(cache_key, by_source):
    """Cachea items por fuente en Window properties."""
    data = {}
    for src, src_items in by_source.items():
        data[src] = [
            {"title": it.get("title", ""), "url": it.get("url", ""),
             "kind": it.get("kind", ""), "source": it.get("source", ""),
             "thumb": it.get("thumb", ""), "image": it.get("image", ""),
             "quality": it.get("quality", "")}
            for it in src_items
        ]
    try:
        win = xbmcgui.Window(10000)
        win.setProperty(f"mw_src_{cache_key}", json.dumps(data))
    except Exception as e:
        xbmc.log(f"[MejorWolf] cache_source_items error: {e}",
                 xbmc.LOGWARNING)


def _get_cached_source_items(cache_key, src):
    """Recupera items de una fuente desde la cache."""
    try:
        win = xbmcgui.Window(10000)
        raw = win.getProperty(f"mw_src_{cache_key}")
        if not raw:
            return []
        data = json.loads(raw)
        return data.get(src, [])
    except Exception:
        return []


def show_source_results(src, cache_key, q=""):
    """Muestra los resultados de una fuente concreta.

    Dentro de cada fuente:
      - Peliculas/documentales: individuales con su titulo y portada TMDB
      - Series: agrupadas por nombre base (estilo Tacones)
    """
    items_data = _get_cached_source_items(cache_key, src)

    # Si cache miss y tenemos query, re-buscar SOLO esa fuente
    if not items_data and q:
        xbmc.log(f"[MejorWolf] source_results: cache miss, re-buscando "
                 f"{src} '{q}'", xbmc.LOGINFO)
        scraper_map = {"dt": dt, "et": et, "wf": wf}
        scraper = scraper_map.get(src)
        if scraper:
            try:
                items_data = scraper.search(q) or []
            except Exception as e:
                xbmc.log(f"[MejorWolf] re-search {src} error: {e}",
                         xbmc.LOGERROR)

    if not items_data:
        _error(f"Sin resultados en {SOURCE_LABEL.get(src, src)}",
               xbmcgui.NOTIFICATION_WARNING, 4000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    src_label = SOURCE_LABEL.get(src, src)
    xbmcplugin.setPluginCategory(HANDLE, f"{src_label}: {q}")

    # Separar individuales (peliculas/docs) de series agrupables.
    # Un item es "agrupable como serie" si:
    #   a) su kind es tvshow, O
    #   b) su titulo tiene marca de capitulo/episodio (Cap.N, SxxExx, NxNN).
    # Esto cubre documentales/series de WolfMax cuya URL /online/<id> se
    # clasifica como "movie" pero que en realidad son capitulos (ej. "Rafa").
    _CAP_MARK = re.compile(
        r"\b[Cc]ap\.?\s*\d+|\b[Ss]\d{1,2}[Ee]\d{1,3}\b|\b\d{1,2}x\d{2,3}\b",
        re.IGNORECASE)
    individual = []
    series_groups = {}
    for it in items_data:
        kind = it.get("kind", "movie")
        title = it.get("title", "")
        is_episode = kind.startswith("tvshow") or bool(_CAP_MARK.search(title))
        if is_episode:
            sname = _extract_series_name(title)
            if sname:
                series_groups.setdefault(sname, []).append(it)
            else:
                individual.append(it)
        else:
            individual.append(it)

    # Grupos de 1 item → individual
    multi_groups = {}
    for sname, group_items in series_groups.items():
        if len(group_items) == 1:
            individual.append(group_items[0])
        else:
            multi_groups[sname] = group_items

    # Cachear grupos para series_group action (reutiliza el sistema existente)
    if multi_groups:
        sg_key = f"{cache_key}_{src}"
        _cache_series_groups(sg_key, multi_groups)
    else:
        sg_key = cache_key

    # Enriquecer con TMDB
    enriched_ind = _enrich_many(individual) if individual else []
    group_list = list(multi_groups.items())
    group_reps = [g[0] for g in (v for _, v in group_list)] if group_list else []
    enriched_groups = _enrich_many(group_reps) if group_reps else []

    # Determinar tipo de contenido
    has_movies = any(_media_type(it.get("kind", "movie")) == "movie"
                     for it in items_data)
    has_tvshows = any(_media_type(it.get("kind", "movie")) == "tvshow"
                      for it in items_data)
    if has_tvshows and not has_movies:
        xbmcplugin.setContent(HANDLE, "tvshows")
    elif has_movies and not has_tvshows:
        xbmcplugin.setContent(HANDLE, "movies")
    else:
        xbmcplugin.setContent(HANDLE, "videos")

    # Mostrar items individuales
    for it, (info, art) in zip(individual, enriched_ind):
        label = info["title"]
        kind = it.get("kind", "movie")
        action_url = _u(action="detail", src=src, url=it["url"],
                       kind=kind, title=it["title"])
        xbmcplugin.addDirectoryItem(
            HANDLE, action_url,
            _li(label, info=info, art=art),
            isFolder=True)

    # Mostrar series agrupadas (carpeta por serie con N caps)
    for (sname, group_items), (info, art) in zip(group_list, enriched_groups):
        n = len(group_items)
        label = f"{sname}  ({n} resultados)"
        action_url = _u(action="series_group", series_name=sname,
                       cache_key=f"{cache_key}_{src}", q=q)
        li = _li(label, info=dict(info, title=label,
                                  mediatype="tvshow"), art=art)
        xbmcplugin.addDirectoryItem(HANDLE, action_url, li, isFolder=True)

    xbmc.log(f"[MejorWolf] source_results {src}: "
             f"{len(individual)} individuales + {len(group_list)} series",
             xbmc.LOGINFO)
    xbmcplugin.endOfDirectory(HANDLE)


def show_series_group(series_name, cache_key, q=""):
    """Muestra los items individuales de un grupo de series.

    Cuando el usuario entra en una carpeta de serie agrupada,
    ve todos los resultados de esa serie (temporadas, capitulos)
    de todas las fuentes, cada uno con su enlace de detalle.
    """
    items_data = _get_cached_series_group(cache_key, series_name)

    if not items_data:
        # Cache expirada o perdida: re-buscar y filtrar
        xbmc.log(f"[MejorWolf] series_group: cache miss, re-buscando '{q}'",
                 xbmc.LOGINFO)
        if q:
            re_items = []
            try:
                with ThreadPoolExecutor(max_workers=3) as ex:
                    futs = [
                        ex.submit(lambda: dt.search(q) or []),
                        ex.submit(lambda: et.search(q) or []),
                        ex.submit(lambda: wf.search(q) or []),
                    ]
                    for fut in futs:
                        re_items.extend(fut.result() or [])
            except Exception:
                pass
            items_data = []
            for it in re_items:
                kind = it.get("kind", "movie")
                if kind.startswith("tvshow"):
                    sname = _extract_series_name(it.get("title", ""))
                    if sname == series_name:
                        items_data.append(it)

    if not items_data:
        _error(f"No se encontraron resultados para: {series_name}",
               xbmcgui.NOTIFICATION_WARNING, 4000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    xbmcplugin.setPluginCategory(HANDLE, series_name)
    xbmcplugin.setContent(HANDLE, "tvshows")

    # Ordenar por capitulo/episodio (Cap.101, 102... o SxxExx). Asi los
    # capitulos salen 1x01, 1x02... en orden y no revueltos.
    items_data = sorted(items_data,
                        key=lambda it: _ep_key(it.get("title", "")))

    # Todos los capitulos son la MISMA serie -> enriquecer TMDB UNA sola vez
    # (con el primer item) y reusar portada/info para todos. Evita N llamadas
    # TMDB (4 caps de Rafa = 1 llamada en vez de 4 -> mucho mas rapido).
    shared_info, shared_art = _enrich_one(items_data[0])

    for it in items_data:
        src = it.get("source", "dt")
        src_tag = SOURCE_LABEL.get(src, src)
        quality = it.get("quality", "")
        quality_tag = f" [{quality}]" if quality else ""

        raw_title = it.get("title", series_name)
        url = it.get("url", "")
        kind = it.get("kind", "tvshow")
        info = dict(shared_info)
        art = shared_art

        # Etiqueta: si detectamos numero de capitulo, mostrar "Cap. N" claro
        s, e = _ep_key(raw_title)
        if e != 99:
            cap_label = f"Cap. {e}" if s in (1, 99) else f"{s}x{e:02d}"
            label = f"{cap_label}{quality_tag}  ({src_tag})"
        else:
            label = f"{raw_title}{quality_tag}  ({src_tag})"

        # WolfMax: si la URL ya es un capitulo reproducible (/online/<id>),
        # vamos DIRECTO a reproducir (no re-buscar la serie -> evita 40s).
        if src == "wf":
            if re.search(r"/(online|movie|capitulo|episodio)/\d+", url):
                action_url = _u(action="detail", src="wf", url=url,
                               kind=kind, title=raw_title)
            else:
                action_url = _u(action="wf_series", q=raw_title,
                               seed_url=url, title=series_name)
        else:
            action_url = _u(action="detail", src=src, url=url,
                           kind=kind, title=raw_title)

        xbmcplugin.addDirectoryItem(
            HANDLE, action_url,
            _li(label, info=dict(info, title=label), art=art),
            isFolder=True)

    xbmc.log(f"[MejorWolf] series_group '{series_name}': "
             f"{len(items_data)} items", xbmc.LOGINFO)
    xbmcplugin.endOfDirectory(HANDLE)


# ── WolfMax: jerarquia Serie > Temporadas > Capitulos ──────────────────────

_SERIES_CACHE_DIR = None


def _series_cache_dir():
    global _SERIES_CACHE_DIR
    if _SERIES_CACHE_DIR is None:
        d = xbmcvfs.translatePath("special://temp/mejorwolf_cache/")
        os.makedirs(d, exist_ok=True)
        _SERIES_CACHE_DIR = d
    return _SERIES_CACHE_DIR


def _cache_key(q):
    import hashlib
    h = hashlib.md5(q.lower().strip().encode("utf-8")).hexdigest()[:12]
    return f"wf_series_{h}.json"


def _save_series_cache(q, data):
    try:
        path = os.path.join(_series_cache_dir(), _cache_key(q))
        payload = {"ts": time.time(), "q": q, "data": data}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        xbmc.log(f"[MejorWolf] cache save err: {e}", xbmc.LOGWARNING)


def _load_series_cache(q, max_age=600):
    try:
        path = os.path.join(_series_cache_dir(), _cache_key(q))
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if time.time() - payload.get("ts", 0) > max_age:
            return None
        data = payload.get("data")
        if data and "seasons" in data:
            data["seasons"] = {int(k): v for k, v in data["seasons"].items()}
        return data
    except Exception as e:
        xbmc.log(f"[MejorWolf] cache load err: {e}", xbmc.LOGWARNING)
        return None


def wf_series(q, seed_url="", title=""):
    clean_q = _series_base(q) or q
    data = _load_series_cache(clean_q)
    if not data:
        progress = xbmcgui.DialogProgressBG()
        progress.create("MejorWolf", "Buscando todos los capitulos...")
        try:
            progress.update(20, "MejorWolf", "Buscando y expandiendo...")
            expanded = wf.search_and_expand(clean_q)
            data = {
                "title":   expanded.get("title", title or clean_q),
                "image":   expanded.get("image"),
                "seasons": expanded.get("seasons", {}),
                "total":   sum(len(v) for v in expanded.get("seasons", {}).values()),
            }
        except Exception as e:
            xbmc.log(f"[MejorWolf] wf_series error: {e}", xbmc.LOGERROR)
            data = {"title": title or clean_q, "image": None, "seasons": {}, "total": 0}
        finally:
            try:
                progress.close()
            except Exception:
                pass
        _save_series_cache(clean_q, data)

    seasons = data.get("seasons", {})
    series_title = data.get("title") or title or q

    if not seasons:
        _error(f"No se encontraron capitulos para: {q}",
               xbmcgui.NOTIFICATION_WARNING, 5000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    meta = tmdb.enrich(series_title, kind="tv")
    art = _build_art(meta, data)
    xbmcplugin.setPluginCategory(HANDLE, series_title)

    if len(seasons) == 1:
        s_num = list(seasons.keys())[0]
        _show_season_episodes(seasons[s_num], series_title, s_num, art, meta)
        return

    xbmcplugin.setContent(HANDLE, "seasons")
    for s_num in sorted(seasons.keys()):
        eps = seasons[s_num]
        n = len(eps)
        label = f"Temporada {s_num}  ({n} capitulo{'s' if n != 1 else ''})"
        info = {
            "title": label, "plot": meta.get("plot"),
            "year": meta.get("year"), "mediatype": "season",
        }
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="wf_season", q=clean_q, season=str(s_num),
               title=series_title, seed_url=seed_url),
            _li(label, info=info, art=art),
            isFolder=True,
        )
    xbmcplugin.endOfDirectory(HANDLE)


def wf_season(q, season, title="", seed_url=""):
    s_num = int(season)
    clean_q = _series_base(q) or q
    data = _load_series_cache(clean_q)
    if not data:
        progress = xbmcgui.DialogProgressBG()
        progress.create("MejorWolf", f"Cargando Temporada {s_num}...")
        try:
            expanded = wf.search_and_expand(clean_q)
            data = {
                "title":   expanded.get("title", title or clean_q),
                "image":   expanded.get("image"),
                "seasons": expanded.get("seasons", {}),
                "total":   sum(len(v) for v in expanded.get("seasons", {}).values()),
            }
            _save_series_cache(clean_q, data)
        except Exception as e:
            xbmc.log(f"[MejorWolf] wf_season error: {e}", xbmc.LOGERROR)
            data = {"title": title or clean_q, "image": None, "seasons": {}}
        finally:
            try:
                progress.close()
            except Exception:
                pass

    episodes = data.get("seasons", {}).get(s_num, [])
    series_title = data.get("title") or title or q

    if not episodes:
        _error(f"Sin capitulos de la temporada {s_num}",
               xbmcgui.NOTIFICATION_WARNING, 5000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    meta = tmdb.enrich(series_title, kind="tv")
    art = _build_art(meta, data)
    _show_season_episodes(episodes, series_title, s_num, art, meta)


def _show_season_episodes(episodes, series_title, s_num, art, meta):
    xbmcplugin.setPluginCategory(HANDLE, f"{series_title} - Temporada {s_num}")
    xbmcplugin.setContent(HANDLE, "episodes")
    for ep in episodes:
        e_num = ep.get("episode", 0)
        quality = ep.get("quality") or ""
        ep_title = ep.get("title", "")
        label = f"{s_num}x{e_num:02d}"
        clean = re.sub(r"\[.*?\]|\(.*?\)", "", ep_title).strip()
        base_norm = re.sub(r"\s+", " ", series_title.lower().strip())
        clean_norm = re.sub(r"\s+", " ", clean.lower().strip())
        if clean_norm.startswith(base_norm):
            clean = clean[len(series_title):].strip(" -:|")
        if clean and clean.lower() != series_title.lower():
            label = f"{label} - {clean}"
        if quality:
            label = f"{label} [{quality}]"
        ep_art = dict(art)
        if ep.get("image"):
            ep_art["thumb"] = ep["image"]
        info = {
            "title": label, "tvshowtitle": series_title,
            "plot": meta.get("plot"), "year": meta.get("year"),
            "mediatype": "episode", "season": s_num, "episode": e_num,
        }
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="detail", src="wf", url=ep["url"],
               kind="tvshow", title=ep_title),
            _li(label, info=info, art=ep_art),
            isFolder=True,
        )
    xbmcplugin.endOfDirectory(HANDLE)


# ── WolfMax: A-Z e Indice ──────────────────────────────────────────────────

def wf_az_menu(filter_kind=None, letter=None):
    label_kind = {"movie": "Peliculas", "tvshow": "Series"}.get(filter_kind, "Todo")
    if not letter:
        xbmcplugin.setPluginCategory(HANDLE, f"A-Z {label_kind}")
        letters = wf.az_letters(kind_filter=filter_kind)
        if not letters:
            xbmcgui.Dialog().ok(
                "MejorWolf",
                "El indice esta vacio. Ejecuta 'Reconstruir indice' "
                "desde Ajustes, o navega un poco por los listados.",
            )
            xbmcplugin.endOfDirectory(HANDLE)
            return
        for letra, count in letters:
            xbmcplugin.addDirectoryItem(
                HANDLE,
                _u(action="wf_az", filter_kind=filter_kind, letter=letra),
                _li(f"{letra}   ({count})", icon=IC["tvshow"]),
                isFolder=True,
            )
        xbmcplugin.endOfDirectory(HANDLE)
        return

    xbmcplugin.setPluginCategory(HANDLE, f"{label_kind} - {letter}")
    items = wf.browse_az(letter, kind_filter=filter_kind)
    if not items:
        _error(f"Sin items para '{letter}'", xbmcgui.NOTIFICATION_WARNING, 3000)
        xbmcplugin.endOfDirectory(HANDLE)
        return
    enriched = _enrich_many(items)
    for it, (info, art) in zip(items, enriched):
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="detail", src="wf", url=it["url"],
               kind=it["kind"], title=it["title"]),
            _li(info["title"], info=info, art=art),
            isFolder=True,
        )
    xbmcplugin.setContent(HANDLE,
                          "movies" if filter_kind == "movie" else "tvshows")
    xbmcplugin.endOfDirectory(HANDLE)


def wf_rebuild_index():
    dlg = xbmcgui.Dialog()
    if not dlg.yesno(
            "MejorWolf",
            "Descargar sitemaps y scrapear el catalogo completo.\n"
            "Puede tardar entre 5-30 minutos.\nContinuar?"):
        return
    progress = xbmcgui.DialogProgress()
    progress.create("MejorWolf", "Preparando reconstruccion...")
    state = {"cancel": False}

    def cb(done, total, current):
        if progress.iscanceled():
            state["cancel"] = True
            return True
        pct = int(done * 100 / total) if total else 0
        progress.update(pct, f"Procesando {done}/{total}\n[{current[:80]}]")
        return False

    try:
        added = wf.rebuild_index(progress_cb=cb)
    except Exception as e:
        xbmc.log(f"[MejorWolf] rebuild error: {e}", xbmc.LOGERROR)
        added = 0
    try:
        progress.close()
    except Exception:
        pass
    if state["cancel"]:
        _error(f"Cancelado. +{added} items", xbmcgui.NOTIFICATION_WARNING, 4000)
    else:
        total, _ = wf.index_stats()
        _error(f"Indice: +{added} nuevos, total {total}",
               xbmcgui.NOTIFICATION_INFO, 5000)


# ── Diagnostico ─────────────────────────────────────────────────────────────

def diagnose():
    xbmcplugin.setPluginCategory(HANDLE, "Diagnostico")

    # Proxy
    ok, msg = hs.diagnose_proxy()
    line = f"Proxy Cloudflare: {msg}"
    xbmcplugin.addDirectoryItem(HANDLE, "", _li(line, icon=IC["help"]), isFolder=False)

    # DoH
    for label, status in dns_doh.diagnose_endpoints():
        xbmcplugin.addDirectoryItem(HANDLE, "", _li(f"{label}: {status}", icon=IC["help"]), isFolder=False)

    # Host resolution
    for host in ("www.wolfmax4k.com", "www.elitetorrent.com", dt.resolve_domain()):
        ips = dns_doh.resolve(host) or []
        line = f"DoH {host}: {', '.join(ips[:3]) if ips else 'sin respuesta'}"
        xbmcplugin.addDirectoryItem(HANDLE, "", _li(line, icon=IC["help"]), isFolder=False)

    # Site access
    targets = [
        ("DonTorrent", dt.base_url() + "/"),
        ("WolfMax4K",
         (ADDON.getSetting("wf_base_url") or "https://www.wolfmax4k.com").rstrip("/") + "/"),
        ("EliteTorrent",
         (ADDON.getSetting("et_base_url") or "https://www.elitetorrent.com").rstrip("/") + "/"),
    ]
    for label, url in targets:
        status, n, final, blocked = hs.diagnose(url)
        if blocked:
            line = f"{label}: {blocked} (HTTP {status})"
        elif status == 200 and n > 1000:
            line = f"{label}: OK (HTTP 200, {n} bytes)"
        else:
            line = f"{label}: HTTP {status}, {n} bytes"
        xbmcplugin.addDirectoryItem(HANDLE, "", _li(line, icon=IC["help"]), isFolder=False)

    # Quick scrape test
    samples = [
        ("DT peliculas", lambda: dt.latest("movie", 1)),
        ("DT series",    lambda: dt.latest("tvshow", 1)),
        ("ET estrenos",  lambda: et.latest("estrenos", 1)),
        ("ET peliculas", lambda: et.latest("movie", 1)),
        ("WF peliculas", lambda: wf.latest("movie", 1)),
    ]
    for label, fn in samples:
        try:
            result = fn()
            count = len(result[0]) if isinstance(result, tuple) else len(result or [])
            line = f"{label}: {count} items"
        except Exception as e:
            line = f"{label}: ERROR {e.__class__.__name__}"
        xbmcplugin.addDirectoryItem(HANDLE, "", _li(line, icon=IC["help"]), isFolder=False)

    xbmcplugin.endOfDirectory(HANDLE)


def open_settings():
    ADDON.openSettings()


# ── Router ──────────────────────────────────────────────────────────────────

def router(qs):
    params = dict(parse_qsl(qs))
    action = params.get("action")
    try:
        if   action is None:            home()
        elif action == "estrenos":      estrenos()
        elif action == "section":       section(params["kind"])
        elif action == "src_movie":     src_section(params.get("src", "dt"), "movie")
        elif action == "src_tvshow":    src_section(params.get("src", "dt"), "tvshow")
        elif action == "documentales_menu": documentales_menu()
        elif action == "list":          list_items(params.get("src", "dt"),
                                                    params["kind"],
                                                    page=int(params.get("page", "1")))
        elif action == "et_genre":      et_genre(params["genre"],
                                                  page=int(params.get("page", "1")))
        elif action == "detail":        detail(params.get("src", "dt"),
                                               params["url"],
                                               params.get("kind", "movie"),
                                               params.get("title", ""))
        elif action == "play":          play(params.get("torrent", ""))
        elif action == "dt_play":       dt_play(params.get("content_id", ""),
                                                 params.get("tabla", ""),
                                                 page_url=params.get("page_url", ""))
        elif action == "search":        search(filter_kind=params.get("filter_kind"))
        elif action == "source_results": show_source_results(
                                            params.get("src", "dt"),
                                            params.get("cache_key", ""),
                                            q=params.get("q", ""))
        elif action == "series_group":  show_series_group(
                                            params.get("series_name", ""),
                                            params.get("cache_key", ""),
                                            q=params.get("q", ""))
        elif action == "wf_series":     wf_series(params.get("q", ""),
                                                   seed_url=params.get("seed_url", ""),
                                                   title=params.get("title", ""))
        elif action == "wf_season":     wf_season(params.get("q", ""),
                                                   params.get("season", "1"),
                                                   title=params.get("title", ""),
                                                   seed_url=params.get("seed_url", ""))
        elif action == "wf_az":         wf_az_menu(
                                            filter_kind=params.get("filter_kind"),
                                            letter=params.get("letter"))
        elif action == "wf_rebuild":    wf_rebuild_index()
        elif action == "generos_menu":  generos_menu()
        elif action == "settings":      open_settings()
        elif action == "diagnose":      diagnose()
        else:                           home()
    except Exception as e:
        import traceback
        xbmc.log(f"[MejorWolf] ROUTER ERROR: {e}\n{traceback.format_exc()}",
                 xbmc.LOGERROR)
        _error(f"Error: {e}")
        try:
            xbmcplugin.endOfDirectory(HANDLE)
        except Exception:
            pass
