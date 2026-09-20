# -*- coding: utf-8 -*-
"""El filtro de relevancia, contra casos reales.

Cada caso: (consulta, titulo, deberia_pasar). Los que llevan comentario salen
de quejas reales del dueno o de titulos que estan hoy en produccion.
"""
import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..", "render_relay"))
import app as A                                          # noqa: E402

CASOS = [
    # --- el fallo que reporto el dueno -----------------------------------
    ("x men", "The Gentlemen 2 Temporada", False),       # ruido: gentle-MEN
    ("x men", "X-Men", True),                            # la original, la que falta
    ("x men", "X-Men Origins Wolverine", True),
    ("x men", "X Men Fenix Oscura", True),
    ("x men", "X-men evolution", True),
    ("x men", "Men in Black", False),                    # solo comparte "men"
    ("x men", "Sleboda 2 Temporada", False),
    ("x men", "Xmen", True),                             # junto: "x" empieza "xmen"
    # --- la misma trampa con otras palabras -------------------------------
    ("amor", "El amortiguador", False),
    ("amor", "Amor en obras", True),
    ("casa", "La casa del dragon", True),
    ("casa", "Recien casada", False),
    ("mar", "Marte", False),
    ("mar", "Mar adentro", True),
    ("cielo", "Desde mi cielo", True),
    ("desde mi cielo", "El mismo cielo", False),          # el caso del docstring
    # --- escribir a medias tiene que seguir valiendo ----------------------
    ("interes", "Interestelar", True),
    ("ted lasso", "Ted Lasso", True),
    ("lasso", "Ted Lasso", True),
    ("silo", "Silo", True),
    ("widows bay", "La maldicion de Widows Bay", True),
    # --- tildes, mayusculas, guiones y plurales ---------------------------
    ("cancion", "La Canción", True),
    ("el club gastronomico", "El club gastronómico 1 Temporada", True),
    ("hombre", "Los hombres", True),
    ("hombres", "El hombre", True),
    # --- lo que se escribe junto y la web separa (o al reves) --------------
    ("spiderman", "Spider-Man", True),
    ("spider man", "Spiderman", True),
    ("starwars", "Star Wars", True),
    ("amor", "Amortiguador", False),
    ("xmen", "X-Men", True),              # lo que probablemente busco el dueno
    ("xmen", "The Gentlemen 2 Temporada", False),
    ("xmen", "X-Men Origins Wolverine", True),
    ("casa", "Lacasitos", False),
    # --- que no se cuele el catalogo entero --------------------------------
    ("zzzqwerty", "X-Men", False),
    ("batman", "Superman", False),
]


def main():
    fallos = []
    for q, t, esperado in CASOS:
        got = A._q_relevant(t, q)
        ok = (got == esperado)
        if not ok:
            fallos.append((q, t, esperado, got))
        print("%s  q=%-18s %-38s esperado=%-5s dio=%s"
              % ("ok " if ok else "MAL", repr(q), repr(t)[:38], esperado, got))
    print("\n%d/%d correctos" % (len(CASOS) - len(fallos), len(CASOS)))
    if fallos:
        print("FALLAN:")
        for f in fallos:
            print("   ", f)
        return 1
    print("TODOS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
