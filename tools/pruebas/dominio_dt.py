# -*- coding: utf-8 -*-
"""Resolver el dominio de DonTorrent en la caja (addon 2.9.74): sin red.

23-09-2026: con DonTorrent caido (su 503 "La web volvera enseguida" en TODOS
sus dominios) `resolve_domain` probaba los 14 dominios -- DoH 15 s + proxy
30 s cada uno -- y no guardaba el fallo, asi que lo repetia en cada trabajo:
mas de un minuto por intento (medido en el Kodi del PC) y Kodi tardando 14 s
de mas en cerrarse. Esto vigila:
  1) su pagina de mantenimiento corta la busqueda al primer dominio;
  2) el fallo se recuerda 3 min, tambien en disco (cada accion de la tele es
     un proceso nuevo), y un acierto lo borra;
  3) un acierto rapido ya no espera al sondeo mas lento (antes, `with
     ThreadPoolExecutor` esperaba a todos al salir);
  4) la tele dice "DonTorrent esta caido" en vez de "intentalo en un minuto".

El perfil de la caja es una carpeta temporal; nada toca la red (Telegram,
Supabase, DoH y el proxy estan sustituidos).
"""
import os
import shutil
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


# --- Kodi de mentira ----------------------------------------------------------
xbmc = types.ModuleType("xbmc")
LOG = []
xbmc.log = lambda msg, *a, **k: LOG.append(str(msg))
xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGDEBUG = 1, 2, 3, 0
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getAddonInfo": lambda self, k: {"profile": PERFIL}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: p
xbmcvfs.exists = os.path.exists
xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
xbmcgui = types.ModuleType("xbmcgui")
xbmcplugin = types.ModuleType("xbmcplugin")
for m in (xbmc, xbmcaddon, xbmcvfs, xbmcgui, xbmcplugin):
    sys.modules[m.__name__] = m
sys.path.insert(0, os.path.abspath(ADDON))
sys.path.insert(0, os.path.abspath(os.path.join(ADDON, "resources")))

import requests                                     # noqa: E402
from resources.lib import scraper_dontorrent as dt  # noqa: E402
from resources.lib import supabase_sync as sb       # noqa: E402

MANT = ('<!DOCTYPE html><html><head><title>La web volverá enseguida</title></head>'
        '<body><img src="/__proxy-dok/dontorrent.svg"><h1>La web volverá enseguida</h1>'
        '<p>Estamos teniendo un problema puntual en el servidor.</p></body></html>')


def resp(st, txt, url="https://dontorrent.moi/"):
    r = requests.models.Response()
    r.status_code = st
    r._content = txt.encode("utf-8")
    r.encoding = "utf-8"
    r.url = url
    return r


def error_http(st, txt):
    r = resp(st, txt)
    return requests.exceptions.HTTPError("%d Server Error" % st, response=r)


# --- nada de red: Telegram, Supabase, DoH y proxy de mentira -------------------
dt._resolve_via_telegram = lambda: ([], set())
sb.get_domain = lambda *a, **k: None
sb.get_fallback_domains = lambda *a, **k: []
SONDEOS = []


def reinicia():
    dt._cached_domain = None
    dt._cached_domain_ts = 0
    dt._RESUELVE_FALLO[0] = 0.0
    dt._RESUELVE_MANT[0] = False
    try:
        os.remove(dt._FALLO_FILE)
    except Exception:
        pass
    del SONDEOS[:]


try:
    print("\n=== 1) Su pagina de mantenimiento corta la busqueda al PRIMER dominio ===")
    comprueba("el fichero del fallo va al perfil de la caja",
              dt._FALLO_FILE.startswith(PERFIL), dt._FALLO_FILE)
    comprueba("el canonico es .moi y va primero en la lista",
              dt._CANONICAL_DOMAIN == "dontorrent.moi"
              and dt.FALLBACK_DOMAINS[0] == "dontorrent.moi")

    def doh_mant(method, url, **k):
        SONDEOS.append(("doh", url))
        r = resp(503, MANT, url)
        dt._mira_mantenimiento(r)
        raise error_http(503, MANT)

    def proxy_mant(url, **k):
        SONDEOS.append(("proxy", url))
        raise error_http(503, MANT)
    dt._doh_fetch, dt._proxy_get = doh_mant, proxy_mant
    reinicia()
    t0 = time.time()
    d = dt.resolve_domain()
    t = time.time() - t0
    comprueba("contesta al momento (%.2f s) con el dominio de siempre" % t,
              t < 1.0 and d == "dontorrent.moi", (t, d))
    comprueba("...habiendo mirado UN solo dominio (no los 14)",
              {u for _, u in SONDEOS} == {"https://dontorrent.moi/"}, SONDEOS)
    comprueba("...y dice que es mantenimiento", dt.en_mantenimiento() is True)
    del SONDEOS[:]
    d = dt.resolve_domain()
    comprueba("la siguiente vez, ni un sondeo (el fallo se recuerda)", not SONDEOS, SONDEOS)
    # otro proceso de la tele (memoria vacia) lo lee del disco
    dt._RESUELVE_FALLO[0] = 0.0
    dt._RESUELVE_MANT[0] = False
    d = dt.resolve_domain()
    comprueba("otra accion de la tele (proceso nuevo) tambien lo sabe, por el disco",
              not SONDEOS and dt.en_mantenimiento() is True, SONDEOS)

    print("\n=== 2) Si solo el proxy ve el mantenimiento (el ISP tumba el DoH) ===")

    def doh_reset(method, url, **k):
        SONDEOS.append(("doh", url))
        raise requests.exceptions.ConnectionError("[WinError 10054] reset")
    dt._doh_fetch = doh_reset
    reinicia()
    t0 = time.time()
    d = dt.resolve_domain()
    comprueba("tambien corta al primero (%.2f s)" % (time.time() - t0),
              len({u for _, u in SONDEOS}) == 1 and dt.en_mantenimiento() is True, SONDEOS)

    print("\n=== 3) Nada contesta (sin mantenimiento): se recuerda igual, sin 'caido' ===")

    def nada(*a, **k):
        SONDEOS.append(("x", a[1] if len(a) > 1 else a[0]))
        raise requests.exceptions.ConnectionError("nada")
    dt._doh_fetch = nada
    dt._proxy_get = lambda url, **k: nada(url)
    reinicia()
    d = dt.resolve_domain()
    n1 = len(SONDEOS)
    comprueba("prueba todos (%d sondeos) y devuelve el de reserva" % n1,
              n1 >= 14 and d == "dontorrent.moi", (n1, d))
    comprueba("...sin decir que es mantenimiento", dt.en_mantenimiento() is False)
    del SONDEOS[:]
    dt.resolve_domain()
    comprueba("...y durante 3 min no se vuelve a sondear", not SONDEOS, SONDEOS)
    dt._RESUELVE_FALLO[0] = time.time() - 200
    try:
        os.remove(dt._FALLO_FILE)
    except Exception:
        pass
    dt.resolve_domain()
    comprueba("pasados los 3 min, se vuelve a probar", len(SONDEOS) >= 14, len(SONDEOS))

    print("\n=== 4) Un acierto rapido NO espera al sondeo mas lento ===")
    viejo_probe = dt._probe_domain

    def probe_lento(host):
        SONDEOS.append(("p", host))
        if host == dt._CANONICAL_DOMAIN:
            return None              # el canonico va antes y en serie: no cuenta
        if host == "dontorrent.club":
            time.sleep(0.2)
            return "dontorrent.club"
        time.sleep(4.0)
        return None
    dt._probe_domain = probe_lento
    reinicia()
    t0 = time.time()
    d = dt.resolve_domain()
    t = time.time() - t0
    dt._probe_domain = viejo_probe
    comprueba("devuelve el que contesta en cuanto contesta (%.2f s; antes esperaba 4 s)" % t,
              d == "dontorrent.club" and t < 2.0, (d, t))
    comprueba("...y un acierto borra el fallo apuntado",
              dt._RESUELVE_FALLO[0] == 0.0 and not os.path.exists(dt._FALLO_FILE))

    print("\n=== 5) El acierto de siempre (DonTorrent sano) ===")

    def doh_bien(method, url, **k):
        SONDEOS.append(("doh", url))
        return resp(200, '<html><a href="/pelicula/1/1/x">torrent</a></html>', url)
    dt._doh_fetch = doh_bien
    reinicia()
    dt._apunta_fallo(False, mant=True)       # venia de una caida
    dt._RESUELVE_FALLO[0] = time.time() - 200  # ...que ya caduco
    try:
        os.remove(dt._FALLO_FILE)
    except Exception:
        pass
    d = dt.resolve_domain()
    comprueba("resuelve .moi a la primera y deja de estar 'en mantenimiento'",
              d == "dontorrent.moi" and len(SONDEOS) == 1
              and dt.en_mantenimiento() is False, (d, SONDEOS))

    print("\n=== 6) Lo que dice la tele al fallar un play de DonTorrent ===")
    src = open(os.path.join(ADDON, "resources", "lib", "main.py"), encoding="utf-8").read()
    i = src.index("def dt_play(")
    trozo = src[i:src.index("\ndef ", i + 10)]
    comprueba("dt_play pregunta a en_mantenimiento() y lo dice tal cual",
              "dt.en_mantenimiento()" in trozo
              and "DonTorrent está caído ahora mismo" in trozo)
finally:
    shutil.rmtree(PERFIL, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: con DonTorrent caido la caja deja de sondear y la tele lo dice")
