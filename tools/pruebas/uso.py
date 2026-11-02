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
FICH = [A._USO_FILE, A._KB_STATUS_FILE, A._KB_NOW_FILE, A._KB_SEEN_FILE, A._USO_PEND_FILE]
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
    comprueba("lo que se ve se suma por título, nunca junto al código (dtbl112)",
              not any(c in crudo for c in CODIGOS) and not any(c in respuesta for c in CODIGOS))

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
                 headers={"X-Forwarded-For": ip + ", 10.9.9.9"})
    st = A._kbstatus_load()
    comprueba("el latido guarda la conexión cifrada, nunca la IP",
              st["111111"].get("ih") and "81.0.0.1" not in json.dumps(st), st["111111"])
    comprueba("la IP que cuenta es la de la casa (la primera), no la del borde de Render",
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
    crudo = open(A._USO_FILE, encoding="utf-8").read()
    comprueba("lo puesto se suma por título, sin el código de la tele",
              "Peli secreta" in crudo and "111111" not in crudo)
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

    print("\n=== 6c) Calidad del servicio, búsquedas, lo más puesto y crecimiento (dtbl106) ===")
    for f in (A._USO_PEND_FILE,):
        try:
            os.remove(f)
        except Exception:
            pass
    t0 = A._t.time()
    # 222222: la web manda The Bear 5x02 por Cinubo y arranca a los 20 s
    cli.post("/kb/send", json={"code": "222222", "cmd": "play_ref", "a": "st", "u": "https://vidara.to/e/y",
                               "t": "The Bear 5x02", "q": "1080p", "k": "tv"})
    A._uso_arranco("222222", A._t.time() + 20)
    # 333333: se manda, se cambia antes de arrancar, y lo segundo nunca arranca
    cli.post("/kb/send", json={"code": "333333", "cmd": "play_ref", "a": "pl", "u": "magnet:?xt=urn:btih:e1",
                               "f": "et", "t": "The Bear 5x03", "k": "tv"})
    cli.post("/kb/send", json={"code": "333333", "cmd": "play_ref", "a": "pl", "u": "magnet:?xt=urn:btih:e2",
                               "f": "wf", "t": "Perdida (2014)", "q": "4K", "k": "movie"})
    # 444444 pone algo con el mando (sin envío de la web)
    A._uso_arranco("444444")
    # pasan 7 minutos: lo de 333333 no arrancó
    st = A._kbstatus_load()
    for c in st:
        st[c]["ts"] = A._t.time() + 400
    st["222222"]["diag"] = {"res": "1920x1080", "cach": "1"}
    st["222222"]["dev"] = {"pl": "android", "kv": "21.1"}
    st["222222"]["pais"] = "ES"
    A._kbstatus_save(st)
    A._kbnow_save({"222222": {"np": {"title": "The Bear", "paused": False}, "ts": A._t.time() + 400}})
    A._uso_muestra(A._t.time() + 400, forzar=True)
    d = cli.get("/uso/datos?k=" + llave).get_json()
    sv = d["servicio"]
    comprueba("Cinubo: 1 arrancó, en ~20 s (tramo 15-30 s)",
              sv["cb"]["ok"] == 1 and 19 <= sv["cb"]["seg_medio"] <= 21 and sv["cb"]["tramos"][1] == 1, sv.get("cb"))
    comprueba("EliteTorrent: se cambió antes de arrancar (no cuenta como fallo)",
              sv["et"]["cambio"] == 1 and sv["et"]["ko"] == 0, sv.get("et"))
    comprueba("WolfMax: en 6 min no arrancó -> 'no arrancó'", sv["wf"]["ko"] == 1 and sv["wf"]["ok"] == 0, sv.get("wf"))
    comprueba("web 1 / mando 1", d["como"] == {"web": 1, "mando": 1}, d["como"])
    comprueba("películas 1 / series 2", d["tipo"] == {"movie": 1, "tv": 2}, d["tipo"])
    comprueba("resolución real: 1 min en 1080", d["resolucion"] == {"1080": 1}, d["resolucion"])
    comprueba("minutos cargando de Cinubo: 1 de 1", sv["cb"]["min_cargando"] == 1 and sv["cb"]["min_viendo"] == 1, sv["cb"])
    comprueba("aparato, versión de Kodi y país", d["aparatos"] == {"android": 1} and d["kodi"] == {"21.1": 1}
              and d["paises"] == {"ES": 1}, (d["aparatos"], d["kodi"], d["paises"]))
    vis = {t: (k, h) for t, k, h in d["semana"]["vistos"]}
    comprueba("lo más visto sale de lo que SE REPRODUCE: 'The Bear' (1 tele, con sus minutos)",
              vis.get("The Bear", (0, 0))[0] == 1, d["semana"]["vistos"])
    comprueba("...y lo mandado que nunca se vio (Perdida) no está", "Perdida" not in vis, vis)
    comprueba("...y también lo puesto con el mando (Peli secreta, de la muestra del principio)",
              "Peli secreta" in vis, vis)
    a = cli.get("/uso/datos?k=" + llave).get_json()["ahora"]
    comprueba("ahora mismo: qué se está viendo, sin código", [x["t"] for x in a["lista"]] == ["The Bear"]
              and not any(c in json.dumps(a) for c in CODIGOS), a.get("lista"))
    for q, n in (("Pérdida", 3), ("  perdida ", 3), ("Gone  Girl!", 0), ("xx", 0)):
        cli.post("/uso/busca", json={"q": q, "n": n}, headers={"X-Forwarded-For": "5.5.5.5, 10.0.0.1"})
    cli.post("/uso/busca", json={"q": "perdida", "n": 2}, headers={"X-Forwarded-For": "6.6.6.6"})
    sem = cli.get("/uso/datos?k=" + llave).get_json()["semana"]
    bus = {q: (a, b) for q, a, b in sem["buscadas"]}
    comprueba("búsquedas: sin tildes ni mayúsculas, y la misma persona no cuenta dos veces",
              bus.get("perdida") == (2, 0), bus)
    comprueba("las que no encuentran nada", ["gone girl", 1] in sem["sin_nada"] and ["xx", 1] in sem["sin_nada"],
              sem["sin_nada"])
    for i in range(45):
        cli.post("/uso/busca", json={"q": "spam %d" % i, "n": 0}, headers={"X-Forwarded-For": "7.7.7.7"})
    sem = cli.get("/uso/datos?k=" + llave).get_json()["semana"]
    comprueba("nadie infla la lista: 40 por persona cada 10 min",
              sum(1 for q, a, b in sem["buscadas"] if q.startswith("spam")) <= 25 and sem["total_busquedas"] <= 46, sem["total_busquedas"])
    comprueba("ningún código en el fichero", not any(c in open(A._USO_FILE, encoding="utf-8").read() for c in CODIGOS))
    import datetime as _dtm
    g = A._uso_valida(A._uso_lee())
    hoy_n = _dtm.date.fromisoformat(d["hoy"]).toordinal()
    g["desde"] = (_dtm.date.fromisoformat(d["hoy"]) - _dtm.timedelta(days=20)).isoformat()
    g["act"] = {"a" * 12: [hoy_n - 10, hoy_n - 3, hoy_n - 2, hoy_n - 1, hoy_n],
                "b" * 12: [hoy_n - 12, hoy_n - 11],
                "c" * 12: [hoy_n]}
    cr = A._uso_crece(g, _dtm.date.fromisoformat(d["hoy"]))
    comprueba("crecimiento: 1 fiel, de 2 que llegaron hace +7 días sigue 1, y 1 se fue",
              (cr["fieles"], cr["llegaron_hace_7"], cr["siguen"], cr["se_fueron"]) == (1, 2, 1, 1), cr)
    comprueba("el título sin el capítulo", A._uso_titulo("The Bear 5x02") == "The Bear"
              and A._uso_titulo("Silo - S02E03") == "Silo" and A._uso_titulo("Alien: Romulus (2024)") == "Alien: Romulus",
              [A._uso_titulo(x) for x in ("The Bear 5x02", "Silo - S02E03", "Alien: Romulus (2024)")])

    print("\n=== 6d) El mismo vídeo no cuenta dos veces (dtbl107) ===")
    antes_pm = dict(cli.get("/uso/datos?k=" + llave).get_json()["como"])
    A._USO_VISTO.clear()
    for i in range(6):
        cli.post("/kb/now", json={"code": "111111", "np": {"title": "Peli larga", "elapsed": i * 3, "total": 5000}})
        if i == 2:          # el fichero de "qué se ve" sale roto (dos workers a la vez)...
            open(A._KB_NOW_FILE, "w").write("{roto")
        if i == 4:          # ...y el otro worker, que no lo tiene en memoria
            A._USO_VISTO.clear()
    pm = cli.get("/uso/datos?k=" + llave).get_json()["como"]
    comprueba("6 avisos del mismo vídeo (con el fichero roto y otro worker): cuenta 1",
              pm.get("mando", 0) - antes_pm.get("mando", 0) == 1, (antes_pm, pm))
    T = A._t.time()
    N = A._uso_titulo_nuevo

    def limpio():
        A._USO_VISTO.clear()
        pd = A._uso_pend_lee()
        for k in [k for k in pd if k.startswith("t:")]:
            pd.pop(k)
        A._uso_pend_guarda(pd, A._t.time())
    limpio()
    comprueba("empieza una serie: cuenta", N("565656", "The Bear 5x02", T, 2, False)[0] is True)
    comprueba("...y a los 6 s Kodi le cambia el nombre al cargar la ficha: el mismo vídeo",
              N("565656", "The Bear · 5x02", T + 6, 8, True)[0] is False)
    comprueba("a mitad de la peli cambia el nombre (minuto 33): no es otro vídeo",
              N("565656", "The Bear 5x02 (2024)", T + 2000, 2000, True)[0] is False)
    comprueba("se acaba y Kodi pone solo el siguiente capítulo: cuenta (en la tele)",
              N("565656", "The Bear · 5x03", T + 2400, 1, True)[0] is True)
    comprueba("se apaga y al día siguiente se reanuda por el minuto 40: cuenta",
              N("565656", "Silo · 2x01", T + 9000, 2400, False)[0] is True)
    t1 = A._t.time()
    N("575757", "Algo", t1 - 30, 2, False)                 # empezó algo hace 30 s
    A._uso_envio("cb", "1080p", "575757", "Perdida", "movie")   # y ahora se manda otra cosa desde la web
    ok_web = N("575757", "Perdida", A._t.time() + 1, 3, True)[0]
    if ok_web:
        A._uso_arranco("575757", A._t.time() + 1)
    comprueba("un envío NUEVO de la web a los 30 s de otro inicio: cuenta igual", ok_web is True)
    # 4 teles con el MISMO código (Kodi copiado), cada una empieza a su hora y avisan por turnos
    limpio()
    inicios = 0
    for i, t in enumerate(("Uno", "Dos", "Tres", "Cuatro")):
        inicios += N("444444", t, T + i * 300, 5, True)[0]
    comp = 0
    for vuelta in range(5):
        for i, t in enumerate(("Uno", "Dos", "Tres", "Cuatro")):
            a, b = N("444444", t, T + 1000 + vuelta * 3 + i * 0.5, 600 + vuelta * 3, True)
            inicios += a
            comp += b
        if vuelta == 2:
            A._USO_VISTO.clear()      # el otro worker
    comprueba("4 teles con un mismo código, avisando por turnos: 4 vídeos, no 24", inicios == 4, inicios)
    comprueba("...y se ve que ese código lo usan varias teles", comp > 0, comp)
    cli.post("/kb/now", json={"code": "444444", "np": {"title": "Uno", "elapsed": 700, "total": 5000}})
    cli.post("/kb/now", json={"code": "444444", "np": {"title": "Dos", "elapsed": 700, "total": 5000}})
    cli.post("/kb/now", json={"code": "444444", "np": {"title": "Uno", "elapsed": 703, "total": 5000}})
    d3 = cli.get("/uso/datos?k=" + llave).get_json()
    comprueba("...y la página lo cuenta como código compartido", d3["dias"][-1]["compartidos"] == 1, d3["dias"][-1])
    A._uso_muestra(A._t.time() + 900, forzar=True)
    sv = cli.get("/uso/datos?k=" + llave).get_json()["servicio"]
    comprueba("lo que recuerda los títulos no se toma por envíos que no arrancaron",
              sum(x["ko"] for x in sv.values()) == 1, sv)
    print("\n=== 6e) Limpiar datos malos, también frente a la copia (dtbl111) ===")
    hoyd = cli.get("/uso/datos?k=" + llave).get_json()["hoy"]
    nube_vieja = A._uso_valida(A._uso_lee())            # la copia de la nube, con lo malo
    comprueba("sin llave no se limpia nada",
              cli.post("/uso/limpia", json={"dia": hoyd, "claves": ["pm"]}).status_code == 401)
    r = cli.post("/uso/limpia", json={"dia": hoyd, "claves": ["pm", "du", "nada"]},
                 headers={"X-MW-Uso": llave}).get_json()
    comprueba("con llave: limpia las claves que existen (y no inventa)", r.get("limpias") == ["pm", "du"], r)
    dl = cli.get("/uso/datos?k=" + llave).get_json()
    comprueba("como y compartidos a cero", dl["como"] == {} and dl["dias"][-1]["compartidos"] == 0,
              (dl["como"], dl["dias"][-1]["compartidos"]))
    f = A._uso_funde(A._uso_lee(), nube_vieja)
    comprueba("al fundir con la copia vieja, lo malo NO vuelve", not f["dias"][hoyd].get("pm") and not f["dias"][hoyd].get("du"),
              f["dias"][hoyd].get("pm"))
    f2 = A._uso_funde(nube_vieja, A._uso_lee())
    comprueba("...ni fundiendo al revés", not f2["dias"][hoyd].get("pm"), f2["dias"][hoyd].get("pm"))
    A._uso_arranco("616161")
    comprueba("y lo nuevo se sigue contando", cli.get("/uso/datos?k=" + llave).get_json()["como"] == {"mando": 1})

    print("\n=== 6f) Envíos: tele apagada y el mismo capítulo otra vez (dtbl111) ===")
    A._uso_envio("dt", "1080p", "808080", "Algo", "movie", espera=False)
    comprueba("a una tele apagada no se le espera (no culpa a la fuente)", "808080" not in A._uso_pend_lee())
    # 616161 ya está viendo "Silo" (título conocido); se le re-envía lo mismo desde la web
    A._uso_envio("cb", "1080p", "616161", "Silo", "tv")
    antes = cli.get("/uso/datos?k=" + llave).get_json()
    st = A._kbstatus_load()
    st["616161"] = {"v": "2.9.94", "ts": A._t.time() + 60}
    A._kbstatus_save(st)
    A._kbnow_save({"616161": {"np": {"title": "Silo", "elapsed": 40, "paused": False}, "ts": A._t.time() + 60}})
    A._uso_muestra(A._t.time() + 60, forzar=True)
    desp = cli.get("/uso/datos?k=" + llave).get_json()
    comprueba("re-enviado y el vídeo va por el segundo 40 de los 60 desde el envío: arrancó (a los ~20 s)",
              desp["servicio"]["cb"]["ok"] == antes["servicio"]["cb"]["ok"] + 1
              and desp["como"].get("web", 0) == antes["como"].get("web", 0) + 1, (antes["servicio"]["cb"], desp["servicio"]["cb"]))
    comprueba("...y no se cuenta como fallo más tarde", (A._uso_pend_lee().get("616161") or {}).get("hecho") == "ok")

    import inspect
    comprueba("cada worker escribe su propio temporal (kbnow y kbstatus)",
              "os.getpid()" in inspect.getsource(A._kbnow_save) and "os.getpid()" in inspect.getsource(A._kbstatus_save))

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
