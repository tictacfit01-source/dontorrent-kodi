# -*- coding: utf-8 -*-
"""Lo visto en el propio móvil («📱 Aquí») en /uso (dtbl134).

Comprueba, sin red:
  1) /vp/latido valida (id de 16 hex, textos limpios) y guarda solo título,
     servidor y por dónde va; `fin` lo quita; si deja de latir, caduca solo;
  2) /uso/datos -> ahora: cuenta el móvil APARTE de las teles, y la lista lo
     marca (aqui=1) con su capítulo y servidor;
  3) `ini` cuenta un vídeo empezado (una vez), y la foto de cada minuto suma
     minutos por servidor (no los de pausa) sin tocar los de las teles;
  4) la página de /uso y la web traen lo suyo.
"""
import os
import shutil
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
os.environ["MW_SIN_USO"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


os.makedirs("/tmp", exist_ok=True)
FICH = [A._USO_FILE, A._KB_STATUS_FILE, A._KB_NOW_FILE, A._KB_SEEN_FILE, A._VP_NOW_FILE]
copias = {}
for f in FICH:
    if os.path.exists(f):
        copias[f] = f + ".prueba_aqui_bak"
        shutil.copy(f, copias[f])
try:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
    cli = A.app.test_client()
    llave = (cli.post("/uso/alta").get_json() or {}).get("k", "")
    assert len(llave) >= 20, "no se pudo reclamar la llave de prueba"
    H = {"X-MW-Uso": llave}
    now = A._t.time()

    print("\n=== 1) /vp/latido ===")
    L = lambda **k: cli.post("/vp/latido", json=k)
    comprueba("id malo: 400", L(id="xyz", t="a").status_code == 400)
    comprueba("sin cuerpo: 400", cli.post("/vp/latido", data="no").status_code == 400)
    r = L(id="a" * 16, t="  The Gentlemen\x07  1x01<>", srv="Vidara", pos=125, dur=3000, ini=1)
    comprueba("latido bueno: 200", r.status_code == 200, r.status_code)
    # sendBeacon manda un Blob: aunque el tipo no sea JSON, se lee
    r = cli.post("/vp/latido", data='{"id":"%s","t":"Interstellar","srv":"Lulustream","pos":60,"dur":9000,"pausa":true,"ini":1}' % ("b" * 16),
                 content_type="text/plain")
    comprueba("como texto plano también", r.status_code == 200, r.status_code)
    g = A._vpnow_load()
    comprueba("guardados los dos", set(g) == {"a" * 16, "b" * 16}, g)
    x = g.get("a" * 16) or {}
    comprueba("título limpio, sin < > ni controles", x.get("t") == "The Gentlemen 1x01", x.get("t"))
    comprueba("solo lo necesario", set(x) == {"t", "srv", "pos", "dur", "pausa", "ts"}, set(x))

    print("\n=== 2) /uso/datos -> ahora ===")
    A._kbstatus_save({"111111": {"v": "2.9.99", "ts": now}})
    A._kbnow_save({"111111": {"np": {"mw": 1, "title": "The Bear · 5x02", "paused": False,
                                     "elapsed": 600, "total": 1800}, "ts": now}})
    d = cli.get("/uso/datos", headers=H).get_json()
    a = d.get("ahora") or {}
    comprueba("teles: 1 viendo (el móvil no cuenta como tele)", a.get("viendo") == 1, a)
    comprueba("móvil: 1 viendo y 1 en pausa", a.get("movil") == 1 and a.get("movil_pausa") == 1, a)
    lm = [z for z in a.get("lista", []) if z.get("aqui")]
    comprueba("lista: dos del móvil", len(lm) == 2, a.get("lista"))
    g1 = next((z for z in lm if z["t"] == "The Gentlemen"), {})
    comprueba("serie con su capítulo y servidor", g1.get("cap") == "1x01" and g1.get("srv") == "Vidara"
              and g1.get("min") == 2 and g1.get("de") == 50, g1)
    comprueba("la tele sigue en la lista", any(z["t"] == "The Bear" and not z.get("aqui") for z in a.get("lista", [])),
              a.get("lista"))
    comprueba("sin llave, nada", cli.get("/uso/datos").status_code == 401)

    print("\n=== 3) vídeos y minutos ===")
    L(id="a" * 16, t="The Gentlemen 1x01", srv="Vidara", pos=155, dur=3000)   # latido normal: no cuenta
    vis = (cli.get("/uso/datos", headers=H).get_json().get("aqui") or {}).get("vistos")
    comprueba("dos vídeos empezados (uno por servidor)", vis == {"Vidara": 1, "Lulustream": 1}, vis)
    A._uso_muestra(forzar=True)
    q = cli.get("/uso/datos", headers=H).get_json()
    mins = (q.get("aqui") or {}).get("min")
    comprueba("1 minuto de Vidara; el de pausa no suma", mins == {"Vidara": 1}, mins)
    hoy = q["hoy"]
    e = A._uso_lee()["dias"][hoy]
    comprueba("las horas de tele no cambian por el móvil", e.get("tm") == 1, e.get("tm"))
    L(id="a" * 16, fin=1)
    g = A._vpnow_load()
    comprueba("fin: se quita", "a" * 16 not in g and "b" * 16 in g, list(g))
    gv = A._vpnow_load()
    gv["b" * 16]["ts"] = now - 200
    A._vpnow_save(gv)
    a = cli.get("/uso/datos", headers=H).get_json()["ahora"]
    comprueba("si deja de latir, desaparece", not a.get("movil") and not a.get("movil_pausa")
              and not [z for z in a["lista"] if z.get("aqui")], a)

    print("\n=== 4) páginas ===")
    pu = cli.get("/uso").get_data(as_text=True)
    comprueba("/uso: lo pinta", "viendo en el móvil" in pu and "aquiTxt(d.aqui)" in pu and "en el móvil" in pu)
    w = A._CAT_PAGE
    comprueba("web: late", "function vpLatido" in w and "'/vp/latido'" in w and "vpLatido(1)" in w)
    print("\n" + ("TODO BIEN" if not fallos else "%d FALLOS" % fallos))
finally:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
        if f in copias:
            shutil.move(copias[f], f)
sys.exit(1 if fallos else 0)
