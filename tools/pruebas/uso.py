# -*- coding: utf-8 -*-
"""El uso de la app, solo para el dueño (dtbl81).

"¿Cuántas teles hay ahora? ¿La gente la usa?" -- y que el resto no lo sepa.
Comprueba, sin red:
  1) /catdiag ya NO enseña cuántas teles ni cuántas cajas sin la llave (y con
     ella, sí);
  2) la llave: se reclama una vez, solo recién arrancado, y sin ella /uso/datos
     contesta 401 sin decir nada;
  3) la muestra por minuto: cuenta cajas, teles con vídeo, pausas y webs; no
     cuenta dos veces lo del otro worker; pico y horas;
  4) en ningún sitio aparece un código de caja (ni en el fichero ni en la copia);
  5) la copia cifrada: ida y vuelta, una copia manipulada o ajena no vale, y
     fundir nunca pierde nada (ni pisa la llave de la copia);
  6) días viejos compactados a cuentas; la hora de España (verano/invierno);
  7) la página: su JavaScript compila y no lleva datos dentro.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

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
FICH = [A._USO_FILE, A._KB_STATUS_FILE, A._KB_NOW_FILE, A._KB_SEEN_FILE]
copias = {}
for f in FICH:
    if os.path.exists(f):
        copias[f] = f + ".prueba_uso_bak"
        shutil.copy(f, copias[f])
tmpd = tempfile.mkdtemp(prefix="mw_uso_")
try:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
    cli = A.app.test_client()
    now = A._t.time()
    CODIGOS = ["111111", "222222", "333333", "444444"]
    A._kbstatus_save({c: {"v": "2.9.90", "ts": now} for c in CODIGOS})
    A._kbnow_save({"111111": {"np": {"title": "Peli secreta", "paused": False}, "ts": now},
                   "222222": {"np": {"title": "Otra", "paused": False}, "ts": now},
                   "333333": {"np": {"title": "Pausada", "paused": True}, "ts": now}})
    with open(A._KB_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"111111": now, "444444": now}, f)

    print("\n=== 1) /catdiag sin llave no dice cuántas teles ni cajas ===")
    r = cli.get("/catdiag").get_json()
    comprueba("sin reproduciendo", "reproduciendo" not in r, list(r))
    comprueba("sin boxes_live", "boxes_live" not in r, list(r))
    comprueba("sin lend_boxes", "lend_boxes" not in (r.get("catbox") or {}), r.get("catbox"))
    comprueba("lo demás sigue (build, vigía, wfih.zip para la extensión)",
              r.get("build") == A.BUILD and "vigia" in r and "wfih" in r, list(r))

    print("\n=== 2) La llave ===")
    r = cli.get("/uso/datos")
    comprueba("sin llave: 401 y nada más", r.status_code == 401 and set(r.get_json()) == {"ok", "error"},
              (r.status_code, r.get_json()))
    t0 = A._USO_T0
    A._USO_T0 = A._t.time() - A._USO_ALTA_S - 5
    comprueba("pasados los 20 min de arrancar no se puede reclamar",
              cli.post("/uso/alta").status_code == 403)
    A._USO_T0 = t0
    r = cli.post("/uso/alta")
    llave = (r.get_json() or {}).get("k", "")
    comprueba("recién arrancado se reclama (una llave larga)", r.status_code == 200 and len(llave) >= 20,
              r.get_json())
    comprueba("y solo una vez", cli.post("/uso/alta").status_code == 403)
    comprueba("se guarda su sha256, no la llave", llave not in open(A._USO_FILE, encoding="utf-8").read())
    comprueba("una llave falsa: 401", cli.get("/uso/datos", headers={"X-MW-Uso": "x" * 24}).status_code == 401)
    r = cli.get("/catdiag?k=" + llave).get_json()
    comprueba("/catdiag con la llave: todo, como antes",
              r.get("reproduciendo", {}).get("n") == 2 and r.get("boxes_live", {}).get("n") == 4, r.get("reproduciendo"))

    print("\n=== 3) La muestra por minuto ===")
    comprueba("toma la muestra", A._uso_muestra(now))
    comprueba("el otro worker, en el mismo minuto, no la repite", not A._uso_muestra(now + 20))
    d = cli.get("/uso/datos", headers={"X-MW-Uso": llave}).get_json()
    a = d["ahora"]
    comprueba("ahora: 2 viendo, 1 en pausa, 4 Kodis, 2 webs",
              (a["viendo"], a["en_pausa"], a["kodis"], a["webs"]) == (2, 1, 4, 2), a)
    hoy = d["dias"][-1]
    comprueba("hoy: 4 Kodis, 3 teles con algo puesto, pico 2",
              (hoy["kodis"], hoy["teles"], hoy["pico"]) == (4, 3, 2), hoy)
    A._kbnow_save({"111111": {"np": {"title": "x", "paused": False}, "ts": now + 70}})
    A._uso_muestra(now + 70)
    hoy = cli.get("/uso/datos?k=" + llave).get_json()["dias"][-1]
    comprueba("la misma tele otra vez no suma otra tele", hoy["teles"] == 3 and hoy["kodis"] == 4, hoy)
    comprueba("el pico se queda en 2", hoy["pico"] == 2, hoy)
    comprueba("minutos de tele: 2 + 1", A._uso_lee()["dias"][d["hoy"]]["tm"] == 3, A._uso_lee()["dias"])
    comprueba("Kodis activos en 7 días: 4", d["activos_7"] == 4, d)
    comprueba("sin caché en el navegador",
              cli.get("/uso/datos?k=" + llave).headers.get("Cache-Control") == "no-store")

    print("\n=== 4) Ningún código, ningún título ===")
    crudo = open(A._USO_FILE, encoding="utf-8").read()
    respuesta = cli.get("/uso/datos?k=" + llave).get_data(as_text=True)
    comprueba("ni en el fichero", not any(c in crudo for c in CODIGOS), crudo[:200])
    comprueba("ni en la respuesta", not any(c in respuesta for c in CODIGOS))
    comprueba("ni qué ve nadie", "secreta" not in crudo and "secreta" not in respuesta)

    print("\n=== 5) La copia cifrada ===")
    kc, km = os.urandom(32), os.urandom(32)
    doc = A._uso_lee()
    paq = A._uso_empaqueta(doc, kc, km)
    comprueba("ida y vuelta", A._uso_desempaqueta(paq, kc, km) == A._uso_valida(doc))
    comprueba("no se lee nada en claro", "dias" not in paq and doc["adm"] not in paq)
    comprueba("con otra clave no vale (None: no se pisa)", A._uso_desempaqueta(paq, os.urandom(32), km) is None
              and A._uso_desempaqueta(paq, kc, os.urandom(32)) is None)
    roto = paq[:-8] + ("A" if paq[-8] != "A" else "B") + paq[-7:]
    comprueba("manipulada no vale", A._uso_desempaqueta(roto, kc, km) is None)
    comprueba("una copia del formato viejo/ajeno no vale", A._uso_desempaqueta("H4sIAAAA", kc, km) is None)
    otro = A._uso_valida({"adm": "b" * 64, "desde": "2026-10-01",
                          "dias": {d["hoy"]: {"c": ["zzz"], "r": [], "m": [], "pico": 9, "pico_h": "22:10",
                                              "tm": 1, "hp": [0] * 22 + [9, 0]},
                                   "2026-10-01": {"nc": 5, "nr": 3, "nm": 2, "pico": 3, "tm": 400}},
                          "cj": {"zzz": ["2026-10-01", d["hoy"]]}})
    f = A._uso_funde(doc, otro)
    fh = f["dias"][d["hoy"]]
    comprueba("fundir: une las cajas de los dos", len(fh["c"]) == 5, fh["c"])
    comprueba("fundir: pico el mayor, con su hora", (fh["pico"], fh["pico_h"]) == (9, "22:10"), fh)
    comprueba("fundir: minutos el mayor (no se suman: la local ya los traía)", fh["tm"] == 3, fh["tm"])
    comprueba("fundir: los días que solo tiene la copia, enteros", f["dias"]["2026-10-01"]["nc"] == 5)
    comprueba("fundir: la llave de la copia manda", f["adm"] == "b" * 64)
    comprueba("fundir: 'desde' el más antiguo", f["desde"] == "2026-10-01")
    comprueba("fundir dos veces = una (idempotente)", A._uso_funde(f, otro) == f)

    print("\n=== 6) Días viejos y la hora de España ===")
    g = A._uso_valida(f)
    g["dias"]["2026-10-02"] = {"c": ["a", "b"], "r": ["a"], "m": [], "pico": 1, "pico_h": "", "tm": 5,
                               "hp": [0] * 24, "mu": 1}
    A._uso_compacta(g, d["hoy"])
    comprueba("un día viejo se queda en cuentas", "c" not in g["dias"]["2026-10-02"]
              and g["dias"]["2026-10-02"]["nc"] == 2 and A._uso_cuenta(g["dias"]["2026-10-02"], "r") == 1,
              g["dias"]["2026-10-02"])
    comprueba("hoy conserva sus huellas", isinstance(g["dias"][d["hoy"]].get("c"), list))
    import calendar
    ver = calendar.timegm((2026, 7, 15, 22, 30, 0))      # 22:30 UTC en julio
    inv = calendar.timegm((2026, 12, 31, 23, 30, 0))     # 23:30 UTC en Nochevieja
    comprueba("verano: UTC+2", A._uso_es(ver) == ("2026-07-16", 0, "00:30"), A._uso_es(ver))
    comprueba("invierno: UTC+1 y cambio de día", A._uso_es(inv) == ("2027-01-01", 0, "00:30"), A._uso_es(inv))

    print("\n=== 6b) Más detalle (dtbl104): casas, ratos, fuentes y calidades ===")
    for i, c in enumerate(CODIGOS):
        ip = "81.0.0.1" if i < 3 else "90.0.0.9"         # 3 Kodis en una casa, 1 en otra
        cli.post("/kb/status", json={"code": c, "v": "2.9.92"},
                 headers={"X-Forwarded-For": "1.2.3.4, " + ip})
    st = A._kbstatus_load()
    comprueba("el latido guarda la conexión cifrada, nunca la IP",
              st["111111"].get("ih") and "81.0.0.1" not in json.dumps(st), st["111111"])
    comprueba("la IP que cuenta es la del proxy (la última), no la que pone el cliente",
              st["111111"]["ih"] == A._uso_huella("ip:81.0.0.1"))
    A._uso_muestra(A._t.time(), forzar=True)
    d = cli.get("/uso/datos?k=" + llave).get_json()
    hoy = d["dias"][-1]
    comprueba("casas: 2 (cuatro Kodis desde dos conexiones)", hoy["casas"] == 2 and d["ip_medidas"], hoy)
    comprueba("ninguna IP en el fichero", "81.0.0.1" not in open(A._USO_FILE, encoding="utf-8").read())
    comprueba("minutos de cada Kodi: se miden (y 1-3 min = 'solo un rato')",
              hoy["km_n"] == 4 and hoy["cortos"] == 4, hoy)
    comprueba("la mediana de lo que ve una tele, en minutos", d["med_min_tele"] >= 1, d["med_min_tele"])
    for body in ({"a": "st", "u": "https://vidara.to/e/x", "q": "1080p"},
                 {"a": "pl", "u": "magnet:?xt=urn:btih:abc", "f": "wf", "q": "2160p 4K"},
                 {"a": "pl", "u": "magnet:?xt=urn:btih:abd"}):
        cli.post("/kb/send", json=dict(body, code="111111", cmd="play_ref", t="Peli secreta"))
    hoy = cli.get("/uso/datos?k=" + llave).get_json()["dias"][-1]
    comprueba("fuentes de lo enviado: Cinubo 1, WolfMax 1, otra 1",
              hoy["fuentes"] == {"cb": 1, "wf": 1, "otra": 1}, hoy["fuentes"])
    comprueba("calidades: 1080 1, 4K 1, sin indicar 1",
              hoy["calidad"] == {"1080": 1, "4k": 1, "nd": 1}, hoy["calidad"])
    comprueba("y ningún título", "secreta" not in open(A._USO_FILE, encoding="utf-8").read())
    g = A._uso_valida(A._uso_lee())
    viejo = dict(g["dias"][d["hoy"]])
    g["dias"]["2026-10-03"] = viejo
    A._uso_compacta(g, d["hoy"])
    gv = g["dias"]["2026-10-03"]
    comprueba("un día viejo: sin huellas ni minutos por Kodi, con sus cuentas",
              "km" not in gv and "ip" not in gv and gv["nip"] == 2 and gv["nk15"] == 4
              and gv["rf"] == {"cb": 1, "wf": 1, "otra": 1}, gv)
    f2 = A._uso_funde(A._uso_lee(), {"dias": {d["hoy"]: {"km": {"a" * 12: 99}, "rf": {"dt": 2}}}})
    comprueba("fundir: minutos y fuentes, el mayor de cada uno",
              f2["dias"][d["hoy"]]["km"]["a" * 12] == 99 and f2["dias"][d["hoy"]]["rf"].get("dt") == 2
              and f2["dias"][d["hoy"]]["rf"].get("cb") == 1, f2["dias"][d["hoy"]].get("rf"))

    print("\n=== 7) La página ===")
    r = cli.get("/uso")
    html = r.get_data(as_text=True)
    comprueba("se sirve, sin caché y fuera de buscadores", r.status_code == 200
              and r.headers.get("Cache-Control") == "no-store" and "noindex" in r.headers.get("X-Robots-Tag", ""))
    comprueba("no lleva ningún dato dentro", not any(c in html for c in CODIGOS) and llave not in html)
    js = html.split("<script>")[1].split("</script>")[0]
    p = os.path.join(tmpd, "uso.js")
    open(p, "w", encoding="utf-8").write(js)
    rr = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    comprueba("su JavaScript compila", rr.returncode == 0, rr.stderr[:300])
finally:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
        if f in copias:
            shutil.move(copias[f], f)
    shutil.rmtree(tmpd, ignore_errors=True)

print("\n%s" % ("TODO EN VERDE" if not fallos else "%d MAL" % fallos))
sys.exit(1 if fallos else 0)
