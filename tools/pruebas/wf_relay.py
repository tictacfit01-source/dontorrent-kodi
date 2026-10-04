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
FICHEROS = [A._WFIDX_FILE, A._EPSC_FILE, A._RESUELTO_FILE]
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
              A._fc_caido, A._fc_atajo, A._box_wf)
    A._kb_enqueue = lambda b, ev: ENCOLADO.append(dict(ev))
    A._catjob_wait_any = lambda jobs, espera, *a, **k: {"items": ted}
    A._box_for = lambda code: "111111"
    A._box_wf = lambda code, excluir=(), minimo=None: "111111"       # una caja al dia (ver 7)
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
         A._fc_caido, A._fc_atajo, A._box_wf) = reales
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
    r2 = (A._kb_enqueue, A._box_for, A._catjob_wait, A._dxih_load, A._box_wf)
    A._kb_enqueue = lambda b, ev: ENC2.append(dict(ev))
    A._box_for = lambda code: "111111"
    A._box_wf = lambda code, excluir=(), minimo=None: "111111"
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
        A._kb_enqueue, A._box_for, A._catjob_wait, A._dxih_load, A._box_wf = r2

    print("\n=== 7) Cajas viejas y capitulos sin serie (dtbl50) ===")
    # 27-09: buscando "el dorado", el dueño vio tarjetas sueltas "4x01", "4x02"...:
    # el trabajo cayo en una caja aun en 2.9.76, cuyo scraper viejo leia de la
    # web nueva los enlaces de capitulo sin el nombre de la serie
    r3 = (A._kbstatus_load, A._box_live, A._live_boxes)
    ESTADO = {"111111": {"ts": time.time(), "v": "2.9.76"},
              "222222": {"ts": time.time(), "v": "2.9.82"},
              "333333": {"ts": time.time(), "v": "2.9.83"}}
    A._kbstatus_load = lambda: ESTADO
    A._box_live = lambda c: c in ESTADO
    A._live_boxes = lambda *a, **k: list(ESTADO)
    try:
        ESTADO["444444"] = {"ts": time.time(), "v": "2.9.77"}
        comprueba("ni a una en 2.9.77 (su lector no ve los archivos desde el 28-09)",
                  A._box_wf("444444") == "222222", A._box_wf("444444"))
        ESTADO["555555"] = {"ts": time.time(), "v": "2.9.81"}
        comprueba("ni a una en 2.9.81 (el 29-09 WolfMax paso a codigos y su API ya no acepta el id)",
                  A._box_wf("555555") == "222222", A._box_wf("555555"))
        ESTADO["666666"] = {"ts": time.time(), "v": "2.9.85"}
        comprueba("pedir un TORRENT de WolfMax, a una caja 2.9.85 (prueba de trabajo v2, 04-10)",
                  A._box_wf("222222", minimo=A._WF_TORRENT_MIN) == "666666",
                  A._box_wf("222222", minimo=A._WF_TORRENT_MIN))
        ESTADO.pop("666666")
        comprueba("...y sin ninguna, la de siempre (quiza tenga el enlace guardado)",
                  (A._box_wf("222222", minimo=A._WF_TORRENT_MIN) or A._box_wf("222222")) == "222222")
        comprueba("un minimo propio mas bajo (series del Inicio: 2.9.80) no salta el de WolfMax",
                  A._box_wf("555555", minimo=(2, 9, 80)) == "222222", A._box_wf("555555", minimo=(2, 9, 80)))
        comprueba("un trabajo de WolfMax NO va a una caja en 2.9.76, aunque sea la suya",
                  A._box_wf("111111") == "222222", A._box_wf("111111"))
        comprueba("la suya, si esta al dia", A._box_wf("333333") == "333333")
        comprueba("la segunda caja, otra al dia distinta", A._box_wf("", excluir=("222222",)) == "333333")
        ESTADO["222222"]["v"] = ESTADO["333333"]["v"] = "2.9.76"
        comprueba("si no hay ninguna al dia: ninguna (mejor nada que basura)", A._box_wf("111111") is None)
    finally:
        A._kbstatus_load, A._box_live, A._live_boxes = r3
    g = A._cat_group_episodes([caja("4x01", N + "/serie/episodio/aa0001", "4K"),
                               caja("La ruta hacia El Dorado", N + "/pelicula/bb0001", "DVDRip", "movie")])
    comprueba("un capitulo sin el nombre de su serie no es una tarjeta",
              [x["title"] for x in g] == ["La ruta hacia El Dorado"], g)
    r4 = (A._catbox_get,)
    A._catbox_get = lambda k: [caja("4x01", N + "/serie/episodio/aa0001", "4K"),
                               caja("4x02", N + "/serie/episodio/aa0002", "4K"),
                               caja("La ruta hacia El Dorado", N + "/pelicula/bb0001", "DVDRip", "movie")]
    try:
        js = cli.get("/catetbox?code=111111&op=search&srcs=wf&q=el%20dorado").get_json()
        comprueba("...ni sale de lo que ya estaba guardado de antes",
                  [x["title"] for x in js.get("items") or []] == ["La ruta hacia El Dorado"], js.get("items"))
    finally:
        (A._catbox_get,) = r4

    print("\n=== 8) Por que WolfMax no da el torrent, dicho (dtbl51) ===")
    r5 = (A._box_wf, A._kb_enqueue, A._catjob_wait_any)
    A._box_wf = lambda code, excluir=(), minimo=None: "222222"
    A._kb_enqueue = lambda b, ev: None
    A._RESUELTO.clear()
    os.path.exists(A._RESUELTO_FILE) and os.remove(A._RESUELTO_FILE)
    try:
        A._catjob_wait_any = lambda jobs, espera, ok=None, corta=None: {"link": "", "error": "limite", "minutos": 42}
        js = cli.get("/catetboxresolve?code=222222&src=wf&url=" + N + "/pelicula/d8k2pt").get_json()
        comprueba("el limite de WolfMax llega a la web con sus minutos",
                  js == {"link": "", "error": "limite", "minutos": 42}, js)
        A._catjob_wait_any = lambda jobs, espera, ok=None, corta=None: {"link": "https://wolfmax4k.com/torrents/x.torrent"}
        js = cli.get("/catetboxresolve?code=222222&src=wf&url=" + N + "/pelicula/d8k2pt").get_json()
        comprueba("con enlace, como siempre", js == {"link": "https://wolfmax4k.com/torrents/x.torrent"}, js)
        A._RESUELTO.clear()
        os.path.exists(A._RESUELTO_FILE) and os.remove(A._RESUELTO_FILE)
        A._catjob_wait_any = lambda jobs, espera, ok=None, corta=None: {"link": "", "error": "cualquier-cosa"}
        js = cli.get("/catetboxresolve?code=222222&src=wf&url=" + N + "/pelicula/d8k2pt").get_json()
        comprueba("un motivo que no se conoce no se inventa", js == {"link": ""}, js)
    finally:
        A._box_wf, A._kb_enqueue, A._catjob_wait_any = r5
        A._RESUELTO.clear()
        os.path.exists(A._RESUELTO_FILE) and os.remove(A._RESUELTO_FILE)
    print("\n=== 9) El Inicio: lo ultimo de WolfMax, de WolfMax (dtbl52) ===")
    # antes salia del indice ordenado por el NUMERO de la URL; con ids al azar
    # ese orden no valia, y "El Dorado" de 1966 salio en Estrenos
    FICHEROS.append(A._WFULT_FILE)
    if os.path.exists(A._WFULT_FILE):
        copia[A._WFULT_FILE] = A._WFULT_FILE + ".prueba_wf_bak"
        shutil.copy(A._WFULT_FILE, copia[A._WFULT_FILE])
        os.remove(A._WFULT_FILE)
    A._WFULT.clear()
    A._WFULT_VUELO.clear()
    anio = time.localtime().tm_year
    PEDIDO = []
    PAG = {1: [caja("Letras robadas", N + "/pelicula/aa0001", "1080p", "movie", anio),
               caja("Letras robadas", N + "/pelicula/aa0002", "4K", "movie", anio),
               caja("El Dorado", N + "/pelicula/aa0003", "DVDRip", "movie", 1966)],
           2: [caja("Poli malo", N + "/pelicula/aa0004", "720p", "movie", anio - 1),
               caja("Ted Lasso 4x08", N + "/serie/episodio/aa0005", "4K")],
           3: []}
    r6 = (A._box_wf, A._kb_enqueue, A._catjob_wait, A._kbstatus_load)
    A._box_wf = lambda code, excluir=(), minimo=None: "222222"
    A._kb_enqueue = lambda b, ev: PEDIDO.append(dict(ev))
    A._catjob_wait = lambda job, espera: {"items": PAG.get(PEDIDO[-1].get("page"), [])}
    try:
        its = A._wfult_trae("movie")
        comprueba("una caja trae lo ultimo en pelis (paginas 1-3, hasta que se acaba), solo pelis",
                  [ev.get("page") for ev in PEDIDO] == [1, 2, 3]
                  and all(ev.get("kind") == "movie" and ev.get("op") == "latest" for ev in PEDIDO)
                  and [x["title"] for x in its] == ["Letras robadas", "Letras robadas", "El Dorado", "Poli malo"],
                  (PEDIDO, [x["title"] for x in its]))
        A._WFULT["movie"] = {"items": its, "ts": time.time()}
        est = A._wf_home_items("estrenos", 12)
        comprueba("Estrenos: solo de este año o del anterior (fuera El Dorado de 1966)",
                  [x["title"] for x in est] == ["Letras robadas", "Poli malo"], [x["title"] for x in est])
        comprueba("...y de cada peli, la MEJOR version (el 4K manda)",
                  est and est[0]["quality"] == "4K" and est[0]["url"].endswith("aa0002"), est[:1])
        cine = A._wf_home_items("peliculas", 12)
        comprueba("Cine: todas (tambien los clasicos)", [x["title"] for x in cine]
                  == ["Letras robadas", "El Dorado", "Poli malo"], [x["title"] for x in cine])
        comprueba("el scroll sigue por donde iba", [x["title"] for x in A._wf_home_items("peliculas", 2, 2)]
                  == ["Poli malo"])
        A._WFULT.clear()
        A._WFULT_VUELO.clear()
        try:
            os.remove(A._WFULT_FILE)
        except Exception:
            pass
        del PEDIDO[:]
        A._WFIDX.clear()
        A._WFIDX[N + "/pelicula/zz0001"] = {"t": "Vieja aprendida", "k": "movie", "q": "720p"}
        A._WFIDX[N + "/pelicula/zz0002"] = {"t": "Recien aprendida", "k": "movie", "q": "4K"}
        fb = A._wf_home_items("peliculas", 12)
        comprueba("sin nada aun: el indice, lo ultimo aprendido primero, y se pide lo ultimo por detras",
                  [x["title"] for x in fb] == ["Recien aprendida", "Vieja aprendida"]
                  and A._WFULT_VUELO.get("movie"), ([x["title"] for x in fb], A._WFULT_VUELO))
        comprueba("...pero NUNCA en Estrenos: el indice no sabe el año (salian Matrix y El Padrino) (dtbl64)",
                  A._wf_home_items_indice("estrenos", 12) == []
                  and A._wf_home_items_indice("peliculas", 12) != [], A._wf_home_items_indice("estrenos", 12))
    finally:
        A._box_wf, A._kb_enqueue, A._catjob_wait, A._kbstatus_load = r6
    ESTADO2 = {"111111": {"ts": time.time(), "v": "2.9.81"}, "222222": {"ts": time.time(), "v": "2.9.82"}}
    r7 = (A._kbstatus_load, A._box_live, A._live_boxes)
    A._kbstatus_load = lambda: ESTADO2
    A._box_live = lambda c: c in ESTADO2
    A._live_boxes = lambda *a, **k: ["111111", "222222"]
    try:
        comprueba("lo ultimo de WolfMax (pelis y series) solo a cajas al dia (dtbl56: 2.9.82)",
                  A._box_wf("", minimo=A._WFULT_MIN["tvshow"]) == "222222"
                  and A._box_wf("", minimo=A._WFULT_MIN["movie"]) == "222222")
        ESTADO2["222222"]["v"] = "2.9.81"
        comprueba("...y si no hay ninguna, ninguna", A._box_wf("", minimo=A._WFULT_MIN["tvshow"]) is None)
    finally:
        A._kbstatus_load, A._box_live, A._live_boxes = r7
    js = cli.get("/catdiag").get_json()
    comprueba("/catdiag ensena lo ultimo de WolfMax", "wf_ultimos" in js and "movie" in js["wf_ultimos"],
              js.get("wf_ultimos"))

    print("\n=== 10) El vigia de las fuentes (dtbl53) ===")
    FICHEROS.append(A._VIGIA_FILE)
    if os.path.exists(A._VIGIA_FILE):
        copia[A._VIGIA_FILE] = A._VIGIA_FILE + ".prueba_wf_bak"
        shutil.copy(A._VIGIA_FILE, copia[A._VIGIA_FILE])
        os.remove(A._VIGIA_FILE)
    BUENO = {"wf": [caja("Dune", N + "/pelicula/aa0001", "4K", "movie", 2021),
                    caja("Dune", N + "/pelicula/aa0002", "1080p", "movie", 2021),
                    caja("Dune La profecia 1x06", N + "/serie/episodio/aa0003", "4K")],
             "et": [dict(caja("Dune: Parte dos", "https://www.elitetorrent.com/p/1", "720p", "movie"), source="et"),
                    dict(caja("Exoplaneta Dune", "https://www.elitetorrent.com/p/2", "720p", "movie"), source="et"),
                    dict(caja("Hijos de Dune", "https://www.elitetorrent.com/p/3", "", "movie"), source="et")]}
    MALO = {"wf": [caja("Dune", N + "/serie/aa0009", "", "serie"), caja("Dune", N + "/serie/aa0010", "", "serie"),
                   caja("4x01", N + "/serie/episodio/aa0011", "4K")],
            "et": [dict(x, quality="") for x in BUENO["et"]]}
    ESCENA = {"v": BUENO}
    r8 = (A._box_wf, A._box_for, A._kb_enqueue, A._catjob_wait, A._dx_search_items, A._fuentes_caidas)
    r8b = A._vigia_enlace
    A._vigia_enlace = lambda src, items: "ok"     # el paso del enlace lo prueba resolver.py
    ULT = []
    A._box_wf = lambda code, excluir=(), minimo=None: "222222"
    A._box_for = lambda code: "222222"
    A._kb_enqueue = lambda b, ev: ULT.append(dict(ev))
    A._catjob_wait = lambda job, espera: {"items": ESCENA["v"].get(ULT[-1].get("srcs"), [])}
    A._dx_search_items = lambda q, max_pages=5, proxy=False: [
        {"title": "Dune", "source": "dx", "url": "https://divxtotal.foo/p/%d" % i, "quality": ""} for i in range(4)]
    A._fuentes_caidas = lambda: []
    try:
        v = A._vigia_ronda()
        comprueba("todo bien: las tres fuentes 'ok' (DivxTotal sin calidad es lo normal)",
                  [v[s]["ok"] for s in ("wf", "et", "dx")] == [True, True, True]
                  and v["wf"]["series_con_caps"] == 1 and all(ev.get("q") == "dune" for ev in ULT), v)
        ESCENA["v"] = MALO
        v = A._vigia_ronda()
        comprueba("WolfMax con series sin capitulos y un '4x01' suelto: se dice que falla y por que",
                  v["wf"]["ok"] is False and "series sin capitulos" in v["wf"]["problemas"]
                  and any("sin el nombre de su serie" in p for p in v["wf"]["problemas"]), v["wf"])
        comprueba("EliteTorrent sin calidad: se dice", v["et"]["ok"] is False
                  and any("sin calidad" in p for p in v["et"]["problemas"]), v["et"])
        desde = v["wf"]["mal_desde"]
        v = A._vigia_ronda()
        comprueba("recuerda DESDE CUANDO va mal (no se reinicia en cada mirada) y la historia",
                  v["wf"]["mal_desde"] == desde and v["wf"]["historia"].endswith("+--"), (v["wf"].get("mal_desde"), desde, v["wf"]["historia"]))
        A._fuentes_caidas = lambda: ["et"]
        v = A._vigia_ronda()
        comprueba("una fuente caida se dice caida (no 'le ha cambiado la web')",
                  v["et"].get("caida") is True and "caida" in v["et"]["problemas"][0], v["et"])
        comprueba("cada 3 h, contando lo que hizo el otro worker", A._vigia_toca() is False)
        js = cli.get("/catdiag").get_json()
        comprueba("/catdiag ensena el vigia", set(js.get("vigia") or {}) >= {"wf", "et", "dx"}, js.get("vigia"))
    finally:
        (A._box_wf, A._box_for, A._kb_enqueue, A._catjob_wait, A._dx_search_items, A._fuentes_caidas) = r8
        A._vigia_enlace = r8b

    print("\n=== 11) Dune de 1984 no es la de 2021 (dtbl54) ===")
    # WolfMax manda el año APARTE del titulo; TMDB se buscaba solo por "Dune",
    # casaba con la de 2021, se le ponia su año y al juntar versiones las de
    # 2021 desaparecian: "WolfMax 4K" de la ficha de 2021 era la de 1984
    PEDIDAS_TMDB = []

    class Resp(object):
        status_code = 200

        def __init__(self, res):
            self._r = res

        def json(self):
            return {"results": self._r}

    P84 = {"id": 841, "title": "Dune", "release_date": "1984-12-14", "popularity": 20,
           "vote_average": 6.2, "poster_path": "/d84.jpg"}
    P21 = {"id": 438631, "title": "Dune", "release_date": "2021-09-15", "popularity": 300,
           "vote_average": 7.8, "poster_path": "/d21.jpg"}

    def tmdb_falso(url, params=None, timeout=None):
        PEDIDAS_TMDB.append(dict(params or {}))
        if (params or {}).get("year") == "1984":
            return Resp([P84])
        if (params or {}).get("year") == "2021":
            return Resp([P21])
        return Resp([P21, P84])

    r9 = (A._TMDB_SESS.get, A._tmdb_is_down)
    A._TMDB_SESS.get = tmdb_falso
    A._tmdb_is_down = lambda: False
    A._CAT_TMDB_CACHE.clear()
    try:
        its = [caja("Dune", N + "/pelicula/dgtkej", "4K", "movie", 1984),
               caja("Dune", N + "/pelicula/d8k2pt", "4K", "movie", 2021)]
        A._cat_enrich(its)
        comprueba("cada Dune con SU año y SU ficha de TMDB",
                  [(x.get("year"), x.get("tmdb_id")) for x in its] == [("1984", 841), ("2021", 438631)],
                  [(x.get("year"), x.get("tmdb_id")) for x in its])
        c = A._wf_colapsa(its)
        comprueba("...y al juntar versiones siguen siendo DOS (la de 2021 no desaparece)",
                  sorted(x["url"][-6:] for x in c) == ["d8k2pt", "dgtkej"], [x["url"] for x in c])
        dt = {"title": "Dune (1984)", "kind": "movie", "source": "dt", "content_id": "123", "tabla": "peliculas"}
        A._cat_enrich([dt])
        comprueba("DonTorrent como siempre: el año del titulo manda", dt.get("tmdb_id") == 841, dt)
        sin = {"title": "Dune", "kind": "movie", "source": "et", "url": "https://www.elitetorrent.com/p/1", "year": 1999}
        A._cat_enrich([sin])
        comprueba("otras fuentes: el año aparte no se usa (solo WolfMax lo da fiable)",
                  not PEDIDAS_TMDB[-1].get("year"), PEDIDAS_TMDB[-1])
    finally:
        A._TMDB_SESS.get, A._tmdb_is_down = r9
        A._CAT_TMDB_CACHE.clear()
    comprueba("una serie de WolfMax sin capitulos (ficha de temporada vacia) se reconoce",
              A._wf_serie_vacia(caja("Ted Lasso", N + "/serie/zryh84", "4K"))
              and not A._wf_serie_vacia(dict(caja("Ted Lasso", N + "/serie/episodio/aa1", "4K"), eps=[{"label": "1x01"}]))
              and not A._wf_serie_vacia(caja("Dune", N + "/pelicula/aa2", "4K", "movie")))
    GUARDADO = []
    r10 = (A._box_wf, A._kb_enqueue, A._catjob_wait_any, A._catbox_get, A._catbox_put, A._fc_caido, A._fc_atajo)
    A._box_wf = lambda code, excluir=(), minimo=None: "222222"
    A._kb_enqueue = lambda b, ev: None
    A._catbox_get = lambda k: None
    A._catbox_put = lambda k, v: GUARDADO.append(k)
    A._fc_caido = lambda s: False
    A._fc_atajo = lambda s: False
    try:
        A._catjob_wait_any = lambda jobs, espera, *a, **k: {"items": [
            caja("Ted Lasso - 1ª Temporada", N + "/serie/zryh84", "", "serie")]}
        cli.get("/catetbox?code=222222&op=search&srcs=wf&q=ted%20lasso")
        comprueba("...y esa respuesta no se guarda 10 min para todos", not GUARDADO, GUARDADO)
        A._catjob_wait_any = lambda jobs, espera, *a, **k: {"items": ted}
        cli.get("/catetbox?code=222222&op=search&srcs=wf&q=ted%20lasso")
        comprueba("una buena, si", len(GUARDADO) == 1, GUARDADO)
    finally:
        (A._box_wf, A._kb_enqueue, A._catjob_wait_any, A._catbox_get, A._catbox_put, A._fc_caido, A._fc_atajo) = r10
    comprueba("las imagenes de fondo aguantan parentesis y comillas (caratulas de EliteTorrent)",
              "function cssUrl(" in A._CAT_PAGE and "background-image:url('+" not in A._CAT_PAGE
              and "esc(cssUrl(poster))" in A._CAT_PAGE and "esc(cssUrl(bd))" in A._CAT_PAGE)

    comprueba("la web lo explica (limite o captcha) con 'Buscar en otras fuentes'",
              "function limiteDlg(" in A._CAT_PAGE and "d.error)limiteDlg(" in A._CAT_PAGE
              and "d.error){limiteDlg(" in A._CAT_PAGE)

    print("\n=== 12) Packs de WolfMax: un archivo, una fila (dtbl55) ===")
    E = N + "/serie/episodio/"
    g = A._cat_group_episodes([
        dict(caja("Ted Lasso 1x06", E + "2ap2ud", "720p", "tvshow"), episode_end=9),
        caja("Ted Lasso 1x04", E + "zrgx6x", "720p", "tvshow"),
        dict(caja("Ted Lasso 1x10", E + "2s42pf", "720p", "tvshow"), episode_end="x")])
    labs = [e["label"] for e in (g[0].get("eps") or [])] if g else []
    comprueba("la busqueda rotula el pack entero si la caja dice donde acaba (2.9.81)",
              "1x06 al 1x09" in labs and "1x04" in labs and "1x10" in labs, labs)
    comprueba("...con su fin", any(e.get("episode_end") == 9 for e in g[0]["eps"]), g[0]["eps"])
    viejo = [{"label": "1x01", "season": 1, "episode": 1, "url": E + "zrfbxt", "src": "wf"},
             {"label": "1x04", "season": 1, "episode": 4, "url": E + "zrgx6x", "src": "wf"}]
    nuevo = [{"label": "1x01 al 1x03", "season": 1, "episode": 1, "episode_end": 3,
              "url": E + "zrfbxt", "src": "wf"}]
    labs = [e["label"] for e in A._eps_un_archivo(viejo + nuevo)]
    comprueba("dos filas del mismo archivo -> una, la del pack", labs == ["1x01 al 1x03", "1x04"], labs)
    comprueba("de otra fuente no se juntan por url",
              len(A._eps_un_archivo([{"label": "2x01", "url": "https://et/x", "src": "et"},
                                     {"label": "2x02", "url": "https://et/x", "src": "et"}])) == 2)
    try:
        os.remove(A._EPSC_FILE)
    except Exception:
        pass
    su = N + "/serie/zrdqqp"
    A._epsc_put("wf", su, "Ted Lasso", viejo)
    A._epsc_put("wf", su, "Ted Lasso", nuevo)
    labs = [e["label"] for e in A._epsc_limpia((A._epsc_get("wf", su) or {}).get("eps"))]
    comprueba("la lista guardada: lo nuevo sustituye al rotulo viejo del mismo archivo",
              labs == ["1x01 al 1x03", "1x04"], labs)
    # lo que YA estaba guardado mal (antes de dtbl55) se cura al leerlo
    A._epsc_put("wf", su + "b", "Ted Lasso", viejo)
    d0 = A._epsc_load()
    d0[A._epsc_clave("wf", su + "b")]["eps"].append(dict(nuevo[0], _ts=time.time()))
    labs = [e["label"] for e in A._epsc_limpia((A._epsc_get("wf", su + "b") or {}).get("eps"))]
    comprueba("sin ninguna caja al dia (off) la web pinta 'WolfMax —' y no reintenta 95 s (dtbl58)",
              "timeout:!!(d&&(d.timeout||d.off)),off:!!(d&&d.off)" in A._CAT_PAGE
              and "!(r&&r.off)&&wfRe<2" in A._CAT_PAGE)
    comprueba("...y lo guardado mal de antes se cura al leerlo", labs.count("1x01") == 0
              and "1x01 al 1x03" in labs, labs)
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
