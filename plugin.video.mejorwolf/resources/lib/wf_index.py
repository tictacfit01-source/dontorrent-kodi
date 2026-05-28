"""
Indice local persistente del catalogo WolfMax4K.

Motivacion
----------
El endpoint de busqueda real del sitio (/mvc/controllers/data.find.php) esta
bloqueado para IPs de Cloudflare Workers, que es justo la unica via que nos
permite esquivar el bloqueo ISP. Sin busqueda ajax, la unica forma de tener
"buscador" y navegacion alfabetica completa es:

  1) Descargar los sitemaps publicos (/sitemaps/sitemap.post_*.xml) que SI
     responden HTML planito y contienen las ~100k URLs playables del sitio
     con ID numerico (movie/online/serie-online-*).
  2) Scrapear el <h1> de cada URL para obtener el titulo.
  3) Guardar (url, title, kind, quality) en un JSON local persistente.
  4) Buscar y ordenar A-Z localmente, sin tocar mas el sitio.

El indice se auto-alimenta al navegar (cada listado visitado agrega sus
items), y el usuario puede forzar un rebuild completo desde el menu.

Formato de disco
----------------
JSON en special://profile/addon_data/plugin.video.mejorwolf/wf_index.json

    {
      "version": 1,
      "updated": "2026-04-21T22:30:00",
      "entries": {
        "<url>": {"title": "...", "kind": "tvshow", "quality": "1080p",
                   "image": "..."},
        ...
      }
    }

Se usa un dict-por-url para dedup O(1) en merge.
"""

import json
import os
import re
import time
import threading
import unicodedata
from urllib.parse import urljoin

import xbmc
import xbmcaddon
import xbmcvfs


_ADDON = xbmcaddon.Addon()
_LOG = lambda msg: xbmc.log(f"[MejorWolf/WFIndex] {msg}", xbmc.LOGINFO)


def _index_path():
    profile = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
    if not os.path.isdir(profile):
        try:
            os.makedirs(profile, exist_ok=True)
        except Exception:
            pass
    return os.path.join(profile, "wf_index.json")


_lock = threading.Lock()
_cache = None             # dict in-memory
_dirty = False
_last_save = 0.0


def _load():
    """Carga el indice en memoria. Idempotente y tolerante a fallos."""
    global _cache
    if _cache is not None:
        return _cache
    path = _index_path()
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries") or {}
            _LOG(f"loaded {len(entries)} entries from {path}")
            _cache = entries
            return _cache
    except Exception as e:
        _LOG(f"load failed: {e}; starting empty")
    _cache = {}
    return _cache


def _save_now():
    global _dirty, _last_save
    if _cache is None:
        return
    path = _index_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "version": 1,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "entries": _cache,
            }, f, ensure_ascii=False)
        os.replace(tmp, path)
        _dirty = False
        _last_save = time.time()
        _LOG(f"saved {len(_cache)} entries")
    except Exception as e:
        _LOG(f"save failed: {e}")


def save(force=False):
    """Guarda en disco. Por defecto throttled a 1 vez cada 10s."""
    with _lock:
        if not _dirty and not force:
            return
        if not force and (time.time() - _last_save) < 10.0:
            return
        _save_now()


def add(items):
    """Agrega/actualiza entradas. items = iterable de dict con url/title/kind/...

    Solo guarda URLs "playables" (con ID numerico), para no contaminar el
    indice con /series/<slug> dead-ends.
    """
    global _dirty
    if not items:
        return 0
    with _lock:
        cache = _load()
        n = 0
        for it in items:
            url = (it.get("url") or "").strip()
            title = (it.get("title") or "").strip()
            if not url or not title:
                continue
            if not _PLAYABLE_RE.search(url):
                continue
            prev = cache.get(url) or {}
            # Preferimos titulos largos (mas info) sobre cortos ("Ver online")
            if prev.get("title") and len(prev["title"]) > len(title) + 5:
                title = prev["title"]
            entry = {
                "title":   title,
                "kind":    it.get("kind") or prev.get("kind") or "movie",
                "quality": it.get("quality") or prev.get("quality"),
                "image":   it.get("image")   or prev.get("image"),
                "source":  "wf",
            }
            if entry != prev:
                cache[url] = entry
                _dirty = True
                n += 1
        if n:
            _LOG(f"add: {n} entries changed, total={len(cache)}")
    # Throttle
    save(force=False)
    return n


def stats():
    with _lock:
        cache = _load()
        by_kind = {}
        for e in cache.values():
            by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    return len(cache), by_kind


_PLAYABLE_RE = re.compile(
    r"/(movie|online|pelicula|capitulo|episodio|serie-online(?:-[\w-]+)?)/\d+",
    re.I,
)


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().strip())


def search(query, kind_filter=None, limit=500):
    """Busca en el indice por AND de tokens. Case/acento-insensible."""
    q = _norm(query)
    tokens = [t for t in re.split(r"[\s\-\._]+", q) if len(t) >= 2]
    if not tokens:
        return []
    with _lock:
        cache = _load()
        out = []
        for url, e in cache.items():
            if kind_filter and _match_kind(e.get("kind"), kind_filter) is False:
                continue
            t = _norm(e.get("title"))
            if all(tok in t for tok in tokens):
                out.append({
                    "title":   e.get("title"),
                    "url":     url,
                    "kind":    e.get("kind") or "movie",
                    "quality": e.get("quality"),
                    "image":   e.get("image"),
                    "source":  "wf",
                })
                if len(out) >= limit:
                    break
    # Ordenar por titulo
    out.sort(key=lambda x: _norm(x["title"]))
    return out


def _match_kind(entry_kind, filter_kind):
    """filter_kind puede ser 'movie' o 'tvshow' (generico)."""
    if not filter_kind:
        return True
    if filter_kind == "movie":
        return entry_kind in ("movie", "documentary")
    if filter_kind == "tvshow":
        return entry_kind == "tvshow"
    return entry_kind == filter_kind


def by_letter(letter, kind_filter=None, limit=2000):
    """Lista entradas cuyo titulo empieza por la letra dada.
    letter puede ser 'A'..'Z' o '#' (numeros/simbolos)."""
    letter = (letter or "").upper()
    with _lock:
        cache = _load()
        out = []
        for url, e in cache.items():
            if not _match_kind(e.get("kind"), kind_filter):
                continue
            t = _norm(e.get("title"))
            if not t:
                continue
            # Quitar articulos iniciales comunes para ordenar/letra
            t_sort = re.sub(r"^(el|la|los|las|un|una|the|a|an) ", "", t)
            first = t_sort[0].upper() if t_sort else "?"
            if letter == "#":
                if not first.isalpha():
                    out.append((t_sort, url, e))
            elif first == letter:
                out.append((t_sort, url, e))
        out.sort(key=lambda x: x[0])
        out = out[:limit]
    return [{
        "title":   e.get("title"),
        "url":     url,
        "kind":    e.get("kind") or "movie",
        "quality": e.get("quality"),
        "image":   e.get("image"),
        "source":  "wf",
    } for _, url, e in out]


def available_letters(kind_filter=None):
    """Devuelve dict {letra: count} de letras con items indexados."""
    with _lock:
        cache = _load()
        counts = {}
        for e in cache.values():
            if not _match_kind(e.get("kind"), kind_filter):
                continue
            t = _norm(e.get("title"))
            t_sort = re.sub(r"^(el|la|los|las|un|una|the|a|an) ", "", t)
            first = t_sort[0].upper() if t_sort else "?"
            key = first if first.isalpha() else "#"
            counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Rebuild desde sitemaps
# ---------------------------------------------------------------------------

SITEMAP_INDEX_URL = "https://wolfmax4k.com/sitemaps/index.xml"
_URL_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_H1_RE = re.compile(r'<h1[^>]*>([^<]{2,300})</h1>', re.I)
_OG_RE = re.compile(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)', re.I)
_QUALITY_RE = re.compile(
    r"\[?(4K|2160p|1080p|720p|480p|BluRay|BDRemux|Remux|HDR10\+|HDR|"
    r"Dolby Vision|DV|WEB-?DL|WEBRip|HEVC|x265|x264|HDTV)\]?",
    re.I,
)


def _classify_url(url):
    low = url.lower()
    if "/serie-online" in low or "/capitulo/" in low or "/episodio/" in low:
        return "tvshow"
    if "/documental" in low:
        return "documentary"
    if "/movie/" in low or "/online/" in low or "/pelicula" in low:
        return "movie"
    return "movie"


def _extract_title(html):
    m = _H1_RE.search(html)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = _OG_RE.search(html)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def _extract_quality(title):
    if not title:
        return None
    m = _QUALITY_RE.search(title)
    return m.group(1) if m else None


def fetch_sitemap_urls(hs_module, sess):
    """Descarga el sitemap index + todos los sitemaps hijos. Devuelve lista
    unica de URLs con ID numerico."""
    _LOG("fetching sitemap index...")
    try:
        r = hs_module.get(sess, SITEMAP_INDEX_URL, timeout=30)
        children = _URL_RE.findall(r.text or "")
    except Exception as e:
        _LOG(f"sitemap index fail: {e}")
        return []
    _LOG(f"sitemap index -> {len(children)} child sitemaps")
    seen = set()
    urls = []
    for child in children:
        if "/sitemaps/" not in child:
            continue
        try:
            r = hs_module.get(sess, child, timeout=30)
            for u in _URL_RE.findall(r.text or ""):
                if _PLAYABLE_RE.search(u) and u not in seen:
                    seen.add(u)
                    urls.append(u)
        except Exception as e:
            _LOG(f"sitemap {child} fail: {e}")
    _LOG(f"sitemap total unique playable URLs: {len(urls)}")
    return urls


def rebuild_from_sitemaps(hs_module, progress_cb=None, max_workers=20,
                          max_urls=None, skip_cached=True):
    """Reconstruye el indice scrapeando titulos de cada URL del sitemap.

    progress_cb(done, total, current_title) se llama periodicamente.
    max_urls limita (util para test); None = todas.
    skip_cached: no re-scrapea URLs ya en el indice.

    Devuelve numero de entradas nuevas.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sess = hs_module.make_session("https://wolfmax4k.com")
    urls = fetch_sitemap_urls(hs_module, sess)
    if max_urls:
        urls = urls[:max_urls]
    if skip_cached:
        with _lock:
            cache = _load()
            urls = [u for u in urls if u not in cache]
    total = len(urls)
    _LOG(f"rebuild: {total} URLs to scrape")
    if not total:
        save(force=True)
        return 0

    done = 0
    added = 0
    batch = []
    last_progress = time.time()

    def _fetch(url):
        try:
            r = hs_module.get(sess, url, timeout=20)
            html = r.text or ""
            title = _extract_title(html)
            if not title:
                return None
            quality = _extract_quality(title)
            kind = _classify_url(url)
            return {
                "url": url, "title": title,
                "kind": kind, "quality": quality,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch, u): u for u in urls}
        for fut in as_completed(futures):
            done += 1
            item = fut.result()
            if item:
                batch.append(item)
            # Cada 50 items: flush al indice y llamar progress
            if len(batch) >= 50:
                added += add(batch)
                batch = []
            now = time.time()
            if progress_cb and (now - last_progress) > 0.5:
                last_progress = now
                cur = (item or {}).get("title", "") if item else ""
                try:
                    cancel = progress_cb(done, total, cur)
                    if cancel:
                        _LOG(f"rebuild cancelled at {done}/{total}")
                        break
                except Exception:
                    pass
    if batch:
        added += add(batch)
    save(force=True)
    _LOG(f"rebuild finished: {done}/{total}, added {added} entries")
    return added
