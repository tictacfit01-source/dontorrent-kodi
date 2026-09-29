# -*- coding: utf-8 -*-
"""A que pelicula de TMDB se engancha cada titulo.

Aqui se decide la caratula, el año y la nota de cada tarjeta. Cuando falla, el
dueno ve la portada de OTRA pelicula -- y esto ya ha mordido varias veces:
'La odisea' cogiendo la de Nolan, 'Toy Story' cogiendo la 5, 'La residencia'
cogiendo la de Asterix, y 'X-Men' cogiendo 'Dias del futuro pasado'.

Habla con TMDB de verdad (desde casa, que a Render lo banea), asi que va
DESPACIO a proposito: una consulta por caso y una pausa entre ellas.

    PYTHONDONTWRITEBYTECODE=1 python -u tools/pruebas/tmdb_match.py
"""
import os
import sys
import time

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "render_relay"))
import app as A                                          # noqa: E402
import requests                                          # noqa: E402

# (consulta, tipo, id de TMDB que DEBE salir, por que)
CASOS = [
    ("X-Men", "movie", 36657,
     "la original de 2000, no 'Dias del futuro pasado' (mucho mas popular)"),
    ("X-Men 2", "movie", 36658, "la secuela de 2003"),
    ("Toy Story", "movie", 862,
     "la de 1995, no 'Toy Story 5' (hype reciente, pocos votos)"),
    ("Matrix", "movie", 603, "la de 1999"),
    ("Dune", "movie", 438631, "la de Villeneuve, la que la gente busca hoy"),
    ("Silo", "tv", 125988, "la serie de Apple"),
    ("Brothers", "tv", 250203,
     "la serie de 2026 (McConaughey), no la PELICULA de 2009 (dtbl59)"),
]

# Lo mismo, por el camino de la CAJA (tmdb.enrich, addon): es el que manda en
# el Inicio (su meta va resuelto con la ficha de DonTorrent). Busca en series Y
# en peliculas y puntua; hasta la 2.9.84 castigaba con -400 a todo lo que
# tuviera menos de 40 votos -- es decir, a los ESTRENOS.
# (titulo tal cual lo da la ficha de DonTorrent, tipo, id que DEBE salir, por que)
CASOS_CAJA = [
    ("Brothers - 1\u00aa Temporada", "tv", 250203,
     "serie de 2026 (una semana, 10 votos) y no la peli de 2009 (4.040 votos)"),
    ("Historia de dos ciudades - 1\u00aa Temporada [1080p]", "tv", 301626,
     "serie de 2026, no la peli de 1935"),
    ("A la deriva - 1\u00aa Temporada [1080p]", "tv", 301389,
     "serie de 2026, no la peli 'Adrift' de 2018"),
    ("X-Men 2", "movie", 36658,
     "la de 2003, no la entrada basura 'X-MEN 2 - Wolverine's story' (2005, 8 votos)"),
    ("Toy Story", "movie", 862, "la de 1995"),
    ("Arcane", "movie", 94605,
     "la serie de Netflix aunque la fuente diga 'peli' (se mira en los dos lados)"),
]


def caja():
    """tmdb.py de la caja con Kodi de mentira y una cache TEMPORAL."""
    import tempfile
    import types
    perfil = tempfile.mkdtemp(prefix="mw_tmdb_")
    xa = types.ModuleType("xbmcaddon")
    xa.Addon = type("Addon", (), {
        "__init__": lambda self, *a, **k: None,
        "getSetting": lambda self, k: {"tmdb_enabled": "true"}.get(k, ""),
        "getAddonInfo": lambda self, k: ""})
    xv = types.ModuleType("xbmcvfs")
    xv.translatePath = lambda q: perfil + os.sep
    xb = types.ModuleType("xbmc")
    xb.log = lambda *a, **k: None
    xb.LOGINFO = xb.LOGWARNING = xb.LOGERROR = xb.LOGDEBUG = 0
    for m in (xa, xv, xb):
        sys.modules[m.__name__] = m
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "plugin.video.mejorwolf"))
    from resources.lib import tmdb as T      # noqa: E402
    return T


def main():
    fallos = []
    for q, ep, esperado, porque in CASOS:
        # Por el camino REAL (_cat_tmdb), no llamando a _tmdb_pick a pelo: ahi
        # dentro hay ademas varias consultas y un filtro de confianza
        # (_tmdb_accept) que descarta los enganches dudosos. Probar solo la
        # eleccion daba por malos casos que en la app salen bien.
        A._CAT_TMDB_CACHE.clear()
        A._TMDB_DOWN_UNTIL[0] = 0.0
        try:
            meta = A._cat_tmdb(q, "tv" if ep == "tv" else "movie")
        except Exception as e:
            print("  (sin red para %r: %s)" % (q, e))
            continue
        got = meta.get("tmdb_id")
        ok = (got == esperado)
        nom = (meta.get("title") or "")[:34]
        anyo = meta.get("year") or "----"
        sin = "" if meta.get("poster") else "  (sin caratula)"
        print("%s %-12s -> %-34s %s id=%-7s%s %s"
              % ("ok  " if ok else "MAL ", repr(q), nom, anyo, got, sin,
                 "" if ok else ("deberia ser " + str(esperado) + ": " + porque)))
        if not ok:
            fallos.append((q, got, esperado))
        time.sleep(1.5)          # sin prisa: no hay que molestar a TMDB
    print("\n--- por el camino de la caja (tmdb.enrich) ---")
    T = caja()
    for q, kind, esperado, porque in CASOS_CAJA:
        try:
            info = T.enrich(q, kind) or {}
        except Exception as e:
            print("  (sin red para %r: %s)" % (q, e))
            continue
        got = info.get("id")
        ok = (got == esperado)
        print("%s %-28s -> %-30s %s id=%-7s %s"
              % ("ok  " if ok else "MAL ", repr(q[:26]), (info.get("title") or "")[:30],
                 info.get("year") or "----", got,
                 "" if ok else ("deberia ser " + str(esperado) + ": " + porque)))
        if not ok:
            fallos.append((q, got, esperado))
        time.sleep(1.5)
    print()
    if fallos:
        print("%d de %d MAL" % (len(fallos), len(CASOS) + len(CASOS_CAJA)))
        return 1
    print("TODOS OK: cada titulo se engancha a su pelicula")
    return 0


if __name__ == "__main__":
    sys.exit(main())
