# -*- coding: utf-8 -*-
"""WolfMax sin saltarse su captcha (dtbl75).

Desde el 07-10 WolfMax (WolfTorrent) pide verificacion humana en cada descarga.
No se salta. Pero:
  1) lo que ya conocemos (su huella) se reproduce por magnet, sin tocar su web;
  2) una PERSONA consigue el .torrent en su web (una casilla) o el ZIP diario con
     todos los del dia anterior, lo sube, y desde ese momento vale para todos;
  3) cada torrent del ZIP se ata a su ficha por titulo + capitulo + TAMAÑO
     ("26,59 GB" = 26,59 GiB, medido con Spider-Man 4K), y si hay duda, no.
Sin red: torrents fabricados aqui mismo.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

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


def benc(x):
    if isinstance(x, int):
        return b"i%de" % x
    if isinstance(x, str):
        x = x.encode("utf-8")
    if isinstance(x, bytes):
        return b"%d:%s" % (len(x), x)
    if isinstance(x, list):
        return b"l" + b"".join(benc(v) for v in x) + b"e"
    if isinstance(x, dict):
        return b"d" + b"".join(benc(k) + benc(x[k]) for k in sorted(x)) + b"e"
    raise TypeError(x)


def torrent(nombre, largo=None, ficheros=None):
    info = {"name": nombre, "piece length": 2097152, "pieces": b"\x00" * 20}
    if ficheros:
        info["files"] = [{"length": n, "path": [p]} for p, n in ficheros]
    else:
        info["length"] = largo
    data = benc({"announce": "udp://tracker.opentrackr.org:1337/announce", "info": info})
    return data, hashlib.sha1(benc(info)).hexdigest()


GIB = 1024 ** 3
SM = 28551267564                                  # Spider-Man 4K, medido
TMP = tempfile.mkdtemp(prefix="mw_wfmag_")
viejos = {k: getattr(A, k) for k in (
    "_WFIH_FILE", "_DXIH_FILE", "_WF_VERIF_FILE", "_WFULT_FILE", "_kb_enqueue",
    "_catjob_wait_any", "_box_wf", "_box_for", "_live_boxes")}
A._WFIH_FILE = os.path.join(TMP, "wfih.json")
A._DXIH_FILE = os.path.join(TMP, "dxih.json")
A._WF_VERIF_FILE = os.path.join(TMP, "verif.json")
A._WFULT_FILE = os.path.join(TMP, "wfult.json")
A._WFULT.clear()
A._WFIH_MEM.update(mt=-1.0, d=None)


def verif(si):
    if si:
        with open(A._WF_VERIF_FILE, "w") as f:
            json.dump({"ts": time.time(), "desde": time.time()}, f)
    elif os.path.exists(A._WF_VERIF_FILE):
        os.remove(A._WF_VERIF_FILE)


PEDIDOS, RESP = [], {}
A._kb_enqueue = lambda box, ev: PEDIDOS.append((box, ev.get("url")))
A._catjob_wait_any = lambda jobs, secs, ok=None, corta=None: (jobs.clear() or dict(RESP)) if jobs else None
A._box_wf = lambda code, excluir=(), minimo=None: next((b for b in ("caja1",) if b not in excluir), None)
A._box_for = lambda code: "caja1"
A._live_boxes = lambda *a, **k: ["caja1"]
cli = A.app.test_client()
N = "https://wolfmax4k.com"
try:
    print("\n=== 1) Leer un .torrent y un ZIP ===")
    d1, ih1 = torrent("Spider-Man Brand New Day [4k 2160p][Esp]", SM)
    r = A._torrent_lee(d1)
    comprueba("la huella es el sha1 del 'info' tal cual, con nombre y bytes",
              r and r["ih"] == ih1 and r["b"] == SM and r["n"].startswith("Spider-Man"), r)
    d2, ih2 = torrent("Serie [HDTV]", ficheros=[("a.mkv", 1000), ("Canal Oficial de Telegram !!!.url", 112)])
    r = A._torrent_lee(d2)
    comprueba("varios ficheros: suma todos", r and r["b"] == 1112 and r["nf"] == 2 and r["ih"] == ih2, r)
    comprueba("lo que no es un .torrent: None (sin reventar)",
              A._torrent_lee(b"hola") is None and A._torrent_lee(b"d4:infoi1ee") is None
              and A._torrent_lee(d1[:-15]) is None and A._torrent_lee(b"l" * 200 + b"e" * 200) is None)
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Spider-Man-Brand-New-Day-(4K)-(WolfTorrent)-(Ih0f).torrent", d1)
        z.writestr("dir/Serie.torrent", d2)
        z.writestr("leeme.txt", "hola")
        z.writestr("trampa.torrent", b"d" + b"0" * (5 * 1024 * 1024))
    ts = A._zip_torrents(zb.getvalue())
    comprueba("ZIP: solo sus .torrent buenos (uno de 5 MB, fuera sin descomprimirlo entero)",
              ts is not None and sorted(i["ih"] for _, i in ts) == sorted([ih1, ih2]), ts)

    print("\n=== 2) El tamaño de WolfMax y el emparejamiento ===")
    comprueba("'26,59 GB' son 26,59 GiB: el Spider-Man 4K casa, con 26,60 no",
              A._wf_tam_casa(SM, "26,59 GB") and not A._wf_tam_casa(SM, "26,60 GB"))
    comprueba("'380,17 MB' tambien", A._wf_tam_casa(int(380.17 * 1024 ** 2), "380,17 MB"))
    meta_sm = {"t": "Spider-Man: Brand New Day", "q": "4K", "sz": "26,59 GB"}
    comprueba("titulo + tamaño: es el", A._wf_casa("Spider-Man Brand New Day [4k 2160p][Esp]", SM, meta_sm))
    comprueba("mismo titulo, otro tamaño: no",
              not A._wf_casa("Spider-Man Brand New Day [1080p][Esp]", 9 * GIB, meta_sm))
    comprueba("mismo tamaño, otro titulo: no", not A._wf_casa("Spider-Man No Way Home [4k]", SM, meta_sm))
    b15 = int(1.31 * GIB)
    m15 = {"t": "Ted Lasso 1x05", "q": "720p", "sz": "1,31 GB"}
    comprueba("capitulo con 'Cap.105' y su tamaño: es el",
              A._wf_casa("Ted Lasso - Temporada 1 [HDTV 720p][Cap.105][Esp]", b15, m15))
    comprueba("...el 1x06 no", not A._wf_casa("Ted Lasso - Temporada 1 [HDTV 720p][Cap.105][Esp]", b15,
                                              dict(m15, t="Ted Lasso 1x06")))
    comprueba("nombre de fichero 'Ted_Lasso_1_5_HDTV_720p' + tamaño: es el",
              A._wf_casa("Ted_Lasso_1_5_HDTV_720p.torrent", b15, m15))
    comprueba("...pero SIN tamaño no se arriesga (el capitulo no se lee)",
              not A._wf_casa("Ted_Lasso_1_5_HDTV_720p.torrent", b15, dict(m15, sz="")))
    comprueba("una peli no es un capitulo", not A._wf_casa("Ted Lasso [Cap.105]", b15,
                                                           {"t": "Ted Lasso", "sz": "1,31 GB"}))

    print("\n=== 3) El almacen propio de huellas ===")
    comprueba("la direccion oficial donde una persona lo consigue",
              A._wf_pasarela(N + "/serie/episodio/2ap2ud") == "https://wolftorrent.com/descarga/serie/2ap2ud"
              and A._wf_pasarela("https://wolftorrent.com/pelicula/r2h3bn/") ==
              "https://wolftorrent.com/descarga/pelicula/r2h3bn"
              and A._wf_pasarela("https://www.elitetorrent.com/x") == "")
    A._wfih_apunta("https://wolftorrent.com/pelicula/r2h3bn", ih1, "Spider-Man", SM)
    e = A._wfih_de(N + "/pelicula/r2h3bn/")
    comprueba("con wolftorrent.com o wolfmax4k.com, con o sin '/', la misma ficha", e and e["ih"] == ih1, e)
    A._wfih_apunta(N + "/pelicula/r2h3bn", ih2, o="zip", pisa=False)
    comprueba("lo emparejado de un ZIP no pisa lo que trajo una persona", A._wfih_de(N + "/pelicula/r2h3bn")["ih"] == ih1)
    with open(A._DXIH_FILE, "w") as f:
        json.dump({N + "/pelicula/aaaa11": {"ih": "a" * 40, "ts": time.time() - 40 * 86400},
                   N + "/serie/episodio/bbbb22": {"ih": "b" * 40, "ts": time.time()},
                   "https://divxtotal.foo/peli/x": {"ih": "c" * 40, "ts": time.time()}}, f)
    n = A._wfih_migra()
    comprueba("las del aprendiz (aunque tengan 40 dias) pasan a su almacen; las de DivxTotal no",
              n == 2 and A._wfih_de(N + "/pelicula/aaaa11") and not A._wfih_de("https://divxtotal.foo/peli/x")
              and A._wfih_migra() == 0, n)
    mg = A._wf_magnet(A._wfih_de(N + "/pelicula/r2h3bn"))
    comprueba("el magnet: su huella, su nombre y los trackers publicos",
              mg.startswith("magnet:?xt=urn:btih:" + ih1) and "tracker.opentrackr.org" in mg
              and "&dn=Spider-Man" in mg, mg[:120])

    print("\n=== 4) Reproducir ===")
    UR = N + "/pelicula/r2h3bn"
    verif(True)
    del PEDIDOS[:]
    r = A._resuelve_enlace("", "wf", UR)
    comprueba("WolfMax pide verificacion y la huella se sabe: magnet AL MOMENTO, sin molestar a ninguna caja",
              r.get("link", "").startswith("magnet:") and r.get("via") == "magnet" and not PEDIDOS, (r, PEDIDOS))
    r = A._resuelve_enlace("", "wf", N + "/serie/episodio/zzzz99")
    comprueba("...y si no se sabe: 'verificacion' con la pagina oficial para conseguirlo",
              r.get("error") == "verificacion" and r.get("wolftorrent") ==
              "https://wolftorrent.com/descarga/serie/zzzz99" and not PEDIDOS, r)
    RESP.clear()
    RESP.update({"link": "", "error": "verificacion"})
    r = A._resuelve_enlace("", "wf", UR, cache=False)
    comprueba("el vigia (sin memoria) sigue mirando la web de verdad, no el magnet",
              r.get("error") == "verificacion" and PEDIDOS, (r, PEDIDOS))
    verif(False)
    del PEDIDOS[:]
    r = A._resuelve_enlace("", "wf", UR)
    comprueba("sin saber aun de la verificacion: la caja la cuenta, y la gente recibe el magnet igual",
              r.get("via") == "magnet" and PEDIDOS and A._wf_verif_activa(), (r, PEDIDOS))
    verif(False)
    RESP.clear()
    RESP.update({"link": "https://wolfmax4k.com/torrents/peliculas/x.torrent"})
    A._RESUELTO.clear()
    try:
        os.remove(A._RESUELTO_FILE)
    except Exception:
        pass
    r = A._resuelve_enlace("", "wf", UR)
    comprueba("si WolfMax quita la verificacion, su .torrent de siempre (el magnet es el plan B)",
              r.get("link", "").endswith(".torrent"), r)
    A._RESUELTO.clear()
    try:
        os.remove(A._RESUELTO_FILE)
    except Exception:
        pass

    print("\n=== 5) La persona sube su .torrent o el ZIP del dia ===")
    verif(True)
    d3, ih3 = torrent("Ted Lasso - Temporada 1 [HDTV 720p][Cap.105][Esp]", b15)
    U3 = N + "/serie/episodio/2s42pf"
    rr = cli.post("/wfsube", data={"f": (io.BytesIO(d3), "Ted_Lasso_1_5_HDTV_720p.torrent"), "url": U3,
                                   "t": "Ted Lasso 1x05", "q": "720p", "sz": "1,31 GB"},
                  content_type="multipart/form-data").get_json()
    comprueba("su .torrent de esa ficha: se guarda y vuelve el magnet para la tele",
              rr.get("ok") and rr.get("link", "").startswith("magnet:?xt=urn:btih:" + ih3)
              and A._wfih_de(U3)["o"] == "persona", rr)
    rr = cli.post("/wfsube", data={"f": (io.BytesIO(d1), "x.torrent"), "url": N + "/serie/episodio/2tzkkn",
                                   "t": "Ted Lasso 1x10", "q": "HDTV", "sz": "380,17 MB"},
                  content_type="multipart/form-data")
    comprueba("si elige OTRO .torrent de Descargas: se dice cual es y no se guarda",
              rr.status_code == 409 and "Spider-Man" in rr.get_json().get("error", "")
              and not A._wfih_de(N + "/serie/episodio/2tzkkn"), rr.get_json())
    comprueba("sin archivo o con basura: un error que se entiende (nunca un cuerpo vacio)",
              cli.post("/wfsube", data={}, content_type="multipart/form-data").get_json().get("error")
              and cli.post("/wfsube", data={"f": (io.BytesIO(b"<html>"), "a.torrent")},
                           content_type="multipart/form-data").status_code == 400)
    # el ZIP del dia: tres torrents; dos fichas conocidas (con tamaño, caja 2.9.89)
    da, iha = torrent("Pelicula Uno [1080p][Esp]", int(5.55 * GIB))
    db, ihb = torrent("Serie Dos - Temporada 2 [HDTV 720p][Cap.203][Esp]", int(1.11 * GIB))
    dc, ihc = torrent("Sin Ficha [4k 2160p][Esp]", int(20.02 * GIB))
    A._WFULT["movie"] = {"ts": time.time(), "items": [
        {"title": "Pelicula Uno", "url": N + "/pelicula/pu1111", "quality": "1080p", "size": "5,55 GB",
         "source": "wf", "kind": "movie"}]}
    A._WFULT["tvshow"] = {"ts": time.time(), "items": [
        {"title": "Serie Dos 2x03", "url": N + "/serie/episodio/sd2203", "quality": "720p", "size": "1,11 GB",
         "source": "wf", "kind": "serie"}]}
    A._wfult_guarda()
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, dat in (("Pelicula-Uno.torrent", da), ("Serie-Dos.torrent", db), ("Sin-Ficha.torrent", dc)):
            z.writestr(nom, dat)
    rr = cli.post("/wfsube", data={"f": (io.BytesIO(zb.getvalue()), "WolfTorrent-2026-10-10.zip")},
                  content_type="multipart/form-data").get_json()
    comprueba("ZIP: los tres guardados, dos ya atados a su ficha, con el dia del ZIP",
              rr.get("ok") and rr.get("n") == 3 and rr.get("emparejados") == 2 and rr.get("dia") == "2026-10-10"
              and A._wfih_de(N + "/serie/episodio/sd2203")["ih"] == ihb, rr)
    U4 = N + "/pelicula/sf4444"
    r = A._resuelve_enlace("", "wf", U4, meta={"t": "Sin Ficha", "q": "4K", "sz": "20,02 GB"})
    comprueba("el que no tenia ficha se encuentra al darle a reproducir (titulo + tamaño) y queda atado",
              r.get("via") == "magnet" and ihc in r.get("link", "") and A._wfih_de(U4)["ih"] == ihc, r)
    js = cli.get("/catetboxresolve?src=wf&url=" + N + "/pelicula/pu1111").get_json()
    comprueba("/catetboxresolve lo da por magnet", js.get("link", "").startswith("magnet:"), js)
    del PEDIDOS[:]
    verif(False)
    j1 = cli.get("/wfmagnet?url=" + N + "/pelicula/pu1111").get_json()
    j2 = cli.get("/wfmagnet?url=" + N + "/pelicula/nadie9").get_json()
    comprueba("/wfmagnet (lo que pregunta Kodi): el magnet si se sabe, vacio si no, y NUNCA encarga nada a una caja",
              j1.get("link", "").startswith("magnet:") and j2 == {"link": ""} and not PEDIDOS, (j1, j2, PEDIDOS))
    verif(True)
    comprueba("las respuestas dicen que el ZIP de ese dia ya esta (la web no lo vuelve a pedir)",
              A._con_caida({}).get("wf_zip", {}).get("dia") == "2026-10-10", A._con_caida({}))
    # dos torrents para UNA ficha: duda -> no se ata
    dd, ihd = torrent("Gemela [1080p]", int(3.33 * GIB))
    de, ihe = torrent("Gemela [1080p] (2)", int(3.33 * GIB))
    A._WFULT["movie"]["items"].append({"title": "Gemela", "url": N + "/pelicula/ge5555", "quality": "1080p",
                                       "size": "3,33 GB", "source": "wf", "kind": "movie"})
    A._wfult_guarda()
    r2 = A._wfih_de_zip([("g1.torrent", A._torrent_lee(dd)), ("g2.torrent", A._torrent_lee(de))], "z.zip")
    comprueba("dos torrents que casan con la MISMA ficha: no se ata ninguno (mejor nada que otra peli)",
              not A._wfih_de(N + "/pelicula/ge5555"), r2)
    it = A._al_servir([{"title": "Pelicula Uno", "source": "wf", "url": N + "/pelicula/pu1111", "quality": "1080p"},
                       {"title": "Otra", "source": "wf", "url": N + "/pelicula/no0000", "quality": "1080p"}])
    comprueba("la web sabe que se puede ver (`mag`), y solo de lo que se sabe",
              it[0].get("mag") == 1 and not it[1].get("mag"), it)
    dg = cli.get("/catdiag").get_json().get("wfih") or {}
    comprueba("/catdiag -> wfih: atadas, reserva, de donde salieron, el ultimo ZIP",
              dg.get("atadas", 0) >= 5 and dg.get("origen", {}).get("persona") == 2
              and dg.get("zip", {}).get("dia"), dg)
    A._WFSUBE_RL["127.0.0.1"] = [time.time()] * 20
    rr = cli.post("/wfsube", data={"f": (io.BytesIO(d3), "a.torrent")}, content_type="multipart/form-data")
    comprueba("y con un tope de subidas por IP", rr.status_code == 429, rr.status_code)
    A._WFSUBE_RL.clear()
finally:
    for k, v in viejos.items():
        setattr(A, k, v)
    A._WFULT.clear()
    A._WFIH_MEM.update(mt=-1.0, d=None)
    A._RESUELTO.clear()
    shutil.rmtree(TMP, ignore_errors=True)

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: WolfMax se ve por magnet, y lo nuevo lo trae una persona con una casilla")
