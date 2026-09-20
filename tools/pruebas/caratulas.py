# -*- coding: utf-8 -*-
"""Que no se tire una caratula que ya teniamos.

La misma pelicula suele venir de varias fuentes y solo una se queda con la
tarjeta. Si la que gana no tiene poster -normal: TMDB banea la IP de Render la
mitad del tiempo- y la que pierde SI lo tiene, la tarjeta salia en gris con la
imagen al lado. Esto comprueba que eso ya no pasa, en el relay y en la web.
"""
import os
import re
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402

POS = "https://image.tmdb.org/t/p/w500/abc.jpg"
fallos = []


def peli(src, q, poster=None, **kw):
    d = {"title": "Dune", "kind": "movie", "source": src, "quality": q,
         "year": "2021", "content_id": src + q, "poster": poster}
    d.update(kw)
    return d


print("=== RELAY: _cat_rank_dedup ===")
print("-- la que GANA por calidad no tiene poster, la que pierde si --")
out = A._cat_rank_dedup([peli("dt", "720p", POS, rating=7.9, overview="Arrakis"),
                         peli("wf", "4K", None)], "dune")
gana = out[0]
print("   se queda:", gana["source"], gana["quality"], "| poster:",
      (gana.get("poster") or "NINGUNO")[-12:], "| nota:", gana.get("rating"))
if not gana.get("poster"):
    fallos.append("la tarjeta ganadora se quedo sin poster teniendo uno al lado")
if gana.get("quality") != "4K":
    fallos.append("deberia ganar la mejor calidad (4K)")

print("-- al reves: gana la que SI tiene poster (no debe perderlo) --")
out = A._cat_rank_dedup([peli("dt", "720p", None),
                         peli("wf", "4K", POS)], "dune")
print("   se queda:", out[0]["source"], out[0]["quality"], "| poster:",
      (out[0].get("poster") or "NINGUNO")[-12:])
if not out[0].get("poster"):
    fallos.append("se perdio el poster de la ganadora")

print("-- sin año: mismo caso --")
a = peli("dt", "720p", POS)
b = peli("wf", "4K", None)
a.pop("year"), b.pop("year")
out = A._cat_rank_dedup([a, b], "dune")
print("   se queda:", out[0]["source"], "| poster:",
      (out[0].get("poster") or "NINGUNO")[-12:])
if not out[0].get("poster"):
    fallos.append("sin año tambien se pierde el poster")

print("-- y NO se hereda lo que es de cada fuente (path/url/id) --")
x = peli("dt", "720p", POS, path="/pelicula/1/dune")
y = peli("wf", "4K", None, url="https://wolfmax4k.com/movie/9")
out = A._cat_rank_dedup([x, y], "dune")
g = out[0]
print("   gana", g["source"], "| url:", g.get("url"), "| path:", g.get("path"))
if g.get("path"):
    fallos.append("se heredo el path de DonTorrent a una tarjeta de WolfMax")

print("\n=== WEB: upgrade() cuando la que llega PIERDE ===")
s = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "render_relay", "app.py"),
         encoding="utf-8").read()
i = s.find("function upgrade(")
cuerpo = s[i:s.find("\nfunction ", i + 10)]
rama = cuerpo.split(" else{")[-1]
tiene = ("poster" in rama and "cambio" in rama and "swapped.push" in rama)
print("   la rama del perdedor rellena huecos y repinta:", tiene)
if not tiene:
    fallos.append("upgrade() sigue sin heredar en la rama del perdedor")
# La LISTA de campos que se heredan (el array que va antes de .forEach), no el
# comentario de al lado: buscar la palabra suelta daba un falso positivo con el
# comentario que explica justamente que temps NO se hereda.
m = re.search(r"\[([^\]]*)\]\.forEach", rama)
campos = [c.strip().strip("'\"") for c in (m.group(1).split(",") if m else [])]
print("   campos que hereda:", campos)
for prohibido in ("temps", "eps", "path", "url", "content_id", "source", "quality"):
    if prohibido in campos:
        fallos.append("upgrade() hereda '%s', que es de cada fuente" % prohibido)
if "poster" not in campos:
    fallos.append("upgrade() no hereda el poster en la rama del perdedor")

print("\n=== LA CARATULA QUE YA TENEMOS EN OTRO SITIO ===")
A._PTIT.clear()
A._posters_norm([{"title": "Silo", "kind": "serie", "source": "dt", "poster": POS}])
b = [{"title": "Silo", "kind": "serie", "source": "wf", "poster": None}]
A._posters_norm(b)
print("   una serie sin imagen se queda la de otra fuente:",
      (b[0]["poster"] or "NO")[-12:])
if not b[0]["poster"]:
    fallos.append("no se presto la caratula entre fuentes")

c = [{"title": "Silo", "kind": "movie", "source": "dx", "poster": None}]
A._posters_norm(c)
print("   pero una PELICULA no se queda la de una SERIE:",
      c[0]["poster"] or "correcto, sin caratula")
if c[0]["poster"]:
    fallos.append("se presto entre pelicula y serie del mismo nombre")

d = [{"title": "Silo", "kind": "serie", "source": "et", "poster": "https://suya.jpg"}]
A._posters_norm(d)
print("   y nunca pisa la que ya trae:", d[0]["poster"])
if d[0]["poster"] != "https://suya.jpg":
    fallos.append("piso una caratula que el item ya traia")

print("\n=== AL CDN SOLO LO QUE FUNCIONA POR CDN ===")
for u, debe, quien in [
        ("https://wolfmax4k.com/a.jpg", True, "WolfMax (directo tarda 6 s)"),
        ("https://www.elitetorrent.com/b.jpg", False, "EliteTorrent (el CDN no puede)"),
        ("https://images.weserv.nl/?url=x", False, "DonTorrent (ya iba por CDN)")]:
    it = [{"title": "zz" + quien, "kind": "movie", "poster": u}]
    A._posters_norm(it)
    fue = ("weserv" in (it[0]["poster"] or "")) and (u != it[0]["poster"])
    ok = (fue == debe)
    print(("   ok  " if ok else "   MAL ") + quien)
    if not ok:
        fallos.append("CDN mal aplicado a " + quien)

print("\n---- VEREDICTO ----")
if fallos:
    for f in fallos:
        print("   MAL:", f)
    print("FALLA ALGO")
    sys.exit(1)
print("TODO OK: ninguna caratula se tira")
