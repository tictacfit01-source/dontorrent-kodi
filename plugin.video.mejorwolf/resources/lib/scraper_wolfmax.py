"""
Scraper para WolfMax4K (wolfmax4k.com).

URLs reales del sitio (verificadas):
  Listado peliculas:  /peliculas/bluray/, /peliculas/bluray-720p/,
                      /peliculas/bluray-1080p/, /peliculas/4k-2160p/
  Listado series:     /series/, /series/720p/, /series/1080p/, /series/4k-2160p/
  Documentales:       /documentales/
  Items pelicula:     /movie/{id}  (Bluray/alta calidad)
                      /online/{id} (720p)
  Items serie:        /series/{slug}
  Busqueda:           ?q={termino}  o  ?s={termino}
  Descarga:           a traves de enlacito.com (redirect a .torrent)
"""

import re
from urllib.parse import urljoin, quote as urlquote
from bs4 import BeautifulSoup
import xbmc
import xbmcaddon
from . import http_session as hs
from . import wf_index

SOURCE = "wf"
_ADDON = xbmcaddon.Addon()
_DEFAULT_BASE = "https://www.wolfmax4k.com"
_LOG = lambda msg: xbmc.log(f"[MejorWolf/WF] {msg}", xbmc.LOGINFO)


def _base():
    url = (_ADDON.getSetting("wf_base_url") or "").strip().rstrip("/")
    return url or _DEFAULT_BASE


def _session():
    return hs.make_session(_base())


# Rutas reales verificadas en el sitio
SECTION_PATH = {
    "movie":       "peliculas/bluray",
    "movie_720p":  "peliculas/bluray-720p",
    "movie_hd":    "peliculas/bluray-1080p",
    "movie_4k":    "peliculas/4k-2160p",
    "tvshow":      "series",
    "tvshow_720p": "series/720p",
    "tvshow_hd":   "series/1080p",
    "tvshow_4k":   "series/4k-2160p",
    "documentary": "documentales",
}

# Subcategorias que NO son items individuales de series
_SERIES_SUBCATS = {"480p", "720p", "1080p", "4k-2160p", "4k"}

_QUALITY_RE = re.compile(
    r"\b(4K\s*UHD|4K|2160p|Dolby\s*Vision|DV|HDR10\+|HDR|Remux|BDRemux"
    r"|BluRay|Blu-Ray|BLuRayRip|BLuRay|1080p|720p|480p|WEB-?DL|WEBRip"
    r"|HEVC|x265|x264|Otros)\b",
    re.IGNORECASE,
)

_EPISODE_RE = re.compile(
    r"(\d{1,2})\s*[xX\xd7]\s*(\d{1,3})|[Ss](\d{1,2})[Ee](\d{1,3})"
)


def _get(path, params=None):
    sess = _session()
    url = urljoin(_base() + "/", path.lstrip("/"))
    r = hs.get(sess, url, params=params)
    return BeautifulSoup(r.content, "html.parser"), r.url


_LISTING_SLUGS = _SERIES_SUBCATS | {
    "bluray", "bluray-720p", "bluray-1080p", "4k-2160p",
    "peliculas", "series", "documentales", "buscar", "search",
}


_TVSHOW_TITLE_HINT_RE = re.compile(
    r"\[\s*Cap\.?\s*\d+\s*\]"           # [Cap.201]
    r"|\bCap(?:itulo)?\s*\d+\b"          # Cap 3, Capitulo 12
    r"|\bTemporada\s*\d+\b"              # Temporada 2
    r"|\b\d{1,2}x\d{2,3}\b"              # 1x05, 10x102
    r"|\bS\d{1,2}E\d{1,3}\b"             # S01E05
    r"|\bmini-?serie\b"                  # Miniserie
    r"|\b(?:HDTV|WEB-?DL|WEB-?Rip)\b(?!.*\b(?:Bluray|BDRemux)\b)",  # HDTV solo (series)
    re.I,
)


def _looks_like_tvshow(title, url):
    """Heuristica: decide si un item es serie (tvshow) o pelicula.

    1. URL contiene '/descargar/series' o '/serie' o '/capitulo/' o
       '/episodio/' -> tvshow.
    2. Titulo tiene marca de capitulo/temporada/SxxExx/HDTV -> tvshow.
    3. En otro caso -> movie.
    """
    if url:
        low = url.lower()
        if ("/descargar/series" in low or "/serie-online" in low
                or "/capitulo/" in low or "/episodio/" in low
                or re.search(r"/descargar/[^/]*(?:series|animacion|manga|telenovela)",
                             low)):
            return True
    if title and _TVSHOW_TITLE_HINT_RE.search(title):
        return True
    return False


def _classify(href):
    """
    Clasifica un href segun el tipo de contenido. WolfMax ha usado varias
    formas a lo largo del tiempo:
      Pelicula: /movie/<id>, /online/<id>, /pelicula/<id>,
                /peliculas/<slug>, /peliculas/<calidad>/<slug>...
      Serie:    /serie/<slug>, /series/<slug>, /series/<calidad>/<slug>,
                /capitulo/<id>, /episodio/<id>
      Documental: /documental/<slug>, /documentales/<slug>
    """
    if not href:
        return None
    h = href.lower()

    # IDs numericos (formato historico)
    if re.search(r"/(movie|online|pelicula|capitulo|episodio|serie-online(?:-[\w-]+)?)/\d+", h):
        if ("/capitulo/" in h or "/episodio/" in h
                or "/serie-online/" in h or "/serie-online-" in h):
            return "tvshow"
        return "movie"

    # Pelicula con slug bajo /peliculas/ (acepta /peliculas/<calidad>/<slug>
    # buscando el ULTIMO segmento que no sea categoria)
    m = re.search(r"/peliculas/([^?#]+?)/?$", h)
    if m:
        parts = [p for p in m.group(1).split("/") if p]
        # quitar segmentos que son categorias conocidas
        leaf = next((p for p in reversed(parts) if p not in _LISTING_SLUGS), None)
        if leaf:
            return "movie"

    # Series: misma logica, ultimo segmento no-categoria
    m = re.search(r"/serie(?:s)?/([^?#]+?)/?$", h)
    if m:
        parts = [p for p in m.group(1).split("/") if p]
        leaf = next((p for p in reversed(parts) if p not in _LISTING_SLUGS), None)
        if leaf:
            return "tvshow"

    # Documentales
    m2 = re.search(r"/documental(?:es)?/([^?#]+?)/?$", h)
    if m2:
        parts = [p for p in m2.group(1).split("/") if p]
        leaf = next((p for p in reversed(parts) if p not in _LISTING_SLUGS), None)
        if leaf:
            return "documentary"
    return None


def _fix_src(src, page_url):
    if not src or src.startswith("data:"):
        return None
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return _base() + src
    if not src.startswith("http"):
        return urljoin(page_url, src)
    return src


def _items_from_soup(soup, page_url, kind_filter=None):
    """
    Estructura real del sitio:
      <a href="/movie/263701">
        <img alt="Gator Lake (2025) [Bluray][Esp]" src="...jpg">
        <span>badge_calidad</span>
        <h3>Gator Lake (2025) [Bluray][Esp]</h3>
      </a>

    El titulo esta en img.alt o en h3 dentro del ancla.
    """
    items, seen = [], set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        kind = _classify(href)
        if not kind:
            continue
        # Correccion por titulo (ver mas abajo)
        if kind_filter and kind != kind_filter:
            # Re-verificar con titulo antes de descartar
            img_tmp = a.find("img")
            t_tmp = (img_tmp.get("alt") if img_tmp else "") or a.get_text(" ", strip=True)
            if kind == "movie" and _looks_like_tvshow(t_tmp, href):
                kind = "tvshow"
            if kind != kind_filter:
                continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)

        img = a.find("img")
        # Title: try img.alt, then h3/h2/.title text, then anchor text
        title = ""
        if img:
            title = (img.get("alt") or "").strip()
        if not title:
            for sel in ("h3", "h2", ".title", ".titulo", ".name"):
                t = a.select_one(sel)
                if t:
                    title = t.get_text(" ", strip=True)
                    if title:
                        break
        if not title:
            title = a.get_text(" ", strip=True)
        title = re.sub(r"\s+", " ", title).strip()
        if not title or len(title) < 2:
            continue

        # Image (lazy-load aware)
        img_src = None
        if img:
            raw = (img.get("src") or img.get("data-src") or
                   img.get("data-original") or img.get("data-lazy-src") or "").strip()
            img_src = _fix_src(raw, page_url)

        # Calidad: del titulo o de badges dentro del ancla
        quality = None
        m = _QUALITY_RE.search(title)
        if m:
            quality = m.group(1)
        if not quality:
            for span in a.find_all(["span", "div"]):
                t = span.get_text(" ", strip=True)
                m2 = _QUALITY_RE.search(t)
                if m2 and t == m2.group(1):  # solo badge puro
                    quality = m2.group(1)
                    break

        # Correccion final: si el titulo deja claro que es serie, override
        if kind == "movie" and _looks_like_tvshow(title, url):
            kind = "tvshow"
        items.append({
            "title":   title,
            "url":     url,
            "kind":    kind,
            "image":   img_src,
            "quality": quality,
            "source":  SOURCE,
        })

    _LOG(f"_items_from_soup -> {len(items)} items from {page_url}")
    # Auto-alimenta el indice persistente. Solo guarda URLs playables
    # (filtro en wf_index.add). Non-fatal si falla.
    try:
        wf_index.add(items)
    except Exception as e:
        _LOG(f"wf_index.add failed (non-fatal): {e}")
    return items


# URLs que se pueden reproducir (tienen ID numerico + pagina de descarga).
# Las URLs /series/<slug> y /peliculas/<slug> sin ID son "dead-end": su
# contenido se puebla via /mvc/controllers/data.find.php, que Cloudflare
# Workers no puede usar (bloqueo por IP).
_PLAYABLE_URL_RE_GLOBAL = re.compile(
    r"/(movie|online|pelicula|capitulo|episodio|serie-online(?:-[\w-]+)?)/\d+",
    re.I,
)

# Mapeo kind -> sufijo de URL /serie-online-* para filtrar por calidad.
_TVSHOW_QUALITY_SUFFIX = {
    "tvshow_720p": ("serie-online-720p",),
    "tvshow_hd":   ("serie-online-hd", "serie-online-1080p"),
    "tvshow_4k":   ("serie-online-4k", "serie-online-4k-2160p", "serie-online-2160p"),
}

# Listings que, a diferencia de /series/, SI devuelven URLs playables
# para series/programas (episodios individuales con /serie-online-*/<id>
# o /online/<id>). Home incluye un mix de pelis y ~37 episodios de serie.
_TVSHOW_LISTINGS = [
    "",                 # home
    "programas-tv",
    "telenovelas",
    "animacion-manga",
    "animacion-infantil",
    "documentales",
]


def latest(kind="movie", page=1):
    _LOG(f"latest kind={kind} page={page}")

    # --- Series/programas: los listados /series/* devuelven URLs dead-end.
    # Agregamos listados que SI devuelven episodios playables con ID.
    if kind.startswith("tvshow"):
        return _latest_tvshow_playable(kind)

    section = SECTION_PATH.get(kind, "peliculas/bluray")
    _LOG(f"latest -> /{section}")
    try:
        soup, url = _get(section)
        items = _items_from_soup(soup, url)
        # Filtrar dead-ends por seguridad (no esperamos que haya en /peliculas,
        # pero defensivo).
        items = [it for it in items
                 if _PLAYABLE_URL_RE_GLOBAL.search(it.get("url") or "")]
        _LOG(f"latest -> {len(items)} items playables")
        return items
    except Exception as e:
        _LOG(f"latest error: {e}")
        raise


def _latest_tvshow_playable(kind):
    """
    Construye el listado de series/programas uniendo varias secciones que
    devuelven URLs playables (/serie-online-*/<id>, /online/<id>).

    Filtra por kind == "tvshow" (asi descartamos las peliculas que estan
    mezcladas en la home) y, si se pide una calidad concreta, por el sufijo
    de URL correspondiente.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    quality_suffixes = _TVSHOW_QUALITY_SUFFIX.get(kind)

    def _fetch(section):
        try:
            soup, page_url = _get(section)
            return section, _items_from_soup(soup, page_url)
        except Exception as e:
            _LOG(f"latest tvshow section {section} fail: {e.__class__.__name__}")
            return section, []

    all_items = []
    seen = set()
    with ThreadPoolExecutor(max_workers=len(_TVSHOW_LISTINGS)) as pool:
        futures = {pool.submit(_fetch, s): s for s in _TVSHOW_LISTINGS}
        for fut in as_completed(futures):
            section, raw = fut.result()
            kept = 0
            for it in raw:
                u = it.get("url") or ""
                if u in seen:
                    continue
                if not _PLAYABLE_URL_RE_GLOBAL.search(u):
                    continue
                # Solo series/programas/documentales (no peliculas de la home)
                if it.get("kind") not in ("tvshow", "documentary"):
                    continue
                if quality_suffixes:
                    low = u.lower()
                    if not any(f"/{s}/" in low for s in quality_suffixes):
                        continue
                seen.add(u)
                all_items.append(it)
                kept += 1
            _LOG(f"latest tvshow section {section}: kept {kept}/{len(raw)}")

    _LOG(f"latest tvshow ({kind}) -> {len(all_items)} items playables")
    return all_items


_TOKEN_RE = re.compile(r'name=["\']?token["\']?\s+value=["\']([^"\']+)', re.I)


# ===== Render Relay (bypass DEFINITIVO del data.find.php) ===================
#
# El AJAX /mvc/controllers/data.find.php devuelve "Denied" para IPs de
# Cloudflare Workers. Solucion: un relay paralelo desplegado en Render.com
# (datacenter US, IP no-CF) que SI puede llamar al AJAX y devuelve el
# JSON real con TODOS los caps de cualquier serie.
#
# Si el usuario configuro `render_relay_url` en settings, esta ruta es la
# PRIMARIA y deberia cubrir el 100% del catalogo wolfmax igual que el
# buscador de la web. Si no configuro nada, caemos a la ruta de catalogo
# de listados (top-100 por categoria) y a Brave/proximity.

def _render_relay_url():
    return (_ADDON.getSetting("render_relay_url") or "").strip().rstrip("/")


def _search_via_relay_catalog(query):
    """Busca via /wfcatalog del Render relay: catalogo cacheado server-side
    (top-100 de cada seccion, incluido /documentales). UNA llamada rapida.
    Es la fuente preferente porque el servidor ya tiene el catalogo crawleado
    y cacheado 30 min -> evita que el TV box crawlee 14 secciones (40s)."""
    base = _render_relay_url()
    if not base:
        return []
    import requests as _rq
    try:
        r = _rq.get(f"{base}/wfcatalog", params={"q": query}, timeout=45)
        _LOG(f"relay /wfcatalog q={query!r} HTTP {r.status_code} "
             f"len={len(r.content)}")
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get("response"):
            return []
        out = []
        for it in (data.get("items") or []):
            u = it.get("url") or ""
            if not u:
                continue
            title = (it.get("title") or "").strip() or u.rsplit("/", 1)[-1]
            kind = _classify(u) or "movie"
            if kind == "movie" and _looks_like_tvshow(title, u):
                kind = "tvshow"
            image = it.get("image") or None
            if image and image.startswith("/"):
                image = _base() + image
            out.append({
                "title": title, "url": u, "kind": kind,
                "image": image, "quality": it.get("quality"),
                "source": SOURCE,
            })
        _LOG(f"_search_via_relay_catalog -> {len(out)} items")
        return out
    except Exception as e:
        _LOG(f"_search_via_relay_catalog error: {e.__class__.__name__}: {e}")
        return []


def _search_via_render_relay(query):
    """Busca via el endpoint /wfsearch del Render relay. Devuelve lista
    normalizada de items (mismo formato que el resto de fuentes) o []
    si no hay relay configurado o falla."""
    base = _render_relay_url()
    if not base:
        return []
    import json as _json
    import requests as _rq
    try:
        url = f"{base}/wfsearch"
        # Pedimos hasta 5 paginas (l=100 por pagina = 500 items max).
        # Wolfmax suele mostrar todo en pg=1 con l=100; mas paginas solo
        # se necesitan para queries muy genericas.
        all_items = []
        seen_urls = set()
        for pg in (1, 2, 3):
            r = _rq.get(url, params={"q": query, "pg": str(pg), "l": "100"},
                        timeout=40)
            _LOG(f"render_relay /wfsearch q={query!r} pg={pg} HTTP "
                 f"{r.status_code} len={len(r.content)}")
            if r.status_code != 200:
                break
            try:
                data = r.json()
            except Exception as e:
                _LOG(f"render_relay JSON parse fail: {e}")
                break
            if not data.get("response"):
                _LOG(f"render_relay no-response: diag={data.get('_diag')}")
                break
            page_items = data.get("items") or []
            if not page_items:
                break
            for it in page_items:
                u = it.get("url") or ""
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                title = (it.get("title") or "").strip() or u.rsplit("/", 1)[-1]
                # GUID puede empezar por movie/, online/, serie-online-*/...
                kind = _classify(u) or "movie"
                if kind == "movie" and _looks_like_tvshow(title, u):
                    kind = "tvshow"
                # Normalizar imagen (puede venir relativa)
                image = it.get("image") or None
                if image and image.startswith("/"):
                    image = _base() + image
                all_items.append({
                    "title":   title,
                    "url":     u,
                    "kind":    kind,
                    "image":   image,
                    "quality": it.get("quality") or None,
                    "source":  SOURCE,
                })
            # Si la pagina vino con menos de 30 items, asumimos que
            # ya no hay mas paginas relevantes
            if len(page_items) < 30:
                break
        _LOG(f"_search_via_render_relay -> {len(all_items)} items totales")
        return all_items
    except Exception as e:
        _LOG(f"_search_via_render_relay error: {e.__class__.__name__}: {e}")
        return []


# ===== Catalogo basado en listados (bypass del data.find.php bloqueado) =====
#
# El AJAX /mvc/controllers/data.find.php devuelve "Denied" para IPs de
# Cloudflare Workers, por lo que NUNCA podemos usarlo. Sin embargo:
#
#   * Las paginas de listado (/series/1080p/, /peliculas/bluray-1080p/,
#     /animacion-manga/, etc.) renderizan SERVER-SIDE las top-100 entradas
#     mas recientes con anchor+img donde:
#         <a href="//wolfmax4k.com/series/1080p/<slug>">
#           <img src="...assets/u/p/c/<id>_<ts>-<Title-With-Hyphens>.jpg">
#
#   * La pagina de aterrizaje de cada serie
#     (/series/<calidad>/<slug>) renderiza SERVER-SIDE TODOS los capitulos
#     como <a href="/online/<id>"> (ej. The Rookie -> 56 caps).
#
# Asi que: catalogo de top-100 por categoria + fan-out a series matched
# = todos los caps de cualquier serie que aparezca en el top-100.
# Para series mas antiguas seguimos cayendo en brave/proximity.

_CATALOG_SECTIONS = [
    "/series/1080p/",
    "/series/4k-2160p/",
    "/series/720p/",
    "/series/480p/",
    "/series/",
    "/animacion-manga/",
    "/animacion-infantil/",
    "/peliculas/bluray-1080p/",
    "/peliculas/4k-2160p/",
    "/peliculas/bluray-720p/",
    "/peliculas/bluray/",
    "/documentales/",
    "/programas-tv/",
    "/telenovelas/",
]

# Bloque <a href="..."> ... <img src="..."> capturando los DOS atributos
_CATALOG_BLOCK_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>'
    r'(?:(?!</a>).){0,1200}?'
    r'<img[^>]+src=["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

# Cache en memoria del catalogo crawleado (TTL 30 min)
_catalog_cache = {"ts": 0.0, "items": []}
_CATALOG_TTL = 30 * 60  # segundos


def _catalog_extract_title_from_image(img_src):
    """De '...assets/u/p/c/15233_1774780024-Irish-Blood.jpg' -> 'Irish Blood'.
    De '...assets/u/p/f/la-batalla-de-oslo--blucher---2025---BluRay-1080p_30_1029.jpg'
       -> 'la batalla de oslo blucher 2025'.
    """
    try:
        leaf = img_src.rsplit("/", 1)[-1]
        leaf = re.sub(r"\.(jpg|jpeg|png|webp).*$", "", leaf, flags=re.I)
        # Series: <id>_<ts>-<Title-With-Hyphens>
        m = re.match(r"^\d+_\d+-(.+)$", leaf)
        if m:
            return re.sub(r"-+", " ", m.group(1)).strip()
        # Peliculas: <slug-with-quality>_<n>_<n>
        leaf = re.sub(r"_\d+_\d+$", "", leaf)
        # Quitar tokens de calidad/ano para hacer matching mas tolerante
        title = re.sub(r"-+", " ", leaf)
        title = re.sub(r"\b(?:bluray|blu-ray|hdtv|web-?dl|hdrip|dvdrip|"
                       r"\d{3,4}p|4k|2160p|1080p|720p|480p|esp|latino|"
                       r"hdr|x264|x265|hevc)\b", "", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip()
        return title
    except Exception:
        return ""


def _catalog_normalize_url(href, page_url):
    """Normaliza href de catalogo a URL absoluta."""
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _base() + href
    if href.startswith("http"):
        return href
    return urljoin(page_url, href)


def _build_catalog():
    """Crawlea las secciones de catalogo en paralelo y devuelve lista de
    items {url, title, kind, image, slug_words}. Cacheado 30 min.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    now = time.time()
    if (now - _catalog_cache["ts"]) < _CATALOG_TTL and _catalog_cache["items"]:
        return _catalog_cache["items"]

    sess = _session()
    base = _base()

    def _fetch(path):
        out = []
        try:
            url = base.rstrip("/") + path
            r = hs.get(sess, url, timeout=15)
            txt = r.content.decode("utf-8", "ignore")
            for href, img_src in _CATALOG_BLOCK_RE.findall(txt):
                full_url = _catalog_normalize_url(href, url)
                if not full_url or "wolfmax4k" not in full_url.lower():
                    continue
                # Filtrar logos / iconos del header
                low_img = img_src.lower()
                if "logo" in low_img or "/temp/img/" in low_img:
                    continue
                kind = _classify(full_url) or ""
                if not kind:
                    continue
                title = _catalog_extract_title_from_image(img_src)
                if not title:
                    # caer al slug de la URL
                    slug = full_url.rstrip("/").rsplit("/", 1)[-1]
                    title = slug.replace("-", " ").strip()
                if len(title) < 2:
                    continue
                img_full = _fix_src(img_src, url)
                out.append({
                    "url":    full_url,
                    "title":  title,
                    "kind":   kind,
                    "image":  img_full,
                    "source": SOURCE,
                })
        except Exception as e:
            _LOG(f"_build_catalog fetch {path} fail: {e.__class__.__name__}")
        return out

    items = []
    seen = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch, p): p for p in _CATALOG_SECTIONS}
        for fut in as_completed(futs):
            for it in fut.result():
                u = it["url"]
                if u in seen:
                    continue
                seen.add(u)
                items.append(it)

    _LOG(f"_build_catalog -> {len(items)} entradas unicas (de "
         f"{len(_CATALOG_SECTIONS)} secciones)")
    _catalog_cache["ts"] = now
    _catalog_cache["items"] = items
    return items


_CATALOG_PLAYABLE_RE = re.compile(
    r"/(movie|online|pelicula|capitulo|episodio|serie-online(?:-[\w-]+)?)/\d+",
    re.I,
)


def _series_landing_caps(landing_url, query_tokens=None):
    """Dada una URL como /series/1080p/the-rookie, devuelve lista de items
    con CADA capitulo (/online/<id>) que la pagina renderiza server-side.
    """
    out = []
    try:
        sess = _session()
        r = hs.get(sess, landing_url, timeout=20)
        txt = r.content.decode("utf-8", "ignore")
        soup = BeautifulSoup(txt, "html.parser")
        seen = set()
        # Usar todos los <a> con href que matchee patron playable
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href:
                continue
            full = href if href.startswith("http") else urljoin(landing_url, href)
            if "wolfmax4k" not in full.lower():
                continue
            if not _CATALOG_PLAYABLE_RE.search(full):
                continue
            if full in seen:
                continue
            seen.add(full)
            label = a.get_text(" ", strip=True)
            # Mejorar label: si el texto esta vacio o es muy corto, usar
            # img.alt o titulo del documento
            if not label or len(label) < 3:
                img = a.find("img")
                if img:
                    label = (img.get("alt") or "").strip()
            if not label:
                # fallback: slug + id
                label = full.rstrip("/").rsplit("/", 1)[-1]
            img_src = None
            img = a.find("img")
            if img:
                raw = (img.get("src") or img.get("data-src")
                       or img.get("data-original") or "").strip()
                img_src = _fix_src(raw, landing_url)
            out.append({
                "title":   re.sub(r"\s+", " ", label).strip(),
                "url":     full,
                "kind":    _classify(full) or "tvshow",
                "image":   img_src,
                "quality": None,
                "source":  SOURCE,
            })
    except Exception as e:
        _LOG(f"_series_landing_caps({landing_url}) fail: "
             f"{e.__class__.__name__}: {e}")
    _LOG(f"_series_landing_caps({landing_url}) -> {len(out)} caps")
    return out


def _search_via_catalog(query):
    """Busqueda VIA CATALOGO: matchea query contra titulos del top-100 de
    cada categoria. Para series-slug matched, sigue el landing y extrae
    todos los caps. Para movies, devuelve la URL directamente.

    Devuelve lista de items playables (tipo `search()`).
    """
    import unicodedata

    def _norm(s):
        if not s:
            return ""
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s.lower().strip())

    q_norm = _norm(query)
    q_tokens = [t for t in re.split(r"[\s\-\._]+", q_norm) if len(t) >= 2]
    if not q_tokens:
        return []

    catalog = _build_catalog()
    if not catalog:
        return []

    # Score por # tokens encontrados (titulo + slug de URL)
    matches = []
    for it in catalog:
        title_n = _norm(it.get("title") or "")
        slug = (it.get("url") or "").rstrip("/").rsplit("/", 1)[-1].lower()
        slug_n = _norm(slug.replace("-", " "))
        hay = title_n + " | " + slug_n
        # Todos los tokens deben aparecer (substring)
        if all(tok in hay for tok in q_tokens):
            matches.append(it)

    _LOG(f"_search_via_catalog: {len(matches)} match en catalogo "
         f"({len(catalog)} entradas) para tokens={q_tokens}")
    if not matches:
        return []

    # Separar: series-landing (necesitan fan-out) vs URLs ya playables
    out = []
    seen_urls = set()
    landing_urls = []
    for m in matches:
        u = m.get("url") or ""
        if _CATALOG_PLAYABLE_RE.search(u):
            if u not in seen_urls:
                seen_urls.add(u)
                out.append(m)
        else:
            # URL tipo /series/<calidad>/<slug> o /peliculas/<calidad>/<slug>
            # -> es un landing, fan-out
            landing_urls.append(u)

    # Fan-out paralelo de landings de series
    if landing_urls:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(6, len(landing_urls))) as pool:
            futs = {pool.submit(_series_landing_caps, u, q_tokens): u
                    for u in landing_urls}
            for fut in as_completed(futs):
                for cap in fut.result():
                    cu = cap.get("url")
                    if cu and cu not in seen_urls:
                        seen_urls.add(cu)
                        out.append(cap)

    _LOG(f"_search_via_catalog -> {len(out)} items playables "
         f"(de {len(landing_urls)} landings + matches directos)")
    return out


def search(query):
    """WolfMax4K search.

    El formulario visible (`/buscar`) solo renderiza una plantilla HTML
    vacia; los resultados REALES se cargan por AJAX contra
    `/mvc/controllers/data.find.php` con form-data:
        _ACTION=buscar, token=<csrf_from_homepage>, q=<query>

    La respuesta es JSON:
        {"response": true,
         "data": {"datafinds": {"0": {"0": {guid, torrentName, calidad, image}, ...}}}}

    Asi que el flujo es:
      1) GET /  -> extraer token del form ffind + cookie PHPSESSID
      2) POST /mvc/controllers/data.find.php con _ACTION+token+q
      3) Parsear JSON y construir items.
    """
    import json as _json
    import unicodedata
    _LOG(f"search: {query!r}")
    sess = _session()
    base = _base()
    errors = []

    # ---- Helper: normalizar para comparacion (sin tildes, minusculas) ----
    def _norm(s):
        if not s:
            return ""
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", s.lower().strip())

    q_norm = _norm(query)
    q_tokens = [t for t in re.split(r"[\s\-\._]+", q_norm) if len(t) >= 2]

    def _matches(title):
        t = _norm(title)
        # AND de tokens — todos deben aparecer como substring
        return all(tok in t for tok in q_tokens) if q_tokens else False

    # === -4) Render Relay (PRIMARY si esta configurado) =====================
    # Llama a data.find.php desde infraestructura no-CF -> devuelve TODOS los
    # caps que da la web. Es la mejor fuente posible. Solo se activa si el
    # usuario ha pegado la URL del relay en settings.
    relay_items = []
    # 0a) Catalogo cacheado server-side (rapido, ~1-2s). Si encuentra
    #     resultados, salimos YA sin tocar las estrategias lentas (brave,
    #     proximity, crawl local). Esto resuelve el caso "rafa" en <3s.
    try:
        cat = _search_via_relay_catalog(query)
        if cat:
            _LOG(f"search relay_catalog -> {len(cat)} items (fast-exit)")
            try:
                wf_index.add(cat)
            except Exception:
                pass
            return cat
    except Exception as e:
        _LOG(f"search relay_catalog error: {e.__class__.__name__}: {e}")
    # 0b) /wfsearch (AJAX data.find.php via ScraperAPI) — suele fallar pero
    #     se intenta por si acaso.
    try:
        relay_items = _search_via_render_relay(query)
        _LOG(f"search render_relay -> {len(relay_items)} items")
        if relay_items:
            try:
                wf_index.add(relay_items)
            except Exception:
                pass
    except Exception as e:
        _LOG(f"search render_relay error: {e.__class__.__name__}: {e}")

    # === -3) Catalogo via listados publicos (PRIMARY) =======================
    # Como el AJAX /mvc/controllers/data.find.php esta bloqueado para IPs
    # CF, replicamos su comportamiento con: catalogo de top-100 entradas
    # por categoria + fan-out a la pagina de aterrizaje de cada serie
    # (renderiza server-side todos los caps). Esto da la MISMA UX que el
    # buscador del navegador para todo lo que este en top-100 reciente.
    # Para titulos antiguos seguimos cayendo en brave/proximity.
    catalog_items = []
    try:
        catalog_items = _search_via_catalog(query)
        _LOG(f"search via_catalog -> {len(catalog_items)} items")
        if catalog_items:
            try:
                wf_index.add(catalog_items)
            except Exception:
                pass
    except Exception as e:
        _LOG(f"search via_catalog error: {e.__class__.__name__}: {e}")

    # === -2) Buscador externo (Brave Search) ================================
    # Brave Search indexa wolfmax4k.com con titulos limpios por pagina
    # (ej. "Alien Earth | Wolfmax4k.com" -> /serie-online-1080p/251281).
    # Hacer `site:wolfmax4k.com <query>` contra brave nos da URL+titulo al
    # instante, sin depender del AJAX interno del sitio (bloqueado para IPs
    # de Cloudflare Workers) ni de que el usuario haya construido un indice.
    brave_responded = False
    brave_items = []
    try:
        brave_items, brave_responded = _search_brave_wrapped(query)
        _LOG(f"search brave -> {len(brave_items)} items "
             f"(responded={brave_responded})")
        if brave_items:
            try:
                wf_index.add(brave_items)
            except Exception:
                pass
    except Exception as e:
        _LOG(f"search brave error: {e.__class__.__name__}: {e}")

    # === Acumulador unico. Los items de Brave (que YA funcionan) entran
    # primero y NUNCA se pierden. Todo lo demas es aditivo.
    merged = []
    seen_m = set()

    def _accum(new_items, tag):
        added = 0
        for it in (new_items or []):
            u = it.get("url")
            if u and u not in seen_m:
                seen_m.add(u)
                merged.append(it)
                added += 1
        _LOG(f"search accum[{tag}]: +{added} / {len(new_items or [])} "
             f"-> total {len(merged)}")

    # 0) Render relay (si esta configurado, es la fuente mas completa)
    _accum(relay_items, "render_relay")

    # 0.5) Catalogo (lo mas relevante: top-100 actual + caps reales)
    _accum(catalog_items, "catalog")

    # 1) Brave items (originales preservados aqui)
    _accum(brave_items, "brave")

    def _filter_expand(sibling_items):
        if not q_tokens:
            return sibling_items
        out = []
        for it in sibling_items:
            tn = _norm(it.get("title") or "")
            url_low = (it.get("url") or "").lower()
            if any(tok in tn or tok in url_low for tok in q_tokens):
                out.append(it)
        return out

    # 2) Expansion #1 desde semillas Brave/DDG/Bing — aditiva.
    #    Ventana adaptativa: si solo hay 1 semilla, ampliamos a window=160
    #    (cubre publicaciones en batch que excedan 80 IDs entre extremos).
    #    SKIP: si el catalogo ya nos dio >=10 items, los caps de la serie ya
    #    estan cubiertos por el fan-out del landing — proximity solo añadiria
    #    latencia (cada scan = 160 fetches via proxy ~ 60s) sin items nuevos.
    # Saltar proximity si ya tenemos cobertura suficiente del relay+catalogo
    rich_sources = len(relay_items) + len(catalog_items)
    skip_expansion = rich_sources >= 10
    if skip_expansion:
        _LOG(f"search: skip expansion#1+#2 (relay+catalog ya rindio "
             f"{rich_sources} items)")
    elif not skip_expansion:
        try:
            # Combinar semillas de catalogo + brave (catalogo aporta IDs reales
            # de episodios que mejoran muchisimo la expansion proximity).
            combined_seeds = []
            _seen_seed = set()
            for it in (catalog_items or []) + (brave_items or []):
                u = it.get("url")
                if u and u not in _seen_seed:
                    _seen_seed.add(u)
                    combined_seeds.append(it)
            seed_window = 160 if len(combined_seeds) <= 1 else 80
            sibling_items = _expand_siblings(combined_seeds, query,
                                             window=seed_window)
            sibling_items = _filter_expand(sibling_items)
            _LOG(f"search expansion#1 filter: {len(sibling_items)} pasan "
                 f"tokens {q_tokens} (window={seed_window})")
            _accum(sibling_items, "expand1")
            if sibling_items:
                try:
                    wf_index.add(sibling_items)
                except Exception:
                    pass
                brave_responded = True
        except Exception as e:
            _LOG(f"search expansion#1 error: {e.__class__.__name__}: {e}")

    # 3) Indice local (items previamente vistos)
    try:
        idx_items = wf_index.search(query, limit=500)
        _accum(idx_items, "indice")
    except Exception as e:
        _LOG(f"wf_index.search error: {e}")
        idx_items = []

    # 4) Expansion #2: SOLO si la #1 fallo en encontrar siblings y aun
    #    tenemos < 3 items (caso "seed aislada"). Re-usamos los items que
    #    haya en el indice como semillas adicionales (puede que el indice
    #    tenga IDs cercanos a otros caps que la seed actual no alcanzaba).
    #    max_seeds=2 + window=80 -> 320 fetches max -> ~6-8s via proxy.
    try:
        if not skip_expansion and len(merged) <= 2 and idx_items:
            seeds2 = list(idx_items)[:6]  # acotar
            sibling2 = _expand_siblings(seeds2, query, window=80)
            sibling2 = _filter_expand(sibling2)
            _LOG(f"search expansion#2 filter: {len(sibling2)} (semillas indice={len(seeds2)})")
            _accum(sibling2, "expand2")
            if sibling2:
                try:
                    wf_index.add(sibling2)
                except Exception:
                    pass
    except Exception as e:
        _LOG(f"search expansion#2 error: {e.__class__.__name__}: {e}")

    # --- Enriquecer titulos via H1 ---
    # Brave a veces devuelve snippets con titulo generico ("Arcane") en vez
    # del titulo real del capitulo ("Arcane [HDTV 1080p][Cap.204]"). Como
    # consecuencia el agrupador no puede extraer (season,episode) y el item
    # sale como "99x99". Arreglo: para todo item con URL ID-based cuyo titulo
    # NO tenga marca de episodio, descargamos el H1 en paralelo via el worker.
    try:
        _enrich_titles_inplace(merged)
    except Exception as e:
        _LOG(f"_enrich_titles_inplace err: {e}")

    # Orden: primero items con numero de capitulo detectable, ordenados por
    # (season, episode). Luego el resto por titulo.
    def _ep_key(it):
        m = re.search(r"[Cc]ap\.?\s*(\d{1,3})(\d{2})", it.get("title") or "")
        if m:
            return (0, int(m.group(1)), int(m.group(2)))
        m = re.search(r"(\d{1,2})x(\d{2,3})", it.get("title") or "")
        if m:
            return (0, int(m.group(1)), int(m.group(2)))
        return (1, 0, 0)
    merged.sort(key=lambda it: (_ep_key(it), (it.get("title") or "").lower()))

    if merged:
        _LOG(f"search merged (brave+indice) -> {len(merged)} items")
        return merged

    # Si brave respondio pero sin matches relevantes y el indice tampoco
    # tiene nada, NO caer al fallback de listados (devuelve items recientes
    # sin relacion). Solo fallback si brave no respondio (error de red/403).
    if brave_responded:
        _LOG("search: brave+indice 0 matches, sin fallback")
        return []

    # === -1) Indice local persistente =======================================
    # Si el usuario ha construido el indice completo (o ha navegado mucho),
    # el catalogo completo vive localmente. Consulta instantanea. Unimos
    # esos resultados con el scan en vivo para no perder lo recien agregado.
    idx_items = []
    try:
        idx_items = wf_index.search(query, limit=500)
        _LOG(f"search indice local -> {len(idx_items)} items")
    except Exception as e:
        _LOG(f"wf_index.search error: {e}")

    # === 0) BUSQUEDA POR LISTADOS (metodo principal) ========================
    #
    # El endpoint AJAX /mvc/controllers/data.find.php esta protegido contra
    # IPs de Cloudflare Workers: responde {"message":"Denied"} independien-
    # temente del token/cookies que se envien. Al no poder quitar la huella
    # "cf-worker" que CF añade automaticamente a subrequests hacia zonas CF,
    # la unica via fiable a traves del worker es recorrer los listados
    # publicos (/peliculas/*, /series/*, /documentales/) que SI responden
    # HTML completo con titulos, y filtrar localmente por el query.
    #
    # Limitacion conocida: solo encuentra titulos que esten en la primera
    # pagina de cada seccion. Suficiente para estrenos y novedades (que es
    # el 95% de las consultas reales).
    # Solo secciones que dan URLs PLAYABLES (/movie/<id>, /online/<id>,
    # /serie-online-*/<id>). Las rutas /series/<slug> y /series/<calidad>/<slug>
    # son trampa: su pagina individual se puebla via data.find.php (que esta
    # bloqueado por IP de Cloudflare Workers) asi que aunque el slug aparezca
    # en la busqueda, hacer click no reproduce nada.
    #
    # Home + 4 calidades de peliculas + documentales + 4 categorias cortas
    # (programas-tv/telenovelas/animacion-*) = ~1000 items playables por
    # busqueda, ~400 unicos tras dedup. Incluye 37 URLs serie-online-*/<id>
    # de la home que SON episodios individuales reproducibles.
    SEARCH_SECTIONS = [
        "",                        # home (142 items, mezcla peli+series)
        "peliculas/bluray",
        "peliculas/bluray-720p",
        "peliculas/bluray-1080p",
        "peliculas/4k-2160p",
        "documentales",
        "programas-tv",
        "telenovelas",
        "animacion-manga",
        "animacion-infantil",
    ]

    # Regex que reconoce una URL "playable": movies y episodios con ID
    # numerico. Las URLs /series/<slug> o /peliculas/<slug> SIN id sufijo
    # caen en manos del scraper detail() que no encuentra descargas.
    _PLAYABLE_URL_RE = re.compile(
        r"/(movie|online|pelicula|capitulo|episodio|serie-online(?:-[\w-]+)?)/\d+",
        re.I,
    )

    if q_tokens:
        _LOG(f"search listings tokens={q_tokens}")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_section(section):
            try:
                soup, page_url = _get(section)
                return section, _items_from_soup(soup, page_url)
            except Exception as e:
                _LOG(f"search section {section} fail: {e.__class__.__name__}")
                return section, []

        all_items = []
        seen_urls = set()
        # 10 fetches en paralelo -> ~2-4s en buena red.
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_fetch_section, s): s for s in SEARCH_SECTIONS}
            for fut in as_completed(futures):
                section, raw_items = fut.result()
                matched = 0
                dropped_dead = 0
                for it in raw_items:
                    u = it.get("url") or ""
                    if u in seen_urls:
                        continue
                    # Excluir URLs que no son playables (dead-end /series/<slug>)
                    if not _PLAYABLE_URL_RE.search(u):
                        dropped_dead += 1
                        continue
                    if _matches(it.get("title")):
                        seen_urls.add(u)
                        all_items.append(it)
                        matched += 1
                _LOG(f"search section {section}: {len(raw_items)} items, "
                     f"{matched} match, {dropped_dead} skipped (not playable)")

        # Union con resultados del indice local (dedup por url)
        if idx_items:
            for it in idx_items:
                u = it.get("url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_items.append(it)
        if all_items:
            _LOG(f"search listings+indice -> {len(all_items)} items")
            return all_items
        errors.append("listings: 0 match")

    # Si no habia tokens validos pero el indice tiene resultados, devolverlos
    if idx_items:
        _LOG(f"search solo-indice -> {len(idx_items)} items")
        return idx_items

    # === 1) Fallback: endpoint dedicado /wfsearch del worker ================
    # Esta via actualmente suele dar "Denied" por el bloqueo de IPs CF,
    # pero se mantiene por si el upstream relaja la restriccion.
    try:
        proxy_base = (_ADDON.getSetting("proxy_url") or "").strip().rstrip("/")
        if proxy_base:
            import requests as _rq
            url = f"{proxy_base}/wfsearch"
            r = _rq.get(url, params={"q": query}, timeout=30,
                        headers={"Accept-Encoding": "identity"})
            _LOG(f"search /wfsearch HTTP {r.status_code} len={len(r.content)} "
                 f"tok={r.headers.get('x-mw-wf-token','?')} "
                 f"ajax={r.headers.get('x-mw-wf-ajax-status','?')}")
            raw = (r.text or "").strip().lstrip("\ufeff")
            try:
                data = _json.loads(raw or "{}")
            except Exception as je:
                _LOG(f"wfsearch json parse fail: {je}; head={raw[:200]!r}")
                data = None
            if data and data.get("response"):
                datafinds = (data.get("data") or {}).get("datafinds") or {}
                items = []
                seen = set()
                for outer_k in sorted(datafinds.keys(), key=lambda x: (len(x), x)):
                    group = datafinds[outer_k] or {}
                    if not isinstance(group, dict):
                        continue
                    for inner_k in sorted(group.keys(), key=lambda x: (len(x), x)):
                        row = group[inner_k] or {}
                        guid = (row.get("guid") or "").strip().lstrip("/")
                        if not guid:
                            continue
                        url = urljoin(base + "/", guid)
                        if url in seen:
                            continue
                        seen.add(url)
                        kind = _classify("/" + guid) or "movie"
                        title = (row.get("torrentName") or "").strip() or guid
                        image = (row.get("image") or "").strip() or None
                        quality = (row.get("calidad") or "").strip() or None
                        items.append({
                            "title":   title,
                            "url":     url,
                            "kind":    kind,
                            "image":   image,
                            "quality": quality,
                            "source":  SOURCE,
                        })
                if items:
                    _LOG(f"search /wfsearch -> {len(items)} items")
                    return items
            errors.append("wfsearch: sin items")
    except Exception as e:
        errors.append(f"wfsearch: {e.__class__.__name__}: {e}")

    # 1) GET homepage -> token + cookie
    try:
        r0 = hs.get(sess, base + "/")
        token_m = _TOKEN_RE.search(r0.text or "")
        token = token_m.group(1) if token_m else ""
        _LOG(f"search token={'OK' if token else 'MISSING'} cookies={len(sess.cookies)}")
    except Exception as e:
        token = ""
        errors.append(f"home: {e.__class__.__name__}")

    # 2) POST al endpoint AJAX real
    if token:
        try:
            r = hs.post(
                sess,
                urljoin(base + "/", "mvc/controllers/data.find.php"),
                data={"_ACTION": "buscar", "token": token, "q": query},
                headers={
                    "Referer":          base + "/",
                    "Origin":           base,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept":           "application/json, text/javascript, */*; q=0.01",
                    "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            # El upstream suele devolver JSON con charset mal declarado; en
            # ese caso r.text puede venir con BOM o espacios. Sanitizamos.
            raw = (r.text or "").strip().lstrip("\ufeff")
            _LOG(f"search AJAX HTTP {r.status_code} len={len(raw)} "
                 f"ct={r.headers.get('content-type','?')!r}")
            try:
                data = _json.loads(raw or "{}")
            except Exception as je:
                _LOG(f"search json parse fail: {je}; head={raw[:200]!r}")
                data = None
            if data and data.get("response"):
                datafinds = (data.get("data") or {}).get("datafinds") or {}
                # datafinds is a dict-of-dicts: {"0": {"0": {...}, "1": {...}, ...}}
                items = []
                seen = set()
                for outer_k in sorted(datafinds.keys(), key=lambda x: (len(x), x)):
                    group = datafinds[outer_k] or {}
                    if not isinstance(group, dict):
                        continue
                    for inner_k in sorted(group.keys(), key=lambda x: (len(x), x)):
                        row = group[inner_k] or {}
                        guid = (row.get("guid") or "").strip().lstrip("/")
                        if not guid:
                            continue
                        url = urljoin(base + "/", guid)
                        if url in seen:
                            continue
                        seen.add(url)
                        kind = _classify("/" + guid) or "movie"
                        title = (row.get("torrentName") or "").strip() or guid
                        image = (row.get("image") or "").strip() or None
                        quality = (row.get("calidad") or "").strip() or None
                        items.append({
                            "title":   title,
                            "url":     url,
                            "kind":    kind,
                            "image":   image,
                            "quality": quality,
                            "source":  SOURCE,
                        })
                if items:
                    _LOG(f"search data.find.php -> {len(items)} items")
                    return items
            errors.append(f"AJAX: 0 items (HTTP {r.status_code})")
        except Exception as e:
            errors.append(f"AJAX: {e.__class__.__name__}: {e}")

    # 1) GET homepage -> token + cookie
    try:
        r0 = hs.get(sess, base + "/")
        token_m = _TOKEN_RE.search(r0.text or "")
        token = token_m.group(1) if token_m else ""
        _LOG(f"search token={'OK' if token else 'MISSING'} cookies={len(sess.cookies)}")
    except Exception as e:
        token = ""
        errors.append(f"home: {e.__class__.__name__}")

    # 2) POST /buscar with the form data
    if token:
        try:
            r = hs.post(
                sess,
                urljoin(base + "/", "buscar"),
                data={"_ACTION": "buscar", "token": token, "q": query},
            )
            soup = BeautifulSoup(r.content, "html.parser")
            items = _items_from_soup(soup, r.url)
            if items:
                _LOG(f"search POST -> {len(items)} items")
                return items
            errors.append(f"POST: 0 items (HTTP {r.status_code})")
        except Exception as e:
            errors.append(f"POST: {e.__class__.__name__}")

    # 3) GET fallbacks (some mirrors expose simpler endpoints)
    attempts = [
        (f"buscar/{urlquote(query)}", None),
        (f"search/{urlquote(query)}", None),
        ("buscar", {"q": query}),
        ("", {"q": query}),
        ("", {"s": query}),
    ]
    for path, params in attempts:
        try:
            soup, url = _get(path, params=params)
            items = _items_from_soup(soup, url)
            if items:
                _LOG(f"search GET {path!r} params={params} -> {len(items)} items")
                return items
        except Exception as e:
            errors.append(f"{path!r} {params}: {e.__class__.__name__}")

    _LOG("search fallida: " + " | ".join(errors))
    return []


# === Buscador externo: Brave Search =========================================
#
# Brave tiene indexado el catalogo de wolfmax4k con metadatos limpios:
#   title  = "Alien Earth | Wolfmax4k.com"
#   url    = https://wolfmax4k.com/serie-online-1080p/251281
# Hacer `site:wolfmax4k.com <query>` y parsear los snippets nos da
# busqueda instantanea, como cualquier buscador real.
#
# Lo invocamos a traves del mismo Cloudflare Worker relay que ya usamos
# para esquivar bloqueos ISP (asi el endpoint es HTTPS->HTTPS y no expone
# la IP del usuario).

_BRAVE_SEARCH_URL = "https://search.brave.com/search"
_WF_HOST_RE = re.compile(r"https?://(?:www\.)?wolfmax4k\.com(/[^\s\"'#?]+)", re.I)


def _slugify(s):
    """Slug estilo wolfmax4k: minusculas, sin tildes, espacios/puntuacion a '-'."""
    import unicodedata as _u
    s = _u.normalize("NFKD", s or "")
    s = "".join(c for c in s if not _u.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _strip_show_markers(title):
    """Quita marcas de capitulo/calidad del titulo para obtener el nombre base."""
    if not title:
        return ""
    t = re.sub(r"\|.*$", "", title)          # "Arcane | Wolfmax4k.com" -> "Arcane"
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", t)
    t = re.sub(r"\bS\d{1,2}E\d{1,3}\b", "", t, flags=re.I)
    t = re.sub(r"\b\d{1,2}\s*[xX]\s*\d{1,3}\b", "", t)
    t = re.sub(r"\bCap[ií]?tulos?\s*\d+(?:[-\s]+\d+)?", "", t, flags=re.I)
    t = re.sub(r"\bTemporada\s*\d+", "", t, flags=re.I)
    t = re.sub(r"\bEpisodios?\s*\d+", "", t, flags=re.I)
    t = re.sub(r"\b(HDTV|WEB-?DL|WEB-?Rip|BluRay|BDRip|BRRip|HEVC|x265|x264|"
               r"4K|2160p|1080p|720p|480p|HDR|DV|Latino|Castellano|Dual|VOSE)\b",
               "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" -.|")
    return t


# Rutas de "serie contenedor" (pagina que lista todos los capitulos de una serie).
# El orden importa: probamos primero las de calidad mas alta.
_SERIES_CONTAINER_PATHS = (
    "series/4k-2160p/{slug}",
    "series/1080p/{slug}",
    "series/720p/{slug}",
    "series/{slug}",
    "descargar/series-4k/{slug}",
    "descargar/series-1080p/{slug}",
    "descargar/series-720p/{slug}",
    "descargar/series/{slug}",
)


def _probe_series_container(slug, timeout=8):
    """Prueba varias URLs candidatas para la pagina contenedora de la serie.
    Devuelve la primera que responda 200 con contenido razonable, o None.

    Criterio "razonable": HTML >10KB y el slug debe aparecer en la URL final
    (si hay redirect) o en el path — descarta paginas 404 que wolfmax4k sirve
    con 200 pero son la home.
    """
    if not slug:
        return None
    from concurrent.futures import ThreadPoolExecutor, as_completed
    base = _base()
    sess = _session()

    def _try(path):
        url = f"{base}/{path.format(slug=slug)}"
        try:
            r = hs.get(sess, url, timeout=timeout)
            if r.status_code != 200:
                return None
            body = r.content or b""
            if len(body) < 8000:
                return None
            # Validar que el slug siga presente en la URL final (si CF/WF
            # redirigio a la home al no encontrar, el slug desaparece)
            final = (r.url or "").lower()
            if slug not in final:
                return None
            # Validar que el body mencione el slug (doble check)
            text_low = body.decode("utf-8", "ignore").lower()
            if slug.replace("-", " ") not in text_low and slug not in text_low:
                return None
            return url
        except Exception:
            return None

    # Probar todas las rutas en paralelo y devolver la primera valida
    # segun el orden de _SERIES_CONTAINER_PATHS.
    results = {}
    with ThreadPoolExecutor(max_workers=len(_SERIES_CONTAINER_PATHS)) as ex:
        futs = {ex.submit(_try, p): p for p in _SERIES_CONTAINER_PATHS}
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                val = fut.result()
                if val:
                    results[p] = val
            except Exception:
                pass
    for p in _SERIES_CONTAINER_PATHS:
        if p in results:
            return results[p]
    return None


def _breadcrumb_from_jsonld(soup):
    """Extrae la URL de 'parent breadcrumb' del JSON-LD de la pagina.
    wolfmax4k (como casi todos los sites con SEO) incluye un schema
    BreadcrumbList. El penultimo elemento es la pagina padre.
    """
    import json as _json
    for s in soup.find_all("script", type="application/ld+json"):
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = _json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if not isinstance(d, dict):
                continue
            # Puede ser BreadcrumbList directa o anidada en @graph
            candidates = []
            if d.get("@type") == "BreadcrumbList":
                candidates.append(d)
            for g in d.get("@graph", []) if isinstance(d.get("@graph"), list) else []:
                if isinstance(g, dict) and g.get("@type") == "BreadcrumbList":
                    candidates.append(g)
            for bc in candidates:
                elements = bc.get("itemListElement") or []
                if len(elements) < 2:
                    continue
                # Ordenar por position si existe
                try:
                    elements = sorted(elements,
                                      key=lambda e: int(e.get("position", 0)))
                except Exception:
                    pass
                # El penultimo es el contenedor (el ultimo es la pagina actual)
                parent = elements[-2] if len(elements) >= 2 else None
                if not parent:
                    continue
                url = parent.get("item") or parent.get("@id")
                if isinstance(url, dict):
                    url = url.get("@id") or url.get("url")
                if url and isinstance(url, str) and "wolfmax4k" in url:
                    return url
    return None


def _discover_series_container(items, query):
    """Localiza la URL de la pagina contenedora de la serie.
    Devuelve (url, titulo) o (None, None).

    Dos estrategias independientes (cualquiera que funcione sirve):
      1) JSON-LD BreadcrumbList desde una semilla de Brave — fiable, usado
         por wolfmax4k para SEO.
      2) Slug probing: generamos candidatos de slug (del titulo de Brave y
         del query) y probamos /series/{slug}, /series/1080p/{slug}, etc.

    NUNCA usamos regex sobre anchors arbitrarios del HTML (eso pillaba
    links de la barra lateral "series relacionadas" como contenedor padre).
    """
    # 1) JSON-LD breadcrumb desde la primera semilla utilizable
    seed_urls = [it.get("url") for it in items if it.get("url")]
    for seed in seed_urls[:2]:
        try:
            r = hs.get(_session(), seed, timeout=12)
            soup = BeautifulSoup(r.content, "html.parser")
            parent = _breadcrumb_from_jsonld(soup)
            if parent:
                # Validar: debe parecer una pagina de serie, no la home
                pl = parent.lower()
                if "/series" in pl or "/descargar/serie" in pl:
                    _LOG(f"_discover_series_container: jsonld -> {parent}")
                    return parent, None
        except Exception as e:
            _LOG(f"_discover_series_container jsonld fail {seed}: {e}")

    # 2) Slug probing: reunir candidatos unicos de slugs
    slugs = []
    seen_s = set()
    for it in items[:4]:
        base = _strip_show_markers(it.get("title", ""))
        if base:
            s = _slugify(base)
            if s and len(s) >= 3 and s not in seen_s:
                seen_s.add(s)
                slugs.append(s)
    qs = _slugify(query)
    if qs and len(qs) >= 3 and qs not in seen_s:
        seen_s.add(qs)
        slugs.append(qs)

    for slug in slugs:
        url = _probe_series_container(slug)
        if url:
            _LOG(f"_discover_series_container: slug probe -> {url} (slug={slug})")
            return url, slug

    _LOG(f"_discover_series_container: nada para query={query!r} items={len(items)}")
    return None, None


def _fanout_series_episodes(container_url):
    """Dado una URL de contenedor de serie, devuelve lista de items (dicts)
    para cada capitulo playable enlazado desde la pagina.

    ESTRICTO: solo aceptamos URLs cuyo path sea hijo/nieto del container_url
    (evita recoger items de la barra lateral de series relacionadas).
    """
    items = []
    try:
        r = hs.get(_session(), container_url, timeout=20)
        soup = BeautifulSoup(r.content, "html.parser")
        from urllib.parse import urlparse as _urlparse
        container_path = _urlparse(container_url).path.rstrip("/")
        if not container_path:
            return []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = href if href.startswith("http") else urljoin(container_url, href)
            if "wolfmax4k" not in full:
                continue
            if full in seen or full.rstrip("/") == container_url.rstrip("/"):
                continue
            full_path = _urlparse(full).path.rstrip("/")
            # Solo hijos/nietos del contenedor — filtra side-panel
            if not full_path.startswith(container_path + "/"):
                continue
            seen.add(full)
            m = _WF_HOST_RE.search(full)
            canon = (_base() + m.group(1).rstrip("/")) if m else full
            label = a.get_text(" ", strip=True) or canon.rsplit("/", 1)[-1]
            items.append({"url": canon, "title": label, "kind": "tvshow"})
    except Exception as e:
        _LOG(f"_fanout_series_episodes({container_url}) fail: {e}")
    return items


def _siblings_by_id_proximity(seed_items, query, max_seeds=2, window=80,
                              workers=20):
    """ESCANEO ACTIVO de IDs vecinas.

    Hallazgo confirmado (probe12): wolfmax4k publica en BATCH — los capitulos
    de una misma serie ocupan IDs CONSECUTIVAS (p.ej. /online/235629=Cap.204,
    235630=Cap.205, 235631=Cap.206 son los 3 episodios de Arcane que existen
    en el sitio). Pero la pagina-semilla NO enlaza a sus hermanas (estan
    aisladas), asi que no basta con scrapear anchors.

    Estrategia: para cada semilla con `/<base>/<id>`, probamos en paralelo
    todas las URLs `/<base>/<id±N>` y nos quedamos con las que respondan
    HTML real (>15KB, sin "404.webp") cuyo <h1> contenga TODOS los tokens
    del query (normalizados).

    window=80 -> 161 fetches por semilla. Con 20 workers via el proxy CF
    son ~3-4s. max_seeds=2 -> ~6-8s en el peor caso. Suficiente para Kodi.
    """
    import unicodedata
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not query:
        return []

    def _norm(s):
        s = unicodedata.normalize("NFKD", s or "")
        return "".join(c for c in s if not unicodedata.combining(c)).lower()

    q_norm = _norm(query)
    q_tokens = [t for t in re.split(r"[\s\-\._]+", q_norm) if len(t) >= 2]
    if not q_tokens:
        return []

    # Patrones de URL ID-based que aceptamos como semilla. Capturamos el
    # path-base entero (sin la ID) para reusarlo en el escaneo.
    SEED_RE = re.compile(
        r"^(?P<base>https?://[^/]+/(?:online|capitulo|episodio|movie|pelicula|"
        r"serie-online(?:-[\w-]+)?))/(?P<id>\d+)/?$",
        re.I,
    )

    # Deduplicar semillas por (base_path, id) para no escanear la misma
    # vecindad varias veces si Brave devuelve ambos /online/N y otra version.
    seeds = []
    seen_seed = set()
    for it in seed_items or []:
        u = (it.get("url") or "").rstrip("/")
        m = SEED_RE.match(u)
        if not m:
            continue
        key = (m.group("base"), int(m.group("id")))
        if key in seen_seed:
            continue
        seen_seed.add(key)
        seeds.append(key)
        if len(seeds) >= max_seeds:
            break

    if not seeds:
        return []

    out = []
    out_seen = set()
    sess = _session()

    def _probe(base, oid):
        try:
            url = f"{base}/{oid}"
            r = hs.get(sess, url, timeout=10)
            body = r.content or b""
            if r.status_code != 200 or len(body) < 15000:
                return None
            text = body.decode("utf-8", "ignore")
            if "404.webp" in text:
                return None
            mh = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S | re.I)
            if not mh:
                return None
            h1 = re.sub(r"<[^>]+>", " ", mh.group(1))
            h1 = re.sub(r"\s+", " ", h1).strip()
            if not h1:
                return None
            tn = _norm(h1)
            if not all(tok in tn for tok in q_tokens):
                return None
            return url, h1
        except Exception:
            return None

    for base, seed_id in seeds:
        lo = max(1, seed_id - window)
        hi = seed_id + window
        ids = [i for i in range(lo, hi + 1) if i != seed_id]
        _LOG(f"_siblings_by_id_proximity: scan {base}/[{lo}..{hi}] "
             f"({len(ids)} ids, tokens={q_tokens})")
        found = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_probe, base, i): i for i in ids}
            for fut in as_completed(futs):
                res = fut.result()
                if not res:
                    continue
                url, h1 = res
                # Canonicalizar al dominio configurado
                hm = _WF_HOST_RE.search(url)
                canon = (_base() + hm.group(1).rstrip("/")) if hm else url
                if canon in out_seen:
                    continue
                out_seen.add(canon)
                # Calidad
                quality = None
                mq = _QUALITY_RE.search(h1)
                if mq:
                    quality = mq.group(1)
                if not quality:
                    low = canon.lower()
                    if "/serie-online-4k" in low:
                        quality = "4K"
                    elif "/serie-online-1080p" in low:
                        quality = "1080p"
                    elif "/serie-online-720p" in low:
                        quality = "720p"
                    elif "/serie-online-hd" in low:
                        quality = "HD"
                # Tipo: si tiene marca de capitulo -> tvshow
                kind = "tvshow" if _looks_like_tvshow(h1, canon) else (
                    _classify(canon) or "tvshow")
                out.append({
                    "title":   h1,
                    "url":     canon,
                    "kind":    kind,
                    "image":   None,
                    "quality": quality,
                    "source":  SOURCE,
                })
                found += 1
        _LOG(f"_siblings_by_id_proximity: {base}/{seed_id} -> {found} hermanos")
    return out


def _expand_siblings(items, query, window=80):
    """Intenta expandir con TODAS las estrategias en paralelo. Devuelve
    union sin duplicar. Las estrategias son independientes y nunca tocan
    los items originales.

    `window` controla el rango +/-N de IDs que escanea proximity. Por
    defecto 80; subir a 160 cuando hay pocas semillas (cobertura mayor a
    costa de mas requests).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if not items and not query:
        return []
    # max_seeds escalado con cantidad de items disponibles para que el
    # 2do barrido use mas semillas (cuando ya conocemos varios IDs cercanos
    # entre si, basta con 2-3; cuando solo conocemos 1-2 lejanos, ampliamos).
    max_seeds = 6 if len(items) >= 3 else 3
    strategies = {
        "container": lambda: _fanout_from_container(items, query),
        "proximity": lambda: _siblings_by_id_proximity(
            items, query, max_seeds=max_seeds, window=window),
    }
    out = []
    seen = set()
    with ThreadPoolExecutor(max_workers=len(strategies)) as ex:
        futs = {ex.submit(fn): name for name, fn in strategies.items()}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                got = fut.result() or []
                new = 0
                for it in got:
                    u = it.get("url")
                    if u and u not in seen:
                        seen.add(u)
                        out.append(it)
                        new += 1
                _LOG(f"_expand_siblings[{name}]: +{new}/{len(got)}")
            except Exception as e:
                _LOG(f"_expand_siblings[{name}] err: {e}")
    return out


def _fanout_from_container(items, query):
    container_url, _ = _discover_series_container(items, query)
    if not container_url:
        return []
    return _fanout_series_episodes(container_url)


# === DuckDuckGo HTML fallback =============================================
# Brave rate-limita (429) cuando hay varias consultas seguidas. Cuando eso
# pasa, nos quedamos sin seeds para proximity -> sin resultados. DDG HTML
# (html.duckduckgo.com/html/) es un endpoint sin API-key, sin rate-limit
# agresivo y que indexa wolfmax4k con snippets similares a Brave.

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"


def _search_ddg(query, max_results=25):
    """DuckDuckGo HTML-only. Devuelve items con la misma estructura que
    _search_brave. Usado como fallback cuando Brave da 429."""
    import requests
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
        "Accept-Encoding": "identity",
    }
    data = {"q": f"site:wolfmax4k.com {query}",
            "kl": "es-es",
            "df": ""}
    # DDG HTML requiere POST con form-urlencoded; el GET devuelve 202
    # (anti-bot). Referer=url propio mejora la aceptacion.
    headers["Referer"] = _DDG_HTML_URL
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    proxy_base = (_ADDON.getSetting("proxy_url") or "").strip().rstrip("/")
    r = None
    # 1) Directo (POST)
    try:
        r = requests.post(_DDG_HTML_URL, data=data, headers=headers,
                          timeout=15, allow_redirects=True)
        if r.status_code != 200 or len(r.content) < 2000:
            _LOG(f"ddg direct POST: HTTP {r.status_code} len={len(r.content)}, "
                 "trying proxy")
            r = None
    except Exception as e:
        _LOG(f"ddg direct fail: {e.__class__.__name__}")
        r = None
    # 2) Fallback via worker (POST no soportado -> usamos GET con form en URL)
    if r is None and proxy_base:
        try:
            from urllib.parse import urlencode as _ue, quote as _q
            full = _DDG_HTML_URL + "?" + _ue(data)
            proxied = proxy_base + "/?u=" + _q(full, safe="")
            r = requests.get(proxied, headers=headers, timeout=25)
            if r.status_code != 200 or len(r.content) < 2000:
                _LOG(f"ddg proxied GET: HTTP {r.status_code} len={len(r.content)}")
                r = None
        except Exception as e:
            _LOG(f"ddg proxied fail: {e.__class__.__name__}")
            r = None
    if r is None:
        return []

    soup = BeautifulSoup(r.content, "html.parser")
    # DDG resultados: <a class="result__a" href="..."> ... </a>
    # El href suele ser un redirect /l/?uddg=<url_encoded>&rut=...
    items = []
    seen = set()
    # Patron de URL ID-playable
    id_pat = re.compile(
        r"/(?:movie|online|pelicula|capitulo|episodio|"
        r"serie-online(?:-[\w-]+)?)/\d+", re.I)
    import unicodedata as _u
    def _n(s):
        s = _u.normalize("NFKD", s or "")
        return "".join(c for c in s if not _u.combining(c)).lower()
    qn = _n(query)
    q_tokens = [t for t in re.split(r"[\s\-\._]+", qn) if len(t) >= 3]

    for a in soup.select("a.result__a"):
        href = (a.get("href") or "").strip()
        title = a.get_text(" ", strip=True)
        # DDG devuelve redirects: //duckduckgo.com/l/?uddg=<real>
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                from urllib.parse import unquote as _uq
                href = _uq(m.group(1))
        elif href.startswith("//"):
            href = "https:" + href
        if "wolfmax4k" not in href.lower():
            continue
        # Solo URLs ID-based
        if not id_pat.search(href):
            continue
        # Canonicalizar dominio
        hm = _WF_HOST_RE.search(href)
        canon = _base() + hm.group(1).rstrip("/") if hm else href
        if canon in seen:
            continue
        # Filtro tokens
        tn = _n(title)
        if q_tokens and not all(t in tn or t in _n(canon) for t in q_tokens):
            continue
        seen.add(canon)
        # Limpiar sufijo "| Wolfmax4k.com" si lo hay
        title = re.sub(r"\s*\|\s*W(?:ww\.)?olfmax4k\.com\s*$", "",
                       title, flags=re.I).strip()
        kind = _classify(canon) or "movie"
        if kind == "movie" and _looks_like_tvshow(title, canon):
            kind = "tvshow"
        items.append({
            "title":   title or canon.rsplit("/", 1)[-1],
            "url":     canon,
            "kind":    kind,
            "image":   None,
            "quality": None,
            "source":  SOURCE,
        })
        if len(items) >= max_results:
            break
    _LOG(f"ddg: {len(items)} items para {query!r}")
    return items


_BING_URL = "https://www.bing.com/search"


def _search_bing(query, max_results=25):
    """Bing HTML SERP como tercer fallback cuando Brave (429) y DDG (202)
    ambos rechazan. Bing HTML es menos agresivo con anti-bot y tambien
    indexa wolfmax4k. Devuelve items con la misma estructura."""
    import requests
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
        "Accept-Encoding": "identity",
        "Referer": "https://www.bing.com/",
    }
    params = {"q": f"site:wolfmax4k.com {query}", "count": "30",
              "setlang": "es", "mkt": "es-ES"}
    proxy_base = (_ADDON.getSetting("proxy_url") or "").strip().rstrip("/")
    from urllib.parse import urlencode as _ue, quote as _q
    full = _BING_URL + "?" + _ue(params)
    r = None
    try:
        r = requests.get(full, headers=headers, timeout=15,
                         allow_redirects=True)
        if r.status_code != 200 or len(r.content) < 2000:
            _LOG(f"bing direct: HTTP {r.status_code} len={len(r.content)}, "
                 "trying proxy")
            r = None
    except Exception as e:
        _LOG(f"bing direct fail: {e.__class__.__name__}")
        r = None
    if r is None and proxy_base:
        try:
            proxied = proxy_base + "/?u=" + _q(full, safe="")
            r = requests.get(proxied, headers=headers, timeout=25)
            if r.status_code != 200 or len(r.content) < 2000:
                _LOG(f"bing proxied: HTTP {r.status_code} len={len(r.content)}")
                r = None
        except Exception as e:
            _LOG(f"bing proxied fail: {e.__class__.__name__}")
            r = None
    if r is None:
        return []

    soup = BeautifulSoup(r.content, "html.parser")
    items = []
    seen = set()
    id_pat = re.compile(
        r"/(?:movie|online|pelicula|capitulo|episodio|"
        r"serie-online(?:-[\w-]+)?)/\d+", re.I)
    import unicodedata as _u
    def _n(s):
        s = _u.normalize("NFKD", s or "")
        return "".join(c for c in s if not _u.combining(c)).lower()
    qn = _n(query)
    q_tokens = [t for t in re.split(r"[\s\-\._]+", qn) if len(t) >= 3]

    # Bing SERP: <li class="b_algo"><h2><a href="...">title</a></h2></li>
    for a in soup.select("li.b_algo h2 a, h2 a"):
        href = (a.get("href") or "").strip()
        title = a.get_text(" ", strip=True)
        if not href or "wolfmax4k" not in href.lower():
            continue
        if not id_pat.search(href):
            continue
        hm = _WF_HOST_RE.search(href)
        canon = _base() + hm.group(1).rstrip("/") if hm else href
        if canon in seen:
            continue
        tn = _n(title)
        if q_tokens and not all(t in tn or t in _n(canon) for t in q_tokens):
            continue
        seen.add(canon)
        title = re.sub(r"\s*\|\s*W(?:ww\.)?olfmax4k\.com\s*$", "",
                       title, flags=re.I).strip()
        kind = _classify(canon) or "movie"
        if kind == "movie" and _looks_like_tvshow(title, canon):
            kind = "tvshow"
        items.append({
            "title":   title or canon.rsplit("/", 1)[-1],
            "url":     canon,
            "kind":    kind,
            "image":   None,
            "quality": None,
            "source":  SOURCE,
        })
        if len(items) >= max_results:
            break
    _LOG(f"bing: {len(items)} items para {query!r}")
    return items


# Patron de "titulo que ya lleva marca de episodio/capitulo". Si un titulo
# lo matchea, su agrupador sabe extraer (season,episode) y NO hace falta
# tocar el H1 de la pagina.
_EPISODE_MARKER_RE = re.compile(
    r"\[?\s*Cap(?:itulo|\u00edtulo)?\.?\s*\[?\s*\d{1,4}"  # Cap.205 / Capitulo [10] / Capítulo 5
    r"|\b\d{1,2}\s*[xX]\s*\d{2,3}\b"                       # 1x05
    r"|\bS\d{1,2}E\d{1,3}\b"                               # S01E05
    r"|Temporada\s*\[?\s*\d{1,2}",                         # Temporada [8]
    re.I,
)


def _enrich_titles_inplace(items):
    """Para cada item cuya URL sea ID-based (/online/<id>, /movie/<id>, ...)
    y cuyo titulo NO tenga marca de episodio, descarga el H1 real en paralelo
    y actualiza `title` y `kind` in-place.

    Esto resuelve el caso donde Brave nos da el snippet con titulo generico
    ("Arcane") en vez del titulo real del capitulo ("Arcane [HDTV 1080p]
    [Cap.204]"), que luego impediria al agrupador ordenar por episodio.
    """
    if not items:
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed
    targets = []
    ID_RE = re.compile(
        r"/(?:online|capitulo|episodio|movie|pelicula|"
        r"serie-online(?:-[\w-]+)?)/\d+", re.I)
    for i, it in enumerate(items):
        url = it.get("url") or ""
        title = it.get("title") or ""
        if not ID_RE.search(url):
            continue
        if _EPISODE_MARKER_RE.search(title):
            continue  # ya tiene marca, no hace falta fetch
        targets.append((i, url))
    if not targets:
        return
    # Tope duro: nunca lanzar mas de 30 H1 fetches por busqueda. Va por
    # proxy y satura facil. Si hay mas, los items extra se quedan con su
    # titulo original (suficiente para mostrar y pulsar).
    MAX_ENRICH = 30
    if len(targets) > MAX_ENRICH:
        _LOG(f"_enrich_titles_inplace: {len(targets)} candidatos -> limito a {MAX_ENRICH}")
        targets = targets[:MAX_ENRICH]
    _LOG(f"_enrich_titles_inplace: {len(targets)}/{len(items)} items a enriquecer")
    sess = _session()

    def _fetch_h1(idx_url):
        idx, url = idx_url
        try:
            r = hs.get(sess, url, timeout=10)
            text = (r.content or b"").decode("utf-8", "ignore")
            if len(text) < 15000 or "404.webp" in text:
                return idx, None
            mh = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S | re.I)
            if not mh:
                return idx, None
            h1 = re.sub(r"<[^>]+>", " ", mh.group(1))
            h1 = re.sub(r"\s+", " ", h1).strip()
            return idx, (h1 or None)
        except Exception:
            return idx, None

    updated = 0
    with ThreadPoolExecutor(max_workers=min(10, len(targets))) as ex:
        futs = {ex.submit(_fetch_h1, tu): tu for tu in targets}
        for fut in as_completed(futs):
            idx, h1 = fut.result()
            if not h1:
                continue
            it = items[idx]
            it["title"] = h1
            # Re-clasificar segun el titulo (puede haber marca de cap.)
            if _looks_like_tvshow(h1, it.get("url", "")):
                it["kind"] = "tvshow"
            updated += 1
    _LOG(f"_enrich_titles_inplace: actualizados {updated}/{len(targets)}")


def _search_brave_wrapped(query):
    """Wrapper que devuelve (items, responded). responded=True significa
    que brave contesto (aunque haya filtrado a 0 items). responded=False
    significa que ni directo ni via proxy se pudo hablar con brave.

    Una sola llamada directa — sin variantes — porque brave rate-limita
    (429) cuando disparamos varias en paralelo. La cobertura real la da
    la proximity scan + el indice local."""
    state = {"responded": False}

    # Una sola query y una sola pagina. Brave rate-limita agresivamente
    # (HTTP 429) cuando lanzamos varias consultas en paralelo, y las
    # variantes como "arcane temporada" traen mas ruido que senal.
    # La cobertura real viene de proximity scan + wf_index, no de brave.
    all_items = _search_brave(query, max_pages=1) or []
    seen = {it.get("url") for it in all_items if it.get("url")}

    # Fallback DDG si Brave no devolvio nada util (suele ser por 429).
    # DDG tiene menos rate-limit y cubre el mismo catalogo wolfmax.
    if not all_items:
        try:
            ddg_items = _search_ddg(query) or []
            for it in ddg_items:
                u = it.get("url")
                if u and u not in seen:
                    seen.add(u)
                    all_items.append(it)
            if ddg_items:
                state["responded"] = True
                _LOG(f"_search_brave_wrapped: DDG fallback -> {len(ddg_items)} items")
        except Exception as e:
            _LOG(f"_search_brave_wrapped: DDG err: {e}")

    # Fallback Bing si DDG tambien fallo. Bing HTML es mas permisivo
    # con anti-bot (brave da 429, ddg da 202, bing suele dar 200).
    if not all_items:
        try:
            bing_items = _search_bing(query) or []
            for it in bing_items:
                u = it.get("url")
                if u and u not in seen:
                    seen.add(u)
                    all_items.append(it)
            if bing_items:
                state["responded"] = True
                _LOG(f"_search_brave_wrapped: Bing fallback -> {len(bing_items)} items")
        except Exception as e:
            _LOG(f"_search_brave_wrapped: Bing err: {e}")

    # IMPORTANTE: las variantes como "arcane temporada" pueden traer
    # resultados irrelevantes (ej. "En temporada baja" pasa el filtro
    # por tener "temporada"). Aplicamos un filtro FINAL exigiendo que
    # la query ORIGINAL este presente en titulo O url. "arcane" ->
    # solo titulos/URLs con "arcane" sobreviven.
    import unicodedata as _u
    def _n(s):
        s = _u.normalize("NFKD", s or "")
        return "".join(c for c in s if not _u.combining(c)).lower()
    orig_tokens = [t for t in re.split(r"[\s\-\._]+", _n(query)) if len(t) >= 3]
    if orig_tokens:
        filt = []
        for it in all_items:
            tn = _n(it.get("title") or "")
            un = _n(it.get("url") or "")
            # Exigimos que TODOS los tokens originales aparezcan en
            # titulo O url combinados (mas laxo que AND estricto pero
            # mucho mas estricto que OR).
            combined = tn + " " + un
            if all(t in combined for t in orig_tokens):
                filt.append(it)
        _LOG(f"_search_brave_wrapped: filtro final {len(filt)}/{len(all_items)} "
             f"(tokens orig={orig_tokens})")
        all_items = filt

    items = all_items
    # Heuristica: si items>0 -> claramente respondio. Si items==0, hacemos
    # un probe mini (mismo timeout corto) para ver si la pagina devuelve HTML
    # con markup de brave (div.snippet presente aunque vacio).
    if items:
        state["responded"] = True
        return items, True

    import requests
    try:
        r = requests.get(_BRAVE_SEARCH_URL,
                         params={"q": f"site:wolfmax4k.com {query}"},
                         headers={
                             "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; "
                                            "Win64; x64) AppleWebKit/537.36 "
                                            "Chrome/124.0 Safari/537.36"),
                             "Accept-Language": "es-ES,es;q=0.9",
                         }, timeout=10, allow_redirects=True)
        if r.status_code == 200 and "snippet" in (r.text or "").lower():
            state["responded"] = True
    except Exception:
        pass
    return items, state["responded"]


def _search_brave(query, max_pages=3):
    """Busca `site:wolfmax4k.com <query>` en Brave y devuelve items
    con la misma estructura que _items_from_soup.

    Recorre hasta max_pages de resultados (~20 por pagina).

    Brave Search NO esta bloqueado por ISPs espanoles, asi que hacemos la
    peticion DIRECTA (sin pasar por el worker de Cloudflare). Esto evita
    que el usuario tenga que redeployar el worker cada vez que sumamos un
    buscador nuevo. Si la peticion directa falla (red caida, IP blacklist),
    probamos por el worker como fallback.
    """
    import unicodedata
    import requests

    def _norm(s):
        s = unicodedata.normalize("NFKD", s or "")
        return "".join(c for c in s if not unicodedata.combining(c)).lower()

    q = f"site:wolfmax4k.com {query}"
    items = []
    seen_urls = set()

    browser_headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
        "Accept-Encoding": "identity",
    }
    proxy_base = (_ADDON.getSetting("proxy_url") or "").strip().rstrip("/")

    for offset in range(max_pages):
        params = {"q": q}
        if offset:
            params["offset"] = str(offset)
        # 1) Directo primero
        r = None
        try:
            r = requests.get(_BRAVE_SEARCH_URL, params=params,
                             headers=browser_headers, timeout=15,
                             allow_redirects=True)
            if r.status_code != 200 or len(r.content) < 3000:
                _LOG(f"brave direct offset={offset}: HTTP {r.status_code} "
                     f"len={len(r.content)}, trying proxy")
                r = None
        except Exception as e:
            _LOG(f"brave direct offset={offset} fail: {e.__class__.__name__}; "
                 f"trying proxy")
            r = None
        # 2) Fallback: via worker (requiere search.brave.com en ALLOWED_HOSTS)
        if r is None and proxy_base:
            try:
                proxied = proxy_base + "/?u=" + requests.utils.quote(
                    f"{_BRAVE_SEARCH_URL}?" +
                    "&".join(f"{k}={requests.utils.quote(v)}" for k, v in params.items()),
                    safe="")
                r = requests.get(proxied, headers=browser_headers, timeout=25)
                if r.status_code != 200:
                    _LOG(f"brave proxied offset={offset}: HTTP {r.status_code}")
                    r = None
            except Exception as e:
                _LOG(f"brave proxied offset={offset} fail: {e.__class__.__name__}")
                r = None
        if r is None:
            break

        soup = BeautifulSoup(r.content, "html.parser")
        # Cada resultado es un <div class="snippet"> que contiene un <a href>
        # y un <div class="title"> con el titulo limpio.
        page_count = 0
        for snip in soup.select("div.snippet"):
            a = snip.find("a", href=True)
            title_el = snip.select_one(".title")
            if not a or not title_el:
                continue
            url = (a.get("href") or "").strip()
            if "wolfmax4k.com" not in url:
                continue
            m = _WF_HOST_RE.search(url)
            if not m:
                continue
            # Canonicalizar al dominio configurado por el usuario
            canon = _base() + m.group(1).rstrip("/")
            if canon in seen_urls:
                continue
            # Solo URLs playables. Hay tres patrones validos:
            #   /movie/<id>, /online/<id>, /serie-online-*/<id>, /capitulo/<id>
            #   /descargar/<categoria>/<slug>/<temporada>/<capitulo>/
            #   /descargar/<categoria>/<slug>/<calidad>/   (peliculas)
            low = canon.lower()
            is_id = bool(re.search(
                r"/(movie|online|pelicula|capitulo|episodio|"
                r"serie-online(?:-[\w-]+)?)/\d+", low))
            is_descargar = low.rstrip("/").count("/") >= 4 and "/descargar/" in low
            if not (is_id or is_descargar):
                continue

            # title viene como "Alien Earth | Wolfmax4k.com" — limpiar sufijo
            title = (title_el.get("title") or
                     title_el.get_text(" ", strip=True) or "").strip()
            title = re.sub(r"\s*\|\s*W(?:ww\.)?olfmax4k\.com\s*$", "",
                           title, flags=re.I).strip()
            if not title or len(title) < 2:
                continue

            # Filtro local sanity-check: al menos uno de los tokens debe
            # aparecer en el titulo (brave a veces devuelve resultados
            # tangenciales al query).
            qn = _norm(query)
            toks = [t for t in re.split(r"[\s\-\._]+", qn) if len(t) >= 3]
            tn = _norm(title)
            if toks and not any(t in tn for t in toks):
                continue

            # Clasificacion: primero por URL, luego corrige por titulo/patron
            kind = _classify(canon) or "movie"
            if kind == "movie" and _looks_like_tvshow(title, canon):
                kind = "tvshow"
            quality = None
            mq = _QUALITY_RE.search(title)
            if mq:
                quality = mq.group(1)
            # Si no hay calidad en el titulo, inferir del patron de URL
            if not quality:
                low = canon.lower()
                if "/serie-online-4k" in low or "/4k-2160p" in low:
                    quality = "4K"
                elif "/serie-online-1080p" in low or "bluray-1080p" in low:
                    quality = "1080p"
                elif "/serie-online-720p" in low or "bluray-720p" in low:
                    quality = "720p"
                elif "/serie-online-hd" in low:
                    quality = "HD"

            seen_urls.add(canon)
            items.append({
                "title":   title,
                "url":     canon,
                "kind":    kind,
                "image":   None,
                "quality": quality,
                "source":  SOURCE,
            })
            page_count += 1
        _LOG(f"brave page offset={offset}: {page_count} new items")
        if page_count == 0:
            break  # sin mas resultados utiles

    return items


# === Navegacion A-Z desde indice local =====================================

def az_letters(kind_filter=None):
    """Devuelve [(letra, count), ...] para construir el menu A-Z."""
    counts = wf_index.available_letters(kind_filter=kind_filter)
    # Orden: # primero, luego A-Z
    letters = []
    if "#" in counts:
        letters.append(("#", counts["#"]))
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch in counts:
            letters.append((ch, counts[ch]))
    return letters


def browse_az(letter, kind_filter=None):
    """Lista items del indice que empiezan por la letra dada."""
    return wf_index.by_letter(letter, kind_filter=kind_filter)


def index_stats():
    return wf_index.stats()


def rebuild_index(progress_cb=None, max_workers=20, max_urls=None):
    """Reconstruye (incrementalmente) el indice desde los sitemaps."""
    return wf_index.rebuild_from_sitemaps(
        hs, progress_cb=progress_cb,
        max_workers=max_workers, max_urls=max_urls,
    )


def fetch_detail_title(url):
    try:
        sess = _session()
        r = hs.get(sess, url)
        soup = BeautifulSoup(r.content, "html.parser")
        h1 = soup.find("h1")
        return h1.get_text(" ", strip=True) if h1 else None
    except Exception:
        return None


_EPISODE_HREF_RE = re.compile(
    r"/(?:capitulo|episodio|cap|ep)/(\d+)"                # /capitulo/123
    r"|\d+x\d+"                                           # 1x01, s1e01 etc
    r"|/s\d+e\d+"                                         # /s01e01
    r"|/temporada-\d+/"                                   # /temporada-1/
    r"|/episode/\d+"                                      # /episode/3
    r"|/serie-online-[^/]+/\d+"                           # /serie-online-1080p/261389
    r"|/online/\d+",                                      # /online/236186 (serie-online short)
    re.I,
)


def detail(url):
    _LOG(f"detail: {url}")
    sess = _session()
    r = hs.get(sess, url)
    soup = BeautifulSoup(r.content, "html.parser")

    # Titulo
    title = None
    for sel in ("h1", ".titulo", ".entry-title"):
        t = soup.select_one(sel)
        if t:
            title = t.get_text(" ", strip=True)
            if title:
                break
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = (og.get("content") or "").strip() or None

    # Sinopsis
    plot = None
    for p in soup.find_all("p"):
        txt = p.get_text(" ", strip=True)
        if len(txt) > 80 and "cookie" not in txt.lower() and "©" not in txt:
            plot = txt
            break

    # Imagen: primero buscar img con la ruta de assets del sitio
    image = None
    for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        if src and ("assets/u/p" in src or "wolfmax4k" in src):
            if not src.startswith("data:"):
                image = _fix_src(src, url)
                break
    if not image:
        og_img = soup.find("meta", property="og:image")
        if og_img:
            image = (og_img.get("content") or "").strip() or None

    # Anno
    year = None
    m = re.search(r"\b(19|20)\d{2}\b", soup.get_text())
    if m:
        year = m.group(0)

    downloads = _find_downloads(soup, url)

    # Series pages on WolfMax don't carry the .torrent links themselves;
    # each capitulo lives at /capitulo/<id> with its own download. If the
    # series detail page yields zero downloads but lots of capitulo links,
    # fan out (in parallel) and collect downloads from each capitulo page,
    # tagging them with episode numbers extracted from the URL/title.
    if not downloads:
        cap_urls = []
        seen_caps = set()
        # Path de la serie actual, para heurisitica "hijo directo".
        # Ej: /series/4k-2160p/the-pitt -> cualquier /series/4k-2160p/the-pitt/...
        from urllib.parse import urlparse as _urlparse
        series_path = _urlparse(url).path.rstrip("/")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = href if href.startswith("http") else urljoin(url, href)
            full_path = _urlparse(full).path.rstrip("/")

            looks_like_ep = False
            if _EPISODE_HREF_RE.search(href):
                looks_like_ep = True
            elif series_path and full_path.startswith(series_path + "/"):
                # hijo directo o nieto del path de la serie
                looks_like_ep = True

            if not looks_like_ep:
                continue

            if full in seen_caps or full == url:
                continue
            seen_caps.add(full)
            label = a.get_text(" ", strip=True) or full
            cap_urls.append((full, label))
        _LOG(f"detail: encontrados {len(cap_urls)} candidatos a episodio")
        if cap_urls:
            _LOG(f"detail: 0 direct downloads, fanning out to {len(cap_urls)} capitulos")
            from concurrent.futures import ThreadPoolExecutor

            def _fetch_cap(item, idx):
                cap_url, cap_label = item
                try:
                    rr = hs.get(_session(), cap_url, timeout=30)
                    csoup = BeautifulSoup(rr.content, "html.parser")
                    dls = _find_downloads(csoup, cap_url)
                    s, e = _episode_from_text(cap_label) or _episode_from_text(
                        csoup.get_text(" ", strip=True)[:500]
                    ) or (None, None)
                    if s is None:
                        s, e = 1, idx + 1
                    out = []
                    for d in dls:
                        out.append({**d,
                                    "season":  s,
                                    "episode": e,
                                    "label":   f"{s:02d}x{e:02d}"})
                    return out
                except Exception as exc:
                    _LOG(f"detail capitulo {cap_url} failed: {exc}")
                    return []

            with ThreadPoolExecutor(max_workers=6) as ex:
                results = list(ex.map(lambda p: _fetch_cap(p[1], p[0]),
                                       list(enumerate(cap_urls))))
            for chunk in results:
                downloads.extend(chunk)

    _LOG(f"detail -> title={title!r} downloads={len(downloads)}")
    return {
        "title":     title,
        "plot":      plot,
        "image":     image,
        "year":      year,
        "downloads": downloads,
    }


def _episode_from_text(text):
    if not text:
        return None
    m = _EPISODE_RE.search(text)
    if m:
        if m.group(1):
            return int(m.group(1)), int(m.group(2))
        return int(m.group(3)), int(m.group(4))
    return None


def _find_downloads(soup, page_url):
    """
    WolfMax4K usa enlaces de descarga a traves de enlacito.com:
      <a href="https://enlacito.com/s.php?i=BASE64">DESCARGAR AHORA</a>

    Seguimos la redireccion para obtener la URL real del .torrent o magnet.
    """
    downloads, seen = [], set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue

        is_magnet  = href.lower().startswith("magnet:")
        is_torrent = href.endswith(".torrent")
        is_redirect = (
            "enlacito.com" in href
            or "acortador" in href
            or re.search(r"/descargar?/|/download/|/get/|/dl/", href, re.I)
        )

        if not any([is_magnet, is_torrent, is_redirect]):
            continue

        cache_key = href
        if cache_key in seen:
            continue
        seen.add(cache_key)

        if is_torrent:
            torrent_url = href if href.startswith("http") else urljoin(page_url, href)
        elif is_redirect:
            # NO resolvemos la cadena aqui. enlacito.com a veces devuelve
            # paginas con JS que mis regex mal-interpretan y acaba saliendo
            # https://google.com como "URL final". Dejamos el shortlink tal
            # cual y play() se encarga de seguirlo con la logica de fetch+
            # escaneo-de-bytes (tolera magnet embebido, .torrent embebido,
            # o .torrent directo con Content-Type binario).
            torrent_url = href if href.startswith("http") else urljoin(page_url, href)
        else:
            torrent_url = href

        label = a.get_text(" ", strip=True)
        if not label or len(label) > 60:
            label = "Descargar"

        season, episode = _episode_from_context(a)
        if season is not None:
            label = f"{season:02d}x{episode:02d}"

        downloads.append({
            "torrent_url": torrent_url,
            "label":       label,
            "season":      season,
            "episode":     episode,
        })

    return downloads


def _episode_from_context(a):
    tr = a.find_parent("tr")
    if tr:
        text = tr.get_text(" ", strip=True)
    else:
        text = (a.parent or a).get_text(" ", strip=True)
    m = _EPISODE_RE.search(text)
    if m:
        if m.group(1):
            return int(m.group(1)), int(m.group(2))
        return int(m.group(3)), int(m.group(4))
    return None, None


# =====================================================================
# Jerarquia Serie → Temporadas → Capitulos
# =====================================================================

def find_series_url(query, seed_url=None):
    """Descubre la URL de la pagina contenedora de una serie.

    Usa la breadcrumb JSON-LD del episodio semilla y/o prueba slugs
    candidatos contra /series/{calidad}/{slug}.

    Devuelve la URL absoluta del contenedor o None.
    """
    items = []
    if seed_url:
        items = [{"url": seed_url, "title": query}]
    url, _ = _discover_series_container(items, query)
    return url


def _parse_season_episode(text):
    """Extrae (temporada, capitulo) de textos estilo WolfMax.

    Soporta los formatos habituales:
        Cap.201     -> (2, 1)   Cap.NNN: primeros digitos=temporada, ultimos 2=capitulo
        Cap.1003    -> (10, 3)  Cap.NNNN: idem con temporada >9
        1x05        -> (1, 5)
        S01E05      -> (1, 5)
        Temporada 2 -> (2, None)   solo marca temporada

    Devuelve (season, episode) o (None, None).
    """
    if not text:
        return None, None
    # 1x05, 2x13, 10x102
    m = re.search(r"\b(\d{1,2})\s*[xX\xd7]\s*(\d{1,3})\b", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    # S01E05
    m = re.search(r"\bS(\d{1,2})E(\d{1,3})\b", text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Cap.NNN / Capitulo NNN (3+ digitos: primeros N-2 = temporada, ultimos 2 = capitulo)
    # (?:tulos?)? hace opcional "tulo/tulos" para matchear tanto "Cap.101" como "Capitulo 101"
    m = re.search(r"[Cc]ap(?:[ií]?tulos?)?\.?\s*\[?\s*(\d{3,5})\b", text)
    if m:
        raw = m.group(1)
        e_num = int(raw[-2:])
        s_num = int(raw[:-2]) if len(raw) > 2 else 1
        return s_num, e_num
    # Cap.NN / Capitulo NN (1-2 digitos: temporada 1)
    m = re.search(r"[Cc]ap(?:[ií]?tulos?)?\.?\s*\[?\s*(\d{1,2})\b", text)
    if m:
        return 1, int(m.group(1))
    return None, None


def get_all_episodes(container_url):
    """Scrapea la pagina contenedora de una serie y devuelve TODOS los
    episodios organizados por temporada.

    Devuelve dict:
        {
            'title':    str,           # titulo limpio de la serie
            'image':    str or None,   # poster/og:image
            'seasons':  {int: [ep_dict, ...]},  # clave=num temporada
            'total':    int,           # total episodios
        }
    Cada ep_dict: {title, url, kind, image, quality, season, episode, source}
    """
    sess = _session()
    try:
        r = hs.get(sess, container_url, timeout=30)
    except Exception as e:
        _LOG(f"get_all_episodes: fetch fail {container_url}: {e}")
        return {"title": "", "image": None, "seasons": {}, "total": 0}

    soup = BeautifulSoup(r.content, "html.parser")

    # Titulo de la serie
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = (og.get("content") or "").strip()
    if not title:
        title = container_url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
    # Limpiar marcas de calidad del titulo de la serie
    title = re.sub(r"\s*\|\s*[Ww]olfmax4k.*$", "", title).strip()

    # Poster
    image = None
    og_img = soup.find("meta", property="og:image")
    if og_img:
        image = (og_img.get("content") or "").strip() or None
    if not image:
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src and "assets/u/p" in src and not src.startswith("data:"):
                image = _fix_src(src, container_url)
                break

    # Recoger todos los enlaces a episodios
    episodes = []
    seen = set()
    idx = 0  # fallback index si no podemos parsear season/ep

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        full = href if href.startswith("http") else urljoin(container_url, href)
        if "wolfmax4k" not in full.lower():
            continue
        if full in seen or full.rstrip("/") == container_url.rstrip("/"):
            continue
        if not _PLAYABLE_URL_RE_GLOBAL.search(full):
            continue
        seen.add(full)

        # Titulo del episodio: primero img.alt (mas completo), luego texto
        label = ""
        img_tag = a.find("img")
        if img_tag:
            label = (img_tag.get("alt") or "").strip()
        if not label:
            for sel in ("h3", "h2", ".title"):
                t = a.select_one(sel)
                if t:
                    label = t.get_text(" ", strip=True)
                    if label:
                        break
        if not label:
            label = a.get_text(" ", strip=True)
        if not label or len(label) < 3:
            label = full.rstrip("/").rsplit("/", 1)[-1]
        label = re.sub(r"\s+", " ", label).strip()

        # Imagen del episodio
        ep_img = None
        if img_tag:
            raw = (img_tag.get("src") or img_tag.get("data-src")
                   or img_tag.get("data-original") or "").strip()
            if raw:
                ep_img = _fix_src(raw, container_url)

        # Season / Episode
        s_num, e_num = _parse_season_episode(label)
        if s_num is None:
            # intentar con la URL
            s_num, e_num = _parse_season_episode(full)
        if s_num is None:
            s_num = 1
        if e_num is None:
            idx += 1
            e_num = idx

        # Calidad
        quality = None
        mq = _QUALITY_RE.search(label)
        if mq:
            quality = mq.group(1)
        if not quality:
            low_url = full.lower()
            if "/serie-online-4k" in low_url or "/4k-2160p" in low_url:
                quality = "4K"
            elif "/serie-online-1080p" in low_url or "/1080p" in low_url:
                quality = "1080p"
            elif "/serie-online-720p" in low_url or "/720p" in low_url:
                quality = "720p"
            elif "/serie-online-hd" in low_url:
                quality = "HD"

        episodes.append({
            "title":   label,
            "url":     full,
            "kind":    "tvshow",
            "image":   ep_img,
            "quality": quality,
            "season":  s_num,
            "episode": e_num,
            "source":  SOURCE,
        })

    # Agrupar por temporada y ordenar
    seasons = {}
    for ep in episodes:
        seasons.setdefault(ep["season"], []).append(ep)
    for s in seasons:
        seasons[s].sort(key=lambda x: x["episode"])

    _LOG(f"get_all_episodes({container_url}): {len(episodes)} eps, "
         f"{len(seasons)} temporadas")
    return {
        "title":  title,
        "image":  image,
        "seasons": seasons,
        "total":  len(episodes),
    }


def search_and_expand(query):
    """Busqueda + expansion completa para una serie.

    Igual que search() pero si detecta que los resultados pertenecen a
    una serie, agrupa por temporada usando _parse_season_episode.

    NOTA: las paginas contenedoras (/series/1080p/<slug>) son dead-ends
    via proxy (contenido cargado via AJAX data.find.php, que devuelve
    "Denied" para IPs de CF Workers). Por eso NO intentamos container
    discovery — vamos directo a agrupar search results por temporada.

    Devuelve dict:
        {
            'type':        'series' | 'mixed',
            'title':       str,         # titulo de la serie (si type=series)
            'image':       str | None,
            'container':   None,
            'seasons':     {int: [items]},  # si type=series
            'items':       [items],     # si type=mixed (peliculas sueltas)
        }
    """
    items = search(query)
    if not items:
        return {"type": "mixed", "items": [], "title": query,
                "image": None, "container": None, "seasons": {}}

    # Verificar si los resultados parecen una serie (mayoria tvshow)
    tvshow_count = sum(1 for it in items if it.get("kind") in ("tvshow", "documentary"))
    is_series = tvshow_count > len(items) * 0.4

    if not is_series or len(items) <= 1:
        return {"type": "mixed", "items": items, "title": query,
                "image": None, "container": None, "seasons": {}}

    # Agrupar los items de busqueda por temporada
    seasons = {}
    for it in items:
        title = it.get("title", "")
        s, e = _parse_season_episode(title)
        _LOG(f"search_and_expand parse: {title!r} -> s={s}, e={e}")
        if s is None:
            s = 1
        if e is None:
            e = 99
        it["season"] = s
        it["episode"] = e
        seasons.setdefault(s, []).append(it)
    for s in seasons:
        seasons[s].sort(key=lambda x: x.get("episode", 99))

    # Deducir titulo limpio: buscar el nombre mas comun entre items
    best_title = query.title()
    if items:
        # Usar _strip_show_markers del primer item que tenga titulo largo
        for it in items:
            t = _strip_show_markers(it.get("title", ""))
            if t and len(t) >= len(query):
                best_title = t
                break

    _LOG(f"search_and_expand({query!r}): {len(items)} items -> "
         f"{len(seasons)} temporadas, {sum(len(v) for v in seasons.values())} eps")

    return {
        "type":      "series",
        "title":     best_title,
        "image":     items[0].get("image"),
        "container": None,
        "seasons":   seasons,
    }
