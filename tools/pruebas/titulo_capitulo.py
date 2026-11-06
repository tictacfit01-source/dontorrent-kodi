# -*- coding: utf-8 -*-
"""Un capitulo elegido DESDE KODI lleva el nombre de la serie (caja 2.9.100).

06-11-2026: /uso mostraba "Serie sin nombre 04x05". Desde la web el titulo ya
viajaba entero ("The Gentlemen 1x03"), pero las fichas de DonTorrent y WolfMax
del propio addon mandaban solo la etiqueta del capitulo ("04x05"): la tele, el
"Continuar" y /uso se quedaban sin saber que serie era. Esto vigila:
  1) DonTorrent: el capitulo se reproduce como "Serie 04x05" (sin la calidad);
  2) WolfMax: igual, y sin repetir la serie si la etiqueta ya la trae;
  3) las peliculas, como siempre.
Kodi de mentira y sin red (el mismo de dt_play.py).
"""
import io
import os
import sys
from urllib.parse import parse_qs, urlparse

AQUI = os.path.dirname(os.path.abspath(__file__))
src = io.open(os.path.join(AQUI, "dt_play.py"), encoding="utf-8").read()
exec(compile(src[:src.index("URL_DT =")], "dt_play_kodi", "exec"))   # Kodi de mentira + main as M

ITEMS = []
xbmcplugin.addDirectoryItem = lambda h, url, li, isFolder=False: ITEMS.append(url)  # noqa: F821
M.xbmcplugin.addDirectoryItem = xbmcplugin.addDirectoryItem                          # noqa: F821
M.tmdb.enrich = lambda *a, **k: {}
M._build_art = lambda *a, **k: {}
M._build_info_base = lambda *a, **k: {}
M._li = lambda label, **k: label


def titulos():
    out = []
    for u in ITEMS:
        q = parse_qs(urlparse(u).query)
        out.append((q.get("t") or [""])[0])
    del ITEMS[:]
    return out


M.dt.detail = lambda url: {"title": "The Gentlemen", "downloads": [
    {"season": 4, "episode": 5, "label": "04x05", "quality": "1080p", "content_id": "1", "tabla": "series"},
    {"season": 4, "episode": 6, "label": "04x06", "quality": "1080p", "content_id": "2", "tabla": "series"}]}
M._detail_dt("https://dontorrent/serie/x", "tvshow", "The Gentlemen")
t = titulos()
comprueba("DonTorrent: capitulo con la serie delante", t == ["The Gentlemen 04x05", "The Gentlemen 04x06"], t)  # noqa: F821

M.wf.detail = lambda url: {"title": "Silo", "downloads": [
    {"season": 2, "label": "2x03", "torrent_url": "magnet:a"},
    {"season": 2, "label": "Silo 2x04", "torrent_url": "magnet:b"}]}
M._detail_wf("https://wolfmax/serie/silo", "tvshow", "Silo")
t = titulos()
comprueba("WolfMax: con la serie, sin repetirla", t == ["Silo 2x03", "Silo 2x04"], t)  # noqa: F821

M.dt.detail = lambda url: {"title": "Parthenope", "year": 2024, "downloads": [
    {"label": "Descargar", "quality": "1080p", "content_id": "9", "tabla": "peliculas"}]}
M._detail_dt("https://dontorrent/peli/x", "movie", "Parthenope")
t = titulos()
comprueba("Pelicula: como siempre", t == ["Parthenope (2024) [1080p]"], t)  # noqa: F821

# 4) (2.9.101) streaming: el mando debe decir lo que suena, no lo ULTIMO que
#    fue por torrent. 06-11: "En el corazon de la bestia" por streaming y en el
#    mando "Silicon Valley 1x01" (el fichero del titulo era el de antes).
NPF = os.path.join(PERFIL, "np_prueba.txt")                                    # noqa: F821
M.xbmcvfs.translatePath = lambda p: NPF if p.endswith("mejorwolf_np.txt") else p
M._set_np_title("Silicon Valley 1x01")
import resources.lib.cinubo as _cbm                                           # noqa: E402
import resources.lib.turbo as _turbo                                          # noqa: E402
_cbm.resolver = lambda u: "https://servidor/v.m3u8"
_turbo.envuelve = lambda url, aviso=None: url
_ListItem.setMimeType = lambda self, *a: None                                  # noqa: F821
_ListItem.setContentLookup = lambda self, *a: None                             # noqa: F821
M._marca_lanzado = lambda: None
M.cb_play("https://lulustream/e/x", "En el corazón de la bestia")
np_t = io.open(NPF, encoding="utf-8").read() if os.path.exists(NPF) else ""
comprueba("Streaming: el mando dice lo que suena", np_t == "En el corazón de la bestia", np_t)  # noqa: F821
M._set_np_title("")
comprueba("Sin titulo: fuera el de antes", not os.path.exists(NPF))           # noqa: F821

print("\n---- VEREDICTO ----")
print("TODO OK: los capitulos elegidos en Kodi llevan el nombre de la serie" if not fallos  # noqa: F821
      else "%d FALLOS" % fallos)  # noqa: F821
sys.exit(1 if fallos else 0)  # noqa: F821
