# -*- coding: utf-8 -*-
"""Cinubo autónomo (dtbl119 + caja 2.9.96). Sin red. Comprueba:
  1) cada cuenta tiene su salud; si la principal pierde la sesión, TODO
     (fichas, capítulos, /t/) pasa solo a la de reserva, y vuelve si se cura;
  2) qué es "sin sesión", qué es "caída" y qué es "no lo sé" (una redirección,
     un 403 de Cloudflare a una caja, una caja que no contesta);
  3) su Telegram: se leen los mensajes y las direcciones que anuncian, sin
     confundir sus enlaces de siempre (Discord, imágenes, noticias);
  4) una dirección nueva: primero SIN sesión (¿es Cinubo?); la sesión nunca va
     a una que no lo parezca; si entra, se adopta y las cajas van allí (solo
     las 2.9.96+); sobrevive a un despliegue (copia cifrada);
  5) el vigía: cuentas, caída + mudanza anunciada -> se adopta sola;
  6) los avisos de /uso (y que /uso/datos los lleva);
  7) la caja 2.9.96: va a la dirección que le dicen y dice a dónde llegó.
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
PRIN = {"seriesly_session": "eyJpdiI6IkFBQUFBQUFBQUFBQUFBQUFBQUFBQUE9PSJ9%3D",
        REM: "eyJpdiI6IkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQj09In0%3D"}
RESE = {REM: "eyJpdiI6IkNVRU5UQTJDVUVOVEEyQ1VFTlRBMj09In0%3D"}


def pagina(usuario=None, dom="cinubo.com", enlaces=True):
    """Una página de capítulo de Cinubo, con o sin sesión."""
    import html
    cab = '<html><head><title>Ted Lasso 4x02 - Cinubo</title><meta name="csrf-token" content="%s"></head><body>' % ("a" * 40)
    per = '<a href="https://%s/@%s">yo</a>' % (dom, usuario) if usuario else '<a href="/login">Entrar</a>'
    d = {"url": "https://%s/t/eyJpdiI6IlRFU1RURVNUVEVTVFRFU1QifQ" % dom, "id": "1", "message": "",
         "info": {"server": "VIDARA", "quality": "HD 1080p", "language": "España", "user": "@x"}}
    en = '<a data-link-playback="%s">x</a>' % html.escape(json.dumps(d), quote=True) if enlaces else ""
    return cab + per + en + "</body></html>"


def tg_html(posts):
    out = ['<html><body>']
    for pid, fecha, texto in posts:
        out.append('<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" '
                   'data-post="cinubo/%d"><div class="tgme_widget_message_text js-message_text" dir="auto">%s</div>'
                   '<a class="tgme_widget_message_date" href="https://t.me/cinubo/%d"><time datetime="%s" class="time">'
                   '</time></a></div></div>' % (pid, texto, pid, fecha))
    return "".join(out) + "</body></html>"


os.makedirs("/tmp", exist_ok=True)


def quita(f):
    try:
        os.remove(f)
    except Exception:
        pass


FICH = [A._CB_FILE, A._CB_CACHE_FILE, A._CB_RITMO_FILE, A._CB_SALUD_FILE, A._CB_DOM_FILE,
        A._CB_TG_FILE, A._VIGIA_FILE, A._USO_FILE]
copias = {}
for f in FICH:
    if os.path.exists(f):
        copias[f] = f + ".prueba_cba_bak"
        shutil.copy(f, copias[f])
reales = (A._cb_cajas, A._kb_enqueue, A._catjob_wait, A._lend_acquire, A._lend_release,
          A._get_con_tope, A._kbstatus_load, A._cb_vivo)
try:
    for f in FICH:
        quita(f)
    A._cb_guarda_tmp(PRIN, [], [RESE])
    A._CB.update(c=None, mt=None)
    comprueba("dos cuentas dadas de alta", len(A._cb_cuentas()) == 2)
    cid_p, cid_r = A._cb_cid(PRIN), A._cb_cid(RESE)

    # La red, de mentira: una caja que contesta lo que diga `web`
    cola = []
    web = {}          # (dom, cuenta) -> (st, texto, host_final)
    A._cb_cajas = lambda: ["111111"]
    A._kbstatus_load = lambda: {"111111": {"v": "2.9.96"}, "222222": {"v": "2.9.95"}}
    A._kb_enqueue = lambda box, ev: cola.append((box, ev))
    A._lend_acquire = lambda box: object()
    A._lend_release = lambda sem: None
    A._cb_vivo = lambda u: True

    def quien(ck):
        return "p" if ck == PRIN else "r" if ck == RESE else "-" if not ck else "?"

    def espera(job, secs):
        box, ev = cola[-1]
        dom = ev.get("dom") or "cinubo.com"
        r = web.get((dom, quien(ev["ck"])), web.get((dom, "*")))
        if r is None:
            return None
        return {"html": json.dumps({"st": r[0], "t": r[1], "h": r[2] if len(r) > 2 else dom})}
    A._catjob_wait = espera

    print("\n=== 1) La salud de cada cuenta y la que manda ===")
    comprueba("sin saber nada, manda la principal", A._cb_activa() == PRIN)
    A._cb_salud_pon(cid_p, "sin_sesion")
    comprueba("la principal sin sesión -> manda la de reserva", A._cb_activa() == RESE)
    comprueba("y el /t/ solo rota entre las sanas", A._cb_vivas() == [RESE])
    A._cb_salud_pon(cid_r, "sin_sesion")
    comprueba("las dos sin sesión: se sigue intentando con todas (nunca cero caminos)",
              A._cb_vivas() == [PRIN, RESE] and A._cb_activa() == PRIN)
    A._cb_salud_pon(cid_p, "ok", "mejorwolf")
    comprueba("la principal se cura -> vuelve a mandar", A._cb_activa() == PRIN)
    s = A._cb_salud_lee()
    comprueba("se guarda el usuario de Cinubo (para el aviso), nunca la cookie",
              s[cid_p]["u"] == "mejorwolf" and PRIN[REM] not in json.dumps(s), s)
    d0 = s[cid_p]["desde"]
    A._cb_salud_pon(cid_p, "ok", "mejorwolf")
    comprueba("el 'desde' no se mueve mientras siga igual", A._cb_salud_lee()[cid_p]["desde"] == d0)
    quita(A._CB_SALUD_FILE)

    print("\n=== 2) Qué es cada cosa ===")
    J = A._cb_juzga_pagina
    comprueba("con su perfil: ok", J(pagina("mejorwolf"), 200, {"h": "cinubo.com"}) == "ok")
    comprueba("Cinubo sin el perfil: sin sesión", J(pagina(None), 200, {"h": "cinubo.com"}) == "sin_sesion")
    comprueba("www.cinubo.com cuenta como la misma", J(pagina(None), 200, {"h": "www.cinubo.com"}) == "sin_sesion")
    comprueba("nos llevó a otra dirección: no se sabe (no es culpa de la cuenta)",
              J(pagina(None, "cinubo.net"), 200, {"h": "cinubo.net"}) is None)
    comprueba("403 de Cloudflare a la caja: no se sabe", J("cf", 403, {}) is None)
    comprueba("la caja no contestó: no se sabe", J(None, 0, {}) is None)
    comprueba("su web no responde (st 0 desde la caja): caída", J("", 0, {"resp": True}) == "caida")
    comprueba("un 503 (lo que deja _cb_pide: sin texto): caída", J(None, 503, {"resp": True}) == "caida")
    comprueba("un 200 que no es Cinubo (aviso del operador): caída",
              J("<html>Esta web ha sido bloqueada por orden judicial</html>", 200, {}) == "caida")
    comprueba("caja vieja (sin 'h'): se juzga igual", J(pagina("mejorwolf"), 200, {}) == "ok")

    print("\n=== 3) Fichas y capítulos con la principal caducada ===")
    A._CB["hora"] = []
    web[("cinubo.com", "p")] = (200, pagina(None))
    web[("cinubo.com", "r")] = (200, pagina("mejor874181"))
    en = A._cb_enlaces("/s/nzp5nh/4x2")
    comprueba("la persona ve sus servidores igual (la reserva los trae)",
              bool(en) and en[0]["srv"] == "VIDARA", en)
    comprueba("se probó la principal y luego la de reserva",
              [quien(ev["ck"]) for b, ev in cola[-2:]] == ["p", "r"], [quien(ev["ck"]) for b, ev in cola])
    s = A._cb_salud_lee()
    comprueba("la principal queda apuntada sin sesión; la reserva, ok con su usuario",
              s[cid_p]["e"] == "sin_sesion" and s[cid_r]["e"] == "ok" and s[cid_r]["u"] == "mejor874181", s)
    n0 = len(cola)
    A._cb_enlaces("/s/otro99/1x1")
    comprueba("lo siguiente va directo por la reserva, sin gastar en la caducada",
              [quien(ev["ck"]) for b, ev in cola[n0:]] == ["r"], [quien(ev["ck"]) for b, ev in cola[n0:]])
    web[("cinubo.com", "r")] = (200, pagina(None))
    n0 = len(cola)
    comprueba("las dos caducadas: None (la web lo dice) y sin bucle", A._cb_enlaces("/s/tres33/1x1") is None
              and len(cola) - n0 <= 2, len(cola) - n0)
    web[("cinubo.com", "p")] = (503, "caido")
    quita(A._CB_SALUD_FILE)
    n0 = len(cola)
    comprueba("Cinubo caído no se confunde con sesión: no se gasta la reserva",
              A._cb_enlaces("/s/cuatro4/1x1") is None and len(cola) - n0 == 1
              and (A._cb_salud_lee().get(cid_p) or {}).get("e") != "sin_sesion", (len(cola) - n0, A._cb_salud_lee()))
    quita(A._CB_SALUD_FILE)
    quita(A._CB_CACHE_FILE)

    print("\n=== 4) Su Telegram ===")
    ahora = A._t.time()
    import datetime as _dt
    iso = lambda ts: _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    posts = A._cb_tg_parsea(tg_html([
        (17, iso(ahora - 40 * 86400), 'Changelog: nuevo buscador. <a href="https://discord.gg/abc">Discord</a> '
                                       'y foto <a href="https://cdn4.telesco.pe/file/x.jpg">x</a>'),
        (18, iso(ahora - 20 * 86400), 'Leed <a href="https://elchapuzasinformatico.com/2026/09/kiev/">la noticia</a>'
                                       ' y entrad en cinubo.com como siempre'),
        (19, iso(ahora - 3600), '&#9888; Nuevo dominio: <a href="https://cinubo.net/">cinubo.net</a><br/>'
                                'Las sesiones se mantienen.'),
    ]))
    comprueba("lee los tres mensajes, con su número y su fecha",
              [p["id"] for p in posts] == [17, 18, 19] and abs(posts[2]["ts"] - (ahora - 3600)) < 5, posts)
    comprueba("el texto, sin etiquetas", "Nuevo dominio: cinubo.net" in posts[2]["txt"] and "<" not in posts[2]["txt"],
              posts[2]["txt"])
    comprueba("Discord, imágenes, noticias y la dirección de siempre NO son candidatas",
              posts[0]["dominios"] == [] and posts[1]["dominios"] == [], (posts[0]["dominios"], posts[1]["dominios"]))
    comprueba("la anunciada sí: cinubo.net", posts[2]["dominios"] == ["cinubo.net"], posts[2]["dominios"])
    comprueba("'cinubox.com' no se tira por acabar en x.com", not A._cb_no_dom("cinubox.com")
              and A._cb_no_dom("x.com") and A._cb_no_dom("cdn4.telesco.pe"))

    def red_tg(url, tope, headers=None, **kw):
        if "t.me" in url:
            return red_tg.html, red_tg.st
        return None, 0
    red_tg.html, red_tg.st = tg_html([(19, iso(ahora - 3600), "Nuevo dominio: cinubo.net")]), 200
    A._get_con_tope = red_tg
    tg = A._cb_tg_mira()
    comprueba("se guarda lo último y las anunciadas", tg["posts"][-1]["id"] == 19
              and "cinubo.net" in tg["anunciados"], tg)
    red_tg.html, red_tg.st = None, 429
    tg = A._cb_tg_mira()
    comprueba("si no se puede leer, se dice desde cuándo y NO se olvida lo leído",
              tg.get("err") == "http 429" and tg.get("mal_desde") and tg["posts"][-1]["id"] == 19, tg)
    red_tg.html, red_tg.st = tg_html([(19, iso(ahora - 3600), "Nuevo dominio: cinubo.net")]), 200
    comprueba("y al volver, el error se quita", "err" not in A._cb_tg_mira())

    print("\n=== 5) Una dirección nueva ===")
    A._CB["hora"] = []
    quita(A._CB_SALUD_FILE)
    web.clear()
    web[("trampa.xyz", "-")] = (200, "<html>Compra este dominio</html>")
    n0 = len(cola)
    comprueba("una que no parece Cinubo: 'no'", A._cb_dom_prueba("trampa.xyz", "telegram") == "no")
    comprueba("...y la sesión NUNCA viajó allí (solo la prueba sin cookies)",
              [ev["ck"] for b, ev in cola[n0:]] == [{}], [ev["ck"] for b, ev in cola[n0:]])
    comprueba("ni se cambia de dirección", A._cb_dom() == "cinubo.com")
    comprueba("a una web conocida (Discord...) ni se le pregunta",
              A._cb_dom_prueba("discord.gg", "telegram") == "no" and len(cola) == n0 + 1)
    web[("cinubo.net", "-")] = (200, pagina(None, "cinubo.net"))
    web[("cinubo.net", "p")] = (200, pagina(None, "cinubo.net"))
    comprueba("Cinubo, pero las cuentas no entran: 'sin_sesion' y queda pendiente",
              A._cb_dom_prueba("cinubo.net", "telegram") == "sin_sesion" and A._cb_cand()["d"] == "cinubo.net"
              and A._cb_dom() == "cinubo.com", A._cb_cand())
    comprueba("las pruebas en la nueva solo van a cajas 2.9.96+",
              all(b == "111111" for b, ev in cola[n0:]))
    A._cb_cajas = lambda: ["222222", "111111"]
    n0 = len(cola)
    web[("cinubo.net", "p")] = (200, pagina("mejorwolf", "cinubo.net"))
    comprueba("la sesión sí entra: se adopta", A._cb_dom_prueba("cinubo.net", "telegram") == "adoptada"
              and A._cb_dom() == "cinubo.net" and not A._cb_cand(), (A._cb_dom(), A._cb_cand()))
    comprueba("la caja vieja (2.9.95) ni se usó para probarla", all(b == "111111" for b, ev in cola[n0:]))
    A._cb_cajas = reales[0]
    A._live_boxes_real = A._live_boxes
    A._live_boxes = lambda: ["222222", "111111"]
    A._cajas_libres_real = A._cajas_libres
    A._cajas_libres = lambda: ["222222", "111111"]
    comprueba("mudado, solo las cajas que saben ir a la nueva", A._cb_cajas() == ["111111"], A._cb_cajas())
    A._live_boxes, A._cajas_libres = A._live_boxes_real, A._cajas_libres_real
    A._cb_cajas = lambda: ["111111"]
    A._CB["hora"] = []
    web[("cinubo.net", "*")] = (200, pagina("mejorwolf", "cinubo.net"))
    n0 = len(cola)
    A._cb_pide(path="/s/abc123/1x1")
    comprueba("y los trabajos llevan la dirección nueva", cola[-1][1].get("dom") == "cinubo.net", cola[-1][1])
    comprueba("los enlaces de la dirección nueva valen", len(A._cb_enlaces_de(pagina("x1", "cinubo.net"))) == 1)
    comprueba("...y los de otra web siguen sin valer", A._cb_enlaces_de(pagina("x1", "otra.web")) == [])
    kc, km = b"k" * 32, b"m" * 32
    gz = A._cb_empaqueta(PRIN, kc, km, [], [RESE])
    comprueba("la dirección va en la copia cifrada (sobrevive al despliegue)",
              A._cb_desempaqueta_casa(gz, kc, km, "dom") == "cinubo.net" and "cinubo.net" not in gz)
    quita(A._CB_DOM_FILE)
    A._CB_DOM_CACHE.update(mt=None, d=None)
    A._CB["dom_nube"] = "cinubo.net"
    comprueba("tras un despliegue (/tmp vacío) se usa la de la copia", A._cb_dom() == "cinubo.net")
    A._CB["dom_nube"] = ""
    A._CB_DOM_CACHE.update(mt=None, d=None)
    comprueba("sin nada, la de siempre", A._cb_dom() == "cinubo.com")
    A._CB["dom_nube"] = "no vale/../x"
    A._CB_DOM_CACHE.update(mt=None, d=None)
    comprueba("una basura en la copia no se usa", A._cb_dom() == "cinubo.com")
    A._CB["dom_nube"] = ""
    A._CB_DOM_CACHE.update(mt=None, d=None)

    print("\n=== 5b) Cinubo nos redirige ===")
    lanzados = []
    real_thr = A._thr.Thread

    class HiloFalso:
        def __init__(self, target=None, args=(), daemon=None):
            self.t, self.a = target, args

        def start(self):
            lanzados.append(self.a)
    A._thr.Thread = HiloFalso
    try:
        web[("cinubo.com", "p")] = (200, pagina(None, "cinubo.org"), "cinubo.org")
        A._CB_MUD["t"] = 0
        A._cb_pide(path="/s/abc123/1x1")
        comprueba("la caja llegó a otra dirección: se prueba (en segundo plano)",
                  lanzados == [("cinubo.org", "redireccion")], lanzados)
        A._cb_pide(path="/s/abc123/1x1")
        comprueba("como mucho una vez cada 30 min", len(lanzados) == 1, lanzados)
        quita(A._CB_SALUD_FILE)
        A._cb_enlaces("/s/redir1/1x1")
        comprueba("y por esa redirección la cuenta NO se da por caducada",
                  (A._cb_salud_lee().get(cid_p) or {}).get("e") != "sin_sesion", A._cb_salud_lee())
    finally:
        A._thr.Thread = real_thr

    print("\n=== 6) El vigía ===")
    web.clear()
    quita(A._CB_SALUD_FILE)
    quita(A._CB_CACHE_FILE)
    A._CB["hora"] = []
    web[("cinubo.com", "p")] = (200, pagina("mejorwolf"))
    web[("cinubo.com", "r")] = (200, pagina(None))
    j = A._cb_vigia()
    comprueba("mira las DOS cuentas: la reserva caducada sale como problema",
              j["n"] == 1 and any("1 de 2" in p for p in j["problemas"]), j)
    av = A._cb_avisos()
    comprueba("aviso amarillo para el dueño: qué cuenta y que las teles siguen",
              any(a["n"] == "amarillo" and "reserva" in a["t"] and "siguen" in a["t"] for a in av), av)
    web[("cinubo.com", "r")] = (200, pagina("mejor874181"))
    j = A._cb_vigia()
    comprueba("las dos bien: sin problemas", j["problemas"] == [] and j["n"] == 2, j)
    comprueba("...y el aviso se va solo", not any("perdió" in a["t"] for a in A._cb_avisos()))
    A._cb_cajas = lambda: []
    comprueba("sin ninguna caja con la que mirar: no se sabe (None, no un '-')", A._cb_vigia() is None)
    A._cb_cajas = lambda: ["111111"]
    # caída + mudanza anunciada en su Telegram
    web.clear()
    quita(A._CB_SALUD_FILE)
    web[("cinubo.com", "*")] = (0, "")
    web[("cinubo.net", "*")] = (200, pagina("mejorwolf", "cinubo.net"))
    web[("cinubo.net", "-")] = (200, pagina(None, "cinubo.net"))
    j = A._cb_vigia()
    comprueba("cae la de siempre + Telegram anuncia cinubo.net -> se adopta SOLA",
              A._cb_dom() == "cinubo.net" and j["problemas"] == [], (A._cb_dom(), j))
    av = A._cb_avisos()
    comprueba("y el dueño lo sabe (aviso de mudanza)", any("mudado a cinubo.net" in a["t"] for a in av), av)
    comprueba("el mensaje de Telegram sale arriba, con enlace",
              any(a.get("u") == "https://t.me/cinubo/19" and a["n"] == "amarillo" for a in av), av)
    # si después cae la nueva y la de siempre vuelve
    web[("cinubo.net", "*")] = (0, "")
    web[("cinubo.com", "*")] = (200, pagina("mejorwolf"))
    web[("cinubo.com", "-")] = (200, pagina(None))
    A._CB["hora"] = []
    A._cb_vigia()
    comprueba("si la nueva cae y la de siempre vuelve, se vuelve a la de siempre", A._cb_dom() == "cinubo.com",
              A._cb_dom())
    # el vigía de las fuentes lo incluye
    A._get_con_tope = red_tg
    real_vb = A._vigia_busca
    A._vigia_busca = lambda src, excluir=(): (None, "sin caja", None)
    try:
        out = A._vigia_ronda()
        comprueba("el vigía de las fuentes lleva a Cinubo ('cb') con su historia",
                  "cb" in out and out["cb"].get("historia", "").endswith("+"), out.get("cb"))
    finally:
        A._vigia_busca = real_vb

    print("\n=== 7) /uso/datos y /catdiag llevan lo de Cinubo (solo con la llave) ===")
    cli = A.app.test_client()
    llave = (cli.post("/uso/alta").get_json() or {}).get("k", "")
    if llave:
        web[("cinubo.com", "r")] = (200, pagina(None))
        A._cb_salud_pon(cid_r, "sin_sesion")
        d = cli.get("/uso/datos", headers={"X-MW-Uso": llave}).get_json()
        comprueba("/uso/datos trae 'avisos'", isinstance(d.get("avisos"), list) and d["avisos"], d.get("avisos"))
        comprueba("sin la llave, nada", cli.get("/uso/datos").status_code in (401, 503))
        c = cli.get("/catdiag?k=" + llave).get_json()
        sal = ((c.get("cinubo") or {}).get("salud") or {})
        comprueba("/catdiag con llave: salud por cuenta, sin cookies",
                  len(sal.get("cuentas") or []) == 2 and PRIN[REM] not in json.dumps(c), sal)
        pg = cli.get("/uso").get_data(as_text=True)
        comprueba("la página sabe pintar los avisos", "avisosHTML(d.avisos)" in pg and "mwav-rojo" in pg)
    else:
        print("  (sin llave de /uso en esta prueba: se salta el 7)")

    print("\n=== 8) La caja 2.9.96 ===")
    sys.path.insert(0, os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf"))
    from resources.lib import cinubo as C
    pedidas = []

    class Resp:
        def __init__(self, url, st=200, text="ok"):
            self.url, self.status_code, self.text = url, st, text

    class Ses:
        def __init__(self):
            self.headers, self.jar = {}, []
            Ses.ult = self

        class _C:
            def __init__(s, o):
                s.o = o

            def set(s, k, v, domain=None, path=None):
                s.o.jar.append((k, domain))

        @property
        def cookies(self):
            return Ses._C(self)

        def get(self, url, **kw):
            pedidas.append(url)
            return Resp(url.replace("cinubo.com", "cinubo.org") if "redir" in url else url)

        def close(self):
            pass
    real_ses = C.requests.Session
    C.requests.Session = Ses
    try:
        r = json.loads(C.pedir({"path": "/s/abc123/1x1", "ck": PRIN}))
        comprueba("sin 'dom': la de siempre, y dice a dónde llegó",
                  pedidas[-1] == "https://cinubo.com/s/abc123/1x1" and r["h"] == "cinubo.com", (pedidas, r))
        comprueba("las cookies, para cinubo.com", all(d == ".cinubo.com" for k, d in Ses.ult.jar))
        r = json.loads(C.pedir({"path": "/s/abc123/1x1", "ck": PRIN, "dom": "cinubo.net"}))
        comprueba("con 'dom': va allí y las cookies son de allí",
                  pedidas[-1] == "https://cinubo.net/s/abc123/1x1" and all(d == ".cinubo.net" for k, d in Ses.ult.jar),
                  (pedidas[-1], Ses.ult.jar))
        C.pedir({"path": "/s/abc123/1x1", "ck": PRIN, "dom": "evil.com/x?"})
        comprueba("un 'dom' raro no vale: la de siempre", pedidas[-1].startswith("https://cinubo.com/"))
        C._RUTA_ORIG = C._RUTA
        r = json.loads(C.pedir({"path": "/s/redir1/1x1", "ck": PRIN}))
        comprueba("si Cinubo redirige, la caja lo cuenta ('h')", r["h"] == "cinubo.org", r)
    finally:
        C.requests.Session = real_ses
finally:
    (A._cb_cajas, A._kb_enqueue, A._catjob_wait, A._lend_acquire, A._lend_release,
     A._get_con_tope, A._kbstatus_load, A._cb_vivo) = reales
    for f in FICH:
        quita(f)
    for f, b in copias.items():
        shutil.move(b, f)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: Cinubo se cuida solo: cuentas, dirección y Telegram")
