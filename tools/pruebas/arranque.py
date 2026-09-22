# -*- coding: utf-8 -*-
"""NADA DE RED EN EL PROCESO PADRE (relay dtbl37).

Con preload, gunicorn importa la app en el proceso padre y luego crea los
workers con fork(). Si en ese momento un hilo del padre esta a mitad de una
conexion HTTPS, el hijo hereda un cerrojo de OpenSSL CERRADO y todo su HTTPS se
cuelga para siempre (su DNS y su UDP siguen bien). Medido el 22-09-2026: a la
misma hora y con la misma IP, un worker con TMDB 322/322 a tiempo y el otro con
TODO su HTTPS colgado. El keepalive y las recuperaciones de copias arrancaban
en el import; ahora arrancan en la primera peticion de cada worker.

Esto vigila la regla: importar no arranca NINGUN hilo, y la primera peticion
arranca todo lo del worker UNA vez. Sin red: lo que haria red, son dobles.
"""
import os
import sys
import threading
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ.pop("MW_SIN_NUBE", None)          # aqui se prueba el arranque DE VERDAD
os.environ.pop("MW_SIN_KEEPALIVE", None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


antes = set(t.ident for t in threading.enumerate())
import app as A                                          # noqa: E402
time.sleep(0.5)
nuevos = [t.name for t in threading.enumerate() if t.ident not in antes]

print("\n=== 1) Importar (lo que hace el proceso PADRE con preload) ===")
comprueba("no arranca NINGUN hilo", nuevos == [], nuevos)

print("\n=== 2) La primera peticion de un worker ===")
llamadas = []
A._self_keepalive = lambda: llamadas.append("keepalive")
A._wfidx_nube_baja = lambda: llamadas.append("indice") or 0
A._semillas_nube_baja = lambda: llamadas.append("semillas") or 0
A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"      # por si acaso: nunca produccion
A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"
cli = A.app.test_client()
cli.get("/ping")
time.sleep(4.5)                     # las recuperaciones esperan 2-3 s antes de bajar
comprueba("arranca el keepalive, la recuperacion del indice y la de semillas",
          sorted(llamadas) == ["indice", "keepalive", "semillas"], sorted(llamadas))
cli.get("/ping")
cli.get("/ping")
time.sleep(4.5)
comprueba("y SOLO una vez por worker (las siguientes peticiones no repiten)",
          sorted(llamadas) == ["indice", "keepalive", "semillas"], sorted(llamadas))

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el padre no hace red, cada worker arranca lo suyo")
