# -*- coding: utf-8 -*-
"""La web NUEVA de WolfMax, del lado de la caja (addon 2.9.77).

27-09-2026: WolfMax volvio de su caida con la web rehecha y todo lo anterior
dio 404. Esto vigila, con marcado REAL recortado (sin red):
  1) wf_web lee busquedas (la peli con todas sus versiones, la temporada con
     su ultimo capitulo), fichas de temporada, capitulo suelto, peli y
     documental; calidades, titulos, rutas y paginas;
  2) el torrent: reto -> prueba de trabajo -> download_url, recordado; con el
     limite de 60/hora no se le insiste; el captcha se dice;
  3) scraper_wolfmax (Kodi de mentira): la busqueda da una version de peli por
     item y los capitulos "Titulo 4x07" con la mejor calidad primero; la serie
     completa junta temporadas y calidades quedandose con la MEJOR de cada
     capitulo; una temporada da enlaces DIFERIDOS (el torrent al reproducir);
     el indice de la caja solo guarda URLs nuevas y tira las viejas.
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import types

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
AQUI = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(AQUI, "..", "..", "plugin.video.mejorwolf")
PERFIL = tempfile.mkdtemp(prefix="mw_perfil_wf_")

fallos = 0


def comprueba(nombre, ok, detalle=""):
    global fallos
    print(("  ok   " if ok else "  MAL  ") + nombre + ("" if ok else "  -> " + str(detalle)))
    if not ok:
        fallos += 1


# --- Kodi de mentira ---------------------------------------------------------
xbmc = types.ModuleType("xbmc")
xbmc.log = lambda *a, **k: None
xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGDEBUG = 1, 2, 3, 0
xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = type("Addon", (), {
    "__init__": lambda self, *a, **k: None,
    "getSetting": lambda self, k: "", "setSetting": lambda self, k, v: None,
    "getSettingBool": lambda self, k: False,
    "getAddonInfo": lambda self, k: {"profile": PERFIL, "version": "2.9.77"}.get(k, "")})
xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda p: PERFIL + os.sep
xbmcvfs.exists = os.path.exists
xbmcvfs.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
xbmcgui = types.ModuleType("xbmcgui")
xbmcplugin = types.ModuleType("xbmcplugin")
for m in (xbmc, xbmcaddon, xbmcvfs, xbmcgui, xbmcplugin):
    sys.modules[m.__name__] = m
sys.path.insert(0, ADDON)
from resources.lib import wf_web as W                      # noqa: E402

# --- marcado real, recortado ----------------------------------------------------
TARJETA_PELI = '''<article class="wolf-card">
  <a class="wolf-card-poster" href="/pelicula/d9db5r" tabindex="-1" aria-hidden="true">
    <img src="https://images.weserv.nl/?url=x" data-original="/caratulas/peliculas/RHVuZQ/Dune-WolfMax4K.jpg" width="120" height="165" alt="Dune " loading="lazy"></a>
  <div class="wolf-card-content">
    <h3 class="wolf-card-title"><a class="wolf-card-main" href="/pelicula/d9db5r" aria-label="Ver ficha: Dune ">Dune </a></h3>
    <p class="wolf-card-meta"><span>2021</span><span>Ciencia Ficción</span><span>Aventuras</span></p>
  </div>
  <ul class="wolf-card-files" aria-label="Archivos de Dune ">
    <li class="wolf-card-file">
      <a class="wolf-card-format" href="/pelicula/d9db5r"><strong>BDremux-1080p</strong></a>
      <span class="wolf-card-size">8.2285 GB</span>
      <button type="button" class="protected-download wolf-card-download" data-content-id="370072" data-tabla="peliculas" aria-label="Descargar Dune : BDremux-1080p"><span>Descargar</span></button>
    </li>
    <li class="wolf-card-file">
      <a class="wolf-card-format" href="/pelicula/d8k2pt"><strong>4K</strong></a>
      <span class="wolf-card-size">18.2849 GB</span>
      <button type="button" class="protected-download wolf-card-download" data-content-id="370057" data-tabla="peliculas" aria-label="Descargar Dune : 4K"><span>Descargar</span></button>
    </li>
  </ul>
  <details class="wolf-card-more"><summary>Ver las 1 versiones restantes</summary>
    <ul class="wolf-card-files-extra">
      <li class="wolf-card-file">
        <a class="wolf-card-format" href="/pelicula/dptwvd"><strong>DVDRip</strong></a>
        <span class="wolf-card-size">2.2073 GB</span>
        <button type="button" class="protected-download wolf-card-download" data-content-id="369736" data-tabla="peliculas" aria-label="Descargar Dune : DVDRip"><span>Descargar</span></button>
      </li>
    </ul>
  </details>
</article>'''


def tarjeta_serie(sid, titulo, ep, fmt, cid):
    return '''<article class="wolf-card">
  <a class="wolf-card-poster" href="/serie/%s" tabindex="-1" aria-hidden="true"><img src="x" data-original="/caratulas/series/VGVk/Ted-Lasso.jpg" alt="%s"></a>
  <div class="wolf-card-content">
    <h3 class="wolf-card-title"><a class="wolf-card-main" href="/serie/%s" aria-label="Ver ficha: %s">%s</a></h3>
    <p class="wolf-card-date">Última subida <time datetime="2026-09-26">26/09/2026</time></p>
  </div>
  <ul class="wolf-card-files" aria-label="Archivos de %s">
    <li class="wolf-card-file">
      <a class="wolf-card-format" href="/serie/episodio/%s"><strong>Episodio %s -</strong><span>%s</span></a>
      <span class="wolf-card-size">7,46 GB</span>
      <button type="button" class="protected-download wolf-card-download" data-content-id="%s" data-tabla="series" aria-label="Descargar">Descargar</button>
    </li>
  </ul>
</article>''' % (sid, titulo, sid, titulo, titulo, titulo, sid, ep, fmt, cid)


BUSQUEDA_TED = ('''<main><header class="wolf-page-heading"><h1>Resultados para «ted lasso»</h1>
<span class="wolf-catalog-total">4 títulos</span></header>
<div class="wolf-catalog-meta"><span>1 a 4 de 4 títulos</span><span>Página 1 de 1</span></div>'''
                + tarjeta_serie("5ecqn5", "Ted Lasso - 4ª Temporada [4k]", "4x07", "4K", "806090")
                + tarjeta_serie("5bgkz2", "Ted Lasso - 4ª Temporada [1080p]", "4x08", "1080p", "806085")
                + tarjeta_serie("fb54bu", "Ted Lasso - 2ª Temporada", "2x12", "HDTV", "756821")
                + tarjeta_serie("2s42pf", "Ted Lasso - 1ª Temporada [720p].", "1x10", "HDTV-720p", "749378")
                + "</main>")


def fila(cid, href, etiqueta, fmt):
    return '''<div id="archivo-%s" class="wolf-episode flex flex-wrap items-center">
      <a class="min-w-0 flex-1 truncate" href="%s">%s</a>
      <span class="wolf-episode-date">26/09/2026</span><span class="wolf-episode-format">%s<span class="wolf-episode-size" aria-label="Tamaño">7,35 GB</span></span>
      <span class="flex items-center gap-2">
      <button type="button" class="protected-download inline-block" data-content-id="%s" data-tabla="series">Descargar</button></span>
    </div>''' % (cid, href, etiqueta, fmt, cid)


def ficha_temporada(titulo, formato, filas):
    return '''<main id="contenido"><div id="ficha"><article class="wolf-detail">
  <div class="wolf-detail-heading"><div class="wolf-detail-poster">
    <img class="wolf-detail-art" width="120" height="165" alt="%s" src="x" data-original="/caratulas/series/VGVk/Ted.webp"></div>
  <div class="wolf-detail-info">
    <p class="text-[11px] font-semibold uppercase text-wolf-accent">Serie · %s</p>
    <h1 class="mt-1.5 break-words font-display text-3xl">%s</h1>
    <dl class="wolf-detail-meta"><div class="flex gap-3"><dt>Formato</dt><dd>%s</dd></div></dl>
  </div></div>
  <h2 class="wolf-synopsis-title">Sinopsis</h2>
  <div class="wolf-synopsis-wrap"><div class="wolf-synopsis" id="wolf-synopsis"><p>Un ingenuo entrenador de fútbol.</p></div></div>
  <div class="wolf-episodes" id="episodios">%s</div>
</article></div></main>''' % (titulo, formato, titulo, formato, "".join(filas))


TEMP4_4K = ficha_temporada("Ted Lasso - 4ª Temporada [4k]", "4K", [
    fila("806087", "/serie/episodio/5d7uyr", "4x01", "4K"),
    fila("806090", "/serie/episodio/5ecqn5", "4x07", "4K")])
TEMP4_1080 = ficha_temporada("Ted Lasso - 4ª Temporada [1080p]", "1080p", [
    fila("806070", "/serie/episodio/aa0001", "4x01", "1080p"),
    fila("806071", "/serie/episodio/aa0002", "4x02", "1080p"),
    fila("806085", "/serie/episodio/5bgkz2", "4x08", "1080p")])
TEMP2 = ficha_temporada("Ted Lasso - 2ª Temporada", "HDTV", [
    fila("756801", "/serie/episodio/fb0001", "2x01", "HDTV"),
    fila("756821", "/serie/episodio/fb54bu", "2x12", "HDTV")])
TEMP1_720 = ficha_temporada("Ted Lasso - 1ª Temporada [720p].", "HDTV-720p", [
    fila("749378", "/serie/episodio/2s42pf", "1x10", "HDTV-720p")])

EPISODIO = '''<main><div id="ficha">
    <nav class="wolf-episode-breadcrumb"><a href="/series">Series</a><span>›</span>
        <a href="/serie/5ecqn5">Ted Lasso - 4ª Temporada [4k]</a><span>›</span><span aria-current="page">Episodio 4x01</span></nav>
    <article class="wolf-detail wolf-episode-detail"><div class="wolf-detail-heading">
      <div class="wolf-detail-poster"><img class="wolf-detail-art" width="120" height="165" alt="x" src="x" data-original="/caratulas/series/VGVk/Ted.webp"></div>
      <div class="wolf-detail-info"><p class="wolf-file-eyebrow">Episodio de serie</p>
        <h1 class="wolf-episode-title">Ted Lasso - 4ª Temporada [4k] <span class="wolf-episode-name">Episodio 4x01</span></h1>
        <a class="wolf-season-link" href="/serie/5ecqn5">Ver temporada</a></div></div>
      <section class="wolf-file-panel" aria-label="Archivo del episodio"><div class="wolf-file-info"><p class="wolf-file-eyebrow">Archivo torrent</p><dl>
        <div><dt>Calidad</dt><dd>4K</dd></div><div><dt>Tamaño del contenido</dt><dd>7,35 GB</dd></div></dl></div>
        <button type="button" class="protected-download wolf-primary-download" data-content-id="806087" data-tabla="series" aria-label="Descargar Episodio 4x01">Descargar episodio</button>
      </section></article></div></main>'''

PELI = '''<main><div id="ficha"><article class="wolf-detail">
  <div class="wolf-detail-heading"><div class="wolf-detail-poster">
    <img class="wolf-detail-art" width="120" height="165" alt="Poli malo" src="x" data-original="/caratulas/peliculas/UG9s/Poli-malo.webp"></div>
  <div class="wolf-detail-info">
    <p class="text-[11px] font-semibold uppercase tracking-[0.22em] text-wolf-accent">Película · BluRay-720p</p>
    <h1 class="mt-1.5 break-words font-display text-3xl">Poli malo</h1>
    <dl class="wolf-detail-meta"><div class="flex gap-3"><dt>Año</dt><dd><a class="wolf-ficha-link" href="/peliculas?anyo=2025">2025</a></dd></div>
    <div class="flex gap-3"><dt>G&eacute;nero</dt><dd><a class="wolf-ficha-link" href="/peliculas?genero=Acci%C3%B3n">Acción</a></dd></div></dl></div></div>
  <section class="wolf-file-panel" aria-label="Archivo de la película">
    <div class="wolf-file-info"><p class="wolf-file-eyebrow">Archivo torrent</p><dl>
      <div><dt>Calidad</dt><dd>BluRay-720p</dd></div><div><dt>Tamaño</dt><dd>3.67 GB</dd></div></dl></div>
    <button type="button" class="protected-download wolf-primary-download" data-content-id="376398" data-tabla="peliculas">Descargar torrent</button></section>
  <div class="wolf-synopsis" id="wolf-synopsis"><p>En Colt Lake Tennessee.</p></div>
</article></div></main>'''


print("\n=== 1) La web nueva, leida ===")
ts = W.tarjetas(TARJETA_PELI)
t = ts[0] if ts else {}
comprueba("la peli sale con TODAS sus versiones (tambien las de 'ver mas')",
          [(a["calidad"], a["cid"]) for a in t.get("archivos", [])]
          == [("1080p", "370072"), ("4K", "370057"), ("DVDRip", "369736")], t.get("archivos"))
comprueba("cada version con SU pagina, y el titulo limpio con su ano",
          t.get("titulo") == "Dune" and t.get("anio") == 2021 and t.get("tipo") == "pelicula"
          and t["archivos"][1]["url"] == "https://wolfmax4k.com/pelicula/d8k2pt"
          and t.get("thumb", "").startswith("https://wolfmax4k.com/caratulas/"), t)
tb = W.tarjetas(BUSQUEDA_TED)
comprueba("la temporada: base, numero, calidad del titulo y su ultimo capitulo",
          [(x["base"], x["temporada"], x["calidad"], x["archivos"][0]["temporada"], x["archivos"][0]["episodio"])
           for x in tb][:2] == [("Ted Lasso", 4, "4K", 4, 7), ("Ted Lasso", 4, "1080p", 4, 8)], tb[:2])
comprueba("paginas: 'Pagina 2 de 961'", W.paginas("<span>Página 2 de 961</span>") == (2, 961)
          and W.paginas(BUSQUEDA_TED) == (1, 1))
d = W.ficha(TEMP4_4K, "https://wolfmax4k.com/serie/5ecqn5")
comprueba("ficha de temporada: sus capitulos con content-id, tabla y calidad",
          d["tipo"] == "serie" and d["base"] == "Ted Lasso" and d["temporada"] == 4 and d["calidad"] == "4K"
          and [(a["etiqueta"], a["cid"], a["tabla"], a["url"][-6:]) for a in d["archivos"]]
          == [("4x01", "806087", "series", "5d7uyr"), ("4x07", "806090", "series", "5ecqn5")], d)
e = W.ficha(EPISODIO, "https://wolfmax4k.com/serie/episodio/5d7uyr")
comprueba("capitulo suelto: titulo sin restos, su temporada y su archivo",
          e["titulo"] == "Ted Lasso - 4ª Temporada [4k]" and e["episodio"] == "Episodio 4x01"
          and e["temporada_url"] == "https://wolfmax4k.com/serie/5ecqn5"
          and [(a["cid"], a["temporada"], a["episodio"], a["calidad"]) for a in e["archivos"]]
          == [("806087", 4, 1, "4K")], e)
p = W.ficha(PELI, "https://www.wolfmax4k.com/pelicula/rw2gwz")
comprueba("peli: calidad, ano, generos, sinopsis y su UNICO archivo (y sin www)",
          p["calidad"] == "720p" and p["anio"] == 2025 and p["generos"] == ["Acción"]
          and p["sinopsis"] == "En Colt Lake Tennessee." and p["url"] == "https://wolfmax4k.com/pelicula/rw2gwz"
          and [(a["cid"], a["tabla"], a["tamano"]) for a in p["archivos"]] == [("376398", "peliculas", "3.67 GB")], p)
comprueba("calidades", [W.calidad(x) for x in ("BluRay-720p", "BDremux-1080p", "4K", "HDTV", "HDTV-720p",
                                               "MicroHD-1080p", "DVDRip", "[4k]", "", "Serie · 4K")]
          == ["720p", "1080p", "4K", "HDTV", "720p", "1080p", "DVDRip", "4K", "", "4K"])
comprueba("la mejor calidad manda", W.rango("4K") > W.rango("1080p") > W.rango("720p") > W.rango("HDTV") > W.rango(""))
comprueba("titulos base", [W.titulo_base(x) for x in ("Ted Lasso - 1ª Temporada [720p].", "Dune.", "Dune ",
                                                      "Reacher - 4ª Temporada [4k]")]
          == ["Ted Lasso", "Dune", "Dune", "Reacher"])
comprueba("rutas: lo nuevo si, lo viejo no",
          [W.ruta(u)[0] for u in ("https://wolfmax4k.com/pelicula/rw2gwz", "https://www.wolfmax4k.com/serie/5ecqn5",
                                  "/serie/episodio/5d7uyr", "https://wolfmax4k.com/documental/episodio/53j97d",
                                  "https://www.wolfmax4k.com/serie-online-4k/156288", "https://wolfmax4k.com/movie/1")]
          == ["pelicula", "serie", "episodio", "episodio_doc", None, None])

print("\n=== 2) El torrent: reto, prueba de trabajo y limite ===")
LLAMADAS = []


def post_bueno(url, cuerpo, cab):
    LLAMADAS.append((url, cuerpo.get("action"), cab.get("Referer")))
    if cuerpo["action"] == "generate":
        return 200, {"success": True, "challenge": "abc123"}
    ok = hashlib.sha256(("abc123" + str(cuerpo["nonce"])).encode()).hexdigest().startswith("000")
    return 200, ({"success": True, "download_url": "//wolfmax4k.com/torrents/peliculas/Poli-malo.torrent"}
                 if ok else {"success": False, "error": "nonce"})


W._TORRENTS.clear()
u = W.torrent(post_bueno, "376398", "peliculas", "https://www.wolfmax4k.com/pelicula/rw2gwz")
comprueba("reto -> prueba de trabajo valida -> .torrent (https y sin www)",
          u == "https://wolfmax4k.com/torrents/peliculas/Poli-malo.torrent"
          and [x[1] for x in LLAMADAS] == ["generate", "validate"]
          and LLAMADAS[0][0] == "https://wolfmax4k.com/api/descargas"
          and LLAMADAS[0][2] == "https://wolfmax4k.com/pelicula/rw2gwz", (u, LLAMADAS))
n = len(LLAMADAS)
comprueba("el mismo archivo otra vez: recordado, sin gastar descarga",
          W.torrent(post_bueno, "376398", "peliculas") == u and len(LLAMADAS) == n)


def post_limite(url, cuerpo, cab):
    LLAMADAS.append(cuerpo.get("action"))
    return 429, {"status": "limit_exceeded", "wait_minutes": 42}


try:
    W.torrent(post_limite, "1", "series")
    comprueba("limite: se dice", False)
except W.Limite as ex:
    comprueba("limite de 60/hora: se dice cuanto esperar", ex.minutos == 42, ex.minutos)
n = len(LLAMADAS)
try:
    W.torrent(post_bueno, "2", "series")
    comprueba("durante el limite ni se pregunta", False)
except W.Limite:
    comprueba("...y mientras dure, ni se le pregunta", len(LLAMADAS) == n, LLAMADAS[n:])
W._LIMITE_HASTA[0] = 0.0


def post_captcha(url, cuerpo, cab):
    if cuerpo["action"] == "generate":
        return 200, {"success": True, "challenge": "zz"}
    return 200, {"status": "captcha_required", "captcha_image": "data:..."}


try:
    W.torrent(post_captcha, "3", "series")
    comprueba("captcha: se dice", False)
except W.Captcha:
    comprueba("si pide captcha, se dice (no se inventa un torrent)", True)
comprueba("enlace diferido de ida y vuelta",
          W.es_diferido(W.diferido("806087", "series")) and W.de_diferido("wf2:series:806087") == ("806087", "series")
          and not W.es_diferido("https://wolfmax4k.com/x.torrent"))

print("\n=== 3) La caja: busqueda, serie completa y ficha ===")
from resources.lib import scraper_wolfmax as S            # noqa: E402
from resources.lib import wf_index as IX                   # noqa: E402

PAGINAS = {
    "/buscar?q=ted%20lasso": BUSQUEDA_TED,
    "/buscar?q=Ted%20Lasso": BUSQUEDA_TED,
    "/buscar?q=dune": TARJETA_PELI,
    "https://wolfmax4k.com/serie/5ecqn5": TEMP4_4K,
    "https://wolfmax4k.com/serie/5bgkz2": TEMP4_1080,
    "https://wolfmax4k.com/serie/fb54bu": TEMP2,
    "https://wolfmax4k.com/serie/2s42pf": TEMP1_720,
    "https://wolfmax4k.com/serie/episodio/5d7uyr": EPISODIO,
    "https://wolfmax4k.com/pelicula/rw2gwz": PELI,
}
PEDIDAS = []


def pide_falso(ruta, timeout=20):
    PEDIDAS.append(ruta)
    if ruta not in PAGINAS:
        raise RuntimeError("404 " + ruta)
    return PAGINAS[ruta]


S._pide = pide_falso
S._post_json = post_bueno
IX._cache = None
viejo_idx = os.path.join(PERFIL, "wf_index.json")
with open(viejo_idx, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "entries": {
        "https://www.wolfmax4k.com/serie-online-4k/156288": {"title": "Ted Lasso [Cap.201]", "kind": "tvshow"},
        "https://wolfmax4k.com/pelicula/abc123": {"title": "Nueva", "kind": "movie"}}}, f)
cargado = IX._load()
comprueba("el indice de la caja tira lo de la web vieja al cargar",
          list(cargado) == ["https://wolfmax4k.com/pelicula/abc123"], list(cargado))

it = S.search("dune")
comprueba("busqueda de una peli: un item POR VERSION, con su calidad y ano",
          [(x["title"], x["kind"], x["quality"], x.get("year"), x["url"][-6:]) for x in it]
          == [("Dune", "movie", "1080p", 2021, "d9db5r"), ("Dune", "movie", "4K", 2021, "d8k2pt"),
              ("Dune", "movie", "DVDRip", 2021, "dptwvd")], it)
it = S.search("ted lasso")
caps = [(x["title"], x["quality"]) for x in it]
comprueba("busqueda de una serie: 'Titulo SxEE' por capitulo, la mejor calidad primero",
          caps[0] == ("Ted Lasso 4x07", "4K") and ("Ted Lasso 2x12", "HDTV") in caps
          and ("Ted Lasso 1x10", "720p") in caps and all(x["kind"] == "tvshow" for x in it), caps)
comprueba("...y lo aprendido va al indice de la caja (URLs nuevas)",
          "https://wolfmax4k.com/serie/episodio/5ecqn5" in IX._load()
          and "https://wolfmax4k.com/pelicula/d8k2pt" in IX._load(), sorted(IX._load())[:4])

del PEDIDAS[:]
r = S.episodios_serie("https://wolfmax4k.com/serie/episodio/5d7uyr")
eps = dict((e["label"], (e["quality"], e["url"][-6:])) for e in r["episodes"])
comprueba("serie completa desde UN capitulo: todas las temporadas y calidades juntas",
          r["title"] == "Ted Lasso" and sorted(eps) == ["1x10", "2x01", "2x12", "4x01", "4x02", "4x07", "4x08"], r)
comprueba("...cada capitulo con la MEJOR calidad (4x01 en 4K, no en 1080p)",
          eps.get("4x01") == ("4K", "5d7uyr") and eps.get("4x02") == ("1080p", "aa0002")
          and eps.get("1x10") == ("720p", "2s42pf"), eps)
comprueba("...y desde la temporada: sin volver a pedir la del capitulo",
          PEDIDAS.count("https://wolfmax4k.com/serie/5ecqn5") == 1, PEDIDAS)
r2 = S.episodios_serie("https://www.wolfmax4k.com/serie-online-4k/156288", "Ted Lasso")
comprueba("un favorito con URL VIEJA: se busca la serie por su nombre",
          len(r2["episodes"]) == 7 and r2["title"] == "Ted Lasso", r2)

W._TORRENTS.clear()
del LLAMADAS[:]
dd = S.detail("https://wolfmax4k.com/serie/5ecqn5")
comprueba("ficha de temporada: capitulos con enlace DIFERIDO (ni una descarga gastada)",
          [(x["label"], x["torrent_url"]) for x in dd["downloads"]]
          == [("4x01", "wf2:series:806087"), ("4x07", "wf2:series:806090")] and not LLAMADAS, dd)
dp = S.detail("https://wolfmax4k.com/pelicula/rw2gwz")
comprueba("ficha de peli: su torrent, ya",
          dp["title"] == "Poli malo" and dp["year"] == 2025 and dp["downloads"][0]["season"] is None
          and dp["downloads"][0]["torrent_url"] == "https://wolfmax4k.com/torrents/peliculas/Poli-malo.torrent", dp)
W._TORRENTS.clear()
del PEDIDAS[:]
tu = S.torrent_de("https://wolfmax4k.com/pelicula/d8k2pt")
comprueba("reproducir una version vista en la busqueda: sin abrir su ficha",
          tu.endswith(".torrent") and not PEDIDAS, (tu, PEDIDAS))
try:
    S.detail("https://www.wolfmax4k.com/serie-online-4k/156288")
    comprueba("URL vieja: se dice", False)
except RuntimeError as ex:
    comprueba("una URL de la web vieja: error claro, sin pedirla", "vieja" in str(ex), ex)

print("\n=== 4) Cuando WolfMax retoca su marcado (28-09) y los packs ===")
# Dos dias despues de estrenar web añadio un atributo a cada archivo de las
# tarjetas y el lector dejo de ver TODOS: series sin capitulos, sin calidades.
# Aqui, esa tarjeta tal cual y otra con los atributos en otro orden y de mas.
HOY = '''<article class="wolf-card"><a class="wolf-card-poster" href="/serie/zryh84" tabindex="-1"><img src="x" data-original="/caratulas/series/VGVk/Ted.jpg" alt="t"></a>
<div class="wolf-card-content"><h3 class="wolf-card-title"><a class="wolf-card-main" href="/serie/zryh84" aria-label="Ver ficha">Ted Lasso - 1ª Temporada [720p].</a></h3></div>
<ul class="wolf-card-files" aria-label="Archivos">
 <li class="wolf-card-file" data-wolf-episode-id="749378"><a class="wolf-card-format" href="/serie/episodio/2s42pf"><strong>Episodio 1x10 -</strong><span>HDTV-720p</span></a>
   <span class="wolf-card-size">1,31 GB</span><button type="button" class="protected-download wolf-card-download" data-content-id="749378" data-tabla="series"><span>Descargar</span></button></li>
 <li data-x="1" class="wolf-card-file nueva"><a href="/serie/episodio/2ap2ud" data-y="2" class="otra wolf-card-format"><strong>Episodio 1x06 al 1x09.</strong><span>HDTV-720p</span></a>
   <span class="wolf-card-size">3,13 GB</span><button data-tabla="series" type="button" data-content-id="749083" class="protected-download">Descargar</button></li>
</ul></article>'''
th = W.tarjetas(HOY)
comprueba("con el atributo nuevo y con todo en otro orden, se leen los dos archivos",
          len(th) == 1 and [(a["cid"], a["tabla"], a["calidad"]) for a in th[0]["archivos"]]
          == [("749378", "series", "720p"), ("749083", "series", "720p")], th)
comprueba("el pack se sabe pack: del 1x06 al 1x09",
          [(a["temporada"], a["episodio"], a["episodio_fin"]) for a in th[0]["archivos"]] == [(1, 10, 0), (1, 6, 9)],
          [(a["etiqueta"], a["episodio_fin"]) for a in th[0]["archivos"]])
comprueba("rotulos: el capitulo suelto como siempre y el pack como lo escribe WolfMax",
          W.etiqueta_ep(1, 10) == "1x10" and W.etiqueta_ep(1, 6, 9) == "1x06 al 1x09"
          and W.episodio_fin("1x09 al 2x01") == 0 and W.episodio_fin("1x00 (PILOTO)") == 0)
roto = HOY.replace("wolf-card-format", "wolf-cambiado")
comprueba("si vuelve a cambiar y no se lee ningun archivo, se nota (para el registro)",
          W.sin_archivos_raro(roto, W.tarjetas(roto)) and not W.sin_archivos_raro(HOY, th))
PAGINAS["https://wolfmax4k.com/serie/zrdqqp"] = ficha_temporada("Ted Lasso - 1ª Temporada [720p]", "HDTV-720p", [
    fila("749001", "/serie/episodio/zrfbxt", "1x01 al 1x03.", "HDTV-720p"),
    fila("749004", "/serie/episodio/zrgx6x", "1x04 -", "HDTV-720p")])
PAGINAS["/buscar?q=Ted%20Lasso"] = BUSQUEDA_TED.replace("</main>", tarjeta_serie(
    "zrdqqp", "Ted Lasso - 1ª Temporada [720p]", "1x04", "HDTV-720p", "749004") + "</main>")
r3 = S.episodios_serie("https://wolfmax4k.com/serie/5ecqn5")
labs = [e["label"] for e in r3["episodes"] if e["season"] == 1]
comprueba("la serie completa rotula el pack y no se come el suelto",
          "1x01 al 1x03" in labs and "1x04" in labs
          and [e.get("episode_end") for e in r3["episodes"] if e["label"] == "1x01 al 1x03"] == [3], labs)

pk = S._items(W.tarjetas(tarjeta_serie("2ap2ud", "Ted Lasso - 1ª Temporada [720p].", "1x06 al 1x09",
                                       "HDTV-720p", "749083")))
comprueba("la busqueda dice donde acaba un pack (2.9.81: antes salia '1x06' a secas)",
          [(x["title"], x.get("episode_end")) for x in pk] == [("Ted Lasso 1x06", 9)], pk)
pk = S._items(W.tarjetas(tarjeta_serie("2s42pf", "Ted Lasso - 1ª Temporada [720p].", "1x10",
                                       "HDTV-720p", "749378")))
comprueba("...y un capitulo suelto no lleva fin", "episode_end" not in pk[0], pk)

del PEDIDAS[:]
PAGINAS["/peliculas"] = TARJETA_PELI
lt = S.latest("movie", 1)
comprueba("lo ultimo en pelis sale de /peliculas (en /ultimos casi todo son series)",
          PEDIDAS == ["/peliculas"] and [x["quality"] for x in lt] == ["1080p", "4K", "DVDRip"], (PEDIDAS, lt))

print("\n=== 5) El menu propio de Kodi (2.9.80) ===")
se = S.search_and_expand("ted lasso")
comprueba("series de Kodi: las temporadas agrupadas (en 2.9.77 llegaba una lista y "
          "el menu decia 'No se encontraron capitulos')",
          isinstance(se, dict) and se.get("title") == "Ted Lasso" and sorted(se.get("seasons") or {}) == [1, 2, 4]
          and all(c.get("url") and c.get("episode") is not None for t in se["seasons"].values() for c in t),
          se if not isinstance(se, dict) else sorted(se.get("seasons") or {}))
DOCU = '''<article class="wolf-card"><a class="wolf-card-main" href="/documental/dd0001">Un documental suelto.</a>
<ul class="wolf-card-files"><li class="wolf-card-file" data-x="1"><a class="wolf-card-format" href="/documental/episodio/dd0002"><strong>Documental Completo - HDTV720p</strong></a>
<button class="protected-download" data-content-id="9001" data-tabla="documentales">Descargar</button></li></ul></article>'''
PAGINAS["/documentales"] = DOCU + BUSQUEDA_TED
docs = S.latest("documentary", 1)
comprueba("documentales: tambien los de un solo archivo (no solo los de capitulos)",
          "Un documental suelto" in [x["title"] for x in docs], [x["title"] for x in docs][:4])

print("\n=== 6) WolfMax pasa a CODIGOS (29-09, madrugada; caja 2.9.82) ===")
# la tarjeta TAL CUAL la sirvio WolfMax a las 01:40: el boton ya no lleva
# data-content-id sino data-content-code, y su API rechaza el id (400)
CODIGOS = '''<article class="wolf-card">
 <a class="wolf-card-poster" href="/serie/zryh84" tabindex="-1" aria-hidden="true"><img src="x" data-original="/caratulas/series/VGVk/Ted%20Lasso-%5BWolfMax4K%5D.jpg" alt="Ted Lasso - 1ª Temporada"></a>
 <div class="wolf-card-content"><h3 class="wolf-card-title"><a class="wolf-card-main" href="/serie/zryh84" aria-label="Ver ficha: Ted Lasso - 1ª Temporada">Ted Lasso - 1ª Temporada</a></h3>
 <p class="wolf-card-date">Última subida <time datetime="2020-10-19">19/10/2020</time></p></div>
 <ul class="wolf-card-files" aria-label="Archivos de Ted Lasso - 1ª Temporada">
  <li class="wolf-card-file" data-wolf-episode-id="749395">
   <a class="wolf-card-format" href="/serie/episodio/2tzkkn"><strong>Episodio 1x10 -</strong><span>HDTV</span></a>
   <span class="wolf-card-size">380,17 MB</span><button type="button" class="protected-download wolf-card-download" data-content-code="2tzkkn" data-tabla="series" aria-label="Descargar Ted Lasso - 1ª Temporada: 1x10 -"><span>Descargar</span></button></li>
  <li class="wolf-card-file" data-wolf-episode-id="749114">
   <a class="wolf-card-format" href="/serie/episodio/2ca9yd"><strong>Episodio 1x06 al 1x09.</strong><span>HDTV</span></a>
   <span class="wolf-card-size">1,43 GB</span><button type="button" class="protected-download wolf-card-download" data-content-code="2ca9yd" data-tabla="series"><span>Descargar</span></button></li>
 </ul>
 <details class="wolf-card-more" data-wolf-episodes="/api/episodios?type=serie&amp;id=748763&amp;after=749114&amp;calidad="><summary>Ver los 3 episodios restantes</summary>
 <ul class="wolf-card-files-extra"><li class="wolf-episode-list-status"><a href="/serie/zryh84">Ver todos los episodios en la ficha</a></li></ul></details>
</article>'''
tc = W.tarjetas(CODIGOS)
comprueba("la busqueda lee los archivos con su CODIGO (antes: 0 archivos, todo sin calidad ni capitulos)",
          [(a["etiqueta"], a["cid"], a["tabla"], a["episodio_fin"]) for a in tc[0]["archivos"]]
          == [("Episodio 1x10 -", "2tzkkn", "series", 0), ("Episodio 1x06 al 1x09.", "2ca9yd", "series", 9)],
          tc[0]["archivos"] if tc else tc)
comprueba("...y el enlace de 'ver mas episodios' no se toma por un archivo", len(tc[0]["archivos"]) == 2)
fc = W.ficha(ficha_temporada("Ted Lasso - 1ª Temporada [720p]", "HDTV-720p", [
    fila("749001", "/serie/episodio/zrfbxt", "1x01 al 1x03.", "HDTV-720p")]).replace(
    'data-content-id="749001"', 'data-content-code="zrfbxt"'), "https://wolfmax4k.com/serie/zrdqqp")
comprueba("la ficha de temporada tambien", [(a["cid"], a["episodio_fin"]) for a in fc["archivos"]] == [("zrfbxt", 3)],
          fc["archivos"])
sin_boton = CODIGOS.replace('data-content-code="2tzkkn" ', "").replace('data-content-code="2ca9yd" ', "")
comprueba("plan C: si el boton vuelve a cambiar, el codigo sale del enlace del archivo",
          [a["cid"] for a in W.tarjetas(sin_boton)[0]["archivos"]] == ["2tzkkn", "2ca9yd"])

API = []


def post_codigos(url, cuerpo, cab):
    """La API de hoy: 'code' si, 'content_id' -> 400."""
    API.append(dict(cuerpo))
    if cuerpo["action"] == "generate":
        if "content_id" in cuerpo or cuerpo.get("code") not in ("2tzkkn", "2ca9yd", "zrfbxt"):
            return 400, {"success": False, "error": "Parámetros inválidos"}
        return 200, {"success": True, "challenge": "abc123"}
    return post_bueno(url, cuerpo, cab)


W._TORRENTS.clear()
u6 = W.torrent(post_codigos, "2tzkkn", "series")
comprueba("el torrent se pide con el codigo: un solo intento",
          u6.endswith(".torrent") and [c for c in API if c["action"] == "generate"]
          == [{"action": "generate", "code": "2tzkkn", "tabla": "series"}], API)
del API[:]
try:
    W.torrent(post_codigos, "749395", "series")
    comprueba("un id numerico de antes: no hay reto", False)
except RuntimeError:
    comprueba("un id numerico de antes prueba como id y como codigo, y lo dice (no se cuelga)",
              [list(c)[1] for c in API] == ["content_id", "code"], API)
comprueba("los enlaces diferidos llevan el codigo",
          W.es_diferido(W.diferido("2tzkkn", "series")) and W.de_diferido("wf2:series:2tzkkn") == ("2tzkkn", "series"))
# un enlace diferido guardado ANTES (id numerico): se saca el codigo de la ficha
PAGINAS["https://wolfmax4k.com/serie/episodio/2tzkkn"] = EPISODIO.replace(
    'data-content-id="806087"', 'data-content-code="2tzkkn"')
S._post_json = post_codigos
S._CID.clear()
W._TORRENTS.clear()
del API[:]
u7 = S.resolver_diferido("wf2:series:806087", "https://wolfmax4k.com/serie/episodio/2tzkkn")
comprueba("un diferido viejo con la url del capitulo: sale con el codigo",
          u7.endswith(".torrent") and {"action": "generate", "code": "2tzkkn", "tabla": "series"} in API, API)
S._CID["https://wolfmax4k.com/serie/episodio/2tzkkn"] = ("806087", "series", "4K")   # recordado de antes
W._TORRENTS.clear()
del API[:]
u8 = S.torrent_de("https://wolfmax4k.com/serie/episodio/2tzkkn")
comprueba("una clave recordada que ya no vale: se vuelve a la ficha",
          u8.endswith(".torrent") and API[-2] == {"action": "generate", "code": "2tzkkn", "tabla": "series"}, API)
S._post_json = post_bueno
from resources.lib import enlacito as EN                   # noqa: E402
FORM = '''<body onload="document.getElementById('GoP22').submit()">
    <form action="https://enlacito.com/#VTJGc2" id="GoP22" method="POST">
        <input type="hidden" name="linkser" value="jbysznk4x.pbz">
        <input type="hidden" name="flow" value="d23fe7630b4745765307031317a1c440785e587943b0c48c">
        <noscript><button type="submit">Continuar</button></noscript>'''
comprueba("enlacito: se mandan TODOS los campos ocultos (desde el 29-09 lleva 'flow'; sin el, 400)",
          EN._campos_ocultos(FORM) == {"linkser": "jbysznk4x.pbz",
                                       "flow": "d23fe7630b4745765307031317a1c440785e587943b0c48c"}
          and EN._campos_ocultos('<input value="x" name="a" type="hidden"><input name="b" type="text" value="y">')
          == {"a": "x"} and EN._campos_ocultos("") == {})

print("\n=== 7) La prueba de trabajo v2 (04-10; caja 2.9.85) ===")
# WolfMax cambio su download.js: 8 rondas, una lista de "nonces", y no se puede
# validar antes de 2 s. Con la v1 contestaba 400 "Parametros invalidos" en
# CADA capitulo nuevo (los que salian eran los que ya estaban guardados).
POW = {"version": 2, "difficulty": 3, "rounds": 8, "min_duration_ms": 600}
V2 = {"t_reto": 0.0, "pendiente": 1, "llamadas": []}


def post_v2(url, cuerpo, cab):
    V2["llamadas"].append(dict(cuerpo))
    if cuerpo["action"] == "generate":
        V2["t_reto"] = time.time()
        return 200, {"success": True, "challenge": "rt42", "pow": dict(POW)}
    if "nonce" in cuerpo or not isinstance(cuerpo.get("nonces"), list):
        return 400, {"success": False, "error": "Parámetros inválidos"}
    ns = cuerpo["nonces"]
    bien = len(ns) == POW["rounds"] and all(
        hashlib.sha256(("rt42:%d:%d" % (c, n)).encode()).hexdigest().startswith("000")
        for c, n in enumerate(ns))
    if not bien:
        return 400, {"success": False, "error": "prueba incorrecta"}
    if time.time() - V2["t_reto"] < POW["min_duration_ms"] / 1000.0:
        return 400, {"success": False, "error": "demasiado rapido"}
    if V2["pendiente"]:
        V2["pendiente"] -= 1
        return 202, {"status": "pow_pending", "retry_after_ms": 300}
    return 200, {"success": True, "external": False,
                 "download_url": "/torrents/series/Ted_Lasso_1_10_HDTV_720p.torrent"}


W._TORRENTS.clear()
t0 = time.time()
u9 = W.torrent(post_v2, "2s42pf", "series")
comprueba("v2: las 8 rondas, sin validar antes del minimo y esperando el 'aun no' -> el .torrent",
          u9 == "https://wolfmax4k.com/torrents/series/Ted_Lasso_1_10_HDTV_720p.torrent", (u9, V2["llamadas"][-1]))
comprueba("...en un tiempo razonable (%.1f s)" % (time.time() - t0), time.time() - t0 < 5)
comprueba("la prueba v2 es la de su download.js: el PRIMER n de cada ronda",
          W.prueba_de_trabajo_v2("rt42", POW)[:1] == [next(n for n in range(100000) if hashlib.sha256(
              ("rt42:0:%d" % n).encode()).hexdigest().startswith("000"))])
comprueba("sin 'pow' en la respuesta, la v1 de siempre (por si WolfMax diera marcha atras)",
          W._TORRENTS.clear() is None and W.torrent(post_bueno, "x1", "peliculas").endswith(".torrent"))

print("\n---- VEREDICTO ----")
if fallos:
    print("%d comprobaciones MAL" % fallos)
    sys.exit(1)
print("TODO OK: la caja lee la web nueva de WolfMax y pide sus torrents")
