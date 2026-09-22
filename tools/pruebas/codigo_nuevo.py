# -*- coding: utf-8 -*-
"""Cambiar el codigo de una tele desde Mis Kodis (addon 2.9.73 + relay dtbl37).

22-09-2026: los codigos del salon y del PC estuvieron publicados en GitHub, y
con un codigo se le pueden mandar ordenes a esa tele. Esto vigila las dos
patas de servidor y caja (la del movil esta en codigo.js):
  - la CAJA guarda el codigo nuevo en disco ANTES de adoptarlo, avisa con un
    latido y lo ensena en la tele; lo invalido lo ignora;
  - el RELAY solo deja pasar un codigo nuevo de 6 cifras, distinto del actual y
    que no use ya otra tele encendida.

Sin red y sin tocar el Kodi de verdad: el perfil de la caja es una carpeta
temporal, y el /tmp del relay (C:\\tmp) se deja como estaba.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"     # nada de red de fondo en la prueba
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


# --- Kodi de mentira, con el perfil en una carpeta temporal -------------------
AVISOS = []
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
    "getAddonInfo": lambda self, k: {"version": "2.9.73"}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: PERFIL + os.sep
xbmcvfs.exists = os.path.exists
xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.NOTIFICATION_INFO = "info"


class _Dialog(object):
    def notification(self, titulo, texto, *a, **k):
        AVISOS.append(texto)


xbmcgui.Dialog = _Dialog
xbmcplugin = types.ModuleType("xbmcplugin")
for m in (xbmc, xbmcaddon, xbmcvfs, xbmcgui, xbmcplugin):
    sys.modules[m.__name__] = m
sys.path.insert(0, os.path.abspath(ADDON))
sys.path.insert(0, os.path.abspath(os.path.join(ADDON, "resources")))

from resources.lib import remote_kb as rkb          # noqa: E402
import service                                       # noqa: E402

try:
    print("\n=== 1) La caja: set_code ===")
    comprueba("el perfil es la carpeta temporal (no el Kodi de verdad)",
              rkb._CODE_FILE.startswith(PERFIL), rkb._CODE_FILE)
    viejo = rkb.get_code()
    comprueba("get_code crea y guarda un codigo de 6 cifras",
              len(viejo) == 6 and open(rkb._CODE_FILE).read().strip() == viejo, viejo)
    comprueba("set_code('123456') lo cambia en disco y en memoria",
              rkb.set_code("123456") and rkb.get_code() == "123456"
              and open(rkb._CODE_FILE).read().strip() == "123456")
    for malo in ("12345", "1234567", "abcdef", "", None):
        comprueba("rechaza %r sin tocar nada" % (malo,),
                  rkb.set_code(malo) is False and rkb.get_code() == "123456")
    # un disco que no deja escribir: NO se adopta en memoria
    buena = rkb._CODE_FILE
    un_fichero = os.path.join(PERFIL, "no_soy_carpeta")
    open(un_fichero, "w").write("x")
    rkb._CODE_FILE = os.path.join(un_fichero, "remote_code.txt")
    try:
        ok = rkb.set_code("654321")
    finally:
        rkb._CODE_FILE = buena
    comprueba("si no se puede guardar, NO se cambia (ni en memoria)",
              ok is False and rkb.get_code() == "123456", (ok, rkb.get_code()))

    print("\n=== 2) La caja: la orden que llega del movil ===")
    latidos = []
    viejo_push, viejo_poll = rkb.push_status, rkb.poll
    rkb.push_status = lambda v, cont=None, diag=None: latidos.append((rkb.get_code(), v))
    try:
        service._codigo_nuevo("654321")
        comprueba("adopta el codigo nuevo", rkb.get_code() == "654321")
        comprueba("y late AL MOMENTO con el nuevo (lo que espera el movil)",
                  latidos == [("654321", "2.9.73")], latidos)
        comprueba("y lo ensena en la tele", any("654321" in a for a in AVISOS), AVISOS)
        n = len(latidos)
        service._codigo_nuevo("654321")
        service._codigo_nuevo("12")
        service._codigo_nuevo(None)
        comprueba("el mismo codigo o uno invalido se ignoran",
                  len(latidos) == n and rkb.get_code() == "654321")
        rkb.poll = lambda timeout=6: [{"c": "codigo_nuevo", "nuevo": "111222"}]
        service._poll_remote_kb()
        comprueba("por el camino de verdad (el sondeo del mando) tambien",
                  rkb.get_code() == "111222"
                  and open(rkb._CODE_FILE).read().strip() == "111222")
    finally:
        rkb.push_status, rkb.poll = viejo_push, viejo_poll

    print("\n=== 3) El relay: /kb/send ===")
    sys.path.insert(0, os.path.abspath(os.path.join(AQUI, "..", "..", "render_relay")))
    import app as A                                  # noqa: E402
    FICH = [A._KB_FILE, A._KB_STATUS_FILE]
    os.makedirs("/tmp", exist_ok=True)
    copia = {}
    for f in FICH:
        if os.path.exists(f):
            copia[f] = f + ".prueba_bak"
            shutil.copy(f, copia[f])
    try:
        with open(A._KB_FILE, "w") as fh:
            json.dump({}, fh)
        with open(A._KB_STATUS_FILE, "w") as fh:
            json.dump({"300003": {"ts": time.time(), "v": "2.9.73"}}, fh)
        cli = A.app.test_client()

        def envia(nuevo, code="100001"):
            r = cli.post("/kb/send", json={"code": code, "cmd": "codigo_nuevo",
                                           "nuevo": nuevo},
                         headers={"X-Forwarded-For": "10.9.8.7"})
            return r.status_code, (r.get_json() or {})

        st, js = envia("200002")
        cola = (json.load(open(A._KB_FILE)).get("100001") or {}).get("ev") or []
        comprueba("un codigo nuevo valido se encola para ESA tele",
                  st == 200 and js.get("ok")
                  and {"c": "codigo_nuevo", "nuevo": "200002"} in cola, (st, js, cola))
        for malo, esperado in (("100001", 400), ("20000", 400), ("2000021", 400),
                               ("", 400), ("300003", 409)):
            st, js = envia(malo)
            comprueba("%r -> %d (%s)" % (malo, esperado, js.get("error")), st == esperado,
                      (st, js))
    finally:
        for f in FICH:
            try:
                if f in copia:
                    shutil.move(copia[f], f)
                elif os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
finally:
    shutil.rmtree(PERFIL, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: la tele cambia de codigo solo si lo puede guardar, y lo confirma")
