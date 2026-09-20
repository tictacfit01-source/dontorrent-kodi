# -*- coding: utf-8 -*-
"""El pool del enriquecimiento, cuando TMDB deja de contestar del todo.

Reproduce lo que se vio en produccion el 20-09 a los 9 minutos de desplegar:
`enrich: vuelo 32` con solo ocho hilos, `sin_hueco` subiendo y CERO
enriquecimientos completados -- o sea, el worker habia dejado de enriquecer
para siempre y las tarjetas salian con la caratula de la fuente y sin nota.

Dos cosas tienen que cumplirse:
  1. el contador de tareas en vuelo NO puede quedarse inflado (una tarea
     cancelada no ejecuta su funcion, asi que su `finally` no corre);
  2. si las ocho plazas se llenan de peticiones colgadas, el pool tiene que
     RENOVARSE solo; si no, no hay vuelta atras sin relevar el worker.
"""
import os
import sys
import time
import threading

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))

import requests                                          # noqa: E402


def _cuelga(*a, **k):
    time.sleep(600)


requests.Session.get = _cuelga
requests.Session.post = _cuelga
requests.get = _cuelga
requests.post = _cuelga

import app as A                                           # noqa: E402

print("build:", A.BUILD)
A._ENR_ATASCO_S = 3.0          # para no esperar dos minutos en la prueba
base = threading.active_count()


def tarea_colgada(x):
    time.sleep(600)
    return x


print("\n--- 1) saturar el pool con tareas que no vuelven ---")
for ronda in range(4):
    A._enr_map(tarea_colgada, list(range(12)), 0.6)
    print("   ronda %d -> vuelo=%2d  sin_hueco=%d  hilos=%d"
          % (ronda + 1, A._ENR_VUELO[0], A._ENR_STATS["sin_hueco"],
             threading.active_count()))

vuelo_atascado = A._ENR_VUELO[0]
print("\n--- 2) esperar a que salte la valvula y reintentar ---")
time.sleep(3.5)
hechas = A._enr_map(lambda x: x * 2, list(range(5)), 3.0)
print("   pools estrenados:", A._ENR_STATS["pools"])
print("   tareas completadas tras la renovacion:", hechas, "de 5")
print("   vuelo ahora:", A._ENR_VUELO[0])
print("   hilos:", threading.active_count(), "(al empezar:", base, ")")

ok_contador = vuelo_atascado <= A._ENR_COLA_MAX
ok_valvula = A._ENR_STATS["pools"] >= 1 and hechas == 5
ok_hilos = threading.active_count() <= base + A._ENR_MAX * 2 + 6

print("\n---- VEREDICTO ----")
print("el contador no se desmadra:      ", ok_contador)
print("el pool se renueva y vuelve a ir:", ok_valvula)
print("los hilos siguen acotados:       ", ok_hilos)
print("TODO OK" if (ok_contador and ok_valvula and ok_hilos) else "FALLA ALGO")
os._exit(0 if (ok_contador and ok_valvula and ok_hilos) else 1)
