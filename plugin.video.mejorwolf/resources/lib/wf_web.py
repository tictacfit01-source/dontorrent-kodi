# -*- coding: utf-8 -*-
"""WolfMax4K: la web NUEVA (27-09-2026).

El 26-09 WolfMax volvio de su caida con la web rehecha de arriba abajo, y todo
lo anterior dejo de valer (las URLs viejas dan 404, el "enlacito" ya no existe):

  pelicula    /pelicula/<id>          una ficha POR VERSION (4K, BDremux-1080p,
                                      MicroHD-1080p, DVDRip...), con su torrent
  serie       /serie/<id>             una ficha por TEMPORADA y calidad
                                      ("Ted Lasso - 4a Temporada [4k]") con
                                      todos sus capitulos: /serie/episodio/<id>
  documental  /documental/<id>        igual que las series:
                                      /documental/episodio/<id>
  buscar      /buscar?q=              tarjetas: la peli con TODAS sus versiones;
                                      la temporada con su ultimo capitulo
  listados    /ultimos, /peliculas?pagina=N (24 por pagina), /series,
              /documentales
  torrent     POST /api/descargas: el MISMO boton protegido que DonTorrent
              (reto -> prueba de trabajo sha256 con 3 ceros -> download_url);
              limite: 60 descargas por hora
Desde la zona de Render WolfMax pone un reto de Cloudflare: todo esto lo hacen
las cajas (casas en Espana, o el proxy desde aqui) y lo suben al relay.

Aqui solo va lo propio de la web: leer sus paginas (funciones puras, probadas
en tools/pruebas/wf_web.py con marcado real) y pedir el torrent. Las
peticiones las hace quien llama, para que el addon use sus caminos de respaldo
(directo -> DNS seguro -> proxy) y las pruebas no toquen la red.
"""
import hashlib
import html as _html
import re
import threading
import time

BASE = "https://wolfmax4k.com"
API_DESCARGAS = BASE + "/api/descargas"

_RUTA_RE = re.compile(
    r"^(?:https?:)?(?://(?:www\.)?wolfmax4k\.com)?"
    r"/(pelicula|serie/episodio|serie|documental/episodio|documental)"
    r"/([a-z0-9]{4,16})/?(?:[?#].*)?$", re.I)
_TIPO = {"pelicula": "pelicula", "serie": "serie", "serie/episodio": "episodio",
         "documental": "documental", "documental/episodio": "episodio_doc"}


def ruta(u):
    """(tipo, id) de una URL de la web nueva; (None, None) si no lo es.
    tipo: pelicula | serie | episodio | documental | episodio_doc."""
    m = _RUTA_RE.match((u or "").strip())
    if not m:
        return None, None
    return _TIPO[m.group(1).lower()], m.group(2).lower()


def es_nueva(u):
    """True si la URL es de la web nueva (las viejas dan 404 desde el 26-09)."""
    return ruta(u)[0] is not None


def absoluta(href):
    """URL completa y SIN www (la redireccion de www convierte un POST en GET)."""
    h = _html.unescape((href or "").strip())
    if not h:
        return ""
    if h.startswith("//"):
        h = "https:" + h
    elif h.startswith("/"):
        h = BASE + h
    return re.sub(r"^https?://www\.wolfmax4k\.com", BASE, h, flags=re.I)


def texto(s):
    """Texto limpio de un trozo de HTML."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


# --- calidad -----------------------------------------------------------------
_RES = (("2160", "4K"), ("4k", "4K"), ("uhd", "4K"), ("1080", "1080p"),
        ("720", "720p"), ("480", "480p"))
_FORMATOS = (("bdremux", "BDRemux"), ("remux", "BDRemux"), ("bluray", "BluRay"),
             ("blu-ray", "BluRay"), ("microhd", "MicroHD"), ("hdtv", "HDTV"),
             ("dvdrip", "DVDRip"), ("hdrip", "HDRip"), ("webrip", "WEBRip"),
             ("web-dl", "WEB-DL"), ("webdl", "WEB-DL"), ("dvd", "DVD"),
             ("ts", "TS"), ("screener", "Screener"))


def calidad(s):
    """"4K", "1080p", "720p", "480p" si se sabe la resolucion; si no, el
    formato ("HDTV", "DVDRip"...); "" si nada. Vale para "BluRay-720p",
    "BDremux-1080p", "4K", "HDTV" o el "[4k]" de un titulo de serie."""
    t = (s or "").lower()
    for pat, q in _RES:
        if re.search(r"(?<![a-z0-9])" + pat, t):
            return q
    for pat, q in _FORMATOS:
        if re.search(r"(?<![a-z0-9])" + re.escape(pat) + r"(?![a-z0-9])", t):
            return q
    return ""


_RANGO = {"4K": 9, "1080p": 8, "BDRemux": 8, "720p": 6, "BluRay": 6,
          "MicroHD": 5, "WEB-DL": 5, "480p": 4, "HDTV": 3, "WEBRip": 3,
          "HDRip": 3, "DVDRip": 2, "DVD": 2}


def rango(q):
    """Para quedarse con la MEJOR version de un capitulo (4K primero)."""
    return _RANGO.get(q or "", 1 if q else 0)


# --- titulos -----------------------------------------------------------------
_EP_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[xX×]\s*(\d{1,3})(?!\d)")
_TEMP_RE = re.compile(
    r"\s*[-–·|:]?\s*(\d{1,2})\s*[ªº°a-z]{0,2}\.?\s*temporada\b.*$",
    re.I)
_TEMP_RE2 = re.compile(r"\s*[-–·|:]?\s*temporada\s*(\d{1,2})\b.*$", re.I)


def episodio(s):
    """(temporada, capitulo) de "4x01", "Episodio 1x10 -", "1x00 (PILOTO)"."""
    m = _EP_RE.search(s or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# PACKS: WolfMax publica a veces varios capitulos en un solo torrent
# ("1x01 al 1x03", 3,5 GB). Rotulados "1x01" a secas, la temporada parecia
# tener huecos (Ted Lasso T1: 1x01, 1x04, 1x05, 1x06, 1x10) y al darle se
# bajaban cuatro capitulos creyendo que era uno.
_EPR_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[xX\u00d7]\s*(\d{1,3})\s*(?:al|a|-|\u2013|y)\s*"
    r"(?:(\d{1,2})\s*[xX\u00d7]\s*)?(\d{1,3})(?!\d)", re.I)


def episodio_fin(s):
    """El ULTIMO capitulo de un pack ("1x06 al 1x09" -> 9); 0 si es uno solo."""
    m = _EPR_RE.search(s or "")
    if not m:
        return 0
    if m.group(3) and int(m.group(3)) != int(m.group(1)):
        return 0
    fin = int(m.group(4))
    return fin if fin > int(m.group(2)) else 0


def etiqueta_ep(temp, cap, fin=0):
    """"4x01", o "1x06 al 1x09" si es un pack (como lo escribe WolfMax)."""
    if fin and fin > cap:
        return "%dx%02d al %dx%02d" % (temp, cap, temp, fin)
    return "%dx%02d" % (temp, cap)


def temporada(titulo):
    """4 de "Ted Lasso - 4a Temporada [4k]"; 0 si no lo dice."""
    for rx in (_TEMP_RE, _TEMP_RE2):
        m = rx.search(titulo or "")
        if m:
            return int(m.group(1))
    return 0


def titulo_base(t):
    """"Ted Lasso" de "Ted Lasso - 1a Temporada [720p]."; "Dune" de "Dune."."""
    t = _html.unescape(t or "")
    t = re.sub(r"\[[^\]]*\]", " ", t)
    t = _TEMP_RE.sub("", t)
    t = _TEMP_RE2.sub("", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .-–·|:,")


# --- paginas -----------------------------------------------------------------
# Leer POR CLASE y por nombre de atributo, nunca por la forma exacta de la
# etiqueta. El 28-09, dos dias despues de estrenar web, WolfMax añadio un
# atributo a cada archivo de las tarjetas (<li class="wolf-card-file"
# data-wolf-episode-id="...">) y el lector, que buscaba la etiqueta tal cual,
# dejo de ver TODOS los archivos de busquedas y listados: las series llegaban
# sin capitulos (una tarjeta vacia por calidad) y /ultimos sin calidades. Sin
# un solo error: la web seguia contestando 200.
_ATR_RE = re.compile(r'([a-zA-Z_:][-\w:.]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')


def _atributos(etiqueta):
    """Los atributos de una etiqueta de apertura, en cualquier orden."""
    return dict((k.lower(), _html.unescape(v if v or not w else w))
                for k, v, w in _ATR_RE.findall(etiqueta or ""))


def _clases(atr):
    return (atr.get("class") or "").split()


def _bloques(html, etiqueta, clase=None, hasta=None):
    """[(atributos, dentro)] de cada <etiqueta ...> con esa clase (entre
    otras) hasta su cierre (o hasta `hasta`). Para etiquetas que no se anidan
    consigo mismas (article, li, a, button, h1, p, section...)."""
    fin = hasta or ("</%s>" % etiqueta)
    out = []
    for m in re.finditer(r"<%s\b([^>]*)>" % etiqueta, html or "", re.I):
        atr = _atributos(m.group(1))
        if clase and clase not in _clases(atr):
            continue
        j = (html or "").find(fin, m.end())
        out.append((atr, html[m.end():j] if j >= 0 else html[m.end():]))
    return out


def _uno(html, etiqueta, clase=None):
    b = _bloques(html, etiqueta, clase)
    return b[0] if b else ({}, "")


def _boton(html):
    """(content-id, tabla) del boton de descarga protegido, o (None, None)."""
    for m in re.finditer(r"<button\b([^>]*)>", html or "", re.I):
        atr = _atributos(m.group(1))
        cid = re.sub(r"\D", "", atr.get("data-content-id") or "")
        tabla = re.sub(r"[^a-z_]", "", (atr.get("data-tabla") or "").lower())
        if cid and tabla:
            return cid, tabla
    return None, None


def _archivo(url, etiqueta, formato, tamano, cid, tabla):
    s, e = episodio(etiqueta)
    return {"url": url, "etiqueta": etiqueta, "formato": formato,
            "calidad": calidad(formato), "tamano": tamano, "cid": cid,
            "tabla": tabla, "temporada": s, "episodio": e,
            "episodio_fin": episodio_fin(etiqueta) if (s or e) else 0}


def tarjetas(html):
    """Las tarjetas de una busqueda o un listado, con sus archivos si los
    trae (la busqueda: la peli con todas sus versiones, la temporada con su
    ultimo capitulo)."""
    out = []
    for _atr_a, a in _bloques(html, "article", "wolf-card"):
        atr_m, dentro_m = _uno(a, "a", "wolf-card-main")
        url = absoluta(atr_m.get("href") or "")
        tipo, _id = ruta(url)
        if not tipo:
            continue
        titulo = texto(dentro_m)
        imgs = [_atributos(x) for x in re.findall(r"<img\b([^>]*)>", a, re.I)]
        orig = next((x.get("data-original") for x in imgs if x.get("data-original")), "")
        _am, meta = _uno(a, "p", "wolf-card-meta")
        spans = [texto(x) for x in re.findall(r"<span\b[^>]*>(.*?)</span>", meta, re.S)]
        anio = next((int(x) for x in spans if re.fullmatch(r"(19|20)\d\d", x)), 0)
        archivos = []
        for _atr_f, f in _bloques(a, "li", "wolf-card-file"):
            atr_fm, dentro = _uno(f, "a", "wolf-card-format")
            cid, tabla = _boton(f)
            if not atr_fm.get("href") or not cid:
                continue
            fuerte = re.search(r"<strong\b[^>]*>(.*?)</strong>", dentro, re.S)
            span = re.search(r"<span\b[^>]*>(.*?)</span>", dentro, re.S)
            etiqueta = texto(fuerte.group(1)) if fuerte else texto(dentro)
            formato = texto(span.group(1)) if span else etiqueta
            _asz, sz = _uno(f, "span", "wolf-card-size")
            archivos.append(_archivo(absoluta(atr_fm["href"]), etiqueta, formato,
                                     texto(sz), cid, tabla))
        out.append({"url": url, "tipo": tipo, "titulo": titulo,
                    "base": titulo_base(titulo), "temporada": temporada(titulo),
                    "calidad": calidad(" ".join(re.findall(r"\[([^\]]*)\]", titulo))),
                    "thumb": absoluta(orig) if orig else "",
                    "anio": anio, "generos": [x for x in spans if not x.isdigit()],
                    "archivos": archivos})
    return out


def sin_archivos_raro(html, tarjetas_leidas):
    """True si la pagina TRAE archivos y el lector no ha visto ninguno: el
    marcado ha vuelto a cambiar (para decirlo en el registro, no callarlo)."""
    return "wolf-card-file" in (html or "") and bool(tarjetas_leidas) and \
        not any(t.get("archivos") for t in tarjetas_leidas)


def paginas(html):
    """(pagina actual, total de paginas) de "Pagina 2 de 961"; (1, 1) si no."""
    m = re.search(r"P\S{1,8}gina\s+(\d+)\s+de\s+(\d+)", texto(html or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (1, 1)


def ficha(html, url=""):
    """Todo lo de una ficha: peli (una version), temporada de serie,
    documental o capitulo suelto. Los archivos, con su content-id y tabla."""
    h = html or ""
    tipo, _id = ruta(url)
    _a1, h1 = _uno(h, "h1")
    nombre_ep = ""
    titulo = ""
    if h1:
        atr_ne, ne = _uno(h1, "span", "wolf-episode-name")
        if ne:
            nombre_ep = texto(ne)
            h1 = re.sub(r"<span\b[^>]*wolf-episode-name[^>]*>.*?</span>", " ", h1, flags=re.S)
        titulo = texto(h1)
    ceja = next((texto(d) for atr, d in _bloques(h, "p") if "text-wolf-accent" in _clases(atr)), "")
    anio = re.search(r"\?anyo=(\d{4})", h)
    generos = [texto(g) for g in re.findall(r'\?genero=[^"]+"[^>]*>(.*?)</a>', h, re.S)]
    sin = re.search(r'id="wolf-synopsis"[^>]*>(.*?)</div>', h, re.S)
    arte = ""
    for m in re.finditer(r"<img\b([^>]*)>", h, re.I):
        atr = _atributos(m.group(1))
        if "wolf-detail-art" in _clases(atr) and atr.get("data-original"):
            arte = atr["data-original"]
            break
    atr_t, _t = _uno(h, "a", "wolf-season-link")
    tempo = atr_t.get("href") or ""
    if not tempo:
        _ab, miga = _uno(h, "nav", "wolf-episode-breadcrumb")
        enl = [x for x in re.findall(r'href="([^"]+)"', miga) if ruta(x)[0] in ("serie", "documental")]
        tempo = enl[0] if enl else ""
    archivos = []
    for m in re.finditer(r"<div\b([^>]*)>", h, re.I):
        atr = _atributos(m.group(1))
        if "wolf-episode" not in _clases(atr):
            continue
        j = h.find("</button>", m.end())
        fila = h[m.end():j] if j >= 0 else ""
        enl = re.search(r'<a\b([^>]*)>(.*?)</a>', fila, re.S)
        cid, tabla = _boton(fila + "</button>" if fila else "")
        href = _atributos(enl.group(1)).get("href") if enl else ""
        if not href or not cid:
            continue
        fmt = re.search(r'wolf-episode-format[^>]*>([^<]*)', fila)
        _az, sz = _uno(fila, "span", "wolf-episode-size")
        archivos.append(_archivo(absoluta(href), texto(enl.group(2)),
                                 texto(fmt.group(1)) if fmt else "", texto(sz), cid, tabla))
    if not archivos:
        _ap, p = _uno(h, "section", "wolf-file-panel")
        if p:
            dd = dict((texto(k).lower(), texto(v)) for k, v in
                      re.findall(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", p, re.S))
            cid, tabla = _boton(p)
            if cid:
                fmt = dd.get("calidad", "")
                tam = next((v for k, v in dd.items() if k.startswith("tama")), "")
                archivos.append(_archivo(absoluta(url) if url else "",
                                         nombre_ep or fmt, fmt, tam, cid, tabla))
    cal = calidad(ceja.split("·")[-1]) if "·" in ceja else ""
    cal = cal or calidad(" ".join(re.findall(r"\[([^\]]*)\]", titulo)))
    if not cal and archivos:
        cal = archivos[0]["calidad"]
    return {"url": absoluta(url) if url else "", "tipo": tipo, "titulo": titulo,
            "base": titulo_base(titulo), "temporada": temporada(titulo),
            "episodio": nombre_ep, "calidad": cal,
            "anio": int(anio.group(1)) if anio else 0, "generos": generos,
            "sinopsis": texto(sin.group(1)) if sin else "",
            "thumb": absoluta(arte) if arte else "",
            "temporada_url": absoluta(tempo) if tempo else "",
            "archivos": archivos}


# --- el torrent ----------------------------------------------------------------
class Limite(Exception):
    """WolfMax: 60 descargas por hora. `minutos`: lo que pide esperar."""

    def __init__(self, minutos=60):
        Exception.__init__(self, "WolfMax: limite de descargas (espera %s min)" % minutos)
        self.minutos = int(minutos or 60)


class Captcha(Exception):
    pass


def prueba_de_trabajo(reto, ceros=3):
    """El nonce tal que sha256(reto + nonce) empieza por `ceros` ceros (lo que
    hace el navegador en download.js). Con 3 ceros son ~4.000 intentos."""
    objetivo = "0" * int(ceros)
    n = 0
    while True:
        if hashlib.sha256((reto + str(n)).encode("utf-8")).hexdigest().startswith(objetivo):
            return n
        n += 1


# El torrent de cada archivo, recordado: la prueba de trabajo cuenta para el
# limite de 60 por hora y el mismo capitulo se pide varias veces (semillas,
# reproducir, la ficha...). Y si WolfMax dice "limite", no se le insiste.
_TORRENTS = {}
_TORRENTS_TTL = 12 * 3600
_LIMITE_HASTA = [0.0]
_LOCK = threading.Lock()


def torrent(post_json, cid, tabla, referer=""):
    """URL del .torrent de un archivo (content-id + tabla).

    post_json(url, cuerpo, cabeceras) -> (status, dict) la pone quien llama.
    Lanza Limite si WolfMax corta (y durante ese rato ya ni se le pregunta),
    Captcha si lo pide, RuntimeError en cualquier otro fallo."""
    clave = (str(cid), str(tabla))
    now = time.time()
    with _LOCK:
        e = _TORRENTS.get(clave)
        if e and now - e[1] < _TORRENTS_TTL:
            return e[0]
        if now < _LIMITE_HASTA[0]:
            raise Limite(max(1, int((_LIMITE_HASTA[0] - now) / 60)))
    cab = {"Content-Type": "application/json", "Origin": BASE,
           "Referer": absoluta(referer) if referer else BASE + "/",
           "X-Requested-With": "XMLHttpRequest"}
    st, g = post_json(API_DESCARGAS, {"action": "generate",
                                      "content_id": int(cid), "tabla": tabla}, cab)
    g = g if isinstance(g, dict) else {}
    _mira_limite(st, g)
    if not g.get("success") or not g.get("challenge"):
        raise RuntimeError("WolfMax: sin reto (%s %s)" % (st, g.get("error") or ""))
    reto = g["challenge"]
    nonce = prueba_de_trabajo(reto, 3)
    st, v = post_json(API_DESCARGAS, {"action": "validate", "challenge": reto,
                                      "nonce": nonce}, cab)
    v = v if isinstance(v, dict) else {}
    _mira_limite(st, v)
    if v.get("status") == "captcha_required":
        raise Captcha("WolfMax pide captcha")
    url = absoluta(v.get("download_url") or "")
    if not v.get("success") or not url:
        raise RuntimeError("WolfMax: sin torrent (%s %s)" % (st, v.get("error") or ""))
    with _LOCK:
        _TORRENTS[clave] = (url, time.time())
        if len(_TORRENTS) > 2000:
            for k in sorted(_TORRENTS, key=lambda k: _TORRENTS[k][1])[:500]:
                _TORRENTS.pop(k, None)
    return url


def _mira_limite(st, d):
    if st == 429 or d.get("status") == "limit_exceeded":
        minutos = int(d.get("wait_minutes") or 60)
        with _LOCK:
            _LIMITE_HASTA[0] = time.time() + 60 * minutos
        raise Limite(minutos)


# Enlace DIFERIDO de un archivo: "wf2:<tabla>:<content-id>". El torrent se pide
# al reproducir, no al listar (una temporada son 10-20 capitulos y el limite es
# de 60 por hora).
def diferido(cid, tabla):
    return "wf2:%s:%s" % (tabla, cid)


def es_diferido(u):
    return bool(re.match(r"^wf2:[a-z_]+:\d+$", (u or "").strip()))


def de_diferido(u):
    _, tabla, cid = (u or "").strip().split(":", 2)
    return cid, tabla
