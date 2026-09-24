# -*- coding: utf-8 -*-
"""El vigilante de memoria del relay (dtbl38): sin red.

23-09-2026, 22:30: cada worker en 161 MB con 1,6 h de vida y las cachas en
~1 MB. Por encima de 150 MB el vigilante podaba CADA 8 s sin soltar nada (626
podas, "libero 0") y cada poda vaciaba las cachas buenas: busquedas, TMDB y
fichas estaban a cero en plena hora de tele. Esto vigila:
  1) una poda que no suelta nada no se repite hasta pasado un rato (10 min;
     2 si va apurado), y una que si suelta no se frena;
  2) el relevo por memoria NO depende de esa pausa;
  3) /catmem ensena el monton de glibc ("malloc") y, a mano, los tipos de
     objeto vivos (?tipos=1).
La parte de glibc (dos arenas, malloc_trim, mallinfo2) solo existe en Linux:
aqui se comprueba que fuera de Linux no hace nada ni rompe; en Linux la
comprueba el workflow relay-check antes de desplegar.
"""
import json
import os
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

A._SEMI_SYNC = "http://127.0.0.1:9/kv/semillas"
A._WFIDX_SYNC = "http://127.0.0.1:9/wfidx"

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


RSS, TOT, LIB = [160.0], [300.0], [0.0]
PODAS, RELEVOS = [], []
viejos = {k: getattr(A, k) for k in ("_rss_mb", "_mem_cgroup_mb", "_mem_poda",
                                     "_bnd_revisa", "_mem_relevo")}
A._rss_mb = lambda: RSS[0]
A._mem_cgroup_mb = lambda: TOT[0]


def poda_falsa(grave=False):
    PODAS.append(grave)
    return LIB[0]


A._mem_poda = poda_falsa
A._bnd_revisa = lambda: None
A._mem_relevo = lambda motivo: RELEVOS.append(motivo)


def limpia():
    del PODAS[:]
    del RELEVOS[:]
    A._MEM_PODA_NO_ANTES[0] = 0.0
    A._MEM_WATCH["pausadas"] = 0
    A._MEM_T0[0] = time.time()          # recien nacido: sin relevos


try:
    print("\n=== 1) Una poda que no suelta nada no se repite cada 8 s ===")
    limpia()
    RSS[0], TOT[0], LIB[0] = 160.0, 300.0, 0.0
    A._mem_vigila_vuelta()
    comprueba("160 MB: poda (suave) una vez", PODAS == [False], PODAS)
    espera = A._MEM_PODA_NO_ANTES[0] - time.time()
    comprueba("...no solto nada: pausa de ~10 min (%.0f s)" % espera, 590 < espera <= 600)
    for _ in range(5):
        A._mem_vigila_vuelta()
    comprueba("las 5 vueltas siguientes NO podan (antes: 5 podas mas)",
              PODAS == [False] and A._MEM_WATCH["pausadas"] == 5,
              (PODAS, A._MEM_WATCH["pausadas"]))
    A._MEM_PODA_NO_ANTES[0] = time.time() - 1
    A._mem_vigila_vuelta()
    comprueba("pasada la pausa, vuelve a intentarlo", PODAS == [False, False], PODAS)

    print("\n=== 2) Una poda que SI suelta no se frena ===")
    limpia()
    LIB[0] = 12.0
    A._mem_vigila_vuelta()
    A._mem_vigila_vuelta()
    comprueba("solto 12 MB: la siguiente vuelta puede volver a podar",
              PODAS == [False, False] and A._MEM_PODA_NO_ANTES[0] == 0.0, PODAS)

    print("\n=== 3) Apurado (poda grave) sin soltar nada: pausa corta ===")
    limpia()
    RSS[0], LIB[0] = 205.0, 0.0
    A._mem_vigila_vuelta()
    espera = A._MEM_PODA_NO_ANTES[0] - time.time()
    comprueba("205 MB: poda GRAVE y pausa de ~2 min (%.0f s)" % espera,
              PODAS == [True] and 110 < espera <= 120, (PODAS, espera))
    comprueba("por debajo de 150 y con el servicio holgado: ni poda ni pausa",
              (lambda: (limpia(), RSS.__setitem__(0, 100.0), TOT.__setitem__(0, 200.0),
                        A._mem_vigila_vuelta(), PODAS == []
                        and A._MEM_WATCH["pausadas"] == 0)[-1])())

    print("\n=== 4) El relevo no depende de la pausa ===")
    limpia()
    A._MEM_T0[0] = time.time() - 3600        # worker con una hora
    A._MEM_PODA_NO_ANTES[0] = time.time() + 600
    RSS[0], TOT[0] = A._MEM_MATAR_MB + 5, 300.0
    A._mem_vigila_vuelta()
    comprueba("memoria por encima del tope, con la poda en pausa: se releva",
              RELEVOS and RELEVOS[0].startswith("memoria=") and not PODAS, (RELEVOS, PODAS))
    limpia()
    A._MEM_T0[0] = time.time() - 3600
    RSS[0], TOT[0] = 130.0, A._MEM_TOTAL_MB + 5
    A._MEM_PODA_NO_ANTES[0] = time.time() + 600
    A._mem_vigila_vuelta()
    comprueba("el servicio entero por encima del tope: se releva el gordo",
              RELEVOS and RELEVOS[0].startswith("servicio="), RELEVOS)
finally:
    for k, v in viejos.items():
        setattr(A, k, v)

print("\n=== 4b) La basura en ciclos, recogida cada 5 min (dtbl39) ===")
import gc as _gc


class _Nodo(object):
    pass


def _basura(n=300):
    """Ciclos como los de una peticion fallida: el objeto se apunta a si mismo."""
    for _ in range(n):
        a, b = _Nodo(), _Nodo()
        a.otro, b.otro = b, a
        a.pila = [a, b, {"html": "x" * 200}]


_gc.disable()
try:
    _basura()
    antes = A._MEM_WATCH.get("gcs", 0)
    A._mem_recoge()
    comprueba("_mem_recoge() recoge los ciclos (%s objetos) y lo apunta"
              % A._MEM_WATCH.get("gc_obj"),
              A._MEM_WATCH.get("gc_obj", 0) >= 600 and A._MEM_WATCH["gcs"] == antes + 1,
              A._MEM_WATCH)
    viejos2 = {k: getattr(A, k) for k in ("_rss_mb", "_mem_cgroup_mb", "_bnd_revisa")}
    A._rss_mb = lambda: 50.0
    A._mem_cgroup_mb = lambda: 100.0
    A._bnd_revisa = lambda: None
    try:
        A._MEM_T0[0] = time.time()
        A._MEM_GC_ULT[0] = time.time() - A._MEM_GC_CADA - 1
        n0 = A._MEM_WATCH["gcs"]
        _basura(50)
        A._mem_vigila_vuelta()
        comprueba("el vigilante la lanza si hace mas de 5 min", A._MEM_WATCH["gcs"] == n0 + 1)
        A._mem_vigila_vuelta()
        comprueba("...y no en cada vuelta de 8 s", A._MEM_WATCH["gcs"] == n0 + 1)
    finally:
        for k, v in viejos2.items():
            setattr(A, k, v)
finally:
    _gc.enable()

print("\n=== 5) glibc: fuera de Linux no hace nada ni rompe ===")
if not sys.platform.startswith("linux"):
    comprueba("sin libc: _mem_trim() -> False y _mem_malloc() -> None",
              A._mem_trim() is False and A._mem_malloc() is None)
else:
    m = A._mem_malloc()
    comprueba("Linux: mallinfo2 contesta", isinstance(m, dict) and "libre_mb" in m, m)
    comprueba("Linux: dos arenas como mucho", A._MALLOC_ARENAS[0] == 2, A._MALLOC_ARENAS)
    comprueba("Linux: malloc_trim contesta", A._mem_trim() is True)
antes = A._MEM_WATCH.get("podas", 0)
lib = A._mem_poda(grave=False)
comprueba("una poda de verdad sigue funcionando (%.1f MB)" % lib,
          isinstance(lib, float) and A._MEM_WATCH["podas"] == antes + 1)

print("\n=== 6) /catmem ===")
cli = A.app.test_client()
js = cli.get("/catmem").get_json()
comprueba("trae 'malloc' y cuanto le queda a la pausa de las podas",
          "malloc" in js and "poda_pausada_s" in js
          and {"trims", "trim_mb", "pausadas"} <= set(js.get("watch") or {}), sorted(js))
js = cli.get("/catmem?tipos=1").get_json()
t = js.get("tipos") or {}
comprueba("?tipos=1: los tipos de objeto vivos (%d objetos)" % js.get("tipos_total", 0),
          isinstance(t, dict) and "dict" in t and js.get("tipos_total", 0) > 1000,
          list(t)[:5])
comprueba("...y sin ?tipos=1 no se cuentan (cuesta)", "tipos" not in cli.get("/catmem").get_json())
js = cli.get("/catmem").get_json()
comprueba("trae las pasadas del recolector y cuando toca la siguiente",
          isinstance((js.get("gc") or {}).get("pasadas"), list)
          and "gc_proxima_s" in js and {"gcs", "gc_obj", "gc_mb"} <= set(js.get("watch") or {}),
          js.get("gc"))
_gc.disable()
try:
    _basura(100)
    js = cli.get("/catmem?gc=1").get_json()
finally:
    _gc.enable()
comprueba("?gc=1 recoge YA y dice cuanto (%s objetos)" % (js.get("recogida") or {}).get("objetos"),
          (js.get("recogida") or {}).get("objetos", 0) >= 200, js.get("recogida"))
comprueba("...y sin ?gc=1 no recoge nada", "recogida" not in cli.get("/catmem").get_json())

print("\n=== 7) el censo de la memoria viva (dtbl45) ===")
# Una cache "plana" (dict de tuplas de textos): el gc no la sigue, y es justo
# lo que no se veia contando objetos. El censo tiene que ponerle nombre.
A._PRUEBA_CENSO = dict(("k%d" % i, ("x" * 100000 + str(i), 1.0)) for i in range(40))
import gc as _gc
_gc.collect()
t0 = time.time()
js = cli.get("/catmem?censo=1").get_json()
dur = time.time() - t0
duenos = dict((d["dueno"], d["mb"]) for d in js.get("por_dueno", []))
comprueba("?censo=1 contesta en poco (%.1f s) y sin cortar" % dur,
          dur < 30 and js.get("cortado") is False and js.get("objetos_gc", 0) > 1000, js.get("s"))
comprueba("la cache plana sale con SU nombre y su peso (%s MB)" % duenos.get("app._PRUEBA_CENSO"),
          duenos.get("app._PRUEBA_CENSO", 0) >= 3.7, list(duenos.items())[:5])
gr = js.get("grandes") or [{}]
comprueba("los textos grandes dicen de quien son",
          any(g.get("dueno") == "app._PRUEBA_CENSO" and g.get("kb", 0) >= 97 for g in gr), gr[:3])
comprueba("el censo cuenta la red (sesiones, hilos) y nunca claves ni contenidos",
          "red" in js and js.get("hilos", 0) >= 1 and "k0" not in json.dumps(js)
          and "xxxx" not in json.dumps(js), js.get("red"))
comprueba("dos censos a la vez: el segundo no se amontona",
          A._CENSO_LOCK.acquire(False) and A._mem_censo().get("ocupado") is True)
A._CENSO_LOCK.release()
del A._PRUEBA_CENSO
print("   censo: %s s, %s MB vistos; duenos: %s" % (js.get("s"), js.get("mb"),
      ", ".join("%s %s" % (d["dueno"], d["mb"]) for d in js.get("por_dueno", [])[:6])))

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el vigilante no vacia las cachas en bucle y ensena el monton")
