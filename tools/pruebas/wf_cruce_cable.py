# -*- coding: utf-8 -*-
"""El cruce WolfMax-DonTorrent POR EL CABLE (dtbl80). Sin red.

dtbl79 desplego el cruce y no aprendio nada: la caja 2.9.90 mandaba el nombre y
los bytes del .torrent, pero /catjob/done guardaba una lista FIJA de campos y
los tiraba. wf_cruce_dt.py estaba en verde porque simulaba _catjob_wait. Aqui no
se simula ninguna etapa del camino:

  caja REAL (service._do_etjob -> _dt_meta -> remote_kb.push_etjob, codigo 2.9.90)
    -> HTTP (cliente de Flask) -> /catjob/done REAL -> _catjob_wait REAL
    -> aprendiz REAL (_apr_ronda, _apr_dt, _apr_guarda) -> regla y cruce REALES

Solo es de mentira lo que sale de casa: DonTorrent (la prueba de trabajo y la
descarga del .torrent devuelven unos bytes de .torrent de verdad) y el reparto
de cajas. Se comprueba:
  1) los campos llegan con el nombre y el tipo que manda la caja (n texto, b entero)
     y la ficha de WolfMax queda atada con la huella REAL de esos bytes;
  2) /catjob/done rechaza n y b con mala forma, y deja los demas campos como antes;
  3) una caja anterior (sin n ni b) no da error ni provoca bucles;
  4) tras un despliegue (la espera de 12 h se borra) no hay avalancha: un trabajo
     por vuelta y las de DonTorrent espaciadas.
"""
import os
import sys
import tempfile
import threading
import time
import types
from urllib.parse import urlparse

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.abspath(os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf"))
RELAY = os.path.abspath(os.path.join(AQUI, "..", "..", "render_relay"))
PERFIL = tempfile.mkdtemp(prefix="mw_perfil_")
TMP = tempfile.mkdtemp(prefix="mw_cable_")

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


# --- Kodi de mentira (como salud_cajas.py) --------------------------------------
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
xbmc.Player = type("Player", (), {"isPlaying": lambda self: False})
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getAddonInfo": lambda self, k: {"profile": PERFIL, "version": "2.9.90"}.get(k, "")})
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
sys.path.insert(0, ADDON)
sys.path.insert(0, os.path.join(ADDON, "resources"))
sys.path.insert(0, RELAY)

import service                                           # noqa: E402  la caja
from resources.lib import remote_kb as rkb               # noqa: E402
from resources.lib import scraper_dontorrent as sdt      # noqa: E402
from resources.lib import http_session as hs             # noqa: E402
import app as A                                          # noqa: E402  el relay

GIB = 1024 ** 3
P = "https://wolfmax4k.com/pelicula/"


def benc(x):
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode("utf-8")
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(benc(v) for v in x) + b"e"
    return b"d" + b"".join(benc(k) + benc(x[k]) for k in sorted(x)) + b"e"


def torrent(nombre, largo):
    return benc({"announce": "udp://x", "info": {"name": nombre, "length": largo,
                                                 "piece length": 16384, "pieces": b"\1" * 20}})


# --- Lo que sale de casa, de mentira ---------------------------------------------
TORRENTS = {}                     # cid -> bytes del .torrent que "da" DonTorrent
CABLE = []                        # lo que la caja mando por HTTP, tal cual


class _Resp(object):
    def __init__(self, content):
        self.content = content
        self.status_code = 200


sdt.resolve_torrent = lambda cid, tb, prefer_direct=True: (
    "https://dontorrent.test/torrents/peliculas/%s.torrent" % cid) if cid in TORRENTS else ""
hs.make_session = lambda *a, **k: object()
hs.get = lambda sess, url, **k: _Resp(TORRENTS[url.rsplit("/", 1)[1][:-8]])

cli = A.app.test_client()


class _HTTP(object):
    """La sesion de la caja: lo que posta va al relay REAL por el cliente de Flask."""
    def post(self, url, json=None, timeout=None):
        CABLE.append(dict(json))
        return cli.post(urlparse(url).path, json=json)


rkb.relay_base = lambda: "https://relay.test"
rkb.get_code = lambda: "123456"
rkb._SESSION = _HTTP()

# --- El relay: sus ficheros a un sitio temporal; las cajas = la caja de arriba ----
viejos = {k: getattr(A, k) for k in ("_CATJOB_FILE", "_DTPACKED_FILE", "_WFIH_FILE", "_WFULT_FILE",
                                     "_DXIH_FILE", "_SEEDS_FILE", "_apr_caja", "_lend_acquire",
                                     "_lend_release", "_kb_enqueue", "_seed_counts_many", "_seeds_guarda",
                                     "_apr_items_inicio", "_semillas_nube_sube", "_dt_caida_ya")}
ENCOLADOS = []
CAJA_VIEJA = [False]


def _a_la_caja(box, ev):
    """El relay deja un trabajo a la caja: la caja lo hace (en su hilo, como
    siempre) y contesta por HTTP a /catjob/done."""
    ENCOLADOS.append(dict(ev))
    if CAJA_VIEJA[0]:
        # caja 2.9.89: contesta lo de siempre, sin n ni b, por el mismo cable
        cuerpo = {"job": ev["job"], "op": ev["op"], "rar": False, "quality": "BluRay",
                  "ih": "ab" * 20, "code": "123456"}
        threading.Thread(target=lambda: (CABLE.append(cuerpo), cli.post("/catjob/done", json=cuerpo))).start()
        return
    service._do_etjob(ev)


try:
    A._CATJOB_FILE = os.path.join(TMP, "catjob.json")
    A._DTPACKED_FILE = os.path.join(TMP, "dtp.json")
    A._WFIH_FILE = os.path.join(TMP, "wfih.json")
    A._WFULT_FILE = os.path.join(TMP, "wfult.json")
    A._DXIH_FILE = os.path.join(TMP, "dxih.json")
    A._SEEDS_FILE = os.path.join(TMP, "seeds.json")
    A._apr_caja = lambda: "123456"
    A._lend_acquire = lambda box: object()
    A._lend_release = lambda s: None
    A._kb_enqueue = _a_la_caja
    A._seed_counts_many = lambda ihs: {}
    A._seeds_guarda = lambda *a, **k: None
    A._semillas_nube_sube = lambda *a, **k: 0
    A._dt_caida_ya = lambda: False
    A._WFIH_MEM.update(mt=-1.0, d=None)
    A._WFULT.clear()
    A._WFULT_MT[0] = 0.0
    A._WFULT["movie"] = {"ts": time.time(), "items": [
        {"title": "Peau a peau", "url": P + "pp1111", "quality": "1080p", "size": "4,82 GB",
         "source": "wf", "kind": "movie"},
        {"title": "Animales", "url": P + "an2222", "quality": "720p", "size": "5,08 GB",
         "source": "wf", "kind": "movie"}]}
    A._wfult_guarda()
    BYTES_PP = int(4.82 * GIB)
    TORRENTS["31078"] = torrent("Peau a peau (2025) [BluRay 1080p][Esp]", BYTES_PP)
    TORRENTS["31069"] = torrent("Animales (2026) [DVDRip][Esp]", int(1.4 * GIB))   # otra version
    HUELLA_PP = A._torrent_lee(TORRENTS["31078"])["ih"]
    ITEMS = [{"source": "dt", "content_id": "31078", "tabla": "peliculas", "kind": "movie", "title": "Peau a peau"},
             {"source": "dt", "content_id": "31069", "tabla": "peliculas", "kind": "movie", "title": "Animales"}]
    A._apr_items_inicio = lambda: list(ITEMS)

    print("\n=== 1) De la caja 2.9.90 al cruce, sin saltarse nada ===")
    A._APR_NEG.clear()
    A._APR_T_DT[0] = 0.0
    A._WF_CRUCE_T[0] = time.time()          # el barrido de cada 10 min, aparte
    A._apr_ronda()
    cuerpo = next((c for c in CABLE if c.get("op") == "dtmeta"), {})
    comprueba("la caja hizo el trabajo dtmeta que le dejo el aprendiz",
              ENCOLADOS and ENCOLADOS[0].get("op") == "dtmeta" and ENCOLADOS[0].get("cid") == "31078", ENCOLADOS)
    comprueba("y lo mando por HTTP con n (texto) y b (entero), como en la 2.9.90",
              cuerpo.get("n") == "Peau a peau (2025) [BluRay 1080p][Esp]" and type(cuerpo.get("b")) is int
              and cuerpo.get("b") == BYTES_PP and cuerpo.get("ih") == HUELLA_PP, cuerpo)
    e = A._dtpacked_load().get("peliculas:31078") or {}
    comprueba("/catjob/done los dejo pasar y el aprendiz los guardo",
              e.get("n") == cuerpo.get("n") and e.get("b") == BYTES_PP and e.get("ih") == HUELLA_PP, e)
    w = A._wfih_de(P + "pp1111") or {}
    comprueba("la ficha de WolfMax queda atada con la huella REAL de esos bytes, origen 'dt'",
              w.get("ih") == HUELLA_PP and w.get("o") == "dt", w)
    comprueba("/catdiag lo cuenta: dt_con_nombre y origen 'dt'",
              (cli.get("/catdiag").get_json().get("semillas") or {}).get("dt_con_nombre") == 1
              and (cli.get("/catdiag").get_json().get("wfih") or {}).get("origen", {}).get("dt") == 1)
    # la otra: DonTorrent tiene OTRA version (DVDRip) -> llega bien, pero no se ata
    A._APR_T_DT[0] = 0.0
    A._apr_ronda()
    e2 = A._dtpacked_load().get("peliculas:31069") or {}
    comprueba("otra version (DVDRip 1,4 GB frente a 720p 5,08 GB): su nombre llega, pero NO se ata",
              e2.get("b") == int(1.4 * GIB) and not A._wfih_de(P + "an2222"), (e2, A._wfih_de(P + "an2222")))

    print("\n=== 2) /catjob/done: n y b solo con su forma; lo demas, como antes ===")
    def deja(**campos):
        cli.post("/catjob/done", json=dict({"job": "prueba1"}, **campos))
        return A._catjob_wait("prueba1", 2.0) or {}
    malos = [(123, 5), ("", 5), ("   ", 5), ("x" * 201, 5), ("ok", -1), ("ok", 0), ("ok", "123"),
             ("ok", True), ("ok", 1.5), ("ok", 2 ** 60), (None, None), (["ok"], 5)]
    res = [(n, b, deja(n=n, b=b, ih="cd" * 20)) for n, b in malos]
    rotos = [(n, b, (r.get("n"), r.get("b"))) for n, b, r in res
             if (r.get("n") not in (None, "ok")) or (r.get("b") is not None and not (isinstance(b, int) and b > 0))]
    comprueba("con mala forma (no texto, vacio, >200, negativo, 0, texto, bool, decimal, enorme): se descartan",
              not rotos and all(r.get("ih") == "cd" * 20 for _n, _b, r in res), rotos)
    r = deja(n="  Una peli [1080p]  ", b=12345, ih="ef" * 20)
    comprueba("con buena forma: pasan (el nombre sin espacios de los lados)",
              r.get("n") == "Una peli [1080p]" and r.get("b") == 12345, r)
    r = deja(n="x" * 200, b=1)
    comprueba("200 caracteres justos: valen", r.get("n") == "x" * 200 and r.get("b") == 1, r)
    todo = {"items": [{"title": "t"}], "link": "magnet:?xt=urn:btih:" + "a" * 40, "rar": True,
            "quality": "4K", "eps": [{"e": 1}], "ih": "a" * 40, "html": "<p>", "error": "captcha", "minutos": 7}
    r = deja(**todo)
    comprueba("los demas campos, exactamente como antes",
              all(r.get(k) == v for k, v in todo.items()) and r.get("n") is None and r.get("b") is None, r)
    r = deja(error="otra cosa", minutos=9999)
    comprueba("...incluidas sus validaciones (error fuera de la lista y minutos con tope)",
              r.get("error") is None and r.get("minutos") == 600, r)

    print("\n=== 3) Una caja sin actualizar (2.9.89) ===")
    ITEMS[:] = [{"source": "dt", "content_id": "40001", "tabla": "peliculas", "kind": "movie",
                 "title": "Peau a peau 2"}]
    A._WFULT["movie"]["items"].append({"title": "Peau a peau 2", "url": P + "pp2222", "quality": "1080p",
                                       "size": "3,00 GB", "source": "wf", "kind": "movie"})
    A._wfult_guarda()
    CAJA_VIEJA[0] = True
    A._APR_T_DT[0] = 0.0
    try:
        A._apr_ronda()
        ok = True
    except Exception as ex:
        ok = ex
    e3 = A._dtpacked_load().get("peliculas:40001") or {}
    comprueba("contesta sin n ni b por el mismo cable: sin error, su huella guardada y sin nombre",
              ok is True and e3.get("ih") == "ab" * 20 and "n" not in e3 and "b" not in e3, (ok, e3))
    comprueba("...y no se le vuelve a pedir en 12 h", (A._APR_NEG.get("nb:peliculas:40001") or (0, 0))[1] == 12 * 3600)
    CAJA_VIEJA[0] = False

    print("\n=== 4) Tras un despliegue: sin avalancha ===")
    ITEMS[:] = [{"source": "dt", "content_id": str(50000 + i), "tabla": "peliculas", "kind": "movie",
                 "title": "Peli %d" % i} for i in range(20)]
    A._WFULT["movie"]["items"] += [{"title": "Peli %d" % i, "url": P + "zz%04d" % i, "quality": "1080p",
                                    "size": "2,00 GB", "source": "wf", "kind": "movie"} for i in range(20)]
    A._wfult_guarda()
    for i in range(20):
        TORRENTS[str(50000 + i)] = torrent("Peli %d [1080p]" % i, 3 * GIB)
    A._APR_NEG.clear()                       # lo que hace un despliegue
    A._APR_T_DT[0] = 0.0
    antes = len(ENCOLADOS)
    for _ in range(5):
        A._apr_ronda()                       # cinco vueltas seguidas, sin esperar
    comprueba("20 pendientes y cinco vueltas seguidas: UN solo trabajo a DonTorrent (espaciado 120 s)",
              len(ENCOLADOS) - antes == 1, len(ENCOLADOS) - antes)
    comprueba("...el ritmo de siempre: como mucho 30 por hora", A._APR_PAUSA_DT >= 120)
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._WFULT.clear()
    A._WFIH_MEM.update(mt=-1.0, d=None)
    A._APR_NEG.clear()

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el nombre y el tamaño llegan de la caja al cruce por el camino real")
