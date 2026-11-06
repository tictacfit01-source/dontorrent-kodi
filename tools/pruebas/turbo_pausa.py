# -*- coding: utf-8 -*-
"""Pausa larga con el acelerador del streaming (caja 2.9.102).

06-11-2026: "cuando pauso, al reanudar dura 30 segundos y se cierra". Con la
tele en pausa Kodi no pide nada; a los 3 min (LIBRE) el acelerador CERRABA la
sesion y, al reanudar, Kodi veia lo que tenia en su memoria y se cortaba.
Reproducido en el Kodi del PC con 4 min de pausa. Ahora la sesion se DUERME:
  1) no se cierra: sigue viva y con su lista;
  2) suelta la memoria salvo los trozos que Kodi pedira al reanudar;
  3) dormida no baja nada; cuando Kodi pide, despierta y vuelve a bajar.
Sin red: solo la clase de la sesion.
"""
import os
import sys
import types

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf", "resources", "lib"))
xbmc = types.ModuleType("xbmc")
xbmc.log = lambda *a, **k: None
xbmc.LOGINFO = 1
sys.modules["xbmc"] = xbmc
import turbo  # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


ses = turbo.Sesion("abcd", {}, "#EXTM3U", ["http://x/%d.ts" % i for i in range(40)], [])
ses.pos = 5
for i in range(5, 17):                      # 12 trozos ya bajados por delante
    t = ses.trozos[i]
    t.partes, t.hechas, t.pedido, t.listo, t.total = [bytearray(b"x" * 1000)], [True], True, True, 1000
with ses.cv:
    ses.duerme()
comprueba("dormida, no cerrada", ses.vivo and ses.dormida)
guardados = [i for i, t in enumerate(ses.trozos) if t.partes]
comprueba("solo guarda lo inmediato (5-7)", guardados == [5, 6, 7], guardados)
with ses.cv:
    comprueba("dormida no baja nada", ses.siguiente_tarea() is None)
ses.dormida = False                          # Kodi vuelve a pedir (ver _trozo)
with ses.cv:
    tarea = ses.siguiente_tarea()
comprueba("despierta: vuelve a bajar lo siguiente", tarea == (8, 0), tarea)
src = open(turbo.__file__, encoding="utf-8").read()
comprueba("el obrero ya no cierra la sesion por estar parada",
          "ses.duerme()" in src and '_S["ses"] = None' not in src.split("def _obrero")[1].split("def _despierta")[0])

print("\n---- VEREDICTO ----")
print("TODO OK: una pausa larga ya no corta el streaming" if not fallos else "%d FALLOS" % fallos)
sys.exit(1 if fallos else 0)
