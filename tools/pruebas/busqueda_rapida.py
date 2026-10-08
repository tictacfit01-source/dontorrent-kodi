# -*- coding: utf-8 -*-
"""La busqueda, mas rapida y sin respuestas a medias pegadas (dtbl72).

Revision en paralelo del 07-10 (cada hallazgo, verificado por dos agentes):
  1) una respuesta PARCIAL guardada tapaba lo que la caja traia despues: la
     segunda pasada del front (a los 7 s) recibia exactamente lo mismo;
  2) una tarjeta sin cartel porque TMDB NO la conoce marcaba la busqueda
     entera como parcial: 7 s mas de "Buscando en mas fuentes" y recalculo
     cada 150 s;
  3) /catsearch esperaba 1,5 s a DivxTotal con DonTorrent ya en mano (el front
     lo pide aparte y lo añade al final), y DivxTotal se buscaba DOS veces;
  4) "Gladiator (El gladiador) Version extendida" iba a TMDB tal cual;
  5) "gladiator 2" no encontraba "Gladiator II" (el buscador es literal).
Sin red.
"""
import os
import sys
import tempfile
import threading
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


RETO = "<html><head><title>Making sure you're not a bot!</title></head><body>Anubis" + "x" * 3000 + "</body></html>"
PAGINA = "<html>PAGINA DE RESULTADOS</html>" + "x" * 3000


def dt(titulo, cid, poster=None):
    return {"title": titulo, "source": "dt", "kind": "movie", "content_id": cid,
            "url": "https://dontorrent/pelicula/%s/x" % cid, "quality": "4K",
            "poster": poster, "rating": 7.0 if poster else None}


DX = {"title": "Gladiator", "source": "dx", "kind": "movie", "content_id": "dx1",
      "url": "https://divxtotal/x", "poster": "https://image.tmdb.org/p.jpg", "rating": 8.2,
      "year": "2000", "quality": "1080p"}

ESC = {"caja": RETO, "dx": [], "dx_tarda": 0.0, "parse": {}}
DXN = [0]


def dx_mock(q, max_pages=5, proxy=False):
    DXN[0] += 1
    time.sleep(ESC["dx_tarda"])
    return [dict(x) for x in ESC["dx"]]


tmp_dtq = tempfile.mktemp(prefix="mw_dtq_")
viejos = {k: getattr(A, k) for k in (
    "_cat_dt_html", "_dx_search_items", "_kb_enqueue", "_catjob_wait_any", "_catjob_wait",
    "_any_live_box", "_dt_caido", "_dt_caida_ya", "_cat_from_cache", "_et_search",
    "_sapi_credits_ok", "_tmdb_alt_titles", "_cat_disambiguate_years", "_cat_enrich",
    "_catsearch_save", "_catsearch_load", "_dt_caida_sondea", "_live_boxes", "_cat_parse_items",
    "_dt_variante_tmdb", "_cat_tmdb", "_tmdb_is_down", "_DTQ_FILE")}


def busca(q):
    A._CATSEARCH_INFLIGHT.clear()
    t0 = time.time()
    js = A.app.test_client().get("/catsearch?q=%s&code=111111" % q.replace(" ", "%20")).get_json()
    return js, time.time() - t0


def fuentes(js):
    return sorted(set(i.get("source") for i in (js.get("items") or [])))


try:
    A._DTQ_FILE = tmp_dtq
    A._DTQ_CACHE.clear()
    A._any_live_box = lambda max_age=90: "111111"
    A._live_boxes = lambda *a, **k: ["111111"]
    A._dt_caido = lambda: False
    A._dt_caida_ya = lambda: False
    A._dt_caida_sondea = lambda *a, **k: None
    A._cat_from_cache = lambda q: []
    A._et_search = lambda q: []
    A._sapi_credits_ok = lambda: False
    A._tmdb_alt_titles = lambda q: []
    A._dt_variante_tmdb = lambda q: ""
    A._cat_disambiguate_years = lambda it, dl, box=None, cap=12: (it, True)
    A._cat_enrich = lambda it, limit=None, vistos=None: it
    A._catsearch_save = lambda d: None
    A._catsearch_load = lambda: {}
    A._cat_dt_html = lambda q: ""                       # Render: nada
    A._dx_search_items = dx_mock
    A._kb_enqueue = lambda box, ev: None
    A._catjob_wait_any = lambda jobs, secs, ok=None, corta=None: {"html": ESC["caja"]}
    A._catjob_wait = lambda job, espera: {"html": ESC["caja"]}
    A._cat_parse_items = lambda h: [dict(x) for x in ESC["parse"].get(h, [])]

    print("\n=== 1) Una respuesta parcial no tapa lo que la caja trae despues ===")
    A._CATSEARCH_CACHE.clear()
    A._DXQ_MEMO.clear()
    ESC.update(caja=RETO, dx=[DX])
    js1, _ = busca("gladiator")
    comprueba("la caja trae un reto: parcial, con lo de DivxTotal",
              js1.get("partial") is True and fuentes(js1) == ["dx"], js1)
    time.sleep(0.02)
    A._dtq_put("gladiator", [dt("Gladiator II", "28412", "https://image.tmdb.org/g2.jpg")])
    js2, _ = busca("gladiator")
    comprueba("la 2a pasada RECALCULA y trae lo que la caja dejo (antes: la misma parcial 150 s)",
              not js2.get("cached") and "dt" in fuentes(js2), js2)
    js3, _ = busca("gladiator")
    comprueba("...y sin nada nuevo de la caja, la guardada vale (no se recalcula en cadena)",
              js3.get("cached") is True, js3)

    print("\n=== 2) 'TMDB no lo conoce' no es una respuesta a medias ===")
    A._cat_enrich = viejos["_cat_enrich"]
    A._cat_tmdb = lambda title, kind="movie", anio=None: {"poster": None, "year": None, "rating": None}
    A._tmdb_is_down = lambda: False
    ESC.update(caja=PAGINA, dx=[], parse={PAGINA: [dt("Gladiator (El gladiador) Version extendida", "111"),
                                                   dt("Gladiator", "222", "https://image.tmdb.org/g.jpg")]})
    A._CATSEARCH_CACHE.clear()
    A._DXQ_MEMO.clear()
    A._DTQ_CACHE.clear()
    os.path.exists(tmp_dtq) and os.remove(tmp_dtq)
    js, t = busca("gladiator ve")
    comprueba("una tarjeta sin cartel porque TMDB contesto que no la conoce: NO parcial",
              js.get("partial") is False and len(js.get("items") or []) >= 1, js)
    A._tmdb_is_down = lambda: True
    A._CATSEARCH_CACHE.clear()
    js, t = busca("gladiator ve2")
    comprueba("...pero con TMDB caido, si (la 2a pasada lo completara)", js.get("partial") is True, js)
    A._tmdb_is_down = viejos["_tmdb_is_down"]
    A._cat_enrich = lambda it, limit=None, vistos=None: it

    print("\n=== 3) Con DonTorrent en mano, no se espera a DivxTotal ===")
    ESC.update(caja=PAGINA, dx=[DX], dx_tarda=1.2,
               parse={PAGINA: [dt("Gladiator", "222", "https://image.tmdb.org/g.jpg")]})
    A._CATSEARCH_CACHE.clear()
    A._DXQ_MEMO.clear()
    js, t = busca("gladiator rapido")
    comprueba("contesta sin los 1,2 s de DivxTotal (%.2f s; antes esperaba hasta 1,5)" % t,
              t < 0.9 and fuentes(js) == ["dt"], (t, fuentes(js)))
    ESC.update(caja=RETO, parse={})
    A._CATSEARCH_CACHE.clear()
    A._DXQ_MEMO.clear()
    js, t = busca("gladiator solo dx")
    comprueba("...pero si DonTorrent no trae nada, DivxTotal SI se espera (es lo unico)",
              fuentes(js) == ["dx"], (t, js))

    print("\n=== 4) DivxTotal, una sola vez por busqueda ===")
    time.sleep(1.3)                           # que acabe el hilo de DX de antes
    A._DXQ_MEMO.clear()
    DXN[0] = 0
    ESC.update(dx=[DX], dx_tarda=0.4)
    res = []
    hs = [threading.Thread(target=lambda: res.append(A._dx_busca_compartida("matrix")))
          for _ in range(2)]
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    comprueba("/catsearch y /catdxsearch a la vez: UNA busqueda a DivxTotal, las dos con su resultado",
              DXN[0] == 1 and len(res) == 2 and all(len(r) == 1 for r in res), (DXN[0], res))
    comprueba("...cada una con SU copia (el enriquecimiento escribe dentro)", res[0][0] is not res[1][0])
    A._dx_busca_compartida("matrix")
    comprueba("y repetirla en 90 s no vuelve a DivxTotal", DXN[0] == 1, DXN[0])
    ESC.update(dx=[], dx_tarda=0.0)
    A._dx_busca_compartida("nada")
    A._dx_busca_compartida("nada")
    comprueba("un vacio NO se recuerda (puede ser un tropiezo)", DXN[0] == 3, DXN[0])

    print("\n=== 5) La edicion no va a TMDB; la secuela, en romanos ===")
    for t_in, t_out in (("Gladiator (El gladiador) Versión extendida", "Gladiator"),
                        ("Gladiator (El gladiador) Version extendida (HDR)", "Gladiator"),
                        ("Avatar Edición especial", "Avatar"),
                        ("Blade Runner Montaje del director", "Blade Runner"),
                        ("Terminator 2 Remasterizada 4K", "Terminator 2"),
                        ("La familia extendida", "La familia extendida"),
                        ("Edición especial", "Edición especial")):
        r = " ".join(A._cat_clean_title(t_in).split())
        comprueba("%r -> %r" % (t_in, t_out), r == t_out, r)
    v = viejos["_dt_variante_tmdb"]
    comprueba("'gladiator 2' prueba primero 'gladiator ii' (DonTorrent: 'Gladiator II')",
              A._dt_variantes("gladiator 2")[:1] == ["gladiator ii"], A._dt_variantes("gladiator 2"))
    comprueba("...y al reves: 'rocky ii' -> 'rocky 2'", A._dt_variantes("rocky ii")[:1] == ["rocky 2"],
              A._dt_variantes("rocky ii"))
    comprueba("lo de siempre no cambia: 'x men' -> 'x-men'; un año no es una secuela; la V tampoco",
              A._dt_variantes("x men") == ["x-men"]
              and A._dt_variantes("blade runner 2049") == ["blade-runner-2049"]
              and A._dt_variantes("v de vendetta") == ["v-de-vendetta"] and A._dt_variantes("rocky v") == ["rocky-v"],
              (A._dt_variantes("blade runner 2049"), A._dt_variantes("v de vendetta"), A._dt_variantes("rocky v")))
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._CATSEARCH_CACHE.clear()
    A._DXQ_MEMO.clear()
    A._DTQ_CACHE.clear()
    for f in (tmp_dtq, tmp_dtq + ".tmp"):
        try:
            os.remove(f)
        except Exception:
            pass

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: la busqueda contesta antes y no se queda a medias")
