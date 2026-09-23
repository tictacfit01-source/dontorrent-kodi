# -*- coding: utf-8 -*-
"""Que el JavaScript de las paginas del relay COMPILE (dtbl38).

La web entera (Inicio, busqueda, fichas, mando) es un solo <script> dentro de
_CAT_PAGE, en render_relay/app.py. Un parentesis de mas en cualquier sitio y
el navegador no ejecuta NADA: pantalla en blanco para todo el mundo, sin un
error que se vea desde fuera (el relay sigue contestando 200). Hasta ahora
nada lo vigilaba: las pruebas de Node sacan funciones sueltas.

Aqui se saca cada <script> de cada pagina, TAL CUAL la sirve el relay, y se le
pasa a `node --check` (compila sin ejecutar). Ademas se pide cada pagina al
relay (cliente de pruebas de Flask) para ver que se sirve.

Necesita Node en el PATH (el mismo de fusion.js). Sin red.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

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


NODE = shutil.which("node")
if not NODE:
    print("MAL: no encuentro node en el PATH")
    sys.exit(1)

PAGINAS = {"_CAT_PAGE (la web)": A._CAT_PAGE, "_KB_PAGE (el mando)": A._KB_PAGE}
tmp = tempfile.mkdtemp(prefix="mw_pagina_")
try:
    print("\n=== 1) Cada <script> compila ===")
    for nombre, html in PAGINAS.items():
        scripts = [(m.group(1) or "", m.group(2)) for m in re.finditer(
            r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I)]
        js = [(i, s) for i, (attrs, s) in enumerate(scripts)
              if s.strip() and "src=" not in attrs
              and not re.search(r"type=[\"'](?!text/javascript|module)", attrs)]
        comprueba("%s: tiene JavaScript que comprobar (%d bloques)" % (nombre, len(js)),
                  len(js) > 0)
        for i, s in js:
            f = os.path.join(tmp, "p%d.js" % i)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(s)
            r = subprocess.run([NODE, "--check", f], capture_output=True, text=True)
            error = (r.stderr or r.stdout or "").strip().splitlines()
            comprueba("%s, bloque %d (%d KB) compila" % (nombre, i, len(s) // 1024),
                      r.returncode == 0, "\n      ".join(error[:8]))

    print("\n=== 2) El relay las sirve ===")
    cli = A.app.test_client()
    for ruta in ("/", "/cat", "/kb"):
        r = cli.get(ruta)
        cuerpo = r.get_data(as_text=True)
        comprueba("GET %s -> 200 con su pagina" % ruta,
                  r.status_code == 200 and "<script" in cuerpo, r.status_code)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el JavaScript de las paginas compila")
