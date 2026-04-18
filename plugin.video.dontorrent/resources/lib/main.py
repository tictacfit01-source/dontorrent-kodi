import sys
import re
from urllib.parse import parse_qsl, urlencode
from concurrent.futures import ThreadPoolExecutor

import os

import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs

from . import scraper, tmdb, player, domain, download, debrid

ADDON = xbmcaddon.Addon()
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 else -1
BASE = sys.argv[0] if sys.argv else "plugin://plugin.video.dontorrent/"

ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ICON = os.path.join(ADDON_PATH, "icon.png")
FANART = os.path.join(ADDON_PATH, "fanart.jpg")
MEDIA = os.path.join(ADDON_PATH, "resources", "media")
# DEFAULT_ART intentionally only sets fanart so we never overwrite a real
# poster with the addon logo when TMDB enrichment misses.
DEFAULT_ART = {"fanart": FANART}


def _ic(name):
    """Resolve a custom section icon shipped in resources/media/."""
    return os.path.join(MEDIA, name)


# Custom flat-colored icons generated via tools/make_icons.py. Same image is
# reused across closely related sections (e.g. movie / movie_hd / movie_4k
# all share the cinema clapper).
SECTION_ICONS = {
    "home_estrenos":     _ic("estrenos.png"),
    "home_movie":        _ic("movie.png"),
    "home_tvshow":       _ic("tvshow.png"),
    "home_documentary":  _ic("documentary.png"),
    "home_search":       _ic("search.png"),
    "home_refresh":      _ic("refresh.png"),
    "home_diagnose":     _ic("diagnose.png"),
    "home_help":         _ic("help.png"),
    "home_settings":     _ic("settings.png"),
    "movie":             _ic("movie.png"),
    "movie_hd":          _ic("movie.png"),
    "movie_4k":          _ic("movie.png"),
    "tvshow":            _ic("tvshow.png"),
    "tvshow_hd":         _ic("tvshow.png"),
    "tvshow_4k":         _ic("tvshow.png"),
    "documentary":       _ic("documentary.png"),
    "estrenos":          _ic("estrenos.png"),
    "estrenos_movie":    _ic("estrenos.png"),
    "estrenos_tvshow":   _ic("estrenos.png"),
    "search_movie":      _ic("search.png"),
    "search_tvshow":     _ic("search.png"),
    "next_page":         _ic("next_page.png"),
}


KIND_LABEL = {
    "movie": "Cine",
    "movie_hd": "Cine HD",
    "movie_4k": "Cine 4K",
    "tvshow": "Series",
    "tvshow_hd": "Series HD",
    "tvshow_4k": "Series 4K",
    "documentary": "Documentales",
    "estrenos": "Estrenos",
    "estrenos_movie": "Estrenos de cine",
    "estrenos_tvshow": "Estrenos de series",
}


def _u(**kwargs):
    return BASE + "?" + urlencode({k: v for k, v in kwargs.items() if v is not None})


def _li(label, info=None, art=None, playable=False, icon=None):
    it = xbmcgui.ListItem(label=label)
    merged = dict(DEFAULT_ART)
    # Icon is used for menu/folder entries. If no specific icon was supplied
    # we fall back to the addon logo, but only as the small icon - never as
    # the poster/thumb (otherwise items without a TMDB match would show the
    # addon logo where the poster should be).
    base_icon = icon or ICON
    merged.setdefault("icon", base_icon)
    merged.setdefault("thumb", base_icon)
    if art:
        merged.update({k: v for k, v in art.items() if v})
    it.setArt(merged)
    if info:
        data = {}
        for k in ("title", "plot", "year", "rating", "mediatype"):
            v = info.get(k)
            if v in (None, "", 0):
                continue
            if k == "year":
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
    # "documentary" is handled specially by tmdb.enrich (tries tv then movie).
    if kind == "documentary":
        return "documentary"
    return "movie" if kind in ("movie", "movie_hd", "movie_4k") else "tv"


def _media_type(kind):
    return "movie" if kind in ("movie", "movie_hd", "movie_4k", "documentary", "estrenos_movie") else "tvshow"


def _display_title(it):
    """Build a display label that always keeps the site's distinguishing
    info (V. Extendida, Parte 1/2) and appends quality (BDremux-1080p, HDTV,
    etc.) when we scraped it. TMDB title is ignored for display because
    multiple site entries collapse to the same canonical TMDB title."""
    base = it.get("title") or ""
    q = it.get("quality")
    if q and q.lower() not in base.lower():
        return f"{base} [{q}]"
    return base


def _enrich_and_build(it, kind):
    # Slugs strip Spanish accents, which breaks TMDB matching (e.g.
    # "El-ltimo-depredador" finds no hit, while "El último depredador"
    # matches immediately). If TMDB misses, fall back to fetching the
    # detail page H1 (which preserves accents) and retry.
    url = it.get("url")
    alt = (lambda u=url: scraper.fetch_detail_title(u)) if url else None
    meta = tmdb.enrich(it["title"], kind=_tmdb_kind(kind), alt_title_fn=alt)
    info = {
        "title": _display_title(it),
        "plot": meta.get("plot"),
        "year": meta.get("year"),
        "rating": meta.get("rating"),
        "mediatype": _media_type(kind),
    }
    art = {}
    if meta.get("poster"):
        art["poster"] = meta["poster"]
        art["thumb"] = meta["poster"]
    if meta.get("fanart"):
        art["fanart"] = meta["fanart"]
    if it.get("image"):
        art.setdefault("thumb", it["image"])
        art.setdefault("poster", it["image"])
    return info, art


def _enrich_many(items, workers=8):
    """Enrich a list of items in parallel. TMDB lookups plus the detail-page
    title fallback are both network-bound, so a small thread pool keeps
    listing pages snappy even when many entries miss TMDB."""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda it: _enrich_and_build(it, it["kind"]), items))


# ---------------- Navigation ----------------

def home():
    xbmcplugin.setPluginCategory(HANDLE, "DonTorrent")
    entries = [
        ("Estrenos",                 _u(action="section", kind="estrenos"),    SECTION_ICONS["home_estrenos"]),
        ("Cine",                     _u(action="section", kind="movie"),       SECTION_ICONS["home_movie"]),
        ("Series",                   _u(action="section", kind="tvshow"),      SECTION_ICONS["home_tvshow"]),
        ("Documentales",             _u(action="list", kind="documentary", page=1), SECTION_ICONS["home_documentary"]),
        ("Buscar",                   _u(action="search"),                      SECTION_ICONS["home_search"]),
        ("Actualizar dominio ahora", _u(action="refresh_domain"),              SECTION_ICONS["home_refresh"]),
        ("Diagnostico",              _u(action="diagnose"),                    SECTION_ICONS["home_diagnose"]),
        ("Ayuda",                    _u(action="help"),                        SECTION_ICONS["home_help"]),
        ("Ajustes",                  _u(action="settings"),                    SECTION_ICONS["home_settings"]),
    ]
    for label, url, ic in entries:
        xbmcplugin.addDirectoryItem(HANDLE, url, _li(label, icon=ic), isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)


def section(kind):
    label = KIND_LABEL.get(kind, kind)
    xbmcplugin.setPluginCategory(HANDLE, label)
    if kind == "movie":
        entries = [
            ("Todas las peliculas", _u(action="list", kind="movie", page=1),    SECTION_ICONS["movie"]),
            ("Peliculas HD",        _u(action="list", kind="movie_hd", page=1), SECTION_ICONS["movie_hd"]),
            ("Peliculas 4K",        _u(action="list", kind="movie_4k", page=1), SECTION_ICONS["movie_4k"]),
            ("Buscar pelicula",     _u(action="search", filter_kind="movie"),   SECTION_ICONS["search_movie"]),
        ]
    elif kind == "tvshow":
        entries = [
            ("Todas las series", _u(action="list", kind="tvshow", page=1),    SECTION_ICONS["tvshow"]),
            ("Series HD",        _u(action="list", kind="tvshow_hd", page=1), SECTION_ICONS["tvshow_hd"]),
            ("Series 4K",        _u(action="list", kind="tvshow_4k", page=1), SECTION_ICONS["tvshow_4k"]),
            ("Buscar serie",     _u(action="search", filter_kind="tvshow"),   SECTION_ICONS["search_tvshow"]),
        ]
    elif kind == "estrenos":
        entries = [
            ("Estrenos mezclados", _u(action="list", kind="estrenos", page=1),         SECTION_ICONS["estrenos"]),
            ("Estrenos de cine",   _u(action="list", kind="estrenos_movie", page=1),   SECTION_ICONS["estrenos_movie"]),
            ("Estrenos de series", _u(action="list", kind="estrenos_tvshow", page=1),  SECTION_ICONS["estrenos_tvshow"]),
        ]
    else:
        entries = [(f"Ultimas {label}", _u(action="list", kind=kind, page=1), SECTION_ICONS.get(kind))]
    for lab, url, ic in entries:
        xbmcplugin.addDirectoryItem(HANDLE, url, _li(lab, icon=ic), isFolder=True)
    xbmcplugin.endOfDirectory(HANDLE)


def list_items(kind, page):
    try:
        items = scraper.latest(kind=kind, page=page)
    except Exception as e:
        xbmcgui.Dialog().notification("DonTorrent", f"Error: {e}", xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(HANDLE)
        return
    enriched = _enrich_many(items)
    for it, (info, art) in zip(items, enriched):
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="detail", url=it["url"], kind=it["kind"], title=it["title"]),
            _li(info["title"], info=info, art=art),
            isFolder=True,
        )
    if items:
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="list", kind=kind, page=page + 1),
            _li(f"Pagina siguiente ({page + 1}) >>", icon=SECTION_ICONS["next_page"]),
            isFolder=True,
        )
    xbmcplugin.setContent(HANDLE, "movies" if _media_type(kind) == "movie" else "tvshows")
    xbmcplugin.endOfDirectory(HANDLE)


def detail(url, kind, title):
    try:
        d = scraper.detail(url)
    except Exception as e:
        xbmcgui.Dialog().notification("DonTorrent", f"Error: {e}", xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(HANDLE)
        return

    # Inside the detail page we already have the accented H1, use it
    # directly as the TMDB query fallback for free.
    alt = (lambda t=d.get("title"): t) if d.get("title") else None
    meta = tmdb.enrich(title, kind=_tmdb_kind(kind), alt_title_fn=alt)
    art = {}
    if meta.get("poster"):
        art["poster"] = meta["poster"]
        art["thumb"] = meta["poster"]
    if meta.get("fanart"):
        art["fanart"] = meta["fanart"]
    if d.get("image"):
        art.setdefault("thumb", d["image"])
        art.setdefault("poster", d["image"])

    info_base = {
        "plot": meta.get("plot") or d.get("plot"),
        "year": meta.get("year") or d.get("year"),
        "rating": meta.get("rating"),
    }

    has_episodes = any(x.get("season") is not None for x in d["downloads"])
    # Prefer the detail-page H1, otherwise the title the caller gave us.
    movie_label = (d.get("title") or title or "").strip() or "Pelicula"

    for dl in d["downloads"]:
        if has_episodes and dl.get("season") is None:
            continue
        if has_episodes:
            label = dl["label"]
            mtype = "episode"
        else:
            label = movie_label
            # If the site added extras to the download row (e.g. a quality
            # tag), surface them alongside the real title.
            extras = dl.get("label")
            if extras and extras.lower() not in ("descargar", movie_label.lower()):
                label = f"{movie_label} - {extras}"
            mtype = "movie"
        info = dict(info_base, title=label, mediatype=mtype)
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="play", cid=dl["content_id"], tabla=dl["tabla"], page=url),
            _li(label, info=info, art=art, playable=True),
            isFolder=False,
        )

    if not d["downloads"]:
        xbmcgui.Dialog().notification("DonTorrent", "Sin descargas en esta ficha", xbmcgui.NOTIFICATION_WARNING)

    xbmcplugin.endOfDirectory(HANDLE)


_NOISE_RE = re.compile(
    r"\b(V\.?\s*Extendida|Vers?i[oó]?n\s+Extendida|FullBluRay|Parte\s+\d+|"
    r"\d+\s*[aª]?\s*Temporada|Temporada\s+\d+|"
    r"1080p|720p|2160p|4K|BluRay|BDremux|BDRip|HDTV|HDRip|DVDRip|microHD|"
    r"WEB-?DL|WEBRip|HEVC|x265|x264|DUAL|VOSE|Latino|Castellano|Espa[nN]ol)\b",
    re.IGNORECASE,
)


def _slug_title(page_url):
    if not page_url:
        return None
    m = re.search(r"/(?:pelicula|serie|documental)/\d+/(?:\d+/)?(.+?)/?$", page_url)
    if not m:
        return None
    raw = m.group(1).split("/")[-1]
    txt = raw.replace("-", " ").replace("_", " ")
    txt = _NOISE_RE.sub("", txt)
    return re.sub(r"\s+", " ", txt).strip(" -.")


def _candidate_queries(page_url):
    """Build several search-query candidates ranked by likelihood of hitting
    DonTorrent's strict substring search. The site only matches contiguous
    substrings, so long franchise titles like 'El Señor de los anillos: La
    comunidad del anillo' return zero hits - we have to query the
    distinctive subtitle ('La comunidad del anillo') instead."""
    cands = []

    # 1) Detail-page H1 (has accents and the proper colon split).
    try:
        h1 = scraper.fetch_detail_title(page_url) or ""
    except Exception:
        h1 = ""
    if h1:
        clean = _NOISE_RE.sub("", re.sub(r"\([^)]*\)|\[[^\]]*\]", "", h1))
        clean = re.sub(r"\s+", " ", clean).strip(" .-")
        if ":" in clean:
            sub = clean.split(":", 1)[1].strip(" .-")
            if sub:
                cands.append(sub)
        if clean:
            cands.append(clean)

    # 2) Slug-derived title (no accents).
    slug = _slug_title(page_url)
    if slug:
        cands.append(slug)
        toks = slug.split()
        # 3) Last 4 tokens (works for "La comunidad del anillo" style)
        if len(toks) > 4:
            cands.append(" ".join(toks[-4:]))
        if len(toks) > 3:
            cands.append(" ".join(toks[-3:]))

    seen, out = set(), []
    for c in cands:
        k = c.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(c)
    return out


def _inspect_alternative(s):
    """For a search-result item, fetch its detail (one HTTP), pick its first
    download row, resolve it through the PoW (one HTTP + cheap CPU) and
    inspect the .torrent (one HTTP). Returns (sibling, dl, torrent_info)
    or (sibling, None, None) on failure."""
    try:
        d = scraper.detail(s["url"])
    except Exception:
        return s, None, None
    dls = d.get("downloads") or []
    if not dls:
        return s, None, None
    dl = dls[0]
    try:
        torrent_url = download.resolve_torrent(dl["content_id"], dl["tabla"], page_url=s["url"])
        info = download.inspect_torrent(torrent_url) or {}
    except Exception:
        return s, dl, None
    return s, dl, info


def _pick_streamable_alternative(page_url):
    """Search DonTorrent for other releases of the same title, inspect each
    .torrent in parallel to determine MKV vs RAR with certainty, sort
    streamable first, and let the user pick. Returns (sibling, dl, info) of
    the chosen entry so the caller can play it directly without re-resolving."""
    siblings = []
    queries_tried = []
    for q in _candidate_queries(page_url):
        queries_tried.append(q)
        try:
            r = scraper.search(q)
        except Exception:
            r = []
        r = [s for s in r if s.get("url") and s["url"] != page_url]
        if r:
            siblings = r
            break
    if not siblings:
        xbmcgui.Dialog().notification(
            "DonTorrent",
            f"Sin alternativas (probe {len(queries_tried)} busquedas)",
            xbmcgui.NOTIFICATION_INFO, 4000,
        )
        return None

    # Cap to the first 12 to keep the "comprobando..." step under ~10s.
    siblings = siblings[:12]

    progress = xbmcgui.DialogProgress()
    progress.create("DonTorrent", "Comprobando si cada version es MKV o RAR...")
    results = []
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(_inspect_alternative, s) for s in siblings]
            for i, fut in enumerate(futs):
                if progress.iscanceled():
                    break
                results.append(fut.result())
                progress.update(int((i + 1) * 100 / len(siblings)))
    finally:
        progress.close()

    # Build menu entries (MKV first, then RAR, then unknown).
    rows = []
    for s, dl, info in results:
        if not dl:
            continue
        if info is None:
            tag, rank = "[?]", 2
            size = "?"
        elif info.get("is_rar"):
            tag, rank = "[RAR]", 1
            size = download.human_size(info.get("total_size") or 0)
        else:
            tag, rank = "[MKV]", 0
            size = download.human_size(info.get("total_size") or 0)
        q = s.get("quality") or ""
        suffix = f" ({q})" if q else ""
        label = f"{tag} {size} - {s['title']}{suffix}"
        rows.append((rank, label, s, dl, info))
    rows.sort(key=lambda r: r[0])

    if not rows:
        xbmcgui.Dialog().notification("DonTorrent", "No se pudo inspeccionar ninguna", xbmcgui.NOTIFICATION_INFO, 4000)
        return None

    idx = xbmcgui.Dialog().select("Versiones disponibles", [r[1] for r in rows])
    if idx < 0:
        return None
    _, _, sibling, dl, info = rows[idx]
    return sibling, dl, info


def _rd_settings():
    """Return (enabled, token, mode) where mode is 'auto', 'rd_only' or
    'elementum_only'."""
    enabled = ADDON.getSetting("rd_enabled") == "true"
    token = ADDON.getSetting("rd_token") or ""
    backend = ADDON.getSetting("player_backend") or "0"
    mode = {"0": "auto", "1": "rd_only", "2": "elementum_only"}.get(backend, "auto")
    return enabled, token.strip(), mode


def _try_realdebrid(magnet_or_torrent):
    """Send the magnet (or fallback HTTPS torrent URL) to Real-Debrid and
    return a direct streamable URL. Shows a progress dialog while waiting.
    Returns None on any failure (caller falls back)."""
    enabled, token, _ = _rd_settings()
    if not enabled or not token:
        return None
    progress = xbmcgui.DialogProgress()
    progress.create("Real-Debrid", "Enviando a Real-Debrid...")
    try:
        def cb(pct, status):
            if progress.iscanceled():
                raise debrid.DebridError("Cancelado por el usuario")
            progress.update(max(1, min(99, pct)), status)
        if magnet_or_torrent.startswith("magnet:"):
            return debrid.stream_url(token, magnet=magnet_or_torrent, progress_cb=cb)
        # If we only have an https .torrent URL, download once and pass bytes.
        import requests
        r = requests.get(magnet_or_torrent, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return debrid.stream_url(token, torrent_bytes=r.content, progress_cb=cb)
    except debrid.DebridError as e:
        xbmcgui.Dialog().notification("Real-Debrid", str(e),
                                       xbmcgui.NOTIFICATION_WARNING, 5000)
        return None
    except Exception as e:
        xbmc.log(f"[plugin.video.dontorrent] RD error: {e}", xbmc.LOGERROR)
        return None
    finally:
        progress.close()


def play(cid, tabla, page_url):
    try:
        torrent_url = download.resolve_torrent(cid, tabla, page_url=page_url)
    except Exception as e:
        xbmcgui.Dialog().notification("DonTorrent", f"Descarga fallida: {e}", xbmcgui.NOTIFICATION_ERROR, 6000)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
        return

    # Always inspect the .torrent: cheap, gives us the magnet URI (which
    # both RD and Elementum handle more reliably than an https URL), and lets
    # us detect RAR packaging before launching playback.
    info = download.inspect_torrent(torrent_url) or {}
    play_uri = info.get("magnet") or torrent_url

    rd_enabled, rd_token, mode = _rd_settings()

    # Real-Debrid path: the user's premium account streams the file
    # straight from RD's servers, including extracting RAR archives. This
    # is the only way to play microHD-style RAR releases without a long
    # local download.
    if mode in ("auto", "rd_only") and rd_enabled and rd_token:
        direct = _try_realdebrid(play_uri)
        if direct:
            xbmc.log(f"[plugin.video.dontorrent] RD direct: {direct[:80]}...", xbmc.LOGINFO)
            item = xbmcgui.ListItem(path=direct)
            item.setProperty("IsPlayable", "true")
            xbmcplugin.setResolvedUrl(HANDLE, True, item)
            return
        if mode == "rd_only":
            xbmcgui.Dialog().notification(
                "DonTorrent", "Real-Debrid fallo y modo es 'solo RD'",
                xbmcgui.NOTIFICATION_ERROR, 5000)
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        # mode == "auto" and RD failed -> fall through to Elementum.

    if mode == "elementum_only":
        # Skip RAR alt-search even if it's a RAR; user explicitly wants Elementum.
        play_url = player.elementum_url(play_uri)
        item = xbmcgui.ListItem(path=play_url)
        xbmcplugin.setResolvedUrl(HANDLE, True, item)
        return

    skip_warning = ADDON.getSetting("skip_rar_warning") == "true"
    if info.get("is_rar") and not skip_warning:
        size = download.human_size(info["total_size"]) if info["total_size"] else "?"
        choice = xbmcgui.Dialog().select(
            "DonTorrent - Archivo RAR detectado",
            [
                "Buscar otra version sin RAR (recomendado)",
                f"Forzar descarga completa y descomprimir ({size}, lento)",
                "Cancelar",
            ],
        )
        if choice == 0:
            picked = _pick_streamable_alternative(page_url)
            if picked is None:
                xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                return
            sibling, dl, alt_info = picked
            if alt_info and alt_info.get("is_rar"):
                cont = xbmcgui.Dialog().yesno(
                    "DonTorrent",
                    "La version elegida tambien es RAR.\nElementum tendra que descargarla entera y descomprimirla.\n\nContinuar?",
                    yeslabel="Continuar",
                    nolabel="Cancelar",
                )
                if not cont:
                    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                    return
            # Use the magnet from the already-inspected alt torrent. If the
            # inspection failed we still try resolve_torrent again as a
            # last resort.
            if alt_info and alt_info.get("magnet"):
                play_uri = alt_info["magnet"]
            else:
                try:
                    play_uri = download.resolve_torrent(dl["content_id"], dl["tabla"], page_url=sibling["url"])
                except Exception as e:
                    xbmcgui.Dialog().notification("DonTorrent", f"Fallo al resolver: {e}", xbmcgui.NOTIFICATION_ERROR, 5000)
                    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
                    return
            # Fall through to playback below with this new uri.
        elif choice != 1:
            xbmcgui.Dialog().notification("DonTorrent", "Reproduccion cancelada", xbmcgui.NOTIFICATION_INFO, 3000)
            xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())
            return
        # choice == 1 (or fall-through from successful alt pick) -> play.

    play_url = player.elementum_url(play_uri)
    xbmc.log(f"[plugin.video.dontorrent] Reproduciendo via {play_url}", xbmc.LOGINFO)
    item = xbmcgui.ListItem(path=play_url)
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def search(filter_kind=None):
    prompt = "Buscar en DonTorrent"
    if filter_kind:
        prompt = f"Buscar {KIND_LABEL.get(filter_kind, filter_kind)}"
    kb = xbmc.Keyboard("", prompt)
    kb.doModal()
    if not kb.isConfirmed():
        xbmcplugin.endOfDirectory(HANDLE)
        return
    q = kb.getText().strip()
    if not q:
        xbmcplugin.endOfDirectory(HANDLE)
        return
    try:
        items = scraper.search(q)
    except Exception as e:
        xbmcgui.Dialog().notification("DonTorrent", f"Error: {e}", xbmcgui.NOTIFICATION_ERROR, 5000)
        xbmcplugin.endOfDirectory(HANDLE)
        return
    if filter_kind:
        items = [i for i in items if i["kind"] == filter_kind]
    enriched = _enrich_many(items)
    for it, (info, art) in zip(items, enriched):
        xbmcplugin.addDirectoryItem(
            HANDLE,
            _u(action="detail", url=it["url"], kind=it["kind"], title=it["title"]),
            _li(info["title"], info=info, art=art),
            isFolder=True,
        )
    xbmcplugin.endOfDirectory(HANDLE)


def refresh_domain():
    d = domain.resolve(force=True)
    xbmcgui.Dialog().notification("DonTorrent", f"Dominio: {d}", xbmcgui.NOTIFICATION_INFO, 4000)


def diagnose():
    info = domain.diagnose()
    lines = [
        f"Canal Telegram: {info['channel']}",
        f"Dominio resuelto: {info['resolved']}",
        "",
        "Disponibles en Telegram (mas reciente primero):",
    ]
    lines += [f"  - {h}" for h in info["telegram_available"]] or ["  (ninguno detectado)"]
    lines.append("")
    lines.append("Censurados en Telegram:")
    lines += [f"  - {h}" for h in info["telegram_censored"]] or ["  (ninguno)"]
    lines.append("")
    try:
        items = scraper.latest("movie", 1)
        lines.append(f"Peliculas en home: {len(items)}")
        for it in items[:5]:
            lines.append(f"  - {it['title']}")
    except Exception as e:
        lines.append(f"Fallo cargando peliculas: {e}")
    lines.append("")
    rd_en, rd_tok, mode = _rd_settings()
    lines.append(f"Real-Debrid activado: {rd_en}  (modo: {mode})")
    if rd_en and rd_tok:
        ok, msg = debrid.ping(rd_tok)
        lines.append(f"  cuenta: {msg}" if ok else f"  ERROR: {msg}")
    elif rd_en:
        lines.append("  (sin token configurado)")
    xbmcgui.Dialog().textviewer("DonTorrent - Diagnostico", "\n".join(lines))


def rd_test():
    rd_en, rd_tok, _ = _rd_settings()
    if not rd_tok:
        xbmcgui.Dialog().ok("Real-Debrid", "No hay token configurado.")
        return
    ok, msg = debrid.ping(rd_tok)
    if ok:
        xbmcgui.Dialog().ok("Real-Debrid", f"Conexion correcta:\n\n{msg}")
    else:
        xbmcgui.Dialog().ok("Real-Debrid", f"Fallo de conexion:\n\n{msg}")


HELP_TEXT = """DonTorrent - Addon privado para Kodi

QUE HACE
Navega el catalogo de DonTorrent (peliculas, series, documentales) y
reproduce los torrents a traves de Elementum.

COMO USARLO
1. Elige una seccion del menu principal: Estrenos, Cine, Series o Documentales.
2. En Cine y Series puedes entrar en Todas, HD o 4K, o usar Buscar.
3. Abre una ficha. Veras un boton Descargar (peliculas) o una lista de
   episodios (series y documentales).
4. Pulsa sobre lo que quieras ver. El addon resuelve el torrent
   automaticamente (toma aprox 1 segundo) y se lo pasa a Elementum,
   que iniciara la reproduccion en streaming.

CAMBIOS DE DOMINIO
DonTorrent cambia su dominio con frecuencia cuando es bloqueado.
El addon revisa automaticamente el canal oficial de Telegram
(@DonTorrent) y detecta el dominio marcado como Disponible (check verde),
descartando los marcados como Censurado (cruz roja).
La revalidacion ocurre cada 12 horas por defecto (configurable en Ajustes).
Tambien puedes forzarla desde el menu -> Actualizar dominio ahora.

REPRODUCCION CON REAL-DEBRID (recomendado para RAR)
Si configuras tu API token de Real-Debrid en Ajustes -> Real-Debrid, el
addon enviara cada torrent a tu cuenta RD, esperara unos segundos y
reproducira un enlace HTTPS directo desde los servidores de RD. Esto
permite ver releases en RAR (microHD, etc.) sin descarga local y sin
descomprimir nada.
Token: real-debrid.com -> Mi cuenta -> API.

MODOS DE REPRODUCCION (Ajustes -> Reproduccion)
- Auto: Real-Debrid si esta configurado, si no Elementum.
- Solo Real-Debrid: nunca usa Elementum.
- Solo Elementum: nunca usa Real-Debrid.

REQUISITOS
- Elementum y/o cuenta Real-Debrid.
- Conexion a Internet.

CAPTCHA
Si haces muchas descargas seguidas en poco tiempo, DonTorrent puede pedir
un captcha. En ese caso espera unos minutos e intentalo de nuevo.

PRIVACIDAD
Este addon es privado, no envia telemetria y no recoge datos.

VERSION 0.5.0
"""


def show_help():
    xbmcgui.Dialog().textviewer("DonTorrent - Ayuda", HELP_TEXT)


def open_settings():
    ADDON.openSettings()


def router(qs):
    params = dict(parse_qsl(qs))
    action = params.get("action")
    try:
        if action is None:
            home()
        elif action == "section":
            section(params["kind"])
        elif action == "list":
            list_items(params["kind"], int(params.get("page", "1")))
        elif action == "detail":
            detail(params["url"], params["kind"], params.get("title", ""))
        elif action == "play":
            play(params["cid"], params["tabla"], params.get("page", ""))
        elif action == "search":
            search(filter_kind=params.get("filter_kind"))
        elif action == "refresh_domain":
            refresh_domain()
        elif action == "diagnose":
            diagnose()
        elif action == "rd_test":
            rd_test()
        elif action == "help":
            show_help()
        elif action == "settings":
            open_settings()
        else:
            home()
    except Exception as e:
        xbmc.log(f"[plugin.video.dontorrent] Router error: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().notification("DonTorrent", f"Error: {e}", xbmcgui.NOTIFICATION_ERROR)
        try:
            xbmcplugin.endOfDirectory(HANDLE)
        except Exception:
            pass
