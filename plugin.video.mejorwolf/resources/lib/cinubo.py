# -*- coding: utf-8 -*-
"""Cinubo (antigua series.ly): streaming en castellano (2.9.91; 2.9.92 más servidores).

Dos cosas, las dos desde la IP de casa (Cloudflare no deja entrar al relay):

* `pedir(ev)`: el relay pide una página de Cinubo con la sesión del dueño
  (trabajo "cbget"). La sesión viaja en el trabajo y NO se guarda aquí.
* `resolver(url)`: de la página de un servidor de vídeo (Lulustream, Vidara...)
  saca la emisión directa (m3u8). Va firmada para la IP que la pide: por eso
  la saca la tele que reproduce, no el relay. Kodi la reproduce sin el
  reproductor web, o sea, sin su publicidad.
"""
import json
import re

try:
    from urllib.parse import urlparse, urlencode, quote
except ImportError:  # pragma: no cover
    from urlparse import urlparse
    from urllib import urlencode

import requests

BASE = "https://cinubo.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0 Safari/537.36")
_NOMBRE = re.compile(r"^(seriesly_session|remember_web_[0-9a-f]{40}|sly_device_id)$")
_VALOR = re.compile(r"^[A-Za-z0-9%=._-]{8,1200}$")
# Solo estas rutas: fichas, capítulos, enlaces, la portada (token) y el buscador
_RUTA = re.compile(r"^/(?:$|[psa]/[a-z0-9]{4,12}(?:/\d{1,3}x\d{1,4})?$|t/[A-Za-z0-9=_%-]{20,600}$)")
_CSRF = re.compile(r'<meta name="csrf-token" content="([A-Za-z0-9]{20,80})"')
_TOPE_TXT = 900000


class NoSale(Exception):
    """El servidor no da el vídeo (caído, cortado por el operador, cambiado)."""


# Si Cinubo se muda de dirección (lo anuncia en t.me/cinubo), el relay lo
# descubre y lo manda en el trabajo ("dom"); sin él, la de siempre (2.9.96).
_DOM = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?\.){1,3}[a-z]{2,12}$")


def _dominio(ev):
    d = str((ev or {}).get("dom") or "").strip().lower()
    return d if _DOM.match(d) else "cinubo.com"


def _sesion(ck, dom="cinubo.com"):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
    for k, v in (ck or {}).items():
        k, v = str(k), str(v)
        if _NOMBRE.match(k) and _VALOR.match(v):
            s.cookies.set(k, v, domain="." + dom, path="/")
    return s


def _host(r):
    """A qué dirección se llegó de verdad (tras las redirecciones)."""
    try:
        return (urlparse(r.url).hostname or "").lower()
    except Exception:
        return ""


def pedir(ev):
    """JSON {"st", "t", "h"} con lo que contesta Cinubo ("h": la dirección a la
    que se llegó; si no es la pedida, Cinubo se ha mudado). "" si no vale."""
    path = str(ev.get("path") or "")
    q = str(ev.get("q") or "").strip()[:80]
    dom = _dominio(ev)
    BASE = "https://" + dom
    s = _sesion(ev.get("ck"), dom)
    # (2.9.104) Una lista de su portada ("para ver ahora", "recientes",
    # "mejor valoradas") de pelis o series. Las cajas viejas no lo conocen y
    # contestan con la portada (path "/"): el relay lo distingue.
    hm = ev.get("home") if isinstance(ev.get("home"), dict) else None
    if hm is not None and (str(hm.get("type")) not in ("movie", "serie", "anime")
                           or str(hm.get("sel")) not in ("now", "recent", "rated")):
        return ""
    try:
        if hm is not None:
            r0 = s.get(BASE + "/", timeout=(6, 12))
            m = _CSRF.search(r0.text or "")
            if not m:
                return json.dumps({"st": r0.status_code or 0, "t": "", "h": _host(r0)})
            try:
                pg = max(1, min(5, int(hm.get("pg") or 1)))
            except (TypeError, ValueError):
                pg = 1
            r = s.post(BASE + "/api/home/posts",
                       json={"type": str(hm["type"]), "page": pg, "filterBy": "", "genres": [],
                             "networks": [], "platforms": [], "customHome": False,
                             "selection": str(hm["sel"])},
                       headers={"Accept": "application/json", "X-CSRF-TOKEN": m.group(1),
                                "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"},
                       timeout=(6, 12))
        elif q:
            r0 = s.get(BASE + "/", timeout=(6, 12))
            m = _CSRF.search(r0.text or "")
            if not m:
                return json.dumps({"st": r0.status_code or 0, "t": (r0.text or "")[:4000],
                                   "h": _host(r0)})
            try:
                pg = max(1, min(5, int(ev.get("pg") or 1)))
            except (TypeError, ValueError):
                pg = 1
            r = s.post(BASE + "/api/search/posts", json={"query": q, "page": pg},
                       headers={"Accept": "application/json", "X-CSRF-TOKEN": m.group(1),
                                "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"},
                       timeout=(6, 12))
        elif _RUTA.match(path):
            cab = {"Referer": BASE + "/"}
            if path.startswith("/t/"):
                cab["Accept"] = "application/json"
            r = s.get(BASE + path, headers=cab, timeout=(6, 15))
        else:
            return ""
        return json.dumps({"st": r.status_code, "t": (r.text or "")[:_TOPE_TXT], "h": _host(r)})
    except Exception:
        return json.dumps({"st": 0, "t": ""})
    finally:
        try:
            s.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Los servidores de vídeo
# ---------------------------------------------------------------------------
def _b(n, base):
    d = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    s = ""
    while True:
        s = d[n % base] + s
        n //= base
        if not n:
            return s


def desempaqueta(src):
    """El clásico eval(function(p,a,c,k,e,d)...) de Dean Edwards, en claro."""
    m = re.search(r"\}\('(.*)',\s*(\d+),\s*(\d+),\s*'(.*?)'\.split\('\|'\)", src or "", re.S)
    if not m:
        return ""
    p, a, c, k = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split("|")
    p = p.replace("\\'", "'")
    tab = {}
    for i in range(c):
        clave = _b(i, a)
        tab[clave] = (k[i] if i < len(k) and k[i] else clave)
    return re.sub(r"\b\w+\b", lambda w: tab.get(w.group(0), w.group(0)), p)


_M3U8 = re.compile(r"https?://[^\"'\s<>]+?\.m3u8[^\"'\s<>]*")


def _origen(url):
    u = urlparse(url)
    return "%s://%s" % (u.scheme, u.netloc)


def _con_cabeceras(url, origen, con_origin=True):
    """URL para Kodi con las cabeceras que el servidor pide (también para la
    clave AES y los trozos: ffmpeg las usa en todas las peticiones).
    Lulustream da 403 si lleva Origin: cada uno la suya."""
    cab = {"User-Agent": UA, "Referer": origen + "/"}
    if con_origin:
        cab["Origin"] = origen
    # espacios como %20 (no '+'): Kodi descodifica las cabeceras con %XX
    return url + "|" + urlencode(cab, quote_via=quote)


_LINKS = re.compile(r"var\s*links\s*=\s*(\{[^;]{10,3000}\})")


def _generico(url, s):
    """La familia Lulustream / Vidhide / Streamwish-XFS (2.9.92): la página trae
    el reproductor empaquetado (P.A.C.K.E.R) con un m3u8 o un objeto
    `links` {hls2, hls3, hls4}. Vidhide cambia de dominio en CADA enlace
    (minochinos.com, callistanise.com...): por eso se prueba con cualquiera."""
    r = s.get(url, headers={"Referer": BASE + "/"}, timeout=(6, 15))
    if r.status_code != 200:
        raise NoSale("http %s" % r.status_code)
    txt = r.text or ""
    claro = txt + " " + desempaqueta(txt)
    org = _origen(r.url)
    m = _LINKS.search(claro)
    if m:
        try:
            ls = json.loads(m.group(1))
        except ValueError:
            ls = {}
        for k in ("hls4", "hls3", "hls2"):
            v = str(ls.get(k) or "")
            if v.startswith("/"):
                v = org + v
            if v.startswith("http"):
                return _para_kodi(v, org)
    m = _M3U8.search(claro)
    if not m:
        raise NoSale("sin m3u8")
    return _para_kodi(m.group(0), org)


def _calienta(m3u8, cab):
    """Despierta el servidor ANTES de dárselo a Kodi: la lista maestra, la del
    vídeo y el principio del primer trozo. Medido el 02-11: Vidhide y Firestream
    tardan hasta 26 s la PRIMERA vez que se pide un vídeo, Kodi se rinde antes
    ("Error creating demuxer") y a la segunda va al instante. True si respondió."""
    try:
        from urllib.parse import urljoin
    except ImportError:  # pragma: no cover
        from urlparse import urljoin
    try:
        r = requests.get(m3u8, headers=cab, timeout=(6, 30))
        if r.status_code != 200 or "#EXTM3U" not in (r.text or "")[:200]:
            return False
        lineas = [l.strip() for l in r.text.splitlines() if l.strip() and not l.startswith("#")]
        if lineas and ".m3u8" in lineas[0].split("?")[0]:
            v = urljoin(m3u8, lineas[0])
            r2 = requests.get(v, headers=cab, timeout=(6, 30))
            trozos = [l.strip() for l in (r2.text or "").splitlines() if l.strip() and not l.startswith("#")]
            base = v
        else:
            trozos, base = lineas, m3u8
        if trozos:
            t = requests.get(urljoin(base, trozos[0]), headers=cab, timeout=(6, 30), stream=True)
            try:
                for _ in t.iter_content(65536):
                    break
            finally:
                t.close()
        return True
    except Exception:
        return False


def _para_kodi(m3u8, org):
    """La emisión, despierta, para Kodi: TAL CUAL si abre sin cabeceras; si no,
    con las suyas (Lulustream pide Referer)."""
    if _calienta(m3u8, {}):
        return m3u8
    _calienta(m3u8, {"User-Agent": UA, "Referer": org + "/"})
    return _con_cabeceras(m3u8, org, con_origin=False)


_lulu = _generico                         # (compatibilidad: así se llamaba)


def _firestream(url, s):
    """Firestream (2.9.92): su página trae un token-blob de un solo uso; se cambia
    por la URL firmada en /api/videos/<id>/resolve (JSON)."""
    r = s.get(url, headers={"Referer": BASE + "/"}, timeout=(6, 15))
    if r.status_code != 200:
        raise NoSale("http %s" % r.status_code)
    m = re.search(r'id="token-blob"[^>]*>([^<]+)', r.text or "")
    if not m:
        raise NoSale("sin token")
    o = _origen(r.url)
    vid = urlparse(r.url).path.rstrip("/").split("/")[-1]
    if not re.match(r"^[0-9A-Za-z_-]{4,40}$", vid):
        raise NoSale("id raro")
    j = s.post(o + "/api/videos/%s/resolve" % vid, json={"blob": m.group(1).strip()},
               headers={"Referer": r.url, "Origin": o}, timeout=(6, 15))
    if j.status_code != 200:
        raise NoSale("http %s" % j.status_code)
    su = str((j.json() or {}).get("signedVideoUrl") or "")
    if su.startswith("/"):
        su = o + su
    if not su.startswith("http"):
        raise NoSale("sin signedVideoUrl")
    _calienta(su, {"User-Agent": UA})
    return su + "|" + urlencode({"User-Agent": UA}, quote_via=quote)


def _vidara(url, s):
    o = _origen(url)
    cod = urlparse(url).path.rstrip("/").split("/")[-1]
    if not re.match(r"^[A-Za-z0-9]{6,32}$", cod):
        raise NoSale("código raro")
    r = s.post(o + "/api/stream", json={"filecode": cod, "device": "web", "codecs": {}},
               headers={"Referer": url, "Origin": o}, timeout=(6, 15))
    if r.status_code != 200:
        raise NoSale("http %s" % r.status_code)
    su = str((r.json() or {}).get("streaming_url") or "")
    if not su.startswith("http"):
        raise NoSale("sin streaming_url")
    return _con_cabeceras(su, o)


# familia de dominios -> cómo se abre (cambian de dominio a menudo: por nombre)
_FAMILIAS = (
    (re.compile(r"(^|\.)lulu[a-z]*\.[a-z]{2,6}$"), _generico),
    (re.compile(r"(^|\.)vidar+a+\.[a-z]{2,6}$"), _vidara),
    (re.compile(r"(^|\.)(firestream|frstream)\.[a-z]{2,6}$"), _firestream),
)


def familia(url):
    host = (urlparse(url).hostname or "").lower()
    for rx, fn in _FAMILIAS:
        if rx.search(host):
            return fn
    return None


def resolver(url):
    """La emisión directa (con sus cabeceras) de la página de un servidor.
    Lanza NoSale si no se puede. Prueba lo nuestro y, si no, ResolveURL."""
    if not str(url or "").startswith("https://"):
        raise NoSale("enlace raro")
    fn = familia(url)
    if fn is not None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        try:
            return fn(url, s)
        except NoSale:
            raise
        except requests.RequestException as e:
            raise NoSale("sin conexión (%s)" % type(e).__name__)
        except Exception as e:
            raise NoSale(type(e).__name__)
        finally:
            s.close()
    # (2.9.92) ninguna familia conocida: la del empaquetado es la más común
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    try:
        return _generico(url, s)
    except Exception:
        pass
    finally:
        s.close()
    try:
        import resolveurl  # noqa: F401  (script.module.resolveurl, si está)
        hm = resolveurl.HostedMediaFile(url=url)
        if hm:
            r = hm.resolve()
            if r:
                return r
    except ImportError:
        raise NoSale("servidor no soportado")
    except Exception as e:
        raise NoSale("ResolveURL: %s" % type(e).__name__)
    raise NoSale("servidor no soportado")
