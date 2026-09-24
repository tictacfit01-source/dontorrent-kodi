# -*- coding: utf-8 -*-
"""Lo que la caja VE de WolfMax y EliteTorrent, contado al relay (addon 2.9.76).

24-09-2026: el relay no puede saber si WolfMax o EliteTorrent estan caidos --
desde la zona de Render sus webs le ponen un reto ("Just a moment", 403) antes
de intentar nada, incluso por nuestro proxy --, pero las cajas, en España, SI
ven el 522 de Cloudflare (su servidor no contesta). Esto vigila:
  1) http_session apunta el codigo de cada web, tambien cuando falla;
  2) con cada trabajo, la caja cuenta `caidas` (un 52x) o `vivas` (le
     contestaron); lo demas (un reto, un 404) no dice nada.
Kodi de mentira, sin red.
"""
import os
import sys
import tempfile
import time
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


xbmc = types.ModuleType("xbmc")
xbmc.log = lambda *a, **k: None
xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGDEBUG = 1, 2, 3, 0
xbmc.executebuiltin = lambda *a, **k: None
xbmc.sleep = lambda ms: None
xbmc.getInfoLabel = lambda *a, **k: ""
xbmc.getCondVisibility = lambda *a, **k: False
xbmc.executeJSONRPC = lambda *a, **k: "{}"
xbmc.Monitor = type("Monitor", (), {"abortRequested": lambda self: False,
                                    "waitForAbort": lambda self, s=1: True})
xbmc.Player = type("Player", (), {})
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getAddonInfo": lambda self, k: {"profile": PERFIL, "version": "2.9.76"}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: PERFIL + os.sep
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

import requests                                          # noqa: E402
from resources.lib import http_session as hs             # noqa: E402
import service                                           # noqa: E402


class _Resp(object):
    def __init__(self, st):
        self.status_code = st


def falla(st):
    def f(*a, **k):
        raise requests.exceptions.HTTPError("%d" % st, response=_Resp(st))
    return f


try:
    print("\n=== 1) http_session apunta el codigo de cada web ===")
    viejo = hs._try_with_fallbacks_impl
    try:
        t0 = time.time()
        hs._try_with_fallbacks_impl = falla(522)
        try:
            hs.get(None, "https://www.wolfmax4k.com/serie-online-4k/270209")
        except requests.exceptions.HTTPError:
            pass
        hs._try_with_fallbacks_impl = lambda *a, **k: _Resp(200)
        hs.get(None, "https://www.elitetorrent.com/")
        v = hs.salud_desde(t0)
        comprueba("un 522 de WolfMax (fallo) y un 200 de EliteTorrent, sin 'www.'",
                  v.get("wolfmax4k.com") == 522 and v.get("elitetorrent.com") == 200, v)
        comprueba("lo de antes del trabajo no cuenta", hs.salud_desde(time.time() + 1) == {})
        hs._try_with_fallbacks_impl = lambda *a, **k: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("reset"))
        t1 = time.time()
        try:
            hs.get(None, "https://www.wolfmax4k.com/")
        except requests.exceptions.ConnectionError:
            pass
        comprueba("un fallo SIN respuesta (reset del operador) no apunta nada",
                  hs.salud_desde(t1) == {}, hs.salud_desde(t1))
    finally:
        hs._try_with_fallbacks_impl = viejo

    print("\n=== 2) La caja lo cuenta con cada trabajo ===")

    def con(vistos):
        out = {}
        viejo_sd = hs.salud_desde
        hs.salud_desde = lambda t0: dict(vistos)
        try:
            service._salud_en(out, 0)
        finally:
            hs.salud_desde = viejo_sd
        return out
    comprueba("WolfMax 522 -> caidas: wf", con({"wolfmax4k.com": 522}) == {"caidas": ["wf"]})
    comprueba("EliteTorrent 200 -> vivas: et", con({"elitetorrent.com": 200}) == {"vivas": ["et"]})
    comprueba("las dos caidas", con({"wolfmax4k.com": 522, "elitetorrent.com": 521})
              == {"caidas": ["wf", "et"]})
    comprueba("un reto (403) o un 404 no dicen nada",
              con({"wolfmax4k.com": 403, "elitetorrent.com": 404}) == {})
    comprueba("DonTorrent u otras webs no se cuentan aqui",
              con({"dontorrent.moi": 503, "divxtotal.foo": 522}) == {})
    comprueba("si en el mismo trabajo algo le contesto, esta viva",
              con({"wolfmax4k.com": 200}) == {"vivas": ["wf"]})
    src = open(os.path.join(ADDON, "service.py"), encoding="utf-8").read()
    i = src.index("def _do_etjob(")
    trozo = src[i:src.index("\ndef ", i + 10)]
    comprueba("_do_etjob lo añade antes de subir el resultado",
              "_salud_en(out, t_ini)" in trozo
              and trozo.index("_salud_en(out, t_ini)") < trozo.index("rkb.push_etjob(out)"))
finally:
    import shutil
    shutil.rmtree(PERFIL, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: la caja cuenta lo que ve de WolfMax y EliteTorrent")
