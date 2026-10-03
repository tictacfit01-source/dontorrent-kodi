# -*- coding: utf-8 -*-
"""La copia de las fichas del Inicio fuera de /tmp (dtbl65).

Render vacia /tmp en cada despliegue y con el se iba lo que las cajas habian
resuelto para cada titulo del Inicio (cartel HD, año, nota, titulo con tildes):
durante unos minutos salia "Un mundo frgil y maravilloso" con carteles pequeños.
Ahora va al worker mw-sync, como las semillas. Aqui, con un worker de mentira:
se lee antes de escribir y se sube la UNION, un meta de mas rango nunca lo pisa
uno de menos, sin poder leer la copia no se sube nada, al arrancar se recupera,
y si no cabe se suben las mas recientes. Sin red.
"""
import json
import os
import shutil
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


def meta(t, rango=2, extra=""):
    m = {"poster": "https://image.tmdb.org/t/p/w500/%s.jpg" % t, "year": "2026", "rating": 7.5,
         "title": t, "overview": "Sinopsis de %s. %s" % (t, extra)}
    if rango >= 1:
        m["dtok"] = 1
    if rango >= 2:
        m["mv"] = 2
    return m


NUBE = {"gz": None}
SUBIDAS = []
LEE_OK = [True]


def get_falso(url, tope, *a, **k):
    if not LEE_OK[0]:
        return "", 0
    return json.dumps({"ok": True, "gz": NUBE["gz"], "ts": 0}), 200


def post_falso(url, cuerpo, tope):
    SUBIDAS.append(len(cuerpo.get("gz") or ""))
    NUBE["gz"] = cuerpo.get("gz")
    return 200


def nube():
    import base64
    import gzip
    if not NUBE["gz"]:
        return {}
    return json.loads(gzip.decompress(base64.b64decode(NUBE["gz"])).decode("utf-8"))["m"]


os.makedirs("/tmp", exist_ok=True)
copia = None
if os.path.exists(A._CAT_ENRICH_FILE):
    copia = A._CAT_ENRICH_FILE + ".prueba_en_bak"
    shutil.copy(A._CAT_ENRICH_FILE, copia)
viejos = (A._get_con_tope, A._post_con_tope)
A._get_con_tope, A._post_con_tope = get_falso, post_falso
try:
    try:
        os.remove(A._CAT_ENRICH_FILE)
    except Exception:
        pass
    print("\n=== 1) Sube la union ===")
    A._cat_enrich_store({"1": meta("Brothers"), "2": meta("Neagley", 1)})
    A._enriq_sube_hilo()
    comprueba("lo de /tmp, arriba", set(nube()) == {"1", "2"} and len(SUBIDAS) == 1, list(nube()))
    os.remove(A._CAT_ENRICH_FILE)
    A._cat_enrich_store({"3": meta("Te conozco")})
    A._enriq_sube_hilo()
    comprueba("con un /tmp a medias (recien desplegado) NO pisa la copia: sube la union",
              set(nube()) == {"1", "2", "3"}, list(nube()))
    comprueba("...y de paso recupera aqui lo que faltaba", set(A._cat_enrich_load()) == {"1", "2", "3"},
              list(A._cat_enrich_load()))

    print("\n=== 2) El rango manda ===")
    os.remove(A._CAT_ENRICH_FILE)
    A._cat_enrich_store({"1": dict(meta("Hermanos", 1), poster="https://image.tmdb.org/t/p/w500/mal.jpg")})
    A._enriq_sube_hilo()
    comprueba("el de rango 2 de la copia gana al de rango 1 de aqui (y aqui se corrige)",
              nube()["1"]["title"] == "Brothers" and A._cat_enrich_load()["1"]["title"] == "Brothers",
              (nube()["1"].get("title"), A._cat_enrich_load()["1"].get("title")))

    print("\n=== 3) Sin poder leer la copia, no se sube nada ===")
    n = len(SUBIDAS)
    LEE_OK[0] = False
    A._enriq_sube_hilo()
    LEE_OK[0] = True
    comprueba("ni una subida a ciegas", len(SUBIDAS) == n, SUBIDAS)

    print("\n=== 4) Al arrancar (tras un despliegue) ===")
    os.remove(A._CAT_ENRICH_FILE)
    n = A._enriq_nube_baja()
    comprueba("recupera las fichas de la copia", n == 3 and A._cat_enrich_load()["3"]["title"] == "Te conozco",
              (n, list(A._cat_enrich_load())))

    print("\n=== 5) Si no cabe, las mas recientes ===")
    tope = A._ENRIQ_TOPE_GZ
    A._ENRIQ_TOPE_GZ = 6000
    try:
        import random
        rnd = random.Random(7)
        grande = dict(("x%d" % i, meta("T%d" % i, 2, "".join(rnd.choice("abcdefghij ") for _ in range(400))))
                      for i in range(60))
        gz, cuantas = A._enriq_empaqueta(grande)
        comprueba("cabe en el tope", 0 < len(gz) <= 6000 and 0 < cuantas < 60, (len(gz), cuantas))
        dentro = A._json.loads(__import__("gzip").decompress(__import__("base64").b64decode(gz)))["m"]
        comprueba("...y son las mas recientes (las ultimas que llegaron)", "x59" in dentro and "x0" not in dentro,
                  sorted(dentro)[:3])
    finally:
        A._ENRIQ_TOPE_GZ = tope
    comprueba("/catdiag lo ensena", "enriq_nube" in (A.app.test_client().get("/catdiag").get_json() or {}))
finally:
    A._get_con_tope, A._post_con_tope = viejos
    try:
        if copia:
            shutil.move(copia, A._CAT_ENRICH_FILE)
        elif os.path.exists(A._CAT_ENRICH_FILE):
            os.remove(A._CAT_ENRICH_FILE)
    except Exception:
        pass

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: las fichas del Inicio sobreviven a los despliegues")
