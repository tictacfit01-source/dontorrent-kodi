"""
Scraper de WolfMax4K (wolfmax4k.com) -- la web NUEVA, desde el 26-09-2026.

WolfMax volvio de su caida con la web rehecha y todo lo anterior dio 404 (las
URLs /movie/, /online/, /serie-online-*, /series/<slug>, el "enlacito", los
sitemaps). El lector de sus paginas y el torrent protegido estan en wf_web.py
(puro, probado sin Kodi en tools/pruebas/wf_web.py); aqui va lo que usan la
caja (service.py) y el menu de Kodi (main.py), con la forma de salida de
siempre:

  search(q)            -> [item]   una peli POR VERSION; una serie, un item
                                   por capitulo "Titulo 4x07" (el relay los
                                   agrupa), la mejor calidad primero
  latest(kind, page)   -> [item]   lo ultimo de una seccion
  detail(url)          -> {title, plot, image, year, quality, downloads}
  episodios_serie(url) -> {title, episodes}   la serie ENTERA, cada capitulo
                                   en la mejor calidad que haya
  torrent_de(url)      -> URL del .torrent de una version o un capitulo
  resolver_diferido(l) -> "wf2:<tabla>:<id>" -> URL del .torrent
  search_and_expand(q) -> {title, image, seasons}   (menu de series de Kodi)
  az_letters / browse_az / index_stats / rebuild_index   (indice local, Kodi)

La version anterior (3.000 lineas de la web vieja) se retiro en 2.9.80.
"""

import concurrent.futures as _cf
import re
import threading
import time
import unicodedata
from urllib.parse import quote as urlquote

import xbmc
import xbmcaddon

from . import http_session as hs
from . import wf_index
from . import wf_web as _W

SOURCE = "wf"
_ADDON = xbmcaddon.Addon()
_LOG = lambda msg: xbmc.log(f"[MejorWolf/WF] {msg}", xbmc.LOGINFO)

_SES = [None]
_SES_LOCK = threading.Lock()
# URL de cada archivo -> (content-id, tabla, calidad), aprendido al listar:
# reproducir no necesita volver a abrir la ficha.
_CID = {}


def _base():
    return _W.BASE


def _ses():
    with _SES_LOCK:
        if _SES[0] is None:
            _SES[0] = hs.make_session(_W.BASE)
        return _SES[0]


def _pide(ruta, timeout=20):
    """HTML de una pagina de WolfMax (directo -> DNS seguro -> proxy)."""
    url = ruta if ruta.startswith("http") else _W.BASE + ruta
    r = hs.get(_ses(), url, timeout=timeout, headers={"Referer": _W.BASE + "/"})
    r.encoding = "utf-8"
    return r.text or ""


def _post_json(url, cuerpo, cab):
    """POST JSON -> (status, dict). Un 429 tambien trae su JSON (el limite)."""
    try:
        r = hs.post(_ses(), url, json=cuerpo, headers=cab, timeout=20,
                    allow_redirects=False)
    except Exception as e:
        r = getattr(e, "response", None)
        if r is None:
            raise
    try:
        return r.status_code, (r.json() or {})
    except Exception:
        return r.status_code, {}


# nombres de antes, por si algo de fuera los usa
_wf_pide = _pide
_wf_post_json = _post_json


def _recuerda(a):
    if a.get("url") and a.get("cid"):
        _CID[a["url"]] = (a["cid"], a["tabla"], a.get("calidad") or "")
        if len(_CID) > 6000:
            for k in list(_CID)[:2000]:
                _CID.pop(k, None)


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _items(tarjetas):
    """Tarjetas de la web -> items de siempre. Una peli, UNA POR VERSION (4K,
    1080p...: el relay las junta y se queda con la mejor); una serie, un item
    por capitulo "Titulo 4x07" (el relay los agrupa en su tarjeta), con la
    mejor calidad primero para que gane al agrupar."""
    pelis, caps = [], []
    for t in tarjetas:
        base = t.get("base") or t.get("titulo") or ""
        comun = {"thumb": t.get("thumb") or None, "image": t.get("thumb") or None}
        if t.get("anio"):
            comun["year"] = t["anio"]
        con_caps = [a for a in t.get("archivos") or [] if a.get("temporada") or a.get("episodio")]
        if t.get("tipo") in ("serie", "documental") and con_caps:
            for a in con_caps:
                _recuerda(a)
                caps.append(dict(comun, title="%s %dx%02d" % (base, a["temporada"], a["episodio"]),
                                 kind="tvshow", url=a["url"],
                                 quality=t.get("calidad") or a.get("calidad") or ""))
            continue
        archivos = t.get("archivos") or []
        if not archivos:
            pelis.append(dict(comun, title=base, url=t["url"], quality=t.get("calidad") or "",
                              kind="movie" if t.get("tipo") == "pelicula" else "tvshow"))
            continue
        for a in archivos:
            _recuerda(a)
            pelis.append(dict(comun, title=base, kind="movie", url=a["url"],
                              quality=a.get("calidad") or t.get("calidad") or ""))
    caps.sort(key=lambda it: -_W.rango(it.get("quality")))
    return pelis + caps


_wf_items = _items


def _tarjetas_de(ruta):
    html = _pide(ruta)
    ts = _W.tarjetas(html)
    if _W.sin_archivos_raro(html, ts):
        _LOG("OJO: la pagina de WolfMax trae archivos y no se ha leido ninguno: "
             "ha cambiado su marcado (wf_web.tarjetas) -- %s" % ruta)
    return html, ts


def search(query):
    """Busqueda en la web nueva: /buscar?q= (una o dos paginas)."""
    q = (query or "").strip()
    if not q:
        return []
    html, ts = _tarjetas_de("/buscar?q=" + urlquote(q))
    if _W.paginas(html)[1] > 1:
        try:
            ts += _tarjetas_de("/buscar?q=%s&pagina=2" % urlquote(q))[1]
        except Exception as e:
            _LOG("buscar p2: %s" % e)
    items = _items(ts)
    _LOG("search %r: %d tarjetas -> %d items" % (q, len(ts), len(items)))
    try:
        wf_index.add(items)
    except Exception:
        pass
    return items


# Pelis: /peliculas (lo ultimo subido, con todas sus versiones) y no /ultimos:
# el 28-09 /ultimos eran 45 series, 4 documentales y UNA peli, y el Inicio se
# quedaba casi sin WolfMax; /peliculas daba 24 de 2025-2026, varias en 4K.
_SECCION = {"movie": "/peliculas", "movie_720p": "/peliculas", "movie_hd": "/peliculas",
            "movie_4k": "/peliculas", "tvshow": "/series", "tvshow_720p": "/series",
            "tvshow_hd": "/series", "tvshow_4k": "/series",
            "documentary": "/documentales"}
_FILTRO = {"movie_4k": "4K", "tvshow_4k": "4K", "movie_hd": "1080p",
           "tvshow_hd": "1080p", "movie_720p": "720p", "tvshow_720p": "720p"}


def latest(kind="movie", page=1):
    """Lo ultimo de una seccion. La web no filtra por calidad: se filtra aqui."""
    kind = kind or "movie"
    ruta = _SECCION.get(kind, "/peliculas")
    page = int(page or 1)
    if page > 1:
        ruta += "?pagina=%d" % page
    items = _items(_tarjetas_de(ruta)[1])
    # cada seccion, lo suyo (los documentales pueden ser de un archivo o por
    # capitulos: esos, todos)
    if kind.startswith("movie"):
        items = [it for it in items if (it.get("kind") or "movie") == "movie"]
    elif kind.startswith("tvshow"):
        items = [it for it in items if it.get("kind") == "tvshow"]
    q = _FILTRO.get(kind)
    if q:
        items = [it for it in items if it.get("quality") == q]
    try:
        wf_index.add(items)
    except Exception:
        pass
    return items


def resolver_diferido(link, referer=""):
    """"wf2:<tabla>:<id>" -> URL del .torrent (la prueba de trabajo, ahora)."""
    cid, tabla = _W.de_diferido(link)
    return _W.torrent(_post_json, cid, tabla, referer)


def torrent_de(url):
    """URL del .torrent de una peli (una version) o un capitulo."""
    u = _W.absoluta(url)
    e = _CID.get(u)
    if e:
        return _W.torrent(_post_json, e[0], e[1], u)
    d = _W.ficha(_pide(u), u)
    if len(d["archivos"]) != 1:
        raise RuntimeError("WolfMax: %s no es un archivo suelto" % u)
    a = d["archivos"][0]
    _recuerda(a)
    return _W.torrent(_post_json, a["cid"], a["tabla"], u)


def detail(url):
    """La ficha en la forma de siempre: {title, plot, image, year, quality,
    downloads:[{label, season, episode, quality, torrent_url, url, size}]}.
    Una peli o un capitulo: su torrent, pedido ya. Una temporada: sus
    capitulos con el enlace DIFERIDO (se pide al reproducir: una temporada son
    10-20 capitulos y WolfMax permite 60 descargas por hora)."""
    u = _W.absoluta(url)
    if not _W.es_nueva(u):
        raise RuntimeError("WolfMax: URL de la web vieja (%s)" % u)
    _LOG("detail: %s" % u)
    d = _W.ficha(_pide(u), u)
    suelto = d["tipo"] in ("pelicula", "episodio", "episodio_doc") and len(d["archivos"]) == 1
    downloads = []
    for a in d["archivos"]:
        _recuerda(a)
        tu = _W.torrent(_post_json, a["cid"], a["tabla"], u) if suelto \
            else _W.diferido(a["cid"], a["tabla"])
        s, e = a.get("temporada") or 0, a.get("episodio") or 0
        es_cap = bool(s or e) and d["tipo"] != "pelicula"
        downloads.append({"label": _W.etiqueta_ep(s, e, a.get("episodio_fin") or 0) if es_cap
                          else (a.get("etiqueta") or d["calidad"] or "Descargar"),
                          "season": s if es_cap else None, "episode": e if es_cap else None,
                          "quality": a.get("calidad") or d["calidad"], "torrent_url": tu,
                          "url": a.get("url") or u, "size": a.get("tamano") or ""})
    title = d["base"] or d["titulo"]
    if d["tipo"] in ("episodio", "episodio_doc") and downloads and downloads[0]["season"] is not None:
        title = "%s %s" % (d["base"], downloads[0]["label"])
    return {"title": title, "plot": d["sinopsis"], "image": d["thumb"],
            "year": d["anio"] or None, "quality": d["calidad"], "downloads": downloads}


def episodios_serie(url, titulo=""):
    """TODOS los capitulos de una serie, juntando temporadas y calidades, cada
    uno en la MEJOR calidad que haya. Vale cualquier URL suya (temporada o
    capitulo) o, si es de la web vieja, su titulo. {title, episodes:[...]}"""
    u = _W.absoluta(url)
    base, fichas, vistas = "", [], set()
    if _W.es_nueva(u):
        # si esa pagina falla, la serie se busca por su nombre (abajo): un
        # tropiezo con una pagina no puede dejar la ficha sin capitulos
        try:
            d = _W.ficha(_pide(u), u)
            if d["tipo"] in ("episodio", "episodio_doc") and d.get("temporada_url"):
                tu = d["temporada_url"]
                d = _W.ficha(_pide(tu), tu)
                vistas.add(tu)
            vistas.add(u)
            base = d["base"]
            fichas.append(d)
        except Exception as e:
            _LOG("episodios_serie %s: %s" % (u, e))
    base = base or _W.titulo_base(titulo)
    if not base:
        return {"title": "", "episodes": []}
    # las demas temporadas y calidades, por la busqueda
    try:
        html, ts = _tarjetas_de("/buscar?q=" + urlquote(base))
        if _W.paginas(html)[1] > 1:
            ts += _tarjetas_de("/buscar?q=%s&pagina=2" % urlquote(base))[1]
    except Exception as e:
        _LOG("episodios_serie buscar: %s" % e)
        ts = []
    nb = _norm(base)
    otras = [t["url"] for t in ts if t["tipo"] in ("serie", "documental")
             and _norm(t["base"]) == nb and t["url"] not in vistas]
    tope = time.time() + 12.0
    ex = _cf.ThreadPoolExecutor(max_workers=6)
    try:
        futs = [ex.submit(lambda x: _W.ficha(_pide(x, timeout=10), x), x) for x in otras[:16]]
        for f in futs:
            try:
                fichas.append(f.result(timeout=max(0.1, tope - time.time())))
            except Exception:
                continue
    finally:
        ex.shutdown(wait=False)
    mejor = {}
    for d in fichas:
        for a in d.get("archivos") or []:
            s, e = a.get("temporada") or 0, a.get("episodio") or 0
            if not (s or e):
                continue
            _recuerda(a)
            q = d.get("calidad") or a.get("calidad") or ""
            fin = a.get("episodio_fin") or 0
            k = (s, e, fin)
            if k not in mejor or _W.rango(q) > _W.rango(mejor[k]["quality"]):
                mejor[k] = {"label": _W.etiqueta_ep(s, e, fin), "season": s, "episode": e,
                            "quality": q, "url": a["url"], "content_id": a["url"],
                            "src": "wf"}
                if fin:
                    mejor[k]["episode_end"] = fin
    eps = [mejor[k] for k in sorted(mejor)]
    _LOG("episodios_serie %r: %d fichas -> %d capitulos" % (base, len(fichas), len(eps)))
    return {"title": base, "episodes": eps}


def episodios_completos(cap_url, slug=""):
    return (episodios_serie(cap_url) or {}).get("episodes") or []


def search_and_expand(query):
    """Para el menu de SERIES de Kodi: {title, image, seasons: {n: [capitulo]}}.
    (En 2.9.77 devolvia la lista de la busqueda y ese menu se quedaba en "No se
    encontraron capitulos".)"""
    series = [it for it in search(query) if it.get("kind") == "tvshow"]
    if not series:
        return {"title": query, "image": None, "seasons": {}}
    nombre = re.sub(r"\s+\d{1,2}x\d{1,3}\b.*$", "", series[0].get("title") or "")
    r = episodios_serie(series[0]["url"], nombre or query)
    temporadas = {}
    for e in r.get("episodes") or []:
        temporadas.setdefault(e["season"], []).append({
            "season": e["season"], "episode": e["episode"],
            "quality": e.get("quality") or "", "url": e["url"],
            "title": "%s %s" % (r.get("title") or query, e["label"])})
    return {"title": r.get("title") or query, "image": series[0].get("thumb"),
            "seasons": temporadas}


def _build_catalog():
    return [], True


# --- el indice local (menu A-Z de Kodi) ---------------------------------------
def az_letters(kind_filter=None):
    """[(letra, cuantos), ...] para el menu A-Z (# primero, luego A-Z)."""
    counts = wf_index.available_letters(kind_filter=kind_filter)
    letras = [("#", counts["#"])] if "#" in counts else []
    return letras + [(ch, counts[ch]) for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if ch in counts]


def browse_az(letter, kind_filter=None):
    return wf_index.by_letter(letter, kind_filter=kind_filter)


def index_stats():
    return wf_index.stats()


def rebuild_index(progress_cb=None, max_workers=20, max_urls=None, paginas=20):
    """Rehace el indice local con los LISTADOS de la web nueva (no hay
    sitemaps): las primeras `paginas` de pelis, series y documentales, poco a
    poco para no cargar a WolfMax. progress_cb(hechas, total, actual) -> True
    para cancelar. Devuelve cuantas entradas nuevas."""
    rutas = []
    for sec in ("/peliculas", "/series", "/documentales"):
        rutas += [sec] + ["%s?pagina=%d" % (sec, n) for n in range(2, paginas + 1)]
    nuevas = 0
    for i, ruta in enumerate(rutas):
        if progress_cb and progress_cb(i, len(rutas), ruta):
            break
        try:
            nuevas += wf_index.add(_items(_tarjetas_de(ruta)[1])) or 0
        except Exception as e:
            _LOG("rebuild %s: %s" % (ruta, e))
        time.sleep(0.5)
    try:
        wf_index.save(force=True)
    except Exception:
        pass
    return nuevas
