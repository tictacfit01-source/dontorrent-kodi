# -*- coding: utf-8 -*-
"""Los datos de cada persona, intactos (rediseño de la web, dtbl126).

Israel: "muy importante que esto sea una actualización que no influya en la
configuración de cada usuario: deben mantener sus listas, sus pelis y su
historial". El rediseño cambia COMO se ve la web, nunca lo que guarda.

Compara la web de ahora con la de una version de referencia (por defecto la
del commit `REF`, la ultima publicada antes del rediseño) y exige:
  1) las MISMAS claves de localStorage, leidas y escritas igual;
  2) cada funcion de la web que toca lo guardado (localStorage, las listas, el
     historial, los Kodis, los vistos, las lapidas y la copia de Google)
     IDENTICA letra a letra;
  3) las lineas sueltas que cargan lo guardado al arrancar, identicas.
Si un cambio necesita tocar una de esas funciones, esta prueba lo frena: hay
que pensarlo dos veces y migrar con cuidado, no de pasada.

Uso: python tools/pruebas/datos_intactos.py [ref]
"""
import os
import re
import subprocess
import sys

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
REF = sys.argv[1] if len(sys.argv) > 1 else "db54696"     # dtbl125, antes del rediseño


def pagina(txt):
    a = txt.index(chr(10) + '_CAT_PAGE = r"""')
    b = txt.index('</html>"""', a)
    return txt[a:b]


def script(p):
    """Todo lo que va detras del <body>: la web lleva varios <script>."""
    return p[p.index("</style></head>"):]


def funciones(js):
    """{nombre: texto} de las funciones de primer nivel (de 'function x(' a la
    siguiente de primer nivel)."""
    pos = [(m.start(), m.group(1)) for m in re.finditer(r"\nfunction (\w+)\(", js)]
    out = {}
    for n, (a, nom) in enumerate(pos):
        b = pos[n + 1][0] if n + 1 < len(pos) else len(js)
        out[nom] = js[a:b].strip()
    return out


TOCA = re.compile(r"localStorage|saveFavs\(|lstSave\(|histSave\(|saveDevs\(|saveSeen\(|"
                  r"delGuarda\(|delMarca\(|syncSubir|syncBajar|favs\s*=|favs\.push|"
                  r"favs\.splice|hist\s*=|hist\.unshift|hist\.splice|LST\s*=|LST\.push|"
                  r"seen\.splice|seen\.unshift")
CLAVES = re.compile(r"localStorage\.(getItem|setItem|removeItem)\('([^']+)'")
# Claves NUEVAS revisadas a mano: comodidades de cada aparato que no tocan
# listas, historial, Kodis ni vistos. Se añaden aquí solo tras pensarlo.
#   mw_cbdonde: "En la tele" o "Aquí" (dtbl131)
#   mw_vppos:   por dónde iba en el reproductor de la web (dtbl131)
NUEVAS_OK = {"mw_cbdonde", "mw_vppos"}
FUNC_NUEVAS_OK = {"cbDonde", "cbPonDonde", "vpGuarda", "vpPosLee"}


def _sin_nuevas(lineas):
    return [l for l in lineas if not any(k in l for k in NUEVAS_OK)]


def main():
    viejo = subprocess.check_output(["git", "-C", RAIZ, "show", "%s:render_relay/app.py" % REF])
    viejo = viejo.decode("utf-8")
    with open(os.path.join(RAIZ, "render_relay", "app.py"), encoding="utf-8") as f:
        nuevo = f.read()
    jv, jn = script(pagina(viejo)), script(pagina(nuevo))
    fallos = 0
    if len(CLAVES.findall(jv)) < 20 or len(funciones(jv)) < 200:
        print("FALLO: no encuentro el JavaScript de la web (la prueba no veria nada)")
        return 1

    def ok(c, msg):
        nonlocal fallos
        print(("  OK   " if c else "  FALLO ") + msg)
        if not c:
            fallos += 1

    print("1. Las mismas claves de localStorage, usadas igual")
    cv, cn = sorted(CLAVES.findall(jv)), sorted(c for c in CLAVES.findall(jn) if c[1] not in NUEVAS_OK)
    ok(cv == cn, "%d usos de %d claves" % (len(cv), len(set(k for _, k in cv))))
    if cv != cn:
        print("     antes-ahora:", sorted(set(cv) - set(cn)), " ahora-antes:", sorted(set(cn) - set(cv)))

    print("2. Las funciones que tocan lo guardado, identicas")
    fv, fn = funciones(jv), funciones(jn)
    vigiladas = sorted(n for n, t in fv.items() if TOCA.search(t))
    malas = []
    for n in vigiladas:
        if fn.get(n) != fv[n]:
            malas.append(n)
    ok(not malas, "%d funciones vigiladas sin un solo cambio" % len(vigiladas))
    for n in malas:
        print("     cambiada o desaparecida:", n)
    nuevas = sorted(n for n, t in fn.items() if n not in fv and n not in FUNC_NUEVAS_OK and TOCA.search(t))
    ok(not nuevas, "ninguna funcion NUEVA escribe en lo guardado")
    for n in nuevas:
        print("     nueva:", n)

    print("3. Las lineas que cargan lo guardado al arrancar, identicas")
    lv = sorted(l.strip() for l in jv.splitlines() if not l.startswith("function ") and CLAVES.search(l))
    ln = sorted(_sin_nuevas(l.strip() for l in jn.splitlines() if not l.startswith("function ") and CLAVES.search(l)))
    ok(lv == ln, "%d lineas" % len(lv))

    print("\n---- VEREDICTO ----")
    print("TODO OK: listas, titulos guardados, historial, Kodis y vistos siguen exactamente igual"
          if not fallos else "%d FALLOS" % fallos)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
