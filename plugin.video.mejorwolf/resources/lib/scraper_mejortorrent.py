"""
Scraper para MejorTorrent (www42.mejortorrent.eu).

URLs reales del sitio (verificadas):
  Listado peliculas: /peliculas, /peliculas-hd, /peliculas-4k
  Listado series:    /series, /series-hd
  Documentales:      /documentales
  Ultimos:           /torrents
  Detalle pelicula:  /pelicula/{id}/{slug}
  Detalle serie:     /serie/{id}/{id}/{slug}
  Busqueda:          /busqueda?q={termino}
"""

import re
from collections import defaultdict
from urllib.parse import urljoin, quote as urlquote
from bs4 import BeautifulSoup, NavigableString
import xbmc
import xbmcaddon
from . import http_session as hs

SOURCE = "mt"
_ADDON = xbmcaddon.Addon()
_DEFAULT_BASE = "https://www43.mejortorrent.eu"
_LOG = lambda msg: xbmc.log(f"[MejorWolf/MT] {msg}", xbmc.LOGINFO)


def _base():
    url = (_ADDON.getSetting("mt_base_url") or "").strip().rstrip("/")
    return url or _DEFAULT_BASE


def _session():
    return hs.make_session(_base())


# Rutas reales del sitio
SECTION_PATH = {
    "movie":       "peliculas",
    "movie_hd":    "peliculas-hd",
    "movie_4k":    "peliculas-4k",
    "tvshow":      "series",
    "tvshow_hd":   "series-hd",
    "documentary": "documentales",
    "estrenos":    "torrents",
}

# Patrones para clasificar URLs de items individuales
_KIND_PATS = [
    (re.compile(r"/pelicula(?:s)?/\d+",  re.I), "movie"),
    (re.compile(r"/pelicula(?:s)?-(?:hd|4k|720p|1080p|2160p)/\d+", re.I), "movie"),
    (re.compile(r"/serie(?:s)?/\d+",     re.I), "tvshow"),
    (re.compile(r"/serie(?:s)?-(?:hd|4k|720p|1080p)/\d+", re.I), "tvshow"),
    (re.compile(r"/documental(?:es)?/\d+", re.I), "documentary"),
]

_QUALITY_RE = re.compile(
    r"\b(4K|2160p|1080p|720p|BluRay|Blu-Ray|BDRemux|WEB-?DL|WEBRip"
    r"|HDRip|HDR|Remux|HEVC|x265|x264|MicroHD|HDTV|DVDRip|HDTV-1080p"
    r"|HDTV-720p|HDTV-480p|Ninguno)\b",
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


def _classify(href):
    for pat, kind in _KIND_PATS:
        if pat.search(href):
            return kind
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
    El sitio muestra cada item con DOS anclas al mismo URL:
      1. <a href="/pelicula/ID/slug"><img src="..." alt=""></a>  (imagen)
      2. <a href="/pelicula/ID/slug">Titulo</a> (texto) seguido de **(Calidad)**

    Agrupamos ambas por URL para construir el item completo.
    """
    entries = defaultdict(lambda: {"title": "", "image": None,
                                   "kind": None, "quality": None})

    all_anchors = soup.find_all("a", href=True)
    for a in all_anchors:
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        kind = _classify(href)
        if not kind:
            continue
        if kind_filter and kind != kind_filter:
            continue
        url = urljoin(page_url, href)
        data = entries[url]
        if data["kind"] is None:
            data["kind"] = kind

        img = a.find("img")
        if img:
            # Enlace imagen (lazy-load aware)
            raw = (img.get("src") or img.get("data-src") or
                   img.get("data-original") or img.get("data-lazy-src") or "").strip()
            src = _fix_src(raw, page_url)
            if src and not data["image"]:
                data["image"] = src
            # alt puede tener el titulo
            alt = (img.get("alt") or "").strip()
            if alt and not data["title"]:
                data["title"] = alt
        else:
            # Enlace texto
            text = a.get_text(" ", strip=True)
            if text and not data["title"]:
                data["title"] = text
            # El hermano siguiente suele ser la calidad: **(DVDRip)**
            if not data["quality"]:
                nxt = a.next_sibling
                if nxt:
                    nxt_str = str(nxt) if isinstance(nxt, NavigableString) else nxt.get_text()
                    m = _QUALITY_RE.search(nxt_str)
                    if m:
                        data["quality"] = m.group(1)

    items = []
    for url, data in entries.items():
        if not data["title"]:
            # Fallback: construir titulo desde el slug de la URL
            slug = url.rstrip("/").split("/")[-1]
            data["title"] = slug.replace("-", " ").title()
        if not data["title"]:
            continue
        quality = data["quality"]
        if not quality:
            m = _QUALITY_RE.search(data["title"])
            if m:
                quality = m.group(1)

        items.append({
            "title":   re.sub(r"\s+", " ", data["title"]).strip(),
            "url":     url,
            "kind":    data["kind"],
            "image":   data["image"],
            "quality": quality,
            "source":  SOURCE,
        })
    return items


def latest(kind="movie", page=1):
    section = SECTION_PATH.get(kind, "peliculas")
    _LOG(f"latest kind={kind} page={page} -> /{section}")
    try:
        if page <= 1:
            soup, url = _get(section)
        else:
            # MejorTorrent usa ?page= para paginar en algunas secciones
            soup, url = _get(section, params={"page": page})
        items = _items_from_soup(soup, url)
        _LOG(f"latest -> {len(items)} items")
        return items
    except hs.CloudflareChallengeError:
        _LOG("latest: Cloudflare challenge — MejorTorrent bloqueado")
        raise
    except Exception as e:
        _LOG(f"latest error: {e}")
        raise


def search(query):
    _LOG(f"search: {query!r}")
    errors = []

    # Try several known search shapes; first non-empty result wins.
    attempts = [
        ("busqueda", {"q": query}),
        (f"busqueda/{urlquote(query)}", None),
        ("buscar", {"q": query}),
        (f"buscar/{urlquote(query)}", None),
        ("", {"q": query}),
        ("", {"s": query}),
    ]
    for path, params in attempts:
        try:
            soup, url = _get(path, params=params)
            items = _items_from_soup(soup, url)
            if items:
                _LOG(f"search {path!r} params={params} -> {len(items)} items")
                return items
        except Exception as e:
            errors.append(f"{path!r} {params}: {e.__class__.__name__}")

    _LOG("search fallida: " + " | ".join(errors))
    return []


def fetch_detail_title(url):
    try:
        sess = _session()
        r = hs.get(sess, url)
        soup = BeautifulSoup(r.content, "html.parser")
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)
        return None
    except Exception:
        return None


def detail(url):
    _LOG(f"detail: {url}")
    sess = _session()
    r = hs.get(sess, url)
    soup = BeautifulSoup(r.content, "html.parser")

    # Titulo
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    if not title:
        og = soup.find("meta", property="og:title")
        if og:
            title = (og.get("content") or "").strip() or None

    # Sinopsis / descripcion
    plot = None
    for sel in (".descripcion p", ".descripcion", ".sinopsis p", ".sinopsis",
                "p.descripcion"):
        tag = soup.select_one(sel)
        if tag:
            txt = tag.get_text(" ", strip=True)
            if len(txt) > 40:
                plot = txt
                break
    if not plot:
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if len(txt) > 80 and "cookie" not in txt.lower():
                plot = txt
                break

    # Imagen
    image = None
    for img in soup.find_all("img"):
        src = img.get("src", "") or img.get("data-src", "")
        if src and "ultracdn" in src or "imagenes" in src:
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
    _LOG(f"detail -> title={title!r} downloads={len(downloads)}")
    return {
        "title":     title,
        "plot":      plot,
        "image":     image,
        "year":      year,
        "downloads": downloads,
    }


def _find_downloads(soup, page_url):
    downloads, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        # Real download links: .torrent files or explicit /descargar/ endpoints.
        # We deliberately exclude the global "/torrents" listing menu link.
        is_torrent = (
            href.lower().endswith(".torrent")
            or re.search(r"/(descarga|descargar|download)(?:/|\?|$)", href, re.I)
            or re.search(r"/torrents/[a-z0-9]", href, re.I)
        )
        if not is_torrent:
            continue
        torrent_url = href if href.startswith("http") else urljoin(page_url, href)
        if torrent_url in seen:
            continue
        seen.add(torrent_url)
        label = a.get_text(" ", strip=True) or "Descargar"
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
