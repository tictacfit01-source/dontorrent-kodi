# -*- coding: utf-8 -*-
"""Reproducir de DonTorrent cuando no se puede bajar el .torrent (addon 2.9.75).

23-09-2026, tele del Comedor, DonTorrent caido: la caja tenia guardado el
enlace del .torrent (cache de 7 dias), enseñaba "Descargando torrent...", no
podia bajarlo y aun asi le pasaba a Elementum la URL de DonTorrent. Elementum
tampoco podia ("Could not resolve torrent" en el log) y la tele se quedaba SIN
HACER NADA, sin un aviso. Reproducido igual en el Kodi del PC. Esto vigila:
  1) sin .torrent, si el relay sabe la huella -> magnet (y nada de la URL);
  2) sin .torrent ni huella -> error con aviso, sin llamar a Elementum;
  3) con DonTorrent en mantenimiento, el aviso lo dice tal cual;
  4) con el .torrent bajado, lo de siempre (fichero a Elementum).
Kodi de mentira y sin red: el relay, DonTorrent y Elementum estan sustituidos.
"""
import os
import sys
import tempfile
import types

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf")
PERFIL = tempfile.mkdtemp(prefix="mw_perfil_")

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


# --- Kodi de mentira ------------------------------------------------------------
AVISOS, RESUELTOS = [], []
xbmc = types.ModuleType("xbmc")
xbmc.log = lambda *a, **k: None
xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGDEBUG = 1, 2, 3, 0
xbmc.executebuiltin = lambda *a, **k: None
xbmc.getCondVisibility = lambda *a, **k: True
xbmc.getInfoLabel = lambda *a, **k: ""
xbmc.executeJSONRPC = lambda *a, **k: "{}"
xbmc.sleep = lambda ms: None
xbmc.Monitor = type("Monitor", (), {"abortRequested": lambda self: False,
                                    "waitForAbort": lambda self, s=1: True})
xbmc.Player = type("Player", (), {"isPlaying": lambda self: False})
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getLocalizedString": lambda self, i: "",
    "getAddonInfo": lambda self, k: {"profile": PERFIL, "path": ADDON,
                                     "version": "2.9.75", "id": "plugin.video.mejorwolf"}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: PERFIL + os.sep if p.startswith("special://") else p
xbmcvfs.exists = os.path.exists
xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.NOTIFICATION_ERROR, xbmcgui.NOTIFICATION_INFO = "error", "info"
xbmcgui.NOTIFICATION_WARNING = "warning"


class _Dialog(object):
    def notification(self, titulo, texto, *a, **k):
        AVISOS.append(texto)

    def yesno(self, *a, **k):
        return True

    def ok(self, *a, **k):
        return True


class _ProgBG(object):
    def create(self, *a, **k):
        pass

    def update(self, *a, **k):
        pass

    def close(self):
        pass


class _ListItem(object):
    def __init__(self, label="", path=""):
        self.path = path

    def setProperty(self, *a):
        pass

    def setInfo(self, *a, **k):
        pass

    def setArt(self, *a, **k):
        pass


xbmcgui.Dialog = _Dialog
xbmcgui.DialogProgressBG = _ProgBG
xbmcgui.ListItem = _ListItem
xbmcplugin = types.ModuleType("xbmcplugin")
xbmcplugin.setResolvedUrl = lambda h, ok, item: RESUELTOS.append((ok, getattr(item, "path", "")))
xbmcplugin.addDirectoryItem = lambda *a, **k: True
xbmcplugin.addDirectoryItems = lambda *a, **k: True
xbmcplugin.endOfDirectory = lambda *a, **k: None
xbmcplugin.addSortMethod = lambda *a, **k: None
xbmcplugin.setContent = lambda *a, **k: None
xbmcplugin.SORT_METHOD_NONE = 0
for m in (xbmc, xbmcaddon, xbmcvfs, xbmcgui, xbmcplugin):
    sys.modules[m.__name__] = m
sys.argv = ["plugin://plugin.video.mejorwolf/", "1", ""]
sys.path.insert(0, os.path.abspath(ADDON))
sys.path.insert(0, os.path.abspath(os.path.join(ADDON, "resources")))

from resources.lib import main as M                  # noqa: E402

URL_DT = "https://dontorrent.supply/torrents/peliculas/parthenope.torrent"
IH = "ab" * 20
MAGNET = "magnet:?xt=urn:btih:" + IH + "&dn=Parthenope"
RELAY = {"magnet": ""}

M._check_elementum = lambda: True
M._set_np_title = lambda *a, **k: None
M._set_continue = lambda *a, **k: None
M.player.elementum_url = lambda uri: "plugin://plugin.video.elementum/play?uri=" + uri
M.dt.resolve_torrent = lambda cid, tb, page_url="": URL_DT
M._dt_magnet_relay = lambda cid, tb, t="": RELAY["magnet"]


def reproduce(bytes_torrent=b"", mant=False, magnet=""):
    del AVISOS[:]
    del RESUELTOS[:]
    RELAY["magnet"] = magnet
    M.dt.download_torrent = lambda u: bytes_torrent
    M.dt.en_mantenimiento = lambda: mant
    M.dt._render_relay_url = lambda: ""        # /dtfetch: sin relay
    M.dt_play("28896", "peliculas", title="Parthenope")
    return list(RESUELTOS), list(AVISOS)


try:
    print("\n=== 1) Sin .torrent y el relay sabe la huella: magnet ===")
    res, av = reproduce(magnet=MAGNET)
    comprueba("se reproduce, y por magnet", res and res[0][0] is True
              and ("uri=" + MAGNET) in res[0][1], res)
    comprueba("...nunca con la URL de DonTorrent", not any(URL_DT in r[1] for r in res), res)

    print("\n=== 2) Sin .torrent ni huella: aviso, y Elementum ni se entera ===")
    res, av = reproduce()
    comprueba("setResolvedUrl(False): la tele no se queda esperando", res == [(False, "")], res)
    comprueba("...con un aviso que se entiende", av and "torrent" in av[0].lower(), av)
    comprueba("...y sin la URL de DonTorrent en ninguna parte", URL_DT not in str(res), res)

    print("\n=== 3) Con DonTorrent en mantenimiento, se dice tal cual ===")
    res, av = reproduce(mant=True)
    comprueba("'DonTorrent está caído ahora mismo'", res == [(False, "")]
              and av and av[0].startswith("DonTorrent está caído"), av)

    print("\n=== 4) Con el .torrent bajado, lo de siempre ===")
    tor = b"d8:announce3:foo4:infod4:name3:abc6:lengthi1e12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"ee" + b" " * 120
    res, av = reproduce(bytes_torrent=tor, magnet=MAGNET)
    comprueba("fichero .torrent a Elementum (no magnet ni URL)",
              res and res[0][0] is True and "mejorwolf_play.torrent" in res[0][1], res)
finally:
    import shutil
    shutil.rmtree(PERFIL, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: sin .torrent, magnet si se sabe la huella; si no, un aviso (nunca silencio)")
