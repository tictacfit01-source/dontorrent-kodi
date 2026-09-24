# -*- coding: utf-8 -*-
"""WolfMax y EliteTorrent caidos, y la lista COMPLETA de capitulos (dtbl40): sin red.

24-09-2026, 07:07: seguian caidos DonTorrent (503), WolfMax y EliteTorrent (522:
el mismo centro de datos). La web decia en el Inicio "las demas fuentes
funcionan" (solo quedaba DivxTotal); la ficha de Ted Lasso de WolfMax enseñaba
los 15 capitulos del indice como si fueran todos (son unos 35); y reproducir de
WolfMax acababa a los 18 s en "¿box encendido?". Esto vigila:
  1) solo un 52x (o la pagina de mantenimiento) cuenta como caida; lo demas no
     decide nada; y el estado se comparte y caduca;
  2) el aviso sabe QUE esta caido y que SI contesta;
  3) la lista completa que trae una caja se guarda, se SUMA (nunca encoge) y
     olvida lo que lleva 30 dias sin verse;
  4) /catboxeps: la de hace poco al momento; con la fuente caida, la de la
     ultima vez o lo que haya, y dicho; lo que traiga una caja, guardado;
  5) /catetbox y el Inicio: las tarjetas con la lista completa;
  6) /catetboxresolve: con la fuente caida se dice al momento.
Escribe en el /tmp del relay (C:\\tmp): guarda y restaura lo que hubiera.
"""
import json
import os
import shutil
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"
A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"
A._DTCAIDA_PROXY = "http://127.0.0.1:9/?u="

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


FICHEROS = [A._FC_FILE, A._EPSC_FILE, A._DTCAIDA_FILE, A._CATBOX_FILE
            if hasattr(A, "_CATBOX_FILE") else A._FC_FILE + ".x"]
os.makedirs("/tmp", exist_ok=True)
copia = {}
for f in FICHEROS:
    if os.path.exists(f):
        copia[f] = f + ".prueba_bak"
        shutil.copy(f, copia[f])

PROXY = {"resp": (None, 522)}
MIRADAS = []


def get_falso(url, tope_s, headers=None, scraper=None, crudo=False, todo=False):
    MIRADAS.append(url)
    return PROXY["resp"]


A._get_con_tope = get_falso


def limpia_estado():
    for f in (A._FC_FILE, A._DTCAIDA_FILE):
        try:
            os.remove(f)
        except Exception:
            pass
    A._FC.clear()
    A._FC_VUELO.clear()
    A._DTCAIDA.update({"visto": 0.0, "caida": False, "desde": 0.0, "st": 0, "dom": ""})
    A._DTCAIDA_VUELO[0] = 0.0
    del MIRADAS[:]


def limpia_eps():
    try:
        os.remove(A._EPSC_FILE)
    except Exception:
        pass
    A._EPSC_MEM.update({"mtime": -1.0, "d": {}})


def ep(s, e, q="4K", url=None):
    return {"label": "%dx%02d" % (s, e), "season": s, "episode": e, "quality": q,
            "url": url or "https://wolfmax4k.com/serie/x/%dx%02d" % (s, e),
            "content_id": url or "https://wolfmax4k.com/serie/x/%dx%02d" % (s, e),
            "src": "wf"}


SERIE = "https://wolfmax4k.com/serie-online-4k/270209"
INDICE = [ep(2, i) for i in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12)] + [ep(4, i) for i in (1, 4, 5, 6)]
COMPLETA = [ep(t, i) for t, n in ((1, 10), (2, 12), (3, 12), (4, 7)) for i in range(1, n + 1)]

try:
    print("\n=== 1) Solo cuenta como caida lo que la fuente dice sin dudas ===")
    for st, esperado, nombre in ((522, True, "522 (Cloudflare no llega a su servidor)"),
                                 (530, True, "530"), (200, False, "200"),
                                 (403, None, "403: no se sabe"),
                                 (0, None, "sin respuesta: no se sabe")):
        limpia_estado()
        PROXY["resp"] = ("<html></html>", st) if st else (None, 0)
        A._fc_mira("wf")
        e = A._fc_lee().get("wf")
        got = None if not e else e.get("caida")
        comprueba("WolfMax %s -> %s" % (nombre, esperado), got is esperado, e)
    comprueba("la mirada va a su portada por el proxy",
              MIRADAS and "wolfmax4k.com" in MIRADAS[-1], MIRADAS)
    limpia_estado()
    PROXY["resp"] = (None, 522)
    A._fc_mira("et")
    A._FC.clear()                       # el otro worker: memoria vacia
    comprueba("el estado se comparte por disco", A._fc_ya("et") is True)
    e = A._fc_lee()["et"]
    e["visto"] = time.time() - 200
    with open(A._FC_FILE, "w") as fh:
        json.dump({"et": e}, fh)
    A._FC.clear()
    PROXY["resp"] = ("<html>ok</html>", 200)
    comprueba("caida vieja + ahora contesta -> ya no esta caida", A._fc_caido("et") is False)
    limpia_estado()
    comprueba("sin caida conocida, preguntar no toca la red",
              A._fc_caido("wf") is False and not MIRADAS)

    print("\n=== 2) El aviso sabe que esta caido y que no ===")
    limpia_estado()
    PROXY["resp"] = (None, 522)
    A._fc_mira("wf")
    A._fc_mira("et")
    A._dt_caida_apunta(True, 503, "dontorrent.moi")
    viejo_dx = A._dx_is_down
    A._dx_is_down = lambda: False
    try:
        d = A._con_caida({})
        comprueba("caidas: DonTorrent, WolfMax y EliteTorrent",
                  d.get("caidas") == ["dt", "wf", "et"], d)
        comprueba("vivas: solo DivxTotal (lo que se sabe)", d.get("vivas") == ["dx"], d)
        A._dx_is_down = lambda: True
        d = A._con_caida({})
        comprueba("si tampoco se sabe de DivxTotal, no se afirma nada", d.get("vivas") == [], d)
    finally:
        A._dx_is_down = viejo_dx
    limpia_estado()
    comprueba("sin caidas, nada que avisar", "caidas" not in A._con_caida({}))

    print("\n=== 3) La lista completa: se guarda, se suma y no encoge ===")
    limpia_eps()
    A._epsc_put("wf", SERIE, "Ted Lasso", COMPLETA)
    c = A._epsc_get("wf", SERIE.replace("://", "://www."))
    comprueba("guardada (y con o sin www es la misma)", c and len(c["eps"]) == 41, c and len(c["eps"]))
    A._epsc_put("wf", SERIE, "Ted Lasso", INDICE)
    comprueba("una caja con la lista a medias NO la encoge",
              len(A._epsc_get("wf", SERIE)["eps"]) == 41)
    A._epsc_put("wf", SERIE, "Ted Lasso", [ep(4, 8)])
    comprueba("un capitulo nuevo se suma", len(A._epsc_get("wf", SERIE)["eps"]) == 42)
    d = A._epsc_load()
    k = A._epsc_clave("wf", SERIE)
    for e in d[k]["eps"]:
        if e["label"] == "1x01":
            e["_ts"] = time.time() - 31 * 86400
    with open(A._EPSC_FILE, "w") as fh:
        json.dump(d, fh)
    A._EPSC_MEM["mtime"] = -1.0
    A._epsc_put("wf", SERIE, "Ted Lasso", [ep(4, 8)])
    labels = [e["label"] for e in A._epsc_get("wf", SERIE)["eps"]]
    comprueba("lo que lleva 30 dias sin verse, fuera", "1x01" not in labels and len(labels) == 41,
              len(labels))
    comprueba("se sirve sin la marca interna", all("_ts" not in e for e in
                                                   A._epsc_limpia(A._epsc_get("wf", SERIE)["eps"])))
    limpia_eps()

    cli = A.app.test_client()
    LLAMADAS = []
    IMPL = {"r": None}
    viejo_impl = A._catboxeps_impl

    def impl_falso():
        LLAMADAS.append(1)
        return A.jsonify(IMPL["r"])
    A._catboxeps_impl = impl_falso
    A._box_eps_by_title = lambda code, src, t, wait=None, cache_only=False: list(INDICE)

    def pide():
        return cli.get("/catboxeps?full=1&code=111111&src=wf&t=Ted%20Lasso&url=" + SERIE).get_json()

    print("\n=== 4) /catboxeps ===")
    try:
        limpia_estado()
        limpia_eps()
        IMPL["r"] = {"title": "Ted Lasso", "episodes": COMPLETA, "via": "caja1:41/1.2s>fin:1.4s"}
        js = pide()
        comprueba("una caja trae la lista completa: se sirve y se guarda",
                  len(js.get("episodes") or []) == 41 and A._epsc_get("wf", SERIE), js.get("via"))
        del LLAMADAS[:]
        js = pide()
        comprueba("la siguiente vez (menos de 6 h): al momento, sin cajas",
                  len(js.get("episodes") or []) == 41 and not LLAMADAS
                  and js.get("via") == "lista-guardada", (LLAMADAS, js.get("via")))
        d = A._epsc_load()
        d[A._epsc_clave("wf", SERIE)]["ts"] = time.time() - 7 * 3600
        with open(A._EPSC_FILE, "w") as fh:
            json.dump(d, fh)
        A._EPSC_MEM["mtime"] = -1.0
        PROXY["resp"] = (None, 522)
        IMPL["r"] = {"title": "Ted Lasso", "episodes": INDICE,
                     "via": "caja1:0/5.2s>caja2:0/12.4s>titulo:15/12.4s"}
        del LLAMADAS[:]
        js = pide()
        comprueba("vieja + las cajas no pueden: la de la ultima vez, entera (41)",
                  len(js.get("episodes") or []) == 41 and js.get("stale") is True, js)
        comprueba("...y como WolfMax da 522, dicho (y no 'parcial')",
                  js.get("fuente_caida") == "wf" and not js.get("parcial"), js)
        del LLAMADAS[:]
        js = pide()
        comprueba("ya sabida la caida: ni se pregunta a las cajas",
                  not LLAMADAS and js.get("fuente_caida") == "wf"
                  and len(js.get("episodes") or []) == 41 and js.get("stale") is True,
                  (LLAMADAS, js))
        limpia_eps()
        js = pide()
        comprueba("caida y sin lista guardada: lo del indice, marcado a medias",
                  len(js.get("episodes") or []) == 15 and js.get("parcial") is True
                  and js.get("fuente_caida") == "wf", js)
        limpia_estado()
        PROXY["resp"] = ("<html>ok</html>", 200)
        IMPL["r"] = {"title": "Ted Lasso", "episodes": INDICE,
                     "via": "caja1:0/5.2s>caja2:0/12.4s>titulo:15/12.4s"}
        js = pide()
        comprueba("fuente viva pero la caja no pudo: lo que haya, SIN inventar caida",
                  len(js.get("episodes") or []) == 15 and "fuente_caida" not in js
                  and not A._epsc_get("wf", SERIE), js)
        js = cli.get("/catboxeps?code=111111&src=dx&url=https://divxtotal.foo/serie/x").get_json()
        comprueba("DivxTotal no pasa por aqui (va directo, como siempre)", LLAMADAS, LLAMADAS)
    finally:
        A._catboxeps_impl = viejo_impl

    print("\n=== 5) Las tarjetas (busqueda e Inicio), con la lista completa ===")
    limpia_estado()
    limpia_eps()
    A._epsc_put("wf", SERIE, "Ted Lasso", COMPLETA)
    TARJETA = {"title": "Ted Lasso", "source": "wf", "kind": "serie", "url": SERIE,
               "content_id": SERIE, "quality": "4K", "eps": list(INDICE)}
    viejo_cb = A._catetbox_impl
    A._catetbox_impl = lambda: A.jsonify({"items": [dict(TARJETA)], "idx": True})
    try:
        js = cli.get("/catetbox?code=111111&op=search&srcs=wf&q=ted%20lasso").get_json()
        it = (js.get("items") or [{}])[0]
        comprueba("busqueda: la tarjeta de Ted Lasso sale con 41 capitulos (no 15)",
                  len(it.get("eps") or []) == 41, len(it.get("eps") or []))
        PROXY["resp"] = (None, 522)
        A._fc_mira("wf")
        js = cli.get("/catetbox?code=111111&op=search&srcs=wf&q=ted%20lasso").get_json()
        comprueba("...y con WolfMax caido, la respuesta lo dice",
                  "wf" in (js.get("caidas") or []), js.get("caidas"))
    finally:
        A._catetbox_impl = viejo_cb
    items = [dict(TARJETA), {"title": "Otra", "source": "dt", "kind": "serie", "eps": []}]
    n = A._epsc_completa(items)
    comprueba("_epsc_completa solo toca series de WolfMax/EliteTorrent con lista mejor",
              n == 1 and len(items[0]["eps"]) == 41 and items[1]["eps"] == [], n)

    print("\n=== 6) /catetboxresolve con la fuente caida ===")
    limpia_estado()
    PROXY["resp"] = (None, 522)
    A._fc_mira("wf")
    RES = []
    viejo_res = A._catetboxresolve_impl
    A._catetboxresolve_impl = lambda: RES.append(1) or A.jsonify({"link": ""})
    try:
        js = cli.get("/catetboxresolve?code=111111&src=wf&url=" + SERIE).get_json()
        comprueba("caida sabida: al momento y dicho, sin preguntar a ninguna caja",
                  js == {"link": "", "fuente_caida": "wf"} and not RES, (js, RES))
        limpia_estado()
        PROXY["resp"] = (None, 522)
        js = cli.get("/catetboxresolve?code=111111&src=wf&url=" + SERIE).get_json()
        comprueba("no se sabia: la caja no trae enlace, se mira y se dice YA",
                  js.get("fuente_caida") == "wf" and RES, (js, RES))
        limpia_estado()
        PROXY["resp"] = ("<html>ok</html>", 200)
        del RES[:]
        js = cli.get("/catetboxresolve?code=111111&src=wf&url=" + SERIE).get_json()
        comprueba("con la fuente viva, el 'sin enlace' de siempre", js == {"link": ""}, js)
    finally:
        A._catetboxresolve_impl = viejo_res
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
print("TODO OK: las fuentes caidas se dicen y las series no se enseñan a medias sin avisar")
