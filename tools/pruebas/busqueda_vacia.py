# -*- coding: utf-8 -*-
"""Cuando DonTorrent NO tiene lo que buscas (dtbl60).

03-10: "Robot salvaje" y "La sociedad de la nieve" tardaban 6-7 s y salian
"parciales" (la web reintentaba): la caja traia en ~1 s la pagina de
DonTorrent con "Se han encontrado 0 resultados", pero el relay seguia
esperando al intento DIRECTO desde Render, que con la IP baneada tarda en
rendirse. Y el ultimo recurso (el titulo original de TMDB) probaba ese mismo
camino muerto antes que la caja.

Aqui, con DonTorrent-directo "colgado" 10 s: la respuesta llega en pocos
segundos, no es parcial, el titulo original se le pide a la CAJA, y un reto
anti-bots o un error que trajera la caja NO cuenta como "0". Sin red.
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


# Lo esencial de la pagina real (03-10, "robot salvaje")
CERO = '''<html><head><title>Busqueda: robot salvaje - DonTorrent</title></head><body>
<div class="card-body"><h1 class="display-4">Resultados</h1>
<p class="lead">Has realizado una búsqueda con <b>robot salvaje</b>.</p>
<p class="lead">Se han encontrado <b>0</b> resultados.</p>
<hr class="my-4"><span><span><b>No se encontraron resultados</b>.<br><br>
&nbsp;&nbsp;# Consejos:<br></span></span></div>''' + ("<!-- relleno -->" * 400) + "</body></html>"
RETO = "<html><head><title>Making sure you're not a bot!</title></head><body>Anubis" + "x" * 3000 + "</body></html>"
DX = {"title": "Robot salvaje", "source": "dx", "kind": "movie", "content_id": "dx1",
      "url": "https://divxtotal/x", "poster": "https://image.tmdb.org/p.jpg", "rating": 8.2,
      "year": "2024", "quality": "1080p"}

comprueba("su pagina de resultados se reconoce (tambien con 0)", A._dt_es_resultados(CERO))
comprueba("un reto anti-bots o nada, no", not A._dt_es_resultados(RETO) and not A._dt_es_resultados(""))

DIRECTO, CAJA, ORDEN = [], [], []
comprueba("la variante con guiones, solo en titulos cortos ('x men' -> 'x-men')",
          A._dt_variantes("x men") == ["x-men"] and A._dt_variantes("spider man") == ["spider-man"]
          and A._dt_variantes("la sociedad de la nieve") == [], A._dt_variantes("la sociedad de la nieve"))


def directo_colgado(q):
    DIRECTO.append(q)
    time.sleep(10)          # Render baneado: tarda en rendirse
    return ""


def prueba(pagina, dx, q):
    """Una /catsearch con la caja trayendo `pagina` y DivxTotal `dx`."""
    del DIRECTO[:]
    del CAJA[:]
    del ORDEN[:]
    A._CATSEARCH_CACHE.clear()
    A._cat_dt_html = directo_colgado
    A._dx_search_items = lambda q, proxy=False: [dict(x) for x in dx]

    def encola(box, ev):
        CAJA.append(ev.get("q"))
        ORDEN.append("pide " + str(ev.get("q")))

    def espera(jobs, secs, ok=None, corta=None):
        ORDEN.append("espera")
        return {"html": pagina}
    A._kb_enqueue = encola
    A._catjob_wait_any = espera
    A._catjob_wait = lambda job, espera: {"html": pagina}
    t0 = time.time()
    js = A.app.test_client().get("/catsearch?q=%s&code=111111" % q.replace(" ", "%20")).get_json()
    return js, time.time() - t0


viejos = {k: getattr(A, k) for k in (
    "_cat_dt_html", "_dx_search_items", "_kb_enqueue", "_catjob_wait_any", "_catjob_wait",
    "_any_live_box", "_dt_caido", "_dt_caida_ya", "_cat_from_cache", "_dtq_get", "_et_search",
    "_sapi_credits_ok", "_tmdb_alt_titles", "_cat_disambiguate_years", "_cat_enrich",
    "_catsearch_save", "_catsearch_load", "_dt_caida_sondea", "_live_boxes")}
try:
    A._any_live_box = lambda max_age=90: "111111"
    A._live_boxes = lambda *a, **k: ["111111"]
    A._dt_caido = lambda: False
    A._dt_caida_ya = lambda: False
    A._dt_caida_sondea = lambda *a, **k: None
    A._cat_from_cache = lambda q: []
    A._dtq_get = lambda q: []
    A._et_search = lambda q: []
    A._sapi_credits_ok = lambda: False
    A._tmdb_alt_titles = lambda q: ["The Wild Robot"]
    A._cat_disambiguate_years = lambda it, dl, box=None, cap=12: (it, True)
    A._cat_enrich = lambda it, limit=None: it
    A._catsearch_save = lambda d: None
    A._catsearch_load = lambda: {}

    print("\n=== 1) DonTorrent dice que no lo tiene; DivxTotal si ===")
    js, t = prueba(CERO, [DX], "robot salvaje")
    comprueba("contesta enseguida (%.1f s; antes ~7 s esperando al camino muerto)" % t, t < 4.5, t)
    comprueba("con lo de DivxTotal y NO parcial (la web no reintenta)",
              [i.get("source") for i in js.get("items") or []] == ["dx"] and js.get("partial") is False, js)
    comprueba("...y se guarda como definitiva (no caduca a los 150 s)",
              (A._CATSEARCH_CACHE.get(next(iter(A._CATSEARCH_CACHE), "")) or {}).get("ttl") is None,
              A._CATSEARCH_CACHE)

    comprueba("la variante 'robot-salvaje' sale A LA VEZ que la literal (antes, otro viaje despues)",
              ORDEN[:3] == ["pide robot salvaje", "pide robot-salvaje", "espera"], ORDEN)

    print("\n=== 2) Nadie lo tiene: el titulo original, a la CAJA ===")
    js, t = prueba(CERO, [], "robot salvaje")
    comprueba("pregunta a la caja por 'The Wild Robot'", "The Wild Robot" in CAJA, CAJA)
    comprueba("...y NO al camino directo muerto (antes: 3,5 s por titulo)",
              "The Wild Robot" not in DIRECTO, DIRECTO)
    comprueba("vacio, en pocos segundos (%.1f s) y no parcial" % t, t < 6.0 and js.get("items") == []
              and js.get("partial") is False, (t, js))

    print("\n=== 3) Lo que la caja trae NO es su pagina de resultados ===")
    js, t = prueba(RETO, [DX], "robot salvaje")
    comprueba("un reto anti-bots no es un '0': sigue siendo parcial (la web reintentara)",
              js.get("partial") is True, js)
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._CATSEARCH_CACHE.clear()

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: si DonTorrent no lo tiene, se dice enseguida")
