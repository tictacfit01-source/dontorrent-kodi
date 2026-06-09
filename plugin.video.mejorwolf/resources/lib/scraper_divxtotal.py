"""Scraper para DivxTotal (4a fuente, en castellano).

DivxTotal NO usa PoW ni el Cloudflare duro (Turnstile): pasa con requests plano
desde el relay. Por eso TODO va por el proxy /relay del relay (la IP de datacenter
no está bloqueada por el ISP, igual que con las otras fuentes).

Estructura del sitio (dominio rota: divxtotal.foo hoy):
  - Búsqueda:  GET /?s=<query>  -> tabla con <a href="/peliculas/<slug>/"> o
               <a href="/series/<slug>/">.
  - Ficha:     /peliculas/<slug>/  -> <h1> título, año/calidad en el texto, y
               botón de descarga <a href="download_tt.php?u=<base64(url .torrent)>">.
  - El .torrent es un fichero ESTÁTICO en el dominio (sin token). Lo baja el
    relay y lo convierte a magnet con torrent.torrent_to_magnet (en play()).
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

_QUALITY_RE = re.compile(
    r"\b(2160p|4K|1080p|720p|480p|BluRay|Blu-Ray|BDRemux|BDRip|BRRip|"
    r"WEB-?DL|WEBRip|HDRip|MicroHD|DVDRip|HDTV|HDR)\b", re.I)
_EPISODE_RE = re.compile(
    r"(\d{1,2})\s*[xX×]\s*(\d{1,3})|[Ss](\d{1,2})[Ee](\d{1,3})")
_SLUG_RE = re.compile(r"/(peliculas|series)/[a-z0-9][a-z0-9\-]+/?$", re.I)


def _relay_base():
    try:
        from . import scraper_dontorrent as dt
        return (dt._render_relay_url() or "").rstrip("/")
    except Exception:
        return ""


def _relay_get(url, timeout=30, binary=False):
    """GET vía el proxy /relay del relay (IP de datacenter, sin bloqueo ISP)."""
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


def _base():
    """Dominio activo de DivxTotal. Ajuste manual > caché > sondeo candidatos."""
    global _cached_domain
    setting = (_ADDON.getSetting("dx_base_url") or "").strip()
    if setting:
        return setting.replace("https://", "").replace("http://", "").rstrip("/")
    if _cached_domain:
        return _cached_domain
    for d in _DOMAINS:
        html = _relay_get(f"https://{d}/", timeout=15)
        if html and ("divxtotal" in html.lower()
                     or "/peliculas/" in html.lower()):
            _cached_domain = d
            _LOG(f"dominio activo: {d}")
            return d
    _cached_domain = _DOMAINS[0]
    return _cached_domain


def _kind_from_href(href):
    h = href.lower()
    if "/peliculas/" in h or "/pelicula/" in h:
        return "movie"
    if "/series/" in h or "/serie/" in h:
        return "tvshow"
    return None


def search(query):
    """Devuelve [{title, url, kind, image, quality, source}]."""
    dom = _base()
    html = _relay_get(f"https://{dom}/?s={quote(query)}")
    if not html:
        _LOG("search: sin HTML")
        return []
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
        seen.add(url)
        items.append({"title": title, "url": url, "kind": kind,
                      "image": None, "quality": "", "source": SOURCE})
    _LOG(f"search '{query}' -> {len(items)} items (dom {dom})")
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
        em = _EPISODE_RE.search(ctx)
        season = episode = None
        label = a.get_text(" ", strip=True) or (title or "Descargar")
        if em:
            if em.group(1):
                season, episode = int(em.group(1)), int(em.group(2))
            else:
                season, episode = int(em.group(3)), int(em.group(4))
            label = "%02dx%02d" % (season, episode)
        downloads.append({"torrent_url": turl, "label": label,
                          "season": season, "episode": episode,
                          "quality": quality})
    _LOG(f"detail '{title}' -> {len(downloads)} descargas")
    return {"title": title, "year": year, "quality": quality,
            "image": None, "downloads": downloads}
