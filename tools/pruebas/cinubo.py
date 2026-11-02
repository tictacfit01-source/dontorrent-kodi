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
FICH = [A._CB_FILE, A._USO_FILE, A._CB_CACHE_FILE]
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
    html = '<a href="https://cinubo.com/@israel126592">yo</a><div>' + "".join([
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
    comprueba("logueado: con el enlace al perfil sí (aunque no sea 'detail-page'); la portada no",
              A._cb_logueado(html) and "detail-page" not in html
              and not A._cb_logueado('<html>portada <form id="logout-form"></form></html>')
              and not A._cb_logueado(None))

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
    comprueba("y en memoria, las dos", A._cb_cookies() == BUENAS)
    OTRA = {REM: "eyJpdiI6IkNVRU5UQU5VRVZBQ1VFTlRBTlVFVkE9PSJ9%3D"}
    A._t.sleep(0.05)
    A._cb_guarda_tmp(OTRA)                    # lo que haría /cb/sesion en el OTRO worker
    os.utime(A._CB_FILE, (A._t.time() + 5, A._t.time() + 5))
    comprueba("cambiada en el otro worker: este la relee (cuenta nueva, no la vieja)",
              A._cb_cookies() == OTRA, A._cb_cookies())
    A._cb_guarda_tmp(BUENAS)
    os.utime(A._CB_FILE, (A._t.time() + 10, A._t.time() + 10))
    comprueba("...y vuelta", A._cb_cookies() == BUENAS)
    comprueba("/catdiag pública no dice nada de Cinubo",
              "seriesly" not in cli.get("/catdiag").get_data(as_text=True))

    print("\n=== 6) El cupo por hora ===")
    A._CB["hora"] = []
    n = sum(1 for _ in range(A._CB_CUPO_H + 20) if A._cb_cupo())
    comprueba("nunca más de %d por hora" % A._CB_CUPO_H, n == A._CB_CUPO_H, n)
    A._CB["hora"] = [A._t.time() - 3700] * A._CB_CUPO_H
    comprueba("pasada la hora, vuelve a haber", A._cb_cupo())

    print("\n=== 7) Por una caja (Render recibe 403 de Cinubo) ===")
    cola, respuestas = [], {}
    reales = (A._cb_cajas, A._kb_enqueue, A._catjob_wait, A._lend_acquire, A._lend_release)
    A._cb_cajas = lambda: ["111111"]
    A._kb_enqueue = lambda box, ev: cola.append((box, ev))
    A._lend_acquire = lambda box: object()
    A._lend_release = lambda sem: None

    def espera(job, secs):
        ev = cola[-1][1]
        clave = ("q:%s:%s" % (ev["q"], ev.get("pg"))) if ev.get("q") else ev.get("path")
        r = respuestas.get(clave)
        return None if r is None else {"html": json.dumps({"st": r[0], "t": r[1]})}
    A._catjob_wait = espera
    try:
        A._CB["hora"] = []
        respuestas["/s/abc123/4x2"] = (200, html)
        txt, st = A._cb_pide(path="/s/abc123/4x2")
        comprueba("la caja trae la página", st == 200 and "@israel126592" in txt)
        box, ev = cola[-1]
        comprueba("el trabajo es cbget, con la sesión y la ruta",
                  ev["op"] == "cbget" and ev["ck"] == BUENAS and ev["path"] == "/s/abc123/4x2", ev)
        comprueba("una caja que no contesta: nada", A._cb_pide(path="/s/zzzzzz") == (None, 0))
        A._CB["hora"] = [A._t.time()] * A._CB_CUPO_H
        n0 = len(cola)
        comprueba("sin cupo, ni se encola", A._cb_pide(path="/s/abc123") == (None, 0) and len(cola) == n0)
        A._CB["hora"] = []
        A._cb_cajas = lambda: []
        comprueba("sin cajas al día: nada, y se dice", A._cb_pide(path="/s/abc123") == (None, 0)
                  and A._CB["ult_err"] == "sin caja")
        A._cb_cajas = lambda: ["111111"]

        print("\n=== 8) La ficha por tmdb_id, los enlaces y los endpoints ===")
        try:
            os.remove(A._CB_CACHE_FILE)
        except Exception:
            pass
        posts = [{"tmdb_id": 97546, "type": "movie", "link": "/p/zzzz11"},
                 {"tmdb_id": 11111, "type": "serie", "link": "/s/yyyy22"},
                 {"tmdb_id": 97546, "type": "serie", "link": "/s/nzp5nh"}]
        respuestas["q:ted lasso:1"] = (200, json.dumps({"posts": posts, "pagination": {"has_more": False}}))
        comprueba("serie: la del mismo tmdb_id Y tipo serie (no la peli con el mismo número)",
                  A._cb_ficha(97546, "serie", "ted lasso") == "/s/nzp5nh")
        comprueba("película: la de tipo movie", A._cb_ficha(97546, "movie", "ted lasso") == "/p/zzzz11")
        n0 = len(cola)
        comprueba("la segunda vez, de la caché (sin caja)",
                  A._cb_ficha(97546, "serie", "ted lasso") == "/s/nzp5nh" and len(cola) == n0)
        respuestas["q:nada:1"] = (200, json.dumps({"posts": [], "pagination": {}}))
        comprueba("si no lo tiene: vacío (y se recuerda)", A._cb_ficha(5, "movie", "nada") == ""
                  and A._cb_cache_get("f", "movie:5", A._CB_TTL_NOESTA) == "")
        respuestas["/s/nzp5nh/4x2"] = (200, html)
        r = cli.get("/cbenlaces?id=97546&kind=serie&t=ted%20lasso&s=4&e=2").get_json()
        comprueba("/cbenlaces: los de castellano de España del capítulo",
                  r.get("ok") and [(x["srv"], x["q"]) for x in r["items"]]
                  == [("VIDARA", "HD 1080p"), ("LULUSTREAM", "HD 720p")], r)
        comprueba("/cbenlaces de una serie sin temporada ni capítulo: 400",
                  cli.get("/cbenlaces?id=97546&kind=serie&t=ted").status_code == 400)
        respuestas["/s/nzp5nh/4x3"] = (200, "<html>portada</html>")
        r = cli.get("/cbenlaces?id=97546&kind=serie&t=ted%20lasso&s=4&e=3").get_json()
        comprueba("sesión caducada: se dice, no 'no hay enlaces'",
                  r.get("ok") is False and r.get("motivo") == "sesion caducada", r)
        ref = "eyJpdiI6IlpkQ3RhVlpKWDJtYkpx"
        respuestas["/t/" + ref] = (200, json.dumps(
            {"e": '<iframe src="https:\\/\\/vidara.to\\/e\\/jkbr5gxmPV0CZ"><\\/iframe>'}))
        r = cli.get("/cbembed?r=" + ref).get_json()
        comprueba("/cbembed: el servidor de vídeo", r.get("u") == "https://vidara.to/e/jkbr5gxmPV0CZ", r)
        n0 = len(cola)
        comprueba("y la segunda vez, de la caché",
                  cli.get("/cbembed?r=" + ref).get_json().get("u") and len(cola) == n0)
        comprueba("/cbembed con una referencia rara: 400", cli.get("/cbembed?r=<script>").status_code == 400)

        orden = A._cb_ordena([{"srv": "VOE", "q": "HD 1080p"}, {"srv": "LULUSTREAM", "q": "HD 720p"},
                               {"srv": "VIDARA", "q": "HD 720p"}, {"srv": "VIDARA", "q": "HD 1080p"},
                               {"srv": "POWVIDEO", "q": "HD 720p"}])
        comprueba("orden: lo que la tele abre sola primero (Vidara, Lulustream); a igualdad, 1080p",
                  [(x["srv"], x["q"]) for x in orden] == [("VIDARA", "HD 1080p"), ("VIDARA", "HD 720p"),
                                                         ("LULUSTREAM", "HD 720p"), ("VOE", "HD 1080p"),
                                                         ("POWVIDEO", "HD 720p")], orden)
        pub = cli.get("/catdiag").get_json()
        adm = cli.get("/catdiag?k=" + llave).get_json()
        comprueba("/catdiag: Cinubo solo con la llave, y sin cookies dentro",
                  "cinubo" not in pub and adm.get("cinubo", {}).get("sesion") is True
                  and all(v not in json.dumps(adm) for v in BUENAS.values()), adm.get("cinubo"))

        print("\n=== 9) A la tele: la orden st ===")
        enviados = []
        A._kb_enqueue = lambda box, ev: enviados.append((box, ev))

        def manda(u):
            return cli.post("/kb/send", json={"code": "123456", "cmd": "play_ref", "a": "st",
                                              "u": u, "t": "Ted Lasso 4x02"})
        r = manda("https://vidara.to/e/jkbr5gxmPV0CZ")
        comprueba("una página https de servidor: se encola para esa tele",
                  r.status_code == 200 and enviados and enviados[-1][0] == "123456"
                  and enviados[-1][1].get("a") == "st", (r.get_json(), enviados[-1:]))
        for malo in ("http://vidara.to/e/x", "javascript:alert(1)", "https://x.y/" + "a" * 500,
                     'https://a.b/"><script>'):
            comprueba("rechaza " + malo[:30], manda(malo).status_code == 400)
    finally:
        A._cb_cajas, A._kb_enqueue, A._catjob_wait, A._lend_acquire, A._lend_release = reales
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
