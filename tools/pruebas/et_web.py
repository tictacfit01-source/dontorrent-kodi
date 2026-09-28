# -*- coding: utf-8 -*-
"""La calidad de EliteTorrent en sus tarjetas (addon 2.9.79).

28-09-2026: EliteTorrent volvio de su caida y todas sus pelis llegaban a la
web SIN calidad. Su tarjeta lleva DOS "span.marca": la primera es la bandera
del idioma (una imagen, sin texto) y la segunda la calidad; el lector tomaba
solo la primera. Marcado REAL recortado, sin red.
"""
import os
import sys
import tempfile
import types

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
PERFIL = tempfile.mkdtemp(prefix="mw_perfil_et_")
fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


xbmc = types.ModuleType("xbmc")
xbmc.log = lambda *a, **k: None
xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGDEBUG = 1, 2, 3, 0
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getAddonInfo": lambda self, k: {"profile": PERFIL, "version": "2.9.79"}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: PERFIL + os.sep
for m in (xbmc, xbmcaddon, xbmcvfs, types.ModuleType("xbmcgui"), types.ModuleType("xbmcplugin")):
    sys.modules[m.__name__] = m
sys.path.insert(0, os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf"))
from bs4 import BeautifulSoup                              # noqa: E402
from resources.lib import scraper_elitetorrent as ET       # noqa: E402


def tarjeta(url, titulo, marca, peso):
    return '''<li><div class=imagen> <a href="%s" title="%s"><img class="brighten lazyed" data-src="https://www.elitetorrent.com/p.jpg"/></a>
 <span class="marca estreno" id=idiomacio><i><img src='x.gif' data-src='espanol.png' title='Pelicula en Español Castellano'/></i></span>
 <span class="marca estreno" style="right: 0px;left: auto;max-width: 60%%;"><i>%s</i></span>
 <div class=voto1 title="Peso de pelicula"><span class=dig1>%s</span></div></div>
 <div class=meta> <a class=nombre href="%s" title="%s">%s</a> </div></li>''' % (url, titulo, marca, peso, url, titulo, titulo)


PAGINA = ('<ul class="miniboxs">'
          + tarjeta("https://www.elitetorrent.com/peliculas/dune-parte-dos/", "Dune: Parte dos", "720p", "2.51GB")
          + tarjeta("https://www.elitetorrent.com/series/the-brave-1x13/", "The Brave 1x13", "HDTV 720p", "1.53 GBs")
          + tarjeta("https://www.elitetorrent.com/peliculas/dune/", "Dune", "---", "desc.")
          + "</ul>")

print("\n=== La calidad de sus tarjetas ===")
its = ET._parse_listing(BeautifulSoup(PAGINA, "html.parser"), "https://www.elitetorrent.com/?s=dune")
q = [(i["title"], i["quality"]) for i in its]
comprueba("la calidad sale de la SEGUNDA marca (la primera es la bandera)",
          q[:2] == [("Dune: Parte dos", "720p"), ("The Brave 1x13", "HDTV 720p")], q)
comprueba("'---' no es una calidad", q[2] == ("Dune", ""), q[2])
comprueba("...y el peso sigue saliendo", [i["size"] for i in its][:2] == ["2.51GB", "1.53 GBs"], its)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: EliteTorrent sale con su calidad")
