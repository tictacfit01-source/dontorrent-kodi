# -*- coding: utf-8 -*-
"""El worker mw-video (ver aquí, dtbl131): de verdad, contra producción.

Por cada servidor que la web deja ver aquí (Lulustream, Vidara, Firestream,
Vidhide) de un capítulo conocido: el relay da la página del servidor
(/cbenlaces + /cbembed, con caché: no gasta cupo de Cinubo si ya se pidió),
mw-video saca la emisión firmada y se baja un trozo como lo haría el
navegador (Origin de la web, sin Referer del servidor). Además:
  - una URL manipulada da 403 (no es un proxy abierto);
  - a la familia Vidara NUNCA le llega un Range (su caché se envenena).

Solo lecturas. Si un servidor no tiene ese capítulo o está muerto, se dice y
se sigue: lo que importa es que los que existen abran.

Uso: py -3.13 tools/pruebas/mw_video.py
"""
import sys
import time
from urllib.parse import quote

import requests

RELAY = "https://mw-render-relay-6noq.onrender.com"
VIDEO = "https://mw-video.israeldm93.workers.dev"
WEB = {"Origin": RELAY}
CAP = "/cbenlaces?id=236235&kind=serie&t=The%20Gentlemen&s=1&e=1"   # The Gentlemen 1x01
AQUI = ("LULUSTREAM", "VIDARA", "FIRESTREAM", "VIDHIDE")


def lineas(t):
    return [l.strip() for l in t.splitlines() if l.strip() and not l.startswith("#")]


def main():
    fallos = 0
    its = requests.get(RELAY + CAP, timeout=40).json().get("items") or []
    if not its:
        print("FALLO: el relay no da servidores de The Gentlemen 1x01")
        return 1
    abiertos = 0
    for srv in AQUI:
        cand = [x for x in its if x["srv"] == srv][:2]
        if not cand:
            print("  --   %-10s no está en este capítulo" % srv)
            continue
        hecho = False
        for it in cand:
            e = requests.get(RELAY + "/cbembed?r=" + it["r"], timeout=40).json()
            if not e.get("ok"):
                print("  --   %-10s subida %s" % (srv, e.get("error")))
                time.sleep(4)
                continue
            t0 = time.time()
            j = requests.get(VIDEO + "/r?u=" + quote(e["u"], safe=""), headers=WEB, timeout=40).json()
            if not j.get("ok"):
                print("  MAL  %-10s mw-video no la saca: %s" % (srv, j.get("error")))
                fallos += 1
                hecho = True
                break
            m = requests.get(j["play"], headers=WEB, timeout=20)
            var = lineas(m.text)[0] if "#EXT-X-STREAM-INF" in m.text else j["play"]
            v = requests.get(var, headers=WEB, timeout=20) if var != j["play"] else m
            tz = lineas(v.text)
            t = requests.get(tz[min(5, len(tz) - 1)], headers=dict(WEB, Range="bytes=0-99"), timeout=60)
            bien = m.headers.get("Access-Control-Allow-Origin") == "*" and t.status_code in (200, 206) and len(t.content) >= 100
            if srv == "VIDARA" and t.status_code != 200:
                bien = False
                print("  MAL  a Vidara le ha llegado un Range (%s)" % t.status_code)
            print("  %s %-10s %d trozos, trozo %s en %.1f s" % ("ok  " if bien else "MAL ", srv, len(tz), t.status_code, time.time() - t0))
            fallos += 0 if bien else 1
            abiertos += 1 if bien else 0
            hecho = True
            break
        if not hecho:
            print("  --   %-10s ninguna subida viva" % srv)
        time.sleep(3)
    if not abiertos:
        print("  MAL  ningún servidor abrió")
        fallos += 1
    r = requests.get(VIDEO + "/p?k=xx.yy&u=https://example.com/", timeout=15)
    ok = r.status_code == 403 and "firma" in r.text
    print("  %s una URL sin firma da 403" % ("ok  " if ok else "MAL "))
    fallos += 0 if ok else 1
    print("\n---- VEREDICTO ----")
    print("TODO OK: mw-video abre los servidores de 'Ver aquí'" if not fallos else "%d FALLOS" % fallos)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
