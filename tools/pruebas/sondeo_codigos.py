# -*- coding: utf-8 -*-
"""(dtbl143) Nadie puede barrer los codigos de las teles.

/kb/status, /kb/now y /kb/list contestaban a cualquier codigo de 6 cifras sin
limite: un script encontraba las ~26 teles en horas (y que estaban viendo).
Ahora se cuentan, por IP real (CF-Connecting-IP), los codigos DISTINTOS que no
existen. Vigila las dos caras: el barrido se corta, y el uso normal (la tele
apagada horas, varios Kodis guardados, recien desplegado, mucha gente detras
del mismo borde de Render) no se entera. Sin red y sin tocar /tmp.
"""
import os, sys, time

os.environ.update(MW_SIN_KEEPALIVE="1", MW_APRENDIZ="0", MW_SIN_NUBE="1")
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "render_relay"))
import app as A  # noqa: E402

MAL = []


def comprueba(nombre, ok, extra=""):
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(extra)))
    if not ok:
        MAL.append(nombre)


VIVA = "123456"
A._kbstatus_load = lambda: {VIVA: {"v": "2.9.102", "ts": time.time()}}
A._kbnow_load = lambda: {}
A._kblist_load = lambda: {}
A._kb_phone_seen = lambda code: None
A._KB_ARRANQUE = time.time() - 3600
c = A.app.test_client()


def get(ruta, code, ip):
    return c.get(ruta + "?code=" + code, headers={"CF-Connecting-IP": ip,
                                                  "X-Forwarded-For": ip + ", 10.0.0.1"}).status_code


print("\n=== 1) El uso normal no se entera ===")
A._SONDEO.clear()
st = [get("/kb/status", VIVA, "1.1.1.1") for _ in range(300)]
comprueba("su tele encendida, 300 consultas seguidas", set(st) == {200}, set(st))
st = [get(r, "654321", "1.1.1.2") for _ in range(200) for r in ("/kb/status", "/kb/now", "/kb/list")]
comprueba("su tele apagada hace horas (codigo que ya no existe), 600 consultas", set(st) == {200}, set(st))
st = [get("/kb/status", "%06d" % (500000 + i), "1.1.1.3") for i in range(8) for _ in range(30)]
comprueba("8 Kodis guardados y todos apagados", set(st) == {200}, set(st))
A._SONDEO.clear()
st = [get("/kb/status", "%06d" % i, "9.9.9.%d" % (i % 200)) for i in range(2000)]
comprueba("2000 codigos distintos desde 200 casas distintas (mismo borde de Render)", set(st) == {200}, set(st))

print("\n=== 2) Un barrido se corta ===")
A._SONDEO.clear()
st = [get("/kb/status", "%06d" % (700000 + i), "6.6.6.6") for i in range(60)]
comprueba("los 40 primeros contestan y luego 429", st[:40] == [200] * 40 and set(st[40:]) == {429}, st[38:43])
comprueba("cortado, tampoco le dice si un codigo REAL existe", get("/kb/status", VIVA, "6.6.6.6") == 429)
comprueba("...ni por /kb/now ni por /kb/list", get("/kb/now", VIVA, "6.6.6.6") == 429 and get("/kb/list", VIVA, "6.6.6.6") == 429)
comprueba("otra casa sigue igual", get("/kb/status", VIVA, "1.1.1.1") == 200)
comprueba("falsear X-Forwarded-For no sirve (cuenta CF-Connecting-IP)",
          c.get("/kb/status?code=" + VIVA, headers={"CF-Connecting-IP": "6.6.6.6",
                                                     "X-Forwarded-For": "8.8.8.8"}).status_code == 429)
e = A._SONDEO["6.6.6.6"]
e["t0"] -= A._SONDEO_WIN + 1
comprueba("a los 10 min vuelve a contestar", get("/kb/status", VIVA, "6.6.6.6") == 200)

print("\n=== 3) Recien desplegado (/tmp vacio) nadie cuenta como barrido ===")
A._SONDEO.clear()
A._kbstatus_load = lambda: {}
A._KB_ARRANQUE = time.time()
st = [get("/kb/status", "%06d" % i, "5.5.5.5") for i in range(80)]
comprueba("los 2 primeros minutos no se cuenta", set(st) == {200}, set(st))
A._KB_ARRANQUE = time.time() - 3600

print("\n=== 4) /kb/send cuenta por la IP del cliente, no por el borde de Render ===")
A._RL.clear()
ips = [A.app.test_request_context(headers={"CF-Connecting-IP": "2.2.2.%d" % i,
                                           "X-Forwarded-For": "2.2.2.%d, 10.0.0.1" % i}) for i in range(2)]
vistas = []
for ctx in ips:
    with ctx:
        vistas.append(A._ip_cliente())
comprueba("dos casas = dos contadores (antes: las dos '10.0.0.1')", vistas == ["2.2.2.0", "2.2.2.1"], vistas)
with A.app.test_request_context(headers={"X-Forwarded-For": "3.3.3.3, 10.0.0.1"}):
    comprueba("sin CF-Connecting-IP, la primera de X-Forwarded-For", A._ip_cliente() == "3.3.3.3", A._ip_cliente())

print("\n---- VEREDICTO ----")
print(("%d comprobaciones MAL" % len(MAL)) if MAL else "TODO OK: los codigos no se pueden barrer y el uso normal no lo nota")
sys.exit(1 if MAL else 0)
