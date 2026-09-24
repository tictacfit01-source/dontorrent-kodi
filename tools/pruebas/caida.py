# -*- coding: utf-8 -*-
"""DonTorrent CAIDO de verdad (dtbl38): todo sin red.

23-09-2026, 16:17: el centro de datos de DonTorrent se quedo sin conexion y su
web contestaba a todo el mundo un 503 "La web volvera enseguida". La ficha de
una serie esperaba 24 s a dos cajas que no podian traer nada y acababa en
"enciende tu Kodi e intentalo de nuevo" -- con la tele encendida. Esto vigila
el arreglo:
  1) que solo cuente como caida lo que DonTorrent DICE (su pagina de
     mantenimiento o un 52x de Cloudflare); un fallo del proxy, un reto o un
     403 no deciden nada;
  2) el estado: se comparte por disco, caduca y se vuelve a mirar;
  3) /catdetail contesta al momento (y si no lo sabia, corta la espera en
     cuanto se entera) y dice la verdad;
  4) /kb/send: por magnet si ya sabemos la huella del torrent; si no, se dice
     sin mandar nada a la tele;
  5) /catsearch, /catbrowse y el aprendiz no mandan trabajo inutil a las cajas.

Escribe en el /tmp del relay (C:\\tmp en Windows): guarda y restaura lo que
hubiera. Nada sale a la red: el proxy apunta a 127.0.0.1:9 y se sustituye.
"""
import http.server
import json
import os
import shutil
import sys
import threading
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"     # nada de red de fondo en la prueba
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

# Pase lo que pase en la prueba, NADA puede llegar a produccion.
A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"
A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"
A._DTCAIDA_PROXY = "http://127.0.0.1:9/?u="

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


FICHEROS = [A._DTCAIDA_FILE, A._DTPACKED_FILE, A._CATDETAIL_FILE, A._CATJOB_FILE,
            A._DT_DOWN_FILE, A._CATSEARCH_FILE, A._CATBROWSE_FILE, A._KB_FILE,
            A._DXIH_FILE, A._SEEDS_FILE]
os.makedirs("/tmp", exist_ok=True)
copia = {}
for f in FICHEROS:
    if os.path.exists(f):
        copia[f] = f + ".prueba_bak"
        shutil.copy(f, copia[f])


def escribe(f, d):
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def borra(f):
    try:
        os.remove(f)
    except Exception:
        pass


# La pagina de mantenimiento, tal cual la sirvio DonTorrent el 23-09 (recortada)
MANT = ('<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex"><meta http-equiv="refresh" content="60">'
        '<title>La web volverá enseguida</title><style>html, body { height: 100%; '
        'margin: 0; } body { font-family: -apple-system, BlinkMacSystemFont, '
        '"Segoe UI", Roboto, Arial, sans-serif; background: #f8fafc; color: '
        '#111827; display: grid; place-items: center; text-align: center; padding: '
        '24px; line-height: 1.55; } main { max-width: 480px; } .logo { width: '
        '240px; max-width: 70vw; height: auto; margin: 0 auto 28px; display: '
        'block; }</style></head><body><main>'
        '<img id="logo" class="logo" src="/__proxy-dok/dontorrent.svg" alt="" hidden>'
        '<h1>La web volverá enseguida</h1><p>Estamos teniendo un problema puntual '
        'en el servidor: puede ser un pico de tráfico o una incidencia en el '
        'centro de datos.</p></main></body></html>')
ANUBIS = ('<html><head><title>Asegurándonos de que no eres un bot</title></head>'
          '<body><script id="anubis_challenge" type="application/json">{}</script>'
          '</body></html>')
IH1 = "ab" * 20
IH2 = "cd" * 20

# --- dobles: ni cajas de verdad, ni DonTorrent, ni proxy -----------------------
ENCOLADOS = []
A._kb_enqueue = lambda code, ev: ENCOLADOS.append((code, dict(ev)))
A._kb_phone_seen = lambda code: None
A._rate_ok = lambda ip: True
A._box_for = lambda code: "111111"
A._live_boxes = lambda *a, **k: ["111111", "222222"]
A._any_live_box = lambda *a, **k: "111111"
REAL_SESSION_GET = A._cat_dt_session_get
A._cat_dt_session_get = lambda path: ("", None)
A._cat_dt_html = lambda q: ""
A._seed_counts_many = lambda ihs, *a, **k: {}

MIRADAS = []
PROXY = {"resp": (MANT, 503), "espera": 0.0}
REAL_GET_CON_TOPE = A._get_con_tope


def get_falso(url, tope_s, headers=None, scraper=None, crudo=False, todo=False):
    MIRADAS.append((url, tope_s, todo))
    if PROXY["espera"]:
        time.sleep(PROXY["espera"])
    return PROXY["resp"]


A._get_con_tope = get_falso


def estado_limpio():
    borra(A._DTCAIDA_FILE)
    A._DTCAIDA.update({"visto": 0.0, "caida": False, "desde": 0.0, "st": 0,
                       "dom": ""})
    A._DTCAIDA_VUELO[0] = 0.0


def estado(caida, hace=0.0, desde_hace=None):
    now = time.time()
    estado_limpio()
    escribe(A._DTCAIDA_FILE, {"visto": now - hace, "caida": caida,
                              "desde": (now - (desde_hace if desde_hace is not None
                                               else hace)) if caida else 0.0,
                              "st": 503 if caida else 200, "dom": "dontorrent.moi"})


class _Srv503(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo = MANT.encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


try:
    print("\n=== 1) Solo cuenta como caida lo que DonTorrent DICE ===")
    casos = [((200, ANUBIS), False, "200 con su reto Anubis = contesta"),
             ((503, MANT), True, "503 con su pagina de mantenimiento"),
             ((502, MANT), True, "502 con su pagina de mantenimiento"),
             ((522, ""), True, "522 de Cloudflare: su servidor no contesta"),
             ((530, None), True, "530 de Cloudflare"),
             ((503, "Service Unavailable"), None, "503 pelado: no se sabe"),
             ((502, "relay error: fetch failed"), None, "fallo de NUESTRO proxy: no se sabe"),
             ((403, "Forbidden"), None, "403: no se sabe"),
             ((429, "Too Many Requests"), None, "429: no se sabe"),
             ((0, None), None, "sin respuesta: no se sabe")]
    for (st, txt), esperado, nombre in casos:
        got = A._dt_caida_clasifica(st, txt)
        comprueba("%s -> %s" % (nombre, esperado), got is esperado, got)

    print("\n=== 2) _get_con_tope(todo=True) lee el cuerpo de un 503 ===")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Srv503)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    txt, st = REAL_GET_CON_TOPE(url, 5.0, todo=True)
    comprueba("con todo=True: (pagina, 503)", st == 503 and "volverá enseguida" in (txt or ""),
              (st, (txt or "")[:60]))
    txt, st = REAL_GET_CON_TOPE(url, 5.0)
    comprueba("sin todo: (None, 503), como siempre", st == 503 and txt is None, (st, txt))
    srv.shutdown()

    print("\n=== 3) Mirar, apuntar y compartir el estado ===")
    estado_limpio()
    del MIRADAS[:]
    PROXY.update(resp=(MANT, 503), espera=0.0)
    e = A._dt_caida_mira()
    comprueba("la mirada va por el proxy, a la portada, leyendo el cuerpo",
              len(MIRADAS) == 1 and MIRADAS[0][0].startswith(A._DTCAIDA_PROXY)
              and "dontorrent" in MIRADAS[0][0] and MIRADAS[0][2] is True, MIRADAS)
    comprueba("mantenimiento -> caida, y apuntada en disco",
              e.get("caida") is True and json.load(open(A._DTCAIDA_FILE)).get("caida") is True)
    comprueba("_dt_caido() dice que si (sin volver a mirar)",
              A._dt_caido() is True and len(MIRADAS) == 1, len(MIRADAS))
    # el OTRO worker se entera por el disco
    A._DTCAIDA.update({"visto": 0.0, "caida": False})
    comprueba("otro worker (memoria vacia) lo lee del disco", A._dt_caida_ya() is True)
    # visto hace 100 s y el proxy no sabe decir nada: lo ultimo vale un rato
    estado(True, hace=100)
    del MIRADAS[:]
    PROXY["resp"] = (None, 0)
    comprueba("caido hace 100 s + mirada sin respuesta -> sigue caido (un rato)",
              A._dt_caido() is True and len(MIRADAS) == 1, len(MIRADAS))
    estado(True, hace=200)
    del MIRADAS[:]
    # dtbl45: una caida vale 15 min mientras se re-mira (antes 150 s, y el
    # aviso del Inicio dejaba de nombrar a DonTorrent la mitad del tiempo)
    comprueba("caido hace 200 s + mirada sin respuesta -> sigue caido (vale 15 min)",
              A._dt_caido() is True and len(MIRADAS) == 1, len(MIRADAS))
    estado(True, hace=16 * 60)
    del MIRADAS[:]
    comprueba("caido hace 16 min + mirada sin respuesta -> ya no se da por caido",
              A._dt_caido() is False and len(MIRADAS) == 1, len(MIRADAS))
    estado(True, hace=100)
    del MIRADAS[:]
    A._dt_caida_sondea()
    time.sleep(0.3)
    comprueba("caido: el sondeo re-mira a los 90 s (no a los 5 min)", len(MIRADAS) == 1,
              len(MIRADAS))
    # ha vuelto
    estado(True, hace=200, desde_hace=3600)
    PROXY["resp"] = (ANUBIS, 200)
    comprueba("caido hace 200 s y ahora contesta -> arriba",
              A._dt_caido() is False and A._DTCAIDA.get("caida") is False
              and not A._DTCAIDA.get("desde"))
    # 'desde' se conserva mientras siga caido
    estado(True, hace=200, desde_hace=3600)
    PROXY["resp"] = (MANT, 503)
    A._dt_caido()
    comprueba("mientras siga caido, 'desde' no se reinicia",
              abs((time.time() - A._DTCAIDA.get("desde", 0)) - 3600) < 5,
              time.time() - A._DTCAIDA.get("desde", 0))
    # sin caida conocida, preguntar NO toca la red
    estado(False, hace=10)
    del MIRADAS[:]
    comprueba("sin caida conocida, _dt_caido() no mira nada",
              A._dt_caido() is False and not MIRADAS)
    # una mirada a la vez
    estado_limpio()
    A._DTCAIDA_VUELO[0] = time.time()
    del MIRADAS[:]
    A._dt_caida_mira()
    comprueba("si ya hay otra mirando, no se lanza otra", not MIRADAS)
    A._DTCAIDA_VUELO[0] = 0.0
    # el sondeo de fondo: solo si hace rato que nadie miraba
    estado(False, hace=10)
    del MIRADAS[:]
    A._dt_caida_sondea()
    time.sleep(0.3)
    comprueba("sondeo con mirada reciente: no hace nada", not MIRADAS)
    estado_limpio()
    A._dt_caida_sondea()
    time.sleep(0.5)
    comprueba("sondeo sin mirada reciente: mira por detras", len(MIRADAS) == 1, MIRADAS)
    # el intento directo de Render tambien se entera (y del regreso)

    class _R(object):
        def __init__(self, st, txt):
            self.status_code, self.text = st, txt

    class _S(object):
        def __init__(self, r):
            self.r = r

        def get(self, *a, **k):
            return self.r
    viejo_ses = A._dt_anubis_session
    borra(A._DT_DOWN_FILE)
    A._DT_DOWN_UNTIL[0] = 0.0
    try:
        estado_limpio()
        A._dt_anubis_session = lambda dom, force=False: (_S(_R(503, MANT)), None)
        REAL_SESSION_GET("/series")
        comprueba("un 503 de mantenimiento en el directo de Render -> caida",
                  A._dt_caida_ya() is True)
        A._dt_anubis_session = lambda dom, force=False: (
            _S(_R(200, '<a href="/serie/1/1/x/">x</a>')), None)
        REAL_SESSION_GET("/series")
        comprueba("y un 200 con fichas -> ha vuelto", A._dt_caida_ya() is False)
    finally:
        A._dt_anubis_session = viejo_ses
        borra(A._DT_DOWN_FILE)
        A._DT_DOWN_UNTIL[0] = 0.0

    cli = A.app.test_client()
    PATH = "/serie/999999/999999/Prueba-Caida"

    print("\n=== 4) /catdetail ===")
    escribe(A._CATDETAIL_FILE, {})
    escribe(A._CATJOB_FILE, {})
    A._CATDETAIL_CACHE.pop(PATH, None)
    estado(True, hace=5)
    del ENCOLADOS[:]
    t0 = time.time()
    js = cli.get("/catdetail?path=%s&code=111111" % PATH).get_json()
    dt = time.time() - t0
    comprueba("caido y sin nada guardado: contesta al momento (%.2f s)" % dt, dt < 1.0, dt)
    comprueba("...con la lista vacia y el aviso", js.get("episodes") == []
              and "dt_caida" in js, js)
    comprueba("...y sin mandar NADA a ninguna caja", not ENCOLADOS, ENCOLADOS)
    viejo = {"title": "Prueba", "episodes": [{"content_id": "1", "tabla": "series",
                                              "label": "1x01", "season": 1,
                                              "episode": 1, "quality": "HDTV"}]}
    A._CATDETAIL_CACHE[PATH] = {"data": viejo, "ts": time.time() - 30 * 86400}
    js = cli.get("/catdetail?path=%s&code=111111" % PATH).get_json()
    comprueba("caido pero con lo de la ultima vez: se sirve, marcado viejo y con aviso",
              len(js.get("episodes") or []) == 1 and js.get("stale") is True
              and "dt_caida" in js, js)
    A._CATDETAIL_CACHE.pop(PATH, None)
    # no se sabia: las cajas ya van, pero la espera se corta en cuanto se sabe
    estado_limpio()
    escribe(A._CATDETAIL_FILE, {})
    del ENCOLADOS[:]
    del MIRADAS[:]
    PROXY.update(resp=(MANT, 503), espera=0.6)
    t0 = time.time()
    js = cli.get("/catdetail?path=%s&code=111111" % PATH).get_json()
    dt = time.time() - t0
    PROXY["espera"] = 0.0
    comprueba("sin saberlo: se entera por detras y corta la espera (%.2f s, antes 24)" % dt,
              dt < 4.0 and "dt_caida" in js and js.get("episodes") == [], (dt, js))
    comprueba("...las dos cajas habian recibido su trabajo (el caso de siempre)",
              len([e for c, e in ENCOLADOS if e.get("op") == "dthtml"]) == 2, ENCOLADOS)
    comprueba("...y se miro UNA vez", len(MIRADAS) == 1, MIRADAS)

    print("\n=== 4b) Una caja trae la pagina de mantenimiento como si fuera buena ===")
    FICHA = '<html><a href="/serie/1/1/x">x</a> La web volverá enseguida </html>'
    comprueba("reconoce su pagina de mantenimiento", A._dt_es_mantenimiento(MANT) is True)
    comprueba("no confunde su reto Anubis", A._dt_es_mantenimiento(ANUBIS) is False)
    comprueba("ni una pagina suya CON fichas aunque diga la frase",
              A._dt_es_mantenimiento(FICHA) is False)
    comprueba("ni una pagina grande", A._dt_es_mantenimiento(MANT + "x" * 70000) is False)
    comprueba("ni nada", A._dt_es_mantenimiento("") is False
              and A._dt_es_mantenimiento(None) is False)
    estado_limpio()
    escribe(A._CATDETAIL_FILE, {})
    escribe(A._CATJOB_FILE, {})
    del ENCOLADOS[:]
    PROXY.update(resp=(None, 0), espera=0.0)     # el proxy no sabe decir nada
    TMDB = []
    viejo_tmdb = A._cat_tmdb
    A._cat_tmdb = lambda t, k="tv": TMDB.append(t) or {}

    def caja_contesta():
        fin = time.time() + 6
        while time.time() < fin:
            jobs = [e.get("job") for c, e in list(ENCOLADOS) if e.get("op") == "dthtml"]
            if jobs:
                time.sleep(0.2)
                A.app.test_client().post("/catjob/done", json={"job": jobs[0], "html": MANT})
                return
            time.sleep(0.05)
    threading.Thread(target=caja_contesta, daemon=True).start()
    t0 = time.time()
    js = cli.get("/catdetail?path=%s&code=111111" % PATH).get_json()
    dt = time.time() - t0
    A._cat_tmdb = viejo_tmdb
    comprueba("la caja trae el mantenimiento: se sabe caido y se corta (%.2f s)" % dt,
              dt < 4.0 and "dt_caida" in js and js.get("episodes") == [], (dt, js))
    comprueba("...sin leerlo como una ficha (ni titulo raro ni TMDB)",
              "volver" not in str(js.get("title") or "") and not TMDB, (js, TMDB))
    e = A._dt_caida_lee()
    comprueba("...y queda apuntado, por la caja", e.get("caida") is True
              and e.get("dom") == "caja", e)
    estado_limpio()
    r = cli.post("/catfeed", json={"kind": "estrenos", "html": MANT, "ficha": 1})
    comprueba("/catfeed con el mantenimiento: no se guarda y se apunta la caida",
              (r.get_json() or {}).get("items") == 0 and A._dt_caida_ya() is True,
              r.get_json())

    print("\n=== 5) /kb/send con DonTorrent caido ===")
    now = time.time()
    escribe(A._DTPACKED_FILE, {
        "peliculas:123": {"ih": IH1, "p": False, "q": "1080p", "ts": now},
        "peliculas:124": {"ih": IH2, "p": True, "q": "1080p", "ts": now}})

    def envia(cid, t="Una peli"):
        r = cli.post("/kb/send", json={"code": "100001", "cmd": "play_ref", "a": "dt",
                                       "c": cid, "tb": "peliculas", "t": t},
                     headers={"X-Forwarded-For": "10.9.8.7"})
        return r.status_code, (r.get_json() or {})

    estado(True, hace=5)
    del ENCOLADOS[:]
    st, js = envia("123", "Una peli (2025)")
    ev = ENCOLADOS[-1][1] if ENCOLADOS else {}
    comprueba("con la huella sabida: sale por magnet y se dice",
              st == 200 and js.get("ok") is True and js.get("via") == "magnet", (st, js))
    comprueba("...la tele recibe un play de magnet (no el de DonTorrent)",
              ev.get("c") == "play_ref" and ev.get("a") == "pl"
              and ev.get("u", "").startswith("magnet:?xt=urn:btih:" + IH1)
              and "cid" not in ev, ev)
    comprueba("...con los trackers de las semillas y el titulo",
              ev.get("u", "").count("&tr=") == len(A._SEED_TRACKERS)
              and "opentrackr" in ev.get("u", "") and "&dn=Una%20peli" in ev.get("u", ""),
              ev.get("u"))
    del ENCOLADOS[:]
    st, js = envia("124")
    comprueba("en RAR: no se manda (Elementum no lo abriria) y se dice",
              js.get("ok") is False and js.get("error") == "dt_caida" and not ENCOLADOS,
              (js, ENCOLADOS))
    st, js = envia("125")
    comprueba("sin huella: no se manda nada a la tele y se dice",
              js.get("ok") is False and js.get("error") == "dt_caida"
              and "dt_caida" in js and not ENCOLADOS, (js, ENCOLADOS))
    estado(False, hace=5)
    st, js = envia("125")
    ev = ENCOLADOS[-1][1] if ENCOLADOS else {}
    comprueba("DonTorrent arriba: el play de siempre, sin tocar",
              js.get("ok") is True and "via" not in js and ev.get("a") == "dt"
              and ev.get("cid") == "125", (js, ev))

    print("\n=== 6) /catsearch y /catbrowse ===")
    ITEM = {"title": "Prueba caida", "source": "dt", "kind": "movie",
            "content_id": "777", "tabla": "peliculas", "poster": "https://x/p.jpg",
            "rating": 7.1, "year": "2024", "quality": "1080p"}
    viejos = {k: getattr(A, k) for k in ("_cat_from_cache", "_dtq_get", "_et_search",
                                         "_dx_search_items", "_sapi_credits_ok",
                                         "_tmdb_alt_titles", "_cat_disambiguate_years",
                                         "_cat_enrich")}
    A._cat_from_cache = lambda q: [dict(ITEM)]
    A._dtq_get = lambda q: []
    A._et_search = lambda q: []
    A._dx_search_items = lambda q, proxy=False: []
    A._sapi_credits_ok = lambda: False
    A._tmdb_alt_titles = lambda q: []
    A._cat_disambiguate_years = lambda it, dl, box=None, cap=12: (it, True)
    A._cat_enrich = lambda it, limit=None: it
    try:
        escribe(A._CATSEARCH_FILE, {})
        A._CATSEARCH_CACHE.clear()
        estado(True, hace=5)
        del ENCOLADOS[:]
        t0 = time.time()
        js = cli.get("/catsearch?q=prueba%20caida&code=111111").get_json()
        dt = time.time() - t0
        comprueba("caido: la busqueda NO pregunta a ninguna caja por DonTorrent",
                  not [e for c, e in ENCOLADOS if e.get("op") == "dthtml"], ENCOLADOS)
        comprueba("...sale rapido (%.2f s) con lo guardado y el aviso" % dt,
                  dt < 5.0 and len(js.get("items") or []) == 1 and "dt_caida" in js,
                  (dt, js))
        js = cli.get("/catsearch?q=prueba%20caida&code=111111").get_json()
        comprueba("...y desde la cache, tambien con el aviso",
                  js.get("cached") is True and "dt_caida" in js, js)
        estado(False, hace=5)
        js = cli.get("/catsearch?q=prueba%20caida&code=111111").get_json()
        comprueba("DonTorrent arriba: sin aviso", "dt_caida" not in js, js)

        A._CATBROWSE_CACHE["estrenos:1"] = {"items": [dict(ITEM)],
                                            "ts": time.time() - 7200}
        estado(True, hace=5)
        js = cli.get("/catbrowse?kind=estrenos&page=1").get_json()
        comprueba("el Inicio (DonTorrent de hace un rato) lleva el aviso",
                  js.get("stale") is True and "dt_caida" in js and js.get("items"), js)
        estado(False, hace=5)
        js = cli.get("/catbrowse?kind=estrenos&page=1").get_json()
        comprueba("...y sin caida, no", "dt_caida" not in js, js)
    finally:
        for k, v in viejos.items():
            setattr(A, k, v)
        A._CATBROWSE_CACHE.pop("estrenos:1", None)
        A._CATSEARCH_CACHE.clear()

    print("\n=== 7) El aprendiz no pide PoW de DonTorrent si esta caido ===")
    PEDIDOS = []
    viejo_items, viejo_dt = A._apr_items_inicio, A._apr_dt
    A._apr_items_inicio = lambda: [{"title": "Sin huella", "source": "dt",
                                    "kind": "movie", "content_id": "999",
                                    "tabla": "peliculas"}]
    A._apr_dt = lambda k: PEDIDOS.append(k) or {"ih": IH1, "p": False, "q": ""}
    A._APR_T_DT[0] = 0.0
    A._APR_T_REF[0] = time.time()
    A._APR_NEG.clear()
    try:
        estado(True, hace=5)
        A._apr_ronda()
        comprueba("caido: ni un trabajo de DonTorrent", not PEDIDOS, PEDIDOS)
        estado(False, hace=5)
        A._APR_T_DT[0] = 0.0
        A._apr_ronda()
        comprueba("arriba: el de siempre", PEDIDOS == ["peliculas:999"], PEDIDOS)
    finally:
        A._apr_items_inicio, A._apr_dt = viejo_items, viejo_dt

    print("\n=== 7b) /dtmagnet: lo que pide la caja cuando no baja el .torrent ===")
    js = cli.get("/dtmagnet?c=123&tb=peliculas&t=Una%20peli").get_json()
    comprueba("con la huella sabida: el magnet", js.get("magnet", "").startswith(
        "magnet:?xt=urn:btih:" + IH1 + "&dn=Una%20peli"), js)
    comprueba("sin huella, en RAR o sin id: vacio",
              cli.get("/dtmagnet?c=125&tb=peliculas").get_json() == {"magnet": ""}
              and cli.get("/dtmagnet?c=124&tb=peliculas").get_json() == {"magnet": ""}
              and cli.get("/dtmagnet?tb=peliculas").get_json() == {"magnet": ""})

    print("\n=== 8) El magnet, a mano ===")
    m = A._dt_magnet("123", "peliculas", "Título con tilde")
    comprueba("magnet con la huella, el titulo codificado y 3 trackers",
              m.startswith("magnet:?xt=urn:btih:" + IH1 + "&dn=T%C3%ADtulo%20con%20tilde")
              and m.count("&tr=udp%3A%2F%2F") == 3, m)
    comprueba("sin huella o en RAR: None",
              A._dt_magnet("125", "peliculas") is None
              and A._dt_magnet("124", "peliculas") is None)
finally:
    for f in FICHEROS:
        try:
            if f in copia:
                shutil.move(copia[f], f)
            elif os.path.exists(f):
                os.remove(f)
        except Exception:
            pass

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: con DonTorrent caido, la web lo dice al momento y no molesta a nadie")
