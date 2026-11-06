# -*- coding: utf-8 -*-
"""Semillas de lo que la gente BUSCA (dtbl125): todo sin red.

Antes el aprendiz solo miraba el Inicio: buscar "dune" ensenaba las tarjetas
sin semillas para siempre (salvo abrir cada ficha). Ahora /seedsknown apunta
lo que no supo contestar y el aprendiz lo atiende primero, sin pedir mas a
las fuentes (mismas pausas y cupos: solo cambia el orden).

Vigila:
  1) /seedsknown pasa al aprendiz SOLO lo que no sabia;
  2) que claves valen (pelis de DonTorrent, DivxTotal, WolfMax) y cuales no
     (series, EliteTorrent, basura);
  3) el fichero compartido: caduca a la media hora, tope de 40, lo nuevo
     primero, y no se reescribe si no hay nada nuevo;
  4) lo apuntado va DELANTE del Inicio en la cola del aprendiz.
Escribe en el /tmp del relay (C:\\tmp en Windows) y lo deja como estaba.
"""
import json
import os
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"      # sin hilos de fondo al importar
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def ok(c, msg):
    global fallos
    print(("  OK   " if c else "  FALLO ") + msg)
    if not c:
        fallos += 1


F = A._APR_BUSC_FILE
os.makedirs(os.path.dirname(F) or ".", exist_ok=True)
copia = None
if os.path.exists(F):
    with open(F, "rb") as f:
        copia = f.read()
try:
    if os.path.exists(F):
        os.remove(F)

    print("1. /seedsknown pasa al aprendiz solo lo que no sabia")
    pasadas = []
    real = A._apr_busc_apunta
    A._apr_busc_apunta = lambda ks: pasadas.extend(ks)
    sabida = "dt:peliculas:999001"
    A._dtpacked_load = (lambda o=A._dtpacked_load:
                        (lambda: {"peliculas:999001": {"ts": time.time(), "ih": "a" * 40,
                                                       "s": 7, "sts": time.time()}}))()
    c = A.app.test_client()
    r = c.post("/seedsknown", json={"k": [sabida, "dt:peliculas:999002",
                                          "u:https://divxtotal.foo/peliculas/x/"]})
    d = r.get_json()
    ok(d["s"].get(sabida) == 7, "la sabida se contesta (7)")
    ok(sabida not in pasadas, "la sabida NO va al aprendiz")
    ok("dt:peliculas:999002" in pasadas and "u:https://divxtotal.foo/peliculas/x/" in pasadas,
       "las que no sabia, si")
    A._apr_busc_apunta = real

    print("2. Que claves valen")
    ok(A._apr_busc_item("dt:peliculas:24611")["source"] == "dt", "peli de DonTorrent")
    ok(A._apr_busc_item("dt:series:24611") is None, "serie de DonTorrent: no (cada capitulo tiene las suyas)")
    ok(A._apr_busc_item("u:https://divxtotal.foo/peliculas/dune/")["source"] == "dx", "DivxTotal")
    ok(A._apr_busc_item("u:https://wolfmax4k.com/pelicula/abc")["source"] == "wf", "WolfMax")
    ok(A._apr_busc_item("u:https://www.elitetorrent.com/peliculas/dune/") is None,
       "EliteTorrent: no (el aprendiz no sabe)")
    ok(A._apr_busc_item("dt:peliculas:abc") is None and A._apr_busc_item("x") is None, "basura: no")
    it = A._apr_busc_item("dt:peliculas:24611")
    ok(A._apr_clave(it) == ("dt", "peliculas:24611"), "la clave del aprendiz es la de la web")

    print("3. El fichero compartido")
    os.environ["MW_APRENDIZ"] = "1"
    A._apr_busc_apunta(["dt:peliculas:1", "u:https://www.elitetorrent.com/p/x/"])
    d = A._apr_busc_lee()
    ok(list(d) == ["dt:peliculas:1"], "apunta solo las que valen")
    m1 = os.path.getmtime(F)
    time.sleep(0.05)
    A._apr_busc_apunta(["dt:peliculas:1"])
    ok(os.path.getmtime(F) == m1, "nada nuevo -> no se reescribe")
    viejo = {"dt:peliculas:2": time.time() - 4000}
    with open(F, "w") as f:
        json.dump(viejo, f)
    A._apr_busc_apunta(["dt:peliculas:3"])
    ok("dt:peliculas:2" not in A._apr_busc_lee(), "lo de hace mas de media hora se va")
    A._apr_busc_apunta(["dt:peliculas:%d" % i for i in range(100, 160)])
    ok(len(A._apr_busc_lee()) <= A._APR_BUSC_MAX, "tope de %d" % A._APR_BUSC_MAX)
    os.environ["MW_APRENDIZ"] = "0"
    os.remove(F)
    A._apr_busc_apunta(["dt:peliculas:5"])
    ok(not os.path.exists(F), "con el aprendiz apagado no se apunta nada")

    print("4. Delante del Inicio")
    with open(F, "w") as f:
        json.dump({"dt:peliculas:7": time.time() - 10, "dt:peliculas:8": time.time()}, f)
    b = A._apr_items_buscados(time.time())
    ok([x["content_id"] for x in b] == ["8", "7"], "lo mas reciente primero")
    pend, _ = A._apr_pendientes(b + [{"source": "dt", "tabla": "peliculas",
                                      "content_id": "9", "kind": "movie"}], time.time())
    ok([k for _, k, _ in pend][:3] == ["peliculas:8", "peliculas:7", "peliculas:9"],
       "la cola: lo buscado, luego el Inicio")
finally:
    try:
        os.remove(F)
    except Exception:
        pass
    if copia is not None:
        with open(F, "wb") as f:
            f.write(copia)

print("\n---- VEREDICTO ----")
print("TODO OK: lo que la gente busca tambien aprende sus semillas" if not fallos
      else "%d FALLOS" % fallos)
sys.exit(1 if fallos else 0)
