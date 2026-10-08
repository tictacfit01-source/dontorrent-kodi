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


import shutil
os.makedirs("/tmp", exist_ok=True)
COPIA_RES = None
if os.path.exists(A._RESUELTO_FILE):
    COPIA_RES = A._RESUELTO_FILE + ".prueba_bak"
    shutil.copy(A._RESUELTO_FILE, COPIA_RES)
    os.remove(A._RESUELTO_FILE)
def olvida():
    A._RESUELTO.clear()
    try:
        os.remove(A._RESUELTO_FILE)
    except Exception:
        pass


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
    olvida()
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
    A._RESUELTO.clear()                     # como si fuera el OTRO worker
    r3 = A._resuelve_enlace("111111", "wf", W)
    comprueba("el otro worker tambien lo sabe (fichero compartido): sin pedirlo otra vez",
              r3.get("cache") and len(PEDIDOS) == 2, (r3, PEDIDOS))

    print("\n=== 2) Si una caja no lo saca, OTRA ===")
    olvida()
    del PEDIDOS[:]
    RESPUESTA.update({"caja1": {"link": ""}, "caja2": {"link": "https://enlacito.com/s.php?i=def"}})
    r = A._resuelve_enlace("111111", "wf", W)
    comprueba("la caja 1 no pudo, la 2 si", r.get("link", "").endswith("def")
              and [b for b, u in PEDIDOS] == ["caja1", "caja2"], (r, PEDIDOS))
    olvida()
    del PEDIDOS[:]
    RESPUESTA.update({"caja1": {"link": "", "error": "limite", "minutos": 37}})
    r = A._resuelve_enlace("111111", "wf", W)
    comprueba("el LIMITE de WolfMax se dice con sus minutos y no se insiste con otra caja",
              r.get("error") == "limite" and r.get("minutos") == 37 and len(PEDIDOS) == 1, (r, PEDIDOS))
    olvida()
    RESPUESTA.update({"caja1": {"link": ""}, "caja2": {"link": ""}})
    r = A._resuelve_enlace("111111", "wf", W)
    comprueba("ninguna lo saca: vacio (la web lo dira)", r == {"link": ""}, r)
    n = A._RESUELVE_N["wf"]
    comprueba("y queda contado (aciertos, de memoria, limite, vacios, otra caja)",
              n["ok"] >= 2 and n["cache"] == 2 and n["limite"] == 1 and n["vacio"] == 1 and n["otra_caja"] >= 2, n)
    d = A.app.test_client().get("/catdiag").get_json() or {}
    comprueba("/catdiag -> resolver", "wf" in (d.get("resolver") or {}), list(d.get("resolver") or {}))

    print("\n=== 2b) WolfMax pide verificacion humana (dtbl70) ===")
    olvida()
    copia_v = None
    if os.path.exists(A._WF_VERIF_FILE):
        copia_v = A._WF_VERIF_FILE + ".prueba_bak"
        shutil.move(A._WF_VERIF_FILE, copia_v)
    try:
        del PEDIDOS[:]
        RESPUESTA.update({"caja1": {"link": "", "error": "verificacion"}})
        r = A._resuelve_enlace("111111", "wf", W)
        comprueba("una caja lo cuenta: se dice y no se insiste con otra caja",
                  r.get("error") == "verificacion" and len(PEDIDOS) == 1, (r, PEDIDOS))
        r = A._resuelve_enlace("111111", "wf", "https://wolfmax4k.com/serie/episodio/otro1")
        comprueba("...y el siguiente capitulo, AL MOMENTO, sin molestar a ninguna caja",
                  r.get("error") == "verificacion" and len(PEDIDOS) == 1, (r, PEDIDOS))
        comprueba("cada respuesta lleva el aviso para la web (wf_verif)", A._con_caida({}).get("wf_verif") is True)
        RESPUESTA.update({"caja1": {"link": "magnet:?xt=urn:btih:et1"}})
        r = A._resuelve_enlace("111111", "et", "https://elitetorrent.com/x")
        comprueba("EliteTorrent no se ve afectado: se le pregunta y da su magnet",
                  r.get("link") == "magnet:?xt=urn:btih:et1", r)
        RESPUESTA.update({"caja1": {"link": "", "error": "verificacion"}})
        olvida()
        RESPUESTA.update({"caja1": {"link": "https://wolfmax4k.com/torrents/series/x.torrent"}})
        A._resuelve_enlace("", "wf", W, cache=False)         # lo que hace el vigia: mirar de verdad
        comprueba("en cuanto WolfMax vuelve a dar un enlace, se olvida solo", not A._wf_verif_activa()
                  and "wf_verif" not in A._con_caida({}))
    finally:
        try:
            os.remove(A._WF_VERIF_FILE)
        except Exception:
            pass
        if copia_v:
            shutil.move(copia_v, A._WF_VERIF_FILE)

    print("\n=== 2c) De punta a punta: lo que dice la caja llega a la web (dtbl71) ===")
    # 2) y 2b) simulaban la espera, y justo ahi estaba el fallo: /catjob/done
    # guardaba unos campos fijos y TIRABA el "error" (el limite y el captcha desde
    # la 2.9.79; la verificacion del 07-10). Aqui el resultado viaja como en
    # produccion: la caja lo POSTea a /catjob/done y el relay lo recoge.
    viejo_cj = A._CATJOB_FILE
    A._CATJOB_FILE = tempfile.mktemp(prefix="mw_catjob_")
    A._catjob_wait_any = viejos["_catjob_wait_any"]
    cli = A.app.test_client()

    def encola_real(box, ev):
        PEDIDOS.append((box, ev.get("url")))
        cuerpo = dict(RESPUESTA.get(box) or {}, job=ev.get("job"), op="resolve", code="000000")
        cli.post("/catjob/done", json=cuerpo)

    A._kb_enqueue = encola_real
    copia_v2 = None
    if os.path.exists(A._WF_VERIF_FILE):
        copia_v2 = A._WF_VERIF_FILE + ".prueba_bak2"
        shutil.move(A._WF_VERIF_FILE, copia_v2)
    try:
        olvida()
        del PEDIDOS[:]
        RESPUESTA.update({"caja1": {"link": "", "error": "verificacion"}})
        r = A._resuelve_enlace("111111", "wf", W, espera=3.0)
        comprueba("verificacion: la dice la caja y la web la recibe",
                  r.get("error") == "verificacion" and len(PEDIDOS) == 1, (r, PEDIDOS))
        A._wf_verif_apunta(False)
        olvida()
        del PEDIDOS[:]
        RESPUESTA.update({"caja1": {"link": "", "error": "limite", "minutos": 37}})
        r = A._resuelve_enlace("111111", "wf", W, espera=3.0)
        comprueba("el limite de WolfMax llega con sus minutos",
                  r.get("error") == "limite" and r.get("minutos") == 37, r)
        olvida()
        del PEDIDOS[:]
        RESPUESTA.update({"caja1": {"link": "", "error": "<script>"},
                          "caja2": {"link": "https://enlacito.com/s.php?i=zz"}})
        r = A._resuelve_enlace("111111", "wf", W, espera=3.0)
        comprueba("un error desconocido no se cuela (y se pide a otra caja)",
                  r.get("link", "").endswith("zz") and "error" not in r
                  and [b for b, u in PEDIDOS] == ["caja1", "caja2"], (r, PEDIDOS))
        olvida()
        RESPUESTA.update({"caja1": {"link": "magnet:?xt=urn:btih:pp"}})
        r = A._resuelve_enlace("111111", "et", "https://elitetorrent.com/y", espera=3.0)
        comprueba("y un enlace normal, igual que siempre", r.get("link") == "magnet:?xt=urn:btih:pp", r)
    finally:
        A._kb_enqueue = encola
        A._catjob_wait_any = espera
        for f in (A._CATJOB_FILE, A._CATJOB_FILE + ".tmp"):
            try:
                os.remove(f)
            except Exception:
                pass
        try:
            os.rmdir(A._CATJOB_FILE + ".lockd")
        except Exception:
            pass
        A._CATJOB_FILE = viejo_cj
        try:
            os.remove(A._WF_VERIF_FILE)
        except Exception:
            pass
        if copia_v2:
            shutil.move(copia_v2, A._WF_VERIF_FILE)

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
        A._resuelve_enlace = lambda code, src, url, espera=18.0, cache=True: {"link": "", "error": "verificacion"}
        v = A._vigia_ronda()
        comprueba("verificacion humana: el vigia la dice con su nombre",
                  v["wf"].get("enlace") == "verificacion"
                  and any("verificacion humana" in p for p in v["wf"]["problemas"]), v.get("wf"))
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
    olvida()
    try:
        if COPIA_RES:
            shutil.move(COPIA_RES, A._RESUELTO_FILE)
        elif os.path.exists(A._RESUELTO_FILE):
            os.remove(A._RESUELTO_FILE)
    except Exception:
        pass

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el enlace de cada capitulo se saca, se recuerda y se vigila")
