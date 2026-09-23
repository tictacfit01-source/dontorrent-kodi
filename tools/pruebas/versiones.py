# -*- coding: utf-8 -*-
"""Las cajas muy desactualizadas (dtbl38): sin red.

23-09-2026: 8 cajas vivas y una en 2.9.54, veinte versiones por detras. El
relay reparte los trabajos PRESTADOS (buscar, fichas, huellas...) entre todas
por turnos, y esa no entiende varias operaciones de hoy: uno de cada ocho
trabajos se quedaba esperando a que caducara. Y su dueño no tenia forma de
saberlo. Esto vigila:
  1) como se comparan versiones y cuanto retraso se tolera (5: al publicar,
     las cajas tardan un dia en actualizarse y no se puede dejar todo el
     trabajo a la primera que lo haga);
  2) los prestamos van solo a cajas al dia (y si no queda ninguna, a todas);
  3) /kb/status dice si ESA tele va muy atrasada, para avisar a su dueño.
Escribe en el /tmp del relay (C:\\tmp): guarda y restaura lo que hubiera.
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

A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"
A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


FICH = [A._KB_STATUS_FILE, A._KB_FILE]
os.makedirs("/tmp", exist_ok=True)
copia = {}
for f in FICH:
    if os.path.exists(f):
        copia[f] = f + ".prueba_bak"
        shutil.copy(f, copia[f])


def cajas(d):
    now = time.time()
    with open(A._KB_STATUS_FILE, "w", encoding="utf-8") as fh:
        json.dump({c: {"v": v, "ts": now - edad} for c, (v, edad) in d.items()}, fh)


try:
    print("\n=== 1) Comparar versiones ===")
    comprueba("'2.9.73' -> (2, 9, 73)", A._ver_tupla("2.9.73") == (2, 9, 73))
    comprueba("sin version -> (0,)", A._ver_tupla("") == (0,) and A._ver_tupla(None) == (0,))
    comprueba("'2.10.1' va despues de '2.9.99'",
              A._ver_tupla("2.10.1") > A._ver_tupla("2.9.99"))
    casos = [("2.9.54", "2.9.74", True, "veinte por detras"),
             ("2.9.68", "2.9.74", True, "seis por detras"),
             ("2.9.69", "2.9.74", False, "cinco: se tolera"),
             ("2.9.73", "2.9.74", False, "una: lo normal al publicar"),
             ("2.9.74", "2.9.74", False, "la misma"),
             ("2.9.75", "2.9.74", False, "mas nueva"),
             ("2.8.99", "2.9.1", True, "otra rama"),
             ("", "2.9.74", True, "sin version")]
    for v, u, esp, nom in casos:
        comprueba("%s frente a %s: %s -> %s" % (v or "''", u, nom, esp),
                  A._ver_vieja(v, u) is esp)

    print("\n=== 2) Los prestamos, solo a cajas al dia ===")
    cajas({"111111": ("2.9.73", 5), "222222": ("2.9.74", 10),
           "333333": ("2.9.54", 3), "444444": ("2.9.74", 300)})   # la 4a, muerta
    todas = A._live_boxes()
    comprueba("sin filtro: las tres vivas", sorted(todas) == ["111111", "222222", "333333"],
              todas)
    aldia = A._live_boxes(al_dia=True)
    comprueba("al dia: sin la de 2.9.54", sorted(aldia) == ["111111", "222222"], aldia)
    vistas = {A._any_live_box() for _ in range(30)}
    comprueba("el reparto nunca le toca a la vieja (30 turnos)",
              vistas == {"111111", "222222"}, vistas)
    cajas({"333333": ("2.9.54", 3)})
    comprueba("si solo queda la vieja, se usa (algo es mejor que nada)",
              A._live_boxes(al_dia=True) == ["333333"] and A._any_live_box() == "333333")
    cajas({"333333": ("2.9.54", 3), "555555": ("", 4), "222222": ("2.9.74", 2)})
    comprueba("una sin version tambien queda fuera",
              A._live_boxes(al_dia=True) == ["222222"], A._live_boxes(al_dia=True))

    print("\n=== 3) /kb/status avisa a su dueño ===")
    cli = A.app.test_client()
    js = cli.get("/kb/status?code=333333").get_json()
    comprueba("la de 2.9.54: 'vieja' y cual es la ultima",
              js.get("vieja") is True and js.get("ultima") == "2.9.74", js)
    js = cli.get("/kb/status?code=222222").get_json()
    comprueba("la de 2.9.74: no", js.get("vieja") is False, js)
    js = cli.get("/kb/status?code=999999").get_json()
    comprueba("una que no existe: desconectada, sin mas", js == {"connected": False}, js)
    src = A._CAT_PAGE
    comprueba("la web lo pinta en Mis Kodis (liveDot con 'vieja')",
              "j.vieja" in src and "devold" in src and "liveDot(dot,dev.code,meta)" in src)
finally:
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
print("TODO OK: los prestamos van a cajas al dia y la vieja se entera")
