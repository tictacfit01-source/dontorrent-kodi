# -*- coding: utf-8 -*-
"""La web NUEVA de WolfMax, del lado del relay (dtbl49).

27-09-2026: WolfMax volvio con la web rehecha; las URLs viejas dan 404 y los
ids nuevos son letras y numeros al azar ("5d7uyr"). Esto vigila, sin red:
  1) lo que manda la caja se agrupa como siempre: UNA tarjeta por serie con
     sus capitulos (la mejor calidad gana), y las versiones de una peli;
     "Dune" (1984) y "Dune" (2021) son dos pelis;
  2) de una URL nueva no se adivina ni la calidad ni el capitulo;
  3) el indice y la cache de listas completas no guardan ni sirven nada de
     la web vieja (de ninguna via: fichero, caja, busqueda, copia);
  4) la busqueda de WolfMax ya no sale del indice (se rehace: daria resultados
     a medias) sino de la caja;
  5) un enlace viejo (historial, favoritos) se dice AL MOMENTO; y el trabajo
     de capitulos lleva el titulo, para que la caja busque la serie por su
     nombre si la URL es vieja.
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

A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"
A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"
fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


os.makedirs("/tmp", exist_ok=True)
FICHEROS = [A._WFIDX_FILE, A._EPSC_FILE]
copia = {}
for f in FICHEROS:
    if os.path.exists(f):
        copia[f] = f + ".prueba_wf_bak"
        shutil.copy(f, copia[f])
cli = A.app.test_client()
N = "https://wolfmax4k.com"


def caja(title, url, q, kind="serie", year=None):
    it = {"title": title, "kind": kind, "source": "wf", "url": url, "content_id": url,
          "thumb": None, "quality": q, "tabla": "wf"}
    if year:
        it["year"] = year
    return it


try:
    print("\n=== 1) Lo que manda la caja, agrupado ===")
    # lo que da la caja 2.9.77 para "ted lasso" (mejor calidad primero) y "dune"
    ted = [caja("Ted Lasso 4x07", N + "/serie/episodio/5ecqn5", "4K"),
           caja("Ted Lasso 4x08", N + "/serie/episodio/5bgkz2", "1080p"),
           caja("Ted Lasso 4x07", N + "/serie/episodio/5a49d5", "720p"),
           caja("Ted Lasso 2x12", N + "/serie/episodio/fb54bu", "HDTV"),
           caja("Ted Lasso 1x10", N + "/serie/episodio/2s42pf", "720p")]
    g = A._cat_group_episodes(ted)
    card = g[0] if g else {}
    comprueba("UNA tarjeta 'Ted Lasso' con sus capitulos, ordenados",
              len(g) == 1 and card.get("title") == "Ted Lasso" and card.get("kind") == "serie"
              and [e["label"] for e in card.get("eps", [])] == ["1x10", "2x12", "4x07", "4x08"], g)
    comprueba("...y el 4x07 es el de 4K (la caja manda la mejor primero)",
              [e for e in card.get("eps", []) if e["label"] == "4x07"][0]["url"].endswith("5ecqn5"), card.get("eps"))
    dune = [caja("Dune", N + "/pelicula/dgtkej", "4K", "movie", 1984),
            caja("Dune", N + "/pelicula/d8k2pt", "4K", "movie", 2021),
            caja("Dune", N + "/pelicula/d9db5r", "1080p", "movie", 2021)]
    c = A._wf_colapsa(A._cat_group_episodes(dune))
    comprueba("'Dune' 1984 y 'Dune' 2021 en 4K: DOS pelis, no una",
              len([x for x in c if x.get("quality") == "4K"]) == 2 and len(c) == 3,
              [(x.get("year"), x.get("quality")) for x in c])

    print("\n=== 2) De una URL nueva no se adivina nada ===")
    comprueba("reconoce la web nueva (y no la vieja)",
              A._wf_url_nueva(N + "/serie/episodio/5d7uyr") and A._wf_url_nueva("https://www.wolfmax4k.com/pelicula/rw2gwz")
              and not A._wf_url_nueva("https://www.wolfmax4k.com/serie-online-4k/156288"))
    comprueba("un id como 'a4kx12' no da 4K", A._wf_quality_from_url(N + "/pelicula/a4kx12") == "",
              A._wf_quality_from_url(N + "/pelicula/a4kx12"))
    comprueba("un id como '12x345' no da temporada 12: manda el titulo",
              A._ep_parse({"url": N + "/serie/episodio/12x345", "title": "Silo 2x03", "source": "wf"}) == (2, 3))
    comprueba("...y la web vieja sigue como siempre",
              A._wf_quality_from_url("https://www.wolfmax4k.com/serie-online-1080p/123") == "1080p")

    print("\n=== 3) El indice y la cache: nada de la web vieja ===")
    A._WFIDX.clear()
    with open(A._WFIDX_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "e": {
            "https://www.wolfmax4k.com/serie-online-4k/156288": {"t": "Ted Lasso [Cap.201]", "k": "tvshow"},
            N + "/pelicula/abc123": {"t": "Nueva", "k": "movie"}}}, f)
    idx = A._wfidx_load()
    comprueba("al cargar el fichero, lo viejo fuera", list(idx) == [N + "/pelicula/abc123"], list(idx))
    r = cli.post("/wffeed", json={"entries": {
        "https://www.wolfmax4k.com/movie/999": {"t": "Vieja", "k": "movie"},
        N + "/serie/episodio/5ecqn5": {"t": "Ted Lasso 4x07", "k": "tvshow", "q": "4K"}}})
    comprueba("lo que empuja una caja: solo lo nuevo",
              N + "/serie/episodio/5ecqn5" in A._WFIDX and "https://www.wolfmax4k.com/movie/999" not in A._WFIDX,
              list(A._WFIDX))
    A._wfidx_learn([caja("Vieja 1x01", "https://www.wolfmax4k.com/serie-online/1", "720p"),
                    caja("Dune", N + "/pelicula/d8k2pt", "4K", "movie")])
    comprueba("lo que aprende de una busqueda: solo lo nuevo",
              N + "/pelicula/d8k2pt" in A._WFIDX and "https://www.wolfmax4k.com/serie-online/1" not in A._WFIDX)
    comprueba("la copia de la nube se filtra igual",
              A._wfidx_solo_nuevas({"https://www.wolfmax4k.com/online/5": {}, N + "/pelicula/x1y2z3": {}})
              == {N + "/pelicula/x1y2z3": {}})
    try:
        os.remove(A._EPSC_FILE)
    except Exception:
        pass
    A._EPSC_MEM.update({"mtime": -1.0, "d": {}})
    SER = "https://www.wolfmax4k.com/serie-online-4k/270209"
    A._epsc_put("wf", SER, "Ted Lasso", [
        {"label": "1x01", "season": 1, "episode": 1, "url": "https://www.wolfmax4k.com/serie-online-4k/156288"},
        {"label": "1x02", "season": 1, "episode": 2, "url": "https://www.wolfmax4k.com/serie-online-4k/156289"}])
    comprueba("una lista guardada con capitulos de la web vieja: como si no hubiera",
              A._epsc_get("wf", SER) is None, A._epsc_get("wf", SER))
    A._epsc_put("wf", SER, "Ted Lasso", [{"label": "4x07", "season": 4, "episode": 7,
                                          "url": N + "/serie/episodio/5ecqn5"}])
    e = A._epsc_get("wf", SER) or {}
    comprueba("...y si se mezcla con capitulos nuevos, solo salen los nuevos",
              [x["label"] for x in e.get("eps", [])] == ["4x07"], e)

    print("\n=== 4) La busqueda de WolfMax, de la caja ===")
    ENCOLADO = []
    reales = (A._kb_enqueue, A._catjob_wait_any, A._box_for, A._catbox_get, A._live_boxes,
              A._fc_caido, A._fc_atajo)
    A._kb_enqueue = lambda b, ev: ENCOLADO.append(dict(ev))
    A._catjob_wait_any = lambda jobs, espera, *a, **k: {"items": ted}
    A._box_for = lambda code: "111111"
    A._catbox_get = lambda k: None
    A._live_boxes = lambda *a, **k: ["111111"]
    A._fc_caido = lambda s: False
    A._fc_atajo = lambda s: False
    try:
        A._WFIDX[N + "/serie/episodio/zz0001"] = {"t": "Ted Lasso 3x01", "k": "tvshow", "q": "4K"}
        js = cli.get("/catetbox?code=111111&op=search&srcs=wf&q=ted%20lasso").get_json()
        comprueba("con el titulo en el indice, igual se le pregunta a la caja",
                  any(ev.get("op") == "search" for ev in ENCOLADO) and not js.get("idx"),
                  (ENCOLADO, js.get("idx")))
        it = (js.get("items") or [{}])[0]
        comprueba("...y sale su tarjeta de serie entera", it.get("title") == "Ted Lasso"
                  and len(it.get("eps") or []) >= 4, it)

        print("\n=== 5) Enlaces viejos: al momento; capitulos: con su titulo ===")
        del ENCOLADO[:]
        t0 = time.time()
        js = cli.get("/catetboxresolve?code=111111&src=wf&url=" + SER).get_json()
        comprueba("un enlace de la web vieja se dice al momento, sin molestar a ninguna caja",
                  js == {"link": "", "vieja": "wf"} and not ENCOLADO and time.time() - t0 < 1, (js, ENCOLADO))
        A._catjob_wait_any = lambda jobs, espera, *a, **k: None
        try:
            os.remove(A._EPSC_FILE)          # sin la lista que guardo la seccion 3
        except Exception:
            pass
        A._EPSC_MEM.update({"mtime": -1.0, "d": {}})
        cli.get("/catboxeps?code=111111&src=wf&full=1&url=" + SER + "&t=Ted%20Lasso")
        ev = next((x for x in ENCOLADO if x.get("op") == "episodes"), {})
        comprueba("el trabajo de capitulos lleva el titulo (la caja busca la serie si la URL es vieja)",
                  ev.get("t") == "Ted Lasso" and ev.get("url") == SER, ENCOLADO)
    finally:
        (A._kb_enqueue, A._catjob_wait_any, A._box_for, A._catbox_get, A._live_boxes,
         A._fc_caido, A._fc_atajo) = reales
    comprueba("la web avisa de la web vieja con su dialogo",
              "function webViejaDlg(" in A._CAT_PAGE and "d.vieja" in A._CAT_PAGE)

    print("\n=== 6) El cupo de WolfMax: las semillas no se comen las descargas ===")
    FICHEROS.append(A._WF_CUPO_FILE)
    if os.path.exists(A._WF_CUPO_FILE):
        copia[A._WF_CUPO_FILE] = A._WF_CUPO_FILE + ".prueba_wf_bak"
        shutil.copy(A._WF_CUPO_FILE, copia[A._WF_CUPO_FILE])
        os.remove(A._WF_CUPO_FILE)
    tomas = [A._wf_cupo_toma() for _ in range(A._WF_CUPO_HORA + 3)]
    comprueba("como mucho %d huellas por hora; las demas, no" % A._WF_CUPO_HORA,
              tomas.count(True) == A._WF_CUPO_HORA and tomas[-1] is False, tomas.count(True))
    ENC2 = []
    r2 = (A._kb_enqueue, A._box_for, A._catjob_wait, A._dxih_load)
    A._kb_enqueue = lambda b, ev: ENC2.append(dict(ev))
    A._box_for = lambda code: "111111"
    A._catjob_wait = lambda job, espera: {"ih": "a" * 40}
    A._dxih_load = lambda: {}
    try:
        cli.get("/seeds?code=111111&src=wf&url=" + N + "/serie/episodio/zz9999")
        comprueba("sin cupo: las semillas de un capitulo NO van a ninguna caja", not ENC2, ENC2)
        os.remove(A._WF_CUPO_FILE)
        cli.get("/seeds?code=111111&src=wf&url=" + N + "/serie/episodio/zz9998")
        comprueba("con cupo, si (y cuenta)", len(ENC2) == 1 and len(A._wf_cupo_lee()) == 1, ENC2)
        del ENC2[:]
        cli.get("/seeds?code=111111&src=wf&url=https://www.wolfmax4k.com/serie-online-4k/156288")
        comprueba("una URL de la web vieja: ni se intenta", not ENC2 and len(A._wf_cupo_lee()) == 1, ENC2)
        js = cli.get("/catdiag").get_json()
        comprueba("/catdiag ensena el cupo", js.get("wf_cupo") == {"usadas_hora": 1, "tope": A._WF_CUPO_HORA},
                  js.get("wf_cupo"))
    finally:
        A._kb_enqueue, A._box_for, A._catjob_wait, A._dxih_load = r2
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
print("TODO OK: el relay entiende la web nueva de WolfMax y olvida la vieja")
