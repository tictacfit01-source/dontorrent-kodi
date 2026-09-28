# -*- coding: utf-8 -*-
"""El avisador de errores de JavaScript de la web (dtbl55).

Un fallo de JavaScript en el movil de otro no se ve desde aqui: la web se
queda a medias y nadie lo cuenta. La web manda el mensaje, la linea y la
version a /jserr; el relay lo guarda AGRUPADO y sin nada de quien, y /catdiag
lo ensena con la linea de app.py donde esta el fallo.

Comprueba el relay (agrupa, topa, no guarda basura, traduce la linea) y, con
Node, el script de la pagina TAL CUAL se sirve (no manda cortes de red, ni
repetidos, ni mas de cinco). Sin red.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
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
copia = None
if os.path.exists(A._JSERR_FILE):
    copia = A._JSERR_FILE + ".prueba_bak"
    shutil.copy(A._JSERR_FILE, copia)
tmp = tempfile.mkdtemp(prefix="mw_jserr_")
try:
    try:
        os.remove(A._JSERR_FILE)
    except Exception:
        pass
    cli = A.app.test_client()
    ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36"
    IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"

    def manda(cuerpo, ua=ANDROID, ip="10.1.1.1"):
        r = cli.post("/jserr", data=cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo),
                     headers={"User-Agent": ua, "X-Forwarded-For": ip, "Content-Type": "text/plain"})
        return r.status_code

    print("\n=== 1) El relay los recoge agrupados ===")
    pl = A._CAT_PAGE.split("\n")
    ln = next(i for i, x in enumerate(pl, 1) if x.startswith("var $=function(s)"))
    err = {"m": "TypeError: Cannot read properties of undefined (reading 'eps')", "l": ln, "c": 5, "b": A.BUILD}
    comprueba("contesta 204 (sendBeacon no espera nada)", manda(err) == 204)
    manda(err, ua=IOS, ip="10.1.1.2")
    r = A._jserr_resumen()
    comprueba("el mismo error dos veces es UNA entrada con n=2", r["total"] == 2 and len(r["lista"]) == 1
              and r["lista"][0]["n"] == 2, r)
    e = r["lista"][0]
    comprueba("dice en que navegadores", e["ua"] == {"android/chrome": 1, "ios/safari": 1}, e["ua"])
    comprueba("y de que version", e["build"] == A.BUILD, e["build"])
    src = open(A.__file__, encoding="utf-8").read().split("\n")
    comprueba("la linea de la pagina se traduce a la de app.py",
              e["app_py"] and src[e["app_py"] - 1].startswith("var $=function(s)"), e["app_py"])
    guardado = open(A._JSERR_FILE, encoding="utf-8").read()
    comprueba("no se guarda NADA de quien (ni IP)", "10.1.1." not in guardado)
    for malo in ("no es json", "{}", json.dumps({"m": ""}), json.dumps({"m": "x", "l": "abc"})):
        manda(malo)
    comprueba("la basura no se guarda", A._jserr_resumen()["total"] == 2, A._jserr_resumen())
    for i in range(25):
        manda({"m": "spam %d" % i, "l": 1}, ip="10.9.9.9")
    comprueba("una IP no puede llenarlo (20 cada 10 min)",
              sum(1 for x in json.load(open(A._JSERR_FILE)).values() if x["m"].startswith("spam")) == 20)
    for i in range(60):
        manda({"m": "otro %d" % i, "l": 1}, ip="10.8.%d.1" % i)
    comprueba("y el fichero no pasa de %d errores distintos" % A._JSERR_MAX,
              len(json.load(open(A._JSERR_FILE))) <= A._JSERR_MAX)
    d = cli.get("/catdiag").get_json() or {}
    comprueba("/catdiag lo ensena", (d.get("jserr") or {}).get("total", 0) > 0, list(d)[:30])

    print("\n=== 2) La pagina: la version dentro y el avisador lo primero ===")
    comprueba("la version va dentro de la pagina", "__MW_BUILD__" not in A._CAT_PAGE
              and ("B='%s'" % A.BUILD) in A._CAT_PAGE)
    i = A._CAT_PAGE.index("<script>\n// AVISADOR DE ERRORES")
    comprueba("el avisador es lo primero del script (antes de que nada pueda fallar)",
              A._CAT_PAGE.index("var $=function(s)") > i)

    print("\n=== 3) El avisador, con Node ===")
    node = shutil.which("node")
    if not node:
        comprueba("hay node", False)
    else:
        m = re.search(r"<script>\n(// AVISADOR DE ERRORES.*?\}\)\(\);)\n", A._CAT_PAGE, re.S)
        js = m.group(1) if m else ""
        prog = r"""
var H={},ENV=[];
var window={addEventListener:function(t,f){H[t]=f}};
var navigator={sendBeacon:function(u,b){ENV.push(JSON.parse(b));return true}};
function fetch(){ENV.push('fetch');return {catch:function(){}}}
""" + js + r"""
function err(m,l){H.error({message:m,lineno:l,colno:1})}
err('TypeError: x is undefined',10);
err('TypeError: x is undefined',10);
err('Script error.',0);
err('TypeError: Failed to fetch',3);
H.unhandledrejection({reason:{name:'TypeError',message:'Load failed'}});
H.unhandledrejection({reason:{name:'AbortError',message:'The user aborted a request.'}});
H.unhandledrejection({reason:{name:'TypeError',message:"Cannot read properties of null (reading 'classList')"}});
for(var i=0;i<10;i++)err('ReferenceError: foo'+i+' is not defined',20+i);
console.log(JSON.stringify(ENV));
"""
        f = os.path.join(tmp, "a.js")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(prog)
        out = subprocess.run([node, f], capture_output=True, text=True, timeout=30)
        try:
            env = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            env = None
        comprueba("el script corre", isinstance(env, list), out.stderr[-500:])
        env = env or []
        ms = [x.get("m") for x in env if isinstance(x, dict)]
        comprueba("un error de verdad se manda, con su linea y version",
                  env and env[0] == {"m": "TypeError: x is undefined", "l": 10, "c": 1, "b": A.BUILD}, env[:1])
        comprueba("repetido, una vez", ms.count("TypeError: x is undefined") == 1, ms)
        comprueba("'Script error.' y los cortes de red no se mandan",
                  not any(("Script error" in x or "fetch" in x or "Load failed" in x or "aborted" in x)
                          for x in ms), ms)
        comprueba("un fallo dentro de una promesa si",
                  "TypeError: Cannot read properties of null (reading 'classList')" in ms, ms)
        comprueba("cinco por carga como mucho", len(env) == 5, len(env))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        if copia:
            shutil.move(copia, A._JSERR_FILE)
        elif os.path.exists(A._JSERR_FILE):
            os.remove(A._JSERR_FILE)
    except Exception:
        pass

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: los errores de la web en los moviles de la gente llegan, agrupados y sin nada de quien")
