# -*- coding: utf-8 -*-
"""Solo cuenta lo que lanza MejorWolf (addon 2.9.95).

03-11-2026: en /uso salía "M+ LA LIGA 1 HD" y otros vídeos de OTROS addons: la
caja subía al relay cualquier vídeo que sonara en Kodi. Ahora el addon marca
cada lanzamiento y el servicio solo sube (y solo usa para "Continuar viendo" y
la calidad) el vídeo que empieza tras una marca sin gastar.

Sin red y sin tocar el Kodi de verdad: Kodi de mentira y perfil temporal.
"""
import os
import shutil
import sys
import tempfile
import time
import types

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf")
PERFIL = tempfile.mkdtemp(prefix="mw_perfil_")
fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


KODI = {"video": False, "f": "", "title": "", "show": ""}
xbmc = types.ModuleType("xbmc")
xbmc.log = lambda *a, **k: None
xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGDEBUG = 1, 2, 3, 0
xbmc.executebuiltin = lambda *a, **k: None
xbmc.sleep = lambda ms: None


def _cond(c):
    return {"Player.HasVideo": KODI["video"]}.get(c, False)


def _info(l):
    return {"Player.Filenameandpath": KODI["f"], "VideoPlayer.Title": KODI["title"],
            "VideoPlayer.TVShowTitle": KODI["show"], "VideoPlayer.Time": "00:10:00",
            "VideoPlayer.Duration": "01:40:00", "Player.Process(videowidth)": "1,920",
            "Player.Process(videoheight)": "1,080"}.get(l, "") if KODI["video"] else ""


xbmc.getInfoLabel = _info
xbmc.getCondVisibility = _cond
xbmc.executeJSONRPC = lambda *a, **k: "{}"
xbmc.Monitor = type("Monitor", (), {"abortRequested": lambda self: False,
                                    "waitForAbort": lambda self, s=1: True})
xbmc.Player = type("Player", (), {})
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getAddonInfo": lambda self, k: {"version": "2.9.95"}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: os.path.join(PERFIL, p.split("/")[-1]) if "://" in p else PERFIL + os.sep
xbmcvfs.exists = os.path.exists
xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.NOTIFICATION_INFO = "info"
xbmcgui.Dialog = type("Dialog", (), {"notification": lambda self, *a, **k: None})
xbmcplugin = types.ModuleType("xbmcplugin")
for m in (xbmc, xbmcaddon, xbmcvfs, xbmcgui, xbmcplugin):
    sys.modules[m.__name__] = m
sys.path.insert(0, os.path.abspath(ADDON))
sys.path.insert(0, os.path.abspath(os.path.join(ADDON, "resources")))

import service                                       # noqa: E402

RELOJ = [time.time()]
service.time = types.SimpleNamespace(time=lambda: RELOJ[0], sleep=lambda s: None)
LANZADO = os.path.join(PERFIL, "mejorwolf_lanzado.txt")


def pasa(s):
    RELOJ[0] += s


def lanza():
    """Lo que hace main._marca_lanzado (con el reloj de la prueba)."""
    open(LANZADO, "w").write("x")
    os.utime(LANZADO, (RELOJ[0], RELOJ[0]))


def suena(f, title="", show=""):
    KODI.update(video=True, f=f, title=title, show=show)


def para():
    KODI.update(video=False, f="", title="", show="")


def reinicia_servicio():
    service._NUESTRO.update(on=False, ok=False, f="", t0=0.0, fin=0.0)


try:
    print("\n=== 1) Otro addon (el fútbol) no sale ===")
    suena("plugin://plugin.video.otro/canal/mlaliga", "M+ LA LIGA 1 HD")
    comprueba("sin lanzar nada nuestro: no se sube", service._get_now_playing() is None)
    comprueba("ni su calidad", service._playback_diag() is None)
    para(); service._get_now_playing(); pasa(30)

    print("\n=== 2) Lo nuestro sí ===")
    lanza(); pasa(95)                       # el torrent tarda en arrancar
    suena("plugin://plugin.video.elementum/play?uri=x", "Lupin")
    np = service._get_now_playing()
    comprueba("se sube con su título", bool(np) and np["title"] == "Lupin", np)
    comprueba("y su calidad", (service._playback_diag() or {}).get("res") == "1,920x1,080")
    pasa(10); suena("http://127.0.0.1:65220/files/Lupin.mkv", "Lupin")
    comprueba("Elementum cambia la ruta al arrancar: sigue siendo nuestro",
              bool(service._get_now_playing()))
    pasa(600)
    comprueba("a mitad de peli sigue", bool(service._get_now_playing()))

    print("\n=== 3) Se actualiza el addon con la peli a medias ===")
    reinicia_servicio(); pasa(1200)
    comprueba("el servicio nuevo la reconoce (mismo vídeo)", bool(service._get_now_playing()))

    print("\n=== 4) Se para y se pone el fútbol en otro addon ===")
    para(); service._get_now_playing(); pasa(120)
    suena("plugin://plugin.video.otro/canal/mlaliga", "M+ LA LIGA 1 HD")
    comprueba("la marca ya se gastó: no se sube", service._get_now_playing() is None)
    comprueba("ni su calidad", service._playback_diag() is None)

    print("\n=== 5) Lanzado hace poco, pero la marca ya se usó: otro addon ===")
    para(); service._get_now_playing(); pasa(30)
    lanza(); pasa(5)
    suena("http://127.0.0.1:65220/files/Peli.mkv", "Peli")
    comprueba("la nuestra sí", bool(service._get_now_playing()))
    para(); service._get_now_playing(); pasa(90)
    suena("plugin://plugin.video.otro/x", "Otra cosa")
    comprueba("lo siguiente, de otro addon, no", service._get_now_playing() is None)

    print("\n=== 6) Una marca vieja (lanzó y no arrancó) no vale ===")
    para(); service._get_now_playing(); pasa(30)
    lanza(); pasa(1000)
    suena("plugin://plugin.video.otro/y", "Tele")
    comprueba("pasados 15 min no se toma por nuestro", service._get_now_playing() is None)

    print("\n=== 7) Sin parar, se cambia a otro addon a mitad ===")
    para(); service._get_now_playing(); pasa(30)
    lanza(); pasa(5); suena("http://127.0.0.1:65220/files/A.mkv", "A")
    comprueba("nuestro", bool(service._get_now_playing()))
    pasa(900); suena("plugin://plugin.video.otro/z", "Z")
    comprueba("el vídeo nuevo de otro addon, no", service._get_now_playing() is None)

    print("\n=== 8) El addon marca antes de cada entrega a Kodi ===")
    src = open(os.path.join(ADDON, "resources", "lib", "main.py"), encoding="utf-8").read()
    comprueba("las 3 entregas (torrent, DonTorrent, streaming) llevan la marca",
              src.count("_marca_lanzado()\n") + src.count("_marca_lanzado()\r\n") == 3
              and src.count("setResolvedUrl(HANDLE, True") == 3, src.count("_marca_lanzado()"))
finally:
    shutil.rmtree(PERFIL, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: solo se ve lo que lanza MejorWolf")
