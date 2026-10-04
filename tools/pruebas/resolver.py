# -*- coding: utf-8 -*-
"""Sacar el enlace de un capitulo/peli de WolfMax o EliteTorrent (dtbl68).

04-10: WolfMax cambio su prueba de trabajo y todos los capitulos nuevos
fallaban ("recien buscados parece que van; unos segundos despues, error").
Nadie lo vio: el vigia solo miraba busquedas, el relay pedia el enlace a UNA
caja y no recordaba nada. Aqui, sin red: lo resuelto se recuerda (repetir es
instantaneo y no gasta el cupo de WolfMax), si una caja no lo saca se le pide a
otra, el limite de WolfMax se dice y no se insiste, se cuenta todo, y el vigia
prueba este paso y lo dice si falla.
"""
import os
import sys
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


PEDIDOS = []          # (caja, url)
RESPUESTA = {}        # caja -> lo que contesta


def encola(box, ev):
    PEDIDOS.append((box, ev.get("url")))
    ev_job = ev.get("job")
    RESP_JOBS[ev_job] = RESPUESTA.get(box)


RESP_JOBS = {}


def espera(jobs, secs, ok=None, corta=None):
    last = None
    for j in list(jobs):
        r = RESP_JOBS.pop(j, None)
        jobs.remove(j)
        if r is None:
            continue
        if ok is None or ok(r):
            return r
        last = r
    return last


viejos = {k: getattr(A, k) for k in ("_kb_enqueue", "_catjob_wait_any", "_box_wf", "_box_for",
                                     "_live_boxes", "_resuelve_enlace", "_vigia_busca",
                                     "_wf_cupo_toma", "_fuentes_caidas")}
A._kb_enqueue = encola
A._catjob_wait_any = espera
A._box_wf = lambda code, excluir=(), minimo=None: next(
    (b for b in ("caja1", "caja2") if b not in excluir), None)
A._box_for = lambda code: "caja1"
A._live_boxes = lambda *a, **k: ["caja1", "caja2"]
W = "https://wolfmax4k.com/serie/episodio/2anek9"
try:
    A._RESUELTO.clear()
    A._RESUELVE_N.clear()
    print("\n=== 1) Lo resuelto se recuerda ===")
    RESPUESTA.update({"caja1": {"link": "https://enlacito.com/s.php?i=abc"}})
    r1 = A._resuelve_enlace("111111", "wf", W)
    r2 = A._resuelve_enlace("111111", "wf", W)
    comprueba("la segunda vez sale de memoria: sin otra prueba de trabajo ni cupo",
              r1.get("link") and r2.get("link") == r1["link"] and r2.get("cache") and len(PEDIDOS) == 1, PEDIDOS)
    A._RESUELTO[("wf", W)] = (r1["link"], time.time() - 3600)
    A._resuelve_enlace("111111", "wf", W)
    comprueba("...pero no para siempre (WolfMax: 30 min)", len(PEDIDOS) == 2, PEDIDOS)

    print("\n=== 2) Si una caja no lo saca, OTRA ===")
    A._RESUELTO.clear()
    del PEDIDOS[:]
    RESPUESTA.update({"caja1": {"link": ""}, "caja2": {"link": "https://enlacito.com/s.php?i=def"}})
    r = A._resuelve_enlace("111111", "wf", W)
    comprueba("la caja 1 no pudo, la 2 si", r.get("link", "").endswith("def")
              and [b for b, u in PEDIDOS] == ["caja1", "caja2"], (r, PEDIDOS))
    A._RESUELTO.clear()
    del PEDIDOS[:]
    RESPUESTA.update({"caja1": {"link": "", "error": "limite", "minutos": 37}})
    r = A._resuelve_enlace("111111", "wf", W)
    comprueba("el LIMITE de WolfMax se dice con sus minutos y no se insiste con otra caja",
              r.get("error") == "limite" and r.get("minutos") == 37 and len(PEDIDOS) == 1, (r, PEDIDOS))
    A._RESUELTO.clear()
    RESPUESTA.update({"caja1": {"link": ""}, "caja2": {"link": ""}})
    r = A._resuelve_enlace("111111", "wf", W)
    comprueba("ninguna lo saca: vacio (la web lo dira)", r == {"link": ""}, r)
    n = A._RESUELVE_N["wf"]
    comprueba("y queda contado (aciertos, de memoria, limite, vacios, otra caja)",
              n["ok"] >= 2 and n["cache"] == 1 and n["limite"] == 1 and n["vacio"] == 1 and n["otra_caja"] >= 2, n)
    d = A.app.test_client().get("/catdiag").get_json() or {}
    comprueba("/catdiag -> resolver", "wf" in (d.get("resolver") or {}), list(d.get("resolver") or {}))

    print("\n=== 3) El vigia prueba este paso ===")
    import json
    import tempfile
    tmpv = tempfile.mktemp(prefix="mw_vigia_")
    viejo_vf = A._VIGIA_FILE
    A._VIGIA_FILE = tmpv
    A._fuentes_caidas = lambda *a, **k: []
    A._wf_cupo_toma = lambda: True
    peli = {"title": "Dune", "source": "wf", "kind": "movie", "quality": "4K",
            "url": "https://wolfmax4k.com/pelicula/d9db5r"}
    serie = {"title": "Ted Lasso 1x01", "source": "wf", "kind": "serie", "quality": "720p",
             "url": "https://wolfmax4k.com/serie/episodio/zrfbxt"}
    A._vigia_busca = lambda src: ([dict(peli), dict(serie), dict(peli, url=peli["url"] + "x")], "") \
        if src == "wf" else ([], "")
    PROBADAS = []
    A._resuelve_enlace = lambda code, src, url, espera=18.0, cache=True: (
        PROBADAS.append((src, url, cache)) or {"link": ""})
    try:
        v = A._vigia_ronda()
        comprueba("si no da el enlace, el vigia lo dice (y sin tirar de memoria)",
                  any("enlace para reproducir" in p for p in v["wf"]["problemas"]) and v["wf"]["ok"] is False
                  and PROBADAS and PROBADAS[0][2] is False, (v.get("wf"), PROBADAS))
        A._resuelve_enlace = lambda code, src, url, espera=18.0, cache=True: {"link": "", "error": "limite"}
        v = A._vigia_ronda()
        comprueba("el limite de WolfMax no es una averia", v["wf"].get("enlace") is None
                  and not any("enlace" in p for p in v["wf"]["problemas"]), v.get("wf"))
        A._resuelve_enlace = lambda code, src, url, espera=18.0, cache=True: {"link": "magnet:?xt=x"}
        v = A._vigia_ronda()
        comprueba("y si lo da, 'enlace: ok'", v["wf"].get("enlace") == "ok", v.get("wf"))
    finally:
        A._VIGIA_FILE = viejo_vf
        try:
            os.remove(tmpv)
        except Exception:
            pass
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._RESUELTO.clear()

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el enlace de cada capitulo se saca, se recuerda y se vigila")
