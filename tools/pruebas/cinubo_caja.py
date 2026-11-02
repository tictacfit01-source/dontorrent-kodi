# -*- coding: utf-8 -*-
"""Cinubo en la caja (2.9.91), sin red.

  1) `pedir` solo va a las rutas de Cinubo que hacen falta (nada de pedir
     cualquier URL con la sesión del dueño);
  2) el desempaquetado P.A.C.K.E.R (Lulustream) devuelve el m3u8;
  3) cada servidor por su familia de dominios (cambian de dominio a menudo);
  4) las cabeceras para Kodi: espacios como %20, Lulustream sin Origin;
  5) un enlace que no es https no se abre.
"""
import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf"))
from resources.lib import cinubo as C                    # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


print("\n=== 1) Solo las rutas que hacen falta ===")
for r in ("/", "/s/nzp5nh", "/s/nzp5nh/4x2", "/p/5wrbjt", "/a/jnxvx1/1x1000",
          "/t/eyJpdiI6IlpkQ3RhVlpKWDJtYkpxMDhMWEtLSWc9PSIs"):
    comprueba("vale " + r[:30], bool(C._RUTA.match(r)))
for r in ("/logout", "/configuracion/notificaciones", "/api/user/settings", "//evil.com/x",
          "/s/nzp5nh/../../logout", "/t/<script>", "https://otra.web/", "/s/NZP5NH"):
    comprueba("no vale " + r[:30], not C._RUTA.match(r))
comprueba("pedir() con una ruta que no vale: nada (y sin red)",
          C.pedir({"path": "/logout", "ck": {}}) == "")

print("\n=== 2) Lulustream: P.A.C.K.E.R ===")
# empaquetado de verdad de: player.setup({sources:[{file:"https://a.b/hls/x/master.m3u8"}]})
p = ("eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+c.toString(a)+'\\\\b','g'),k[c]);return p}"
     "('0.1({2:[{3:\"4://5.6/7/8/9.a\"}]})',11,11,'player|setup|sources|file|https|a|b|hls|x|master|m3u8'.split('|')))")
claro = C.desempaqueta(p)
comprueba("desempaqueta", 'player.setup({sources:[{file:"https://a.b/hls/x/master.m3u8"}]})' in claro, claro)
comprueba("y de ahí sale el m3u8", C._M3U8.search(claro).group(0) == "https://a.b/hls/x/master.m3u8")
comprueba("sin empaquetar: vacío", C.desempaqueta("<html></html>") == "")

print("\n=== 3) Cada servidor por su familia ===")
for u in ("https://luluvdo.com/e/x", "https://luluvido.com/e/x", "https://lulustream.com/e/x"):
    comprueba("Lulustream: " + u[8:25], C.familia(u) is C._generico)
for u in ("https://vidara.to/e/x", "https://vidaraa.cc/e/x"):
    comprueba("Vidara: " + u[8:25], C.familia(u) is C._vidara)
for u in ("https://firestream.site/e/x", "https://frstream.life/e/x"):
    comprueba("Firestream: " + u[8:25], C.familia(u) is C._firestream)
comprueba("Vidhide (dominio distinto en cada enlace): sin familia, va al genérico",
          C.familia("https://minochinos.com/embed/x") is None and callable(C._generico))
for u in ("https://bysekoze.com/e/x", "https://hgcloud.to/e/x", "https://evil-luluvdo.com.attacker.net/e/x"):
    comprueba("otro (ResolveURL o nada): " + u[8:30], C.familia(u) is None)

print("\n=== 4) Las cabeceras para Kodi ===")
u = C._con_cabeceras("https://a.b/m.m3u8", "https://luluvdo.com", con_origin=False)
comprueba("espacios como %20, nunca '+'", "+" not in u.split("|", 1)[1] and "%20" in u, u)
comprueba("Lulustream sin Origin", "Origin" not in u)
comprueba("Vidara con Origin", "Origin=" in C._con_cabeceras("https://a.b/m.m3u8", "https://vidara.to"))

print("\n=== 5) Solo https ===")
for malo in ("http://luluvdo.com/e/x", "javascript:alert(1)", ""):
    try:
        C.resolver(malo)
        comprueba("rechaza " + malo[:20], False)
    except C.NoSale:
        comprueba("rechaza " + (malo[:20] or "(vacío)"), True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: la caja solo pide a Cinubo lo necesario y abre los servidores conocidos")
