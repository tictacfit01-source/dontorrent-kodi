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


def quita(f):
    try:
        os.remove(f)
    except Exception:
        pass
FICH = [A._CB_FILE, A._USO_FILE, A._CB_CACHE_FILE, A._CB_RITMO_FILE]
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
    paq2 = A._cb_empaqueta(BUENAS, kc, km, ["123456", "654321", "x"])
    comprueba("con las teles de casa: las cookies igual y las teles, sin basura",
              A._cb_desempaqueta(paq2, kc, km) == BUENAS
              and A._cb_desempaqueta_casa(paq2, kc, km) == ["123456", "654321"])
    comprueba("los códigos de las teles tampoco se leen en claro", "123456" not in paq2 and "654321" not in paq2)
    import base64 as _b64
    vieja = "c1." + _b64.b64encode(A._uso_cifra(json.dumps(BUENAS).encode("utf-8"), kc, km)).decode("ascii")
    comprueba("la copia de antes (solo cookies) se sigue leyendo",
              A._cb_desempaqueta(vieja, kc, km) == BUENAS and A._cb_desempaqueta_casa(vieja, kc, km) == [])

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

    def falso_get(path="", json_=False, tope=12.0, referer=None, **kw):
        return resp.get("txt"), resp.get("st", 200)
    real_get = A._cb_pide
    A._cb_pide = falso_get
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
        A._cb_pide = real_get

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
    real_vivo = A._cb_vivo
    A._cb_vivo = lambda u: "muerto" not in u          # sin red: lo dice la URL
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

        print("\n=== 7a) Una caja a la que Cloudflare dice 403: la siguiente ===")
        A._CB_MALAS.clear()
        A._cb_cajas = lambda: ["700001", "700002"]
        real_espera = A._catjob_wait

        def espera2(job, secs):
            box, ev = cola[-1]
            if box == "700001":
                return {"html": json.dumps({"st": 403, "t": "cf"})}
            return {"html": json.dumps({"st": 200, "t": html})}
        A._catjob_wait = espera2
        A._CB["hora"] = []
        txt, st = A._cb_pide(path="/s/abc123/4x2")
        comprueba("la 1ª da 403 -> la 2ª la trae", st == 200 and cola[-1][0] == "700002", (st, cola[-1][0]))
        n0 = len(cola)
        A._cb_pide(path="/s/abc123/4x2")
        comprueba("y la 1ª queda apartada: ya no se le pide",
                  len(cola) == n0 + 1 and cola[-1][0] == "700002", [c[0] for c in cola[n0:]])
        A._CB_MALAS["700001"] = A._t.time() - A._CB_MALAS_S - 1
        A._cb_pide(path="/s/abc123/4x2")
        comprueba("pasadas 6 h se vuelve a probar", cola[n0 + 1][0] == "700001", [c[0] for c in cola[n0:]])
        A._catjob_wait = real_espera
        A._CB_MALAS.clear()
        A._cb_cajas = lambda: ["111111"]

        print("\n=== 7b) Primero las teles de casa del dueño ===")
        rv = (A._live_boxes, A._kbstatus_load, A._cajas_libres)
        try:
            A._live_boxes = lambda *a, **k: ["900001", "900002", "900003", "900004"]
            A._kbstatus_load = lambda: {c: {"v": "2.9.91"} for c in ("900001", "900002", "900003", "900004")}
            A._cajas_libres = lambda: ["900001", "900004"]
            A._cb_cajas = reales[0]
            r = cli.post("/cb/sesion", json={"c": BUENAS, "casa": ["900003", "900004", "malo"]},
                         headers={"X-MW-Uso": llave})
            comprueba("/cb/sesion guarda las teles de casa (solo códigos válidos)",
                      r.status_code == 200 and r.get_json().get("casa") == 2, r.get_json())
            comprueba("orden: casa libre, casa ocupada, otra libre, otra ocupada",
                      A._cb_cajas() == ["900004", "900003", "900001", "900002"], A._cb_cajas())
            r = cli.post("/cb/sesion", json={"c": BUENAS}, headers={"X-MW-Uso": llave})
            comprueba("renovar la sesión sin decir 'casa' no las borra", A._cb_casa() == ["900003", "900004"])
            adm = cli.get("/catdiag?k=" + llave).get_data(as_text=True)
            comprueba("el diagnóstico dice cuántas, nunca los códigos",
                      "900003" not in adm and "900004" not in adm and '"casa": 2' in adm.replace('"casa":2', '"casa": 2'), adm[:0])
        finally:
            A._live_boxes, A._kbstatus_load, A._cajas_libres = rv
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
        nombres_real = A._cb_nombres_tmdb
        A._cb_nombres_tmdb = lambda tmdb, kind: []
        comprueba("si no lo tiene: vacío (y se recuerda)", A._cb_ficha(5, "movie", "nada") == ""
                  and A._cb_cache_get("f", "no2:movie:5", A._CB_TTL_NOESTA) == "")
        # (dtbl144) Los Simpson: la tarjeta "Los Simpsons" de DonTorrent no la encuentra
        # en Cinubo; no puede dejar apuntado "no la tiene" para la serie entera.
        simp = {"posts": [{"tmdb_id": 456, "type": "serie", "link": "/s/simp01"}], "pagination": {}}
        respuestas["q:Los Simpsons:1"] = (200, json.dumps({"posts": [{"tmdb_id": 9, "type": "movie", "link": "/p/otra01"}], "pagination": {}}))
        respuestas["q:Los Simpson:1"] = (200, json.dumps(simp))
        respuestas["q:The Simpsons:1"] = (200, json.dumps(simp))
        A._cb_nombres_tmdb = lambda tmdb, kind: ["Los Simpson", "The Simpsons"]
        comprueba("con el título torcido de una fuente, la encuentra por el nombre de TMDB",
                  A._cb_ficha(456, "serie", "Los Simpsons") == "/s/simp01")
        A._cb_nombres_tmdb = lambda tmdb, kind: None
        comprueba("si TMDB no contesta: 'no lo sé' (None), y NO se apunta como que no está",
                  A._cb_ficha(457, "serie", "Los Simpsons") is None
                  and A._cb_cache_get("f", "no2:tv:457", A._CB_TTL_NOESTA) is None)
        A._cb_cache_pon("f", "tv:458", "")
        respuestas["q:Los Simpson 458:1"] = (200, json.dumps({"posts": [{"tmdb_id": 458, "type": "serie", "link": "/s/simp02"}], "pagination": {}}))
        comprueba("un 'no la tiene' apuntado antes de dtbl144 ya no vale",
                  A._cb_ficha(458, "serie", "Los Simpson 458") == "/s/simp02")
        A._cb_nombres_tmdb = nombres_real
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

        print("\n=== 8d) El ritmo: nunca se llega al límite de Cinubo ===")
        try:
            quita(A._CB_RITMO_FILE)
        except Exception:
            pass
        oks = 0
        for k in range(A._CB_RITMO_N):
            rk = "eyJpdiI6IlJJVE1PUklUTU9SSVRN" + str(k)
            respuestas["/t/" + rk] = (200, json.dumps({"e": '<iframe src="https://vidara.to/e/abc%d"></iframe>' % k}))
            oks += bool(cli.get("/cbembed?r=" + rk).get_json().get("u"))
        r = cli.get("/cbembed?r=eyJpdiI6IlJJVE1PUklUTU9SSVRNXX").get_json()
        comprueba("6 seguidos van; el 7º espera (con los segundos), sin pedirlo a Cinubo",
                  oks == A._CB_RITMO_N and r.get("error") == "espera" and 0 < r.get("s", 0) <= 61, (oks, r))
        comprueba("lo que ya está en caché no cuenta ni espera",
                  cli.get("/cbembed?r=eyJpdiI6IlJJVE1PUklUTU9SSVRN0").get_json().get("u"))
        quita(A._CB_RITMO_FILE)
        rc = "eyJpdiI6IkNBUFRDSEFDQVBUQ0hB"
        respuestas["/t/" + rc] = (200, json.dumps({"captcha_required": True}))
        r = cli.get("/cbembed?r=" + rc).get_json()
        comprueba("si aun así salta el captcha (y no hay otra cuenta): espera, como mucho 90 s (no 'error')",
                  r.get("error") == "espera" and r.get("s") == 90, r)
        r = cli.get("/cbembed?r=eyJpdiI6IlJJVE1PUklUTU9SSVRNYY").get_json()
        comprueba("...y mientras dura la calma, nadie más le pide a Cinubo", r.get("error") == "espera" and r.get("s", 0) > 55, r)
        quita(A._CB_RITMO_FILE)

        orden = A._cb_ordena([{"srv": "VOE", "q": "HD 1080p"}, {"srv": "LULUSTREAM", "q": "HD 720p"},
                               {"srv": "VIDARA", "q": "HD 720p"}, {"srv": "VIDARA", "q": "HD 1080p"},
                               {"srv": "POWVIDEO", "q": "HD 720p"}])
        comprueba("orden: lo que la tele abre sola primero; la mejor calidad; a igualdad, Lulustream (dtbl121)",
                  [(x["srv"], x["q"]) for x in orden] == [("VIDARA", "HD 1080p"), ("LULUSTREAM", "HD 720p"),
                                                         ("VIDARA", "HD 720p"), ("VOE", "HD 1080p"),
                                                         ("POWVIDEO", "HD 720p")], orden)
        pub = cli.get("/catdiag").get_json()
        adm = cli.get("/catdiag?k=" + llave).get_json()
        comprueba("/catdiag: Cinubo solo con la llave, y sin cookies dentro",
                  "cinubo" not in pub and adm.get("cinubo", {}).get("sesion") is True
                  and all(v not in json.dumps(adm) for v in BUENAS.values()), adm.get("cinubo"))

        print("\n=== 8b) La búsqueda de Cinubo ===")
        t = A._cb_tarjeta({"tmdb_id": 136315, "type": "serie", "title": "The Bear", "item_date": "2022-06-23",
                           "link": "/s/prqffv", "poster": "https://image.tmdb.org/t/p/w300/x.jpg", "vote_average": 8.123})
        comprueba("serie -> tarjeta de streaming con su tmdb_id",
                  t and t["kind"] == "serie" and t["source"] == "cb" and t["tmdb_id"] == 136315
                  and t["year"] == "2022" and t["rating"] == 8.1, t)
        comprueba("anime cuenta como serie; peli como movie",
                  A._cb_tarjeta({"tmdb_id": 1, "type": "anime", "link": "/a/abcd12"})["kind"] == "serie"
                  and A._cb_tarjeta({"tmdb_id": 1, "type": "movie", "link": "/p/abcd12"})["kind"] == "movie")
        comprueba("sin tmdb_id, tipo raro o enlace raro: fuera",
                  A._cb_tarjeta({"tmdb_id": 0, "type": "movie", "link": "/p/abcd12"}) is None
                  and A._cb_tarjeta({"tmdb_id": 3, "type": "persona", "link": "/p/abcd12"}) is None
                  and A._cb_tarjeta({"tmdb_id": 3, "type": "movie", "link": "javascript:x"}) is None)
        comprueba("un cartel que no es de TMDB no se usa",
                  A._cb_tarjeta({"tmdb_id": 3, "type": "movie", "link": "/p/abcd12", "poster": "http://evil/x.jpg"})["poster"] == "")
        respuestas["q:the bear:1"] = (200, json.dumps({"posts": [
            {"tmdb_id": 136315, "type": "serie", "title": "The Bear", "link": "/s/prqffv"},
            {"tmdb_id": 9, "type": "persona", "title": "Jeremy", "link": "/p/zz"}]}))
        r = cli.get("/cbsearch?q=The%20Bear").get_json()
        comprueba("/cbsearch: las tarjetas válidas", r.get("ok") and [x["title"] for x in r["items"]] == ["The Bear"], r)
        n0 = len(cola)
        r = cli.get("/cbsearch?q=the  BEAR!").get_json()
        comprueba("la misma búsqueda escrita distinta sale de la caché (sin caja)",
                  r.get("items") and len(cola) == n0, (r, len(cola) - n0))
        comprueba("/cbsearch sin texto: 400", cli.get("/cbsearch?q=a").status_code == 400)
        src_web = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
        comprueba("en la web, Cinubo nunca gana a un torrent (ni a WolfMax con casilla)",
                  "if((x.source||'dt')==='cb')return -5000;" in src_web
                  and "return q*10-1000;" in src_web)

        print("\n=== 8c) El Inicio con lo de Cinubo ===")
        portada = ('<html><script>\n    window.homeInitialData = ' + json.dumps({
            "movie": {"posts": [{"tmdb_id": 1283515, "type": "movie", "title": "Verity", "link": "/p/abcd12"}]},
            "serie": {"posts": [{"tmdb_id": 259909, "type": "serie", "title": "Dexter: Resurrección", "link": "/s/abcd13"}]},
            "anime": {"posts": []}}) + ';\n</script></html>')
        respuestas["/"] = (200, portada)
        r = cli.get("/cbinicio?kind=estrenos").get_json()
        comprueba("Estrenos: las pelis de su portada", [x["title"] for x in r.get("items", [])] == ["Verity"], r)
        n0 = len(cola)
        r = cli.get("/cbinicio?kind=series").get_json()
        comprueba("Series: las series, de la caché (sin otra caja)",
                  [x["kind"] for x in r.get("items", [])] == ["serie"] and len(cola) == n0, r)
        comprueba("otra pestaña: 400", cli.get("/cbinicio?kind=x").status_code == 400)

        print("\n=== 8d2) Castigo: esperas que crecen, y vuelven a cero ===")
        quita(A._CB_RITMO_FILE)
        cid = "prueba"
        A._cb_ritmo(captcha=True, cid=cid)
        e1 = A._cb_ritmo(cid=cid)
        d = json.load(open(A._CB_RITMO_FILE))
        d["a"][cid]["calma"] = A._t.time() - 1                  # pasó la 1ª espera
        json.dump(d, open(A._CB_RITMO_FILE, "w"))
        comprueba("pasada la 1ª espera, se puede probar una vez", A._cb_ritmo(cid=cid) == 0)
        A._cb_ritmo(captcha=True, cid=cid)                      # ...y sigue castigada
        e2 = A._cb_ritmo(cid=cid)
        comprueba("1er captcha: 2 min; si al volver sigue castigada: 5 min (no se insiste)",
                  110 <= e1 <= 120 and 290 <= e2 <= 300, (e1, e2))
        A._cb_ritmo(captcha="ok", cid=cid)
        d = json.load(open(A._CB_RITMO_FILE))
        comprueba("al primer acierto, el castigo se olvida", d["a"][cid]["nivel"] == 0)
        quita(A._CB_RITMO_FILE)
        A._cb_ritmo(cid="h")
        comprueba("dos peticiones a la misma cuenta, a menos de 3 s: la 2ª espera",
                  0 < A._cb_ritmo(cid="h") <= 3)
        quita(A._CB_RITMO_FILE)

        print("\n=== 8e) Varias cuentas y enlaces muertos ===")
        quita(A._CB_RITMO_FILE)
        OTRA_C = {REM: "eyJpdiI6IkNVRU5UQTJDVUVOVEEyQ1VFTlRBMj09In0%3D"}
        r = cli.post("/cb/sesion", json={"c": BUENAS, "otras": [OTRA_C, {"basura": "x"}]}, headers={"X-MW-Uso": llave})
        comprueba("/cb/sesion guarda las cuentas de reserva (solo las válidas)", r.get_json().get("cuentas") == 2, r.get_json())
        usadas = []

        def espera_cuentas(job, secs):
            box, ev = cola[-1]
            usadas.append(ev["ck"].get(REM, ""))
            if ev["ck"] == BUENAS:
                return {"html": json.dumps({"st": 200, "t": json.dumps({"captcha_required": True})})}
            return {"html": json.dumps({"st": 200, "t": json.dumps({"e": '<iframe src="https://vidara.to/e/OTRA1"></iframe>'})})}
        A._catjob_wait = espera_cuentas
        A._CB["turno"] = len(A._cb_cuentas()) - 1        # que empiece por la principal
        r = cli.get("/cbembed?r=eyJpdiI6IkNVRU5UQVNDVUVOVEFT").get_json()
        comprueba("la principal pide captcha -> la de reserva lo saca, sin esperar",
                  r.get("u") == "https://vidara.to/e/OTRA1" and usadas == [BUENAS[REM], OTRA_C[REM]], (r, usadas))
        usadas.clear()
        r = cli.get("/cbembed?r=eyJpdiI6IkNVRU5UQVNDVUVOVEFUMg").get_json()
        comprueba("la castigada descansa: ya ni se le pregunta", r.get("u") and usadas == [OTRA_C[REM]], usadas)

        def espera_muerto(job, secs):
            return {"html": json.dumps({"st": 200, "t": json.dumps({"e": '<iframe src="https://firestream.site/e/muerto"></iframe>'})})}
        A._catjob_wait = espera_muerto
        rm = "eyJpdiI6Ik1VRVJUT01VRVJUT01V"
        r = cli.get("/cbembed?r=" + rm).get_json()
        comprueba("un enlace muerto (Video Not Found): 'muerto', no a la tele", r.get("error") == "muerto", r)
        n0 = len(cola)
        r = cli.get("/cbembed?r=" + rm).get_json()
        comprueba("...y se recuerda: ni se vuelve a preguntar", r.get("error") == "muerto" and len(cola) == n0, r)
        A._catjob_wait = espera
        cli.post("/cb/sesion", json={"c": BUENAS, "otras": []}, headers={"X-MW-Uso": llave})
        quita(A._CB_RITMO_FILE)

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
        A._cb_vivo = real_vivo
    # (dtbl145) El reproductor del móvil avisa de un enlace borrado: el relay no
    # se fía, pregunta al worker y solo si lo ve borrado lo aparta para todos.
    print("\n=== aviso de enlace borrado (/cbembed/muerto) ===")
    real_w = A._cb_vivo_worker
    try:
        cli = A.app.test_client()
        REF = "eyJpdiI6Ik1VRVJUT01VRVJUT01VRVJUTyJ9"
        A._cb_cache_pon("i", REF, "https://luluvid.com/e/borrado1")
        # (dtbl147) se cree al móvil: desde Render el mismo enlace se ve vivo
        # (los servidores borran por países), así que el relay no lo pregunta
        A._cb_vivo_worker = lambda u, tope=5.0: True
        A._CB_AVISOS.clear()
        r = cli.post("/cbembed/muerto?r=" + REF).get_json()
        comprueba("el aviso del móvil aparta el enlace para todos",
                  r.get("muerto") is True and A._cb_cache_get("i", REF, A._CB_TTL_EMBED) == "", r)
        comprueba("y queda apuntado para /catdiag", (A._CB.get("ult_muerto") or [0, ""])[1].startswith("https://luluvid"))
        A._CB_AVISOS.clear()
        cods = []
        for n in range(7):
            A._cb_cache_pon("i", REF + "T%d" % n, "https://luluvid.com/e/t%d" % n)
            cods.append(cli.post("/cbembed/muerto?r=%sT%d" % (REF, n)).status_code)
        comprueba("tope: 6 avisos por conexión cada 10 min, el 7º se rechaza",
                  cods == [200] * 6 + [429] and A._cb_cache_get("i", REF + "T6", A._CB_TTL_EMBED), cods)
        A._CB_AVISOS.clear()
        e = cli.get("/cbembed?r=" + REF).get_json()
        comprueba("la siguiente persona recibe 'muerto' sin gastar Cinubo", e.get("error") == "muerto", e)
        comprueba("referencia rara: 400", cli.post("/cbembed/muerto?r=<x>").status_code == 400)
        # (dtbl146) lo ya comprobado borrado no vuelve a salir como botón
        reales2 = A._cb_ficha, A._cb_enlaces
        try:
            A._cb_ficha = lambda tmdb, kind, titulo: "/s/simp"
            A._cb_enlaces = lambda ruta: [{"srv": "LULUSTREAM", "q": "SD HDTV", "r": REF},
                                          {"srv": "VIDARA", "q": "HD 720p", "r": REF + "VIVO"}]
            r = cli.get("/cbenlaces?id=456&kind=serie&t=Los%20Simpson&s=7&e=3").get_json()
            comprueba("/cbenlaces quita el borrado y lo cuenta",
                      [x["srv"] for x in r.get("items", [])] == ["VIDARA"] and r.get("muertos") == 1, r)
            A._cb_enlaces = lambda ruta: [{"srv": "LULUSTREAM", "q": "SD HDTV", "r": REF}]
            r = cli.get("/cbenlaces?id=456&kind=serie&t=Los%20Simpson&s=7&e=3").get_json()
            comprueba("todos borrados: lista vacía con 'muertos' (no 'sin castellano')",
                      r.get("items") == [] and r.get("muertos") == 1 and "otros" not in r, r)
        finally:
            A._cb_ficha, A._cb_enlaces = reales2
        # _cb_vivo: Render no puede ver la página -> decide el worker
        real_get = A._get_con_tope
        try:
            A._get_con_tope = lambda *a, **k: ("<html>reto</html>", 403)
            A._cb_vivo_worker = lambda u, tope=5.0: False
            comprueba("Render ve un 403 y el worker lo ve borrado: muerto", A._cb_vivo("https://luluvid.com/e/x") is False)
            A._cb_vivo_worker = lambda u, tope=5.0: True
            comprueba("Render ve un 403 y el worker no sabe/vivo: vivo", A._cb_vivo("https://luluvid.com/e/x") is True)
            A._get_con_tope = lambda *a, **k: ("File is no longer available as it expired or has been deleted.", 200)
            comprueba("Render ve la página de borrado: muerto", A._cb_vivo("https://luluvid.com/e/x") is False)
        finally:
            A._get_con_tope = real_get
    finally:
        A._cb_vivo_worker = real_w
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
