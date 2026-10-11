# -*- coding: utf-8 -*-
"""El MISMO torrent en DonTorrent y en WolfMax (dtbl79 + caja 2.9.90). Sin red.

WolfMax republica muchos .torrent de DonTorrent tal cual (misma huella). Los de
DonTorrent los aprende el aprendiz solo; con su nombre y su tamaño, una ficha de
WolfMax sin huella se ata a la de DonTorrent SOLO si es, sin duda, el mismo
archivo. Se comprueba:
  1) la caja 2.9.90 da nombre y bytes como el relay los lee;
  2) la regla estricta: mismo titulo (sin palabras de mas), tamaño exacto,
     misma resolucion y año, nada de capitulos; versiones, titulos parecidos y
     secuelas NO casan;
  3) el cruce: ata con origen "dt", no pisa lo atado, no ata con duda (dos
     candidatos, dos fichas, otro de los ZIP, huella ya de otra ficha, RAR,
     series) y repetirlo no cambia nada;
  4) el aprendiz: guarda nombre y tamaño, vuelve a pedir SOLO lo que puede
     casar, y una caja sin actualizar ni falla ni se le pide en bucle;
  5) la copia de las semillas en mw-sync sigue igual (sin nombre ni tamaño).
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(RAIZ, "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


def benc(x):
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode("utf-8")
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(benc(v) for v in x) + b"e"
    return b"d" + b"".join(benc(k) + benc(x[k]) for k in sorted(x)) + b"e"


GIB = 1024 ** 3
P = "https://wolfmax4k.com/pelicula/"
ih = lambda s: hashlib.sha1(s.encode()).hexdigest()     # noqa: E731

TMP = tempfile.mkdtemp(prefix="mw_cruce_")
viejos = {k: getattr(A, k) for k in ("_WFIH_FILE", "_DTPACKED_FILE", "_WFULT_FILE", "_DXIH_FILE",
                                     "_SEEDS_FILE", "_apr_caja", "_lend_acquire", "_lend_release",
                                     "_kb_enqueue", "_catjob_wait", "_seed_counts_many", "_seeds_guarda",
                                     "_apr_items_inicio", "_semillas_nube_sube", "_dt_caida_ya", "_apr_dt")}
try:
    A._WFIH_FILE = os.path.join(TMP, "wfih.json")
    A._DTPACKED_FILE = os.path.join(TMP, "dtp.json")
    A._WFULT_FILE = os.path.join(TMP, "wfult.json")
    A._DXIH_FILE = os.path.join(TMP, "dxih.json")
    A._SEEDS_FILE = os.path.join(TMP, "seeds.json")
    A._seed_counts_many = lambda ihs: {}
    A._seeds_guarda = lambda *a, **k: None
    A._semillas_nube_sube = lambda *a, **k: 0
    A._dt_caida_ya = lambda: False

    def reinicia(fichas, dtp, wfih=None):
        """fichas: [(codigo, titulo, calidad, tamaño)] de PELICULAS de WolfMax."""
        A._WFULT.clear()
        A._WFULT_MT[0] = 0.0
        A._WFULT["movie"] = {"ts": time.time(), "items": [
            {"title": t, "url": P + c, "quality": q, "size": sz, "source": "wf", "kind": "movie"}
            for c, t, q, sz in fichas]}
        A._wfult_guarda()
        json.dump(dtp, open(A._DTPACKED_FILE, "w", encoding="utf-8"))
        json.dump(wfih or {"h": {}, "p": {}, "zip": {}}, open(A._WFIH_FILE, "w", encoding="utf-8"))
        A._WFIH_MEM.update(mt=-1.0, d=None)

    print("\n=== 1) La caja 2.9.90 da el nombre y los bytes como los lee el relay ===")
    spec = importlib.util.spec_from_file_location(
        "tparse", os.path.join(RAIZ, "plugin.video.mejorwolf", "resources", "lib", "torrent.py"))
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    uno = benc({"announce": "x", "info": {"name": "Insidious Fuera del mas alla [4k 2160p][Esp].mkv",
                                          "length": int(18.88 * GIB), "piece length": 1, "pieces": b"\0" * 20}})
    varios = benc({"announce": "x", "info": {"name": "Peli (2025) [Bluray 1080p][Esp]",
                                             "name.utf-8": "Película (2025) [Bluray 1080p][Esp]",
                                             "files": [{"length": 7 * GIB, "path": ["a.mkv"]},
                                                       {"length": 1234, "path": ["a.srt"]}],
                                             "piece length": 1, "pieces": b"\0" * 20}})
    for nombre, dat in (("un archivo", uno), ("varios archivos y name.utf-8", varios)):
        n, b = T.nombre_y_tamano(dat)
        r = A._torrent_lee(dat)
        comprueba("%s: mismo nombre y bytes que _torrent_lee del relay" % nombre,
                  (n, b) == (r["n"], r["b"]), ((n, b), (r["n"], r["b"])))
    comprueba("basura: ('', 0) sin excepcion", T.nombre_y_tamano(b"<html>") == ("", 0)
              and T.nombre_y_tamano(b"") == ("", 0) and T.nombre_y_tamano(b"d4:infoi1ee") == ("", 0))
    srv = open(os.path.join(RAIZ, "plugin.video.mejorwolf", "service.py"), encoding="utf-8").read()
    comprueba("el trabajo dtmeta de la caja devuelve n y b",
              'out["n"], out["b"] = tparse.nombre_y_tamano(data)' in srv
              and 'out["n"] = m.get("n", "")' in srv and 'out["b"] = m.get("b", 0)' in srv)

    print("\n=== 2) La regla estricta: sin duda, o nada ===")
    E = A._wf_casa_estricta
    ins = {"t": "Insidious: Fuera del más allá", "q": "4K", "sz": "18,88 GB"}
    bueno = "Insidious Fuera del mas alla (2023) [4k 2160p][Esp]"
    comprueba("mismo titulo, tamaño exacto y resolucion: casa", E(bueno, int(18.88 * GIB), ins))
    ams = {"t": "Amsterdamned II: Misterio en los canales", "q": "BluRay", "sz": "14,76 GB"}
    comprueba("nombre real algo mas largo ('...El regreso'), mismo archivo: casa",
              E("Amsterdamned II Misterio en los canales El regreso (2025) [Bluray 1080p][Esp]", 15845053569, ams))
    comprueba("otro tamaño: no", not E(bueno, int(18.50 * GIB), ins))
    comprueba("OTRA VERSION del mismo titulo (1080p) con el mismo tamaño: no",
              not E("Insidious Fuera del mas alla (2023) [Bluray 1080p][Esp]", int(18.88 * GIB), ins))
    comprueba("otro AÑO (la de 2010 frente a la de 2023): no",
              not E("Insidious Fuera del mas alla (2010) [4k 2160p]", int(18.88 * GIB), dict(ins, y="2023")))
    alien = {"t": "Alien", "q": "4K", "sz": "20,00 GB"}
    comprueba("titulo PARECIDO mas largo ('Alien' frente a 'Alien Romulus'): no",
              not E("Alien Romulus (2024) [4k 2160p][Esp]", int(20.0 * GIB), alien))
    comprueba("SECUELA ('Insidious 2' frente a 'Insidious'): no",
              not E("Insidious (2010) [4k 2160p]", int(18.88 * GIB), dict(ins, t="Insidious 2")))
    comprueba("un CAPITULO no es una peli: no",
              not E("Ted Lasso [HDTV][Cap.401]", int(0.5 * GIB), {"t": "Ted Lasso", "q": "HDTV", "sz": "512 MB"}))
    comprueba("ficha SIN tamaño (la regla del ZIP aceptaria por calidad): no",
              not E(bueno, int(18.88 * GIB), dict(ins, sz="")))

    print("\n=== 3) El cruce: ata lo seguro, nada con duda, y repetirlo no cambia nada ===")
    X = ih("ya-atada")
    fichas = [("aaa111", "Insidious: Fuera del más allá", "4K", "18,88 GB"),            # se ata
              ("bbb222", "Amsterdamned II: Misterio en los canales", "BluRay", "14,76 GB"),  # se ata
              ("ccc333", "Ya Atada", "4K", "5,00 GB"),          # atada antes (persona): intocable
              ("kkk000", "Del Zip", "1080p", "6,00 GB"),          # atada antes (zip): intocable
              ("ddd444", "Gemela", "1080p", "3,33 GB"),            # dos torrents casan: duda
              ("eee555", "Doble", "1080p", "2,22 GB"),             # un torrent, dos fichas: duda
              ("fff666", "Doble", "1080p", "2,22 GB"),
              ("ggg777", "Con Reserva", "1080p", "4,44 GB"),       # otro de los ZIP casa: duda
              ("iii999", "En Rar", "1080p", "7,77 GB"),            # el de DonTorrent es RAR
              ("jjj000", "Huella Ocupada", "1080p", "8,88 GB")]    # su torrent ya es de otra ficha
    dtp = {
        "peliculas:1": {"ih": ih("ins"), "ts": time.time(), "q": "4K", "p": False,
                        "n": "Insidious Fuera del mas alla (2023) [4k 2160p][Esp]", "b": int(18.88 * GIB)},
        "peliculas:2": {"ih": ih("ams"), "ts": time.time(), "q": "BluRay", "p": False,
                        "n": "Amsterdamned II Misterio en los canales El regreso (2025) [Bluray 1080p][Esp]",
                        "b": 15845053569},
        "peliculas:3": {"ih": ih("otra-ya"), "ts": time.time(), "p": False, "n": "Ya Atada [4k]", "b": 5 * GIB},
        "peliculas:30": {"ih": ih("otra-zip"), "ts": time.time(), "p": False, "n": "Del Zip [1080p]", "b": 6 * GIB},
        "peliculas:4": {"ih": ih("gem1"), "ts": time.time(), "p": False, "n": "Gemela [1080p]", "b": int(3.33 * GIB)},
        "peliculas:5": {"ih": ih("gem2"), "ts": time.time(), "p": False, "n": "Gemela [1080p] (2)",
                        "b": int(3.33 * GIB) + 1000},
        "peliculas:6": {"ih": ih("doble"), "ts": time.time(), "p": False, "n": "Doble [1080p]", "b": int(2.22 * GIB)},
        "peliculas:7": {"ih": ih("res-dt"), "ts": time.time(), "p": False, "n": "Con Reserva [1080p]",
                        "b": int(4.44 * GIB)},
        "peliculas:8": {"ih": ih("rar"), "ts": time.time(), "p": True, "n": "En Rar [1080p]", "b": int(7.77 * GIB)},
        "peliculas:9": {"ih": X, "ts": time.time(), "p": False, "n": "Huella Ocupada [1080p]", "b": int(8.88 * GIB)},
        "series:10": {"ih": ih("serie"), "ts": time.time(), "p": False, "n": "Insidious Fuera del mas alla [4k]",
                      "b": int(18.88 * GIB)},
    }
    wfih0 = {"h": {P + "ccc333": {"ih": ih("persona"), "o": "persona", "ts": 1},
                   P + "kkk000": {"ih": ih("del-zip"), "o": "zip", "ts": 1},
                   "https://wolfmax4k.com/pelicula/otra-ficha": {"ih": X, "o": "aprendiz", "ts": 1}},
             "p": {ih("res-zip"): {"n": "Con Reserva [1080p][Esp]", "b": int(4.44 * GIB) + 5, "f": "x.torrent",
                                   "d": "2026-10-10", "ts": 1}},
             "zip": {}}
    reinicia(fichas, dtp, wfih0)
    n1 = A._wf_cruce_dt()
    h = A._wfih_actual()["h"]
    comprueba("ata exactamente las dos seguras", n1 == 2 and h.get(P + "aaa111", {}).get("ih") == ih("ins")
              and h.get(P + "bbb222", {}).get("ih") == ih("ams"), (n1, sorted(h)))
    comprueba("...con su origen 'dt', su nombre y sus bytes",
              h[P + "aaa111"].get("o") == "dt" and h[P + "aaa111"].get("b") == int(18.88 * GIB)
              and "Insidious" in h[P + "aaa111"].get("n", ""), h.get(P + "aaa111"))
    comprueba("no pisa lo que ya estaba atado (por una persona y por un ZIP)",
              h[P + "ccc333"] == wfih0["h"][P + "ccc333"] and h[P + "kkk000"] == wfih0["h"][P + "kkk000"])
    comprueba("duda: dos torrents para una ficha -> nada", P + "ddd444" not in h)
    comprueba("duda: un torrent para dos fichas -> nada", P + "eee555" not in h and P + "fff666" not in h)
    comprueba("duda: otro torrent de los ZIP tambien casa -> nada", P + "ggg777" not in h)
    comprueba("un torrent en RAR -> nada", P + "iii999" not in h)
    comprueba("una huella que ya es de otra ficha -> nada", P + "jjj000" not in h)
    comprueba("lo de SERIES de DonTorrent no entra en el cruce",
              not any(e.get("ih") == ih("serie") for e in h.values()))
    antes = open(A._WFIH_FILE, "rb").read()
    n2 = A._wf_cruce_dt()
    comprueba("repetirlo: 0 cambios y el almacen identico, byte a byte",
              n2 == 0 and open(A._WFIH_FILE, "rb").read() == antes, n2)
    comprueba("/catdiag lo cuenta: origen 'dt' y cruce_dt",
              (A.app.test_client().get("/catdiag").get_json().get("wfih") or {}).get("origen", {}).get("dt") == 2)

    print("\n=== 4) El aprendiz ===")
    PEDIDOS, RESP = [], {}
    A._apr_caja = lambda: "111111"
    A._lend_acquire = lambda box: object()
    A._lend_release = lambda s: None
    A._kb_enqueue = lambda box, ev: PEDIDOS.append(ev)
    A._catjob_wait = lambda job, t: dict(RESP)
    RESP.update({"ih": ih("nueva"), "rar": False, "quality": "4K",
                 "n": "Nueva Peli (2026) [4k 2160p][Esp]", "b": int(21.5 * GIB)})
    r = A._apr_dt("peliculas:9001")
    comprueba("caja 2.9.90: el aprendiz recibe nombre y bytes", r.get("n", "").startswith("Nueva Peli")
              and r.get("b") == int(21.5 * GIB), r)
    RESP.clear()
    RESP.update({"ih": ih("vieja"), "rar": False, "quality": "4K"})
    r_vieja = A._apr_dt("peliculas:9002")
    comprueba("caja ANTERIOR (sin n ni b): su huella como siempre, sin error y sin nombre",
              r_vieja.get("ih") == ih("vieja") and "n" not in r_vieja and "b" not in r_vieja, r_vieja)
    for raro in ({"n": 5, "b": 10}, {"n": "x", "b": "abc"}, {"n": "x", "b": -3}, {"n": "  ", "b": 9}):
        RESP.clear()
        RESP.update(dict(raro, ih=ih("raro")))
        rr = A._apr_dt("peliculas:9003")
        if "n" in rr or "b" in rr:
            break
    comprueba("nombre o bytes con mala forma: se ignoran, sin error", "n" not in rr and "b" not in rr, rr)
    reinicia([("lll111", "Nueva Peli", "4K", "21,50 GB")], {})
    A._apr_guarda("dt", "peliculas:9001", {"ih": ih("nueva"), "p": False, "q": "4K",
                                           "n": "Nueva Peli (2026) [4k 2160p][Esp]", "b": int(21.5 * GIB)},
                  time.time())
    e = A._dtpacked_load().get("peliculas:9001") or {}
    comprueba("se guarda con nombre y bytes...", e.get("b") == int(21.5 * GIB) and e.get("n"), e)
    comprueba("...y al momento ata su ficha de WolfMax (origen 'dt')",
              (A._wfih_de(P + "lll111") or {}).get("o") == "dt", A._wfih_de(P + "lll111"))
    A._apr_guarda("dt", "peliculas:9001", {"ih": ih("otra-version"), "p": False, "q": "4K"}, time.time())
    e = A._dtpacked_load().get("peliculas:9001") or {}
    comprueba("si DonTorrent cambia ese .torrent, el nombre y bytes viejos se olvidan",
              e.get("ih") == ih("otra-version") and "n" not in e and "b" not in e, e)
    comprueba("...y la ficha ya atada no se toca", (A._wfih_de(P + "lll111") or {}).get("ih") == ih("nueva"))

    # quien se vuelve a pedir: solo lo que puede casar
    reinicia([("mmm111", "Insidious: Fuera del más allá", "4K", "18,88 GB")], {
        "peliculas:9100": {"ih": ih("ins"), "ts": time.time(), "p": False, "q": "4K", "s": 5, "sts": time.time()},
        "peliculas:9101": {"ih": ih("otra"), "ts": time.time(), "p": False, "q": "4K", "s": 5, "sts": time.time()},
        "peliculas:9102": {"ih": ih("conb"), "ts": time.time(), "p": False, "q": "4K", "s": 5, "sts": time.time(),
                           "n": "Insidious x", "b": 1}})
    items = [{"source": "dt", "content_id": "9100", "tabla": "peliculas", "kind": "movie",
              "title": "Insidious: Fuera del más allá"},
             {"source": "dt", "content_id": "9101", "tabla": "peliculas", "kind": "movie", "title": "Otra Cosa"},
             {"source": "dt", "content_id": "9102", "tabla": "peliculas", "kind": "movie",
              "title": "Insidious: Fuera del más allá"}]
    A._APR_NEG.clear()
    pend, _v = A._apr_pendientes(items, time.time())
    ks = [k for _s, k, _i in pend]
    comprueba("se vuelve a pedir SOLO la de DonTorrent con ficha de WolfMax libre de ese titulo",
              ks == ["peliculas:9100"], ks)
    A._APR_NEG["nb:peliculas:9100"] = (time.time(), 12 * 3600)
    pend, _v = A._apr_pendientes(items, time.time())
    comprueba("si la caja ya dijo que no sabe (12 h): no se pide", not pend, pend)
    A._APR_NEG.clear()
    # una vuelta real del aprendiz con una caja sin actualizar
    A._apr_items_inicio = lambda: items[:1]
    A._APR_T_DT[0] = 0.0
    A._WF_CRUCE_T[0] = time.time()
    A._apr_dt = lambda k: {"ih": ih("ins"), "p": False, "q": "4K"}     # caja sin actualizar
    try:
        A._apr_ronda()
        ok = True
    except Exception as ex:
        ok = ex
    comprueba("una vuelta con una caja sin actualizar: sin error...", ok is True, ok)
    neg = A._APR_NEG.get("nb:peliculas:9100")
    comprueba("...y no se le vuelve a pedir en 12 h (nada de bucles)", neg and neg[1] == 12 * 3600, neg)
    pend, _v = A._apr_pendientes(items, time.time())
    comprueba("...la siguiente vuelta ya no la tiene pendiente", not pend, pend)

    print("\n=== 5) Lo que NO cambia ===")
    src = open(os.path.join(RAIZ, "render_relay", "app.py"), encoding="utf-8").read()
    comprueba("la copia de las semillas en mw-sync sube lo mismo que antes (sin nombre ni bytes)",
              'for kk in ("p", "q", "ih", "ts", "s", "sts")' in src)
    comprueba("y siguen siendo cuatro escrituras firmadas a mw-sync",
              src.count("_post_con_tope(") == 1 + src.count("_post_con_tope(_SEMI_SYNC") +
              src.count("_post_con_tope(_ENRIQ_SYNC") + src.count("_post_con_tope(_WFIH_SYNC") +
              src.count("_post_con_tope(_WFIDX_SYNC"))
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._WFULT.clear()
    A._WFIH_MEM.update(mt=-1.0, d=None)
    A._APR_NEG.clear()
    shutil.rmtree(TMP, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: lo que es el mismo torrent en DonTorrent se ve por magnet en WolfMax; con duda, nada")
