# -*- coding: utf-8 -*-
"""Las Series 4K de DonTorrent para los capitulos 4K de WolfMax (dtbl82). Sin red.

Medido el 02-11: WolfMax republica TAL CUAL (misma huella) los capitulos de las
Series 4K de DonTorrent, y casi ninguno se podia ver desde su tarjeta por su
verificacion. El aprendiz los aprende de DonTorrent y los deja en la reserva de
los ZIP. Se comprueba, con el aprendiz REAL (_apr_ronda, _apr_guarda) y solo de
mentira DonTorrent y la caja:
  1) la lista de temporadas 4K y cada temporada, UNA pagina por vuelta;
  2) cada capitulo, con la pausa de DonTorrent (no hay avalancha);
  3) el capitulo 4K va a la reserva y la tarjeta de WolfMax lo reproduce por
     magnet (/wfmagnet), con titulo + capitulo + tamaño exacto;
  4) otro capitulo, otro tamaño u otra calidad: NO se ata nada;
  5) lo que no es 4K no va a la reserva (pero queda mirado);
  6) tras un despliegue (cola en memoria vacia) no se vuelve a pedir lo mirado;
  7) la copia: "dt" se valida, se funde y se sube;
  8) fuera de Render (sin MW_DT4K) no hace nada; /catdiag lo cuenta.
"""
import json
import os
import shutil
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["MW_SIN_KEEPALIVE"] = "1"
os.environ["MW_APRENDIZ"] = "0"
os.environ["MW_SIN_NUBE"] = "1"
os.environ["MW_SIN_USO"] = "1"
os.environ["MW_DT4K"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "..", "render_relay"))
import app as A                                          # noqa: E402

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


def ih(s):
    import hashlib
    return hashlib.sha1(s.encode()).hexdigest()


def gib(b):
    """Como lo escribe WolfMax: GiB con dos decimales y coma."""
    return ("%.2f GB" % (b / 1024 ** 3)).replace(".", ",")


LISTA = """<html><a href="/serie/130883/130883/Kill-Jackie-1-Temporada-4k">x</a>
<a href="/serie/130883/130883/Kill-Jackie-1-Temporada-4k">x</a>
<a href="/serie/129832/129832/Materia-oscura-2-Temporada-4K">x</a>
<a href="/serie/1/1/Otra-cosa-1-Temporada-720p">x</a></html>"""


def fila(ep, cid):
    return ("<tr> <td style='vertical-align: middle;'>%s</td> <td><a class=\"protected-download\" "
            "data-content-id=\"%d\" data-tabla=\"series\">Descargar</a></td> "
            "<td>2026-10-13</td> <td></td> </tr>" % (ep, cid))


TEMP = {
    "/serie/130883/130883/Kill-Jackie-1-Temporada-4k":
        "<title>Descargar Kill Jackie - DonTorrent</title><table><tbody>%s%s%s</tbody></table>"
        % (fila("1x01", 130884), fila("1x02", 130885), fila("1x03", 130886)),
    "/serie/129832/129832/Materia-oscura-2-Temporada-4K":
        "<title>Descargar Materia oscura - DonTorrent</title><table><tbody>%s</tbody></table>"
        % fila("2x07", 129840),
}
TORR = {   # lo que la caja sacaria de cada .torrent de DonTorrent
    "series:130884": ("Kill Jackie [4k 2160p][Cap.101]", 5110366082),
    "series:130885": ("Kill Jackie [4k 2160p][Cap.102]", 4987123456),
    "series:130886": ("Kill Jackie [HDTV 720p][Cap.103]", 1290806410),   # no es 4K
    "series:129840": ("Materia oscura [4k 2160p][Cap.207]", 12245860056),
}

FICH = [A._WFIH_FILE, A._DTPACKED_FILE, A._APR_FILE]
copias = {}
for f in FICH:
    if os.path.exists(f):
        copias[f] = f + ".prueba_dt4k_bak"
        shutil.copy(f, copias[f])
try:
    os.makedirs("/tmp", exist_ok=True)
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
    A._WFIH_MEM["d"] = None
    pedidas, pows = [], []

    def _html(path):
        pedidas.append(path)
        return LISTA if path == "/series/4k" else (TEMP.get(path) or ("" if "page" not in path else "<html></html>"))

    def _apr_dt(k):
        pows.append(k)
        n, b = TORR[k]
        return {"ih": ih(k), "p": False, "q": "4K" if "4k" in n else "720p", "n": n, "b": b}

    A._dt4k_html = _html
    A._apr_dt = _apr_dt
    A._apr_items_inicio = lambda: []
    A._seed_counts_many = lambda ihs, *a, **k: {}
    A._dt_caida_ya = lambda: False
    A._semillas_nube_sube = lambda *a, **k: 0
    A._wf_cruce_dt = lambda: 0
    A._mem_cgroup_mb = lambda: 100

    print("\n=== 1) Una pagina por vuelta ===")
    A._apr_ronda()
    comprueba("la 1a vuelta lee la lista (3 paginas) y no pide ningun .torrent",
              pedidas == ["/series/4k", "/series/4k/page/2", "/series/4k/page/3"] and not pows,
              (pedidas, pows))
    comprueba("dos temporadas 4K (sin repetir, sin la de 720p)", A._DT4K["temps"] == [
        "/serie/130883/130883/Kill-Jackie-1-Temporada-4k",
        "/serie/129832/129832/Materia-oscura-2-Temporada-4K"], A._DT4K["temps"])
    del pedidas[:]
    A._APR_T_DT[0] = 0.0
    A._apr_ronda()
    comprueba("la 2a lee UNA temporada", pedidas == ["/serie/130883/130883/Kill-Jackie-1-Temporada-4k"],
              pedidas)
    comprueba("y pide UN capitulo", pows == ["series:130884"], pows)

    print("\n=== 2) La pausa de DonTorrent ===")
    A._apr_ronda()
    comprueba("enseguida otra vuelta: ningun .torrent mas (pausa de 120 s)", pows == ["series:130884"], pows)
    for _ in range(6):
        A._APR_T_DT[0] = 0.0
        A._apr_ronda()
    comprueba("con la pausa cumplida, los 4 capitulos", sorted(pows) == sorted(TORR), pows)
    comprueba("ninguno dos veces", len(pows) == len(set(pows)), pows)

    print("\n=== 3) La tarjeta de WolfMax, por magnet ===")
    d = A._wfih_lee()
    comprueba("los 4 mirados en la copia ('dt')", set(d["dt"]) == set(TORR), d["dt"])
    comprueba("3 capitulos 4K en la reserva", set(d["p"]) == {ih("series:130884"), ih("series:130885"),
                                                            ih("series:129840")}, list(d["p"]))
    cli = A.app.test_client()
    U1 = "https://wolfmax4k.com/serie/episodio/kj0101"
    r = cli.get("/wfmagnet", query_string={"url": U1, "t": "Kill Jackie 1x01", "sz": gib(5110366082),
                                           "q": "4K"}).get_json()
    comprueba("Kill Jackie 1x01 4K: magnet con la huella de DonTorrent",
              ih("series:130884") in (r.get("link") or ""), r)
    comprueba("y queda atada a su ficha", (A._wfih_de(U1) or {}).get("ih") == ih("series:130884"))
    r = cli.get("/wfmagnet", query_string={"url": "https://wolfmax4k.com/serie/episodio/mo0207",
                                           "t": "Materia oscura 2x07", "sz": gib(12245860056)}).get_json()
    comprueba("Materia oscura 2x07: tambien", ih("series:129840") in (r.get("link") or ""), r)

    print("\n=== 4) Si hay duda, nada ===")
    r = cli.get("/wfmagnet", query_string={"url": "https://wolfmax4k.com/serie/episodio/kj0102",
                                           "t": "Kill Jackie 1x02", "sz": "3,10 GB"}).get_json()
    comprueba("otro tamaño: nada", not r.get("link"), r)
    r = cli.get("/wfmagnet", query_string={"url": "https://wolfmax4k.com/serie/episodio/kj0104",
                                           "t": "Kill Jackie 1x04", "sz": gib(4987123456)}).get_json()
    comprueba("el tamaño de otro capitulo con otro numero: nada", not r.get("link"), r)
    r = cli.get("/wfmagnet", query_string={"url": "https://wolfmax4k.com/serie/episodio/xx0102",
                                           "t": "Otra serie 1x02", "sz": gib(4987123456)}).get_json()
    comprueba("el tamaño de un capitulo con otro titulo: nada", not r.get("link"), r)

    print("\n=== 5) Lo que no es 4K ===")
    comprueba("el 720p, mirado pero NO en la reserva",
              "series:130886" in d["dt"] and ih("series:130886") not in d["p"])

    print("\n=== 6) Tras un despliegue ===")
    A._DT4K.update({"t_lista": 0.0, "temps": [], "vistas": {}, "pend": []})
    antes = list(pows)
    for _ in range(5):
        A._APR_T_DT[0] = 0.0
        A._apr_ronda()
    comprueba("relee lista y temporadas, pero no vuelve a pedir ningun .torrent", pows == antes,
              pows[len(antes):])

    print("\n=== 7) La copia ===")
    v = A._wfih_valida({"h": {}, "p": {}, "dt": {"series:1": ih("a"), "peliculas:2": ih("b"),
                                                 "series:3": "no-es-huella", "series:x": ih("c")}})
    comprueba("valida: solo series:<id> -> huella", v["dt"] == {"series:1": ih("a")}, v["dt"])
    comprueba("el formato viejo (sin dt) sigue valiendo", A._wfih_valida({"h": {}, "p": {}})["dt"] == {})
    rem = A._wfih_vacio()
    rem["dt"] = {"series:777": ih("remoto"), "series:130884": ih("otra")}
    A._wfih_funde(rem)
    d2 = A._wfih_lee()
    comprueba("fundir: trae lo que falta y no pisa lo de aqui",
              d2["dt"].get("series:777") == ih("remoto") and d2["dt"]["series:130884"] == ih("series:130884"),
              d2["dt"])
    subido = {}
    A._get_con_tope = lambda *a, **k: (json.dumps({"gz": ""}), 200)
    A._post_con_tope = lambda url, cuerpo, *a, **k: (subido.update(cuerpo), 200)[1]
    A._wfih_sube_hilo()
    import base64
    import gzip
    doc = json.loads(gzip.decompress(base64.b64decode(subido.get("gz") or "")).decode()) if subido else {}
    comprueba("la subida lleva 'dt'", set(doc.get("dt") or {}) >= set(TORR), list(doc))

    print("\n=== 8) Solo en Render; /catdiag ===")
    os.environ["MW_DT4K"] = "0"
    del pedidas[:]
    A._DT4K["t_lista"] = 0.0
    comprueba("con MW_DT4K=0 no lee nada", A._dt4k_pendientes(time.time()) == [] and not pedidas, pedidas)
    os.environ["MW_DT4K"] = "1"
    c = cli.get("/catdiag").get_json().get("dt4k") or {}
    comprueba("/catdiag -> dt4k: mirados, en reserva, atadas",
              c.get("mirados", 0) >= 4 and c.get("atadas") == 2 and c.get("en_reserva") == 1, c)
finally:
    for f in FICH:
        try:
            os.remove(f)
        except Exception:
            pass
        if f in copias:
            shutil.move(copias[f], f)

print("\n%s" % ("TODO EN VERDE" if not fallos else "%d MAL" % fallos))
sys.exit(1 if fallos else 0)
