# -*- coding: utf-8 -*-
"""Las series del Inicio con la ficha de OTRA cosa (dtbl59 + addon 2.9.84).

29-09: en Estrenos, "Brothers" (serie de 2026, McConaughey y Harrelson) salia
con el cartel, el año y la nota de la PELICULA de 2009 ("Hermanos"); lo mismo
"Historia de dos ciudades" (la de 1935) y "A la deriva" (la de 2018). La caja
castigaba a todo lo que tuviera menos de 40 votos -- es decir, a los estrenos --
y el relay daba por bueno para siempre lo que la caja mandaba (`dtok`).

Aqui: el relay NO se fia del meta de una caja anterior para una SERIE (lo
rehace el, buscandola como serie, y se lo vuelve a pedir a las cajas), uno
nuevo (`mv` 2) manda y no lo pisa uno viejo, y la tarjeta cuenta temporadas
DISTINTAS (la misma en HDTV y en 720p no son "2 temporadas"). Sin red: TMDB de
mentira (el de verdad lo mira tmdb_match.py).
"""
import json
import os
import re
import shutil
import subprocess
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


HERMANOS = {"poster": "https://image.tmdb.org/t/p/w500/hermanos2009.jpg", "year": "2009",
            "rating": 7.3, "tmdb_id": 7445, "title": "Hermanos", "dt_title": "Brothers - 1ª Temporada",
            "dtok": 1}                                   # lo que mando una caja 2.9.83
BROTHERS = {"poster": "https://image.tmdb.org/t/p/w500/brothers2026.jpg", "year": "2026",
            "rating": 8.7, "tmdb_id": 250203, "title": "Brothers", "dt_title": "Brothers - 1ª Temporada",
            "dtok": 1, "mv": 2}                          # lo que manda una caja 2.9.84

os.makedirs("/tmp", exist_ok=True)
FICH = [A._CAT_ENRICH_FILE, A._CATBROWSE_FILE]
copia = {}
for f in FICH:
    if os.path.exists(f):
        copia[f] = f + ".prueba_st_bak"
        shutil.copy(f, copia[f])
tmp = tempfile.mkdtemp(prefix="mw_series_")
try:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass

    print("\n=== 1) Cuanto vale el meta de una caja ===")
    comprueba("a ciegas 0, con ficha 1, con ficha y emparejamiento nuevo 2",
              [A._meta_rango(m) for m in ({}, None, {"poster": "x"}, HERMANOS, BROTHERS)] == [0, 0, 0, 1, 2])
    comprueba("una SERIE solo manda con el emparejamiento nuevo",
              not A._meta_fiable(HERMANOS, "serie") and A._meta_fiable(BROTHERS, "serie"))
    comprueba("una PELI con la ficha sigue mandando (su ficha trae año y director)",
              A._meta_fiable(HERMANOS, "movie") and not A._meta_fiable({"poster": "x"}, "movie"))

    print("\n=== 2) Lo guardado: el nuevo manda y no lo pisa el viejo ===")
    A._cat_enrich_store({"130334": HERMANOS})
    A._cat_enrich_store({"130334": BROTHERS})
    comprueba("el de la 2.9.84 sustituye al de antes", A._cat_enrich_load()["130334"]["tmdb_id"] == 250203)
    A._cat_enrich_store({"130334": HERMANOS})
    comprueba("...y una caja atrasada no lo deshace en su siguiente vuelta",
              A._cat_enrich_load()["130334"]["tmdb_id"] == 250203, A._cat_enrich_load()["130334"])

    print("\n=== 3) El enriquecimiento no se fia del meta viejo de una serie ===")
    os.remove(A._CAT_ENRICH_FILE)
    A._cat_enrich_store({"130334": HERMANOS, "555": dict(HERMANOS, tmdb_id=1, dt_title="Una peli")})
    PREGUNTAS = []

    def tmdb_falso(title, kind="movie", anio=None):
        PREGUNTAS.append((title, kind))
        return dict(BROTHERS) if kind == "tv" else {"poster": None, "year": None, "rating": None}
    viejo = A._cat_tmdb
    A._cat_tmdb = tmdb_falso
    try:
        its = [{"title": "Brothers", "kind": "serie", "source": "dt", "content_id": "130334", "thumb": "t.jpg"},
               {"title": "Una peli", "kind": "movie", "source": "dt", "content_id": "555", "thumb": "p.jpg"}]
        en = A._cat_enrich([dict(x) for x in its])
    finally:
        A._cat_tmdb = viejo
    serie = [x for x in en if x["content_id"] == "130334"][0]
    peli = [x for x in en if x["content_id"] == "555"][0]
    comprueba("la serie se busca COMO SERIE y sale la de 2026",
              ("Brothers", "tv") in PREGUNTAS and serie.get("year") == "2026"
              and "brothers2026" in (serie.get("poster") or ""), (PREGUNTAS, serie))
    comprueba("la peli con su ficha sigue sin gastar TMDB", ("Una peli", "movie") not in PREGUNTAS
              and peli.get("tmdb_id") == 1, (PREGUNTAS, peli))

    print("\n=== 4) Lo que empuja una caja al Inicio (/catenrich) ===")
    os.remove(A._CAT_ENRICH_FILE)
    cli = A.app.test_client()
    A._CATBROWSE_CACHE["estrenos:1"] = {"ts": time.time(), "items": [
        {"title": "Brothers", "kind": "serie", "source": "dt", "content_id": "130334",
         "thumb": "t.jpg", "poster": "t.jpg"}]}
    cli.post("/catenrich", json={"kind": "estrenos", "meta": {"130334": HERMANOS}})
    it = A._CATBROWSE_CACHE["estrenos:1"]["items"][0]
    comprueba("el meta viejo de una caja atrasada NO se pinta en la serie",
              it.get("poster") == "t.jpg" and it.get("year") in (None, ""), it)
    cli.post("/catenrich", json={"kind": "estrenos", "meta": {"130334": BROTHERS}})
    it = A._CATBROWSE_CACHE["estrenos:1"]["items"][0]
    comprueba("el de la 2.9.84 si", "brothers2026" in (it.get("poster") or "") and str(it.get("year")) == "2026", it)
    A._CATBROWSE_CACHE.pop("estrenos:1", None)

    print("\n=== 5) La tarjeta cuenta temporadas DISTINTAS ===")
    node = shutil.which("node")
    m = re.search(r"( // Temporadas DISTINTAS:.*?else if\(_tp===1&&_tn\[0\]>1\)_ts=' · T'\+_tn\[0\];)",
                  A._CAT_PAGE, re.S)
    comprueba("el trozo esta en la pagina", bool(m))
    if node and m:
        prog = ("function et(x){" + m.group(1) + " return _ts}\n" +
                "console.log(JSON.stringify([" +
                "et({temps:[{n:1,quality:'HDTV'},{n:1,quality:'720p'}]})," +
                "et({temps:[{n:1},{n:2},{n:2}]})," +
                "et({temps:[{n:2,quality:'HDTV'},{n:2,quality:'720p'}]})," +
                "et({temps:[{n:1}]}),et({})]))")
        f = os.path.join(tmp, "t.js")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(prog)
        out = subprocess.run([node, f], capture_output=True, text=True, encoding="utf-8", timeout=30)
        try:
            r = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            r = out.stderr
        comprueba("la misma temporada en HDTV y 720p no son '2 temporadas'; T1+T2 si; solo la T2, 'T2'",
                  r == ["", " · 2 temporadas", " · T2", "", ""], r)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    for f in FICH:
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
print("TODO OK: una serie ya no se queda con la ficha de la pelicula homonima")
