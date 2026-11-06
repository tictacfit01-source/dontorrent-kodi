# -*- coding: utf-8 -*-
"""El mapa de /uso (dtbl123): luces donde hay una tele viendo algo.

Comprueba, sin red:
  1) /uso/datos trae `mapa` (solo con la llave): por ciudad, viendo / pausa /
     encendido; lo de otros addons no cuenta como "viendo"; los Kodis sin
     ubicar se cuentan aparte; ni códigos, ni títulos, ni IPs dentro;
  2) el latido NUNCA espera a la geolocalización (aunque el servicio tarde),
     las IPs privadas no se mandan a nadie y lo ya ubicado no se repite;
  3) la cadena de servicios: si el primero falla o no sabe, el segundo; y el
     tercero; si ninguno, no se insiste en 30 min;
  4) la página trae la sección, su chip y el SRI de Leaflet.
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
FICH = [A._USO_FILE, A._KB_STATUS_FILE, A._KB_NOW_FILE, A._KB_SEEN_FILE, A._GEO_FILE]
copias = {}
for f in FICH:
    if os.path.exists(f):
        copias[f] = f + ".prueba_mapa_bak"
        shutil.copy(f, copias[f])
busca_real = A._geo_busca
get_real = A._get_con_tope
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

    print("\n=== 1) /uso/datos -> mapa ===")
    A._geo_guarda_uno("ihmad", 40.4168, -3.7038, "Madrid", "ES")
    A._geo_guarda_uno("ihsev", 37.3891, -5.9845, "Sevilla", "ES")
    A._kbstatus_save({
        "111111": {"v": "2.9.99", "ts": now, "ih": "ihmad"},
        "222222": {"v": "2.9.99", "ts": now, "ih": "ihmad"},
        "333333": {"v": "2.9.99", "ts": now, "ih": "ihsev"},
        "444444": {"v": "2.9.99", "ts": now, "ih": "ihsev"},
        "555555": {"v": "2.9.99", "ts": now, "ih": "ihnadie"},
        "666666": {"v": "2.9.99", "ts": now - 300, "ih": "ihmad"},     # apagado
    })
    A._kbnow_save({
        "111111": {"np": {"mw": 1, "title": "Peli secreta", "paused": False}, "ts": now},
        "222222": {"np": {"mw": 1, "title": "Otra secreta", "paused": False}, "ts": now},
        "333333": {"np": {"mw": 1, "title": "Pausada", "paused": True}, "ts": now},
        "444444": {"np": {"title": "Fútbol de otro addon", "paused": False}, "ts": now},
        "555555": {"np": {"mw": 1, "title": "Sin ubicar", "paused": False}, "ts": now},
    })
    r = cli.get("/uso/datos")
    comprueba("sin llave no hay mapa (401)", r.status_code == 401 and "mapa" not in (r.get_json() or {}))
    d = cli.get("/uso/datos", headers=H).get_json()
    mp = d.get("mapa") or {}
    P = {p["c"]: p for p in mp.get("puntos", [])}
    comprueba("dos ciudades", set(P) == {"Madrid", "Sevilla"}, P)
    comprueba("Madrid: 2 viendo (el apagado no cuenta)", P.get("Madrid", {}).get("v") == 2
              and P["Madrid"]["p"] == 0 and P["Madrid"]["k"] == 0, P.get("Madrid"))
    comprueba("Sevilla: 1 pausa + 1 encendido (otro addon no es 'viendo')",
              P.get("Sevilla", {}).get("p") == 1 and P["Sevilla"]["k"] == 1 and P["Sevilla"]["v"] == 0,
              P.get("Sevilla"))
    comprueba("primero lo que se ve", mp["puntos"][0]["c"] == "Madrid", mp["puntos"])
    comprueba("sin ubicar contado aparte", mp.get("sin_ubicar") == {"viendo": 1, "kodis": 1}, mp.get("sin_ubicar"))
    txt = json.dumps(mp, ensure_ascii=False)
    comprueba("ni códigos ni títulos ni huellas en el mapa",
              not any(x in txt for x in ("111111", "333333", "555555", "secreta", "Pausada", "Fútbol", "ihmad")), txt)
    comprueba("coordenadas redondeadas a la ciudad", P["Madrid"]["la"] == 40.42 and P["Madrid"]["lo"] == -3.7,
              P["Madrid"])

    print("\n=== 2) El latido no espera a la geolocalización ===")
    llamadas = []

    def lenta(ip):
        llamadas.append(ip)
        time.sleep(3)
        return (41.39, 2.17, "Barcelona", "ES")
    A._geo_busca = lenta
    os.environ.pop("MW_SIN_GEO", None)
    t0 = time.time()
    r = cli.post("/kb/status", json={"code": "777777", "v": "2.9.99"},
                 headers={"CF-Connecting-IP": "83.45.1.2"})
    dt = time.time() - t0
    comprueba("el latido contesta enseguida (%.2f s) aunque el servicio tarde 3 s" % dt,
              r.status_code == 200 and dt < 1.5, (r.status_code, dt))
    for _ in range(60):
        if not A._GEO_HILO["vivo"]:
            break
        time.sleep(0.1)
    ih = A._kbstatus_load()["777777"]["ih"]
    comprueba("el hilo la ubica", (A._geo_lee().get(ih) or [None])[2:3] == ["Barcelona"], A._geo_lee().get(ih))
    raw = open(A._GEO_FILE, encoding="utf-8").read()
    comprueba("la IP no se guarda", "83.45.1.2" not in raw and "83.45.1.2" not in open(A._KB_STATUS_FILE).read())
    n0 = len(llamadas)
    cli.post("/kb/status", json={"code": "777777", "v": "2.9.99"}, headers={"CF-Connecting-IP": "83.45.1.2"})
    time.sleep(0.3)
    comprueba("ya ubicada: no se vuelve a preguntar", len(llamadas) == n0, llamadas)
    for ip in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "::1"):
        cli.post("/kb/status", json={"code": "888888", "v": "2.9.99"}, headers={"CF-Connecting-IP": ip})
    time.sleep(0.3)
    comprueba("las IPs privadas no salen de aquí", len(llamadas) == n0, llamadas)
    os.environ["MW_SIN_GEO"] = "1"
    cli.post("/kb/status", json={"code": "999999", "v": "2.9.99"}, headers={"CF-Connecting-IP": "90.1.2.3"})
    time.sleep(0.3)
    comprueba("MW_SIN_GEO=1 lo apaga", len(llamadas) == n0, llamadas)
    os.environ.pop("MW_SIN_GEO", None)
    A._geo_busca = busca_real

    print("\n=== 3) La cadena de servicios ===")
    pedidas = []

    def falso(resp):
        def g(url, tope, headers=None, **kw):
            pedidas.append(url.split("/")[2])
            for dom, val in resp.items():
                if dom in url:
                    return val
            return None, 0
        return g
    A._get_con_tope = falso({"ipwho.is": (None, 429),
                             "ipinfo.io": ('{"city":"Valencia","country":"ES","loc":"39.4699,-0.3763"}', 200)})
    comprueba("1º limita -> 2º", A._geo_busca("1.2.3.4") == (39.4699, -0.3763, "Valencia", "ES")
              and pedidas == ["ipwho.is", "ipinfo.io"], pedidas)
    pedidas[:] = []
    A._get_con_tope = falso({"ipwho.is": ('{"success":false,"message":"reserved range"}', 200),
                             "ipinfo.io": ('{"bogon":true}', 200),
                             "geojs.io": ('{"latitude":"43.36","longitude":"-8.41","city":"A Coruña","country_code":"ES"}', 200)})
    comprueba("1º no sabe, 2º sin loc -> 3º", A._geo_busca("1.2.3.4") == (43.36, -8.41, "A Coruña", "ES"), pedidas)
    A._get_con_tope = falso({"ipwho.is": ("<html>basura", 200), "ipinfo.io": ('{"loc":"0,0"}', 200),
                             "geojs.io": ('{"latitude":"nan"}', 200)})
    comprueba("ninguno sabe -> None", A._geo_busca("1.2.3.4") is None)
    A._get_con_tope = get_real
    A._GEO_KO.clear()
    A._GEO_KO["ihko"] = time.time()
    antes = dict(A._GEO_PEND)
    A._geo_apunta("ihko", "90.1.2.3")
    comprueba("tras fallar, no insiste en 30 min", A._GEO_PEND == antes and "ihko" not in A._GEO_PEND)

    print("\n=== 4) La página ===")
    pg = cli.get("/uso").get_data(as_text=True)
    comprueba("chip Mapa y sección", 'href="#s-mapa"' in pg and "mapaSec(d.mapa)" in pg)
    comprueba("Leaflet con SRI", "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" in pg
              and "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" in pg)
    comprueba("BUILD", int(A.BUILD[4:]) >= 123, A.BUILD)
finally:
    A._geo_busca = busca_real
    A._get_con_tope = get_real
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
    for f, c in copias.items():
        shutil.move(c, f)

print("\n%s" % ("TODO OK" if not fallos else "%d FALLOS" % fallos))
sys.exit(1 if fallos else 0)
