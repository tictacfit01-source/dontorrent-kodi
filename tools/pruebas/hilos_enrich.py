# -*- coding: utf-8 -*-
"""Prueba el arreglo de los hilos del enriquecimiento SIN tocar la red.

Simula lo que de verdad pasa en produccion: TMDB deja de contestar (gotea) y
el enrich se cuela. Antes, cada llamada creaba 8 hilos nuevos que no volvian
jamas; ahora tiene que haber un tope duro y la peticion tiene que volver a
tiempo pase lo que pase.
"""
import os
import sys
import time
import threading

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
RELAY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..", "render_relay")
sys.path.insert(0, RELAY)

import requests

COLGADAS = [0]


class _RespFalsa(object):
    status_code = 599

    def json(self):
        return {}

    def close(self):
        pass


def _get_que_cuelga(*a, **k):
    """Como TMDB cuando banea a Render: no contesta, pero tampoco corta."""
    COLGADAS[0] += 1
    time.sleep(600)
    return _RespFalsa()


requests.Session.get = _get_que_cuelga
requests.Session.post = _get_que_cuelga
requests.get = _get_que_cuelga
requests.post = _get_que_cuelga

import app as A                                     # noqa: E402

print("build:", A.BUILD)
base = threading.active_count()
print("hilos tras importar:", base)

items = [{"title": "Pelicula de prueba %d" % i, "kind": "movie",
          "content_id": "x%d" % i, "source": "wf",
          "thumb": "/relay?u=https%3A%2F%2Fejemplo/x.jpg"} for i in range(24)]

t0 = time.time()
out = A._bounded(lambda: A._cat_enrich(items, limit=24), 10.0, default=items)
dur = time.time() - t0
time.sleep(0.5)
hilos = threading.active_count()

print("---- RESULTADO ----")
print("tardo: %.1f s (presupuesto 10)" % dur)
print("items devueltos:", len(out))
print("con poster:", sum(1 for i in out if i.get("poster")))
print("peticiones a TMDB colgadas:", COLGADAS[0])
print("hilos ahora:", hilos, "(antes del enrich:", base, ")")
print("enrich stats:", A._ENR_STATS, "en vuelo:", A._ENR_VUELO[0])

# Segunda tanda: lo que antes multiplicaba los hilos (8 por llamada)
t0 = time.time()
for n in range(5):
    its = [dict(i) for i in items]
    A._bounded(lambda: A._cat_enrich(its, limit=24), 3.0, default=its)
dur2 = time.time() - t0
time.sleep(0.5)
print("5 enrich mas seguidos: %.1f s, hilos: %d" % (dur2, threading.active_count()))
print("enrich stats:", A._ENR_STATS, "en vuelo:", A._ENR_VUELO[0])

techo = base + A._ENR_MAX + 4
ok_hilos = threading.active_count() <= techo
ok_tiempo = dur < 13 and dur2 < 20
ok_poster = all(i.get("poster") for i in out)
print("---- VEREDICTO ----")
print("hilos acotados (<= %d):" % techo, ok_hilos)
print("vuelve a tiempo:", ok_tiempo)
print("ningun item sin caratula:", ok_poster)
print("TODO OK" if (ok_hilos and ok_tiempo and ok_poster) else "FALLA ALGO")
os._exit(0)
