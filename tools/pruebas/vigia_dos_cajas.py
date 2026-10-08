# -*- coding: utf-8 -*-
"""Que un tropiezo de UNA caja no parezca una averia de la fuente (dtbl72).

Revision en paralelo del 07-10:
  1) el vigia preguntaba a UNA caja prestada; si no contestaba o traia casi
     nada apuntaba un '-' (la gente, en cambio, ya probaba otra caja). Y no
     quedaba el porque: de "+-+++-++" no se podia saber que habia fallado;
  2) DivxTotal con el disyuntor saltado salia "solo 0 resultados";
  3) lo ultimo de WolfMax para el Inicio: un solo camino, sin rastro, y los
     dos workers se pisaban la foto al guardarla;
  4) el indice de WolfMax perdia la caratula ("i") cada vez que aprendia;
  5) los contadores del enlace (/catdiag -> resolver) eran de UN worker.
Sin red.
"""
import json
import os
import sys
import tempfile
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


N = "https://wolfmax4k.com"


def wf(t, cod, q="4K", kind="movie", year=2021):
    return {"title": t, "source": "wf", "kind": kind, "url": N + ("/pelicula/" if kind == "movie"
            else "/serie/episodio/") + cod, "quality": q, "year": year,
            "thumb": "https://wolfmax4k.com/img/%s.jpg" % cod}


BUENO = {"wf": [wf("Dune", "aa0001"), wf("Dune", "aa0002", "1080p"),
                wf("Dune La profecia 1x06", "aa0003", kind="serie", year=None)],
         "et": [{"title": "Dune %d" % i, "source": "et", "kind": "movie",
                 "url": "https://www.elitetorrent.com/p/%d" % i, "quality": "720p"} for i in range(3)]}
# lo que contesta cada caja: None = no contesta; si no, la lista de items
CAJAS = {}
PEDIDOS = []
TMP = tempfile.mkdtemp(prefix="mw_vigia_")
viejos = {k: getattr(A, k) for k in (
    "_box_wf", "_box_for", "_live_boxes", "_kb_enqueue", "_catjob_wait", "_dx_search_items",
    "_fuentes_caidas", "_vigia_enlace", "_VIGIA_FILE", "_WFULT_FILE", "_RESUELVE_N_BASE",
    "_wfidx_load", "_wfidx_save", "_wfidx_nube_sube")}
JOBS = {}
try:
    A._VIGIA_FILE = os.path.join(TMP, "vigia.json")
    A._WFULT_FILE = os.path.join(TMP, "wfult.json")
    A._RESUELVE_N_BASE = os.path.join(TMP, "resuelve_n")
    A._box_wf = lambda code, excluir=(), minimo=None: next(
        (b for b in ("caja1", "caja2") if b not in excluir), None)
    A._box_for = lambda code: "caja1"
    A._live_boxes = lambda *a, **k: ["caja1", "caja2"]

    def encola(box, ev):
        PEDIDOS.append((box, ev.get("op"), ev.get("srcs"), ev.get("page")))
        JOBS[ev.get("job")] = (box, ev)
    A._kb_enqueue = encola

    def espera(job, secs):
        box, ev = JOBS.pop(job)
        r = CAJAS.get(box)
        if callable(r):
            r = r(ev)
        if r is None:
            return None
        return {"items": [x for x in r if x.get("source") == ev.get("srcs")]}
    A._catjob_wait = espera
    A._dx_search_items = lambda q, max_pages=5, proxy=False: [
        {"title": "Dune", "source": "dx", "url": "https://divxtotal/p/%d" % i, "quality": ""} for i in range(4)]
    A._fuentes_caidas = lambda: []
    A._vigia_enlace = lambda src, items: "ok"

    print("\n=== 1) El vigia prueba OTRA caja antes de apuntar un fallo ===")
    CAJAS.update(caja1=None, caja2=BUENO["wf"] + BUENO["et"])
    v = A._vigia_ronda()
    comprueba("la caja 1 no contesta, la 2 si: WolfMax y EliteTorrent 'ok' (antes, dos '-')",
              v["wf"]["ok"] and v["et"]["ok"] and v["wf"].get("cajas") == 2 and v["et"].get("cajas") == 2,
              (v["wf"], v["et"]))
    comprueba("...preguntando a las dos", {b for b, op, s, p in PEDIDOS if op == "search"} == {"caja1", "caja2"},
              PEDIDOS)
    del PEDIDOS[:]
    CAJAS.update(caja1=BUENO["wf"] + BUENO["et"])
    v = A._vigia_ronda()
    comprueba("si la primera contesta bien, no se molesta a la segunda",
              v["wf"]["ok"] and {b for b, op, s, p in PEDIDOS} == {"caja1"} and "cajas" not in v["wf"], PEDIDOS)
    CAJAS.update(caja1=None, caja2=None)
    v = A._vigia_ronda()
    comprueba("si NINGUNA contesta, entonces si: '-' y dicho por que (con 2 cajas)",
              v["wf"]["ok"] is False and v["wf"]["ult_mal"]["cajas"] == 2
              and "no contesto" in v["wf"]["ult_mal"]["problemas"][0], v["wf"])
    CAJAS.update(caja1=BUENO["wf"] + BUENO["et"])
    v = A._vigia_ronda()
    comprueba("el PORQUE del ultimo '-' se queda aunque la ronda siguiente vaya bien",
              v["wf"]["ok"] and v["wf"]["historia"].endswith("-+") and v["wf"].get("ult_mal", {}).get("problemas"),
              v["wf"])

    print("\n=== 2) DivxTotal con el disyuntor saltado no es '0 resultados' ===")
    A._DX_DOWN_UNTIL[0] = time.time() + 60
    v = A._vigia_ronda()
    comprueba("se dice que no contesta a Render (disyuntor)",
              v["dx"]["ok"] is False and "disyuntor" in v["dx"]["problemas"][0], v["dx"])
    A._DX_DOWN_UNTIL[0] = 0.0
    v = A._vigia_ronda()
    comprueba("...y en cuanto contesta, ok", v["dx"]["ok"], v["dx"])

    print("\n=== 3) Lo ultimo de WolfMax: otra caja, rastro, y sin pisarse ===")
    del PEDIDOS[:]
    pelis = [wf("Peli %d" % i, "pp%04d" % i, year=2026) for i in range(5)]
    CAJAS.update(caja1=None, caja2=lambda ev: pelis if ev.get("page") == 1 else [])
    its = A._wfult_trae("movie")
    comprueba("la caja 1 no contesta: se le pide a la 2 y llega",
              len(its) == 5 and A._WFULT_DIAG["movie"]["cajas"] == 2 and A._WFULT_DIAG["movie"]["por"] == "",
              (len(its), A._WFULT_DIAG.get("movie")))
    CAJAS.update(caja1=None, caja2=None)
    its = A._wfult_trae("movie")
    comprueba("ninguna: vacio, y queda dicho por que", its == [] and A._WFULT_DIAG["movie"]["por"]
              == "la caja no contesto", A._WFULT_DIAG.get("movie"))
    js = A.app.test_client().get("/catdiag").get_json() or {}
    comprueba("/catdiag -> wf_ultimos lo ensena",
              ((js.get("wf_ultimos") or {}).get("movie") or {}).get("ult_intento", {}).get("por")
              == "la caja no contesto", js.get("wf_ultimos"))
    A._WFULT.clear()
    with open(A._WFULT_FILE, "w", encoding="utf-8") as f:
        json.dump({"tvshow": {"items": [wf("Serie", "ss0001", kind="serie")], "ts": 100.0}}, f)
    A._WFULT["movie"] = {"items": pelis, "ts": 200.0}
    A._wfult_guarda()
    with open(A._WFULT_FILE, encoding="utf-8") as f:
        disco = json.load(f)
    comprueba("guardar no borra la clase que guardo el OTRO worker", set(disco) == {"movie", "tvshow"}, list(disco))
    disco["movie"] = {"items": pelis[:2], "ts": 300.0}
    with open(A._WFULT_FILE, "w", encoding="utf-8") as f:
        json.dump(disco, f)
    os.utime(A._WFULT_FILE, (time.time() + 5, time.time() + 5))
    comprueba("si el otro worker la renovo, se coge la suya (no la vieja de memoria)",
              A._wfult_lee("movie").get("ts") == 300.0, A._wfult_lee("movie").get("ts"))
    A._WFULT.clear()

    print("\n=== 4) El indice de WolfMax no pierde la caratula ===")
    IDX = {}
    A._wfidx_load = lambda: IDX
    A._wfidx_save = lambda: None
    A._wfidx_nube_sube = lambda: None
    u = N + "/pelicula/ii0001"
    A._wfidx_learn([dict(wf("Dune", "ii0001"))])
    comprueba("aprende la caratula de la tarjeta", IDX[u].get("i", "").endswith("ii0001.jpg"), IDX.get(u))
    sin = wf("Dune", "ii0001")
    sin.pop("thumb")
    A._wfidx_learn([sin])
    comprueba("...y una tarjeta sin ella no la borra", IDX[u].get("i", "").endswith("ii0001.jpg"), IDX.get(u))

    print("\n=== 5) Los contadores del enlace, de los dos workers ===")
    with open(A._RESUELVE_N_BASE + ".99999.json", "w", encoding="utf-8") as f:
        json.dump({"wf": {"ok": 2, "verificacion": 3, "ult_ok": 5, "ult_fallo": 9}}, f)
    A._RESUELVE_N.clear()
    A._resuelve_cuenta("wf", "ok")
    A._resuelve_cuenta("et", "ok")
    t = A._resuelve_n_todos()
    comprueba("se suman (y la ultima vez, la mas reciente)",
              t["wf"]["ok"] == 3 and t["wf"]["verificacion"] == 3 and t["wf"]["ult_ok"] > 5
              and t["wf"]["ult_fallo"] == 9 and t["et"]["ok"] == 1, t)
    js = A.app.test_client().get("/catdiag").get_json() or {}
    comprueba("/catdiag -> resolver los ensena sumados", (js.get("resolver") or {}).get("wf", {}).get("ok") == 3,
              js.get("resolver"))
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._RESUELVE_N.clear()
    A._WFULT.clear()
    A._WFULT_DIAG.clear()
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: un tropiezo de una caja no parece una averia, y lo que pasa queda dicho")
