"""
Scraper para EliteTorrent (www.elitetorrent.com).

Estructura del sitio:
  Estrenos:      /estrenos-/
  Peliculas:     /peliculas-1/
  Series:        /serie/
  Generos:       /genero/accion/, /genero/comedia/, etc.
  Calidad:       /calidad/1080p-10-1/, /calidad/720p/, etc.
  Castellano:    /idioma/castellano-17-1/
  Busqueda:      ?s={termino}
  Detalle:       /peliculas/{slug}/ o /series/{slug}/

Magnets: enlace a acortame-esto.com/s.php?i=BASE64
  -> 5 capas de base64 + ROT13 = magnet link
"""

import re
import base64
from urllib.parse import urljoin, quote as urlquote
from bs4 import BeautifulSoup
import xbmc
import xbmcaddon
from . import http_session as hs

SOURCE = "et"
_ADDON = xbmcaddon.Addon()
_DEFAULT_BASE = "https://www.elitetorrent.com"
_LOG = lambda msg: xbmc.log(f"[MejorWolf/ET] {msg}", xbmc.LOGINFO)


def _base():
    url = (_ADDON.getSetting("et_base_url") or "").strip().rstrip("/")
    return url or _DEFAULT_BASE


def _session():
    return hs.make_session(_base())


# ── Rutas del sitio ──────────────────────────────────────────────────────

SECTION_PATH = {
    "estrenos":    "estrenos-/",
    "movie":       "peliculas-1/",
    "tvshow":      "serie/",
    "movie_hd":    "calidad/1080p-10-1/",
    "movie_720p":  "calidad/720p/",
    "movie_hdrip": "calidad/hdrip-1/",
    "movie_micro": "peliculas-microhd/",
    "castellano":  "idioma/castellano-17-1/",
}

GENRES = {
    "accion":         "genero/accion/",
    "animacion":      "genero/animacion/",
    "aventura":       "genero/aventura/",
    "ciencia_ficcion":"genero/ciencia-ficcion/",
    "comedia":        "genero/comedias/",
    "crimen":         "genero/crimen/",
    "documental":     "genero/documental/",
    "drama":          "genero/dramatico/",
    "familia":        "genero/familia/",
    "fantasia":       "genero/fantasia-1/",
    "guerra":         "genero/guerra/",
    "historia":       "genero/historia/",
    "misterio":       "genero/misterio/",
    "musica":         "genero/musica/",
    "romance":        "genero/romance-2-1/",
    "suspense":       "genero/suspense-5-1/",
    "terror":         "genero/terror-10-1/",
    "thriller":       "genero/thriller/",
    "western":        "genero/western-9-1/",
}

GENRE_LABELS = {
    "accion":         "Accion",
    "animacion":      "Animacion",
    "aventura":       "Aventura",
    "ciencia_ficcion":"Ciencia Ficcion",
    "comedia":        "Comedia",
    "crimen":         "Crimen",
    "documental":     "Documental",
    "drama":          "Drama",
    "familia":        "Familia",
    "fantasia":       "Fantasia",
    "guerra":         "Guerra",
    "historia":       "Historia",
    "misterio":       "Misterio",
    "musica":         "Musica",
    "romance":        "Romance",
    "suspense":       "Suspense",
    "terror":         "Terror",
    "thriller":       "Thriller",
    "western":        "Western",
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _get(path):
    """GET a una ruta del sitio. Devuelve (soup, url_final)."""
    base = _base()
    url = f"{base}/{path}" if not path.startswith("http") else path
    s = _session()
    r = hs.get(s, url)
    return BeautifulSoup(r.text, "html.parser"), r.url


def _rot13(text):
    """Decodifica ROT13."""
    result = []
    for c in text:
        if 'a' <= c <= 'z':
            result.append(chr((ord(c) - ord('a') + 13) % 26 + ord('a')))
        elif 'A' <= c <= 'Z':
            result.append(chr((ord(c) - ord('A') + 13) % 26 + ord('A')))
        else:
            result.append(c)
    return "".join(result)


def _decode_link(encoded_b64):
    """Decodifica un enlace de EliteTorrent (magnet o .torrent URL).

    El enlace viene como base64 anidado (5 capas) + ROT13.
    Devuelve el enlace decodificado (magnet:... o https://...) o None.
    """
    data = encoded_b64.strip()
    for _ in range(20):  # Hasta 20 capas de base64
        try:
            # Corregir padding — las capas intermedias pueden perderlo
            missing = len(data) % 4
            if missing:
                data_padded = data + "=" * (4 - missing)
            else:
                data_padded = data
            decoded = base64.b64decode(data_padded).decode("utf-8", errors="replace")
            decoded = decoded.strip()
            # Encontrado: magnet o URL (en claro o ROT13)
            if decoded.startswith(("magnet:", "zntarg:", "http", "uggc")):
                data = decoded
                break
            data = decoded
        except Exception:
            break

    # ROT13: zntarg → magnet, uggcf → https, uggc → http
    if data.startswith("zntarg:") or data.startswith("uggc"):
        data = _rot13(data)

    if data.startswith("magnet:") or data.startswith("http"):
        return data
    return None


def _extract_quality(text):
    """Extrae la calidad de un texto (720p, 1080p, 4K, etc.)."""
    m = re.search(
        r"\b(4K|2160p|1080p|720p|HDRip|BluRay|BDRemux|WEB-?DL|"
        r"microHD|MicroHD|HDTV|DVDRip|Remux)\b",
        text or "", re.I,
    )
    return m.group(1) if m else ""


# ── Listados ─────────────────────────────────────────────────────────────

def _parse_listing(soup, page_url):
    """Extrae items de una pagina de listado de EliteTorrent.

    Devuelve lista de dicts con: title, url, thumb, quality, size, kind.
    """
    items = []
    for li in soup.select("ul.miniboxs li"):
        link = li.select_one("a.nombre") or li.select_one("div.meta a")
        if not link:
            # Intentar desde la imagen
            link = li.select_one("div.imagen > a")
        if not link:
            continue

        title = (link.get("title") or link.get_text()).strip()
        href = link.get("href", "")
        if not href:
            continue
        url = urljoin(page_url, href)

        # Imagen
        img = li.select_one("img[data-src]")
        thumb = img["data-src"] if img else ""
        if not thumb:
            img = li.select_one("img[src]")
            thumb = img["src"] if img else ""

        # Calidad (badge)
        quality_el = li.select_one("span.marca i")
        quality = quality_el.get_text().strip() if quality_el else ""
        if not quality:
            quality = _extract_quality(title)

        # Tamaño
        size_el = li.select_one("span.dig1") or li.select_one("div.voto1 span")
        size = size_el.get_text().strip() if size_el else ""

        # Tipo (pelicula o serie)
        kind = "movie"
        if "/serie/" in url or "/series/" in url:
            kind = "tvshow"

        items.append({
            "title": title,
            "url": url,
            "thumb": thumb,
            "quality": quality,
            "size": size,
            "kind": kind,
            "source": SOURCE,
        })

    return items


def _get_next_page(soup, page_url):
    """Busca el enlace a la pagina siguiente."""
    # EliteTorrent usa paginacion con /page/N/ o ?paged=N
    next_link = soup.select_one("a.nextpostslink") or soup.select_one("a.next")
    if next_link and next_link.get("href"):
        return urljoin(page_url, next_link["href"])
    return None


def latest(kind="estrenos", page=1):
    """Lista items de una seccion.

    kind: estrenos, movie, tvshow, movie_hd, movie_720p, castellano
    """
    path = SECTION_PATH.get(kind, "estrenos-/")

    # Paginacion: EliteTorrent usa /page/N/ dentro de la seccion
    if page > 1:
        path = path.rstrip("/") + f"/page/{page}/"

    _LOG(f"latest kind={kind} page={page} -> /{path}")
    try:
        soup, url = _get(path)
        items = _parse_listing(soup, url)

        # Info de paginacion
        next_url = _get_next_page(soup, url)

        _LOG(f"latest -> {len(items)} items, next={bool(next_url)}")
        return items, next_url
    except Exception as e:
        _LOG(f"latest error: {e}")
        return [], None


def genre(genre_key, page=1):
    """Lista items de un genero."""
    path = GENRES.get(genre_key, f"genero/{genre_key}/")
    if page > 1:
        path = path.rstrip("/") + f"/page/{page}/"

    _LOG(f"genre {genre_key} page={page} -> /{path}")
    try:
        soup, url = _get(path)
        items = _parse_listing(soup, url)
        next_url = _get_next_page(soup, url)
        _LOG(f"genre -> {len(items)} items")
        return items, next_url
    except Exception as e:
        _LOG(f"genre error: {e}")
        return [], None


def search(query):
    """Busca en EliteTorrent."""
    base = _base()
    url = f"{base}/?s={urlquote(query)}"
    _LOG(f"search: {query}")
    try:
        s = _session()
        r = hs.get(s, url)
        soup = BeautifulSoup(r.text, "html.parser")
        items = _parse_listing(soup, r.url)
        _LOG(f"search -> {len(items)} results")
        return items
    except Exception as e:
        _LOG(f"search error: {e}")
        return []


# ── Detalle + Magnets ────────────────────────────────────────────────────

def detail(url):
    """Obtiene los magnet links de una pagina de detalle.

    Devuelve lista de dicts con: magnet, label, quality.
    """
    _LOG(f"detail: {url}")
    try:
        s = _session()
        r = hs.get(s, url)
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        torrent_url = None   # URL directa al .torrent como fallback

        # Buscar enlaces de descarga (clase enlace_torrent)
        for a in soup.select("a.enlace_torrent"):
            href = a.get("href", "")
            label = a.get_text().strip()

            # Los enlaces van a acortame-esto.com/s.php?i=BASE64
            m = re.search(r"[?&]i=([A-Za-z0-9+/=]+)", href)
            if not m:
                continue

            encoded = m.group(1)
            link = _decode_link(encoded)
            if not link:
                continue

            quality = _extract_quality(label)
            if not quality:
                quality_el = soup.select_one("span.marca i")
                quality = quality_el.get_text().strip() if quality_el else ""

            if link.startswith("magnet:"):
                results.append({
                    "magnet": link,
                    "label": label,
                    "quality": quality,
                    "is_magnet": True,
                })
            elif link.startswith("http"):
                # URL directa al .torrent — guardar como fallback
                torrent_url = link
                results.append({
                    "magnet": link,
                    "label": label,
                    "quality": quality,
                    "is_magnet": False,
                })

        # Si no encontramos enlaces con clase, buscar por data-src (VIP)
        if not results:
            for a in soup.select("a.linktorrent[data-src]"):
                encoded = a.get("data-src", "")
                if encoded:
                    try:
                        decoded = base64.b64decode(encoded).decode("utf-8")
                        if decoded.startswith("magnet:"):
                            results.append({
                                "magnet": decoded,
                                "label": a.get_text().strip() or "Magnet",
                                "quality": _extract_quality(a.get_text()),
                                "is_magnet": True,
                            })
                    except Exception:
                        pass

        # Extraer info adicional de la ficha
        info = {}
        sinopsis_el = soup.select_one("div.descripcion_ficha")
        if sinopsis_el:
            info["plot"] = sinopsis_el.get_text().strip()

        title_el = soup.select_one("h1.titulo_ficha") or soup.select_one("h1")
        if title_el:
            info["title"] = title_el.get_text().strip()

        poster_el = soup.select_one("div.ficha_imagen img[data-src]")
        if poster_el:
            info["thumb"] = poster_el["data-src"]

        _LOG(f"detail -> {len(results)} magnets")
        return results, info

    except Exception as e:
        _LOG(f"detail error: {e}")
        return [], {}
