#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PUENTE RESIDENCIAL — repuebla el Inicio del relay desde la IP de casa.

POR QUE EXISTE
--------------
DonTorrent (y DivxTotal) BANEAN periodicamente la IP de datacenter de Render ->
desde Render el relay no alcanza las fuentes -> el Inicio queda VACIO. La unica
IP no baneada es la RESIDENCIAL. El box (Android TV) hace de puente y empuja el
catalogo a /catfeed cada ~8 min, PERO se DUERME con la tele -> a veces el Inicio
queda vacio horas.

Este script hace de SEGUNDO puente residencial usando ESTE PC (misma IP de casa,
no baneada): scrapea DonTorrent (estrenos/peliculas/series) reutilizando la propia
logica del relay (Anubis, dominio, parseo) y empuja el HTML a /catfeed. El relay
lo cachea -> /catbrowse sirve el Inicio al INSTANTE aunque Render siga baneado.

Pensado para correr cada ~15 min (Programador de tareas de Windows) MIENTRAS el PC
este encendido. Es idempotente y seguro: 3 GETs por ejecucion (~ritmo del box), no
en bucle; si una fuente falla, sigue con las demas; nunca lanza excepcion al SO.

NO machacar: ejecutarlo a mano en bucle banearia la IP de CASA. 15 min es el ritmo
seguro (el box usa ~8 min). Ver memoria verificar-sin-rafagas / dontorrent-buscador.
"""
import os
import sys
import time

# El relay vive en el mismo directorio que este script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RELAY = os.environ.get("MW_RELAY", "https://mw-render-relay-6noq.onrender.com")
KINDS = [("estrenos", "/"), ("peliculas", "/peliculas"), ("series", "/series")]


def _log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = "%s  %s" % (ts, msg)
    print(line, flush=True)
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "puente_residencial.log"), "a",
                  encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    try:
        import app  # reutiliza Anubis/dominio/parseo del relay (desde IP de casa)
        import requests
    except Exception as e:
        _log("ERROR importando app/requests: %r" % e)
        return 1
    ok = 0
    for kind, path in KINDS:
        try:
            html, dom = app._cat_dt_session_get(path)
            n = len(app._cat_parse_items(html or ""))
            if not html or n == 0:
                _log("%s -> sin HTML/items (DonTorrent no respondio); salto" % kind)
                continue
            r = requests.post(RELAY + "/catfeed",
                              json={"kind": kind, "html": html}, timeout=60)
            if r.status_code == 200:
                ok += 1
                _log("%s -> %d bytes, %d items | /catfeed OK" % (kind, len(html), n))
            else:
                _log("%s -> /catfeed HTTP %s: %s" % (kind, r.status_code, r.text[:80]))
        except Exception as e:
            _log("%s -> ERROR: %r" % (kind, repr(e)[:140]))
        time.sleep(2)   # pequeño respiro entre fuentes (no ser rafaga)
    _log("FIN: %d/%d listados empujados" % (ok, len(KINDS)))
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:   # nunca reventar hacia el Programador de tareas
        _log("FATAL: %r" % e)
        sys.exit(1)
