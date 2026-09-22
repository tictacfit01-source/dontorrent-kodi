# -*- coding: utf-8 -*-
"""Como se reescribe una busqueda para el buscador LITERAL de DonTorrent.

"x men" da 0 fichas y "x-men" da 10 (medido el 20-09-2026): eso lo cubren las
variantes de espacios y guiones. Lo que quedaba suelto era escribirlo TODO
JUNTO ("xmen"), donde no hay por donde meter el guion a ciegas: desde el 22-09
se le pregunta a TMDB como se escribe. Aqui, sin red: TMDB simulado con la
forma real de sus respuestas.
"""
import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
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


print("\n=== Espacios y guiones (sin preguntar a nadie) ===")
comprueba("'x men' -> 'x-men'", A._dt_variantes("x men")[:1] == ["x-men"], A._dt_variantes("x men"))
comprueba("'x-men' -> 'x men' y 'xmen'", A._dt_variantes("x-men") == ["x men", "xmen"],
          A._dt_variantes("x-men"))
comprueba("una palabra suelta no tiene variantes de este tipo", A._dt_variantes("xmen") == [])

print("\n=== Todo junto: como lo escribe TMDB ===")
RESPUESTAS = {
    "xmen": [{"media_type": "tv", "name": "X Men, La Serie Animada"},
             {"media_type": "movie", "title": "X-Men: Apocalipsis"}],
    "spiderman": [{"media_type": "movie", "title": "Spider-Man: Brand New Day"}],
    "walle": [{"media_type": "person", "name": "Knut Walle"},
              {"media_type": "person", "name": "Walle Siivonen"}],
    "matrix": [{"media_type": "movie", "title": "Matrix"}],
    "wallee": [{"media_type": "movie", "title": "WALL·E"}],
}
llamadas = []


class R:
    def __init__(self, q, st=200):
        self.q, self.status_code = q, st

    def json(self):
        return {"results": RESPUESTAS.get(self.q, [])}


class Sess:
    def get(self, url, params=None, timeout=None):
        llamadas.append(params["query"])
        return R(params["query"], 429 if params["query"] == "baneado" else 200)


viejo_sess, viejo_down = A._TMDB_SESS, A._TMDB_DOWN_UNTIL[0]
A._TMDB_SESS = Sess()
A._DTV_TMDB.clear()
A._TMDB_DOWN_UNTIL[0] = 0.0
try:
    comprueba("'xmen' -> 'x-men'", A._dt_variante_tmdb("xmen") == "x-men")
    comprueba("'spiderman' -> 'spider-man'", A._dt_variante_tmdb("spiderman") == "spider-man")
    comprueba("personas NO cuentan ('walle' no se inventa nada)",
              A._dt_variante_tmdb("walle") == "")
    comprueba("un titulo de UNA palabra no genera variante ('matrix')",
              A._dt_variante_tmdb("matrix") == "")
    comprueba("'Wall·E' se parte bien ('wallee' no casa con 'wall e')",
              A._dt_variante_tmdb("wallee") == "")
    n = len(llamadas)
    A._dt_variante_tmdb("xmen")
    comprueba("la segunda vez sale de la cache (sin llamar a TMDB)", len(llamadas) == n)
    comprueba("frases, guiones, numeros sueltos o cosas raras: ni se pregunta",
              A._dt_variante_tmdb("x men") == "" and A._dt_variante_tmdb("x-men") == ""
              and A._dt_variante_tmdb("abc") == "" and A._dt_variante_tmdb("hola!") == ""
              and len(llamadas) == n, llamadas[n:])
    A._dt_variante_tmdb("baneado")
    comprueba("si TMDB contesta 429, se marca caido (el resto del enrich no espera)",
              A._tmdb_is_down())
    n2 = len(llamadas)
    comprueba("y mientras esta caido no se le pregunta",
              A._dt_variante_tmdb("antman") == "" and len(llamadas) == n2)
finally:
    A._TMDB_SESS = viejo_sess
    A._TMDB_DOWN_UNTIL[0] = viejo_down
    A._DTV_TMDB.clear()

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: 'xmen' ya encuentra X-Men en DonTorrent")
