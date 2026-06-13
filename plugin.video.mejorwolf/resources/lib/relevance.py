"""Filtro de relevancia para busquedas que devuelven coincidencias FLOJAS
(DivxTotal, EliteTorrent...): a veces traen resultados que no tienen que ver con
lo buscado (matching por reparto/descripcion, o "ultimos" cuando no hay match).

DonTorrent NO lo necesita (su busqueda es exacta).

Politica: quedarse con los titulos que contienen TODAS las palabras
significativas de la consulta; si ninguno, los que contengan ALGUNA; si tampoco,
devolver [] (mejor no mostrar nada que mostrar basura)."""
import re
import unicodedata

_STOP = {"el", "la", "los", "las", "de", "del", "y", "a", "en", "un", "una",
         "the", "of", "to", "lo", "su", "al", "o", "and"}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)


def filter_items(items, query):
    toks = [t for t in _norm(query).split() if len(t) > 1 and t not in _STOP]
    if not toks:
        return items

    def score(it):
        words = set(_norm((it or {}).get("title", "")).split())
        return sum(1 for t in toks if t in words)

    full = [it for it in items if score(it) == len(toks)]
    if full:
        return full
    return [it for it in items if score(it) >= 1]
