# -*- coding: utf-8 -*-
"""Cinubo (antigua series.ly) en el relay (dtbl85).

Streaming en castellano con la sesión del dueño. Comprueba, sin red:
  1) las cookies: solo las que sirven, con valores sanos; nada de otras;
  2) la copia cifrada: ida y vuelta, ajena o manipulada no vale, no se lee nada
     en claro;
  3) los enlaces de una ficha: SOLO castellano de España, en su orden, con su
     servidor y calidad; nada raro como referencia;
  4) el iframe del servidor y el captcha (se cuenta, no se rompe);
  5) /cb/sesion y /cb/prueba solo con la llave de /uso;
  6) el cupo por hora: no se pasa nunca de él.
"""
import json
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


REM = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d"
BUENAS = {"seriesly_session": "eyJpdiI6IkFBQUFBQUFBQUFBQUFBQUFBQUFBQUE9PSJ9%3D",
          REM: "eyJpdiI6IkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQj09In0%3D"}


def fila(url, srv, q, lang):
    import html
    d = {"url": url, "id": "1", "message": "",
         "info": {"server": srv, "quality": q, "language": lang, "user": "@x"}}
    return '<a href="javascript:void(0)" data-link-playback="%s">' % html.escape(json.dumps(d), quote=True)


os.makedirs("/tmp", exist_ok=True)
FICH = [A._CB_FILE, A._USO_FILE]
copias = {}
for f in FICH:
    if os.path.exists(f):
        copias[f] = f + ".prueba_cb_bak"
        shutil.copy(f, copias[f])
try:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass

    print("\n=== 1) Las cookies ===")
    v = A._cb_valida(dict(BUENAS, cf_clearance="x" * 40, otra="y" * 20,
                          seriesly_session_falsa="z" * 30))
    comprueba("se quedan solo las dos que sirven", set(v) == set(BUENAS), v)
    comprueba("un valor con espacios o comillas no entra",
              A._cb_valida({"seriesly_session": 'abc def"ghijkl'}) == {})
    comprueba("un remember con nombre que no es el de Laravel no entra",
              A._cb_valida({"remember_web_xyz": "a" * 30}) == {})
    comprueba("basura en vez de dict: vacío", A._cb_valida(["x"]) == {} and A._cb_valida(None) == {})

    print("\n=== 2) La copia cifrada ===")
    kc, km = os.urandom(32), os.urandom(32)
    paq = A._cb_empaqueta(BUENAS, kc, km)
    comprueba("ida y vuelta", A._cb_desempaqueta(paq, kc, km) == BUENAS)
    comprueba("no se lee nada en claro",
              all(x not in paq for x in list(BUENAS.values()) + ["seriesly", "remember"]))
    comprueba("con otras claves no se abre", A._cb_desempaqueta(paq, os.urandom(32), km) is None)
    malo = paq[:-6] + ("A" if paq[-6] != "A" else "B") + paq[-5:]
    comprueba("manipulada no vale", A._cb_desempaqueta(malo, kc, km) is None)
    comprueba("otro formato (la de /uso) no vale", A._cb_desempaqueta("u1.xxxx", kc, km) is None)

    print("\n=== 3) Los enlaces de una ficha ===")
    T = "https://cinubo.com/t/"
    html = "<div class='detail-page'>" + "".join([
        fila(T + "eyJpdiI6IlpkQ3RhVlpKWDJtYkpx", "Vidara", "HD 1080p", "Español - España"),
        fila(T + "eyJpdiI6IkxBVElOT0xBVElOT0xB", "VOE", "HD 1080p", "Español - Latino"),
        fila(T + "eyJpdiI6IlNVQlNVQlNVQlNVQlNV", "LULUSTREAM", "HD 720p", "Español - Subtitulado"),
        fila(T + "eyJpdiI6IkxVTFVMVUxVTFVMVUxV", "LULUSTREAM", "HD 720p", "Español - España"),
        fila("https://otra.web/t/eyJpdiI6IkFKRU5PQUpFTk9BSkVOTw", "BYSE", "HD 1080p", "Español - España"),
        fila(T + "eyJ<script>alert(1)</script>xxxxxxxxxx", "BYSE", "HD 1080p", "Español - España"),
        fila(T + "eyJpdiI6IkVOR0VOR0VOR0VOR0VO", "ALTERNATIVO", "HD 720p", "Inglés - Subtitulado"),
    ]) + "</div>"
    en = A._cb_enlaces_de(html)
    comprueba("solo los dos de castellano de España, en su orden",
              [(x["srv"], x["q"]) for x in en] == [("VIDARA", "HD 1080p"), ("LULUSTREAM", "HD 720p")], en)
    comprueba("la referencia es el token de /t/, sin la web delante",
              en and en[0]["r"] == "eyJpdiI6IlpkQ3RhVlpKWDJtYkpx", en[:1])
    comprueba("ni otra web ni una referencia con HTML dentro",
              all("<" not in x["r"] and "otra.web" not in x["r"] for x in en))
    comprueba("logueado: con ficha sí, la portada no",
              A._cb_logueado(html) and not A._cb_logueado("<html>portada</html>") and not A._cb_logueado(None))

    print("\n=== 4) El servidor (iframe) y el captcha ===")
    resp = {}

    def falso_get(path, json_=False, tope=12.0, referer=None):
        return resp.get("txt"), resp.get("st", 200)
    real_get = A._cb_get
    A._cb_get = falso_get
    try:
        resp.update(txt=json.dumps({"e": '<iframe src="https:\\/\\/luluvdo.com\\/e\\/ve3xgnce2zrn" width="640"><\\/iframe>'}))
        e, err = A._cb_embed("eyJxxxxxxxxxxxxxxxxxxxxx")
        comprueba("saca la URL del servidor", e == "https://luluvdo.com/e/ve3xgnce2zrn" and err == "", (e, err))
        antes = A._CB["captcha"]
        resp.update(txt=json.dumps({"captcha_required": True, "site_key": "x"}))
        e, err = A._cb_embed("eyJxxxxxxxxxxxxxxxxxxxxx")
        comprueba("captcha: nada, se dice y se cuenta", e is None and err == "captcha" and A._CB["captcha"] == antes + 1)
        resp.update(txt=None, st=429)
        comprueba("un 429: nada, con su motivo", A._cb_embed("eyJxxxxxxxxxxxxxxxxxxxxx") == (None, "http 429"))
        resp.update(txt="<html>no json</html>", st=200)
        comprueba("algo que no es JSON: nada", A._cb_embed("eyJxxxxxxxxxxxxxxxxxxxxx")[0] is None)
    finally:
        A._cb_get = real_get

    print("\n=== 5) Solo el dueño ===")
    cli = A.app.test_client()
    llave = (cli.post("/uso/alta").get_json() or {}).get("k", "")
    comprueba("(llave reclamada para la prueba)", len(llave) >= 20)
    r = cli.post("/cb/sesion", json={"c": BUENAS})
    comprueba("sin llave, /cb/sesion: 403 y nada guardado",
              r.status_code == 403 and not os.path.exists(A._CB_FILE))
    comprueba("sin llave, /cb/prueba: 403", cli.get("/cb/prueba").status_code == 403)
    r = cli.post("/cb/sesion", json={"c": {"otra": "x" * 30}}, headers={"X-MW-Uso": llave})
    comprueba("con llave pero sin cookies que sirvan: 400", r.status_code == 400)
    r = cli.post("/cb/sesion", json={"c": BUENAS}, headers={"X-MW-Uso": llave})
    comprueba("con llave: guardadas", r.status_code == 200 and r.get_json().get("n") == 2, r.get_json())
    comprueba("en /tmp, y se leen igual", A._cb_lee_tmp() == BUENAS)
    comprueba("la sesión HTTP lleva las dos cookies",
              {c.name for c in A._cb_sesion().cookies} == set(BUENAS))
    comprueba("/catdiag pública no dice nada de Cinubo",
              "seriesly" not in cli.get("/catdiag").get_data(as_text=True))

    print("\n=== 6) El cupo por hora ===")
    A._CB["hora"] = []
    n = sum(1 for _ in range(A._CB_CUPO_H + 20) if A._cb_cupo())
    comprueba("nunca más de %d por hora" % A._CB_CUPO_H, n == A._CB_CUPO_H, n)
    A._CB["hora"] = [A._t.time() - 3700] * A._CB_CUPO_H
    comprueba("pasada la hora, vuelve a haber", A._cb_cupo())
    A._CB["hora"] = [A._t.time()] * A._CB_CUPO_H
    llamadas = []
    real = A._get_con_tope
    A._get_con_tope = lambda *a, **k: (llamadas.append(a) or ("x", 200))
    try:
        comprueba("sin cupo, no se pide nada a Cinubo",
                  A._cb_get("/s/x") == (None, 0) and not llamadas)
    finally:
        A._get_con_tope = real
finally:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
    for f, b in copias.items():
        shutil.move(b, f)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: la sesión de Cinubo, cifrada y solo del dueño; solo castellano de España; con cupo")
