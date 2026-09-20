# -*- coding: utf-8 -*-
"""La agrupacion de temporadas, con titulos REALES de produccion."""
import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..", "render_relay"))
import app as A                                          # noqa: E402


def serie(t, src="dt", cid=None, **kw):
    d = {"title": t, "kind": "serie", "source": src,
         "content_id": cid or t, "path": "/serie/" + (cid or "x")}
    d.update(kw)
    return d


def peli(t, src="dt"):
    return {"title": t, "kind": "movie", "source": src, "content_id": t}


print("=== 1) Cuatro temporadas de DonTorrent -> UNA tarjeta ===")
out = A._agrupa_temporadas([
    serie("Ted Lasso - 1a Temporada", cid="a"),
    serie("Ted Lasso - 2a Temporada", cid="b"),
    serie("Ted Lasso - 3a Temporada", cid="c"),
    serie("Ted Lasso - 4a Temporada", cid="d")])
print("tarjetas:", len(out), "| titulo:", repr(out[0]["title"]),
      "| temps:", [t["n"] for t in out[0].get("temps", [])])
assert len(out) == 1 and out[0]["title"] == "Ted Lasso"
assert [t["n"] for t in out[0]["temps"]] == [1, 2, 3, 4]

print("\n=== 2) Una sola temporada: el titulo TAMBIEN se limpia ===")
out = A._agrupa_temporadas([serie("Crookhaven 1 Temporada", cid="e")])
print("titulo:", repr(out[0]["title"]), "| temps:", [t["n"] for t in out[0].get("temps", [])])
assert out[0]["title"] == "Crookhaven"
assert [t["n"] for t in out[0]["temps"]] == [1]

print("\n=== 3) Lo que NO se debe tocar ===")
casos = [
    (serie("Temporada de caza"), "Temporada de caza"),      # es su nombre
    (peli("Temporada alta"), "Temporada alta"),             # pelicula
    (serie("Alerta 24 horas"), "Alerta 24 horas"),
    (serie("Los 100"), "Los 100"),
    (peli("Ted Lasso - 1a Temporada", "dt"), "Ted Lasso - 1a Temporada"),
]
for it, esperado in casos:
    r = A._agrupa_temporadas([dict(it)])[0]
    ok = r["title"] == esperado
    print(("ok  " if ok else "MAL ") + repr(it["title"])[:42] + " -> " + repr(r["title"]))
    assert ok, (it["title"], r["title"], esperado)

print("\n=== 4) Series DISTINTAS que empiezan igual no se mezclan ===")
out = A._agrupa_temporadas([
    serie("Sandokan 1 Temporada", cid="f"),
    serie("Sandokan Regresa 1 Temporada", cid="g")])
print("tarjetas:", len(out), [o["title"] for o in out])
assert len(out) == 2

print("\n=== 5) Misma serie en DOS fuentes: cada una su tarjeta (las funde la web) ===")
out = A._agrupa_temporadas([
    serie("Fauda 5 Temporada", src="dt", cid="h"),
    serie("Fauda", src="wf", cid="i")])
print("tarjetas:", len(out), [(o["title"], o["source"]) for o in out])
assert len(out) == 2 and out[0]["title"] == "Fauda" and out[1]["title"] == "Fauda"
print("   -> los dos titulos coinciden EXACTAMENTE, que es lo que permite fundirlas")

print("\n=== 6) Idempotente: pasar dos veces no cambia nada ===")
uno = A._agrupa_temporadas([serie("Fauda 5 Temporada", cid="j"),
                            serie("Fauda 6 Temporada", cid="k")])
dos = A._agrupa_temporadas([dict(x) for x in uno])
print("1a:", uno[0]["title"], [t["n"] for t in uno[0]["temps"]])
print("2a:", dos[0]["title"], [t["n"] for t in dos[0].get("temps", [])])
assert uno[0]["title"] == dos[0]["title"] and len(uno) == len(dos)

print("\n=== 7) No se muta lo que hay en la cache ===")
original = serie("Sleboda 2 Temporada", cid="m")
antes = original["title"]
A._agrupa_temporadas([original])
print("en cache sigue:", repr(original["title"]))
assert original["title"] == antes

print("\n=== 8) Con los 14 titulos REALES del Inicio de produccion ===")
reales = ["Crookhaven 1 Temporada", "El club gastronomico 1 Temporada",
          "A Tale of Two Cities 1 Temporada", "La mujer miniatura 1 Temporada",
          "Las ultimas horas del Titanic 1 Temporada", "Los Forsyte 2 Temporada",
          "Fauda 5 Temporada", "La timonel 1 Temporada",
          "Materia oscura 2 Temporada", "Star Trek Strange New Worlds 4 Temporada",
          "Stuart no consigue salvar el Universo 1 Temporada", "Sleboda 2 Temporada",
          "Made in Korea 2 Temporada", "The Gentlemen 2 Temporada"]
out = A._agrupa_temporadas([serie(t, cid=str(i)) for i, t in enumerate(reales)])
sucios = [o["title"] for o in out if "emporada" in o["title"]]
print("tarjetas:", len(out), "| con 'Temporada' colgando:", len(sucios))
for o in out[:5]:
    print("   ", repr(o["title"]), "T" + str(o["temps"][0]["n"]))
assert not sucios and len(out) == 14

print("\nTODO OK")
