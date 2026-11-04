# -*- coding: utf-8 -*-
"""Acelerador del streaming (2.9.97; 2.9.98 sin Range).

Medido el 03-11 (The Gentlemen 1x03, Cinubo): los servidores de vídeo limitan
la velocidad POR CONEXIÓN. Vidara daba 1,6 Mbit/s por conexión y 9,2 con 4 a
la vez; Lulustream 4,2 y 27,5. Kodi baja los trozos del HLS de uno en uno por
una sola conexión: con un vídeo de 3 Mbit/s se paraba cada minuto en TODOS los
servidores aunque la casa tenga 136 Mbit/s.

Esto es un pequeño servidor en 127.0.0.1 que vive en el servicio de la caja.
Kodi le pide la lista y los trozos a él; él baja por delante varios trozos a la
vez (cada uno entero: nunca con Range, ver _baja) y se los va dando a Kodi
según llegan. Si algo falla al montarlo, Kodi va directo, como antes: nunca
es peor que no tenerlo.
"""
import base64
import json
import os
import re
import threading
import time

try:
    from urllib.parse import urljoin, urlencode, parse_qsl, urlparse, quote
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
except ImportError:  # pragma: no cover
    raise

import requests

HILOS = 4                    # descargas a la vez (medido: 4 bastan para x3-x6)
DELANTE_MAX = 12             # trozos por delante (unos 2 min)
BYTES_MAX = 64 * 1024 * 1024 # tope de memoria por delante (cajas de 1-2 GB)
ESPERA_KODI = 45.0           # sin un byte en este tiempo -> se corta (Kodi salta)
REINTENTOS = 4
LIBRE = 180.0                # sin que Kodi pida nada: se suelta la memoria


def _log(msg):
    try:
        import xbmc
        xbmc.log("[MejorWolf/turbo] " + msg, xbmc.LOGINFO)
    except Exception:  # fuera de Kodi (pruebas)
        pass


# ---------------------------------------------------------------- la sesión

class _Trozo(object):
    """Un trozo del vídeo: sus partes se rellenan en orden de petición."""
    __slots__ = ("url", "total", "rango", "partes", "hechas", "fallo",
                 "pedido", "listo")

    def __init__(self, url):
        self.url = url
        self.total = None      # bytes (si se sabe)
        self.rango = None      # None = sin probar; True/False
        self.partes = []       # bytearray por parte
        self.hechas = []       # bool por parte
        self.fallo = False
        self.pedido = False    # ya hay alguien con él (la parte 0)
        self.listo = False

    def bytes_tiene(self):
        return sum(len(p) for p in self.partes)


class Sesion(object):
    def __init__(self, sid, cab, lista, trozos, claves, durs=None):
        self.sid = sid
        self.durs = durs or [10.0] * len(trozos)
        self.cab = cab
        self.lista = lista           # texto m3u8 ya reescrito
        self.trozos = [_Trozo(u) for u in trozos]
        self.claves = claves         # [url] (AES / MAP), se sirven bajo demanda
        self.clave_cache = {}
        self.pos = 0
        self.cv = threading.Condition()
        self.vivo = True
        self.t0 = time.time()
        self.bajado = 0
        self.esperas = 0             # veces que Kodi tuvo que esperar
        self.danados = 0             # trozos que el servidor solo da a medias
        self.ultimo = time.time()    # última petición de Kodi
        self.http = requests.Session()
        self.http.headers.update(cab)

    # ---- qué bajar ahora: lo primero sin pedir dentro de la ventana
    def siguiente_tarea(self):
        """(trozo_idx, parte_idx) o None. Llamar con self.cv cogido."""
        delante = 0
        for i in range(self.pos, min(len(self.trozos), self.pos + DELANTE_MAX)):
            t = self.trozos[i]
            if t.listo or t.fallo:
                delante += t.bytes_tiene()
                if delante > BYTES_MAX:
                    return None
                continue
            if not t.pedido:
                t.pedido = True
                return (i, 0)
            if t.rango:
                for j in range(1, len(t.partes)):
                    if t.hechas[j] is None:      # sin pedir
                        t.hechas[j] = False      # pedida
                        return (i, j)
            delante += t.bytes_tiene()
            if delante > BYTES_MAX:
                return None
        return None

    def colchon(self):
        """Segundos de vídeo ya bajados seguidos desde donde va Kodi."""
        seg = 0.0
        for i in range(self.pos, len(self.trozos)):
            if not self.trozos[i].listo:
                break
            seg += self.durs[i]
        return seg

    def poda(self):
        """Suelta la memoria de lo ya visto y de lo que quedó lejos (salto)."""
        # También las descargas a medias: tras un salto, los obreros ocupados
        # con trozos que ya no se van a ver retrasaban el que Kodi espera (17 s
        # medidos el 03-11 y Kodi se descolocaba). Al cambiar el objeto, el
        # obrero lo nota en el siguiente bloque y lo deja.
        for i, t in enumerate(self.trozos):
            if (i < self.pos - 1 or i >= self.pos + DELANTE_MAX + 2) and (t.partes or t.pedido):
                self.trozos[i] = _Trozo(t.url)

    def cierra(self):
        with self.cv:
            self.vivo = False
            self.cv.notify_all()
        try:
            self.http.close()
        except Exception:
            pass


_S = {"ses": None, "cv": threading.Condition(), "srv": None, "port": 0, "k": ""}


def _baja(ses, i, j):
    """Baja el trozo i entero, SIN Range (j siempre 0).

    04-11: el Vidara de otra máquina (nginx 97bf1.com) guarda en caché la
    PRIMERA respuesta de cada trozo, con Range incluido, y luego la da a todos
    sea cual sea la petición: al pedir partes de 1 MB, el trozo quedaba
    envenenado con su primer MB y Kodi repetía unos segundos sin avanzar. Así
    que nunca se pide un pedazo: la velocidad sale de bajar 4 trozos a la vez.
    """
    t = ses.trozos[i]
    for vez in range(REINTENTOS):
        if not ses.vivo or ses.trozos[i] is not t:
            return
        try:
            r = ses.http.get(t.url, timeout=(6, 12), stream=True)
            if r.status_code not in (200, 206):
                r.close()
                raise IOError("http %s" % r.status_code)
            total = r.headers.get("Content-Length")
            total = int(total) if total and str(total).isdigit() else None
            cr = r.headers.get("Content-Range") or ""
            m = re.match(r"bytes\s+(\d+)-(\d+)/(\d+)", cr)
            if m and (int(m.group(1)) != 0 or int(m.group(2)) < int(m.group(3)) - 1):
                # el servidor solo da un pedazo (caché envenenada por otro):
                # se entrega lo que hay, sin inventar ni repetir nada
                _log("trozo %d: el servidor solo da %s" % (i, cr))
                ses.danados += 1
                if ses.danados == 1:
                    _avisa_danado()
                if int(m.group(1)) != 0:
                    r.close()
                    raise IOError("pedazo que no empieza en 0")
            with ses.cv:
                if not t.partes:
                    t.rango = False
                    t.partes = [bytearray()]
                    t.hechas = [False]
                if t.total is None:
                    t.total = total
                ses.cv.notify_all()
            # es un reintento: lo ya guardado se salta (y si la longitud no
            # cuadra con la del primer intento, se empieza de cero)
            saltar = len(t.partes[0])
            if saltar and total is not None and t.total is not None and total != t.total:
                r.close()
                raise IOError("cambió de tamaño")
            try:
                for c in r.iter_content(65536):
                    if not ses.vivo or ses.trozos[i] is not t:
                        return
                    if saltar:
                        if len(c) <= saltar:
                            saltar -= len(c)
                            continue
                        c = c[saltar:]
                        saltar = 0
                    with ses.cv:
                        t.partes[0].extend(c)
                        ses.bajado += len(c)
                        ses.cv.notify_all()
            finally:
                r.close()
            if t.total and len(t.partes[0]) < t.total:
                raise IOError("corto")
            with ses.cv:
                t.hechas[0] = True
                t.listo = True
                ses.cv.notify_all()
            return
        except Exception as e:
            if vez == REINTENTOS - 1:
                _log("trozo %d: %s" % (i, type(e).__name__))
            time.sleep(0.8 * (vez + 1))
    with ses.cv:
        if ses.trozos[i] is t:
            t.fallo = True
        ses.cv.notify_all()


def _avisa_danado():
    """Una vez por vídeo: que la persona sepa por qué salta y qué hacer."""
    try:
        import xbmcgui
        xbmcgui.Dialog().notification(
            "MejorWolf", "Este servidor tiene partes dañadas y dará saltos: mejor elige Lulustream",
            xbmcgui.NOTIFICATION_WARNING, 12000)
    except Exception:  # fuera de Kodi (pruebas)
        pass


def _obrero():
    while True:
        with _S["cv"]:
            ses = _S["ses"]
            tarea = None
            if ses is not None and ses.vivo and time.time() - ses.ultimo > LIBRE:
                _resumen(ses)
                ses.cierra()
                _S["ses"] = None
                continue
            if ses is not None and ses.vivo:
                with ses.cv:
                    tarea = ses.siguiente_tarea()
            if tarea is None:
                _S["cv"].wait(2.0)
                continue
        try:
            _baja(ses, tarea[0], tarea[1])
        except Exception as e:  # nunca se muere un obrero
            _log("obrero: %s" % type(e).__name__)
        with _S["cv"]:
            _S["cv"].notify_all()


def _despierta():
    with _S["cv"]:
        _S["cv"].notify_all()


# --------------------------------------------------------- montar la lista

def _texto(http, url):
    r = http.get(url, timeout=(6, 15))
    if r.status_code != 200 or "#EXTM3U" not in (r.text or "")[:300]:
        raise IOError("lista http %s" % r.status_code)
    return r.text, r.url


def _bw(linea):
    m = re.search(r"BANDWIDTH=(\d+)", linea)
    return int(m.group(1)) if m else 0


def monta(url, cab):
    """Sesión nueva para esta emisión. Devuelve la URL local o lanza."""
    http = requests.Session()
    http.headers.update(cab)
    try:
        txt, real = _texto(http, url)
        if "#EXT-X-STREAM-INF" in txt:
            if re.search(r"#EXT-X-MEDIA:[^\n]*URI=", txt):
                raise ValueError("audio aparte")      # no merece el riesgo
            mejor, bw, sig = None, -1, False
            for l in txt.splitlines():
                l = l.strip()
                if l.startswith("#EXT-X-STREAM-INF"):
                    sig, b = True, _bw(l)
                elif sig and l and not l.startswith("#"):
                    if b > bw:
                        mejor, bw = urljoin(real, l), b
                    sig = False
            if not mejor:
                raise ValueError("maestra vacía")
            txt, real = _texto(http, mejor)
        if "#EXT-X-ENDLIST" not in txt:
            raise ValueError("en directo")
        if "#EXT-X-BYTERANGE" in txt:
            raise ValueError("byterange")
    finally:
        http.close()
    sid = base64.urlsafe_b64encode(os.urandom(6)).decode().rstrip("=")
    ext = "m4s" if "#EXT-X-MAP" in txt else "ts"
    trozos, claves, out, durs = [], [], [], []
    dur = 10.0
    def _uri(m):
        claves.append(urljoin(real, m.group(1)))
        return 'URI="/%s/k%d.key"' % (sid, len(claves) - 1)
    for l in txt.splitlines():
        s = l.strip()
        if not s:
            continue
        if s.startswith("#"):
            if s.startswith("#EXT-X-KEY") or s.startswith("#EXT-X-MAP"):
                s = re.sub(r'URI="([^"]+)"', _uri, s)
            if s.startswith("#EXTINF:"):
                try:
                    dur = float(s[8:].split(",")[0])
                except ValueError:
                    dur = 10.0
            out.append(s)
        else:
            trozos.append(urljoin(real, s))
            durs.append(dur)
            out.append("/%s/%d.%s" % (sid, len(trozos) - 1, ext))
    if not trozos:
        raise ValueError("sin trozos")
    ses = Sesion(sid, cab, "\n".join(out) + "\n", trozos, claves, durs)
    with _S["cv"]:
        viejo = _S["ses"]
        _S["ses"] = ses
        _S["cv"].notify_all()
    if viejo is not None:
        _resumen(viejo)
        viejo.cierra()
    _log("sesión %s: %d trozos" % (sid, len(trozos)))
    return "http://127.0.0.1:%d/%s/index.m3u8" % (_S["port"], sid)


def _resumen(ses):
    dt = max(1.0, time.time() - ses.t0)
    _log("fin sesión %s: %.0f MB en %.0f s (%.1f Mbit/s), Kodi esperó %d veces"
         % (ses.sid, ses.bajado / 1e6, dt, ses.bajado * 8 / 1e6 / dt, ses.esperas))


# ------------------------------------------------------------ el servidor

class _Hilos(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _envia(self, cod, cuerpo=b"", tipo="text/plain"):
        self.send_response(cod)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        if cuerpo and self.command != "HEAD":
            self.wfile.write(cuerpo)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            _log("petición: %s" % type(e).__name__)
            try:
                self._envia(500)
            except Exception:
                pass

    def _get(self):
        p = urlparse(self.path)
        if p.path == "/ping":
            return self._envia(200, b"ok")
        if p.path == "/monta":
            q = dict(parse_qsl(p.query))
            if q.get("k") != _S["k"]:
                return self._envia(403)
            try:
                d = json.loads(base64.urlsafe_b64decode(q.get("d", "").encode()).decode())
                u = monta(str(d["u"]), dict(d.get("h") or {}))
                out = {"ok": True, "u": u}
            except Exception as e:
                out = {"ok": False, "e": "%s: %s" % (type(e).__name__, str(e)[:80])}
            return self._envia(200, json.dumps(out).encode(), "application/json")
        if p.path == "/colchon":
            ses = _S["ses"]
            c = 0.0
            if ses is not None and ses.vivo:
                with ses.cv:
                    c = ses.colchon()
            return self._envia(200, json.dumps({"s": c}).encode(), "application/json")
        m = re.match(r"^/([A-Za-z0-9_-]{4,16})/(index\.m3u8|k(\d+)\.key|(\d+)\.(?:ts|m4s))$", p.path)
        ses = _S["ses"]
        if not m or ses is None or ses.sid != m.group(1) or not ses.vivo:
            return self._envia(404)
        if m.group(2) == "index.m3u8":
            return self._envia(200, ses.lista.encode(), "application/vnd.apple.mpegurl")
        if m.group(3) is not None:
            return self._clave(ses, int(m.group(3)))
        return self._trozo(ses, int(m.group(4)))

    def _clave(self, ses, k):
        if k >= len(ses.claves):
            return self._envia(404)
        b = ses.clave_cache.get(k)
        if b is None:
            r = ses.http.get(ses.claves[k], timeout=(6, 15))
            if r.status_code != 200:
                return self._envia(502)
            b = ses.clave_cache[k] = r.content
        return self._envia(200, b, "application/octet-stream")

    def _trozo(self, ses, i):
        if i >= len(ses.trozos):
            return self._envia(404)
        ses.ultimo = time.time()
        if i not in (ses.pos, ses.pos + 1):
            _log("salto %d -> %d" % (ses.pos, i))
        with ses.cv:
            if i != ses.pos:
                ses.pos = i
                ses.poda()
            t = ses.trozos[i]
        _despierta()
        # espera a saber el tamaño (o a que falle)
        t0 = time.time()
        esperado = False
        with ses.cv:
            while ses.vivo and not t.partes and not t.fallo and time.time() - t0 < ESPERA_KODI:
                esperado = True
                ses.cv.wait(1.0)
            if t.fallo or not t.partes:
                return self._envia(502)
        if self.command == "HEAD":
            return self._envia(200, b"", "video/mp2t")
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4" if self.path.endswith(".m4s") else "video/mp2t")
        if t.total:
            self.send_header("Content-Length", str(t.total))
        self.end_headers()
        # va dando lo que llega, parte a parte, en orden
        for j in range(len(t.partes)):
            hecho = 0
            while True:
                with ses.cv:
                    ult = time.time()
                    while (ses.vivo and len(t.partes[j]) <= hecho and t.hechas[j] is not True
                           and not t.fallo and ses.trozos[i] is t):
                        ses.ultimo = time.time()
                        esperado = True
                        ses.cv.wait(1.0)
                        if time.time() - ult > ESPERA_KODI:
                            return
                    trozo = bytes(t.partes[j][hecho:])
                    acabado = t.hechas[j] is True
                    fallo = t.fallo or ses.trozos[i] is not t
                if trozo:
                    self.wfile.write(trozo)
                    hecho += len(trozo)
                if acabado and hecho >= len(t.partes[j]):
                    break
                if fallo or not ses.vivo:
                    return
        if esperado:
            ses.esperas += 1


def arranca():
    """En el servicio: el servidor y los obreros. Devuelve el puerto o 0."""
    if _S["srv"] is not None:
        return _S["port"]
    try:
        srv = _Hilos(("127.0.0.1", 0), _Manejador)
    except Exception as e:
        _log("no arranca: %s" % e)
        return 0
    _S["srv"] = srv
    _S["port"] = srv.server_address[1]
    _S["k"] = base64.urlsafe_b64encode(os.urandom(9)).decode()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(HILOS):
        threading.Thread(target=_obrero, daemon=True).start()
    _log("escuchando en 127.0.0.1:%d" % _S["port"])
    return _S["port"]


def para():
    ses = _S["ses"]
    if ses is not None:
        _resumen(ses)
        ses.cierra()
    srv = _S["srv"]
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass
    _S["srv"] = None


# ------------------------------------------------- lado del plugin (cb_play)

def _ficha():
    try:
        import xbmcvfs
        return xbmcvfs.translatePath("special://temp/mejorwolf_turbo.json")
    except Exception:
        return os.path.join(os.environ.get("TEMP", "/tmp"), "mejorwolf_turbo.json")


def anuncia():
    """El servicio deja dicho dónde escucha (puerto y llave)."""
    try:
        with open(_ficha(), "w") as f:
            json.dump({"port": _S["port"], "k": _S["k"], "ts": time.time()}, f)
    except Exception:
        pass


COLCHON = 25.0               # antes de dárselo a Kodi: unos 25 s ya bajados
COLCHON_MAX = 30.0           # ... pero sin esperar más de esto


def envuelve(url, aviso=None):
    """Desde cb_play: la emisión (con |cabeceras) -> la URL local del acelerador.
    Si no está o no puede, la misma URL: Kodi va directo, como antes.
    Espera un colchón: con servidores sin Range (Vidara) el primer trozo llega
    más lento de lo que se ve; mejor esperar al principio que cortar a los 10 s."""
    try:
        with open(_ficha()) as f:
            d = json.load(f)
        port, k = int(d.get("port") or 0), str(d.get("k") or "")
        if not port:
            return url
        u, _, h = url.partition("|")
        if ".m3u8" not in u.split("?")[0]:
            return url
        cab = dict(parse_qsl(h)) if h else {}
        cab.setdefault("User-Agent", "Mozilla/5.0")
        dd = base64.urlsafe_b64encode(json.dumps({"u": u, "h": cab}).encode()).decode()
        r = requests.get("http://127.0.0.1:%d/monta" % port,
                         params={"k": k, "d": dd}, timeout=(2, 20))
        j = r.json()
        if j.get("ok") and str(j.get("u") or "").startswith("http://127.0.0.1:"):
            t0 = time.time()
            while time.time() - t0 < COLCHON_MAX:
                try:
                    c = float(requests.get("http://127.0.0.1:%d/colchon" % port,
                                           timeout=(1, 3)).json().get("s") or 0)
                except Exception:
                    break
                if c >= COLCHON:
                    break
                if aviso:
                    try:
                        aviso(min(99, int(100 * c / COLCHON)))
                    except Exception:
                        pass
                time.sleep(0.5)
            return j["u"]
        _log("directo: %s" % j.get("e"))
    except Exception as e:
        _log("directo: %s" % type(e).__name__)
    return url
