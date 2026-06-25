# -*- coding: utf-8 -*-
"""Aumenta catalog_seed.json con los campos de la FICHA ENRIQUECIDA
(overview/backdrop/genres/tmdb_id) usando el MISMO _cat_tmdb de la app, desde
IP residencial (TMDB alcanzable). NO toca poster/year/rating ya presentes.
Ejecutar: python augment_seed.py   (desde render_relay/)."""
import json
import time
import app  # reusa _cat_tmdb (mismo matching que el catalogo) + mapa de generos

SEED = "catalog_seed.json"
seed = json.load(open(SEED, encoding="utf-8"))

total = enr = 0
for key, blk in seed.items():
    items = blk.get("items") if isinstance(blk, dict) else blk
    for it in items:
        total += 1
        kind = "tv" if it.get("kind") == "serie" else "movie"
        meta = app._cat_tmdb(it.get("title", ""), kind)
        got = False
        if meta.get("overview"):
            it["overview"] = meta["overview"]; got = True
        if meta.get("backdrop"):
            it["backdrop"] = meta["backdrop"]
        if meta.get("genres"):
            it["genres"] = meta["genres"]
        if meta.get("tmdb_id"):
            it["tmdb_id"] = meta["tmdb_id"]; got = True
        if got:
            enr += 1
        time.sleep(0.04)   # suave con TMDB

with open(SEED, "w", encoding="utf-8") as f:
    json.dump(seed, f, ensure_ascii=False)

# resumen por lista
for key, blk in seed.items():
    items = blk.get("items") if isinstance(blk, dict) else blk
    e = sum(1 for x in items if x.get("overview") or x.get("tmdb_id"))
    print(key, "->", e, "/", len(items), "enriquecidos")
print("TOTAL:", enr, "/", total)
