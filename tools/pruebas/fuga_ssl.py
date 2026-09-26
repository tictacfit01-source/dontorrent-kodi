# -*- coding: utf-8 -*-
"""La fuga de memoria de los cloudscraper sin cerrar (dtbl47).

24-09-2026: cada worker del relay perdia ~36 MB/h de memoria en C y el
servicio se relevaba cada ~3 h. /catmem?quien= lo encontro: adaptadores de
cloudscraper vivos sin su sesion, con su PoolManager, su pool, una conexion
abierta y su SSLContext (~0,9 MB de certificados en C), en un ciclo que el gc
no puede recoger:
    SSLContext -> wrap_socket (metodo del adaptador) -> adaptador -> PoolManager
    -> pool -> conexion keep-alive -> socket SSL -> objeto SSL de C -> SSLContext
La ultima flecha el gc no la ve. Solo pasa si la sesion se suelta SIN cerrar y
con una conexion abierta en el pool.

Esto vigila:
  1) que la fuga EXISTE tal cual (con un cloudscraper pelado, sin nuestro
     arreglo): si un dia deja de reproducirse, esta prueba lo dira y habra que
     revisar si el arreglo sigue haciendo falta;
  2) que con _make_scraper() soltado SIN cerrar no queda nada (el finalizador);
  3) que _dx_get y _dx_probe cierran su cloudscraper siempre;
  4) que el dominio de DivxTotal no se busca en bucle si no sale (dtbl48:
     4, 8, 16 y 30 min como mucho; al funcionar, ritmo normal).
Sin red: un servidor HTTPS en 127.0.0.1 con un certificado de usar y tirar
(openssl, en una carpeta temporal que se borra; nunca va al repo). Sin
openssl, las partes 1 y 2 se saltan (y se dice).
"""
import gc
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402
import cloudscraper                                      # noqa: E402

warnings.filterwarnings("ignore")
fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


def vivos(tipo):
    gc.collect()
    gc.collect()
    return sum(1 for o in gc.get_objects() if type(o).__name__ == tipo)


def busca_openssl():
    for c in (shutil.which("openssl"),
              r"C:\Program Files\Git\mingw64\bin\openssl.exe",
              r"C:\Program Files\Git\usr\bin\openssl.exe"):
        if c and os.path.exists(c):
            return c
    return None


class Hola(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive: la conexion se queda en el pool

    def do_GET(self):
        cuerpo = b"hola"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


print("\n=== 1-2) La fuga, con HTTPS de verdad en local ===")
ossl = busca_openssl()
tmp = tempfile.mkdtemp(prefix="mw_fuga_ssl_")
srv = None
try:
    if not ossl:
        print("  SALTADO: no hay openssl para el certificado de prueba")
    else:
        crt, key = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
        subprocess.run([ossl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", key, "-out", crt, "-days", "1",
                        "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                       check=True, capture_output=True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Hola)
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(crt, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "https://localhost:%d/" % srv.server_address[1]

        base = vivos("CipherSuiteAdapter")
        for _ in range(3):                  # 1) cloudscraper PELADO, sin cerrar
            s = cloudscraper.create_scraper()
            r = s.get(url, verify=crt, timeout=10)
            assert r.text == "hola", r.text
            del s, r
        n = vivos("CipherSuiteAdapter") - base
        comprueba("la fuga existe: 3 cloudscraper sin cerrar -> %d adaptadores que el gc "
                  "no puede recoger" % n, n == 3, n)

        base = vivos("CipherSuiteAdapter")
        base_ctx = vivos("SSLContext")
        for _ in range(3):                  # 2) el nuestro, soltado sin cerrar
            s = A._make_scraper()
            r = s.get(url, verify=crt, timeout=10)
            assert r.text == "hola", r.text
            del s, r
        n = vivos("CipherSuiteAdapter") - base
        nc = vivos("SSLContext") - base_ctx
        comprueba("con _make_scraper() soltado SIN cerrar no queda nada (%d adaptadores, "
                  "%d SSLContext)" % (n, nc), n == 0 and nc == 0, (n, nc))

        s = A._make_scraper()               # y mientras vive, funciona igual
        r1 = s.get(url, verify=crt, timeout=10)
        r2 = s.get(url, verify=crt, timeout=10)
        comprueba("mientras la sesion vive, reutiliza su conexion como siempre",
                  r1.text == r2.text == "hola", (r1.text, r2.text))
        s.close()
        del s, r1, r2
finally:
    if srv is not None:
        srv.shutdown()
        srv.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 3) _dx_get y _dx_probe cierran su cloudscraper ===")


class Falso(object):
    def __init__(self, html="", falla=False):
        self.cerrado = 0
        self.html = html
        self.falla = falla

    def get(self, url, **k):
        if self.falla:
            raise RuntimeError("tarpit")
        r = type("R", (), {})()
        r.status_code, r.text, r.url = 200, self.html, url
        return r

    def close(self):
        self.cerrado += 1


CREADOS = []
reales = (A._make_scraper, A._get_con_tope, A.requests.get)
try:
    def crea(**k):
        f = Falso(**k)
        CREADOS.append(f)
        return f
    A._make_scraper = lambda: crea(html="<a href='/peliculas/x'>x</a>")
    # el camino plano devuelve el reto -> _dx_get pasa a cloudscraper
    A._get_con_tope = lambda url, tope, headers=None, scraper=None, **k: (
        ("<html>ok</html>", 200) if scraper is not None
        else ("<html>Just a moment...</html>", 403))
    t = A._dx_get("https://divxtotal.prueba/x", tope_s=12.0)
    comprueba("_dx_get: con reto usa cloudscraper y lo cierra",
              t == "<html>ok</html>" and len(CREADOS) == 1 and CREADOS[0].cerrado == 1,
              (t, [c.cerrado for c in CREADOS]))
    del CREADOS[:]
    A._get_con_tope = lambda url, tope, headers=None, scraper=None, **k: (
        _ for _ in ()).throw(RuntimeError("tarpit")) if scraper is not None \
        else ("<html>Just a moment...</html>", 403)
    t = A._dx_get("https://divxtotal.prueba/x", tope_s=12.0)
    comprueba("_dx_get: y tambien si revienta", t is None and CREADOS
              and CREADOS[0].cerrado == 1, [c.cerrado for c in CREADOS])
    del CREADOS[:]
    A.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("plano"))
    A._dx_probe("divxtotal.prueba")
    comprueba("_dx_probe: el cloudscraper del 2o intento se cierra",
              len(CREADOS) == 1 and CREADOS[0].cerrado == 1, [c.cerrado for c in CREADOS])
    del CREADOS[:]
    A._make_scraper = lambda: crea(falla=True)
    A._dx_probe("divxtotal.prueba")
    comprueba("_dx_probe: y tambien si revienta",
              len(CREADOS) == 1 and CREADOS[0].cerrado == 1, [c.cerrado for c in CREADOS])
finally:
    A._make_scraper, A._get_con_tope, A.requests.get = reales

print("\n=== 4) El dominio de DivxTotal no se busca en bucle (dtbl48) ===")
PROBES = []
GUARDADO = [None]
reales = (A._dx_probe, A._dx_load_domain, A._dx_save_domain, dict(A._DX_DESC),
          dict(A._DX_DOM_CACHE))
try:
    A._dx_probe = lambda d: PROBES.append(d) or None
    A._dx_load_domain = lambda: GUARDADO[0]
    A._dx_save_domain = lambda h: GUARDADO.__setitem__(0, h)
    A._DX_DESC.update(fallos=0, proxima=0.0)
    A._DX_DOM_CACHE.update(dom=None, ts=0.0)
    ahora = time.time()
    A._dx_descubre()
    n1 = len(PROBES)
    comprueba("sin exito: prueba los %d dominios una vez y espera 4 min" % n1,
              n1 == len(A._DX_DOMAINS) and A._DX_DESC["fallos"] == 1
              and 230 < A._DX_DESC["proxima"] - ahora < 250, A._DX_DESC)
    A._dx_descubre()
    comprueba("...y antes de esos 4 min no vuelve a probar nada", len(PROBES) == n1, len(PROBES))
    A._DX_DESC["proxima"] = 0.0
    A._dx_descubre()
    comprueba("el 2o fallo espera 8 min", A._DX_DESC["fallos"] == 2
              and 470 < A._DX_DESC["proxima"] - time.time() < 490, A._DX_DESC)
    A._DX_DESC.update(fallos=9, proxima=0.0)
    A._dx_descubre()
    comprueba("y nunca mas de 30 min", 1790 < A._DX_DESC["proxima"] - time.time() <= 1800,
              A._DX_DESC)
    A._DX_DESC["proxima"] = 0.0
    A._dx_probe = lambda d: "divxtotal.nuevo"
    A._dx_descubre()
    comprueba("en cuanto uno funciona: se guarda y vuelve el ritmo normal",
              GUARDADO[0] == "divxtotal.nuevo" and A._DX_DESC["fallos"] == 0
              and A._DX_DESC["proxima"] == 0.0, (GUARDADO, A._DX_DESC))
finally:
    A._dx_probe, A._dx_load_domain, A._dx_save_domain = reales[:3]
    A._DX_DESC.clear()
    A._DX_DESC.update(reales[3])
    A._DX_DOM_CACHE.clear()
    A._DX_DOM_CACHE.update(reales[4])

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: un cloudscraper soltado ya no deja memoria colgada")
