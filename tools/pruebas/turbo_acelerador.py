# -*- coding: utf-8 -*-
"""Prueba del acelerador del streaming (caja 2.9.97): resources/lib/turbo.py.

Un servidor falso hace de Vidara (ignora Range, trozos disfrazados de .css) y
de Lulustream (admite Range, AES con clave). Se comprueba que Kodi recibe los
mismos bytes, en orden, que se baja por delante en paralelo, que aguanta cortes
a mitad de un trozo y saltos, y que si algo no cuadra se va directo.

    py -3.13 tools/pruebas/turbo_acelerador.py
"""
import os
import sys
import json
import time
import threading
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf"))
os.environ.setdefault("TEMP", AQUI)
from resources.lib import turbo  # noqa: E402

N = 12
TAM = 300000
DATOS = [hashlib.sha256(b"t%d" % i).digest() * (TAM // 32) for i in range(N)]
ESTADO = {"a_la_vez": 0, "max": 0, "corta": set(), "peticiones": 0, "lento": 0.0, "rangos": 0}
CACHE = {}   # como el nginx de 97bf1.com: la primera respuesta de cada trozo, para todos
CERROJO = threading.Lock()


class Falso(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/v/master.m3u8":
            return self._txt("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000\nbaja.m3u8\n"
                             "#EXT-X-STREAM-INF:BANDWIDTH=3000000\nalta.m3u8\n")
        if p in ("/v/alta.m3u8", "/l/index.m3u8", "/d/live.m3u8", "/c/index.m3u8"):
            ext = "css" if p.startswith("/v/") else "ts"
            l = ["#EXTM3U", "#EXT-X-TARGETDURATION:10"]
            if p.startswith("/l/"):
                l.append('#EXT-X-KEY:METHOD=AES-128,URI="https://x/clave?t=1"'.replace("https://x", "http://127.0.0.1:%d/l" % self.server.server_address[1]))
            for i in range(N):
                l += ["#EXTINF:10.0,", "seg_%03d.%s?tok=1" % (i, ext)]
            if not p.startswith("/d/"):
                l.append("#EXT-X-ENDLIST")
            return self._txt("\n".join(l) + "\n")
        if p == "/v/baja.m3u8":
            return self._txt("#EXTM3U\n#EXTINF:10,\nx.ts\n#EXT-X-ENDLIST\n")
        if p == "/l/clave":
            return self._bin(b"K" * 16, 200)
        if "/seg_" in p:
            i = int(p.split("seg_")[1][:3])
            rango = p.startswith("/l/") or p.startswith("/c/")
            b = DATOS[i]
            if self.headers.get("Range"):
                ESTADO["rangos"] += 1
            with CERROJO:
                ESTADO["peticiones"] += 1
                ESTADO["a_la_vez"] += 1
                ESTADO["max"] = max(ESTADO["max"], ESTADO["a_la_vez"])
            try:
                rg = self.headers.get("Range")
                if p.startswith("/c/"):
                    rg = CACHE.setdefault(p, rg or "")   # envenenada si la 1a llevaba Range
                if rango and rg:
                    a, _, z = rg.split("=")[1].partition("-")
                    a, z = int(a), min(int(z or len(b) - 1), len(b) - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (a, z, len(b)))
                    cuerpo = b[a:z + 1]
                else:
                    # como Vidara: 206 con el trozo entero aunque se pida Range
                    self.send_response(206 if rg else 200)
                    if rg:
                        self.send_header("Content-Range", "bytes 0-%d/%d" % (len(b) - 1, len(b)))
                    cuerpo = b
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Content-Type", "text/css")
                self.end_headers()
                corta = i in ESTADO["corta"]
                if corta:
                    ESTADO["corta"].discard(i)      # solo la primera vez
                for k in range(0, len(cuerpo), 16384):
                    if corta and k > len(cuerpo) // 2:
                        return                       # corte a mitad
                    if ESTADO["lento"]:
                        time.sleep(ESTADO["lento"])
                    try:
                        self.wfile.write(cuerpo[k:k + 16384])
                    except (ConnectionError, OSError):
                        return                       # el acelerador cerró (al parar)
            finally:
                with CERROJO:
                    ESTADO["a_la_vez"] -= 1
            return
        self.send_response(404)
        self.end_headers()

    def _txt(self, s):
        return self._bin(s.encode(), 200, "application/vnd.apple.mpegurl")

    def _bin(self, b, cod, tipo="application/octet-stream"):
        self.send_response(cod)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class _H(ThreadingMixIn, HTTPServer):
    daemon_threads = True


FALLOS = []


def ok(cond, que):
    print(("  OK   " if cond else "  FALLO ") + que)
    if not cond:
        FALLOS.append(que)


def main():
    import requests
    srv = _H(("127.0.0.1", 0), Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    port = turbo.arranca()
    ok(port > 0, "arranca en 127.0.0.1")
    turbo.anuncia()

    print("1. Como Vidara (maestra, sin Range, .css)")
    ESTADO["lento"] = 0.002
    u = turbo.envuelve(base + "/v/master.m3u8|User-Agent=X&Referer=" + base + "/")
    ok(u.startswith("http://127.0.0.1:%d/" % port), "envuelve da la URL local")
    lista = requests.get(u).text
    ok(lista.count(".ts") == N and "seg_" not in lista, "lista reescrita: %d trozos locales" % N)
    ok("baja" not in lista, "elige la variante de más calidad")
    ok(turbo._S["ses"].colchon() >= 20, "espera colchón antes de dárselo a Kodi")
    raiz = u.rsplit("/", 1)[0]
    bien = True
    for i in range(N):
        if requests.get("%s/%d.ts" % (raiz, i), timeout=30).content != DATOS[i]:
            bien = False
    ok(bien, "Kodi recibe los mismos bytes, en orden")
    ok(ESTADO["max"] >= 3, "baja varios a la vez (máx %d)" % ESTADO["max"])

    print("2. Como Lulustream (Range + clave AES) y un corte a mitad")
    ESTADO.update(max=0, lento=0.0)
    ESTADO["corta"] = {2, 5}
    u = turbo.envuelve(base + "/l/index.m3u8")
    lista = requests.get(u).text
    ok('URI="/' in lista and "k0.key" in lista, "la clave AES también va por el acelerador")
    raiz = u.rsplit("/", 1)[0]
    ok(requests.get(raiz + "/k0.key").content == b"K" * 16, "la clave llega bien")
    bien = all(requests.get("%s/%d.ts" % (raiz, i), timeout=30).content == DATOS[i] for i in range(N))
    ok(bien, "los trozos cortados a mitad se reanudan y llegan enteros")
    ok(ESTADO["max"] >= 3, "trozos en paralelo (máx %d)" % ESTADO["max"])

    print("2b. Caché que guarda la primera respuesta (04-11, Vidara de 97bf1.com)")
    ESTADO["rangos"] = 0
    u = turbo.envuelve(base + "/c/index.m3u8")
    raiz = u.rsplit("/", 1)[0]
    bien = all(requests.get("%s/%d.ts" % (raiz, i), timeout=30).content == DATOS[i] for i in range(N))
    ok(ESTADO["rangos"] == 0, "nunca pide un pedazo (Range): %d veces" % ESTADO["rangos"])
    ok(bien, "cada trozo llega entero y sin repetir su principio")
    CACHE["/c/seg_003.ts"] = "bytes=0-99999"     # envenenada por otro
    u = turbo.envuelve(base + "/c/index.m3u8")
    raiz = u.rsplit("/", 1)[0]
    b3 = requests.get(raiz + "/3.ts", timeout=30).content
    ok(b3 == DATOS[3][:100000], "si el servidor solo da un pedazo, se entrega ese, sin repetirlo (%d bytes)" % len(b3))
    ok(requests.get(raiz + "/4.ts", timeout=30).content == DATOS[4], "y el siguiente sigue bien")

    print("3. Vidara cortado a mitad (sin Range: se salta lo ya guardado)")
    ESTADO["corta"] = {7}
    u = turbo.envuelve(base + "/v/alta.m3u8")
    raiz = u.rsplit("/", 1)[0]
    ok(requests.get(raiz + "/7.ts", timeout=30).content == DATOS[7], "trozo 7 entero tras el corte")

    print("4. Salto (Kodi pide el 10 de repente) y luego atrás")
    ok(requests.get(raiz + "/10.ts", timeout=30).content == DATOS[10], "salto adelante")
    ok(requests.get(raiz + "/1.ts", timeout=30).content == DATOS[1], "salto atrás")

    print("4b. Tras un salto, las descargas viejas se sueltan (03-11: 17 s y Kodi descolocado)")
    ESTADO["lento"] = 0.05                      # ~300 KB/s por conexión
    u = turbo.envuelve(base + "/v/alta.m3u8")
    raiz = u.rsplit("/", 1)[0]
    r = requests.get(raiz + "/0.ts", stream=True, timeout=30)
    next(r.iter_content(1024))
    r.close()
    time.sleep(0.5)                              # los 4 obreros ocupados con 0-3
    t0 = time.time()
    r = requests.get(raiz + "/11.ts", stream=True, timeout=30)
    next(r.iter_content(1024))
    dt = time.time() - t0
    r.close()
    ok(dt < 3.0, "el trozo del salto empieza a llegar en %.1f s" % dt)
    ESTADO["lento"] = 0.0

    print("5. Lo que no se acelera va directo")
    ok(turbo.envuelve(base + "/d/live.m3u8") == base + "/d/live.m3u8", "en directo -> directo")
    ok(turbo.envuelve("https://x/video.mp4|A=b") == "https://x/video.mp4|A=b", "mp4 -> directo")
    turbo.para()
    ok(turbo.envuelve(base + "/v/alta.m3u8") == base + "/v/alta.m3u8", "servicio parado -> directo")

    print("\n%s" % ("TODO OK" if not FALLOS else "FALLOS: %d" % len(FALLOS)))
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
