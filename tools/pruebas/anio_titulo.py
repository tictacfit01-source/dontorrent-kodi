# -*- coding: utf-8 -*-
"""El año y el titulo original entre parentesis, en el RELAY.

22-09-2026: WolfMax titula "Poli malo (Bad Man) (2025)" y DonTorrent "Poli
malo", con el mismo año y el mismo tmdb_id. Salian DOS tarjetas de la misma
pelicula: cuatro o cinco parejas por pestana del Inicio. La web lo arregla en
mergeResults (tools/pruebas/fusion.js) y el relay aqui: `_titulo_clave` para el
dedup de la busqueda y `_anio_fuera` para el titulo que se pinta.

Sin red: solo funciones puras del relay.
"""
import copy
import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"     # nada de red de fondo en la prueba
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


print("\n=== _titulo_clave: lo que decide si son la misma ===")
CASOS = [
    # (titulo, año del item, titulo esperado, año esperado)
    ("Gail Daughtry y el vale por un rollo VIP (2026)", "2026",
     "Gail Daughtry y el vale por un rollo VIP", "2026"),
    ("Poli malo (Bad Man) (2025)", "2025", "Poli malo", "2025"),
    ("Cuatro historias de deseo 3 (Lust Stories 3) (2026)", "",
     "Cuatro historias de deseo 3", "2026"),
    ("A La Cara (2026)", None, "A La Cara", "2026"),
    ("El Caso Braibanti [2022]", "", "El Caso Braibanti", "2022"),
    # el año del item manda sobre el del titulo
    ("Suspiria (1977)", "2018", "Suspiria", "2018"),
    # sin año, el parentesis se queda: "Dune (Parte Dos)" NO es "Dune"
    ("Dune (Parte Dos)", "", "Dune (Parte Dos)", ""),
    # con año, el parentesis del final se quita (titulo original)
    ("Dune (Parte Dos)", "2024", "Dune", "2024"),
    # titulos que SON un numero o llevan uno dentro
    ("1917 (2019)", "", "1917", "2019"),
    ("Blade Runner 2049 (2017)", "", "Blade Runner 2049", "2017"),
    ("Blade Runner 2049", "", "Blade Runner 2049", ""),
    # un parentesis que es TODO el titulo no se toca
    ("(2026)", "", "(2026)", ""),
    ("", "", "", ""),
]
for tit, y, et, ey in CASOS:
    t, yy = A._titulo_clave({"title": tit, "year": y})
    comprueba("%r -> %r %r" % (tit, et, ey), (t.strip(), yy) == (et, ey), (t, yy))

print("\n=== _anio_fuera: el titulo que se PINTA ===")
items = [
    {"title": "Gail Daughtry y el vale por un rollo VIP (2026)", "year": "2026"},
    {"title": "Poli malo (Bad Man) (2025)"},
    {"title": "Silo", "year": "2023"},
    {"title": "(2026)"},
    {"title": "Momentos decisivos Generacion 11-S (2026)", "kind": "serie"},
]
antes = copy.deepcopy(items)
A._anio_fuera(items)
comprueba("quita el año del final", items[0]["title"] == "Gail Daughtry y el vale por un rollo VIP",
          items[0]["title"])
comprueba("y conserva el año que ya tenia", items[0]["year"] == "2026")
comprueba("el titulo original se QUEDA (es informacion)",
          items[1]["title"] == "Poli malo (Bad Man)", items[1]["title"])
comprueba("el año del titulo pasa a ser el año", items[1].get("year") == "2025", items[1])
comprueba("lo que no lleva año no cambia", items[2] == antes[2])
comprueba("un titulo que es solo '(2026)' no se queda vacio", items[3]["title"] == "(2026)")
comprueba("vale tambien para series", items[4]["title"] == "Momentos decisivos Generacion 11-S"
          and items[4]["year"] == "2026", items[4])
otra = copy.deepcopy(items)
A._anio_fuera(otra)
comprueba("idempotente: pasar dos veces no cambia nada", otra == items)

print("\n=== El dedup de la busqueda (_cat_rank_dedup) ===")
res = A._cat_rank_dedup([
    {"title": "Poli malo", "kind": "movie", "year": "2025", "quality": "DVDRIP", "source": "dt"},
    {"title": "Poli malo (Bad Man) (2025)", "kind": "movie", "year": "2025", "quality": "BluRay", "source": "wf"},
    {"title": "Suspiria", "kind": "movie", "year": "2018", "source": "dt"},
    {"title": "Suspiria (1977)", "kind": "movie", "source": "wf"},
    {"title": "Dune", "kind": "movie", "year": "2021", "source": "dt"},
    {"title": "Dune (Parte Dos)", "kind": "movie", "source": "et"},
], "")
tits = sorted(x["title"] + "|" + x.get("source", "") for x in res)
comprueba("Poli malo: UNA tarjeta, la de mejor calidad",
          sum(1 for x in res if x["title"].startswith("Poli malo")) == 1
          and any(x["title"].startswith("Poli malo") and x["source"] == "wf" for x in res), tits)
comprueba("Suspiria 1977 y 2018 siguen siendo dos",
          sum(1 for x in res if x["title"].startswith("Suspiria")) == 2, tits)
comprueba("Dune y Dune (Parte Dos) siguen siendo dos",
          sum(1 for x in res if x["title"].startswith("Dune")) == 2, tits)

print("\n=== Y al servir (_al_servir) no se pierde nada ===")
serv = A._al_servir([{"title": "El Caso Braibanti (2022)", "kind": "movie", "source": "wf",
                      "poster": "https://image.tmdb.org/t/p/w342/x.jpg"}])
comprueba("titulo limpio + año + caratula normalizada",
          serv[0]["title"] == "El Caso Braibanti" and serv[0]["year"] == "2022"
          and "/w500/" in serv[0]["poster"], serv[0])

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el año y el titulo original ya no parten la misma pelicula")
