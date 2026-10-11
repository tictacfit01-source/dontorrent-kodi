# -*- coding: utf-8 -*-
"""Android "Compartir -> MejorWolf", lado del servidor (dtbl78). Sin red.

  1) el manifest declara el share_target (POST multipart, archivos .torrent/.zip);
  2) /sw.js sale con el manejador de /compartir y su cache nueva (v18), y al
     activarse NO borra lo compartido que aun no se ha recogido;
  3) POST /compartir (si el service worker no estaba) vuelve a la app y NO
     guarda nada en el servidor;
  4) el MISMO tope de tamaño en el service worker, la pagina y /wfsube, y
     /wfsube lo aplica antes de leer el formulario;
  5) la huella sha256 es OBLIGATORIA y el servidor la calcula sobre lo que
     recibe: sin ella o distinta, 400 y nada guardado;
  6) /wfsube dice su CERTEZA: exacta (titulo + tamaño), titulo, sin_ficha;
  7) el diagnostico: solo de una casa nuestra (el codigo no se guarda ni se
     devuelve), tamaño acotado, campos validados, caduca a los 3 dias y
     /catdiag solo enseña cuentas;
  8) lo de la fase 1 sigue igual: las escrituras en mw-sync van firmadas.
"""
import hashlib
import io
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "render_relay"))
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
    return b"d" + b"".join(benc(k) + benc(x[k]) for k in sorted(x)) + b"e"


def sha(b):
    return hashlib.sha256(b).hexdigest()


cli = A.app.test_client()
_post = cli.post


def sube(data):
    """/wfsube como lo manda la web: con la sha256 buena, salvo que la prueba
    ponga otra (o ninguna, con sha=None)."""
    data = dict(data)
    if "sha" not in data and "f" in data:
        data["sha"] = sha(data["f"][0].getvalue())
    if data.get("sha") is None:
        data.pop("sha", None)
    return _post("/wfsube", data=data, content_type="multipart/form-data")


TMP = tempfile.mkdtemp(prefix="mw_compartir_")
viejos = {k: getattr(A, k) for k in ("_WFIH_FILE", "_CAJAS_VISTAS_FILE", "_WF_VERIF_FILE", "_COMPARTIR_DIAG_FILE")}
try:
    A._WFIH_FILE = os.path.join(TMP, "wfih.json")
    A._CAJAS_VISTAS_FILE = os.path.join(TMP, "cajas.json")
    A._WF_VERIF_FILE = os.path.join(TMP, "verif.json")
    A._COMPARTIR_DIAG_FILE = os.path.join(TMP, "diag.json")
    A._WFIH_MEM.update(mt=-1.0, d=None)
    A._caja_vista("111111")

    print("\n=== 1) El manifest ===")
    m = json.loads(cli.get("/manifest.webmanifest").get_data(as_text=True))
    st = m.get("share_target") or {}
    fs = ((st.get("params") or {}).get("files") or [{}])[0]
    comprueba("share_target: POST multipart a /compartir, archivo en el campo 'f'",
              st.get("action") == "/compartir" and st.get("method") == "POST"
              and st.get("enctype") == "multipart/form-data" and fs.get("name") == "f", st)
    comprueba("...para .torrent y .zip", all(x in fs.get("accept", []) for x in
                                            (".torrent", "application/x-bittorrent", ".zip")), fs)
    comprueba("el manifest sigue siendo de la app de siempre",
              m.get("start_url") == "/" and m.get("display") == "standalone")

    print("\n=== 2) El service worker que se sirve ===")
    sw = cli.get("/sw.js").get_data(as_text=True)
    comprueba("cache nueva (v18): los moviles cogen el manifest nuevo", "mw-shell-v18" in sw and "mw-shell-v17" not in sw)
    comprueba("recoge el POST a /compartir y abre la app con ?compartido",
              "'/compartir'" in sw and "compartido=" in sw and "req.formData()" in sw)
    comprueba("al activarse no borra lo compartido pendiente", "k!=='mw-compartido'" in sw)

    print("\n=== 3) POST /compartir sin service worker: vuelve a la app y no guarda nada ===")
    antes = os.path.exists(A._WFIH_FILE)
    r = _post("/compartir", data={"f": (io.BytesIO(b"d4:infod6:lengthi1eee"), "x.torrent")},
              content_type="multipart/form-data")
    comprueba("303 a /?compartido=sinsw", r.status_code == 303 and r.headers.get("Location") == "/?compartido=sinsw",
              (r.status_code, r.headers.get("Location")))
    comprueba("...y nada guardado en el servidor", os.path.exists(A._WFIH_FILE) == antes)

    print("\n=== 4) El mismo tope de tamaño en todas partes ===")
    MAX = A._COMPARTIR_MAX
    pagina = cli.get("/").get_data(as_text=True)
    comprueba("15 MB en /wfsube, en el service worker y en la pagina (un solo numero)",
              A._WFSUBE_MAX == MAX == 15 * 1024 * 1024 and ("f.size>%d" % MAX) in sw
              and ("WFMAXB=%d" % MAX) in pagina, (A._WFSUBE_MAX, MAX))
    comprueba("...sin marcadores sin sustituir", "__MW_MAXB__" not in sw and "__MW_MAXB__" not in pagina)
    grande = b"x" * (MAX + 70000)
    r = _post("/wfsube", data={"f": (io.BytesIO(grande), "video.mp4")}, content_type="multipart/form-data")
    comprueba("/wfsube rechaza lo enorme por su tamaño ANTES de leer el formulario (413 aun sin codigo)",
              r.status_code == 413, r.status_code)

    print("\n=== 5) La huella sha256, obligatoria y comprobada por el servidor ===")
    def tor(nombre, largo):
        info = {"name": nombre, "length": largo, "piece length": 1, "pieces": b"\x00" * 20}
        return benc({"announce": "x", "info": info})
    GIB = 1024 ** 3
    U = "https://wolfmax4k.com/pelicula/r2xz6p"
    d = tor("Insidious Fuera del mas alla [4k 2160p][Esp]", int(18.88 * GIB))
    ficha = {"code": "111111", "url": U, "t": "Insidious: Fuera del más allá", "q": "4K", "sz": "18,88 GB"}
    r = sube(dict(ficha, f=(io.BytesIO(d), "a.torrent"), sha=None))
    comprueba("sin huella: 400 y NADA guardado", r.status_code == 400 and "huella" in r.get_json().get("error", "")
              and not A._wfih_de(U), r.get_json())
    r = sube(dict(ficha, f=(io.BytesIO(d), "a.torrent"), sha=sha(d[:-1] + b"X")))
    comprueba("huella de OTROS bytes (alterado por el camino): 400 y NADA guardado",
              r.status_code == 400 and "alterado" in r.get_json().get("error", "") and not A._wfih_de(U), r.get_json())
    r = sube(dict(ficha, f=(io.BytesIO(d), "a.torrent"), sha="zz" * 32))
    comprueba("huella con forma rara: 400", r.status_code == 400, r.status_code)
    comprueba("...y no se ha escrito nada en el almacen de huellas", not os.path.exists(A._WFIH_FILE))

    print("\n=== 6) La certeza de /wfsube ===")
    j = sube(dict(ficha, f=(io.BytesIO(d), "a.torrent"), sha=sha(d).upper())).get_json()
    comprueba("titulo + tamaño exacto (huella buena): 'exacta' (la web lo manda sin preguntar)",
              j.get("ok") and j.get("certeza") == "exacta" and A._wfih_de(U), j)
    j = sube({"code": "111111", "url": "https://wolfmax4k.com/pelicula/r2rh86", "t": "Tu, yo y la Toscana", "q": "4K",
              "f": (io.BytesIO(tor("Tu yo y la Toscana [4k]", 5 * GIB)), "b.torrent")}).get_json()
    comprueba("solo el titulo (sin tamaño): 'titulo' (la web pregunta)", j.get("ok") and j.get("certeza") == "titulo", j)
    j = sube({"code": "111111", "f": (io.BytesIO(tor("Suelto [1080p]", 3 * GIB)), "c.torrent")}).get_json()
    comprueba("sin ficha: 'sin_ficha' (la web pregunta)", j.get("ok") and j.get("certeza") == "sin_ficha", j)
    r = sube(dict(ficha, f=(io.BytesIO(tor("Spider-Man [4k]", 26 * GIB)), "d.torrent")))
    comprueba("otro archivo para esa ficha: 409, sin certeza que valga", r.status_code == 409, r.get_json())
    comprueba("sin codigo de tele: 401 (como en la fase 1)",
              sube({"f": (io.BytesIO(d), "a.torrent")}).status_code == 401)

    print("\n=== 7) El diagnostico de cada recepcion ===")
    BUENO = {"via": "compartir", "chrome": "153", "sw_chrome": "153", "q": "0", "sw": True,
             "sw_recibido": False, "sw_bytes": 0, "campos": ["title", "text"], "resultado": "sw_sin_archivo"}
    def diag(b, **kw):
        return _post("/compartir/diag", json=b, **kw)
    comprueba("sin codigo de tele: 401, nada guardado", diag(BUENO).status_code == 401
              and not os.path.exists(A._COMPARTIR_DIAG_FILE))
    comprueba("con un codigo que MejorWolf no conoce: 403, nada guardado",
              diag(dict(BUENO, code="999999")).status_code == 403 and not os.path.exists(A._COMPARTIR_DIAG_FILE))
    r = _post("/compartir/diag", data=json.dumps(dict(BUENO, code="111111", relleno="x" * 3000)),
              content_type="application/json")
    comprueba("demasiado grande: 413 sin leerlo", r.status_code == 413, r.status_code)
    malos = [dict(BUENO, via="otra"), dict(BUENO, resultado="inventado"), dict(BUENO, sw_bytes="345"),
             dict(BUENO, sw_bytes=-1), dict(BUENO, integro="si"), dict(BUENO, chrome="153.0.1"),
             dict(BUENO, q="<x>"), dict(BUENO, campos="f")]
    comprueba("campos que no son lo que deben (via, resultado, tipos, versiones, q, campos): 400 cada uno",
              all(diag(dict(x, code="111111")).status_code == 400 for x in malos)
              and not os.path.exists(A._COMPARTIR_DIAG_FILE))
    r = diag(dict(BUENO, code="111111", t="Insidious", archivo="ZDg6YW5ub3VuY2U=", ua="Mozilla/5.0 x" * 20,
                  campos=["title", "text", "<script>"]))
    comprueba("uno bueno: 200 y la respuesta NO devuelve el codigo", r.status_code == 200
              and r.get_json() == {"ok": True}, r.get_json())
    disco = json.load(open(A._COMPARTIR_DIAG_FILE, encoding="utf-8"))
    txt = json.dumps(disco)
    comprueba("se guarda lo que vio el movil (Chrome 153 entrego el formulario SIN archivo)",
              disco[-1].get("chrome") == "153" and disco[-1].get("sw_recibido") is False
              and disco[-1].get("resultado") == "sw_sin_archivo", disco[-1])
    comprueba("...y NADA mas: ni codigo de tele, ni titulo, ni archivo, ni el navegador entero, ni IP",
              "111111" not in txt and "Insidious" not in txt and "ZDg6" not in txt and "Mozilla" not in txt
              and "127.0.0.1" not in txt and disco[-1].get("campos") == ["title", "text", "otro"], disco[-1])
    cd = cli.get("/catdiag").get_json().get("compartir") or []
    comprueba("/catdiag (publico) solo da CUENTAS por navegador y resultado, no los registros",
              cd and cd[0].get("n") == 1 and cd[0].get("chrome") == "153" and cd[0].get("resultado") == "sw_sin_archivo"
              and not any(k in cd[0] for k in ("ts", "sw_bytes", "pag_bytes", "q", "code")), cd)
    # caducidad: uno de hace 4 dias desaparece de /catdiag y del disco
    viejo = dict(disco[-1], ts=int(time.time()) - 4 * 86400, resultado="recogido")
    json.dump([viejo] + disco, open(A._COMPARTIR_DIAG_FILE, "w", encoding="utf-8"))
    cd = cli.get("/catdiag").get_json().get("compartir") or []
    comprueba("caducan solos a los 3 dias: el viejo ya no cuenta en /catdiag",
              not any(g.get("resultado") == "recogido" for g in cd), cd)
    diag({"code": "111111", "via": "selector", "resultado": "elegido", "pag_bytes": 1})
    disco = json.load(open(A._COMPARTIR_DIAG_FILE, encoding="utf-8"))
    comprueba("...y al escribir el siguiente se borra del disco", all(x.get("resultado") != "recogido" for x in disco), disco)
    for i in range(35):
        diag({"code": "111111", "via": "selector", "resultado": "elegido", "pag_bytes": i})
    comprueba("y como mucho 30 en disco", len(json.load(open(A._COMPARTIR_DIAG_FILE, encoding="utf-8"))) == 30)

    print("\n=== 8) Las escrituras protegidas siguen firmadas ===")
    src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
    comprueba("_post_con_tope sigue firmando con _kv_cabeceras", "cab.update(_kv_cabeceras(url, datos))" in src)
    comprueba("y ninguna escritura nueva a mw-sync en esta fase",
              src.count("_post_con_tope(") == 1 + src.count("_post_con_tope(_SEMI_SYNC") +
              src.count("_post_con_tope(_ENRIQ_SYNC") + src.count("_post_con_tope(_WFIH_SYNC") +
              src.count("_post_con_tope(_WFIDX_SYNC"))
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._WFIH_MEM.update(mt=-1.0, d=None)
    shutil.rmtree(TMP, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: el servidor recibe lo compartido entero, con su huella, y no guarda nada de mas")
