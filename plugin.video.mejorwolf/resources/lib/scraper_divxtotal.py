"""Scraper para DivxTotal (4a fuente, en castellano).

DivxTotal NO usa PoW ni el Cloudflare duro (Turnstile): pasa con requests plano
desde el relay. Por eso TODO va por el proxy /relay del relay (la IP de datacenter
no esta bloqueada por el ISP, igual que con las otras fuentes).

Estructura del sitio (dominio rota: divxtotal.foo hoy):
  - Busqueda:  GET /?s=<query>  -> tabla con <a href="/peliculas/<slug>/"> o
               <a href="/series/<slug>/">.
  - Listados:  /peliculas/ , /series/  (paginacion: /peliculas/page/N/).
  - Ficha:     /peliculas/<slug>/  -> <h1> titulo, ano/calidad en el texto, y
               boton <a href="download_tt.php?u=<base64(url .torrent)>">.
  - El .torrent es un fichero ESTATICO en el dominio (sin token). Lo baja el
    relay y lo convierte a magnet con torrent.torrent_to_magnet (en play()).

Velocidad: la busqueda hace UNA sola llamada al relay (sin sondear la home
antes). Solo si esa llamada no trae resultados se prueban otros dominios.
"""
import re
import base64
from urllib.parse import quote, urljoin, urlparse, parse_qs

import xbmc
import xbmcaddon
from bs4 import BeautifulSoup

SOURCE = "dx"
_ADDON = xbmcaddon.Addon()


def _LOG(msg):
    xbmc.log(f"[MejorWolf/DX] {msg}", xbmc.LOGINFO)


# Dominios candidatos (rotan). Se puede forzar en Ajustes (dx_base_url).
_DOMAINS = ["divxtotal.foo", "divxtotal.gg", "divxtotal.cam", "divxtotal.fyi",
            "divxtotal.run", "divxtotal.one", "divxtotal.es"]
_cached_domain = None

# Seccion del sitio por 'kind' (estrenos/cine/series).
_SECTION = {"movie": "peliculas", "movie_hd": "peliculas",
            "movie_4k": "peliculas", "estrenos": "peliculas",
            "tvshow": "series", "tvshow_hd": "series"}

_QUALITY_RE = re.compile(
    r"\b(2160p|4K|1080p|720p|480p|BluRay|Blu-Ray|BDRemux|BDRip|BRRip|"
    r"WEB-?DL|WEBRip|HDRip|MicroHD|DVDRip|HDTV|HDR)\b", re.I)
# El marcador de episodio va al FINAL del contexto ("The Pitt2x12"), por eso lo
# anclamos a $ para no confundirnos con numeros del titulo.
_EP_END = re.compile(r"(\d{1,2})\s*[xX×]\s*(\d{1,3})\s*$")
_EP_TAIL = re.compile(r"\s*\d{1,2}\s*[xX×]\s*\d{1,3}\s*$")
_SLUG_RE = re.compile(r"/(peliculas|series)/[a-z0-9][a-z0-9\-]+/?$", re.I)


def _relay_base():
    try:
        from . import scraper_dontorrent as dt
        return (dt._render_relay_url() or "").rstrip("/")
    except Exception:
        return ""


def _relay_get(url, timeout=25, binary=False):
    """GET via el proxy /relay del relay (IP de datacenter, sin bloqueo ISP)."""
    base = _relay_base()
    if not base:
        return None
    try:
        import requests
        r = requests.get(f"{base}/relay", params={"u": url}, timeout=timeout)
        if r.status_code == 200 and len(r.content) > 200:
            return r.content if binary else r.text
    except Exception as e:
        _LOG(f"relay_get error: {e}")
    return None


def _domain():
    """Dominio a usar SIN sondear (rapido): ajuste > cache > primero."""
    setting = (_ADDON.getSetting("dx_base_url") or "").strip()
    if setting:
        return setting.replace("https://", "").replace("http://", "").rstrip("/")
    return _cached_domain or _DOMAINS[0]


def _kind_from_href(href):
    h = href.lower()
    if "/peliculas/" in h or "/pelicula/" in h:
        return "movie"
    if "/series/" in h or "/serie/" in h:
        return "tvshow"
    return None


def _parse_listing(html, dom):
    """Saca los items (peliculas/series) de una pagina de listado o busqueda."""
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not _SLUG_RE.search(href):
            continue
        kind = _kind_from_href(href)
        if not kind:
            continue
        url = urljoin(f"https://{dom}/", href)
        if url in seen:
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        if not title or len(title) < 2:
            continue
        # En series, el listado muestra "Show 2x12": quitamos el marcador para
        # que quede el nombre limpio del show (la ficha agrupa los episodios).
        if kind == "tvshow":
            title = _EP_TAIL.sub("", title).strip() or title
        seen.add(url)
        items.append({"title": title, "url": url, "kind": kind,
                      "image": None, "quality": "", "source": SOURCE})
    return items


def _fetch_listing(make_url):
    """Prueba el dominio por defecto (1 llamada); si NO trae items, prueba los
    demas candidatos. `make_url(dom)` construye la URL. Cachea el que funcione."""
    global _cached_domain
    primary = _domain()
    candidates = [primary] + [d for d in _DOMAINS if d != primary]
    tried = []
    for dom in candidates[:4]:
        if dom in tried:
            continue
        tried.append(dom)
        html = _relay_get(make_url(dom))
        if not html:
            continue
        items = _parse_listing(html, dom)
        if items:
            _cached_domain = dom
            return items
    return []


def search(query):
    """Devuelve [{title, url, kind, image, quality, source}]."""
    items = _fetch_listing(lambda d: f"https://{d}/?s={quote(query)}")
    _LOG(f"search '{query}' -> {len(items)} items")
    return items


def latest(kind="movie", page=1):
    """Listado de estrenos/cine/series (para navegar)."""
    section = _SECTION.get(kind, "peliculas")
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1

    def mk(d):
        if page <= 1:
            return f"https://{d}/{section}/"
        return f"https://{d}/{section}/page/{page}/"

    items = _fetch_listing(mk)
    _LOG(f"latest {kind} p{page} -> {len(items)} items")
    return items


def _decode_tt(href):
    """download_tt.php?u=<base64> -> URL real del .torrent."""
    try:
        u = (parse_qs(urlparse(href).query).get("u") or [""])[0]
        if not u:
            return None
        u += "=" * (-len(u) % 4)   # padding base64
        dec = base64.b64decode(u).decode("utf-8", "replace")
        return dec if dec.startswith("http") else None
    except Exception:
        return None


def detail(url):
    """Ficha: {title, year, quality, image, downloads:[{torrent_url, label,
    season, episode, quality}]}."""
    html = _relay_get(url)
    if not html:
        return {"title": None, "downloads": []}
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else None
    body = soup.get_text(" ", strip=True)
    ym = re.search(r"\b(19|20)\d{2}\b", body)
    year = ym.group(0) if ym else None
    qm = _QUALITY_RE.search(body)
    quality = qm.group(1) if qm else ""

    downloads, seen = [], set()
    for a in soup.find_all("a", href=True):
        if "download_tt.php" not in a["href"]:
            continue
        turl = _decode_tt(urljoin(url, a["href"]))
        if not turl or turl in seen:
            continue
        seen.add(turl)
        ctx = (a.find_parent("tr") or a.parent or a).get_text(" ", strip=True)
        em = _EP_END.search(ctx.strip())
        season = episode = None
        label = a.get_text(" ", strip=True) or (title or "Descargar")
        if em:
            season, episode = int(em.group(1)), int(em.group(2))
            label = "%dx%02d" % (season, episode)
        downloads.append({"torrent_url": turl, "label": label,
                          "season": season, "episode": episode,
                          "quality": quality})
    _LOG(f"detail '{title}' -> {len(downloads)} descargas")
    return {"title": title, "year": year, "quality": quality,
            "image": None, "downloads": downloads}
