"""
MejorWolf Render relay.

Mismas funciones que el Cloudflare Worker /?u= y /wfsearch, pero corriendo
en infraestructura NO-Cloudflare. Como Wolf banea por IP los rangos de
Cloudflare/Render/etc, opcionalmente enruta wolfmax via ScraperAPI (pool
residencial rotante) cuando esta presente la variable SCRAPERAPI_KEY.

Endpoints:
  GET /                     -> ping
  GET /relay?u=<url>        -> proxy generico
  GET /wfsearch?q=<query>   -> busqueda completa wolfmax (GET shell + POST AJAX
                                manteniendo sesion, via ScraperAPI si esta
                                configurada).
"""
import os
import re
import gzip as _gzmod
import hashlib as _hashmod
import threading as _thr   # usado a nivel de modulo desde ~L1483 (_DT_BOX_SEM);
                           # DEBE importarse aqui arriba o el modulo crashea al
                           # cargar (NameError) y gunicorn no levanta -> relay 000.
import requests
import cloudscraper
from urllib.parse import urlencode, quote as urlquote
from flask import Flask, request, Response, jsonify, send_file

app = Flask(__name__)

# === Ahorro de ancho de banda (Render Hobby = 5 GB/mes de egress) ==========
# 2026-07-19: el workspace se SUSPENDIO por agotar los 5 GB. Todo lo textual
# sale ahora comprimido (la pagina de 80 KB -> ~20 KB) y la pagina se sirve
# con ETag/304 (reaperturas ~200 B). NO tocar sin medir el egress.
_GZ_TYPES = ("text/html", "application/json", "text/plain", "text/css",
             "application/javascript", "image/svg+xml", "application/xml",
             "application/manifest+json")


@app.after_request
def _gzip_response(resp):
    try:
        if (resp.status_code != 200 or resp.direct_passthrough
                or resp.headers.get("Content-Encoding")
                or resp.mimetype not in _GZ_TYPES
                or "gzip" not in (request.headers.get("Accept-Encoding")
                                  or "").lower()):
            return resp
        body = resp.get_data()
        if len(body) < 500:
            return resp
        gz = _gzmod.compress(body, 6)
        if len(gz) >= len(body):
            return resp
        resp.set_data(gz)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(gz))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        pass
    return resp

# === ScraperAPI (residential proxy bypass) =================================
# Si esta presente esta env var, todas las peticiones a wolfmax4k pasan por
# ScraperAPI que rota IPs residenciales. Wolf no puede banear porque cada
# request sale por una IP distinta.
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

# Sticky session — necesitamos GET shell + POST AJAX usando la MISMA
# sesion (cookies+token comparten estado). ScraperAPI mantiene cookies
# si pasamos session_number=N (mismo N en ambas requests).
SCRAPERAPI_BASE = "http://api.scraperapi.com"


def _scraperapi_url(target_url, session_number=None, post=False, premium=True):
    """Construye URL de ScraperAPI envolviendo target_url.

    `premium=true` -> IPs residenciales, 25 creditos/request. Necesario solo
    para el AJAX data.find.php (muy protegido). Las paginas de LISTADO del
    catalogo salen con peticion normal (premium=False -> 1 credito), lo que
    reduce el consumo ~25x. Plan free = 1000 creditos/mes.
    """
    params = {
        "api_key":      SCRAPERAPI_KEY,
        "url":          target_url,
        "keep_headers": "true",
        "country_code": "es",
    }
    if premium:
        params["premium"] = "true"
    if session_number is not None:
        params["session_number"] = str(session_number)
    return SCRAPERAPI_BASE + "/?" + urlencode(params)


def _wolf_get(session_number, url, headers=None, timeout=60):
    """GET hacia wolfmax via ScraperAPI (si hay key) o cloudscraper."""
    if SCRAPERAPI_KEY:
        wrapped = _scraperapi_url(url, session_number=session_number)
        return requests.get(wrapped, headers=headers or {}, timeout=timeout)
    cs = _make_scraper()
    return cs.get(url, headers=headers or {}, timeout=timeout)


def _wolf_post(session_number, url, data=None, headers=None, timeout=60):
    """POST hacia wolfmax via ScraperAPI (si hay key) o cloudscraper."""
    if SCRAPERAPI_KEY:
        wrapped = _scraperapi_url(url, session_number=session_number)
        return requests.post(wrapped, data=data, headers=headers or {},
                             timeout=timeout)
    cs = _make_scraper()
    return cs.post(url, data=data, headers=headers or {}, timeout=timeout)


def _make_scraper():
    """Cloudscraper para flujos donde no haya ScraperAPI configurado."""
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )


# === ScraperAPI MODO PROXY (failover anti-baneo para la BUSQUEDA) ===========
# DonTorrent/DivxTotal banean la IP de datacenter de Render. En modo PROXY,
# nuestras propias peticiones (las que ya resuelven Anubis con requests) salen
# por el pool de IPs de ScraperAPI -> no se pueden banear. Se usa SOLO como
# FAILOVER de la busqueda (cuando el directo falla) para no gastar creditos en
# uso normal. NO premium por defecto (1 credito/request; premium=residencial
# ~10-25). El plan free son 1000 creditos/mes -> de sobra para buscar a mano.
try:
    import urllib3 as _urllib3
    _urllib3.disable_warnings()   # modo proxy hace MITM -> verify=False ruidoso
except Exception:
    pass


def _sapi_proxies(premium=False, country="es"):
    """Dict de proxies para `requests`/cloudscraper via ScraperAPI proxy-mode.
    None si no hay key (entonces el llamante NO debe intentar el failover)."""
    if not SCRAPERAPI_KEY:
        return None
    opts = "scraperapi"
    if country:
        opts += ".country_code=" + country
    if premium:
        opts += ".premium=true"
    u = "http://%s:%s@proxy-server.scraperapi.com:8001" % (opts, SCRAPERAPI_KEY)
    return {"http": u, "https": u}


_SAPI_CRED = {"left": None, "ts": 0.0}


def _sapi_credits_ok(min_left=10):
    """True si ScraperAPI tiene saldo suficiente. Cachea 20 min y consulta
    /account (que NO gasta creditos). Evita intentar el failover -y perder
    segundos- cuando el plan free esta agotado (WolfMax se lo come). Se
    reactiva solo cuando vuelve a haber saldo (p.ej. al reiniciarse el mes)."""
    if not SCRAPERAPI_KEY:
        return False
    now = _t.time()
    if _SAPI_CRED["left"] is None or (now - _SAPI_CRED["ts"]) > 1200:
        try:
            r = requests.get("https://api.scraperapi.com/account",
                             params={"api_key": SCRAPERAPI_KEY}, timeout=8)
            _SAPI_CRED["left"] = int((r.json() or {}).get("creditsLeft", 0))
        except Exception:
            pass   # conserva el ultimo valor conocido
        _SAPI_CRED["ts"] = now
    return (_SAPI_CRED["left"] or 0) >= min_left


@app.get("/sapi")
def sapi_status():
    """Saldo de ScraperAPI (NO gasta creditos de scraping). Para vigilar que el
    failover de busqueda no agote el plan free ni rompa WolfMax."""
    if not SCRAPERAPI_KEY:
        return jsonify({"key": False})
    try:
        r = requests.get("https://api.scraperapi.com/account",
                         params={"api_key": SCRAPERAPI_KEY}, timeout=12)
        try:
            acc = r.json()
        except Exception:
            acc = {"raw": (r.text or "")[:200]}
        return jsonify({"key": True, "http": r.status_code, "account": acc})
    except Exception as e:
        return jsonify({"key": True, "error": repr(e)[:140]})


ALLOWED_HOSTS = (
    "mejortorrent",
    "wolfmax4k",
    "dontorrent",
    "divxtotal",
    "elitetorrent",
    "enlacito.com",
    "short-info.link",
    "acortador.es",
    "image.tmdb.org",
    "themoviedb.org",
    "api.themoviedb.org",
    "search.brave.com",
    "duckduckgo.com",
    "html.duckduckgo.com",
)

BROWSER_HEADERS = {
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

SKIP_RESP_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "strict-transport-security",
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
}

PASSTHROUGH_HEADERS = (
    "x-requested-with",
    "origin",
    "accept",
    "accept-language",
)


def host_allowed(target_url: str) -> bool:
    try:
        from urllib.parse import urlparse
        h = urlparse(target_url).hostname or ""
        return any(d in h.lower() for d in ALLOWED_HOSTS)
    except Exception:
        return False


def _serve_page(html):
    """HTML con revalidacion: el navegador SIEMPRE pregunta (los cambios se
    ven al recargar), pero si la pagina no cambio recibe un 304 sin cuerpo
    (~200 B) en vez de los ~80 KB. Ahorro de egress (limite 5 GB/mes)."""
    etag = '"' + _hashmod.md5(html.encode("utf-8")).hexdigest()[:16] + '"'
    if etag in (request.headers.get("If-None-Match") or ""):
        r = Response(status=304)
    else:
        r = Response(html, mimetype="text/html; charset=utf-8")
    r.headers["Cache-Control"] = "no-cache, must-revalidate"
    r.headers["ETag"] = etag
    return r


# ===========================================================================
# PWA: instalable en el movil (icono en la pantalla de inicio, pantalla
# completa). Manifest + service worker + iconos. Todo gratis, sin dependencias.
# ===========================================================================
_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#4aa3ff"/><stop offset="1" stop-color="#0a6bff"/>
</linearGradient></defs>
<rect width="512" height="512" fill="url(#bg)"/>
<g fill="#ffffff">
<path d="M150 120 L210 215 L128 205 Z"/>
<path d="M362 120 L302 215 L384 205 Z"/>
<path d="M158 192 H354 L338 300 L292 366 L256 402 L220 366 L174 300 Z"/>
</g>
<g fill="#0a6bff">
<path d="M200 252 L246 246 L226 282 Z"/>
<path d="M312 252 L266 246 L286 282 Z"/>
<path d="M256 322 L236 356 L276 356 Z"/>
</g></svg>"""

_MANIFEST_JSON = """{
 "name":"MejorWolf","short_name":"MejorWolf",
 "description":"Tu cine y series en casa",
 "start_url":"/","scope":"/","display":"standalone","orientation":"portrait",
 "background_color":"#06070c","theme_color":"#06070c",
 "icons":[
  {"src":"/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any"}
 ]
}"""

_SW_JS = """var C='mw-shell-v17';
self.addEventListener('install',function(e){e.waitUntil(caches.open(C).then(function(c){return c.add('/').catch(function(){})}));self.skipWaiting()});
self.addEventListener('activate',function(e){e.waitUntil(caches.keys().then(function(ks){return Promise.all(ks.map(function(k){if(k!==C)return caches.delete(k)}))}).then(function(){return self.clients.claim()}))});
// Navegacion: red con timeout de 4s -> si el relay va lento/caido, sirve la
// shell CACHEADA al instante (la PWA NUNCA se queda en el splash). Solo cachea
// respuestas OK (nunca un 502). La pagina, ya cargada, gestiona sus datos.
function navResp(req){return new Promise(function(resolve){var done=false;
 var t=setTimeout(function(){if(done)return;caches.match('/').then(function(c){if(c&&!done){done=true;resolve(c)}})},4000);
 fetch(req).then(function(r){if(done)return;if(r&&r.ok){var cp=r.clone();caches.open(C).then(function(c){c.put('/',cp)})}done=true;clearTimeout(t);resolve(r)}).catch(function(){if(done)return;caches.match('/').then(function(c){done=true;clearTimeout(t);resolve(c||new Response('<h1>Sin conexion</h1>',{headers:{'Content-Type':'text/html'}}))})});
});}
self.addEventListener('fetch',function(e){
 var req=e.request;if(req.method!=='GET')return;
 var url=new URL(req.url);
 if(url.pathname.indexOf('/preview')===0)return;
 if(req.mode==='navigate'){e.respondWith(navResp(req));return;}
 if(url.pathname==='/manifest.webmanifest'||url.pathname==='/icon.svg'||url.pathname==='/icon-512.png'){
  e.respondWith(caches.match(req).then(function(r){return r||fetch(req).then(function(rr){if(rr&&rr.ok){var cp=rr.clone();caches.open(C).then(function(c){c.put(req,cp)})}return rr})}));
 }
});
"""


@app.get("/manifest.webmanifest")
def manifest():
    return Response(_MANIFEST_JSON, mimetype="application/manifest+json",
                    headers={"Cache-Control": "max-age=86400"})


@app.get("/sw.js")
def sw_js():
    r = Response(_SW_JS, mimetype="application/javascript",
                 headers={"Cache-Control": "no-cache"})
    r.add_etag()
    return r.make_conditional(request)


@app.get("/icon.svg")
def icon_svg():
    return Response(_ICON_SVG, mimetype="image/svg+xml",
                    headers={"Cache-Control": "max-age=604800"})


@app.get("/icon-512.png")
def icon_png():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon-512.png")
    try:
        return send_file(p, mimetype="image/png",
                         max_age=604800)
    except Exception:
        return Response(_ICON_SVG, mimetype="image/svg+xml")


@app.get("/")
def root():
    # La app principal (el catalogo) vive en la raiz. El keepalive pinguea aqui
    # e ignora el cuerpo, asi que servir HTML es inofensivo.
    return _serve_page(_CAT_PAGE)


@app.get("/ping")
def ping():
    return Response("MejorWolf relay OK. ScraperAPI=" +
                    ("ON" if SCRAPERAPI_KEY else "OFF") + " build=dtbl05",
                    mimetype="text/plain")


# === Kodi repo proxy =======================================================
# Sirve los archivos del repositorio Kodi proxeando desde GitHub raw.
# Esto resuelve el problema de Kodi para Windows que no puede negociar TLS
# con raw.githubusercontent.com (probablemente por TLS 1.3 o HTTP/2).
# Render usa HTTPS estandar con Let's Encrypt y HTTP/1.1 — compatible con
# CUALQUIER version de Kodi.
_REPO_BASE = ("https://raw.githubusercontent.com/"
              "tictacfit01-source/dontorrent-kodi/main/repo")


@app.route("/repo/<path:filepath>", methods=["GET", "HEAD"])
def repo_proxy(filepath):
    """Proxy del repositorio Kodi.

    GET /repo/addons.xml
    GET /repo/addons.xml.md5
    GET /repo/plugin.video.mejorwolf/plugin.video.mejorwolf-2.4.2.zip
    """
    url = f"{_REPO_BASE}/{filepath}"
    try:
        r = requests.get(url, timeout=30, stream=False,
                         headers={"User-Agent": "MejorWolfRelay/1.0",
                                  "Accept": "*/*"})
    except Exception as e:
        return Response(f"upstream error: {e}", status=502,
                        mimetype="text/plain")

    # Decidir Content-Type segun extension
    ct = r.headers.get("Content-Type", "")
    if filepath.endswith(".xml"):
        ct = "application/xml; charset=utf-8"
    elif filepath.endswith(".md5"):
        ct = "text/plain; charset=utf-8"
    elif filepath.endswith(".zip"):
        ct = "application/zip"
    elif filepath.endswith(".png"):
        ct = "image/png"
    elif filepath.endswith(".jpg") or filepath.endswith(".jpeg"):
        ct = "image/jpeg"

    headers = {
        "Content-Type": ct,
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=300",  # 5 min, suficiente para Kodi
        "X-MW-Repo-Upstream": str(r.status_code),
    }
    return Response(r.content, status=r.status_code, headers=headers)


@app.route("/relay", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
def relay():
    target = request.args.get("u", "")
    if not target:
        return Response("missing u", status=400)
    if not host_allowed(target):
        return Response("Host not allowed: " + target, status=403)

    fwd = dict(BROWSER_HEADERS)
    cookie = request.headers.get("cookie")
    if cookie:
        fwd["Cookie"] = cookie
    ct = request.headers.get("content-type")
    if ct:
        fwd["Content-Type"] = ct
    ref = request.headers.get("referer")
    if ref and host_allowed(ref):
        fwd["Referer"] = ref
    for name in PASSTHROUGH_HEADERS:
        v = request.headers.get(name)
        if v:
            canon = "-".join(p.capitalize() for p in name.split("-"))
            fwd[canon] = v

    body = request.get_data() if request.method not in ("GET", "HEAD") else None
    is_wolf = "wolfmax4k" in target.lower()
    # MejorTorrent tambien esta tras Cloudflare -> cloudscraper (como WolfMax).
    is_mt = "mejortorrent" in target.lower()
    try:
        if is_wolf and SCRAPERAPI_KEY:
            wrapped = _scraperapi_url(target, session_number=None)
            r = requests.request(request.method, wrapped, headers=fwd,
                                 data=body, timeout=60, allow_redirects=True)
        elif is_wolf or is_mt:
            cs = _make_scraper()
            r = cs.request(request.method, target, headers=fwd, data=body,
                           timeout=30, allow_redirects=True)
        else:
            r = requests.request(request.method, target, headers=fwd,
                                 data=body, timeout=25, allow_redirects=True,
                                 stream=False)
    except Exception as e:
        return Response("relay error: " + e.__class__.__name__ + ": " + str(e),
                        status=502)

    out_headers = {}
    for k, v in r.headers.items():
        if k.lower() in SKIP_RESP_HEADERS:
            continue
        out_headers[k] = v
    out_headers["Access-Control-Allow-Origin"] = "*"
    out_headers["X-MW-Render-Status"] = str(r.status_code)
    out_headers["X-MW-Render-Final"] = r.url
    out_headers["X-MW-Via-Scraperapi"] = "1" if (is_wolf and SCRAPERAPI_KEY) else "0"
    return Response(r.content, status=r.status_code, headers=out_headers)


_TOKEN_RE = re.compile(
    r'name=["\']?token["\']?\s+value=["\']([^"\']+)', re.I,
)


# === WolfMax catalogo cacheado (fallback cuando data.find.php falla) ========
# El AJAX data.find.php esta bloqueado por Cloudflare para datacenters.
# Como fallback, crawleamos las secciones de listado (que SI renderizan
# server-side el top-100 de cada categoria) via ScraperAPI, y cacheamos el
# resultado 30 min. Asi el addon hace UNA llamada rapida y obtiene todo el
# catalogo reciente (incluido /documentales -> "Rafa").
import time as _wt
import unicodedata as _wud

_WF_CATALOG_SECTIONS = [
    "/series/1080p/", "/series/4k-2160p/", "/series/720p/",
    "/series/", "/animacion-manga/",
    "/peliculas/bluray-1080p/", "/peliculas/4k-2160p/",
    "/peliculas/bluray-720p/", "/peliculas/bluray/",
    "/documentales/", "/programas-tv/",
]
# Captura el bloque entero de cada tarjeta: href + img-src + (todo el HTML
# interior, de donde sacamos card-title y card-text con el Cap. N).
_WF_BLOCK_RE = re.compile(
    r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>'
    r'((?:(?!</a>).){0,1500}?)'
    r'<img[^>]+src=["\']([^"\']+)["\']'
    r'((?:(?!</a>).){0,600})',
    re.IGNORECASE | re.DOTALL,
)
_WF_CARDTITLE_RE = re.compile(
    r'card-title[^>]*>([^<]{1,80})</', re.IGNORECASE)
_WF_CARDTEXT_RE = re.compile(
    r'card-text[^>]*>([^<]{0,40})</', re.IGNORECASE)
_WF_PLAYABLE_RE = re.compile(
    r"/(movie|online|pelicula|capitulo|episodio|serie-online(?:-[\w-]+)?)/\d+",
    re.I,
)
_wf_catalog_cache = {"ts": 0.0, "items": []}
_WF_CATALOG_TTL = 6 * 3600  # 6h: catalogo cambia poco, ahorra creditos


def _wf_norm(s):
    if not s:
        return ""
    s = _wud.normalize("NFKD", s)
    s = "".join(c for c in s if not _wud.combining(c))
    return re.sub(r"\s+", " ", s.lower().strip())


def _wf_title_from_img(img_src):
    """Extrae titulo del nombre de la imagen, igual que el scraper Kodi."""
    try:
        leaf = img_src.rsplit("/", 1)[-1]
        leaf = re.sub(r"\.(jpg|jpeg|png|webp).*$", "", leaf, flags=re.I)
        m = re.match(r"^\d+_\d+-(.+)$", leaf)
        if m:
            return re.sub(r"-+", " ", m.group(1)).strip()
        leaf = re.sub(r"_\d+_\d+$", "", leaf)
        title = re.sub(r"-+", " ", leaf)
        title = re.sub(r"\b(?:bluray|blu-ray|hdtv|web-?dl|hdrip|dvdrip|"
                       r"\d{3,4}p|4k|2160p|1080p|720p|480p|esp|latino|"
                       r"hdr|x264|x265|hevc)\b", "", title, flags=re.I)
        return re.sub(r"\s+", " ", title).strip()
    except Exception:
        return ""


def _wf_build_catalog():
    """Crawlea las secciones de listado via ScraperAPI. Cache 30 min."""
    now = _wt.time()
    if (now - _wf_catalog_cache["ts"]) < _WF_CATALOG_TTL and _wf_catalog_cache["items"]:
        return _wf_catalog_cache["items"], True  # (items, from_cache)

    base = "https://www.wolfmax4k.com"

    _diag_status = {}

    def _fetch(path):
        out = []
        try:
            url = base + path
            # Catalogo = paginas de listado publicas. Las servimos con
            # CLOUDSCRAPER (GRATIS, ilimitado), NO con ScraperAPI. Las
            # paginas de listado superan el anti-bot de Cloudflare con
            # cloudscraper sin gastar creditos. ScraperAPI solo se reserva
            # para el AJAX data.find.php (que cloudscraper no puede). Asi
            # el catalogo (Cine/Series/Documentales/busqueda WF) es gratis.
            cs = _make_scraper()
            r = cs.get(url, headers=BROWSER_HEADERS, timeout=30)
            _diag_status[path] = r.status_code
            if r.status_code != 200:
                return out
            txt = r.content.decode("utf-8", "ignore")
            for href, mid, img_src, tail in _WF_BLOCK_RE.findall(txt):
                low_img = img_src.lower()
                if "logo" in low_img or "/temp/img/" in low_img:
                    continue
                full = href.strip()
                if full.startswith("//"):
                    full = "https:" + full
                elif full.startswith("/"):
                    full = base + full
                if "wolfmax4k" not in full.lower():
                    continue
                # Titulo REAL desde el card-title del HTML (mejor que el
                # nombre de la imagen). El card-text suele traer "Cap. N".
                inner = mid + tail
                ct = _WF_CARDTITLE_RE.search(inner)
                base_title = (ct.group(1).strip() if ct else "") \
                    or _wf_title_from_img(img_src)
                cap = _WF_CARDTEXT_RE.search(inner)
                cap_txt = (cap.group(1).strip() if cap else "")
                # Componer "Rafa - Cap. 104" para distinguir capitulos
                if cap_txt and re.search(r"\d", cap_txt):
                    title = f"{base_title} - {cap_txt}"
                else:
                    title = base_title
                if not title:
                    title = full.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                if len(title) < 2:
                    continue
                img_full = img_src
                if img_full.startswith("//"):
                    img_full = "https:" + img_full
                elif img_full.startswith("/"):
                    img_full = base + img_full
                out.append({"url": full, "title": title, "image": img_full})
            _diag_status[path] = f"{r.status_code}/items={len(out)}"
        except Exception as e:
            _diag_status[path] = f"ERR:{e.__class__.__name__}:{str(e)[:60]}"
        return out

    # ScraperAPI free permite solo 5 hilos concurrentes -> usamos 3 para
    # no toparnos con el limite (que devuelve 429 y descarta secciones).
    from concurrent.futures import ThreadPoolExecutor as _TPE
    items, seen = [], set()
    with _TPE(max_workers=3) as pool:
        for batch in pool.map(_fetch, _WF_CATALOG_SECTIONS):
            for it in batch:
                if it["url"] not in seen:
                    seen.add(it["url"])
                    items.append(it)

    _wf_catalog_cache["last_status"] = _diag_status
    if items:  # solo cacheamos si obtuvimos algo
        _wf_catalog_cache["ts"] = now
        _wf_catalog_cache["items"] = items
    return items, False


def _wf_catalog_search(query):
    """Busca query en el catalogo cacheado. Devuelve items normalizados.

    Prioriza URLs REPRODUCIBLES (/online/<id>, /movie/<id>, etc.). Los
    'landing' tipo /series/<slug> sin id no se pueden reproducir y su
    fan-out suele dar 0 caps -> se descartan salvo que no haya nada mejor.
    """
    q_tokens = [t for t in re.split(r"[\s\-\._]+", _wf_norm(query)) if len(t) >= 2]
    if not q_tokens:
        return []
    catalog, _ = _wf_build_catalog()
    playable, landings = [], []
    for it in catalog:
        title_n = _wf_norm(it.get("title") or "")
        url = it.get("url") or ""
        slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
        slug_n = _wf_norm(slug.replace("-", " "))
        if not all(tok in (title_n + " | " + slug_n) for tok in q_tokens):
            continue
        entry = {
            "url": url,
            "title": it["title"],
            "image": it.get("image"),
            "quality": None,
            "guid": url.rstrip("/").rsplit("/", 1)[-1],
        }
        if _WF_PLAYABLE_RE.search(url):
            playable.append(entry)
        else:
            landings.append(entry)
    # Si hay reproducibles, devolvemos SOLO esos (evita entradas rotas).
    # Si no, devolvemos los landings como ultimo recurso.
    return playable if playable else landings


@app.get("/wfcatalog")
def wfcatalog():
    """Busqueda WolfMax via catalogo cacheado (rapido, sin depender del
    AJAX bloqueado). GET /wfcatalog?q=rafa -> {response, items}."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"response": False, "error": "missing q"}), 400
    try:
        items = _wf_catalog_search(q)
        _, from_cache = _wf_build_catalog()
        return jsonify({"response": True, "items": items,
                        "_diag": {"cached": from_cache,
                                  "catalog_size": len(_wf_catalog_cache["items"]),
                                  "section_status": _wf_catalog_cache.get("last_status", {})}})
    except Exception as e:
        import traceback
        return jsonify({"response": False, "error": str(e),
                        "tb": traceback.format_exc()[-500:]}), 502


@app.get("/wfsearch")
def wfsearch():
    """Busqueda dedicada wolfmax: GET shell + POST data.find.php manteniendo
    sesion via ScraperAPI session_number (cookies+token consistentes entre
    ambas requests)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return Response("missing q", status=400)
    pg = request.args.get("pg") or "1"
    limit = request.args.get("l") or "100"
    raw_mode = request.args.get("raw") == "1"

    base = "https://www.wolfmax4k.com"
    diag = {"phase": "init", "scraperapi": bool(SCRAPERAPI_KEY)}

    # Sesion sticky por query — asi misma IP+cookies entre GET y POST
    import hashlib
    session_number = int(hashlib.sha1(q.encode()).hexdigest()[:8], 16) % 1000

    try:
        # 1) GET HOMEPAGE para harvest token + cookie.
        # IMPORTANTE: Chrome envia Referer "/" (home), no /buscar/<q>.
        # El token CSRF que valida data.find.php proviene del form ffind
        # de la HOME, no del de la pagina de busqueda. Usar la URL
        # equivocada hace que el server rechaze con "Denied".
        diag["phase"] = "shell"
        diag["session_number"] = session_number
        shell_url = base + "/"
        r0 = _wolf_get(session_number, shell_url,
                       headers={**BROWSER_HEADERS}, timeout=70)
        diag["shell_status"] = r0.status_code
        diag["shell_bytes"] = len(r0.content)
        text = r0.text
        m = _TOKEN_RE.search(text)
        token = m.group(1) if m else ""
        diag["token"] = "ok" if token else "miss"
        if not token:
            return jsonify({"response": False,
                            "data": {"error": "no token"},
                            "_diag": diag,
                            "_html_sample": text[:400]}), 502

        # Capturar PHPSESSID y cualquier cookie del shell. Los pasamos
        # explicitamente al POST porque ScraperAPI puede no propagar
        # cookies entre requests al mismo session_number.
        cookies_to_fwd = []
        for c in r0.cookies:
            cookies_to_fwd.append(f"{c.name}={c.value}")
        # ScraperAPI tambien puede devolver Set-Cookie en headers raw
        sc_header = r0.headers.get("Set-Cookie") or ""
        for piece in sc_header.split(","):
            mm = re.match(r"^\s*([^=;\s]+)=([^;]*)", piece)
            if mm and mm.group(1) not in [c.split("=")[0] for c in cookies_to_fwd]:
                cookies_to_fwd.append(f"{mm.group(1)}={mm.group(2)}")
        cookie_header = "; ".join(cookies_to_fwd)
        diag["cookies"] = cookie_header[:120]

        # 2) POST AJAX -> data.find.php
        # Replica EXACTA del request de Chrome capturado con DevTools:
        # - Sin www en el host
        # - multipart/form-data (NO urlencoded)
        # - Campos: token, cidr=0, c=0, q, l, pg  (NO _ACTION!)
        # - SIN X-Requested-With
        # - Referer: home (NO /buscar/<q>)
        # - Accept: */*
        diag["phase"] = "ajax"
        ajax_url = "https://wolfmax4k.com/mvc/controllers/data.find.php"
        # Construir multipart/form-data manualmente con boundary tipo Chrome
        boundary = "----WebKitFormBoundaryyMTCxsxFHq3bxBSN"
        crlf = "\r\n"
        parts = []
        for name, value in [
            ("token", token),
            ("cidr",  "0"),
            ("c",     "0"),
            ("q",     q),
            ("l",     limit),
            ("pg",    pg),
        ]:
            parts.append(f"--{boundary}{crlf}"
                         f"Content-Disposition: form-data; name=\"{name}\"{crlf}{crlf}"
                         f"{value}{crlf}")
        parts.append(f"--{boundary}--{crlf}")
        body = "".join(parts).encode("utf-8")
        ajax_headers = {
            "Accept":            "*/*",
            "Accept-Language":   "es-ES,es;q=0.9",
            "Origin":            "https://www.wolfmax4k.com",
            "Referer":           "https://www.wolfmax4k.com/",
            "Content-Type":      f"multipart/form-data; boundary={boundary}",
            "Priority":          "u=1, i",
            "Sec-Ch-Ua":
                '"Google Chrome";v="147", "Not.A/Brand";v="8", '
                '"Chromium";v="147"',
            "Sec-Ch-Ua-Mobile":   "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest":    "empty",
            "Sec-Fetch-Mode":    "cors",
            "Sec-Fetch-Site":    "same-site",
            "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/147.0.0.0 Safari/537.36",
        }
        # NOTA: Chrome NO envia Cookie en su POST a data.find.php (verificado
        # via DevTools). Si la enviamos podemos romper la validacion del token.
        # Asi que omitimos cookie_header aqui aunque la tengamos.
        # POST con body raw (no data=dict, para mantener el body multipart exacto)
        if SCRAPERAPI_KEY:
            wrapped = _scraperapi_url(ajax_url, session_number=session_number)
            r1 = requests.post(wrapped, data=body, headers=ajax_headers,
                               timeout=70)
        else:
            cs = _make_scraper()
            r1 = cs.post(ajax_url, data=body, headers=ajax_headers, timeout=70)
        diag["ajax_status"] = r1.status_code
        diag["ajax_bytes"] = len(r1.content)
        diag["ajax_text_sample"] = (r1.text or "")[:200]
        try:
            data = r1.json()
        except Exception:
            data = None

        if raw_mode:
            return Response(
                r1.content, status=r1.status_code,
                mimetype=r1.headers.get("content-type", "application/json"),
                headers={"Access-Control-Allow-Origin": "*",
                         "X-MW-Diag": str(diag)},
            )

        if not data or not data.get("response"):
            return jsonify({"response": False, "data": data,
                            "_diag": diag}), 200

        # Normalizar a items playables
        out = []
        datafinds = (data.get("data") or {}).get("datafinds") or {}
        if isinstance(datafinds, list):
            buckets = datafinds
        else:
            buckets = [datafinds.get(str(i)) for i in range(20)
                       if datafinds.get(str(i))]
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            for k in sorted(bucket.keys(),
                            key=lambda x: int(x) if str(x).isdigit() else 0):
                it = bucket[k]
                if not isinstance(it, dict):
                    continue
                guid = (it.get("guid") or "").strip().lstrip("/")
                if not guid:
                    continue
                full_url = base + "/" + guid
                out.append({
                    "url":     full_url,
                    "title":   (it.get("torrentName") or "").strip(),
                    "image":   it.get("image"),
                    "quality": it.get("calidad"),
                    "guid":    guid,
                })

        return jsonify({
            "response": True,
            "items":    out,
            "_diag":    diag,
        })

    except Exception as e:
        diag["error"] = e.__class__.__name__ + ": " + str(e)
        return jsonify({"response": False, "_diag": diag}), 502


# === DonTorrent endpoints =================================================
# El Worker de Cloudflare no puede resolver Anubis PoW (excede 10ms CPU).
# Aqui en Render NO hay limite CPU agresivo asi que podemos hacer PoW
# completo (typically 1-5s for difficulty 5).
#
# Ademas: el ISP español bloquea DNS de DonTorrent. Como Render esta fuera
# de España, puede resolver y conectar sin problema. Esto es la solucion
# definitiva para que DT funcione desde Android TV Box.

import hashlib as _hl
import json as _json
import time as _t
import re as _re_dt
from urllib.parse import quote as _uq


DT_FALLBACK = [
    # El canonico VIGENTE va primero (rotacion: .science -> .review ->
    # .management -> .supply, comprobado end-to-end el 12-09-2026 desde la IP
    # residencial: .management hace 301 a .supply). El auto-curativo lo corrige
    # en vivo, pero arrancar en frio por el bueno evita ese redirect en CADA
    # deploy (con /tmp recien borrado), y un 301 convierte el POST del PoW en
    # GET -> 405 -> "no se puede reproducir".
    "dontorrent.supply",
    "dontorrent.management", "dontorrent.review", "dontorrent.support",
    "dontorrent.science", "dontorrent.irish", "dontorrent.club",
    "dontorrent.info", "dontorrent.istanbul", "dontorrent.lighting",
    "dontorrent.reisen",
]

# --- Auto-deteccion del dominio oficial vigente (AUTO-CURATIVO) --------------
# DonTorrent rota de dominio cada cierto tiempo; los viejos hacen 301 al nuevo y
# CADA pagina lleva el dominio oficial en su schema.org. Tras una busqueda CON
# resultados lo extraemos y lo recordamos en /tmp -> la proxima vez vamos
# directos. En arranque en frio (/tmp vacio) se re-descubre en la 1a busqueda.
# Asi, cuando roten de dominio, el buscador se arregla solo sin tocar nada.
_DT_DOMAIN_FILE = "/tmp/mw_dt_domain.txt"
_DT_HOST_RE = _re_dt.compile(r"^(?:www\.)?(dontorrent\.[a-z]{2,12})$", _re_dt.I)


def _dt_valid_host(host):
    """Solo acepta el apex 'dontorrent.<tld>' (con o sin www). Rechaza clones
    tipo 'dontorrent.evil.com' y hosts ajenos (imagenes, CDNs, etc.)."""
    if not host:
        return None
    m = _DT_HOST_RE.match(host.strip().lower())
    return m.group(1) if m else None


def _dt_load_domain():
    try:
        with open(_DT_DOMAIN_FILE, "r", encoding="utf-8") as f:
            return _dt_valid_host(f.read().strip())
    except Exception:
        return None


def _dt_save_domain(host):
    host = _dt_valid_host(host)
    if not host:
        return
    try:
        tmp = _DT_DOMAIN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(host)
        os.replace(tmp, _DT_DOMAIN_FILE)
    except Exception:
        pass


def _dt_discover_canonical(html):
    """Extrae el dominio oficial actual del HTML de una pagina de DonTorrent.
    Señales, de mas a menos fiable: target del SearchAction (schema.org), url del
    WebSite (schema.org), <link rel=canonical>, og:url. Devuelve 'dontorrent.X' o
    None. Cada candidato se valida con _dt_valid_host; ademas solo se llama sobre
    paginas que YA dieron resultados, asi que no puede adoptar un dominio basura."""
    if not html:
        return None
    for pat in (
        r'"target"\s*:\s*"https?://([^/"]+)/buscar',
        r'"url"\s*:\s*"https?://([^/"]+?)/?"',
        r'rel=["\']canonical["\'][^>]*href=["\']https?://([^/"\']+)',
        r'property=["\']og:url["\'][^>]*content=["\']https?://([^/"\']+)',
    ):
        for host in _re_dt.findall(pat, html, _re_dt.I):
            valid = _dt_valid_host(host)
            if valid:
                return valid
    return None


# Cache de cookies Anubis en memoria (proceso). Funciona porque Render mantiene
# la misma IP de salida — las cookies IP-bound siguen siendo validas.
_DT_COOKIES = {}   # domain -> {"cookies": {}, "ts": ...}
_DT_TTL = 3600 * 6  # 6h
# La sesion Anubis (cookie del PoW ya resuelto) se COMPARTE entre los 2 workers
# via /tmp: el PoW (~5-13s) lo paga UN solo worker y el otro lee la cookie del
# disco -> tras un deploy ya no lo pagan los dos. La cookie va atada a la IP de
# salida (compartida por ambos workers en Render) -> valida cross-worker.
_DT_COOKIES_FILE = "/tmp/mw_dt_anubis.json"


def _dt_cookies_load():
    try:
        with open(_DT_COOKIES_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _dt_cookies_persist(domain, ent):
    """Escribe la cookie de `domain` al fichero compartido (atomico: tmp+replace)."""
    try:
        d = _dt_cookies_load()
        d[domain] = ent
        tmp = _DT_COOKIES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _DT_COOKIES_FILE)
    except Exception:
        pass


def _dt_parse_challenge(html):
    """Extrae el challenge Anubis del HTML."""
    m = _re_dt.search(r'<script\s+id=["\']anubis_challenge["\'][^>]*>(.*?)</script>',
                       html, _re_dt.S)
    if m:
        try:
            return _json.loads(m.group(1).strip())
        except Exception:
            pass
    m = _re_dt.search(r'const\s+challenge_data\s*=\s*(\{.*?\});', html, _re_dt.S)
    if m:
        try:
            return _json.loads(m.group(1).strip())
        except Exception:
            pass
    return None


def _dt_solve_pow(random_data, difficulty):
    """Resuelve PoW Anubis: busca nonce tal que sha256(random_data+nonce)
    empiece con `difficulty` ceros HEX. Comparamos en BYTES (digest) en vez de
    generar el hexdigest de 64 chars y un str nuevo en CADA vuelta: en dificultad
    5 son ~1M de hashes y en la CPU de Render free (~0,1 CPU) eso eran ~13s. Con
    digest+comparacion de bytes (y la base codificada 1 sola vez) baja a ~5s. El
    resultado (hexdigest del hash ganador) es identico al de antes."""
    base = str(random_data).encode()
    full = difficulty // 2          # bytes que deben ser 0x00 enteros
    half = difficulty & 1           # +1 nibble alto (medio byte) a 0 si impar
    zeros = b"\x00" * full
    t0 = _t.time()
    for nonce in range(50_000_000):
        d = _hl.sha256(base + str(nonce).encode()).digest()
        if d[:full] == zeros and (not half or d[full] < 0x10):
            return d.hex(), nonce, _t.time() - t0
    raise RuntimeError("PoW: nonce no encontrado")


def _dt_anubis_session(domain, force=False):
    """Devuelve session con cookies Anubis resueltas para `domain`. Reusa la
    cache en memoria y, si esta vacia (worker frio / la resolvio el OTRO worker),
    la del disco compartido (/tmp) -> el PoW lo paga 1 solo worker. force=True
    ignora AMBAS caches y RE-resuelve (cookie caducada/invalida)."""
    if not force:
        cached = _DT_COOKIES.get(domain)
        if not (cached and (_t.time() - cached["ts"]) < _DT_TTL):
            disk = _dt_cookies_load().get(domain)   # ¿la resolvio el otro worker?
            if (disk and disk.get("cookies")
                    and (_t.time() - disk.get("ts", 0)) < _DT_TTL):
                _DT_COOKIES[domain] = disk
                cached = disk
        if cached and (_t.time() - cached["ts"]) < _DT_TTL:
            s = requests.Session()
            s.cookies.update(cached["cookies"])
            s.headers.update(BROWSER_HEADERS)
            return s, False  # False = no resolvio

    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    r = s.get(f"https://{domain}/", timeout=6)
    if "anubis_challenge" not in r.text:
        # No hay Anubis activo
        ent = {"cookies": dict(s.cookies), "ts": _t.time()}
        _DT_COOKIES[domain] = ent
        _dt_cookies_persist(domain, ent)
        return s, False

    ch = _dt_parse_challenge(r.text)
    if not ch:
        raise RuntimeError("Anubis: challenge no parseable")
    chal = ch.get("challenge", {})
    rules = ch.get("rules", {})
    rand = chal.get("randomData", "")
    diff = rules.get("difficulty", chal.get("difficulty", 5))
    cid  = chal.get("id", "")
    if not rand or not cid:
        raise RuntimeError("Anubis: datos incompletos")

    h, nonce, elapsed = _dt_solve_pow(rand, diff)
    pass_url = (
        f"https://{domain}/.within.website/x/cmd/anubis/api/pass-challenge"
        f"?response={h}&nonce={nonce}&id={_uq(cid, safe='')}"
        f"&elapsedTime={int(elapsed*1000)}&redir=/"
    )
    s.get(pass_url, timeout=8, allow_redirects=False)

    if "browser-pow-auth" not in s.cookies:
        raise RuntimeError("Anubis: pass-challenge no devolvio cookie auth")

    ent = {"cookies": dict(s.cookies), "ts": _t.time()}
    _DT_COOKIES[domain] = ent
    _dt_cookies_persist(domain, ent)   # comparte con el otro worker (no repaga PoW)
    return s, True


@app.route("/dtsearch", methods=["GET", "POST"])
def dtsearch():
    """Busqueda DonTorrent con Anubis solver server-side.

    GET  /dtsearch?q=ozark[&domain=dontorrent.science]
    POST /dtsearch  body: {"q":"ozark","domain":"..."}

    Devuelve HTML de /buscar (igual que el sitio responderia).
    El addon parsea los items con su _parse_items normal.
    """
    if request.method == "POST":
        try:
            body = request.get_json(silent=True) or {}
        except Exception:
            body = {}
        q = (body.get("q") or "").strip()
        preferred = (body.get("domain") or "").strip()
    else:
        q = (request.args.get("q") or "").strip()
        preferred = (request.args.get("domain") or "").strip()

    if not q:
        return jsonify({"error": "missing q"}), 400

    # Si DonTorrent esta baneando la IP de Render (breaker compartido ON),
    # RENDIRSE AL INSTANTE con 502. Asi el addon de Kodi NO se queda 60s
    # esperando al relay y cae enseguida a su DoH residencial (IP de casa, NO
    # baneada) -> Kodi busca igual. Sin esto, /dtsearch colgaba 30s/dominio y
    # Kodi "no buscaba nada". El breaker se auto-resetea en 90s.
    if _dt_is_down():
        return jsonify({"error": "dt_down"}), 502

    diag = {"phase": "init", "preferred": preferred}
    try:
        # Lista de dominios a probar: preferido primero, luego los fallback.
        # Algunos mirrors (ej. science) tienen el buscador desincronizado y
        # devuelven 0 resultados aunque la home funcione. Por eso probamos
        # varios hasta que uno devuelva resultados de verdad.
        dom_candidates = []
        if preferred:
            dom_candidates.append(preferred)
        # El dominio APRENDIDO (auto-detectado en la ultima busqueda con
        # resultados) va primero: es el oficial vigente. Luego las semillas.
        # Aunque la semilla este obsoleta, el 301->re-POST y la auto-deteccion
        # (schema.org) acaban llevandonos al dominio bueno y lo recordamos.
        for d in [_dt_load_domain()] + DT_FALLBACK:
            if d and d not in dom_candidates:
                dom_candidates.append(d)

        from urllib.parse import urlparse as _urlparse

        def _post_page_on(domain, sess, page):
            data = {"valor": q, "Buscar": "Buscar"}
            if page > 1:
                data["p"] = str(page)
            # NO seguir el redirect del POST: un dominio deprecado hace 301 a
            # otro (p.ej. support -> review) y seguirlo convierte el POST en GET,
            # PERDIENDO el termino (-> "Busqueda: -", 0 resultados). En su lugar
            # re-POSTeamos directo al dominio destino (robusto ante rotaciones).
            rr = sess.post(f"https://{domain}/buscar", data=data,
                           timeout=8, allow_redirects=False)
            if rr.status_code in (301, 302, 303, 307, 308):
                newdom = _urlparse(rr.headers.get("Location") or "").hostname
                if newdom and newdom != domain:
                    ns, _ = _dt_anubis_session(newdom)
                    rr = ns.post(f"https://{newdom}/buscar", data=data,
                                 timeout=8, allow_redirects=False)
                else:
                    rr = sess.post(f"https://{domain}/buscar", data=data,
                                   timeout=8, allow_redirects=True)
            if "anubis_challenge" in rr.text:
                ns, _ = _dt_anubis_session(domain, force=True)
                rr = ns.post(f"https://{domain}/buscar", data=data, timeout=8,
                             allow_redirects=False)
            return rr

        domain = None
        sess = None
        solved = False
        r = None
        full_html = ""
        tried = []
        got_results = False
        for cand in dom_candidates[:1]:   # 1 dominio aprendido (no saturar; el
            # 301-handling de _post_page_on sigue la rotacion si el dominio cambio)
            # TOPE DURO: resolver Anubis + POST puede colgar (slow-drip del baneo
            # evade el timeout de requests). _bounded abandona en 8s -> /dtsearch
            # NO cuelga 30-60s y Kodi cae a su DoH residencial al instante.
            def _try_dom(c=cand):
                s2, sv = _dt_anubis_session(c)
                rr = _post_page_on(c, s2, 1)
                return (s2, sv, rr, rr.text)
            # Tope 4s: la busqueda combinada del addon corta DonTorrent a 7s y
            # luego el box aun resuelve por DoH (~2s). Si el relay tardara 8s en
            # rendirse, el DoH llegaria a 10s -> TARDE. Con 4s: 4+2=6s < 7s, los
            # resultados de DonTorrent (via DoH del box) SI entran a tiempo.
            got = _bounded(_try_dom, 4.0, None)
            if got is None:
                tried.append(f"{cand}:TIMEOUT")
                continue
            try:
                s2, sv, rr, html = got
                # ¿Tiene resultados de contenido reales?
                n_items = len(_re_dt.findall(
                    r"/(?:pelicula|serie|documental)/\d+/", html))
                tried.append(f"{cand}:{n_items}")
                if n_items > 0:
                    domain, sess, solved, r, full_html = cand, s2, sv, rr, html
                    got_results = True
                    break
                # Guardar el primero como fallback aunque sea 0
                if domain is None:
                    domain, sess, solved, r, full_html = cand, s2, sv, rr, html
            except Exception as e:
                tried.append(f"{cand}:ERR")
                continue

        # Breaker compartido: si DonTorrent no dio resultados (baneo/cuelgue de la
        # IP de Render), marcarlo CAIDO -> las siguientes peticiones (web y el
        # /dtsearch que prioriza Kodi) se rinden al instante (502) y Kodi cae a su
        # DoH residencial. Si dio resultados, reactivar DonTorrent.
        _dt_mark(got_results)

        # Ningun dominio alcanzable (todo timeout/baneo) -> 502 limpio al instante.
        if r is None:
            return jsonify({"error": "dt_unreachable", "tried": tried}), 502

        # AUTO-CURATIVO: si hubo resultados, aprende el dominio oficial vigente
        # del schema.org de la pagina (donde de verdad sirvio, tras posibles 301)
        # y recuerdalo para la proxima busqueda. Fallback: el candidato que
        # funciono. Solo se persiste tras resultados reales -> nunca un clon.
        learned = None
        if got_results:
            learned = _dt_discover_canonical(full_html) or _dt_valid_host(domain)
            if learned:
                _dt_save_domain(learned)
        diag["domain"] = domain
        diag["learned"] = learned
        diag["tried"] = tried
        diag["anubis_solved"] = solved
        diag["phase"] = "search"

        def _post_page(page):
            return _post_page_on(domain, sess, page)

        # Detectar numero total de paginas desde buscarPagina(N) en el HTML
        page_nums = [int(n) for n in
                     _re_dt.findall(r"buscarPagina\((\d+)\)", full_html)]
        max_page = max(page_nums) if page_nums else 1
        # Limite de seguridad: no mas de 10 paginas (~100 resultados)
        max_page = min(max_page, 10)
        diag["max_page"] = max_page

        # Extraer los <p>..serie/pelicula/documental..</p> de paginas 2..N.
        # Las pedimos EN PARALELO (no secuencial) — reduce el tiempo de
        # ~Nx1s a ~1s para busquedas con muchas paginas. Mantenemos el
        # orden de pagina para no desordenar los resultados.
        extra_blocks = []
        if max_page > 1:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            def _fetch_page_blocks(pg):
                try:
                    rp = _post_page(pg)
                    blocks = []
                    for m in _re_dt.finditer(r"<p>.*?</p>", rp.text, _re_dt.S):
                        blk = m.group(0)
                        if _re_dt.search(r"/(?:pelicula|serie|documental)/\d+/", blk):
                            blocks.append(blk)
                    return blocks
                except Exception:
                    return []
            pages = list(range(2, max_page + 1))
            with _TPE(max_workers=min(8, len(pages))) as _ex:
                results = list(_ex.map(_fetch_page_blocks, pages))
            for blocks in results:
                extra_blocks.extend(blocks)

        if extra_blocks:
            inject = "".join(extra_blocks)
            # Insertar antes de <nav (la paginacion) o al final del card-body
            if "<nav" in full_html:
                full_html = full_html.replace("<nav", inject + "<nav", 1)
            else:
                full_html = full_html + inject
        diag["extra_items"] = len(extra_blocks)

        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "X-MW-Dt-Domain": domain or "?",
            "X-MW-Dt-Learned": learned or "",
            "X-MW-Dt-Status": str(r.status_code),
            "X-MW-Dt-Bytes": str(len(full_html)),
            "X-MW-Dt-Anubis-Solved": "1" if solved else "0",
            "X-MW-Dt-Pages": str(max_page),
        }
        return Response(full_html, status=r.status_code, headers=headers)

    except Exception as e:
        diag["error"] = e.__class__.__name__ + ": " + str(e)
        return jsonify({"error": str(e), "_diag": diag}), 502


@app.route("/dtfetch", methods=["GET"])
def dtfetch():
    """Proxy GET para DonTorrent con Anubis solver automatico.

    GET /dtfetch?u=https://dontorrent.science/serie/12345/1/slug/

    Devuelve el HTML de la URL solicitada. Resuelve Anubis si aparece.
    """
    target = (request.args.get("u") or "").strip()
    if not target:
        return jsonify({"error": "missing u"}), 400
    if "dontorrent" not in target.lower():
        return jsonify({"error": "host not allowed"}), 403

    try:
        from urllib.parse import urlparse
        domain = urlparse(target).hostname
        sess, solved = _dt_anubis_session(domain)
        r = sess.get(target, timeout=30, allow_redirects=True)

        if "anubis_challenge" in r.text:
            sess, _ = _dt_anubis_session(domain, force=True)
            r = sess.get(target, timeout=30)

        headers = {
            "Content-Type": r.headers.get("Content-Type",
                                          "text/html; charset=utf-8"),
            "Access-Control-Allow-Origin": "*",
            "X-MW-Dt-Status": str(r.status_code),
            "X-MW-Dt-Anubis-Solved": "1" if solved else "0",
        }
        return Response(r.content, status=r.status_code, headers=headers)

    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/dtpow", methods=["POST"])
def dtpow():
    """Resuelve PoW de descarga DonTorrent y devuelve URL del .torrent.

    POST /dtpow body: {"domain":"...","content_id":123,"tabla":"x"}

    Hace generate + solve + validate del API api_validate_pow.php
    manteniendo la sesion Anubis (misma IP).
    """
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    domain = (body.get("domain") or "").strip()
    content_id = body.get("content_id")
    tabla = (body.get("tabla") or "").strip()

    if not content_id or not tabla:
        return jsonify({"error": "missing content_id/tabla"}), 400

    # Resolucion unica y auto-curativa (refresca cookies Anubis si caducaron).
    _t0 = _t.time()
    url = _dt_download_url(domain, content_id, tabla)
    if url:
        return jsonify({"success": True, "download_url": url,
                        "elapsed": round(_t.time() - _t0, 2)})
    return jsonify({"error": "pow failed", "phase": "all-domains-failed"}), 502


# ===========================================================================
# DETECCION DE EMPAQUETADO (RAR) -> badge en la Lista del movil
# ===========================================================================
# El movil pregunta /dtpacked?c=<content_id>&tb=<tabla>. Resolvemos el .torrent
# (PoW) UNA vez, miramos si trae el video en RAR/zip/7z, y cacheamos 30 dias
# (los .torrent son estaticos). La cache es COMPARTIDA: si alguien ya lo resolvio,
# el resto lo ve al instante. Asi el badge sale sin recargar ni saturar.

def _bdecode(s):
    def dec(i):
        c = s[i:i + 1]
        if c == b"i":
            j = s.index(b"e", i)
            return int(s[i + 1:j]), j + 1
        if c == b"l":
            i += 1
            out = []
            while s[i:i + 1] != b"e":
                v, i = dec(i)
                out.append(v)
            return out, i + 1
        if c == b"d":
            i += 1
            out = {}
            while s[i:i + 1] != b"e":
                k, i = dec(i)
                v, i = dec(i)
                out[k] = v
            return out, i + 1
        j = s.index(b":", i)
        n = int(s[i:j])
        return s[j + 1:j + 1 + n], j + 1 + n
    v, _ = dec(0)
    return v


_PACK_RE_R = re.compile(rb"\.(?:rar|r\d{2,3}|part\d+\.rar|zip|7z|001)$", re.I)
_VID_RE_R = re.compile(rb"\.(?:mkv|mp4|avi|m4v|mov|ts|mpg|mpeg|wmv|flv|webm)$",
                       re.I)


def _torrent_packed(data):
    """True si el .torrent trae el video empaquetado (RAR/zip/7z) sin video
    real suelto (ignora muestras y videos < 50 MB)."""
    try:
        meta = _bdecode(data)
        info = meta.get(b"info") or {}
        files = info.get(b"files")
        entries = []
        if isinstance(files, list):
            for f in files:
                p = f.get(b"path") if isinstance(f, dict) else None
                if isinstance(p, list) and p and isinstance(p[-1],
                                                            (bytes, bytearray)):
                    entries.append((bytes(p[-1]),
                                    int(f.get(b"length") or 0)))
        else:
            nm = info.get(b"name")
            if isinstance(nm, (bytes, bytearray)):
                entries.append((bytes(nm), int(info.get(b"length") or 0)))
        if not entries:
            return False
        if not any(_PACK_RE_R.search(n) for n, _ in entries):
            return False

        def real_vid(n, sz):
            low = n.lower()
            if not _VID_RE_R.search(n):
                return False
            if b"sample" in low or b"muestra" in low:
                return False
            return sz == 0 or sz > 50 * 1024 * 1024

        return not any(real_vid(n, sz) for n, sz in entries)
    except Exception:
        return False


def _torrent_quality(data):
    """Calidad sacada del nombre del .torrent (4K/1080p/720p/HDTV...). '' si nada."""
    try:
        meta = _bdecode(data)
        info = meta.get(b"info") or {}
        names = []
        nm = info.get(b"name")
        if isinstance(nm, (bytes, bytearray)):
            names.append(bytes(nm).decode("utf-8", "ignore"))
        files = info.get(b"files")
        if isinstance(files, list):
            for f in files:
                p = f.get(b"path") if isinstance(f, dict) else None
                if isinstance(p, list) and p and isinstance(p[-1],
                                                            (bytes, bytearray)):
                    names.append(bytes(p[-1]).decode("utf-8", "ignore"))
        for n in names:
            m = _CAT_QRE.search(n)
            if m:
                return _cat_norm_quality(m.group(1))
    except Exception:
        pass
    return ""


# ===========================================================================
# SALUD DE SEMILLAS: info_hash del .torrent + scrape UDP a trackers vivos.
# Para que el movil pueda mostrar/avisar de cuantos seeders tiene un titulo
# ANTES de reproducir (lo de "sin semillas" no es fallo del addon: enjambres
# muertos en series viejas). Se cuelga de la cache de /dtpacked (sin PoW extra).
# ===========================================================================
import socket as _sock
import struct as _struct

_SEEDS_TTL = 2700   # 45 min: los seeders cambian, refrescamos
_SEED_TRACKERS = (("tracker.opentrackr.org", 1337),
                  ("tracker.torrent.eu.org", 451),
                  ("open.stealth.si", 80))


def _bspan(data, i):
    """Indice final (exclusivo) del elemento bencoded que empieza en i."""
    c = data[i:i + 1]
    if c == b"i":
        return data.index(b"e", i) + 1
    if c == b"l" or c == b"d":
        i += 1
        while data[i:i + 1] != b"e":
            i = _bspan(data, i)
        return i + 1
    j = data.index(b":", i)
    n = int(data[i:j])
    return j + 1 + n


def _dt_infohash(data):
    """SHA1 del dict info (span EXACTO de los bytes) -> 20 bytes, o None."""
    try:
        ip = data.find(b"4:info")
        if ip < 0:
            return None
        s = ip + 6
        e = _bspan(data, s)
        return _hl.sha1(data[s:e]).digest()
    except Exception:
        return None


def _udp_scrape_one(host, port, info_hash, timeout=3.0):
    """Seeders de un tracker UDP (BEP-15), o None si no responde a tiempo."""
    s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        addr = (host, port)
        s.sendto(_struct.pack(">QII", 0x41727101980, 0,
                              int.from_bytes(os.urandom(4), "big")), addr)
        action, _rt, cid = _struct.unpack(">IIQ", s.recv(16))
        if action != 0:
            return None
        s.sendto(_struct.pack(">QII", cid, 2,
                              int.from_bytes(os.urandom(4), "big")) + info_hash,
                 addr)
        resp = s.recv(20)
        a2, _ = _struct.unpack(">II", resp[:8])
        if a2 != 2:
            return None
        seeders, _comp, _leech = _struct.unpack(">III", resp[8:20])
        return int(seeders)
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def _dt_seed_count(info_hash):
    """Maximo de seeders entre trackers vivos, consultados EN PARALELO. -1 si
    ninguno respondio. Antes era SECUENCIAL (3 trackers x 3s de timeout = hasta
    9s en el camino de CADA calculo de semillas, cuando el 1er tracker no
    contesta); en paralelo el peor caso es ~3s. El scrape UDP a los trackers NO
    toca DonTorrent ni TMDB -> cero riesgo de baneo."""
    from concurrent.futures import ThreadPoolExecutor as _TPE
    best = -1
    try:
        with _TPE(max_workers=len(_SEED_TRACKERS)) as ex:
            for n in ex.map(
                    lambda hp: _udp_scrape_one(hp[0], hp[1], info_hash),
                    _SEED_TRACKERS):
                if n is not None and n > best:
                    best = n
    except Exception:
        pass
    return best


def _dtpacked_seeds(ent, now):
    """Seeders del item; refresca por UDP si caducaron y hay ih guardado.
    Muta `ent` (entrada de la cache) -> el llamante debe guardar."""
    s = ent.get("s")
    if s is not None and (now - ent.get("sts", 0) < _SEEDS_TTL):
        return s
    ihex = ent.get("ih")
    if not ihex:
        return s
    try:
        sc = _dt_seed_count(bytes.fromhex(ihex))
    except Exception:
        sc = -1
    if sc >= 0:
        ent["s"] = sc
        ent["sts"] = now
        return sc
    return s


def _dt_download_url(domain, content_id, tabla):
    """Breaker + tope de concurrencia (max 2 ops DonTorrent), luego delega."""
    if _dt_is_down() or not _DT_SEM.acquire(blocking=False):
        return None
    try:
        return _dt_download_url_inner(domain, content_id, tabla)
    finally:
        _DT_SEM.release()


def _dt_download_url_inner(domain, content_id, tabla):
    """Resuelve la URL del .torrent (mismo PoW que /dtpow). Devuelve url o None.
    Si DonTorrent no responde, BAJA el breaker compartido para todos."""
    dom_candidates = []
    if domain:
        dom_candidates.append(domain)
    for d in DT_FALLBACK:
        if d not in dom_candidates:
            dom_candidates.append(d)
    got = [False]
    def _try(dom, fresh):
        # fresh=True: cookies Anubis caducadas/invalidas -> RE-resolver (ignora la
        # cache de memoria Y la de disco), no solo limpiar la copia en memoria.
        sess, _ = _dt_anubis_session(dom, force=fresh)
        api = f"https://{dom}/api_validate_pow.php"
        r1 = sess.post(api, json={"action": "generate",
                                  "content_id": int(content_id),
                                  "tabla": tabla}, timeout=8)
        got[0] = True   # DonTorrent respondio (no esta caido)
        if r1.status_code in (404, 405):
            return None
        gen = r1.json()
        challenge = gen.get("challenge")
        if not gen.get("success") or not challenge:
            return None
        if isinstance(challenge, dict):
            rand = challenge.get("randomData", "")
            diff = challenge.get("difficulty", 3)
        else:
            rand, diff = str(challenge), 3
        _h, nonce, _e = _dt_solve_pow(rand, diff)
        r2 = sess.post(api, json={"action": "validate", "challenge": challenge,
                                  "nonce": nonce}, timeout=8)
        val = r2.json()
        if not val.get("success") or not val.get("download_url"):
            return None
        url = val["download_url"]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = f"https://{dom}{url}"
        return url

    # Auto-curativo: 1 dominio (no saturar), 1er intento normal y 2o con cookies
    # frescas (causa habitual de fallo: cookies Anubis caducadas, NO bloqueo IP).
    for dom in dom_candidates[:1]:
        for fresh in (False, True):
            try:
                url = _try(dom, fresh)
                if url:
                    _dt_mark(True)
                    return url
            except Exception:
                pass
    _dt_mark(got[0])   # si NADA respondio -> DonTorrent caido -> breaker compartido
    return None


_DTPACKED_FILE = "/tmp/mw_dtpacked.json"
_DTPACKED_TTL = 2592000   # 30 dias (los .torrent son estaticos)


def _dtpacked_load():
    try:
        with open(_DTPACKED_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _dtpacked_save(d):
    try:
        tmp = _DTPACKED_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _DTPACKED_FILE)
    except Exception:
        pass


# Proteccion de la IP RESIDENCIAL del box: resolver un .torrent en el box es un
# PoW (CPU + peticion a DonTorrent desde la IP de casa). La cuadricula del Inicio
# pide /dtpacked por CADA peli DT visible (badges RAR) -> con Render baneado eso
# podria lanzar DECENAS de PoW al box y banear la IP de casa (justo la que NO debe
# caer, es el puente). Por eso: serializamos (1 a la vez) y limitamos el numero
# por ventana. Lo que no pase -> sin badge (cosmetico); la ficha/aviso de
# reproduccion (1-2 peticiones) si pasa siempre.
_DT_BOX_SEM = _thr.Semaphore(1)
_DT_BOX_LOCK = _thr.Lock()
_DT_BOX_CALLS = []          # timestamps de llamadas dtmeta-via-box recientes
_DT_BOX_BUDGET = 8          # maximo por ventana
_DT_BOX_WINDOW = 120.0      # 2 min


def _dt_box_allow():
    """Presupuesto: como mucho _DT_BOX_BUDGET resoluciones en el box por ventana,
    para que un Inicio lleno de pelis DT no machaque la IP residencial."""
    now = _t.time()
    with _DT_BOX_LOCK:
        global _DT_BOX_CALLS
        _DT_BOX_CALLS = [t for t in _DT_BOX_CALLS if now - t < _DT_BOX_WINDOW]
        if len(_DT_BOX_CALLS) >= _DT_BOX_BUDGET:
            return False
        _DT_BOX_CALLS.append(now)
        return True


def _dt_meta_via_box(cid, tb):
    """Render baneado por DonTorrent -> un Kodi VIVO del sistema (IP residencial)
    resuelve el .torrent y devuelve rar+quality+info_hash; con el hash el relay
    deriva los seeders por scrape UDP (lo unico que SI va desde la IP de Render
    aunque DonTorrent la banee). Sin depender del code (any_live_box). Devuelve
    {'packed','quality','ih','seeds'} o None si no hay box vivo, esta limitado, o
    el box no logro el .torrent (sin ih -> no cacheamos nada falso)."""
    box = _any_live_box()
    if not box:
        return None
    # 1 a la vez (no avalancha de PoW en el box) + presupuesto por ventana.
    if not _DT_BOX_SEM.acquire(blocking=False):
        return None
    try:
        if not _dt_box_allow():
            return None
        job = "dp" + os.urandom(5).hex()
        _kb_enqueue(box, {"c": "etjob", "job": job, "op": "dtmeta",
                          "cid": cid, "tb": tb})
        res = _catjob_wait(job, 14.0)
    finally:
        _DT_BOX_SEM.release()
    if not res:
        return None
    ihx = re.sub(r"[^a-f0-9]", "", (res.get("ih") or "").lower())[:40]
    if len(ihx) != 40:
        return None
    out = {"packed": bool(res.get("rar")), "quality": res.get("quality") or "",
           "ih": ihx, "seeds": None}
    sc = _dt_seed_count(bytes.fromhex(ihx))
    if sc >= 0:
        out["seeds"] = sc
    return out


@app.get("/dtpacked")
def dtpacked():
    cid = re.sub(r"\D", "", request.args.get("c", ""))[:12]
    tb = re.sub(r"[^a-z0-9_]", "", request.args.get("tb", "").lower())[:24]
    if not (cid and tb):
        return jsonify({"packed": None})
    key = f"{tb}:{cid}"
    d = _dtpacked_load()
    ent = d.get(key)
    now = _t.time()
    if ent and (now - ent.get("ts", 0) < _DTPACKED_TTL):
        seeds = _dtpacked_seeds(ent, now)
        out = {"packed": ent.get("p"), "quality": ent.get("q", ""),
               "cached": True}
        if seeds is not None:
            out["seeds"] = seeds
            _dtpacked_save(d)   # persistir seeders refrescados
        return jsonify(out)
    # Mismo tope duro que en /dtseeds (ver el porque alli): el directo baneado
    # se comia ~25s antes de rendirse y esto lo pide la ficha ANTES de reproducir.
    _dom = request.args.get("domain", "").strip()
    url = _bounded(lambda: _dt_download_url(_dom, cid, tb), 6.0)
    if not url:
        # Render baneado -> via box (IP residencial): rar+quality+info_hash;
        # el relay deriva seeders por scrape UDP. Cacheamos igual que el directo.
        mb = _dt_meta_via_box(cid, tb)
        if mb is None:
            return jsonify({"packed": None})
        ent = {"p": mb["packed"], "q": mb["quality"], "ts": now, "ih": mb["ih"]}
        if mb["seeds"] is not None:
            ent["s"] = mb["seeds"]
            ent["sts"] = now
        d[key] = ent
        if len(d) > 3000:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[:len(d) - 3000]:
                d.pop(k, None)
        _dtpacked_save(d)
        out = {"packed": mb["packed"], "quality": mb["quality"], "viabox": True}
        if mb["seeds"] is not None:
            out["seeds"] = mb["seeds"]
        return jsonify(out)
    packed = None
    quality = ""
    ihex = ""
    seeds = None
    try:
        from urllib.parse import urlparse
        sess, _ = _dt_anubis_session(urlparse(url).hostname)
        r = sess.get(url, timeout=25, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 100:
            packed = _torrent_packed(r.content)
            quality = _torrent_quality(r.content)
            ih = _dt_infohash(r.content)
            if ih:
                ihex = ih.hex()
                sc = _dt_seed_count(ih)
                if sc >= 0:
                    seeds = sc
    except Exception:
        packed = None
    if packed is not None:
        ent = {"p": bool(packed), "q": quality, "ts": now}
        if ihex:
            ent["ih"] = ihex
        if seeds is not None:
            ent["s"] = seeds
            ent["sts"] = now
        d[key] = ent
        if len(d) > 3000:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[:len(d) - 3000]:
                d.pop(k, None)
        _dtpacked_save(d)
    out = {"packed": packed, "quality": quality}
    if seeds is not None:
        out["seeds"] = seeds
    return jsonify(out)


@app.get("/dtseeds")
def dtseeds():
    """Seeders reales de un item DonTorrent (scrape UDP). Lo usa el movil para
    avisar ANTES de reproducir. Reusa/rellena la cache de /dtpacked (sin PoW
    extra si ya conocemos el info_hash del titulo)."""
    cid = re.sub(r"\D", "", request.args.get("c", ""))[:12]
    tb = re.sub(r"[^a-z0-9_]", "",
                request.args.get("tb", "").lower())[:24] or "peliculas"
    if not cid:
        return jsonify({"seeds": None})
    key = f"{tb}:{cid}"
    d = _dtpacked_load()
    ent = d.get(key)
    now = _t.time()
    if ent and ent.get("ih"):
        s = _dtpacked_seeds(ent, now)
        _dtpacked_save(d)
        return jsonify({"seeds": s, "cached": True})
    # TOPE DURO al camino directo. Render lo tiene baneado casi siempre, y
    # `_dt_download_url_inner` recorre los 10 dominios de DT_FALLBACK resolviendo
    # Anubis con timeout=8 en cada uno: medido 2026-08-06, las semillas tardaban
    # ~40s en salir (39-42s) cuando el BOX las resuelve en ~14s. Con 6s de tope
    # se abandona el directo y se va al box, que es quien puede. No se pierde el
    # camino directo: el hilo sigue de fondo y, si DonTorrent no responde, BAJA
    # el breaker compartido -> las siguientes peticiones ya ni lo intentan.
    url = _bounded(lambda: _dt_download_url("", cid, tb), 6.0)
    if not url:
        # Render baneado -> el box trae el info_hash; el scrape UDP va aqui.
        mb = _dt_meta_via_box(cid, tb)
        if mb is None or mb["seeds"] is None:
            return jsonify({"seeds": None})
        ent = d.get(key) or {}
        ent["ih"] = mb["ih"]
        ent["s"] = mb["seeds"]
        ent["sts"] = now
        ent.setdefault("ts", now)
        ent.setdefault("p", mb["packed"])
        ent.setdefault("q", mb["quality"])
        d[key] = ent
        _dtpacked_save(d)
        return jsonify({"seeds": mb["seeds"], "viabox": True})
    try:
        from urllib.parse import urlparse
        sess, _ = _dt_anubis_session(urlparse(url).hostname)
        r = sess.get(url, timeout=25, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 100:
            ih = _dt_infohash(r.content)
            if ih:
                sc = _dt_seed_count(ih)
                ent = d.get(key) or {}
                ent["ih"] = ih.hex()
                if sc >= 0:
                    ent["s"] = sc
                    ent["sts"] = now
                if "p" not in ent:   # de paso, rellenamos RAR/calidad (gratis)
                    ent["p"] = bool(_torrent_packed(r.content))
                    ent["q"] = _torrent_quality(r.content)
                    ent["ts"] = now
                d[key] = ent
                _dtpacked_save(d)
                return jsonify({"seeds": (sc if sc >= 0 else None)})
    except Exception:
        pass
    return jsonify({"seeds": None})


@app.get("/catdtmeta")
def catdtmeta():
    """Calidad + RAR de un item DonTorrent, resueltos por el BOX (la IP de Render
    no puede con el PoW de descarga). Cache compartida con /dtpacked."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    cid = re.sub(r"\D", "", request.args.get("c", ""))[:12]
    tb = re.sub(r"[^a-z0-9_]", "",
                request.args.get("tb", "").lower())[:24] or "peliculas"
    if not cid:
        return jsonify({"rar": False, "quality": ""})
    key = f"{tb}:{cid}"
    d = _dtpacked_load()
    ent = d.get(key)
    now = _t.time()
    if ent and (now - ent.get("ts", 0) < _DTPACKED_TTL) and ("q" in ent):
        return jsonify({"rar": bool(ent.get("p")),
                        "quality": ent.get("q", ""), "cached": True})
    if len(code) != 6:
        return jsonify({"rar": False, "quality": ""})
    job = "et" + os.urandom(5).hex()
    _kb_enqueue(code, {"c": "etjob", "job": job, "op": "dtmeta",
                       "cid": cid, "tb": tb})
    res = _catjob_wait(job, 22.0)
    if res is None:
        return jsonify({"rar": False, "quality": "", "timeout": True})
    rar = bool(res.get("rar"))
    q = res.get("quality") or ""
    d = _dtpacked_load()
    d[key] = {"p": rar, "q": q, "ts": now}
    if len(d) > 3000:
        for k in sorted(d, key=lambda k: d[k].get("ts", 0))[:len(d) - 3000]:
            d.pop(k, None)
    _dtpacked_save(d)
    return jsonify({"rar": rar, "quality": q})


# ===========================================================================
# PROBE: sondeo de viabilidad de fuentes (Anubis / Cloudflare-ligero / Turnstile)
# ===========================================================================
# Herramienta interna para evaluar candidatos a 4a fuente DESDE la IP del relay
# (datacenter), que es el entorno real. Allowlist por marca para no abrir proxy.
_PROBE_BRANDS = ("dontorrent", "mejortorrent", "wolfmax", "divxtotal",
                 "grantorrent", "gran-torrent", "todotorrent", "torrentrapid",
                 "pctmix", "pctnew", "newpct", "elitetorrent", "esdivx",
                 "tomadivx", "descargas", "estrenos", "torrentlocura",
                 "atomohd", "atomixhq", "todopelis", "pelisip", "cinecalidad")


def _probe_detect(text):
    t = (text or "")[:6000].lower()
    if ("anubis" in t or "asegurándonos" in t or "asegurandonos" in t
            or "making sure you" in t or ("difficulty" in t and "sha256" in t)):
        return "anubis"
    if ("just a moment" in t or "challenges.cloudflare.com" in t
            or "cf-mitigated" in t or "/cdn-cgi/challenge" in t
            or "turnstile" in t):
        return "cloudflare-challenge"
    n = len(re.findall(r"/(?:pelicula|serie|documental|descargar|torrent)s?/",
                       t))
    return "open" if n >= 3 else "unknown"


@app.get("/probe")
def probe():
    u = (request.args.get("u") or "").strip()
    if not u.startswith("http"):
        return jsonify({"error": "bad u"}), 400
    from urllib.parse import urlparse
    host = (urlparse(u).hostname or "").lower()
    if not any(b in host for b in _PROBE_BRANDS):
        return jsonify({"error": "host not allowed", "host": host}), 403
    out = {"host": host}
    try:
        r = requests.get(u, headers=BROWSER_HEADERS, timeout=20,
                         allow_redirects=True)
        out["plain"] = {"status": r.status_code, "final": r.url,
                        "prot": _probe_detect(r.text), "bytes": len(r.text)}
    except Exception as e:
        out["plain"] = {"error": e.__class__.__name__}
    try:
        cs = _make_scraper()
        r2 = cs.get(u, timeout=35, allow_redirects=True)
        out["cs"] = {"status": r2.status_code, "final": r2.url,
                     "prot": _probe_detect(r2.text), "bytes": len(r2.text)}
    except Exception as e:
        out["cs"] = {"error": e.__class__.__name__}
    return jsonify(out)


# ===========================================================================
# DIVXTOTAL: busqueda solida (multi-dominio + TODAS las paginas), como DonTorrent
# ===========================================================================
# El box hace UNA sola llamada a /dxsearch?q=...; aqui resolvemos el dominio
# activo (cacheado y COMPARTIDO entre todos los boxes), traemos todas las paginas
# de resultados en paralelo, y devolvemos el HTML concatenado para que el addon
# lo parsee. requests plano (DivxTotal pasa); si detecta challenge -> cloudscraper.
_DX_DOMAINS = ["divxtotal.foo", "divxtotal.gg", "divxtotal.cam",
               "divxtotal.fyi", "divxtotal.run", "divxtotal.one",
               "divxtotal.es", "divxtotal.mov"]
_DX_DOM_CACHE = {"dom": None, "ts": 0.0}
_DX_DOM_TTL = 3600
# AUTO-CURATIVO: persistimos el dominio vigente en /tmp (sobrevive a reinicios de
# proceso dentro de la misma instancia) y APRENDEMOS rotaciones: si un dominio
# viejo redirige 301 al nuevo, adoptamos el host final aunque NO este en la lista.
# Asi, cuando DivxTotal rote de TLD, el buscador se arregla solo sin tocar nada.
_DX_DOMAIN_FILE = "/tmp/mw_dx_domain.txt"
_DX_HOST_RE = re.compile(r"^(?:www\.)?(divxtotal\.[a-z]{2,12})$", re.I)


def _dx_valid_host(host):
    """Solo acepta el apex 'divxtotal.<tld>' (con o sin www). Rechaza clones y
    hosts ajenos (cdns, imagenes...)."""
    if not host:
        return None
    m = _DX_HOST_RE.match(host.strip().lower())
    return m.group(1) if m else None


def _dx_load_domain():
    try:
        with open(_DX_DOMAIN_FILE, "r", encoding="utf-8") as f:
            return _dx_valid_host(f.read().strip())
    except Exception:
        return None


def _dx_save_domain(host):
    host = _dx_valid_host(host)
    if not host:
        return
    try:
        tmp = _DX_DOMAIN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(host)
        os.replace(tmp, _DX_DOMAIN_FILE)
    except Exception:
        pass


def _dx_get(url, proxy=False):
    """HTML de una URL de DivxTotal: requests plano y, si hay challenge de
    Cloudflare, reintenta con cloudscraper. None si no se pudo.
    proxy=True -> via ScraperAPI en modo API (el MISMO mecanismo probado que usa
    WolfMax a diario). El modo-proxy (proxies= de requests) NUNCA llego a
    ScraperAPI (el contador de peticiones de la cuenta no se movia): failover
    roto en silencio. El modo-API es un GET normal a api.scraperapi.com."""
    if proxy:
        if not SCRAPERAPI_KEY:
            return None   # pidieron failover pero no hay key -> no insistir
        try:
            wrapped = _scraperapi_url(url, premium=False)   # 1 credito
            r = requests.get(wrapped, timeout=55)
            if r.status_code == 200 and len(r.text or "") > 500:
                return r.text
        except Exception:
            pass
        return None
    try:
        r = requests.get(url, headers=BROWSER_HEADERS,
                         timeout=20, allow_redirects=True)
        t = r.text
        low = t[:4000].lower()
        if (r.status_code == 200 and "just a moment" not in low
                and "challenge-platform" not in low and "cf-mitigated" not in low):
            return t
    except Exception:
        pass
    try:
        cs = _make_scraper()
        r2 = cs.get(url, timeout=35, allow_redirects=True)
        if r2.status_code == 200:
            return r2.text
    except Exception:
        pass
    return None


def _dx_probe(domain):
    """Pide la home de `domain`. Si sirve catalogo (o redirige a otro 'divxtotal.*'
    que lo sirve), devuelve el host REAL vigente -> aprende rotaciones aunque el
    TLD nuevo no este en _DX_DOMAINS. None si no responde con catalogo."""
    from urllib.parse import urlparse as _up
    url = f"https://{domain}/"
    for use_cs in (False, True):
        try:
            if use_cs:
                r = _make_scraper().get(url, timeout=30, allow_redirects=True)
            else:
                r = requests.get(url, headers=BROWSER_HEADERS, timeout=15,
                                 allow_redirects=True)
            # Pagina ENTERA (no solo la cabecera): DivxTotal antepone ahora un
            # bloque JSON-LD enorme y '/peliculas/' no aparece hasta ~29000
            # chars -> con la ventana de 6000 el probe RECHAZABA el dominio
            # bueno, _dx_domain se eternizaba probando los 8 y DivxTotal
            # desaparecia de la web. Se acepta tambien la forma escapada del
            # JSON ('\\/peliculas\\/') por si el HTML fuera todo schema.org.
            low = (r.text or "").lower()
            if r.status_code == 200 and ("/peliculas/" in low
                                         or "\\/peliculas\\/" in low):
                host = _dx_valid_host(_up(r.url).hostname or "")
                return host or domain
        except Exception:
            pass
    return None


def _dx_domain(force_probe=False):
    """Dominio de DivxTotal. El camino CALIENTE (busquedas/listados) NO sondea
    JAMAS: cache -> persistido -> _DX_DOMAINS[0] (el canonico actual). El probe
    es otra conexion que puede caer en el TARPIT de Cloudflare (goteo lento
    aleatorio a IPs de datacenter que evade el timeout de requests): la traza
    de /catdiag demostro que las busquedas morian AQUI dentro, sondeando en
    frio. El descubrimiento (force_probe=True) queda SOLO para el 2o plano
    (keepalive y _dx_reprobe_async)."""
    now = _t.time()
    saved = _dx_load_domain()
    if not force_probe:
        if _DX_DOM_CACHE["dom"] and now - _DX_DOM_CACHE["ts"] < _DX_DOM_TTL:
            return _DX_DOM_CACHE["dom"]
        if saved:
            _DX_DOM_CACHE["dom"] = saved
            _DX_DOM_CACHE["ts"] = now
        return saved or _DX_DOMAINS[0]
    # Descubrimiento (2o plano): dominio aprendido primero, luego la lista.
    # _dx_probe aprende el host final si hubo redireccion.
    cand = ([saved] if saved else []) + [d for d in _DX_DOMAINS if d != saved]
    for d in cand:
        real = _dx_probe(d)
        if real:
            _DX_DOM_CACHE["dom"] = real
            _DX_DOM_CACHE["ts"] = now
            _dx_save_domain(real)
            return real
    return _DX_DOM_CACHE["dom"] or saved or _DX_DOMAINS[0]


_DX_REPROBE = {"ts": 0.0}
_DX_REPROBE_LOCK = _thr.Lock()


def _dx_reprobe_async():
    """Re-descubre el dominio dx en un hilo aparte, como mucho 1 vez cada 2 min
    (candado anti-tormenta: N busquedas fallidas concurrentes NO lanzan N
    probes, que agravarian el tarpit). Fire-and-forget: nadie lo espera."""
    with _DX_REPROBE_LOCK:
        if _t.time() - _DX_REPROBE["ts"] < 120:
            return
        _DX_REPROBE["ts"] = _t.time()
    _thr.Thread(target=lambda: _dx_domain(force_probe=True),
                daemon=True).start()


@app.route("/dxsearch", methods=["GET", "POST"])
def dxsearch():
    if request.method == "POST":
        q = ((request.get_json(silent=True) or {}).get("q") or "").strip()
    else:
        q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "missing q"}), 400
    from urllib.parse import quote as _q
    dom = _dx_domain()
    qq = _q(q)
    html1 = _dx_get(f"https://{dom}/?s={qq}")
    if not html1:
        # reintento en conexion NUEVA (tarpit); si el dominio roto, el
        # re-descubrimiento va en 2o plano (nunca sondear en linea)
        html1 = _dx_get(f"https://{dom}/?s={qq}")
    if not html1:
        _dx_reprobe_async()
        return Response("", status=502,
                        headers={"X-MW-Dx-Domain": dom or ""})
    nums = [int(n) for n in re.findall(r"/page/(\d+)/", html1)]
    max_page = min(max(nums), 6) if nums else 1
    parts = [html1]
    if max_page > 1:
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _fp(p):
            return _dx_get(f"https://{dom}/page/{p}/?s={qq}") or ""

        with _TPE(max_workers=min(5, max_page - 1)) as ex:
            parts.extend(ex.map(_fp, range(2, max_page + 1)))
    return Response("\n".join(parts),
                    headers={"Content-Type": "text/html; charset=utf-8",
                             "Access-Control-Allow-Origin": "*",
                             "X-MW-Dx-Domain": dom or "",
                             "X-MW-Dx-Pages": str(max_page)})


# --- DivxTotal DIRECTO desde el relay (parseado a items del catalogo) -------
# El relay SI alcanza DivxTotal (no esta bloqueado como DonTorrent/TMDB), asi
# que la busqueda dx NO necesita el box: la servimos en /catsearch al instante
# (rapido y SIN code -> los amigos ven DivxTotal aunque no tengan Kodi
# encendido). Antes dx iba web->relay->BOX->relay/dxsearch->box->relay->web
# (ida-y-vuelta lento e inconsistente); ahora el relay lo trae directo (~2s).
_DX_SLUG_RE = re.compile(r'/(?:peliculas|series)/[a-z0-9][a-z0-9\-]+/?$', re.I)
_DX_KIND_RE = re.compile(r'/(peliculas|series)/', re.I)
_DX_EP_TAIL = re.compile(r'\s*\d{1,2}\s*[xX×]\s*\d{1,3}\s*$')
_DX_STOP = {"el", "la", "los", "las", "de", "del", "y", "a", "en", "un", "una",
            "the", "of", "to", "lo", "su", "al", "o", "and"}
_DX_A_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                      re.I | re.S)


def _dx_norm(s):
    s = _wud.normalize("NFKD", s or "")
    s = "".join(c for c in s if not _wud.combining(c)).lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)


def _dx_relevance(items, q):
    """Quita ruido: titulos con TODAS las palabras > con ALGUNA (como el box)."""
    toks = [t for t in _dx_norm(q).split() if len(t) > 1 and t not in _DX_STOP]
    if not toks:
        return items

    def score(it):
        words = set(_dx_norm(it.get("title", "")).split())
        return sum(1 for t in toks if t in words)
    full = [it for it in items if score(it) == len(toks)]
    return full or [it for it in items if score(it) >= 1]


def _dx_parse_items(html, dom):
    """Items (peli/serie) del HTML de DivxTotal -> MISMO formato que el box
    (_src_item_compact): source/url/content_id/kind/thumb/quality/tabla."""
    out, seen = [], set()
    for m in _DX_A_RE.finditer(html or ""):
        href = (m.group(1) or "").strip()
        if not _DX_SLUG_RE.search(href):
            continue
        km = _DX_KIND_RE.search(href)
        kind = "serie" if (km and km.group(1).lower() == "series") else "movie"
        url = href if href.startswith("http") else f"https://{dom}{href}"
        if url in seen:
            continue
        # quita el resaltado inline del termino buscado SIN espacio (ver DT) para
        # no partir palabras: "Desapa<span>rec</span>ida" -> "Desaparecida".
        _raw = re.sub(r"</?(?:span|b|strong|em|mark|i|u|font)\b[^>]*>", "",
                      m.group(2) or "")
        title = re.sub(r"<[^>]+>", " ", _raw)
        title = re.sub(r"\s+", " ", title).strip()
        if not title or len(title) < 2:
            continue
        if kind == "serie":
            title = _DX_EP_TAIL.sub("", title).strip() or title
        seen.add(url)
        out.append({"title": title, "kind": kind, "source": "dx",
                    "url": url, "content_id": url, "thumb": None,
                    "quality": "", "tabla": "dx"})
    return out


# Breaker + tope de concurrencia para DivxTotal (la via DIRECTA). DivxTotal puede
# tardar muchisimo (reto Cloudflare -> cloudscraper ~35s) y antes esos hilos se
# ABANDONABAN tras el join(9s) de /catsearch y se ACUMULABAN -> es lo que mas
# saturaba el relay al buscar varias cosas seguidas. Ahora: como mucho 2 ops DX a
# la vez y, si DX va lento o esta bloqueado, se SALTA un rato (no machaca la IP, no
# se cuelga). Espejo del breaker de DonTorrent. En memoria (por worker) -> simple.
_DX_SEM = _thr.Semaphore(2)
_DX_DOWN_UNTIL = [0.0]
_DX_DOWN_COOLDOWN = 90


def _dx_is_down():
    return _t.time() < _DX_DOWN_UNTIL[0]


def _dx_mark(ok):
    _DX_DOWN_UNTIL[0] = 0.0 if ok else (_t.time() + _DX_DOWN_COOLDOWN)


def _dx_search_items(q, max_pages=5, proxy=False):
    """Busca en DivxTotal y devuelve items del catalogo web. proxy=True -> via
    ScraperAPI (failover explicito, NO pasa por el breaker/tope directo)."""
    if proxy:
        return _dx_search_items_inner(q, max_pages, proxy=True)
    # Via directa: breaker + tope. Si DX esta caido o ya hay 2 ops -> [] al instante
    # (no abre otro hilo que luego se abandone y se acumule).
    if _dx_is_down() or not _DX_SEM.acquire(blocking=False):
        return []
    try:
        return _dx_search_items_inner(q, max_pages, proxy=False)
    finally:
        _DX_SEM.release()


_DX_TRACE = {}   # etapas/tiempos de la ULTIMA busqueda dx directa (ver /catdiag)


def _dx_search_items_inner(q, max_pages=5, proxy=False):
    """Busca en DivxTotal DIRECTO (relay) y devuelve items del catalogo web.
    proxy=True -> via ScraperAPI (failover cuando el directo esta baneado)."""
    from urllib.parse import quote as _q
    t0 = _t.time()
    if not proxy:
        _DX_TRACE.clear()
        _DX_TRACE["q"] = q[:30]

    def _tr(k, v):
        if not proxy:
            _DX_TRACE[k] = v
            _DX_TRACE["t"] = round(_t.time() - t0, 1)
    dom = _dx_domain()
    _tr("dom", dom)
    if not dom:
        if not proxy:
            _dx_mark(False)
        return []
    qq = _q(q)
    url1 = f"https://{dom}/?s={qq}"
    if proxy:
        html1 = _dx_get(url1, proxy=True)
    else:
        # Cloudflare TARPITEA al azar conexiones desde la IP de Render (goteo
        # lento que evade el timeout de requests: cada byte reinicia el reloj).
        # Tope duro CORTO + reintento en conexion NUEVA = redibujar la loteria
        # (misma peticion iba en 1-2s o colgaba 60s segun la conexion que
        # tocara). 2 intentos de 5s caben de sobra en el presupuesto de 14s.
        html1 = _bounded(lambda: _dx_get(url1), 5.0, None)
        _tr("fetch1", len(html1 or ""))
        if not html1:
            html1 = _bounded(lambda: _dx_get(url1), 5.0, None)
            _tr("fetch2", len(html1 or ""))
    if not html1:
        if not proxy:
            _dx_mark(False)        # inalcanzable -> saltar DX un rato
            _dx_reprobe_async()    # y re-descubrir el dominio en 2o PLANO
            _tr("fail", "reprobe_bg")
        return []
    if not proxy:
        # Alcanzamos DivxTotal -> breaker ARRIBA (aunque la query no tenga matches o
        # haya tardado): asi DX se SIGUE intentando y aparece en cuanto responde
        # rapido. El tope de 2 ops (_DX_SEM) ya evita que un DX lento se acumule; el
        # breaker solo salta si NO se pudo ALCANZAR DivxTotal (bloqueo real) -> ahi
        # si conviene no machacar la IP un rato.
        _dx_mark(True)
    nums = [int(n) for n in re.findall(r"/page/(\d+)/", html1)]
    max_page = min(max(nums), max_pages) if nums else 1
    parts = [html1]
    if max_page > 1:
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _fp(p):
            u = f"https://{dom}/page/{p}/?s={qq}"
            if proxy:
                return _dx_get(u, proxy=True) or ""
            # tope duro por pagina: UNA conexion tarpiteada no bloquea el map
            # entero (mejor perder una pagina que toda la busqueda)
            return _bounded(lambda: _dx_get(u), 5.0, "") or ""
        with _TPE(max_workers=min(5, max_page - 1)) as ex:
            parts.extend(ex.map(_fp, range(2, max_page + 1)))
    items, seen = [], set()
    for h in parts:
        for it in _dx_parse_items(h, dom):
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            items.append(it)
    _tr("parsed", len(items))
    rel = _dx_relevance(items, q)
    _tr("relevant", len(rel))
    return rel


# --- Ficha DivxTotal DIRECTO desde el relay (episodios + .torrent + semillas) -
# El relay SI alcanza DivxTotal, asi que la ficha (episodios de una serie, o el
# .torrent de una peli) NO necesita el box: la resolvemos aqui igual que la
# busqueda. Mismo formato que el box (_src_episodes/_src_resolve) para no tocar
# la web. Replica scraper_divxtotal.detail() con regex (el relay no usa bs4).
_DX_DL_RE = re.compile(r"download_tt\.php\?u=([A-Za-z0-9+/=]+)", re.I)
_DX_EP_RE = re.compile(r"(\d{1,2})\s*[xX×]\s*(\d{1,3})")
_DX_QFILE_RE = re.compile(
    r"(2160p|1080p|720p|480p|bdremux|blu-?ray|brrip|bdrip|web-?dl|webrip|"
    r"hdrip|microhd|dvdrip|hdtv|4k|hdr)", re.I)
_DX_TR_RE = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_DX_IMG_RE = re.compile(
    r'<img[^>]+(?:src|data-src)=["\']([^"\']*/wp-content/uploads/[^"\']+)["\']',
    re.I)
_DX_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)


def _dx_decode_tt(b64):
    """download_tt.php?u=<base64> -> URL real del .torrent (estatico)."""
    try:
        import base64 as _b64
        u = (b64 or "") + "=" * (-len(b64 or "") % 4)
        dec = _b64.b64decode(u).decode("utf-8", "replace")
        return dec if dec.startswith("http") else None
    except Exception:
        return None


def _dx_detail(url):
    """Ficha DivxTotal: {title, year, image, downloads:[{torrent_url, label,
    season, episode, quality}]}. El .torrent ya sirve para reproducir (a='pl')
    y para derivar el hash de las semillas (sin pasar por el box)."""
    html = _dx_get(url)
    if not html:
        return {"title": "", "downloads": []}
    hm = _DX_H1_RE.search(html)
    title = (re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", hm.group(1))).strip()
             if hm else "")
    body = re.sub(r"<[^>]+>", " ", html)
    ym = re.search(r"\b(19|20)\d{2}\b", body)
    year = ym.group(0) if ym else None
    pm = _DX_IMG_RE.search(html)
    image = pm.group(1) if pm else None
    if image and image.startswith("//"):
        image = "https:" + image
    downloads, seen = [], set()
    rows = _DX_TR_RE.findall(html)
    # Las SERIES traen un enlace por fila <tr> (con su NxNN). Las PELICULAS, en
    # cambio, llevan el boton FUERA de la tabla (bloque "Opcion 2"): habia <tr>
    # en la pagina (7) pero NINGUNO con enlace, y como el fallback era
    # `rows or [html]` -> con la tabla presente el resto del HTML no se miraba
    # JAMAS -> downloads=[] -> sin info_hash -> DivxTotal se quedaba SIN SEMILLAS
    # (y sin .torrent para reproducir). Verificado 2026-08-06 contra fichas
    # reales: pelicula 0/1, serie 9/9. Ahora, ademas de las filas, se miran
    # VENTANAS locales alrededor de cada enlace suelto: la ventana acota el
    # texto del que se deduce el NxNN, para que un "1x09" de otra parte de la
    # pagina no cuele temporada/episodio falsos en una pelicula.
    # Las ventanas van DESPUES de las filas: si un enlace ya salio por su fila,
    # el dedup por `seen` descarta la ventana y se conserva el NxNN de la fila
    # (mas fiable). No se filtra por "el b64 aparece en alguna fila": en la ficha
    # real el mismo b64 asoma dentro de un <tr> escrito de otra forma que el
    # regex NO captura, y ese filtro se cargaba el unico enlace de la pelicula.
    chunks = list(rows)
    for _m in _DX_DL_RE.finditer(html):
        chunks.append(html[max(0, _m.start() - 400):_m.end() + 200])
    for row in chunks:
        bm = _DX_DL_RE.search(row)
        if not bm:
            continue
        turl = _dx_decode_tt(bm.group(1))
        if not turl or turl in seen:
            continue
        seen.add(turl)
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row)).strip()
        # El ULTIMO marcador NxNN del texto (robusto ante "1x09 FINAL").
        ems = _DX_EP_RE.findall(txt)
        season = int(ems[-1][0]) if ems else None
        episode = int(ems[-1][1]) if ems else None
        qm = _DX_QFILE_RE.search(turl.rsplit("/", 1)[-1])
        quality = _cat_norm_quality(qm.group(1)) if qm else ""
        label = ("%dx%02d" % (season, episode)) if (season and episode) else (
            title or "Descargar")
        downloads.append({"torrent_url": turl, "label": label,
                          "season": season, "episode": episode,
                          "quality": quality})
    return {"title": title, "year": year, "image": image,
            "downloads": downloads}


def _dx_episodes_payload(url):
    """Episodios de una serie DivxTotal en el formato que espera la web (igual
    que /catboxeps via box). Enriquece poster/year/nota con TMDB."""
    det = _dx_detail(url)
    eps = []
    for dl in det.get("downloads", []):
        link = dl.get("torrent_url")
        if not link:
            continue
        s, e = dl.get("season"), dl.get("episode")
        label = ("%dx%02d" % (s, e)) if (s and e) else (
            dl.get("label") or "Episodio")
        eps.append({"label": label, "season": s or 0, "episode": e or 0,
                    "quality": dl.get("quality") or "", "link": link,
                    "content_id": link})
    title = _cat_clean_quality(det.get("title") or "")[0]
    # TOPE al enrich: TMDB banea a Render y una llamada colgada se come uno
    # de los 8 hilos (ver §9). Sin poster salen igual; colgados, no.
    meta = (_bounded(lambda: _cat_tmdb(title, "tv"), 2.5, {}) or {}) if title else {}
    return {"title": title or "Serie",
            "poster": meta.get("poster") or det.get("image"),
            "year": meta.get("year") or det.get("year"),
            "rating": meta.get("rating"), "episodes": eps}


# Listado DivxTotal (estrenos/peliculas/series) DIRECTO desde el relay. Es el
# FALLBACK del Inicio: cuando DonTorrent banea/rate-limitea la IP de Render, el
# Inicio NO debe quedar en blanco -> tira de DivxTotal, que el relay si alcanza.
_DX_BROWSE = {"estrenos": "", "peliculas": "peliculas", "series": "series"}


def _dx_browse_items(kind, page=1):
    dom = _dx_domain()
    if not dom:
        return []
    section = _DX_BROWSE.get(kind, "peliculas")
    base = f"https://{dom}/{section}/" if section else f"https://{dom}/"
    url = base if page <= 1 else (base.rstrip("/") + f"/page/{page}/")
    html = _dx_get(url)
    if not html:
        html = _dx_get(url)        # reintento en conexion nueva (tarpit)
        if not html:
            _dx_reprobe_async()    # dominio quiza rotado -> 2o plano
    if not html:
        return []
    items, seen = [], set()
    for it in _dx_parse_items(html, dom):
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        items.append(it)
    return items


# ===========================================================================
# TECLADO REMOTO (escribir busquedas desde el movil)
# ===========================================================================
# El movil abre /kb (escaneando un QR que lleva el codigo del box), escribe la
# busqueda y la envia. El servicio del addon sondea /kb/poll con su codigo y
# abre los resultados en la tele. Almacen en fichero (compartido entre workers,
# efimero: las busquedas son de un solo uso).
_KB_FILE = "/tmp/mw_kb.json"
_KB_TTL = 600   # una busqueda pendiente caduca a los 10 min


class _FileLock:
    """Lock ENTRE PROCESOS (los 2 workers gunicorn) para serializar el
    read-modify-write de las colas de /tmp (mw_kb.json, mw_catjob.json).

    Sin esto habia una RACE: el box sondea /kb/poll cada ~0.5s (load->pop->save)
    y otro worker encola un evento (load->add->save); si el poll guardaba JUSTO
    despues del enqueue, PISABA el evento -> el mando y la busqueda perdian ~75%
    de las ordenes. `os.mkdir` es atomico en todos los SO (sirve tambien para el
    test local en Windows). Si un worker muriese con el lock cogido, se limpia por
    antiguedad (>8s). Si no se consigue en `timeout`, se opera SIN lock (mejor un
    race raro que colgar el mando)."""
    def __init__(self, path, timeout=4.0):
        self._lockdir = path + ".lockd"
        self._timeout = timeout
        self._got = False

    def __enter__(self):
        start = _t.time()
        while True:
            try:
                os.mkdir(self._lockdir)
                self._got = True
                break
            except FileExistsError:
                try:
                    if _t.time() - os.path.getmtime(self._lockdir) > 8:
                        os.rmdir(self._lockdir)
                        continue
                except OSError:
                    pass
                if _t.time() - start > self._timeout:
                    break
                _t.sleep(0.005)
        return self

    def __exit__(self, *exc):
        if self._got:
            try:
                os.rmdir(self._lockdir)
            except OSError:
                pass
        return False


def _kb_load():
    try:
        with open(_KB_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _kb_save(d):
    try:
        tmp = _KB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _KB_FILE)
    except Exception:
        pass


def _kb_clean(d):
    now = _t.time()
    return {k: v for k, v in d.items()
            if (now - v.get("ts", 0)) < _KB_TTL}


# --- Actividad del MOVIL por codigo (sondeo adaptativo del box) ------------
# El movil "se deja ver" en /kb/send, /kb/status, /kb/now y /kb/list (GET);
# /kb/poll devuelve "fast": true si hubo movil hace <3 min y el box sondea a
# 0.3s solo entonces (sin movil baja a ~1.2s -> ~4x menos egress 24/7).
# Escritura throttled (20s por codigo); lectura sin lock (timestamps, un race
# es inocuo). Boxes viejos ignoran la clave "fast" (compatible).
_KB_SEEN_FILE = "/tmp/mw_kb_seen.json"
_KB_SEEN_TTL = 180
_KB_SEEN_WRITE_GAP = 20


def _kb_seen_load():
    try:
        with open(_KB_SEEN_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _kb_phone_seen(code):
    if not code:
        return
    try:
        now = _t.time()
        d = _kb_seen_load()
        if now - d.get(code, 0) < _KB_SEEN_WRITE_GAP:
            return
        d[code] = now
        d = {k: v for k, v in d.items() if now - v < 3600}
        tmp = _KB_SEEN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _KB_SEEN_FILE)
    except Exception:
        pass


def _kb_phone_active(code):
    try:
        return (_t.time() - _kb_seen_load().get(code, 0)) < _KB_SEEN_TTL
    except Exception:
        return True   # ante la duda, sondeo rapido (no degradar el mando)


_KB_ALLOWED_CMDS = {"home", "back", "playpause", "stop",
                    "volup", "voldown", "mute",
                    "seek_fwd", "seek_back", "seekto",
                    "up", "down", "left", "right", "ok",
                    # subtitulos y audio (addon 2.9.66+): lo que mas se echa en
                    # falta viendo series. Una caja vieja los ignora sin mas.
                    "subs", "subsnext", "audionext",
                    "list", "open", "play_ref"}

# Lista (espejo de la pantalla de Kodi) que el box empuja y el movil lee.
_KB_LIST_FILE = "/tmp/mw_kb_list.json"
_KB_LIST_TTL = 600


def _kblist_load():
    try:
        with open(_KB_LIST_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _kblist_save(d):
    try:
        tmp = _KB_LIST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _KB_LIST_FILE)
    except Exception:
        pass


# Estado de reproduccion ("Estas viendo ..."): el box lo sube mientras hay
# video; el movil lo lee en la pestaña Mando. TTL corto: si el box deja de
# subirlo (paro/Kodi cerrado) el panel desaparece solo.
_KB_NOW_FILE = "/tmp/mw_kb_now.json"
_KB_NOW_TTL = 20


def _kbnow_load():
    try:
        with open(_KB_NOW_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _kbnow_save(d):
    try:
        tmp = _KB_NOW_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _KB_NOW_FILE)
    except Exception:
        pass


# Estado del box: latido cada ~30s con version + (opcional) "Continuar viendo".
# /tmp efimero: el estado caduca solo si el box deja de latir (Kodi cerrado),
# que es justo lo que queremos para el "conectado hace Xs".
_KB_STATUS_FILE = "/tmp/mw_kb_status.json"
_KB_STATUS_TTL = 600


def _kbstatus_load():
    try:
        with open(_KB_STATUS_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _kbstatus_save(d):
    try:
        tmp = _KB_STATUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _KB_STATUS_FILE)
    except Exception:
        pass


def _live_boxes(max_age=90):
    """Codes de TODAS las cajas con latido reciente, de más reciente a menos."""
    try:
        now = _t.time()
        out = [(ent.get("ts", 0), code)
               for code, ent in _kbstatus_load().items()
               if (now - ent.get("ts", 0)) < max_age]
        out.sort(reverse=True)
        return [c for _ts, c in out]
    except Exception:
        return []


_LIVE_RR = [0]     # cursor del reparto (round-robin) entre cajas vivas


def _any_live_box(max_age=90):
    """Code de CUALQUIER box con latido reciente (vivo), o None.
    Sirve para que lo que necesita IP residencial (dthtml de DonTorrent cuando
    Render está baneado) funcione AUNQUE el visitante no tenga su código puesto:
    si hay algún Kodi del sistema encendido, lo usamos. El op dthtml es invisible
    para el dueño del box (solo descarga HTML en 2º plano, no toca su pantalla).

    REPARTE (round-robin) en vez de devolver siempre la de latido más reciente:
    antes UNA sola caja cargaba con todo el trabajo prestado del sistema (y el
    tope de concurrencia la ahogaba), mientras las otras 5 estaban ociosas. Los
    trabajos son independientes entre sí, así que repartir no rompe nada y
    multiplica el paralelismo real."""
    boxes = _live_boxes(max_age)
    if not boxes:
        return None
    _LIVE_RR[0] = (_LIVE_RR[0] + 1) % 1000000
    return boxes[_LIVE_RR[0] % len(boxes)]


# Trabajos SIMULTANEOS hacia una caja PRESTADA (la de otro, porque la del
# visitante esta apagada). Sin tope, cada peticion deja un hilo del relay
# esperando hasta 24s: con 8 hilos (2 workers x 4 gthread) bastan unos pocos
# visitantes a la vez para que hasta /ping deje de responder — pasó el
# 2026-08-07 al desplegar dtbk36 (conectaba y negociaba TLS en 0,16s pero no
# contestaba nunca; se arreglo con un reinicio limpio). Con la caja PROPIA no
# se limita: ahi el visitante solo se hace esperar a si mismo.
_BOX_LEND_SEM = _thr.Semaphore(4)
# ...y ADEMAS un tope POR CAJA (2): el global protege los hilos del RELAY, este
# protege a cada Kodi de que le caigan encima todos los trabajos del sistema.
# Con el reparto de `_any_live_box` los trabajos van a cajas distintas, asi que
# el global de 4 se reparte de verdad (antes 2 trabajos a la MISMA caja).
_BOX_LEND_PER = {}
_BOX_LEND_LOCK = _thr.Lock()


def _lend_acquire(box):
    """Hueco para un trabajo hacia una caja PRESTADA. Devuelve el semáforo de la
    caja (para soltarlo luego) o None si no hay hueco -> responder YA, sin colgar
    un hilo del relay."""
    with _BOX_LEND_LOCK:
        sem = _BOX_LEND_PER.get(box)
        if sem is None:
            sem = _thr.Semaphore(2)
            _BOX_LEND_PER[box] = sem
    if not sem.acquire(blocking=False):
        return None
    if not _BOX_LEND_SEM.acquire(blocking=False):
        sem.release()
        return None
    return sem


def _lend_release(sem):
    try:
        _BOX_LEND_SEM.release()
        sem.release()
    except Exception:
        pass


# WolfMax es la fuente LENTA (hasta 24s por peticion) y por tanto la que mas
# tiempo tiene un hilo del relay (solo hay 8) parado esperando. Tope propio:
# como mucho 2 busquedas de WolfMax a la vez EN TODO EL SISTEMA; el resto se
# responde al instante sin WolfMax (las demas fuentes salen igual). Con la cache
# de /catetbox, repetir una busqueda de WolfMax no consume hueco.
_WF_SEM = _thr.Semaphore(2)


def _box_live(code, max_age=90):
    """¿ESE code tiene latido reciente? (mismo criterio que /kb/status)."""
    try:
        ent = _kbstatus_load().get(code) or {}
        return (_t.time() - ent.get("ts", 0)) < max_age
    except Exception:
        return False


def _box_for(code):
    """Caja a la que encolar un trabajo de DATOS (buscar, episodios, resolver un
    link). La suya si esta ENCENDIDA; si esta apagada, CUALQUIER otra caja viva
    del sistema; None si no hay ninguna.

    El 2026-08-06 se vio el problema en vivo: con el salon apagado, /catetbox
    encolaba igualmente al code muerto, el trabajo expiraba y la web se quedaba
    SIN EliteTorrent, DivxTotal ni WolfMax ("solo DonTorrent"), aunque habia otra
    caja encendida que podia haberlo hecho. El relay ya usaba este apaño para
    dthtml/dtmeta (`code if len(code)==6 else _any_live_box()`), pero solo cubria
    "no hay code", no "el code esta muerto", que es el caso normal.

    OJO: esto vale para trabajos INVISIBLES (bajan datos en 2o plano y no tocan
    la pantalla del dueño de la caja). Reproducir y el mando siguen yendo a SU
    caja: eso no se toca."""
    if len(code or "") == 6 and _box_live(code):
        return code
    return _any_live_box()


_KB_PAGE = r"""<!doctype html><html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>MejorWolf</title>
<style>
:root{--bg0:#06070c;--bg1:#0e1320;--card:rgba(255,255,255,.06);--stroke:rgba(255,255,255,.10);
--txt:#f4f6fb;--sub:#8a93a6;--blue:#0a84ff;--blue2:#409cff;--green:#30d158;--red:#ff453a;--glass:rgba(255,255,255,.07)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
/* RED DE SEGURIDAD: pase lo que pase, la pagina no se va de ancho ni se puede
   arrastrar de lado. Las barras que SI se deslizan a proposito (listas,
   temporadas, ultimas busquedas) llevan su propio scroll y no se ven afectadas.
   Ademas, nada puede ser mas ancho que su sitio. */
html,body{max-width:100%;overflow-x:hidden}
img,video,svg,iframe{max-width:100%}
html,body{margin:0;background:#06070c;-webkit-user-select:none;-moz-user-select:none;user-select:none;-webkit-touch-callout:none}
input,textarea{-webkit-user-select:text;-moz-user-select:text;user-select:text}
body{font-family:-apple-system,"SF Pro Display","SF Pro Text",system-ui,Segoe UI,Roboto,sans-serif;
color:var(--txt);min-height:100vh;display:flex;justify-content:center;overscroll-behavior-y:contain;
background:radial-gradient(1200px 700px at 50% -10%,#1b2740 0%,transparent 60%),
radial-gradient(900px 600px at 90% 10%,#241a36 0%,transparent 55%),
linear-gradient(180deg,var(--bg1),var(--bg0)) #06070c}
.wrap{width:100%;max-width:460px;padding:22px 18px 40px}
.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:19px;letter-spacing:.2px}
.brand .dot{width:26px;height:26px;border-radius:9px;background:linear-gradient(145deg,var(--blue2),var(--blue));
display:flex;align-items:center;justify-content:center;font-size:15px;box-shadow:0 6px 16px rgba(10,132,255,.45)}
.code{width:104px;font-variant-numeric:tabular-nums;letter-spacing:3px;font-weight:600;font-size:14px;
color:var(--txt);text-align:center;background:var(--glass);border:1px solid var(--stroke);
padding:9px 10px;border-radius:12px;outline:0}
.seg{display:flex;background:rgba(255,255,255,.05);border:1px solid var(--stroke);border-radius:16px;
padding:5px;margin-bottom:22px}
.seg button{flex:1;border:0;background:transparent;color:var(--sub);font-weight:600;font-size:15px;
padding:11px;border-radius:12px;transition:.25s;cursor:pointer}
.seg button.on{color:#0b1020;background:#f4f6fb;box-shadow:0 4px 14px rgba(0,0,0,.35)}
.pane{animation:fade .35s ease}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.search{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--stroke);
border-radius:16px;padding:14px 16px}
.search svg{flex:none;opacity:.6}
.search input{flex:1;background:transparent;border:0;outline:0;color:var(--txt);font-size:17px}
.search input::placeholder{color:var(--sub)}
.primary{width:100%;margin-top:14px;border:0;border-radius:16px;padding:16px;font-size:17px;font-weight:600;
color:#fff;cursor:pointer;background:linear-gradient(145deg,var(--blue2),var(--blue));
box-shadow:0 10px 26px rgba(10,132,255,.40);transition:.15s}
.primary:active{transform:scale(.98);filter:brightness(.95)}
.card{background:var(--card);border:1px solid var(--stroke);border-radius:22px;padding:18px;margin-top:18px}
.card .ttl{font-size:13px;color:var(--sub);font-weight:600;margin:0 0 14px;letter-spacing:.3px;text-transform:uppercase}
.media{display:flex;justify-content:center;align-items:center;gap:13px}
.rb{width:58px;height:58px;border-radius:50%;border:1px solid var(--stroke);background:var(--glass);
color:var(--txt);display:flex;align-items:center;justify-content:center;cursor:pointer;transition:.15s}
.rb:active{transform:scale(.92);background:rgba(255,255,255,.14)}
.rb.play{width:70px;height:70px;background:linear-gradient(145deg,#3dd46a,#27c257);border-color:transparent;
box-shadow:0 8px 22px rgba(48,209,88,.40);color:#06140a}
.rb.stop{color:var(--red)}
.rb.sk{font-size:15px;font-weight:700;line-height:1}
.rb.sk small{font-size:10px;font-weight:600;opacity:.75;margin-left:1px}
.rb svg{display:block}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.row.three{grid-template-columns:1fr 1fr 1fr}
.pill{border:1px solid var(--stroke);background:var(--glass);color:var(--txt);border-radius:14px;padding:14px;
font-size:15px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:7px;
transition:.15s}
.pill:active{transform:scale(.97);background:rgba(255,255,255,.14)}
.jump{display:flex;gap:10px;margin-top:14px}
.jump input{flex:1;background:var(--glass);border:1px solid var(--stroke);border-radius:14px;color:var(--txt);
padding:14px;font-size:16px;outline:0;text-align:center}
.jump input::placeholder{color:var(--sub);font-size:13px}
.jump .pill{width:108px;flex:none;background:linear-gradient(145deg,var(--blue2),var(--blue));border:0;color:#fff}
.padwrap{display:flex;justify-content:center;margin:6px 0 2px}
.pad{position:relative;width:248px;height:248px;border-radius:50%;border:1px solid var(--stroke);
background:radial-gradient(circle at 50% 32%,rgba(255,255,255,.10),transparent 55%),
conic-gradient(from 0deg,rgba(255,255,255,.05),rgba(255,255,255,.02),rgba(255,255,255,.05));
box-shadow:inset 0 1px 0 rgba(255,255,255,.10),inset 0 -20px 40px rgba(0,0,0,.5),0 18px 40px rgba(0,0,0,.45)}
.pad .arrow{position:absolute;color:var(--sub);font-size:22px;width:56px;height:56px;display:flex;
align-items:center;justify-content:center;cursor:pointer;border-radius:50%;transition:.12s}
.pad .arrow:active{background:rgba(255,255,255,.12);color:#fff}
.pad .up{top:8px;left:50%;transform:translateX(-50%)}
.pad .down{bottom:8px;left:50%;transform:translateX(-50%)}
.pad .left{left:8px;top:50%;transform:translateY(-50%)}
.pad .right{right:8px;top:50%;transform:translateY(-50%)}
.pad .ok{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:104px;height:104px;
border-radius:50%;border:1px solid var(--stroke);background:radial-gradient(circle at 50% 35%,#2a3346,#161c2a);
display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;letter-spacing:1px;
cursor:pointer;box-shadow:0 8px 20px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.12);transition:.12s}
.pad .ok:active{transform:translate(-50%,-50%) scale(.95)}
.navrow{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
.titlebar{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.titlebar h2{margin:0;font-size:22px;font-weight:700;letter-spacing:.2px}
.tools{display:flex;gap:8px}
.tools .pill{padding:10px 14px;border-radius:12px;font-size:16px}
.item{display:flex;align-items:center;gap:14px;background:var(--card);border:1px solid var(--stroke);
border-radius:18px;padding:10px;margin-bottom:11px;cursor:pointer;transition:.15s}
.item:active{transform:scale(.99);background:rgba(255,255,255,.10)}
.item .poster{width:56px;height:84px;border-radius:11px;object-fit:cover;flex:none;background:#21262d;
box-shadow:0 6px 16px rgba(0,0,0,.5)}
.poster.noimg{background:#21262d}
.meta{flex:1;min-width:0}
.meta .t{font-size:16px;font-weight:600;line-height:1.25}
.meta .s{font-size:13px;color:var(--sub);margin-top:4px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tag{background:rgba(255,255,255,.08);border:1px solid var(--stroke);border-radius:7px;padding:2px 7px;
font-size:11px;font-weight:600;color:#cfd6e4}
.tag.q4k{color:#ffd479;border-color:rgba(255,212,121,.3)}
.tag.rar{color:#ff9f6e;border-color:rgba(255,159,110,.42);background:rgba(255,159,110,.10)}
.score{color:var(--green);font-weight:700}
.chev{color:var(--sub);font-size:20px;flex:none;opacity:.7}
.hint{color:var(--sub);font-size:13px;text-align:center;margin:24px 8px}
#st{margin-top:14px;text-align:center;font-size:15px;min-height:22px}
.ok{color:var(--green)}.err{color:var(--red)}
.hidden{display:none}
.foot{text-align:center;color:var(--sub);font-size:12px;margin-top:24px;opacity:.6}
.micbtn{flex:none;border:0;background:transparent;color:var(--sub);width:36px;height:36px;border-radius:50%;
display:flex;align-items:center;justify-content:center;cursor:pointer;transition:.15s;margin:-6px -4px -6px 0}
.micbtn:active{transform:scale(.9)}
.micbtn.rec{color:#fff;background:var(--red);box-shadow:0 0 0 0 rgba(255,69,58,.5);animation:micpulse 1.2s infinite}
@keyframes micpulse{0%{box-shadow:0 0 0 0 rgba(255,69,58,.5)}70%{box-shadow:0 0 0 12px rgba(255,69,58,0)}100%{box-shadow:0 0 0 0 rgba(255,69,58,0)}}
.np{margin:2px 0 18px}
.np.hidden{display:none}
.np .nplab{font-size:11px;color:var(--sub);font-weight:600;letter-spacing:.4px;text-transform:uppercase;margin-bottom:5px}
.np .npttl{font-size:17px;font-weight:700;line-height:1.25;margin-bottom:11px}
.npbar{height:5px;border-radius:3px;background:rgba(255,255,255,.10);overflow:hidden}
.npbar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue2),var(--blue));
border-radius:3px;transition:width .95s linear}
.nprow{display:flex;justify-content:space-between;align-items:baseline;margin-top:8px;font-size:12.5px;
color:var(--sub);font-variant-numeric:tabular-nums}
.nprow .npf{color:#cfd6e4;font-weight:600}
.recb{background:linear-gradient(145deg,rgba(10,132,255,.18),rgba(10,132,255,.05));
border:1px solid rgba(10,132,255,.38);border-radius:16px;padding:14px 16px;margin-bottom:16px}
.recb.hidden{display:none}
.recb .rlab{font-size:11px;color:#8fb6ff;font-weight:700;letter-spacing:.4px;text-transform:uppercase;margin-bottom:4px}
.recb .rtit{font-size:17px;font-weight:700;line-height:1.25;color:#eaf1ff}
.recb .primary{margin-top:12px}
.contc{position:relative;background:linear-gradient(145deg,rgba(48,209,88,.16),rgba(48,209,88,.05));
border:1px solid rgba(48,209,88,.36);border-radius:16px;padding:14px 16px;margin-bottom:16px}
.contc.hidden{display:none}
.contc .cx{position:absolute;top:8px;right:8px;width:28px;height:28px;border-radius:50%;
display:flex;align-items:center;justify-content:center;font-size:20px;line-height:1;color:var(--sub);
cursor:pointer;transition:.15s;border:1px solid var(--stroke);background:rgba(255,255,255,.05)}
.contc .cx:active{transform:scale(.9);background:rgba(255,255,255,.14)}
.contc .clab{font-size:11px;color:#8fe0a6;font-weight:700;letter-spacing:.4px;text-transform:uppercase;margin-bottom:4px}
.contc .ctit{font-size:15px;font-weight:600;color:#eafff0;line-height:1.3}
.contc .primary{margin-top:11px;background:linear-gradient(145deg,#3dd46a,#27c257);
box-shadow:0 8px 22px rgba(48,209,88,.35);color:#06140a}
.boxst{display:flex;align-items:center;justify-content:center;gap:7px;font-size:12px;
color:var(--sub);margin-top:18px;min-height:16px}
.boxst .dot{width:8px;height:8px;border-radius:50%;flex:none}
.boxst .dot.on{background:#30d158;box-shadow:0 0 7px rgba(48,209,88,.7)}
.boxst .dot.off{background:#ff453a}
</style></head><body><div class="wrap">

<div class="top">
 <div class="brand"><span class="dot">&#128058;</span> MejorWolf</div>
 <input id="code" class="code" inputmode="numeric" maxlength="6" placeholder="código">
</div>

<div class="seg">
 <button id="tab-mando" class="on" onclick="showTab('mando')">Mando</button>
 <button id="tab-lista" onclick="showTab('lista')">Lista</button>
</div>

<section id="pane-mando" class="pane">
 <div id="recb" class="recb hidden">
  <div class="rlab">&#128233; Te han recomendado</div>
  <div id="rtit" class="rtit"></div>
  <button id="recbtn" class="primary" onclick="recAction()">Buscar en mi tele</button>
 </div>
 <div id="contc" class="contc hidden">
  <div class="cx" onclick="contDismiss()" title="Descartar">&times;</div>
  <div class="clab">&#9654; Continuar viendo</div>
  <div id="contt" class="ctit"></div>
  <button class="primary" onclick="contPlay()">Continuar en mi tele</button>
 </div>
 <div class="search">
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#8a93a6" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
  <input id="q" autocomplete="off" placeholder="Buscar película o serie..." enterkeyhint="search">
  <button id="mic" class="micbtn" type="button" aria-label="Buscar por voz"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2.5" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/></svg></button>
 </div>
 <button id="go" class="primary">Buscar en la tele</button>

 <div class="card">
  <p class="ttl">Reproducción</p>
  <div id="np" class="np hidden">
   <div class="nplab">Estás viendo</div>
   <div id="npttl" class="npttl"></div>
   <div class="npbar"><i id="npfill"></i></div>
   <div class="nprow"><span id="npa"></span><span id="npf" class="npf"></span></div>
  </div>
  <div class="media">
   <div class="rb sk" onclick="cmd('seek_back')">-10<small>s</small></div>
   <div class="rb stop" onclick="cmd('stop')"><svg width="22" height="22" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/></svg></div>
   <div class="rb play" onclick="cmd('playpause')"><svg width="30" height="30" viewBox="0 0 24 24"><path d="M8 6 L18 12 L8 18 Z" fill="currentColor"/></svg></div>
   <div class="rb sk" onclick="cmd('seek_fwd')">+30<small>s</small></div>
  </div>
  <div class="jump">
   <input id="mn" type="number" min="0" inputmode="numeric" placeholder="ir al minuto exacto...">
   <div class="pill" onclick="seekTo()">Saltar a</div>
  </div>
  <div class="row three" style="margin-top:14px">
   <div class="pill" onclick="cmd('voldown')">&#128265;</div>
   <div class="pill" onclick="cmd('mute')">&#128263;</div>
   <div class="pill" onclick="cmd('volup')">&#128266;</div>
  </div>
 </div>

 <div class="card">
  <p class="ttl">Navegación</p>
  <div class="padwrap"><div class="pad">
   <div class="arrow up" onclick="cmd('up')">&#9650;</div>
   <div class="arrow left" onclick="cmd('left')">&#9664;</div>
   <div class="ok" onclick="cmd('ok')">OK</div>
   <div class="arrow right" onclick="cmd('right')">&#9654;</div>
   <div class="arrow down" onclick="cmd('down')">&#9660;</div>
  </div></div>
  <div class="navrow">
   <div class="pill" onclick="cmd('home')">&#127968; Inicio</div>
   <div class="pill" onclick="cmd('back')">&#8617; Atrás</div>
  </div>
 </div>
</section>

<section id="pane-lista" class="pane hidden">
 <div class="titlebar">
  <h2 id="ltitle">Lista</h2>
  <div class="tools">
   <div class="pill" onclick="listBack()">&#8617;</div>
   <div class="pill" onclick="loadList()">&#8635;</div>
  </div>
 </div>
 <div id="items"></div>
</section>

<div id="st"></div>

<div id="lb" onclick="closeBig(event)" style="position:fixed;inset:0;background:rgba(0,0,0,.93);z-index:50;display:none;flex-direction:column;align-items:center;justify-content:center;padding:20px;gap:14px">
 <img id="lbimg" alt="" style="max-width:86%;max-height:64vh;border-radius:14px;object-fit:contain;box-shadow:0 12px 50px rgba(0,0,0,.7)">
 <div id="lblbl" style="color:#f4f6fb;text-align:center;font-size:16px;font-weight:600;max-width:92%"></div>
 <div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:380px">
  <button class="primary" style="margin:0" onclick="lbOpen(event)">Abrir / Reproducir</button>
  <div style="display:flex;gap:10px">
   <button class="pill" style="flex:1" onclick="shareItem(event)">&#128229; Compartir</button>
   <button class="pill" style="flex:1" onclick="closeBig(event)">Cerrar</button>
  </div>
 </div>
</div>

<div id="boxst" class="boxst"></div>
<div class="foot">MejorWolf &middot; mando remoto</div>
</div><script>
var p=new URLSearchParams(location.search);
var code=document.getElementById('code'),st=document.getElementById('st'),q=document.getElementById('q');
function loadCode(){try{return localStorage.getItem('mw_code')||'';}catch(e){return '';}}
function saveCode(){try{var c=code.value.trim();if(c.length===6)localStorage.setItem('mw_code',c);}catch(e){}}
var _cp=(p.get('c')||'').replace(/\D/g,'');
if(_cp.length===6) code.value=_cp; else code.value=loadCode();
saveCode(); code.addEventListener('input',saveCode);
function haptic(){try{if(navigator.vibrate)navigator.vibrate(9);}catch(e){}}
var msgTimer=null;
function setMsg(t,cls){st.className=cls||'';st.textContent=t;
 if(msgTimer){clearTimeout(msgTimer);msgTimer=null;}
 if(t&&cls!=='err'){msgTimer=setTimeout(function(){st.textContent='';st.className='';},2500);}}
function getCode(){var c=code.value.trim();if(c.length<6){setMsg('Falta el código de 6 cifras','err');return null;}return c;}
function post(body,okmsg){
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(function(r){return r.json()})
  .then(function(j){if(j&&j.ok){if(okmsg)setMsg(okmsg,'ok');}else{setMsg((j&&j.error)||'Error','err');}})
  .catch(function(){setMsg('Sin conexión','err');});}
function send(){haptic();var c=getCode();if(!c)return;var query=q.value.trim();
 if(!query){setMsg('Escribe algo','err');return;}
 setMsg('Enviando...');post({code:c,query:query},'Enviado. Mira la tele');q.value='';}
function cmd(x){haptic();var c=getCode();if(!c)return;post({code:c,cmd:x},'Enviado');}
function seekTo(){haptic();var c=getCode();if(!c)return;var mn=document.getElementById('mn').value.trim();
 if(mn===''){setMsg('Pon un minuto','err');return;}
 post({code:c,cmd:'seekto',min:parseInt(mn,10)},'Saltando al minuto '+mn);}
document.getElementById('go').onclick=send;
q.addEventListener('keydown',function(e){if(e.key==='Enter')send();});

function showTab(t){haptic();
 document.getElementById('pane-mando').classList.toggle('hidden',t!=='mando');
 document.getElementById('pane-lista').classList.toggle('hidden',t!=='lista');
 document.getElementById('tab-mando').classList.toggle('on',t==='mando');
 document.getElementById('tab-lista').classList.toggle('on',t==='lista');
 if(t==='lista') startLive(); else stopLive();
 if(t==='mando'){startNow();startStatus();}else{stopNow();stopStatus();}}

var listTs=0,listLive=false,listReqTimer=null,listPollT=null;
function parseLabel(label){
 var qual='';var m=label.match(/\[([^\]]+)\]/);if(m)qual=m[1];
 var yr='';var ym=label.match(/\b(19|20)\d{2}\b/);if(ym)yr=ym[0];
 var t=label.replace(/\[[^\]]*\]/g,'').replace(/\((19|20)\d{2}\)/g,'').replace(/\s{2,}/g,' ').trim();
 t=t.replace(/[\s.\-:]+$/,'').trim();
 return {t:t||label,y:yr,q:qual};}
function ph(){var d=document.createElement('div');d.className='poster noimg';return d;}
// ---- Badge RAR (📦) en la Lista: resuelto perezosamente vía /dtpacked, cacheado ----
var packedCache={},packedQueue=[],packedActive=0;
function addRarBadge(sub){if(!sub||sub.querySelector('.tag.rar'))return;
 var b=document.createElement('span');b.className='tag rar';b.textContent='📦 RAR';sub.appendChild(b);}
function markPacked(sub,ref){
 if(!ref||ref.a!=='dt'||!ref.c||!sub)return;
 var key='dt:'+ref.tb+':'+ref.c;var v=packedCache[key];
 if(v===true){addRarBadge(sub);return;}
 if(v===false||v==='pending')return;
 packedCache[key]='pending';packedQueue.push({sub:sub,key:key,c:ref.c,tb:ref.tb});pumpPacked();}
function pumpPacked(){
 while(packedActive<2&&packedQueue.length){
  var job=packedQueue.shift();packedActive++;
  (function(job){
   fetch('/dtpacked?c='+encodeURIComponent(job.c)+'&tb='+encodeURIComponent(job.tb))
    .then(function(r){return r.json()}).then(function(j){packedActive--;
     if(j&&j.packed===true){packedCache[job.key]=true;if(job.sub&&job.sub.isConnected)addRarBadge(job.sub);}
     else if(j&&j.packed===false){packedCache[job.key]=false;}
     else{delete packedCache[job.key];}
     pumpPacked();})
    .catch(function(){packedActive--;delete packedCache[job.key];pumpPacked();});
  })(job);}}
function renderList(items){
 var cont=document.getElementById('items');cont.innerHTML='';
 if(!items||!items.length){cont.innerHTML='<p class="hint">Abre una sección en la tele (Estrenos, Cine, una búsqueda...).</p>';return;}
 items.forEach(function(it,i){
  var pl=parseLabel(it.label||'');
  var row=document.createElement('div');row.className='item';
  row.onclick=function(){openItem(i,it.label,it.dir);};
  if(it.poster){var img=document.createElement('img');img.className='poster';img.loading='lazy';img.src=it.poster;
   img.onerror=function(){var d=ph();if(img.parentNode)img.parentNode.replaceChild(d,img);};
   img.onclick=function(e){e.stopPropagation();showBig(it.poster,it.label,i,it.dir,it.ref);};
   row.appendChild(img);}
  else row.appendChild(ph());
  var meta=document.createElement('div');meta.className='meta';
  var tt=document.createElement('div');tt.className='t';tt.textContent=pl.t;meta.appendChild(tt);
  var sub=document.createElement('div');sub.className='s';
  if(pl.y){var ys=document.createElement('span');ys.textContent=pl.y;sub.appendChild(ys);}
  if(pl.q){var qs=document.createElement('span');qs.className='tag'+(/4k|2160/i.test(pl.q)?' q4k':'');qs.textContent=pl.q;sub.appendChild(qs);}
  if(it.rating&&it.rating>0){var sc=document.createElement('span');sc.className='score';sc.textContent='★ '+(''+it.rating).replace('.',',');sub.appendChild(sc);}
  if(sub.childNodes.length||(it.ref&&it.ref.a==='dt'))meta.appendChild(sub);
  row.appendChild(meta);
  var ch=document.createElement('div');ch.className='chev';ch.innerHTML=it.dir?'&#8250;':'&#9654;';row.appendChild(ch);
  cont.appendChild(row);
  if(it.ref&&it.ref.a==='dt')markPacked(sub,it.ref);});}
function fetchList(){
 var c=code.value.trim();if(c.length>=6){
  fetch('/kb/list?code='+c).then(function(r){return r.json()}).then(function(j){
   if(j&&j.ts&&j.ts!==listTs){listTs=j.ts;renderList(j.items);document.getElementById('ltitle').textContent=j.title||'Lista';}
  }).catch(function(){});}
 if(listLive) listPollT=setTimeout(fetchList,600);}
function reqList(){var c=code.value.trim();if(c.length>=6) post({code:c,cmd:'list'});}
function startLive(){var c=getCode();if(!c)return;if(listLive)return;
 listLive=true;listTs=0;setMsg('');reqList();fetchList();listReqTimer=setInterval(reqList,2000);}
function stopLive(){listLive=false;
 if(listReqTimer){clearInterval(listReqTimer);listReqTimer=null;}
 if(listPollT){clearTimeout(listPollT);listPollT=null;}}
function loadList(){haptic();listTs=0;reqList();}
function listBack(){haptic();var c=getCode();if(!c)return;post({code:c,cmd:'back'});}
function openItem(i,label,dir){haptic();var c=getCode();if(!c)return;setMsg('Abriendo...','ok');
 post({code:c,cmd:'open',i:i,label:label||''});
 if(!dir){setTimeout(function(){showTab('mando');},450);}}
var lbIdx=-1,lbLabel='',lbDir=false,lbRef=null;
function showBig(poster,label,i,dir,ref){lbIdx=i;lbLabel=label||'';lbDir=!!dir;lbRef=ref||null;
 document.getElementById('lbimg').src=poster||'';
 document.getElementById('lblbl').textContent=label||'';
 document.getElementById('lb').style.display='flex';}
function closeBig(e){if(e)e.stopPropagation();document.getElementById('lb').style.display='none';}
function lbOpen(e){if(e)e.stopPropagation();var i=lbIdx,l=lbLabel,dd=lbDir;closeBig();if(i>=0)openItem(i,l,dd);}
// ---- Estas viendo (now playing) ----
var npData=null,npLive=false,npPollT=null,npTick=null;
function fmtDur(s){s=Math.max(0,Math.round(s));var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;
 var mm=(h>0?String(m).padStart(2,'0'):String(m));
 return (h>0?h+':':'')+mm+':'+String(x).padStart(2,'0');}
function fmtClock(d){return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
function renderNow(){var box=document.getElementById('np');
 if(!npData){box.classList.add('hidden');return;}
 box.classList.remove('hidden');
 var el=npData.elapsed; if(!npData.paused) el+=(Date.now()-npData.at)/1000;
 if(npData.total>0&&el>npData.total) el=npData.total;
 document.getElementById('npttl').textContent=npData.title||'Reproduciendo';
 var pct=npData.total>0?Math.min(100,el/npData.total*100):0;
 document.getElementById('npfill').style.width=pct+'%';
 document.getElementById('npa').textContent=fmtDur(el)+(npData.total>0?' / '+fmtDur(npData.total):'');
 var f=document.getElementById('npf');
 if(npData.paused){f.textContent='En pausa';}
 else if(npData.total>0){var fin=new Date(Date.now()+(npData.total-el)*1000);f.textContent='Finaliza a las '+fmtClock(fin);}
 else{f.textContent='';}}
function pollNow(){var c=code.value.trim();
 if(c.length>=6){fetch('/kb/now?code='+c).then(function(r){return r.json()}).then(function(j){
  if(j&&j.np){npData={title:j.np.title||'',elapsed:+j.np.elapsed||0,total:+j.np.total||0,paused:!!j.np.paused,at:Date.now()};}
  else{npData=null;} renderNow();
 }).catch(function(){});}
 if(npLive) npPollT=setTimeout(pollNow,2000);}
function startNow(){if(npLive)return;npLive=true;pollNow();npTick=setInterval(renderNow,1000);}
function stopNow(){npLive=false;if(npPollT){clearTimeout(npPollT);npPollT=null;}if(npTick){clearInterval(npTick);npTick=null;}}

// ---- Estado del box (conectado/versión) + Continuar viendo ----
var stLive=false,stPollT=null,contRef=null,contDismissed='';
try{contDismissed=localStorage.getItem('mw_cont_dismiss')||'';}catch(e){}
function contKey(c){return c?((c.a||'')+':'+(c.ci||c.u||'')+':'+(c.title||'')):'';}
function fmtMM(s){s=Math.max(0,Math.round(s));var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;
 return (h>0?h+':':'')+(h>0?String(m).padStart(2,'0'):''+m)+':'+String(x).padStart(2,'0');}
function renderStatus(j){
 var el=document.getElementById('boxst'),cc=document.getElementById('contc');
 if(!j){el.innerHTML='';cc.classList.add('hidden');contRef=null;return;}
 var conn=!!j.connected;
 el.innerHTML='<span class="dot '+(conn?'on':'off')+'"></span>'+(conn?('Tele conectada'+(j.v?' · MejorWolf '+j.v:'')):'Tele desconectada — abre Kodi en la tele');
 var c=j.cont;
 if(conn&&c&&c.total>0&&c.elapsed<c.total*0.92&&contKey(c)!==contDismissed){
  contRef=c;
  document.getElementById('contt').textContent='«'+(c.title||'')+'»  ·  '+fmtMM(c.elapsed)+' / '+fmtMM(c.total);
  cc.classList.remove('hidden');
 }else{contRef=null;cc.classList.add('hidden');}}
function pollStatus(){var c=code.value.trim();
 if(c.length>=6){fetch('/kb/status?code='+c).then(function(r){return r.json()}).then(renderStatus).catch(function(){});}
 if(stLive) stPollT=setTimeout(pollStatus,12000);}
function startStatus(){if(stLive)return;stLive=true;pollStatus();}
function stopStatus(){stLive=false;if(stPollT){clearTimeout(stPollT);stPollT=null;}}
function contPlay(){var cc=document.getElementById('contc');if(cc)cc.classList.add('hidden');
 haptic();var c=getCode();if(!c)return;if(!contRef)return;
 var body={code:c,cmd:'play_ref',a:contRef.a,t:contRef.title||'',resume:contRef.elapsed};
 if(contRef.a==='dt'){body.c=contRef.ci;body.tb=contRef.tb;}else{body.u=contRef.u;}
 setMsg('Reanudando en tu tele...','ok');post(body,'Reanudando');}
function contDismiss(){if(contRef)contDismissed=contKey(contRef);
 try{localStorage.setItem('mw_cont_dismiss',contDismissed);}catch(e){}
 var cc=document.getElementById('contc');if(cc)cc.classList.add('hidden');haptic();}

// ---- Busqueda por voz (Web Speech API, gratis; Chrome Android) ----
var rec=null;
(function initVoice(){
 var SR=window.SpeechRecognition||window.webkitSpeechRecognition;
 var mic=document.getElementById('mic');
 if(!SR){mic.style.display='none';return;}
 mic.onclick=function(){haptic();
  if(rec){try{rec.stop();}catch(e){}rec=null;return;}
  try{rec=new SR();}catch(e){return;}
  rec.lang='es-ES';rec.interimResults=false;rec.maxAlternatives=1;
  mic.classList.add('rec');setMsg('Escuchando...');
  rec.onresult=function(e){try{q.value=e.results[0][0].transcript||'';}catch(_){}};
  rec.onerror=function(){setMsg('No te he oído','err');};
  rec.onend=function(){mic.classList.remove('rec');rec=null;
   if(q.value.trim()){setMsg('Revisa y pulsa Buscar','ok');try{q.focus();}catch(_){}}else{setMsg('');}};
  try{rec.start();}catch(e){mic.classList.remove('rec');rec=null;}};
})();

document.addEventListener('visibilitychange',function(){
 if(document.hidden){stopLive();stopNow();stopStatus();}
 else{ if(!document.getElementById('pane-lista').classList.contains('hidden')) startLive();
       if(!document.getElementById('pane-mando').classList.contains('hidden')){ startNow(); startStatus(); } }});

// ---- Compartir pelicula por enlace (sin guardar nada: el titulo va en el link) ----
function shareItem(e){if(e)e.stopPropagation();
 var pl=parseLabel(lbLabel||'');var t=(pl.t||lbLabel||'').trim();
 if(!t){setMsg('No hay título para compartir','err');return;}
 var link;
 if(lbRef&&lbRef.a&&!lbDir){
  link=location.origin+'/kb?play='+encodeURIComponent(lbRef.a)+'&t='+encodeURIComponent(t)+(pl.y?'&yr='+encodeURIComponent(pl.y):'');
  if(lbRef.a==='dt'){link+='&ci='+encodeURIComponent(lbRef.c)+'&tb='+encodeURIComponent(lbRef.tb);}
  else if(lbRef.a==='pl'){link+='&u='+encodeURIComponent(lbRef.u);}
 }else{
  link=location.origin+'/kb?ver='+encodeURIComponent(t)+(pl.y?'&yr='+encodeURIComponent(pl.y):'');
 }
 var nice=t+(pl.y?' ('+pl.y+')':'');
 closeBig();
 if(navigator.share){navigator.share({title:'MejorWolf',text:'Te recomiendo «'+nice+'» en MejorWolf',url:link}).then(function(){},function(){});}
 else if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(link).then(function(){setMsg('Enlace copiado','ok');},function(){window.prompt('Copia el enlace y mándalo:',link);});}
 else{window.prompt('Copia el enlace y mándalo:',link);}}
var recMode='ver',recRef=null;
function recSearch(){var b=document.getElementById('recb');if(b)b.classList.add('hidden');
 haptic();var c=getCode();if(!c)return;var query=((recRef&&recRef.t)||'').trim();
 if(!query){setMsg('Nada que buscar','err');return;}
 setMsg('Buscando en tu tele...','ok');post({code:c,query:query},'Buscado. Mira la tele');}
function recPlay(){var b=document.getElementById('recb');if(b)b.classList.add('hidden');
 haptic();var c=getCode();if(!c)return;if(!recRef){return;}
 var body={code:c,cmd:'play_ref',a:recRef.a,t:recRef.t};
 if(recRef.a==='dt'){body.c=recRef.c;body.tb=recRef.tb;}else{body.u=recRef.u;}
 setMsg('Abriendo en tu tele...','ok');post(body,'Abriendo en tu tele');}
function recAction(){if(recMode==='play')recPlay();else recSearch();}
startNow();startStatus();
(function handleShare(){
 var play=p.get('play');
 if(play){var t=p.get('t')||'';var yr=p.get('yr')||'';
  recMode='play';recRef={a:play,t:t,c:p.get('ci')||'',tb:p.get('tb')||'',u:p.get('u')||''};
  document.getElementById('rtit').textContent=t+(yr?' ('+yr+')':'');
  document.getElementById('recbtn').textContent='▶ Ver ahora en mi tele';
  document.getElementById('recb').classList.remove('hidden');
  try{history.replaceState({},'',location.pathname);}catch(e){}
  showTab('mando');return;}
 var v=p.get('ver');if(!v)return;var yr2=p.get('yr')||'';
 recMode='ver';recRef={t:v};
 document.getElementById('rtit').textContent=v+(yr2?' ('+yr2+')':'');
 document.getElementById('recbtn').textContent='Buscar en mi tele';
 document.getElementById('recb').classList.remove('hidden');
 try{history.replaceState({},'',location.pathname);}catch(e){}
 showTab('mando');})();
</script></body></html>"""


@app.get("/kb")
def kb_page():
    # La web del mando ahora sirve el CATALOGO (app principal). El QR del box
    # apunta aqui, asi que abre la app completa. El mando clasico: /kb/clasico.
    return _serve_page(_CAT_PAGE)


@app.get("/kb/clasico")
def kb_page_clasico():
    return _serve_page(_KB_PAGE)


# Limite de peticiones por IP en /kb/send (anti fuerza-bruta del codigo, sin
# molestar al uso normal: en vivo se mandan ~1 'list'/2s). En memoria.
_RL = {}
_RL_MAX = 45
_RL_WIN = 10.0


def _rate_ok(ip):
    now = _t.time()
    q = _RL.get(ip)
    if q is None:
        q = []
        _RL[ip] = q
    while q and now - q[0] > _RL_WIN:
        q.pop(0)
    if len(q) >= _RL_MAX:
        return False
    q.append(now)
    if len(_RL) > 800:        # poda SOLO IPs inactivas: limpiar TODO reseteaba
        for k, v in list(_RL.items()):   # tambien el contador de un atacante
            if not v or (now - v[-1]) > _RL_WIN:
                _RL.pop(k, None)
    return True


@app.post("/kb/send")
def kb_send():
    # El ULTIMO valor de X-Forwarded-For lo añade el proxy de Render (fiable);
    # los anteriores los pone el cliente y son FALSEABLES -> con el primero, un
    # atacante rotaba el header y se saltaba el rate-limit.
    ip = (request.headers.get("X-Forwarded-For", "")
          or request.remote_addr or "?").split(",")[-1].strip()
    if not _rate_ok(ip):
        return jsonify({"ok": False, "error": "demasiadas peticiones"}), 429
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    code = re.sub(r"\D", "", str(body.get("code") or ""))[:6]
    if len(code) != 6:
        return jsonify({"ok": False, "error": "código inválido"}), 400
    query = (body.get("query") or "").strip()[:120]
    cmd = (body.get("cmd") or "").strip().lower()[:20]
    if query:
        ev = {"q": query}
    elif cmd in _KB_ALLOWED_CMDS:
        ev = {"c": cmd}
        if cmd == "open":
            try:
                ev["i"] = int(body.get("i"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "índice inválido"}), 400
            ev["label"] = (body.get("label") or "")[:160]   # para verificar
        elif cmd == "seekto":
            try:
                ev["min"] = max(0, int(body.get("min")))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "minuto inválido"}), 400
        elif cmd == "play_ref":
            a = (body.get("a") or "").strip().lower()[:4]
            ev["a"] = a
            ev["t"] = (body.get("t") or "")[:160]
            try:
                ev["resume"] = max(0, int(body.get("resume") or 0))
            except (TypeError, ValueError):
                ev["resume"] = 0
            if a == "dt":
                ev["cid"] = re.sub(r"\D", "", str(body.get("c") or ""))[:12]
                ev["tb"] = re.sub(r"[^a-z0-9_]", "",
                                  str(body.get("tb") or "").lower())[:24]
                if not (ev["cid"] and ev["tb"]):
                    return jsonify({"ok": False,
                                    "error": "referencia inválida"}), 400
            elif a == "pl":
                u = (body.get("u") or "").strip()
                if not (u.startswith("magnet:") or u.startswith("http")
                        or u.endswith(".torrent")):
                    return jsonify({"ok": False,
                                    "error": "enlace inválido"}), 400
                ev["u"] = u[:2000]
            else:
                return jsonify({"ok": False,
                                "error": "referencia inválida"}), 400
    else:
        return jsonify({"ok": False, "error": "nada que enviar"}), 400
    # Encolar BAJO el lock entre procesos (_kb_enqueue): este RMW compite con
    # /kb/poll (load->pop->save cada ~0.3s); sin lock una orden del movil podia
    # perderse o ejecutarse duplicada (la misma race que describe _FileLock).
    _kb_enqueue(code, ev)
    _kb_phone_seen(code)
    return jsonify({"ok": True})


@app.get("/kb/poll")
def kb_poll():
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"events": []})
    with _FileLock(_KB_FILE):   # serializa con _kb_enqueue (no perder eventos)
        d = _kb_clean(_kb_load())
        entry = d.pop(code, None)
        if entry:
            _kb_save(d)   # consumo: devolvemos los eventos pendientes y limpiamos
    # "fast": hay un movil delante (visto <3 min) -> el box sondea a 0.3s;
    # si no, el box (2.9.52+) espacia el sondeo. Boxes viejos la ignoran.
    fast = _kb_phone_active(code)
    return jsonify({"events": entry.get("ev", []), "fast": fast}) if entry \
        else jsonify({"events": [], "fast": fast})


@app.post("/kb/list")
def kb_list_push():
    """El box sube la lista (espejo) de la pantalla actual de Kodi."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    code = re.sub(r"\D", "", str(body.get("code") or ""))[:6]
    if len(code) != 6:
        return jsonify({"ok": False}), 400
    items = body.get("items") or []
    if not isinstance(items, list):
        items = []
    d = _kblist_load()
    now = _t.time()
    d = {k: v for k, v in d.items() if (now - v.get("ts", 0)) < _KB_LIST_TTL}
    d[code] = {"items": items[:120], "title": (body.get("title") or "")[:80],
               "ts": now}
    _kblist_save(d)
    return jsonify({"ok": True})


@app.get("/kb/list")
def kb_list_get():
    """El movil lee la lista (espejo) actual."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"items": [], "ts": 0})
    _kb_phone_seen(code)
    d = _kblist_load()
    entry = d.get(code) or {}
    return jsonify({"items": entry.get("items", []),
                    "title": entry.get("title", ""),
                    "ts": entry.get("ts", 0)})


@app.post("/kb/now")
def kb_now_push():
    """El box sube el estado de reproduccion actual (o null si nada suena)."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    code = re.sub(r"\D", "", str(body.get("code") or ""))[:6]
    if len(code) != 6:
        return jsonify({"ok": False}), 400
    np = body.get("np")
    d = _kbnow_load()
    now = _t.time()
    d = {k: v for k, v in d.items() if (now - v.get("ts", 0)) < _KB_NOW_TTL}
    if isinstance(np, dict):
        d[code] = {"np": {"title": str(np.get("title", ""))[:120],
                          "elapsed": int(np.get("elapsed", 0) or 0),
                          "total": int(np.get("total", 0) or 0),
                          "paused": bool(np.get("paused"))},
                   "ts": now}
    else:
        d.pop(code, None)
    _kbnow_save(d)
    return jsonify({"ok": True})


@app.get("/kb/now")
def kb_now_get():
    """El movil lee el estado de reproduccion actual."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"np": None})
    _kb_phone_seen(code)
    d = _kbnow_load()
    entry = d.get(code) or {}
    if entry and (_t.time() - entry.get("ts", 0)) < _KB_NOW_TTL:
        return jsonify({"np": entry.get("np")})
    return jsonify({"np": None})


@app.post("/kb/status")
def kb_status_push():
    """Latido del box: version + (opcional) 'Continuar viendo'."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    code = re.sub(r"\D", "", str(body.get("code") or ""))[:6]
    if len(code) != 6:
        return jsonify({"ok": False}), 400
    now = _t.time()
    d = _kbstatus_load()
    d = {k: v for k, v in d.items() if (now - v.get("ts", 0)) < _KB_STATUS_TTL}
    entry = {"v": str(body.get("v", ""))[:16], "ts": now}
    diag = body.get("diag")
    if isinstance(diag, dict):
        entry["diag"] = {k: (str(v)[:40] if v is not None else None)
                         for k, v in list(diag.items())[:10]}
    cont = body.get("cont")
    if isinstance(cont, dict):
        entry["cont"] = {
            "title": str(cont.get("title", ""))[:120],
            "a": str(cont.get("a", ""))[:4],
            "ci": re.sub(r"\D", "", str(cont.get("ci", "")))[:12],
            "tb": re.sub(r"[^a-z0-9_]", "",
                         str(cont.get("tb", "")).lower())[:24],
            "u": str(cont.get("u", ""))[:2000],
            "elapsed": int(cont.get("elapsed", 0) or 0),
            "total": int(cont.get("total", 0) or 0),
        }
    d[code] = entry
    _kbstatus_save(d)
    return jsonify({"ok": True})


@app.get("/kb/status")
def kb_status_get():
    """El movil lee el estado del box (conectado, version, continuar)."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"connected": False})
    _kb_phone_seen(code)
    entry = _kbstatus_load().get(code)
    if not entry:
        return jsonify({"connected": False})
    age = _t.time() - entry.get("ts", 0)
    return jsonify({"connected": age < 90, "age": int(age),
                    "v": entry.get("v", ""), "cont": entry.get("cont"),
                    "diag": entry.get("diag")})


@app.get("/kb/qr")
def kb_qr():
    import io
    import segno
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    host = request.host_url.rstrip("/")
    url = f"{host}/kb?c={code}" if code else f"{host}/kb"
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=2,
                                    dark="0d1117", light="ffffff")
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


# ===========================================================================
# CATALOGO (web EN PRUEBAS, aparte): buscar en el movil y enviar a la tele
# ===========================================================================
# Totalmente aislado de /kb y /dtsearch: si esto falla, el mando y el box
# siguen igual. MVP (fase 1): DonTorrent PELICULAS -> JSON con poster TMDB +
# referencia (content_id, tabla). El boton "Reproducir en la tele" REUSA
# /kb/send (play_ref a=dt), que ya esta probado. Sin coste (TMDB free).
_CAT_TMDB_KEY = "f090bb54758cabf231fb605d3e3e0468"   # misma key publica del addon
# Sesion HTTP COMPARTIDA para TMDB (keep-alive): el enrich hace decenas de
# llamadas seguidas y sin pool cada una pagaba su handshake TLS entero. El pool
# de urllib3 es thread-safe -> vale para los 8 hilos del enrich.
_TMDB_SESS = requests.Session()
try:
    from requests.adapters import HTTPAdapter as _HTTPAdapter
    _TMDB_SESS.mount("https://", _HTTPAdapter(pool_connections=4,
                                              pool_maxsize=16))
except Exception:
    pass
_CAT_TMDB_CACHE = {}       # (kind, titulo, año) -> {"m": meta, "ts": ...}
_CAT_TMDB_MAX = 3000       # tope de titulos en memoria (Render 512MB)
_CAT_TMDB_NULL_TTL = 600   # los SIN poster caducan a los 10 min: antes un titulo
                           # sin match se cacheaba PARA SIEMPRE y nunca se
                           # auto-sanaba aunque TMDB volviera a responder bien

# Mapa de generos TMDB (movie + tv) -> nombre es-ES. La busqueda TMDB devuelve
# `genre_ids` (no nombres); los traducimos aqui SIN llamada extra (lista fija y
# estable de TMDB). Ids compartidos entre movie/tv (16,35,80,99,18,10751,9648,37)
# significan lo mismo; los tv-only (107xx) se añaden aparte.
_TMDB_GENRES = {
    28: "Acción", 12: "Aventura", 16: "Animación", 35: "Comedia",
    80: "Crimen", 99: "Documental", 18: "Drama", 10751: "Familia",
    14: "Fantasía", 36: "Historia", 27: "Terror", 10402: "Música",
    9648: "Misterio", 10749: "Romance", 878: "Ciencia ficción",
    10770: "Película de TV", 53: "Suspense", 10752: "Bélica", 37: "Western",
    10759: "Acción y aventura", 10762: "Infantil", 10763: "Noticias",
    10764: "Reality", 10765: "Ciencia ficción", 10766: "Telenovela",
    10767: "Entrevistas", 10768: "Guerra y política",
}


def _cat_clean_title(title):
    t = (title or "").split(" - ")[0]   # "Show - 1ª Temporada [720p]" -> "Show"
    t = _re_dt.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = _re_dt.sub(r"\b\d{1,2}\s*[ªºoa]\b", " ", t)   # ordinales sueltos (1ª, 2º)
    # numero de temporada PEGADO al titulo sin ordinal: DonTorrent escribe
    # "Dulces magnolias 5 Temporada" -> sin esto la query TMDB llevaba el "5" y
    # no encontraba la serie (la mayoria de series del Inicio se quedaban sin
    # poster/nota). Quita el numero (y ordinal opcional) que precede a "temporada".
    t = _re_dt.sub(r"\b\d{1,2}\s*[ªºoa]?\s*(?=temporada\b)", " ", t, flags=_re_dt.I)
    t = _re_dt.sub(r"\b(temporada|parte|cap\w*|capitulo|\d{1,2}\s*x\s*\d{1,3})\b.*",
                   "", t, flags=_re_dt.I)
    t = _re_dt.sub(r"\b(1080p|720p|480p|2160p|4k|bluray|blu-?ray|brrip|bdrip|"
                   r"web-?dl|webrip|hdtv|microhd|dvdrip|hdrip|x264|x265|hevc|"
                   r"dual|castellano|latino|vose?)\b.*", "", t, flags=_re_dt.I)
    return t


# Breaker TMDB: TMDB tambien banea la IP de Render tras muchas llamadas (el enrich
# hace 18-36 por pantalla). Si una llamada falla, dejamos de tocar TMDB un rato:
# el enrich devuelve SIN poster AL INSTANTE (no cuelga, no abre conexiones que se
# acumulen). Asi el relay no se satura ni se queda sin memoria. Reintenta a los 2min.
_TMDB_DOWN_UNTIL = [0.0]
_TMDB_DOWN_COOLDOWN = 120


def _tmdb_is_down():
    return _t.time() < _TMDB_DOWN_UNTIL[0]


def _tmdb_mark(ok):
    _TMDB_DOWN_UNTIL[0] = 0.0 if ok else (_t.time() + _TMDB_DOWN_COOLDOWN)


def _tmdb_pick(results, clean, year, ep):
    """Elige el MEJOR resultado de la MISMA respuesta de TMDB (sin llamadas extra
    -> cero riesgo de baneo). Antes se cogia res[0] a ciegas: TMDB prioriza el
    titulo EXACTO sobre el popular, asi que 'Profanacion' devolvia una peli oscura
    de 1934 (titulo exacto) en vez de la de Departamento Q (2014, mucho mas
    popular pero titulada 'Los casos del Departamento Q: Profanacion').
    Ponderamos: popularidad * boost por coincidencia de titulo, +/- cercania de
    año cuando se conoce. Asi gana el correcto sin gastar ni una peticion mas."""
    nq = _et_norm(clean or "")

    def _names(it):
        vals = (it.get("title"), it.get("name"),
                it.get("original_title"), it.get("original_name"))
        return [_et_norm(v) for v in vals if v]

    def _year_of(it):
        d = it.get("release_date") or it.get("first_air_date") or ""
        return d[:4]

    def _score(it):
        names = _names(it)
        if nq and nq in names:
            w = 2.5                      # titulo EXACTO
        elif nq and any((nq in n) or (n in nq) for n in names if n):
            w = 1.3                      # uno contiene al otro
        else:
            w = 1.0
        s = (float(it.get("popularity") or 0.0) + 0.5) * w
        yr = _year_of(it)
        if year and yr:
            try:
                d = abs(int(yr) - int(year))
                if d <= 1:
                    s *= 1.6             # año casi exacto -> casi seguro es esta
                elif d >= 6:
                    s *= 0.6             # muy lejos del año -> probablemente NO
            except Exception:
                pass
        # Ponderar por vote_count: entre titulos HOMONIMOS gana el ESTABLECIDO, no
        # una NOVEDAD con mucho hype y pocos votos. Caso real: "Toy Story" (sin año
        # en DonTorrent) cogia "Toy Story 5" (2026, popularity 351 pero solo 252
        # votos) en vez de la original (1995, 19998 votos). DonTorrent tiene pelis
        # ya asentadas -> el conteo de votos es mejor señal que la popularity volatil.
        vc = float(it.get("vote_count") or 0)
        f = max(0.2, min(1.0, vc / 800.0))
        # EXCEPCION: ESTRENO con titulo EXACTO. Pocos votos es lo NORMAL en una
        # peli recien salida -> no aplastarla. Caso real: 'La residencia' (2025,
        # 85 votos) perdia contra 'Asterix y la residencia de los dioses' (2014,
        # 1480 votos, match por substring) y la ficha entera salia de Asterix.
        # Solo EXACTO+reciente: 'Toy Story 5' (no exacto) sigue sin robarle el
        # sitio a la original, y 'Profanacion' (exacta pero de 1934, oscura)
        # sigue perdiendo contra la del Departamento Q.
        if w >= 2.5 and yr:
            try:
                if int(yr) >= _t.localtime().tm_year - 2:
                    f = max(f, 0.85)
            except Exception:
                pass
        s *= f
        return s

    try:
        return max(results, key=_score)
    except Exception:
        return results[0]


def _tmdb_accept(clean, pick):
    """True si el match TMDB es de CONFIANZA. Evita pegar una caratula AJENA cuando
    el titulo no existe en TMDB (packs/recopilatorios/documentales raros): sin esto
    `_tmdb_pick` siempre devolvia el mas popular -> 'caratulas que no tienen nada que
    ver'. Compara con TODOS los nombres del candidato (es-ES y ORIGINAL) para NO
    rechazar traducciones (p.ej. 'Adam Resurrected' = 'Adam Resucitado')."""
    nq = _et_norm(clean or "")
    names = [_et_norm(n) for n in (pick.get("title"), pick.get("name"),
             pick.get("original_title"), pick.get("original_name")) if n]
    if not nq or not names:
        return False
    for npk in names:                       # coincidencia plena o de substring
        if npk and (nq == npk or nq in npk or npk in nq):
            return True
    qtok = [w for w in nq.split() if len(w) > 2]
    if not qtok:
        return False
    for npk in names:                       # o cubre la 1a palabra + >=70% tokens
        ptok = set(npk.split())
        hit = sum(1 for w in qtok if w in ptok)
        if qtok[0] in ptok and hit / len(qtok) >= 0.7:
            return True
    return False


_TMDB_QUAL_RE = _re_dt.compile(
    r"\b(1080p|720p|480p|2160p|4k|uhd|hdr|bluray|blu-?ray|brrip|bdrip|web-?dl|"
    r"webrip|hdtv|microhd|dvdrip|hdrip|x264|x265|hevc|remux|3d|imax|dual|castellano|"
    r"latino|vose?|subtitulad|extendid|edicion|edición|director|sin censura|ac3|dts)\b",
    _re_dt.I)


def _saga_context(title):
    """Texto entre paréntesis que es CONTEXTO de saga (p.ej. 'Los casos del
    Departamento Q'), NO calidad/formato/año. Sirve para DESAMBIGUAR títulos
    comunes: 'Redención (Los casos del Departamento Q)' a secas matchea 'Redención'
    (Southpaw, ajena); con el contexto en la query TMDB devuelve la película
    correcta. '' si el paréntesis es basura (calidad/año) o no hay."""
    for c in _re_dt.findall(r"[\(\[]([^\)\]]+)[\)\]]", title or ""):
        c = c.strip()
        if _TMDB_QUAL_RE.search(c):
            continue
        if _re_dt.fullmatch(r"[\d\W]+", c):   # solo numeros/simbolos (año) -> no
            continue
        words = [w for w in _re_dt.findall(r"[^\W\d_]+", c, _re_dt.U) if len(w) > 1]
        if len(words) >= 2:                   # >=2 palabras significativas -> saga
            return c
    return ""


def _cat_tmdb(title, kind="movie"):
    """Poster/año/nota de TMDB. kind='movie'|'tv'. Cache en memoria + breaker."""
    clean = _cat_clean_title(title)
    ym = _re_dt.search(r"\b(19|20)\d{2}\b", title)
    year = ym.group(0) if ym else None
    clean = _re_dt.sub(r"\b(19|20)\d{2}\b", "", clean)
    clean = _re_dt.sub(r"\s+", " ", clean).strip(" -.:")
    ckey = (kind, clean.lower(), year or "")
    ent = _CAT_TMDB_CACHE.get(ckey)
    if ent:
        hit = ent["m"]
        # acierto (con poster) vale para siempre; fallo caduca (_NULL_TTL)
        if hit.get("poster") or (_t.time() - ent["ts"]) < _CAT_TMDB_NULL_TTL:
            return hit
    out = {"poster": None, "year": year, "rating": None}
    if _tmdb_is_down():
        return out   # TMDB baneado -> sin poster al instante (no toca la red)
    try:
        ep = "tv" if kind == "tv" else "movie"
        ctx = _saga_context(title)
        # Query con CONTEXTO de saga PRIMERO (desambigua títulos comunes tipo
        # "Redención"); si no da match de confianza, query limpia (base). Solo añade
        # una 2a llamada cuando hay paréntesis de saga Y la 1a no acierta -> coste
        # mínimo (la mayoría de items no tienen saga -> 1 sola query, como antes).
        tries = ([f"{clean} {ctx}"] if ctx else []) + [clean]
        for qi, q in enumerate(tries):
            params = {"api_key": _CAT_TMDB_KEY, "language": "es-ES",
                      "query": q, "include_adult": "false"}
            if year and ep == "movie" and qi == len(tries) - 1:
                params["year"] = year   # filtro de año solo en la query limpia
            r = _TMDB_SESS.get(f"https://api.themoviedb.org/3/search/{ep}",
                               params=params, timeout=(3, 4))
            if r.status_code != 200:
                # 429/403: TMDB rate-limita la IP de Render. NO lanza excepcion, asi
                # que sin esto el breaker no saltaba y el enrich se atascaba (el
                # Inicio con 36 items se colgaba). Marcamos caido -> el resto del
                # enrich va SIN poster AL INSTANTE; reintenta a los 2 min.
                _tmdb_mark(False)
                return out
            res = (r.json() or {}).get("results") or []
            if res:
                top = _tmdb_pick(res, clean, year, ep)
                if _tmdb_accept(clean, top):
                    pp = top.get("poster_path")
                    bd = top.get("backdrop_path")
                    d = top.get("release_date") or top.get("first_air_date") or ""
                    gids = top.get("genre_ids") or []
                    # overview/backdrop/generos/id vienen GRATIS en esta MISMA
                    # respuesta de busqueda -> 0 llamadas extra, 0 riesgo de baneo.
                    out = {"poster": (f"https://image.tmdb.org/t/p/w342{pp}" if pp else None),
                           "year": d[:4] or year, "rating": top.get("vote_average"),
                           # El titulo viene GRATIS aqui y sirve para restaurar
                           # las tildes que DonTorrent no publica (ver _dt_mutila).
                           "title": (top.get("title") or top.get("name") or ""),
                           "overview": (top.get("overview") or "").strip(),
                           "backdrop": (f"https://image.tmdb.org/t/p/w780{bd}" if bd else None),
                           "genres": [_TMDB_GENRES[g] for g in gids
                                      if g in _TMDB_GENRES][:3],
                           "tmdb_id": top.get("id")}
                    break   # match de confianza -> no probar más queries
                # match NO de confianza -> probar la siguiente query; si no quedan,
                # out se queda SIN poster TMDB (el enrich usa la carátula propia de
                # DonTorrent o ninguna, nunca una ajena). Ver _tmdb_accept.
        _tmdb_mark(True)
        _CAT_TMDB_CACHE[ckey] = {"m": out, "ts": _t.time()}
        if len(_CAT_TMDB_CACHE) > _CAT_TMDB_MAX:   # poda las mas viejas
            for k in sorted(_CAT_TMDB_CACHE,
                            key=lambda k: _CAT_TMDB_CACHE[k]["ts"])[:200]:
                _CAT_TMDB_CACHE.pop(k, None)
    except Exception:
        _tmdb_mark(False)   # no cachear -> reintenta cuando TMDB se recupere
    return out


# Circuit breaker DonTorrent: si DonTorrent no responde (rate-limit a la IP de
# Render), se SALTA al instante durante un rato en vez de colgar cada peticion.
# Asi la web responde YA (con las fuentes-box) y reintenta DonTorrent cada 90s.
# COMPARTIDO entre workers via /tmp: en cuanto UN worker detecta que DonTorrent
# esta caido, TODOS lo saltan al instante (antes era por-worker -> cada worker
# frio se colgaba ~40s la primera vez). Asi solo 1 peticion paga el sondeo.
_DT_DOWN_UNTIL = [0.0]
_DT_DOWN_COOLDOWN = 90
_DT_SLOW = 12          # si una operacion DonTorrent tarda mas -> baja el breaker
_DT_DOWN_FILE = "/tmp/mw_dt_down"

# Tope de operaciones DonTorrent SIMULTANEAS. Aunque DonTorrent se cuelgue (PoW
# Anubis dificultad 5 + rate-limit pueden tardar >45s antes de que el breaker
# salte), como mucho 2 hilos quedan ocupados con DonTorrent -> SIEMPRE quedan
# hilos libres para servir la pagina, /ping y las fuentes-box. El relay NO se
# satura ni se cae. La 3a peticion concurrente devuelve al instante (cache/vacio).
_DT_SEM = _thr.Semaphore(2)   # _thr ya viene importado arriba del modulo


def _dt_is_down():
    if _t.time() < _DT_DOWN_UNTIL[0]:
        return True
    try:
        with open(_DT_DOWN_FILE) as f:
            until = float(f.read().strip() or 0)
        if _t.time() < until:
            _DT_DOWN_UNTIL[0] = until   # sincroniza memoria local
            return True
    except Exception:
        pass
    return False


def _dt_mark(ok):
    if ok:
        _DT_DOWN_UNTIL[0] = 0.0
        try:
            os.remove(_DT_DOWN_FILE)
        except Exception:
            pass
    else:
        until = _t.time() + _DT_DOWN_COOLDOWN
        _DT_DOWN_UNTIL[0] = until
        try:
            with open(_DT_DOWN_FILE, "w") as f:
                f.write(str(until))
        except Exception:
            pass


def _cat_dt_html(q):
    """Breaker + tope de concurrencia (max 2 ops DonTorrent), luego delega."""
    if _dt_is_down() or not _DT_SEM.acquire(blocking=False):
        return ""
    try:
        return _cat_dt_html_inner(q)
    finally:
        _DT_SEM.release()


def _cat_dt_html_inner(q):
    """POST de busqueda a DonTorrent (dominio APRENDIDO) -> HTML de TODAS las
    paginas concatenado. Reusa Anubis."""
    t0 = _t.time()
    from urllib.parse import urlparse as _up
    data = {"valor": q, "Buscar": "Buscar"}
    got = False
    for dom in [d for d in dict.fromkeys([_dt_load_domain()] + DT_FALLBACK)
                if d][:1]:
        try:
            s, _ = _dt_anubis_session(dom)

            def _post(page, _s=s, _dom=dom):
                dd = dict(data)
                if page > 1:
                    dd["p"] = str(page)
                rr = _s.post(f"https://{_dom}/buscar", data=dd, timeout=6,
                             allow_redirects=False)
                if "anubis_challenge" in rr.text:
                    ns, _ = _dt_anubis_session(_dom, force=True)
                    rr = ns.post(f"https://{_dom}/buscar", data=dd, timeout=6,
                                 allow_redirects=False)
                return rr

            r1 = _post(1)
            got = True   # DonTorrent respondio (aunque no haya match)
            if r1.status_code in (301, 302, 303, 307, 308):
                nd = _up(r1.headers.get("Location") or "").hostname
                if nd and nd != dom:
                    continue
            if not _re_dt.search(r"/(?:pelicula|serie|documental)/\d+/", r1.text):
                continue
            full = r1.text
            pgs = [int(n) for n in _re_dt.findall(r"buscarPagina\((\d+)\)", full)]
            mx = min(max(pgs) if pgs else 1, 12)
            if mx > 1:
                from concurrent.futures import ThreadPoolExecutor as _TPE

                def _pg(p):
                    try:
                        return _post(p).text
                    except Exception:
                        return ""
                with _TPE(max_workers=min(8, mx - 1)) as ex:
                    full += "".join(ex.map(_pg, range(2, mx + 1)))
            _dt_mark(True)   # DT RESPONDIO con resultados -> breaker ARRIBA aunque
            # haya sido lento (el PoW de Anubis en frio tarda 1 vez; _DT_SEM(2) ya
            # evita saturar). Marcarlo "caido" por lento causaba el bache de ~90s
            # sin resultados tras arrancar en frio.
            return full
        except Exception:
            continue
    _dt_mark(got)   # "caido" SOLO si DT no respondio (no por lento)
    return ""


def _cat_dt_session_get(path):
    """GET a una ruta de DonTorrent (dominio APRENDIDO) con sesion Anubis.
    Devuelve (html, domain) o ('', None). Breaker + tope de concurrencia: si
    DonTorrent esta caido o ya hay 2 ops en curso, devuelve al instante."""
    if _dt_is_down() or not _DT_SEM.acquire(blocking=False):
        return "", None
    try:
        t0 = _t.time()
        got = False
        for dom in [d for d in dict.fromkeys([_dt_load_domain()] + DT_FALLBACK)
                    if d][:1]:
            try:
                s, _ = _dt_anubis_session(dom)
                rr = s.get(f"https://{dom}{path}", timeout=6,
                           allow_redirects=True)
                got = True
                if "anubis_challenge" in rr.text:
                    s, _ = _dt_anubis_session(dom, force=True)
                    rr = s.get(f"https://{dom}{path}", timeout=6)
                if rr.status_code == 200 and _re_dt.search(
                        r"/(?:pelicula|serie|documental)/\d+/", rr.text):
                    # DT respondio con resultados -> breaker ARRIBA aunque lento
                    # (el PoW en frio tarda 1 vez; _DT_SEM(2) ya evita saturar).
                    _dt_mark(True)
                    return rr.text, dom
            except Exception:
                continue
        _dt_mark(got)   # "caido" SOLO si DT no respondio (no por lento)
        return "", None
    finally:
        _DT_SEM.release()


_CAT_QRE = _re_dt.compile(
    r"\b(4K|2160p|1080p|720p|HDRip|BluRay|BDRemux|BDRip|WEB-?DL|WEBRip|"
    r"MicroHD|HDTV|DVDRip|Remux|UHD)\b", _re_dt.I)

_CAT_KIND_MAP = {"pelicula": "movie", "serie": "serie", "documental": "doc"}
_CAT_KIND_TABLA = {"movie": "peliculas", "doc": "documentales"}


def _cat_norm_quality(qraw):
    """Normaliza un token de calidad para mostrar (4K / 1080p / 720p / BluRay...)."""
    t = (qraw or "").strip().lower()
    if t in ("4k", "2160p", "uhd"):
        return "4K"
    if t == "1080p":
        return "1080p"
    if t == "720p":
        return "720p"
    return (qraw or "").strip().upper() if t in (
        "hdrip", "hdtv", "dvdrip") else (qraw or "").strip()


def _cat_clean_quality(title):
    """DonTorrent mete la calidad en el titulo (p.ej. 'Matrix [4K]'). Devuelve
    (titulo_limpio_para_mostrar, calidad_normalizada)."""
    m = _CAT_QRE.search(title or "")
    q = _cat_norm_quality(m.group(1)) if m else ""
    clean = _re_dt.sub(r"\[[^\]]*\]", "", title or "")   # quita [4K], [1080p AC3]
    clean = _CAT_QRE.sub("", clean)
    clean = _re_dt.sub(r"\(\s*[-–—]\s*", "(", clean)     # "( - IMAX" -> "(IMAX"
    clean = _re_dt.sub(r"\(\s*\)|\[\s*\]", "", clean)    # parentesis/corchetes vacios
    clean = _re_dt.sub(r"\s{2,}", " ", clean).strip(" .-·")
    return (clean or (title or "").strip()), q


def _cat_parse_items(html):
    """Peliculas y series (con su path de ficha) del HTML de un listado/busqueda.
    Por cada content_id se queda con el MEJOR titulo: atributo title (limpio) >
    texto del enlace > alt de la imagen > slug. Asi en los listados (donde el 1er
    enlace es la imagen sin titulo) no salen titulos basura tipo '127509/Tro...'."""
    best, order = {}, []
    for m in _re_dt.finditer(r"<a\b([^>]*)>(.*?)</a>", html, _re_dt.S | _re_dt.I):
        attrs, inner = m.group(1), m.group(2)
        hm = _re_dt.search(
            r'''href=["']/(pelicula|serie|documental)/(\d+)((?:/[^"'#?]*)?)["']''',
            attrs, _re_dt.I)
        if not hm:
            continue
        kind = _CAT_KIND_MAP.get(hm.group(1).lower(), "movie")
        cid, rest = hm.group(2), (hm.group(3) or "")
        tm = _re_dt.search(r'''title=["']([^"']+)["']''', attrs)
        # DonTorrent RESALTA el termino buscado DENTRO del titulo con un <span>:
        # "Desapa<span ...>rec</span>idas". Hay que quitar esas etiquetas INLINE
        # SIN espacio (si no, "Desaparecidas" -> "Desapa rec idas" -> rompe el match
        # TMDB y el titulo visible). Las demas etiquetas si separan con espacio.
        inner_hl = _re_dt.sub(r"</?(?:span|b|strong|em|mark|i|u|font)\b[^>]*>",
                              "", inner)
        itxt = _re_dt.sub(r"\s+", " ", _re_dt.sub(r"<[^>]+>", " ", inner_hl)).strip()
        am = _re_dt.search(r'''alt=["']([^"']+)["']''', attrs + " " + inner)
        if tm and tm.group(1).strip():
            title, score = tm.group(1).strip(), 3
        elif itxt:
            title, score = itxt, 2
        elif am and am.group(1).strip():
            title, score = am.group(1).strip(), 1
        else:
            title, score = rest.rstrip("/").split("/")[-1].replace("-", " "), 0
        title = _re_dt.sub(r"\s+", " ", title).strip()
        if not title:
            continue
        # Caratula PROPIA de DonTorrent (img dentro del enlace, via weserv CDN):
        # respaldo cuando TMDB no encuentra el titulo -> toda tarjeta tiene foto.
        thumb = None
        im = _re_dt.search(r'''<img[^>]+(?:data-src|src)=["']([^"']+)["']''',
                           inner, _re_dt.I)
        if im:
            thumb = im.group(1)
            if thumb.startswith("//"):
                thumb = "https:" + thumb
            thumb = _re_dt.sub(r"w=\d+&h=\d+", "w=342&h=513", thumb)
        key = (kind, cid)
        path = (f"/serie/{cid}{rest}" if (kind == "serie" and rest) else None)
        # Ruta de la FICHA (con slug) para TODOS los tipos -> hace falta para leer el
        # AÑO real de la peli (DT exige el slug; por cid pelado da 404). En pelis/docs
        # va aparte (fpath -> dtpath) para no tocar 'path' (que en el front abre series).
        _seg = {"movie": "pelicula", "doc": "documental"}.get(kind, "serie")
        fpath = f"/{_seg}/{cid}{rest}" if rest else None
        e = best.get(key)
        if e is None:
            order.append(key)
            best[key] = {"title": title, "score": score, "path": path,
                         "fpath": fpath, "thumb": thumb}
        else:
            if score > e["score"]:
                e["title"], e["score"] = title, score
            if path and not e["path"]:
                e["path"] = path
            if fpath and not e.get("fpath"):
                e["fpath"] = fpath
            if thumb and not e["thumb"]:
                e["thumb"] = thumb
    out = []
    for k in order:
        kind, cid = k
        e = best[k]
        disp, qual = _cat_clean_quality(e["title"])
        if not qual and e["thumb"]:
            # DonTorrent casi nunca pone la calidad en el TITULO del listado, pero
            # el nombre del fichero de su CARATULA si la lleva ([DVDRip], [HDTV]...)
            # -> la recuperamos de ahi (cobertura ~96% vs casi 0 solo por titulo).
            mq = _CAT_QRE.search(e["thumb"])
            if mq:
                qual = _cat_norm_quality(mq.group(1))
        it = {"title": disp, "content_id": cid, "kind": kind,
              "thumb": e["thumb"], "quality": qual, "source": "dt"}
        if kind == "serie":
            it["path"] = e["path"] or f"/serie/{cid}/"
        else:   # movie / doc -> descarga directa con su tabla
            it["tabla"] = _CAT_KIND_TABLA.get(kind, "peliculas")
            if e.get("fpath"):
                it["dtpath"] = e["fpath"]   # ficha con slug -> año real (desambigua homonimos)
        out.append(it)
    return out


def _dt_mutila(s):
    """Aplica a un titulo la MISMA mutilacion que hace DonTorrent en sus slugs:
    BORRAR la letra acentuada entera ('Obsesión' -> 'Obsesin').

    Por que existe (verificado 2026-08-07 sobre el HTML real del listado): en
    /peliculas cada ficha es UN solo <a> con la imagen dentro, SIN atributo
    `title` y SIN `alt` — el unico texto es el slug de la URL
    (`/pelicula/30780/La-ambicin-de-los-Savage`), y DonTorrent lo genera ya sin
    la vocal. Los unicos caracteres acentuados del HTML estan en los metadatos
    de la pagina ("Películas", "Español"), NO en los titulos. Es decir: la web
    original NO publica el titulo completo, asi que el catalogo salia con
    'Obsesin', 'Admisin imposible' o 'Cmplices hasta el final'.

    Se usa para comparar: si el titulo de TMDB, mutilado igual, coincide con el
    nuestro, es el MISMO titulo y podemos poner el bueno."""
    out = []
    for ch in s or "":
        d = _wud.normalize("NFD", ch)
        if len(d) > 1 and _wud.combining(d[-1]):
            continue
        out.append(ch)
    return "".join(out)


def _cat_enrich(items, limit=120):
    # NO descartamos resultados: la busqueda debe volcar TODO lo que da la web
    # original (DonTorrent puede traer 49+ en "batman"). Enriquecemos con TMDB
    # (poster/nota) hasta `limit` en paralelo; el resto (rarisimo) se devuelve
    # tal cual -> aparece igualmente (con su miniatura propia o sin poster).
    head, tail = items[:limit], items[limit:]
    from concurrent.futures import ThreadPoolExecutor as _TPE
    seed_idx = _seed_meta_index()   # {content_id: {poster TMDB, year, rating}}
    # Cache que llena el BOX resolviendo la FICHA de DonTorrent. Lo marcado con
    # `dtok` es mejor que cualquier cosa que saquemos aqui (titulo de la fuente y
    # homonimos ya desempatados por director), asi que se aplica TAL CUAL y ese
    # item ni siquiera pasa por TMDB -> ademas de correcto, sale gratis y mas
    # rapido. Vale para el Inicio y para la BUSQUEDA (mismo content_id).
    enr_idx = _cat_enrich_load()

    def _go(it):
        # Solo items de DonTorrent: el content_id es SU identificador y no tiene
        # por que significar nada en dx/et/wf.
        _dtm = (enr_idx.get(str(it.get("content_id"))) or {}
                if it.get("source") == "dt" else {})
        if _dtm.get("dtok") and _cat_apply_meta(it, _dtm):
            if not it.get("poster"):
                it["poster"] = it.get("thumb")
            return it
        meta = _cat_tmdb(it["title"],
                         "tv" if it.get("kind") == "serie" else "movie")
        poster, year, rating = meta.get("poster"), meta.get("year"), meta.get("rating")
        if (not poster) or (rating is None):
            # TMDB no respondio (banea la IP de Render). En vez de DEGRADAR a la
            # caratula no-HD y perder nota/año, reusamos lo PRE-ENRIQUECIDO de la
            # semilla por content_id (poster HD + nota + año persisten siempre).
            sm = seed_idx.get(it.get("content_id"))
            if sm:
                poster = poster or sm.get("poster")
                year = year or sm.get("year")
                rating = rating if rating is not None else sm.get("rating")
        # Restaurar las TILDES que DonTorrent no publica (ver _dt_mutila): solo
        # si el titulo de TMDB, mutilado igual que el slug, coincide EXACTAMENTE
        # con el nuestro. Asi 'Obsesin' -> 'Obsesión' y 'Cmplices hasta el final'
        # -> 'Cómplices hasta el final', pero un titulo DISTINTO (TMDB en ingles,
        # otra peli) NUNCA sustituye al de la web original (norma §0: espejo).
        _tt = (meta.get("title") or "").strip()
        _cur = (it.get("title") or "").strip()
        if _tt and _cur and _tt != _cur and _dt_mutila(_tt).lower() == _cur.lower():
            it["title"] = _tt
        it["poster"] = poster or it.get("thumb")   # TMDB/semilla > DT propia
        it["year"] = year or it.get("year")
        it["rating"] = rating
        # Ficha enriquecida (todo GRATIS de la misma respuesta TMDB). Si TMDB
        # estaba caido, quedan vacios -> la ficha degrada con elegancia.
        if meta.get("overview"):
            it["overview"] = meta["overview"]
        if meta.get("backdrop"):
            it["backdrop"] = meta["backdrop"]
        if meta.get("genres"):
            it["genres"] = meta["genres"]
        if meta.get("tmdb_id"):
            it["tmdb_id"] = meta["tmdb_id"]
        return it
    try:
        with _TPE(max_workers=8) as ex:
            head = list(ex.map(_go, head))
    except Exception:
        pass
    return head + tail


# === EliteTorrent (catalogo: 2a fuente, peliculas) =========================
_ET_BASE = "https://www.elitetorrent.com"
# Render (IP de datacenter) recibe el reto Cloudflare "Just a moment" de ET y
# cloudscraper NO lo resuelve (challenge nuevo tipo Turnstile). El box, con IP
# residencial de casa, sí puede. Hasta enrutar ET por el box (o un proxy
# residencial), se deja APAGADO por defecto -> la busqueda combinada usa solo
# DonTorrent. Poner ET_ENABLED=1 en el entorno para reactivarlo.
_ET_ENABLED = os.environ.get("ET_ENABLED", "").strip() == "1"


def _et_session():
    try:
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows",
                     "mobile": False})
    except Exception:
        s = requests.Session()
        s.headers.update(BROWSER_HEADERS)
        return s


def _et_rot13(t):
    out = []
    for c in t:
        if "a" <= c <= "z":
            out.append(chr((ord(c) - 97 + 13) % 26 + 97))
        elif "A" <= c <= "Z":
            out.append(chr((ord(c) - 65 + 13) % 26 + 65))
        else:
            out.append(c)
    return "".join(out)


def _et_decode_link(enc):
    """Enlace ET = base64 anidado (+ROT13) -> magnet / .torrent."""
    import base64
    data = (enc or "").strip()
    for _ in range(20):
        try:
            pad = len(data) % 4
            dd = data + ("=" * (4 - pad) if pad else "")
            dec = base64.b64decode(dd).decode("utf-8", "replace").strip()
            if dec.startswith(("magnet:", "zntarg:", "http", "uggc")):
                data = dec
                break
            data = dec
        except Exception:
            break
    if data.startswith("zntarg:") or data.startswith("uggc"):
        data = _et_rot13(data)
    return data if data.startswith(("magnet:", "http")) else None


def _et_norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _re_dt.sub(r"\s+", " ", _re_dt.sub(r"[^a-z0-9 ]", " ", s)).strip()


def _et_relevant(title, q):
    nt, nq = _et_norm(title), _et_norm(q)
    if nq and nq in nt:
        return True
    toks = [w for w in nq.split() if len(w) > 2]
    if not toks:
        return bool(nq) and nq in nt
    hit = sum(1 for w in toks if w in nt)
    return hit >= max(1, len(toks) - 1)


def _q_relevant(title, q):
    """Relevancia ESTRICTA para la busqueda combinada: el titulo debe contener
    TODAS las palabras significativas (>2 letras) de la consulta. Asi 'desde mi
    cielo' no trae 'El mismo cielo' (solo comparte 'cielo')."""
    nt, nq = _et_norm(title), _et_norm(q)
    if not nq:
        return True
    if nq in nt:
        return True
    toks = [w for w in nq.split() if len(w) > 2]
    if not toks:
        return nq in nt
    return all(w in nt for w in toks)


def _et_search(q):
    """Peliculas de EliteTorrent para la busqueda combinada. Best-effort: si ET
    cae o bloquea la IP de Render, devuelve [] (DonTorrent sigue mandando)."""
    if not _ET_ENABLED:
        return []
    try:
        s = _et_session()
        r = s.get(_ET_BASE + "/?s=" + urlquote(q), timeout=16)
        html = r.text or ""
        if "miniboxs" not in html and "class=\"nombre" not in html:
            return []
        out, seen = [], set()
        for lim in _re_dt.finditer(r"(?is)<li\b[^>]*>(.*?)</li>", html):
            li = lim.group(1)
            am = _re_dt.search(
                r'''<a[^>]*class=["'][^"']*\bnombre\b[^"']*["'][^>]*'''
                r'''href=["']([^"']+)["'][^>]*>(.*?)</a>''', li,
                _re_dt.I | _re_dt.S)
            if not am:
                am = _re_dt.search(
                    r'''<a[^>]*href=["']([^"']+)["'][^>]*'''
                    r'''title=["']([^"']+)["']''', li, _re_dt.I)
            if not am:
                continue
            href = am.group(1)
            tlm = _re_dt.search(r'''title=["']([^"']+)["']''', li)
            title = (tlm.group(1) if tlm
                     else _re_dt.sub(r"<[^>]+>", " ", am.group(2)))
            title = _re_dt.sub(r"\s+", " ", title or "").strip()
            if not title or not href:
                continue
            url = href if href.startswith("http") else (
                _ET_BASE + ("" if href.startswith("/") else "/") + href)
            if "/serie" in url or "/series" in url:
                continue   # de momento ET solo aporta peliculas
            if url in seen or not _et_relevant(title, q):
                continue
            seen.add(url)
            im = _re_dt.search(
                r'''<img[^>]+(?:data-src|src)=["']([^"']+)["']''', li, _re_dt.I)
            thumb = im.group(1) if im else None
            if thumb and thumb.startswith("//"):
                thumb = "https:" + thumb
            qm = _re_dt.search(
                r'''class=["'][^"']*marca[^"']*["'][^>]*>\s*<i[^>]*>([^<]+)''',
                li, _re_dt.I)
            tdisp, tq = _cat_clean_quality(title)
            qual = _cat_norm_quality(qm.group(1).strip()) if qm else tq
            out.append({"title": tdisp, "content_id": url, "kind": "movie",
                        "source": "et", "url": url, "thumb": thumb,
                        "quality": qual, "tabla": "et"})
        return out[:24]
    except Exception:
        return []


def _et_resolve(url):
    """Ficha ET -> mejor enlace (magnet preferido, si no .torrent). '' si nada."""
    try:
        s = _et_session()
        html = s.get(url, timeout=16).text or ""
        cands = []
        for hm in _re_dt.finditer(
                r'''<a[^>]*class=["'][^"']*enlace_torrent[^"']*["']'''
                r'''[^>]*href=["']([^"']+)["']''', html, _re_dt.I):
            mm = _re_dt.search(r"[?&]i=([A-Za-z0-9+/=]+)", hm.group(1))
            if not mm:
                continue
            link = _et_decode_link(mm.group(1))
            if link:
                cands.append(link)
        if not cands:   # VIP: a.linktorrent[data-src] (base64 simple)
            import base64
            for dm in _re_dt.finditer(
                    r'''<a[^>]*class=["'][^"']*linktorrent[^"']*["']'''
                    r'''[^>]*data-src=["']([^"']+)["']''', html, _re_dt.I):
                try:
                    dec = base64.b64decode(dm.group(1)).decode("utf-8")
                    if dec.startswith("magnet:"):
                        cands.append(dec)
                except Exception:
                    pass
        for c in cands:
            if c.startswith("magnet:"):
                return c
        return cands[0] if cands else ""
    except Exception:
        return ""


def _cat_merge(dt_items, et_items):
    """DonTorrent manda; EliteTorrent solo añade lo que DonTorrent no tiene."""
    keys = {_et_norm(it.get("title")) for it in dt_items}
    out = list(dt_items)
    for it in et_items:
        k = _et_norm(it.get("title"))
        if k and k in keys:
            continue
        keys.add(k)
        out.append(it)
    return out


# Calidad de busqueda: DonTorrent lista CADA torrent por separado (la misma peli
# en 4K y en SD -> "Matrix Reloaded" aparece 2-3 veces) y NO ordena por relevancia
# (busca "breaking bad" y sale "El Camino" antes que la serie). Esto:
#   1) DEDUP por (titulo normalizado, AÑO, tipo) dejando la version de MEJOR
#      calidad. El AÑO es la CLAVE para no fundir REMAKES del mismo titulo
#      (Suspiria 1977 vs 2018, IT 1990 vs 2017): son pelis DISTINTAS y deben salir
#      las dos. Solo se colapsan si coinciden titulo Y año (= misma peli, otra
#      calidad). Por eso el dedup corre DESPUES del enrich TMDB (ver /catsearch).
#   2) ORDENA por relevancia al termino (exacto/prefijo arriba), ESTABLE -> dentro
#      de cada nivel respeta el orden de la web (temporadas 1,2,3...).
# NO inventa ni descarta titulos DISTINTOS -> sigue siendo reflejo de la web,
# solo mas limpio y con lo relevante arriba.
_CAT_QUAL_RANK = {"4k": 5, "2160p": 5, "uhd": 5, "1080p": 4, "1080": 4,
                  "720p": 2, "720": 2, "480p": 1}


# --- Capitulos sueltos -> UNA tarjeta de serie ------------------------------
# EliteTorrent y WolfMax NO publican una ficha por serie: publican una ficha por
# CAPITULO ("the-office-9x22", "Ted Lasso Temporada Temporada [ 4 ] Capitulo
# [ 6 ]"). Volcarlos tal cual inundaba la busqueda (30 tarjetas de lo mismo), asi
# que hasta ahora se TIRABAN y esas dos fuentes no aportaban series. Aqui se
# agrupan por serie: 1 tarjeta con los episodios dentro (cada uno con la URL de
# SU ficha, que la web resuelve al reproducir con /catetboxresolve).
#
# OJO con el titulo de EliteTorrent: llega con la codificacion rota ("The office
# ? 9?22") porque su listado mezcla encodings. Por eso la temporada/episodio se
# saca PRIMERO de la URL, que es ASCII puro y fiable (misma leccion que con las
# fichas de DonTorrent), y solo se cae al titulo cuando la URL no los lleva
# (WolfMax usa /serie-online-4k/<id>).
# "the-office-9x22" y "the-office-8x24-final" (el sufijo es habitual: FINAL,
# la calidad, un "-2" de version repetida...)
_EPU_RE = _re_dt.compile(
    r"[-/](\d{1,2})\s*x\s*(\d{1,3})(?:[-_][a-z0-9\-]{1,24})?/?$", _re_dt.I)
# "...-temporada-2-capitulo-10": EliteTorrent tambien usa este formato entero
_EPU_TC = _re_dt.compile(
    r"temporada[-_](\d{1,2})[-_]cap[i\W]?tulos?[-_](\d{1,3})", _re_dt.I)
_EPT_WF = _re_dt.compile(
    r"temporada\s*\[?\s*(\d{1,2})\s*\]?.*?cap[i\W]?tulos?\s*\[?\s*(\d{1,3})\s*\]?",
    _re_dt.I | _re_dt.S)
# "La casa del dragon 2x8" / "The office - 8x24 FINAL": el par va al final,
# como mucho con un rabo corto SIN digitos detras (FINAL, [HDTV]...)
_EPT_X = _re_dt.compile(
    r"(\d{1,2})\s*[x\u00d7]\s*(\d{1,3})\b[^\d]{0,20}$", _re_dt.I)
_EPT_SE = _re_dt.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", _re_dt.I)
# Corte del titulo: todo lo que venga desde aqui es paja de capitulo/temporada
_EP_CUT = _re_dt.compile(
    r"\s*(?:[-\u2013\u00b7|]\s*)?(?:temporada|capitulo|cap[i\W]tulo|season|"
    r"episodio|\d{1,2}\s*[x\u00d7]\s*\d{1,3}|S\d{1,2}\s*E\d{1,3}).*$",
    _re_dt.I | _re_dt.S)


def _ep_parse(it):
    """(season, episode) de un item de fuente-caja, o None si no es un capitulo."""
    u = (it.get("url") or it.get("content_id") or "").strip()
    for rx in (_EPU_RE, _EPU_TC):
        m = rx.search(u)
        if m:
            return int(m.group(1)), int(m.group(2))
    t = it.get("title") or ""
    for rx in (_EPT_WF, _EPT_X, _EPT_SE):
        m = rx.search(t)
        if m:
            try:
                return int(m.group(1)), int(m.group(2))
            except ValueError:
                return None
    return None


def _ep_base_title(t, se=None):
    """Titulo de la SERIE a partir del titulo del capitulo.

    Con `se` (temporada/episodio ya sacados de la URL) se corta tambien el
    "9x22" aunque la X llegue ROTA ("The office � 9�22"): se busca el
    par de numeros AL FINAL separado por cualquier basura. Anclado al final a
    proposito -> un titulo con numeros en medio ("Alerta 24 horas") no se toca.
    """
    t = t or ""
    if se:
        try:
            rx = _re_dt.compile(
                r"[\s\W_]*\b0*%d\s*[^\w]{0,4}\s*0*%d\b[^\d]{0,20}$"
                % (int(se[0]), int(se[1])))
            t2 = rx.sub("", t)
            if t2.strip():
                t = t2
        except Exception:
            pass
    t = _EP_CUT.sub("", t)
    t = _re_dt.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)
    t = _re_dt.sub(r"\s+", " ", t).strip(" -\u2013\u00b7|:.,")
    # restos de la codificacion rota de EliteTorrent al final ("The office ?")
    t = _re_dt.sub(r"[\s\W_]+$", "", t)
    return t.strip()


def _title_score(t):
    """Cuanto de bien escrito esta un titulo (para elegir entre variantes)."""
    t = t or ""
    if not t:
        return -99
    sc = 0
    sc -= 10 * t.count("\ufffd")            # caracteres de reemplazo = ilegible
    sc -= 10 * t.count("?")
    if any(c in t for c in "\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1\u00c1\u00c9\u00cd\u00d3\u00da\u00d1"):
        sc += 2                             # con tildes de verdad
    # a igualdad, el mejor capitalizado ("The Office" > "The office")
    sc += sum(1 for w in t.split() if w[:1].isupper()) * 0.1
    return sc


# La calidad de WolfMax va en la URL, no en el titulo: /serie-online-4k/<id>,
# /online-1080p/<id>, /peliculas-4k-2160p/... Sin ella su 4K (capitulos de 9 GB,
# lo mejor que hay en el sistema) perdia TODOS los desempates y quedaba escondido
# detras de un 1080p de otra fuente.
_WFQ = (("4k", "4K"), ("2160", "4K"), ("1080", "1080p"), ("720", "720p"))


def _wf_quality_from_url(u):
    u = (u or "").lower()
    # OJO: el DOMINIO ya lleva "4k" (wolfmax4k.com) -> si se mira la URL entera,
    # hasta /serie-online-1080p/ sale como 4K. Solo la RUTA.
    i = u.find("//")
    if i >= 0:
        j = u.find("/", i + 2)
        u = u[j:] if j >= 0 else ""
    for pat, q in _WFQ:
        if pat in u:
            return q
    return ""


def _cat_group_episodes(items):
    """Capitulos sueltos de et/wf -> 1 tarjeta de serie con `eps` dentro.
    Lo que no sea un capitulo (peliculas) sale TAL CUAL y en su mismo sitio."""
    out, byk = [], {}
    for it in items:
        src = it.get("source") or ""
        se = _ep_parse(it) if src in ("et", "wf") else None
        if not se:
            out.append(it)
            continue
        base = _ep_base_title(it.get("title") or "", se)
        if not base:
            out.append(it)
            continue
        k = (src, _et_norm(base))
        ep = {"label": "%dx%02d" % se, "season": se[0], "episode": se[1],
              "quality": it.get("quality") or "",
              "url": it.get("url") or it.get("content_id") or "",
              "content_id": it.get("url") or it.get("content_id") or "",
              "src": src}
        if not ep["quality"] and src == "wf":
            ep["quality"] = _wf_quality_from_url(ep["url"])
        card = byk.get(k)
        if card is None:
            card = {"title": base, "kind": "serie", "source": src,
                    "url": ep["url"], "content_id": ep["url"],
                    "thumb": it.get("thumb"), "quality": it.get("quality") or "",
                    "tabla": src, "eps": []}
            byk[k] = card
            out.append(card)
        if not any(e["label"] == ep["label"] for e in card["eps"]):
            card["eps"].append(ep)
        if not card.get("thumb") and it.get("thumb"):
            card["thumb"] = it.get("thumb")
        # El mismo capitulo puede venir con el titulo bien o con la codificacion
        # rota ("The Office" vs "The office \ufffd 9\ufffd21"): la tarjeta se
        # queda con el MEJOR escrito (sin caracteres de reemplazo; a igualdad, el
        # que conserve tildes, que es el que DonTorrent/ET publican bien).
        if base != card["title"] and _title_score(base) > _title_score(card["title"]):
            card["title"] = base
    for card in byk.values():
        card["eps"].sort(key=lambda e: (e["season"], e["episode"]))
        if not card.get("quality"):
            # la calidad de la tarjeta = la de sus capitulos (si coinciden)
            qs = {e.get("quality") for e in card["eps"] if e.get("quality")}
            if len(qs) == 1:
                card["quality"] = qs.pop()
    return out


def _cat_rank_dedup(items, q):
    qn = _et_norm(q)
    qtoks = [t for t in qn.split() if len(t) > 1 and t not in _DX_STOP]

    def qr(it):
        return _CAT_QUAL_RANK.get((it.get("quality") or "").strip().lower(), 0)

    # FASE 1: los items CON año -> dedup por (titulo, AÑO, tipo), mejor calidad. El
    # año (TMDB / ficha DT) separa REMAKES del mismo titulo (Suspiria 1977 vs 2018)
    # de la misma peli en otra calidad (Matrix 4K vs SD -> mismo año -> se funden).
    withy, order, titleset, noyear, passthrough = {}, [], {}, {}, []
    for it in items:
        tn = _et_norm(it.get("title"))
        if not tn:
            passthrough.append(it)
            continue
        kind = it.get("kind") or "movie"
        yr = str(it.get("year") or "").strip()
        if yr:
            k = (tn, yr, kind)
            if k not in withy:
                withy[k] = it
                order.append(k)
            elif qr(it) > qr(withy[k]):
                withy[k] = it
            titleset.setdefault((tn, kind), set()).add(yr)
        else:
            nk = (tn, kind)   # mejor calidad entre los SIN año del mismo titulo
            if nk not in noyear or qr(it) > qr(noyear[nk]):
                noyear[nk] = it
    # FASE 2: un item SIN año suele ser el MISMO homonimo que uno CON año (TMDB solo
    # le fallo el año) -> NO crear tarjeta nueva "pelada"; se descarta y, si el
    # titulo tiene UNA sola peli, le sube la calidad. Si NINGUN homonimo trae año
    # (TMDB caido del todo para ese titulo), se conserva (1 por titulo, como antes).
    out = [withy[k] for k in order]
    for nk, it in noyear.items():
        ybs = titleset.get(nk)
        if not ybs:
            out.append(it)
        elif len(ybs) == 1:
            best = withy[(nk[0], next(iter(ybs)), nk[1])]
            if qr(it) > qr(best):
                best["quality"] = it.get("quality") or best.get("quality")
    out += passthrough
    if not qn:
        return out

    def score(it):
        tn = _et_norm(it.get("title") or "")
        if not tn:
            return -1
        s = 0
        if tn == qn:                       # match EXACTO
            s = 100
        elif tn.startswith(qn):            # EMPIEZA por lo buscado
            s = 60
        elif (" " + qn + " ") in (" " + tn + " "):   # lo CONTIENE entero
            s = 40
        if qtoks:                          # +bonus por palabras de la query
            tw = set(tn.split())
            allw = sum(1 for t in qtoks if t in tw)
            if allw == len(qtoks):
                s += 20
            s += allw
        return s

    return sorted(out, key=score, reverse=True)   # estable -> respeta orden web


# Año REAL leido de la ficha de DonTorrent para desambiguar HOMONIMOS (remakes con
# el MISMO titulo: Suspiria 1977 vs 2018, IT 1990 vs 2017). El listado de DT no trae
# año y TMDB le da el mismo a ambos -> sin esto el dedup los fundiria en uno solo.
_CAT_DT_YEAR_CACHE = {}   # content_id -> "YYYY" (el año de una peli no cambia nunca)
_CAT_DT_YEAR_FAIL = {}    # content_id -> ts del ultimo fallo (negative-cache, no machacar)
_CAT_DT_YEAR_FAIL_TTL = 300
_DT_YEAR_FILE = "/tmp/mw_dt_years.json"
_DT_YEAR_LOCK = _thr.Lock()
_CAT_DT_YEAR_RE = _re_dt.compile(r"A[nñ]o[^0-9]{0,40}((?:19|20)\d{2})", _re_dt.I)


def _dt_years_load():
    try:
        with open(_DT_YEAR_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _dt_years_save(cid, y):
    # El año de una peli NO cambia -> persistir en disco lo resuelto = se calcula UNA
    # vez (sobrevive a la expiracion de la cache de busqueda y se comparte entre los
    # 2 workers). Menos fichas a DonTorrent en el tiempo -> menos riesgo de baneo.
    try:
        with _DT_YEAR_LOCK:
            d = _dt_years_load()
            d[str(cid)] = y
            tmp = _DT_YEAR_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(d, f)
            os.replace(tmp, _DT_YEAR_FILE)
    except Exception:
        pass


def _dt_detail_year(it, deadline, box=None):
    """Año REAL del campo 'Año' de la ficha DT de `it`. Orden: cache memoria -> disco
    -> GET directo (NAVEGAR DonTorrent SI funciona desde Render aunque BUSCAR este
    baneado) -> BOX (IP residencial). Cacheado (memoria+disco) y con negative-cache
    para no reintentar en bucle un cid que acaba de fallar. '' si no se pudo."""
    cid = it.get("content_id")
    if not cid:
        return ""
    cid = str(cid)
    if cid in _CAT_DT_YEAR_CACHE:
        return _CAT_DT_YEAR_CACHE[cid]
    disk = _dt_years_load().get(cid)
    if disk:
        _CAT_DT_YEAR_CACHE[cid] = disk
        return disk
    if (_t.time() - _CAT_DT_YEAR_FAIL.get(cid, 0)) < _CAT_DT_YEAR_FAIL_TTL:
        return ""   # fallo reciente -> no machacar DonTorrent
    path = it.get("dtpath") or it.get("path")
    if not path or _t.time() >= deadline:
        return ""
    html = ""
    try:   # 1) GET directo: la ficha es navegacion (no la busqueda POST baneada)
        html, _ = _cat_dt_session_get(path)
    except Exception:
        html = ""
    if not html and box and (deadline - _t.time()) > 2.5:   # 2) via box residencial
        try:
            job = "dy" + os.urandom(5).hex()
            _kb_enqueue(box, {"c": "etjob", "job": job, "op": "dthtml",
                              "path": path})
            html = (_catjob_wait(job, min(8.0, deadline - _t.time()))
                    or {}).get("html") or ""
        except Exception:
            html = ""
    m = _CAT_DT_YEAR_RE.search(html or "")
    y = m.group(1) if m else ""
    if y:
        _CAT_DT_YEAR_CACHE[cid] = y
        _CAT_DT_YEAR_FAIL.pop(cid, None)
        _dt_years_save(cid, y)
    else:
        _CAT_DT_YEAR_FAIL[cid] = _t.time()
    return y


def _cat_disambiguate_years(items, deadline, box=None, cap=12):
    """Cuando varios items DT comparten titulo+tipo Y el MISMO año (o ninguno), TMDB
    no los pudo separar (homonimos). Leemos el año REAL de la ficha de cada uno para
    que el dedup posterior MANTENGA separadas las pelis distintas (Suspiria 1977 vs
    2018) pero siga fundiendo la misma peli en otra calidad. Solo en COLISIONES
    (raro), acotado al deadline y con tope de fichas -> sin scraping masivo.
    Devuelve (items, ok): ok=False si queda alguna colision SIN resolver -> el caller
    NO debe cachear (o cachear poco) para que el siguiente intento lo reintente."""
    from collections import defaultdict
    groups = defaultdict(list)
    for it in items:
        tn = _et_norm(it.get("title"))
        if tn:
            groups[(tn, it.get("kind") or "movie")].append(it)
    targets = []
    for g in groups.values():
        if len(g) < 2:
            continue
        if len({str(i.get("year") or "") for i in g}) > 1:
            continue   # TMDB ya los diferencio por año -> no hace falta tocar DT
        for it in g:
            if it.get("source") == "dt" and it.get("content_id"):
                targets.append(it)
    if not targets:
        return items, True
    if (deadline - _t.time()) < 2.0:   # sin margen -> no resolver y avisar (no cachear)
        return items, False
    capped = targets[:cap]

    def _go(it):
        y = _dt_detail_year(it, deadline, box)
        if not y:
            return False
        old = str(it.get("year") or "")
        it["year"] = y
        if y != old:
            # El poster/sinopsis venian de la query GENERICA (la peli homonima
            # equivocada -> el remake mostraba el cartel del original). Con el año
            # real re-enriquecemos para traer la FICHA correcta (poster, nota...).
            meta = _cat_tmdb(it["title"] + " " + y,
                             "tv" if it.get("kind") == "serie" else "movie")
            if meta.get("poster"):
                it["poster"] = meta["poster"]
            if meta.get("year"):
                it["year"] = meta["year"]
            if meta.get("rating") is not None:
                it["rating"] = meta["rating"]
            for k in ("overview", "backdrop", "genres", "tmdb_id"):
                if meta.get(k):
                    it[k] = meta[k]
        return True
    results = []
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        # 2 workers = mismo tope que _DT_SEM(2) -> cada ficha consigue su turno en
        # vez de fallar al instante por contencion del semaforo.
        with _TPE(max_workers=2) as ex:
            results = list(ex.map(_go, capped))
    except Exception:
        results = []
    ok = bool(results) and all(results) and len(targets) <= cap
    return items, ok


def _tmdb_alt_titles(q):
    """Titulos ALTERNATIVOS (es / original) de TMDB para una query que no encontro
    NADA en la web -> reintentar con el nombre que usa la web (idiomas distintos:
    'interestelar'->'Interstellar', 'jungla de cristal'->'Die Hard'). Maximo 2;
    [] si TMDB caido o sin match. Solo se llama en busquedas VACIAS -> sin coste
    en las que si encuentran."""
    if _tmdb_is_down() or not q:
        return []
    qn = _et_norm(q)
    alts, seen = [], {qn}
    try:
        for ep in ("movie", "tv"):
            r = _TMDB_SESS.get(f"https://api.themoviedb.org/3/search/{ep}",
                               params={"api_key": _CAT_TMDB_KEY,
                                       "language": "es-ES", "query": q,
                                       "include_adult": "false"},
                               timeout=(2, 3))
            if r.status_code != 200:
                _tmdb_mark(False)
                continue
            res = (r.json() or {}).get("results") or []
            if not res:
                continue
            top = res[0]
            for t in (top.get("title"), top.get("name"),
                      top.get("original_title"), top.get("original_name")):
                tn = _et_norm(t or "")
                if tn and tn not in seen:
                    seen.add(tn)
                    alts.append(t)
        _tmdb_mark(True)
    except Exception:
        _tmdb_mark(False)
    return alts[:2]


def _cat_from_cache(q):
    """PLAN B de la BUSQUEDA, sin tocar la red: busca en los LISTADOS de
    DonTorrent que ya tenemos cacheados (los que el box empuja por /catfeed cada
    ~8 min y que /catbrowse sirve en el Inicio).

    Por que hace falta (verificado 2026-08-07): la busqueda de DonTorrent es un
    POST /buscar y era el UNICO camino sin plan B — el proxy CF Worker no puede
    servir POST (el JWT de Anubis va atado a la IP, §9) asi que se dejo solo con
    DoH. Ese dia coincidieron las dos desgracias: Render BANEADO por DonTorrent
    y el ISP del box RESETEANDO el DoH (WinError 10054) -> `fetch_html
    (q=obsession)` moria en 150ms y la web decia "Sin resultados"... mientras
    "Obsession" estaba EN EL INICIO, en la cache, servida por el mismo relay.

    No sustituye al buscador (solo ve lo que hay en los listados recientes),
    pero convierte un "Sin resultados" en falso en los resultados que SI tenemos.
    Los items ya vienen enriquecidos (poster HD + nota), asi que salen bien."""
    out, seen = [], set()
    try:
        src = dict(_catbrowse_load())
        for k, v in _CATBROWSE_CACHE.items():
            src[k] = v
        for key, rec in src.items():
            if str(key).startswith("dx:"):      # solo DonTorrent, no el fallback dx
                continue
            for it in (rec or {}).get("items", []) or []:
                title = it.get("title") or ""
                if not title or not _q_relevant(title, q):
                    continue
                k = it.get("content_id") or title.lower()
                if k in seen:
                    continue
                seen.add(k)
                out.append(dict(it))
    except Exception:
        pass
    return out


# --- Lo que DonTorrent trae por QUERY, aunque llegue TARDE -------------------
# La busqueda responde rapido con lo que tenga; el hilo de la caja sigue vivo y,
# cuando llega (a veces 15-20s: Anubis frio, o el ISP tumbando el POST /buscar y
# teniendo que probar otra caja), deja aqui su resultado. Asi el siguiente
# vistazo -el reintento del front, u otra persona buscando lo mismo- ya lo tiene.
_DTQ_CACHE = {}
_DTQ_TTL = 600
_DTQ_MAX = 80
_DTQ_FILE = "/tmp/mw_dtq.json"


def _dtq_load():
    try:
        with open(_DTQ_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _dtq_get(q):
    k = (q or "").lower()
    ent = _DTQ_CACHE.get(k)
    if not ent:
        ent = _dtq_load().get(k)
        if ent:
            _DTQ_CACHE[k] = ent
    if ent and (_t.time() - ent.get("ts", 0)) < _DTQ_TTL:
        return ent.get("items") or []
    return []


def _dtq_put(q, items):
    if not items:
        return
    k = (q or "").lower()
    rec = {"items": items, "ts": _t.time()}
    _DTQ_CACHE[k] = rec
    try:
        d = _dtq_load()
        d[k] = rec
        if len(d) > _DTQ_MAX:
            for kk in sorted(d, key=lambda x: d[x].get("ts", 0))[:len(d) - _DTQ_MAX]:
                d.pop(kk, None)
        tmp = _DTQ_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _DTQ_FILE)
    except Exception:
        pass


@app.get("/catsearch")
def catsearch():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": []})
    # Cache fresca -> INSTANTANEO y sin tocar DonTorrent/TMDB (menos baneo). Solo
    # se cachean resultados NO vacios (ver final): si DonTorrent estaba caido y la
    # busqueda salio vacia, la siguiente reintenta de verdad (no cachea el vacio).
    qkey = q.lower()
    now = _t.time()
    cent = _CATSEARCH_CACHE.get(qkey)
    if not cent:   # memoria vacia (worker frio / otro worker la calculo) -> disco
        cent = _catsearch_load().get(qkey)
        if cent:
            _CATSEARCH_CACHE[qkey] = cent
    if cent and (now - cent["ts"]) < cent.get("ttl", _CATSEARCH_TTL):
        return jsonify({"items": cent["items"], "cached": True})
    # --- Single-flight: si una busqueda IDENTICA ya se esta calculando en este
    # worker, NO lanzamos otro fan-out; esperamos su resultado y servimos la cache.
    # Mata la amplificacion de los reintentos del front (csTry hasta 6x) que era
    # una de las causas de que el relay se saturase al buscar varias cosas seguidas. --
    _owner = False
    _ev = _CATSEARCH_INFLIGHT.get(qkey)
    if _ev is None:
        with _CATSEARCH_INFLIGHT_LOCK:
            _ev = _CATSEARCH_INFLIGHT.get(qkey)
            if _ev is None:
                _ev = _thr.Event()
                _CATSEARCH_INFLIGHT[qkey] = _ev
                _owner = True
    if not _owner:
        # Otra peticion identica manda; esperamos SU resultado (no abrimos otro
        # fan-out). Cuando termine, servimos su cache.
        _ev.wait(8.0)
        cent = _CATSEARCH_CACHE.get(qkey) or _catsearch_load().get(qkey)
        if cent and (_t.time() - cent["ts"]) < cent.get("ttl", _CATSEARCH_TTL):
            return jsonify({"items": cent["items"], "cached": True})
        if cent and cent.get("items"):
            # caducada pero utilizable: mejor lo de hace un rato que nada
            return jsonify({"items": cent["items"], "cached": True,
                            "stale": True, "partial": True})
        # El dueño aun no ha terminado. OJO: aqui se devolvia un 503 con el
        # cuerpo VACIO y eso REVIENTA el r.json() del navegador -> el front lo
        # tomaba por error de red y acababa pintando "Despertando el
        # servidor...". Respuesta valida con `retry` y que el front vuelva.
        return jsonify({"items": [], "retry": True, "partial": True})
    try:
        code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
        # Probes con TOPE DURO (hilos daemon): si DonTorrent/ET cuelgan la conexion
        # (Render bloqueado -> ignora el timeout de requests), los abandonamos y
        # seguimos. Asi la peticion NUNCA se cuelga y llega el fallback al box.
        import threading as _th
        _r = {"dt": [], "et": [], "dx": [], "box": []}
        # El BOX vivo (IP residencial) es la via FIABLE y RAPIDA: la IP datacenter
        # de Render es LENTA con ambas fuentes (Anubis de DonTorrent y Cloudflare de
        # DivxTotal) -> en frio la busqueda directa tarda >20s y el front se rinde
        # ("Despertando el servidor..."). Con code va a ESE box; SIN code, a
        # cualquier box vivo (_any_live_box, mismo patron que /dtpacked) -> la
        # busqueda desde la WEB va rapida AUNQUE no se escriba codigo. El box corre
        # EN PARALELO con el intento directo (antes era SECUENCIAL: 8s directo + 20s
        # box = >28s, imposible para el front).
        box = code if len(code) == 6 else _any_live_box()
        _ready = _th.Event()   # lo activan DT-directo o el box al traer resultados;
        # tambien salta cuando AMBOS han TERMINADO aunque sea sin resultados: si
        # DonTorrent esta caido (breaker -> "" al instante) y no hay box vivo, no
        # queda nada que esperar -> antes se consumia el wait entero (16s) para
        # servir un resultado solo-DivxTotal que estaba listo en ~3s.
        _left = [2]                     # DT-directo + box pendientes
        _left_lock = _th.Lock()

        def _src_done():
            with _left_lock:
                _left[0] -= 1
                if _left[0] <= 0:
                    _ready.set()

        def _w_dt():
            try:
                r = _cat_parse_items(_cat_dt_html(q)) or []
                _r["dt"] = r
                if r:
                    _dtq_put(q, r)     # aunque la peticion ya haya respondido
                    _ready.set()
            except Exception:
                pass
            finally:
                _src_done()

        _box_done = _th.Event()   # el box TERMINO (con o sin resultados)

        def _w_box():
            try:
                if not box:
                    return

                def _ask(b):
                    j = "ds" + os.urandom(5).hex()
                    _kb_enqueue(b, {"c": "etjob", "job": j, "op": "dthtml",
                                    "q": q})
                    return j
                _okh = lambda r: bool((r or {}).get("html"))
                _jobs = [_ask(box)]
                # HEDGE: si la caja elegida no ha traído el HTML en 6s, se lo
                # pedimos TAMBIEN a otra caja viva y nos quedamos con la primera
                # que llegue. El buscador de DonTorrent depende de que ESA caja
                # tenga el Anubis caliente (~0,6s) o frío (~30s) y de que su ISP
                # no le tumbe el POST /buscar: con 6 cajas encendidas casi
                # siempre hay una en condiciones. En el caso normal NO cuesta
                # nada: la segunda solo se pregunta cuando la primera se retrasa.
                res = _catjob_wait_any(_jobs, 6.0, _okh)
                if not _okh(res):
                    b2 = next((b for b in _live_boxes() if b != box), None)
                    if b2:
                        _jobs.append(_ask(b2))
                    if _jobs:
                        res2 = _catjob_wait_any(_jobs, 10.0, _okh)
                        if _okh(res2):
                            res = res2
                h = (res or {}).get("html") or ""
                if h:
                    r = _cat_parse_items(h) or []
                    _r["box"] = r
                    if r:
                        _dtq_put(q, r)   # aunque la peticion ya haya respondido
                        _ready.set()
            except Exception:
                pass
            finally:
                _src_done()
                _box_done.set()

        def _w_et():
            try:
                _r["et"] = _et_search(q) or []
            except Exception:
                pass

        def _w_dx():
            # DivxTotal DIRECTO (sin code); LENTO desde Render (Cloudflare) -> ultimo
            # recurso, nunca bloquea la respuesta.
            try:
                _r["dx"] = _dx_search_items(q) or []
            except Exception:
                pass
        _ths = [_th.Thread(target=f, daemon=True)
                for f in (_w_dt, _w_box, _w_et, _w_dx)]
        for t in _ths:
            t.start()
        # Devolvemos EN CUANTO DonTorrent-directo (Anubis caliente ~7s) o el box
        # (residencial ~5-8s) traigan resultados, con TOPE GLOBAL de 16s < 20s del
        # front -> la busqueda NUNCA se "eterniza". Tras el wait damos un respiro
        # corto a ET/DX para recoger lo que ya hayan traido (sin esperar su lentitud).
        # TOPE TOTAL DURO (now+19s, bajo los 20s del front) -> la busqueda NUNCA se
        # cuelga ni el front se rinde. Antes podian SUMARSE espera-box (16s) +
        # gracia-DX (6s) + ScraperAPI (18s) = hasta 40s ("Buscando..." eterno). CLAVE:
        # al box/DT-directo le damos su espera COMPLETA (~16s; NO cortar una fuente
        # lenta-pero-VIVA, p.ej. Anubis frio -> antes con 13s la cortaba y devolvia
        # VACIO en falso); el RESTO (DX, ScraperAPI, fallback, enrich) solo consume lo
        # que QUEDA -> el total nunca pasa del tope. El caso comun (box ~5-8s) corta
        # la espera al traer resultados y va rapido.
        # TOPE TOTAL de 12s (el front espera 20). La busqueda ya NO se queda
        # esperando a un camino que hoy no trae nada: responde con lo que tenga
        # y lo que llegue tarde se guarda para el siguiente vistazo (_DTQ).
        _dl = now + 12.0
        _rem = lambda: max(0.0, _dl - _t.time())
        # ¿Tenemos ya la respuesta en la cache del catalogo? Es local e
        # INSTANTANEO (sin red), asi que se mira ANTES de esperar a nadie.
        _cache_hits = _cat_from_cache(q)
        # Con algo en mano, la espera la marca EL BOX, no el reloj: es el camino
        # fiable (IP residencial). En cuanto termina —traiga resultados o no—
        # salimos, sin quedarnos esperando al intento DIRECTO desde Render, que
        # con DonTorrent baneado tarda una eternidad en rendirse y era el que
        # inflaba la busqueda a 10-20s. Si el box va bien (~5-8s) se le espera
        # igual, asi que NO se pierden los resultados del buscador completo; si
        # esta muerto (hoy: ISP reseteando el DoH, falla en 150ms) salimos casi
        # al instante con lo cacheado. Sin nada en cache, la espera de siempre:
        # mas vale tardar que devolver vacio en falso.
        # (2026-08-07: el dueño lo dijo claro — 20s buscando es inaceptable.)
        # Lo que la caja trajo en una busqueda ANTERIOR de esto mismo (puede
        # haber llegado despues de responder): es local y vale como resultado.
        _late_hits = _dtq_get(q)
        if _late_hits:
            _cache_hits = _cat_merge(_late_hits, _cache_hits)
        # Con algo en mano se espera POCO; sin nada, un respiro mas largo pero
        # tampoco eterno: mas vale enseñar 8 resultados en 4s que 30 en 20s.
        # Si ya tenemos el resultado REAL de DonTorrent (lo trajo la caja en la
        # pasada anterior y quedo en _DTQ), casi no hay que esperar a nadie.
        _ready.wait(min(1.5 if _late_hits else (4.0 if _cache_hits else 8.0),
                        _rem()))
        _ths[2].join(min(0.4, _rem()))          # ET (off) -> instantaneo
        # DX: si DT/box trajeron algo, respiro corto (1.5s) para fusionar lo que ya
        # este; si terminaron SIN resultados (el wait salio al instante por el
        # contador), DivxTotal es la unica fuente restante -> se le da su tiempo
        # real (reto Cloudflare ~3-6s), siempre acotado al deadline total.
        # DX: el front lo pide APARTE (/catdxsearch) y lo fusiona al llegar, asi
        # que aqui solo se recoge lo que ya este; no se le espera.
        _ths[3].join(min(1.5 if (_r["dt"] or _r["box"]) else 3.0, _rem()))  # DX
        dt_items = _r["dt"] or _r["box"]        # el box se parsea igual que DT
        # Ningun camino a DonTorrent vivo (Render baneado Y el ISP del box
        # tumbando el POST de /buscar) -> lo que ya teniamos cacheado (calculado
        # arriba, antes de esperar a la red).
        if not dt_items:
            dt_items = _cache_hits
        et_items = _r["et"]
        dx_items = _r["dx"]
        # FAILOVER anti-baneo via ScraperAPI (IPs residenciales). Solo si el directo
        # NO trajo NADA Y queda presupuesto suficiente -> nunca pasa del tope. En uso
        # normal NO gasta creditos. Permite buscar SIN el box (Render baneado).
        if (not dt_items and not dx_items) and _sapi_credits_ok() and _rem() > 4.0:
            dx_items = _bounded(lambda: _dx_search_items(q, proxy=True),
                                min(8.0, _rem()), []) or []
        if not dt_items and not et_items and not dx_items:
            # FALLBACK DE IDIOMA: la web puede tener el titulo en OTRO idioma
            # ('interestelar'->'Interstellar'). Solo si la busqueda salio VACIA.
            # Vive DENTRO del presupuesto comun (_dl): antes tenia el suyo
            # propio de 18,5s y por eso una busqueda vacia acababa tardando 15,8s
            # (medido). Si no cabe, no se intenta: la respuesta sale `partial` y
            # la 2a pasada del front, ya con la cache caliente, tendra margen.
            _fdl = _dl
            _alts = (_bounded(lambda: _tmdb_alt_titles(q),
                              min(3.5, _fdl - _t.time()), [])
                     if (_fdl - _t.time()) > 5.0 else [])
            for _alt in _alts:
                if _fdl - _t.time() <= 2.0:
                    break
                try:   # DT-directo ACOTADO al presupuesto (Anubis frio no lo revienta)
                    _b = min(3.5, _fdl - _t.time())
                    r2 = _cat_parse_items(
                        _bounded(lambda a=_alt: _cat_dt_html(a), _b, "") or "") or []
                except Exception:
                    r2 = []
                _fr = _fdl - _t.time()
                if not r2 and box and _fr > 2.5:
                    try:
                        j2 = "da" + os.urandom(5).hex()
                        _kb_enqueue(box, {"c": "etjob", "job": j2,
                                          "op": "dthtml", "q": _alt})
                        h2 = (_catjob_wait(j2, min(4.0, _fr)) or {}).get("html") or ""
                        if h2:
                            r2 = _cat_parse_items(h2) or []
                    except Exception:
                        r2 = []
                if r2:
                    dt_items = r2
                    break
            if not dt_items:
                return jsonify({"items": [],
                                "partial": not (_r["dt"] or _r["box"])})
        merged = _cat_merge(_cat_merge(dt_items, et_items), dx_items)
        # Enrich (poster/AÑO/genero TMDB) ACOTADO al deadline total: con TMDB
        # lento/frio podia añadir ~5s y pasarse del tope. Si no le da tiempo,
        # seguimos con los items SIN enriquecer (titulos ya visibles; el front no se
        # cuelga). El breaker TMDB ya evita que cada llamada cuelgue. Va PRIMERO
        # porque el dedup necesita el AÑO para no fundir remakes del mismo titulo
        # (Suspiria 1977 vs 2018); la cache TMDB por titulo hace que enriquecer las
        # versiones repetidas sea gratis (mismo titulo -> mismo año cacheado).
        # Si TODO viene ya enriquecido (es el caso de la cache del catalogo:
        # poster HD y nota ya resueltos), NO se toca TMDB — que ademas banea a
        # Render y es justo lo que hace lenta esta ruta.
        if merged and all(it.get("poster") and it.get("rating") is not None
                          for it in merged):
            enr = merged
        else:
            # Dentro del presupuesto COMUN (_dl). Si TMDB no da tiempo, los
            # items salen con su caratula propia -> mejor eso que 20s de rueda;
            # ademas la respuesta se cachea con TTL corto y la 2a pasada del
            # front los enriquece (para entonces la cache TMDB ya esta caliente).
            enr = _bounded(lambda: _cat_enrich(merged),
                           max(1.5, min(5.0, _rem())), merged) or merged
        # Homonimos (Suspiria 1977 vs 2018): DT no da año en el listado y TMDB le da
        # el mismo a ambos -> el dedup los fundiria. Si hay choque de titulo, leemos
        # el año REAL de la ficha DT (la propia funcion gestiona su presupuesto). Si
        # NO logra resolver la colision (sin margen/box/DT) -> _disok=False y NO se
        # cachea largo (TTL corto) para que el siguiente intento la resuelva.
        enr, _disok = _cat_disambiguate_years(enr, min(_dl, now + 10.5), box)
        items = _cat_rank_dedup(enr, q)   # dedup (titulo+año) + orden por relevancia
        # ¿Falta el buscador de DonTorrent? Entonces esto es PARCIAL: el front
        # volvera a preguntar en unos segundos y para entonces el hilo de la caja
        # habra dejado su resultado en _DTQ.
        _parcial = not (_r["dt"] or _r["box"])
        # sin poster = el enrich no cupo -> tampoco es una respuesta "definitiva"
        if enr and not all(it.get("poster") for it in enr):
            _parcial = True
        if items:   # cachear SOLO resultados utiles (no cachear vacios -> reintentar)
            # Si una colision de homonimos quedo SIN resolver, TTL corto (90s) -> se
            # reintenta pronto (y al resolverla se cachea ya el TTL largo), pero sin
            # machacar (no recalcula en cada pulsacion). Resuelto/limpio -> TTL normal.
            rec = {"items": items, "ts": now}
            if not _disok:
                rec["ttl"] = 90
            if _parcial:
                rec["ttl"] = 150  # parcial -> caduca pronto y se completa solo
                                  # (45s era tan corto que recalculaba en cadena
                                  #  y disparaba el single-flight a todas horas)
            _CATSEARCH_CACHE[qkey] = rec
            try:   # persistir a disco -> compartido entre workers (gthread=2 procesos)
                disk = _catsearch_load()
                disk[qkey] = rec
                if len(disk) > _CATSEARCH_MAX:   # poda: deja las MAX mas recientes
                    for k in sorted(disk, key=lambda k: disk[k].get("ts", 0))[
                            :len(disk) - _CATSEARCH_MAX]:
                        disk.pop(k, None)
                _catsearch_save(disk)
            except Exception:
                pass
            if len(_CATSEARCH_CACHE) > _CATSEARCH_MAX:   # evicta la mas vieja en memoria
                try:
                    old = min(_CATSEARCH_CACHE, key=lambda k: _CATSEARCH_CACHE[k]["ts"])
                    _CATSEARCH_CACHE.pop(old, None)
                except Exception:
                    _CATSEARCH_CACHE.clear()
        return jsonify({"items": items, "partial": _parcial})
    finally:
        # SIEMPRE liberamos el single-flight (aunque haya excepcion) -> nunca deja
        # una query "bloqueada" para siempre, y despierta a los que esperan.
        with _CATSEARCH_INFLIGHT_LOCK:
            _CATSEARCH_INFLIGHT.pop(qkey, None)
        _ev.set()


_DXBG = {}   # queries con el failover de ScraperAPI corriendo en 2o plano


@app.get("/catdxsearch")
def catdxsearch():
    """DivxTotal DIRECTO desde el relay, en JSON enriquecido, para que el FRONT lo
    pida EN PARALELO y lo fusione cuando llegue. Por que separado de /catsearch: el
    dominio actual de DivxTotal (divxtotal.foo) esta BLOQUEADO por el ISP residencial
    -> ni el box ni la casa lo alcanzan; pero la IP de datacenter de Render SI. Y
    DivxTotal tarda ~6s (reto Cloudflare), mas que el tope de DX dentro de /catsearch
    (1.5s) -> ahi nunca entraba. Aqui le damos su tiempo SIN frenar el resultado
    principal (DT). Cacheado 10 min por query -> no machaca DivxTotal en repeticiones."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": []})
    qkey = "dx\x01" + q.lower()
    now = _t.time()
    cent = _CATSEARCH_CACHE.get(qkey) or _catsearch_load().get(qkey)
    if cent and (now - cent.get("ts", 0)) < cent.get("ttl", _CATSEARCH_TTL):
        _CATSEARCH_CACHE[qkey] = cent
        return jsonify({"items": cent["items"], "cached": True})
    items = _bounded(lambda: _dx_search_items(q), 8.0, []) or []
    if not items and _sapi_credits_ok() and qkey not in _DXBG:
        # FAILOVER anti-tarpit via ScraperAPI (IP residencial): Cloudflare
        # tarpitea el patron '/?s=' desde la IP de Render (la portada pasa, la
        # BUSQUEDA no) -> el directo sale vacio aunque DivxTotal este vivo.
        # Solo pagina 1 (1 credito; plan free 1000/mes, _sapi_credits_ok guarda
        # el saldo) y SOLO cuando el directo fallo -> en uso normal no gasta.
        # El front no aborta este endpoint (fusiona cuando llegue) -> el tiempo
        # extra no rompe nada.
        # 30s de margen: ScraperAPI reintenta INTERNAMENTE (hasta 60s); con un
        # tope corto el proxy casi nunca terminaba y el failover salia vacio.
        # Pero ESPERARLO dejaba la peticion en 44s y al front mirando una rueda:
        # ahora va en SEGUNDO PLANO y deja el resultado en la cache -> quien
        # vuelva a buscar eso (el propio front, que reintenta) ya lo tiene.
        _DXBG[qkey] = _t.time()

        def _dx_bg(q=q, qkey=qkey):
            try:
                r = _dx_search_items(q, max_pages=1, proxy=True) or []
                if r:
                    r = _bounded(lambda: _cat_enrich(r, limit=40), 8.0, r) or r
                    r = _cat_rank_dedup(r, q)
                    rec = {"items": r, "ts": _t.time()}
                    _CATSEARCH_CACHE[qkey] = rec
                    try:
                        disk = _catsearch_load()
                        disk[qkey] = rec
                        _catsearch_save(disk)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                _DXBG.pop(qkey, None)
        _thr.Thread(target=_dx_bg, daemon=True).start()
        items = []
    if items:
        items = _bounded(lambda: _cat_enrich(items, limit=40), 6.0, items) or items
        items = _cat_rank_dedup(items, q)
        rec = {"items": items, "ts": now}
        _CATSEARCH_CACHE[qkey] = rec
        try:   # persistir (compartido entre los 2 workers gthread)
            disk = _catsearch_load()
            disk[qkey] = rec
            if len(disk) > _CATSEARCH_MAX:
                for k in sorted(disk, key=lambda k: disk[k].get("ts", 0))[
                        :len(disk) - _CATSEARCH_MAX]:
                    disk.pop(k, None)
            _catsearch_save(disk)
        except Exception:
            pass
    return jsonify({"items": items})


@app.get("/catetresolve")
def catetresolve():
    u = (request.args.get("u") or "").strip()
    if "elitetorrent" not in u.lower():
        return jsonify({"link": ""}), 400
    return jsonify({"link": _et_resolve(u) or ""})


# === EliteTorrent via BOX (IP residencial) =================================
# Render esta bloqueado por el Cloudflare de ET, pero el box (IP de casa) SI
# entra. El movil pide /catetbox?code=&q= -> encolamos un evento 'etjob' (mismo
# canal que el mando) -> el box scrapea ET y POSTea el resultado a /catjob/done
# -> aqui hacemos long-poll hasta tenerlo (o timeout -> [] y manda DonTorrent).
_CATJOB_FILE = "/tmp/mw_catjob.json"
_CATJOB_TTL = 120


def _catjob_load():
    try:
        with open(_CATJOB_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _catjob_save(d):
    try:
        tmp = _CATJOB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _CATJOB_FILE)
    except Exception:
        pass


def _kb_enqueue(code, ev):
    """Mete un evento en la cola del box (la que consume /kb/poll).
    Bajo lock entre procesos para no PISAR/perder el evento si el box sondea
    (load->pop->save) a la vez que este enqueue (load->add->save)."""
    with _FileLock(_KB_FILE):
        d = _kb_clean(_kb_load())
        entry = d.get(code) or {"ev": [], "ts": _t.time()}
        evs = entry.get("ev", [])
        evs.append(ev)
        entry["ev"] = evs[-20:]
        entry["ts"] = _t.time()
        d[code] = entry
        _kb_save(d)


def _catjob_wait_any(jobs, secs, ok=None):
    """Espera a VARIOS trabajos de caja a la vez y devuelve el PRIMERO que sirva.
    `jobs` es una lista MUTABLE: los que ya contestaron se van quitando (así se
    puede volver a llamar sin re-esperar a los que ya respondieron). `ok(res)`
    decide si un resultado vale; los que no valen se descartan y se sigue
    esperando a los demás — una caja puede contestar VACÍA porque su ISP le tumba
    la petición mientras otra la trae entera."""
    end = _t.time() + secs
    last = None
    while jobs and _t.time() < end:
        got = []
        with _FileLock(_CATJOB_FILE):   # serializa con /catjob/done
            d = _catjob_load()
            for j in list(jobs):
                r = d.pop(j, None)
                if r is not None:
                    got.append(r)
                    jobs.remove(j)
            if got:
                _catjob_save(d)
        for r in got:
            if ok is None or ok(r):
                return r
            last = r
        if not got:
            _t.sleep(0.4)
    return last


def _catjob_wait(job, secs):
    end = _t.time() + secs
    while _t.time() < end:
        with _FileLock(_CATJOB_FILE):   # serializa con /catjob/done
            d = _catjob_load()
            r = d.pop(job, None)
            if r is not None:
                _catjob_save(d)
        if r is not None:
            return r
        _t.sleep(0.4)
    return None


# === INDICE DE WOLFMAX EN EL RELAY ==========================================
# Lo empujan las cajas (POST /wffeed) desde su indice local. Con esto la busqueda
# de WolfMax es LOCAL: milisegundos, sin cola, sin depender de que haya una caja
# despierta y sin que el bloqueo del ISP pinte nada. Se persiste en /tmp (se
# pierde en cada deploy) y se re-pide a una caja en cuanto hace falta.
_WFIDX = {}                      # url -> {"t": titulo, "k": kind, "q": calidad}
_WFIDX_TS = [0.0]
_WFIDX_FILE = "/tmp/mw_wfidx.json"
_WFIDX_MAX = 15000
_WFIDX_ASKED = [0.0]             # ultima vez que se pidio a una caja


def _wfidx_load():
    if _WFIDX:
        return _WFIDX
    try:
        with open(_WFIDX_FILE, "r", encoding="utf-8") as f:
            d = _json.load(f) or {}
        _WFIDX.update(d.get("e") or {})
        _WFIDX_TS[0] = d.get("ts", 0)
    except Exception:
        pass
    return _WFIDX


# --- Copia del indice fuera de /tmp (sobrevive a los despliegues) --------
_WFIDX_SYNC = "https://mw-sync.israeldm93.workers.dev/wfidx"
_WFIDX_SUBIDO = [0.0]        # cuando se subio por ultima vez
_WFIDX_CAMBIOS = [0]         # entradas nuevas desde la ultima subida


def _wfidx_nube_baja():
    """Trae el indice guardado en el worker (solo si aqui no hay nada)."""
    try:
        if _wfidx_load():
            return 0
        import base64
        import gzip
        r = requests.get(_WFIDX_SYNC, timeout=12)
        d = r.json() if r.status_code == 200 else {}
        gz = (d or {}).get("gz")
        if not gz:
            return 0
        crudo = gzip.decompress(base64.b64decode(gz)).decode("utf-8")
        ent = _json.loads(crudo) or {}
        if not isinstance(ent, dict) or not ent:
            return 0
        _WFIDX.update(ent)
        _WFIDX_TS[0] = _t.time()
        _wfidx_save()
        return len(ent)
    except Exception:
        return 0


def _wfidx_nube_sube(forzar=False):
    """Guarda el indice en el worker, como mucho cada 5 minutos."""
    try:
        ahora = _t.time()
        if not forzar and (ahora - _WFIDX_SUBIDO[0] < 300 or _WFIDX_CAMBIOS[0] < 25):
            return 0
        idx = _wfidx_load()
        if not idx:
            return 0
        import base64
        import gzip
        gz = base64.b64encode(
            gzip.compress(_json.dumps(idx).encode("utf-8"), 6)).decode("ascii")
        _WFIDX_SUBIDO[0] = ahora
        _WFIDX_CAMBIOS[0] = 0
        r = requests.post(_WFIDX_SYNC, json={"gz": gz}, timeout=20)
        return len(idx) if r.status_code == 200 else 0
    except Exception:
        return 0


def _wfidx_arranca():
    """Al arrancar: recuperar el indice (en un hilo, sin frenar el arranque)."""
    def _ir():
        try:
            _t.sleep(2)
            n = _wfidx_nube_baja()
            if n:
                print("[wfidx] recuperadas %d entradas de la copia" % n)
        except Exception:
            pass
    try:
        _thr.Thread(target=_ir, daemon=True).start()
    except Exception:
        pass


_wfidx_arranca()      # recupera el indice nada mas arrancar (hilo aparte)


def _wfidx_save():
    try:
        tmp = _WFIDX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump({"ts": _WFIDX_TS[0], "e": _WFIDX}, f)
        os.replace(tmp, _WFIDX_FILE)
    except Exception:
        pass


def _wf_norm(s):
    s = _wud.normalize("NFKD", s or "")
    s = "".join(c for c in s if not _wud.combining(c)).lower()
    return _re_dt.sub(r"[^a-z0-9 ]", " ", s)


def _wfidx_ask_box():
    """Pedir a una caja que empuje su indice (como mucho una vez por minuto)."""
    if _t.time() - _WFIDX_ASKED[0] < 60:
        return
    box = _any_live_box()
    if not box:
        return
    _WFIDX_ASKED[0] = _t.time()
    try:
        _kb_enqueue(box, {"c": "wfidx"})
    except Exception:
        pass


def _wf_idx_search(q, limit=40):
    """Busqueda LOCAL en el indice de WolfMax. AND de tokens, sin red."""
    idx = _wfidx_load()
    if not idx:
        return []
    toks = [t for t in _wf_norm(q).split() if len(t) > 1]
    if not toks:
        return []
    out = []
    for url, e in idx.items():
        tn = _wf_norm(e.get("t"))
        if not tn:
            continue
        if all(t in tn for t in toks):
            kind = "serie" if (e.get("k") or "").startswith("tvshow") else "movie"
            out.append({"title": e.get("t") or "", "kind": kind, "source": "wf",
                        "url": url, "content_id": url, "thumb": None,
                        "quality": e.get("q") or _wf_quality_from_url(url),
                        "tabla": "wf"})
            if len(out) >= limit * 4:
                break
    # los de MAS calidad primero (4K arriba: es lo que el dueño quiere ver)
    out.sort(key=lambda it: -_CAT_QUAL_RANK.get(
        (it.get("quality") or "").lower(), 0))
    return out[:limit]


# Marca de capitulo en el titulo: sin ella no hay forma de agrupar una serie.
_WF_CAP_RE = re.compile(r"(cap[ií]tulo|\bcap\.?\s*\d|\d+\s*x\s*\d{2}|temporada)",
                        re.I)
# URL de WolfMax que apunta a UN capitulo (no a la ficha de la serie).
_WF_EP_URL_RE = re.compile(r"/(?:serie-online[\w-]*|online|capitulo|episodio)/\d+", re.I)


def _wf_palabras(t):
    """Palabras del titulo, en minusculas y sin signos (para comparar)."""
    return [w for w in re.split(r"[^\w]+", (t or "").lower(), flags=re.U) if w]


def _wf_completa_partidas(items):
    """Completa cada serie de WolfMax con los trozos que en el indice estan
    titulados con una PARTE de su nombre.

    En WolfMax los capitulos de una misma serie aparecen titulados de varias
    maneras ("Widows Bay" y "La maldicion de Widows Bay"). El filtro de
    relevancia se lleva por delante los que no contienen todas las palabras de
    la busqueda, asi que la serie salia con la mitad de capitulos segun como se
    escribiera. Aqui se recuperan: se buscan en el indice las entradas cuyo
    titulo sea una tira de palabras SEGUIDAS del titulo de la serie (dos o
    mas), y se dejan en manos de `_une_series_partidas`, que sigue exigiendo
    misma calidad y capitulos que no se solapen.
    """
    series = [it for it in (items or [])
              if (it or {}).get("source") == "wf" and it.get("kind") == "serie"]
    if not series:
        return items
    try:
        idx = _wfidx_load()
    except Exception:
        return items
    if not idx:
        return items
    extra = []
    for it in series:
        pal = _wf_palabras(it.get("title"))
        if len(pal) < 2:
            continue
        # todas las tiras de 2+ palabras seguidas del titulo, menos el entero
        tiras = set()
        for i in range(len(pal)):
            for j in range(i + 2, len(pal) + 1):
                if j - i < len(pal):
                    tiras.add(" ".join(pal[i:j]))
        if not tiras:
            continue
        for u, e in idx.items():
            t = (e or {}).get("t") or ""
            if _wf_pobre(u, t, e.get("k")):
                continue
            base = " ".join(_wf_palabras(_ep_base_title(t)))
            if base and base in tiras:
                extra.append({"title": t, "url": u, "content_id": u,
                              "kind": ("serie" if (e.get("k") or "").startswith("tvshow")
                                       else "movie"),
                              "quality": e.get("q") or "", "source": "wf",
                              "tabla": "wf"})
    if not extra:
        return items
    extra = _cat_group_episodes(extra)
    for x in extra:
        if not x.get("quality"):
            x["quality"] = _wf_quality_from_url(x.get("url") or x.get("content_id"))
        disp, ql = _cat_clean_quality(x.get("title", ""))
        x["title"] = disp
        if not x.get("quality"):
            x["quality"] = ql
    # Solo interesan como RELLENO: se unen a las que ya estaban y lo que no se
    # una se descarta (no vamos a sacar tarjetas que el usuario no ha buscado).
    unido = _une_series_partidas(list(items) + [x for x in extra
                                                if x.get("kind") == "serie"])
    return [it for it in unido if it in items]


def _une_series_partidas(items):
    """Une tarjetas de la MISMA fuente que son trozos de la misma serie.

    Ver la cabecera del arreglo: "Widows Bay" y "La maldicion de Widows Bay"
    eran la misma serie con los capitulos repartidos. Solo se unen si un titulo
    contiene al otro (palabras completas), comparten fuente y calidad, y sus
    capitulos NO se solapan -> dos series que se parezcan de nombre pero tengan
    el mismo 1x01 NUNCA se mezclan.
    """
    def _pal(t):
        t = _cat_norm_title(t) if "_cat_norm_title" in globals() else (t or "").lower()
        return [w for w in re.split(r"[^\w]+", t, flags=re.U) if w]

    def _clave_eps(it):
        out = set()
        for e in (it.get("eps") or []):
            s_, e_ = e.get("season"), e.get("episode")
            if s_ or e_:
                out.add((s_ or 0, e_ or 0))
            elif e.get("label"):
                out.add(("l", e["label"]))
        return out

    series = [it for it in (items or [])
              if (it or {}).get("kind") == "serie" and (it.get("eps") or [])]
    if len(series) < 2:
        return items
    fuera = set()
    for i, a in enumerate(series):
        if id(a) in fuera:
            continue
        for b in series[i + 1:]:
            if id(b) in fuera:
                continue
            if (a.get("source") or "") != (b.get("source") or ""):
                continue
            if (a.get("quality") or "") != (b.get("quality") or ""):
                continue
            pa, pb = _pal(a.get("title")), _pal(b.get("title"))
            if not pa or not pb:
                continue
            corto, largo = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
            # el titulo corto tiene que estar ENTERO dentro del largo, seguido
            if not any(largo[k:k + len(corto)] == corto
                       for k in range(len(largo) - len(corto) + 1)):
                continue
            if _clave_eps(a) & _clave_eps(b):
                continue                       # se solapan: son series distintas
            # gana el del titulo mas largo; se lleva los capitulos de los dos
            ganador, perdedor = (a, b) if len(pa) >= len(pb) else (b, a)
            eps = (ganador.get("eps") or []) + (perdedor.get("eps") or [])
            eps.sort(key=lambda e: (e.get("season") or 0, e.get("episode") or 0,
                                    e.get("label") or ""))
            ganador["eps"] = eps
            for campo in ("poster", "year", "rating", "overview", "tmdb_id"):
                if not ganador.get(campo) and perdedor.get(campo):
                    ganador[campo] = perdedor[campo]
            fuera.add(id(perdedor))
    if not fuera:
        return items
    return [it for it in items if id(it) not in fuera]


def _wf_colapsa(items):
    """Una sola tarjeta de WolfMax por titulo+calidad, la que trae CAPITULOS.

    El indice arrastra entradas degradadas (ver _wf_pobre) y la misma serie
    salia dos veces: la buena con sus capitulos y una vacia. La web las funde,
    pero entonces el contador de la barra deja de cuadrar con lo que se ve.
    """
    fuera, otros = {}, []
    orden = []
    for it in (items or []):
        if (it or {}).get("source") != "wf":
            otros.append(it)
            continue
        k = ((it.get("title") or "").strip().lower(), it.get("quality") or "")
        vieja = fuera.get(k)
        if vieja is None:
            fuera[k] = it
            orden.append(k)
            continue
        if len(it.get("eps") or []) > len(vieja.get("eps") or []):
            fuera[k] = it
    return otros + [fuera[k] for k in orden]


def _wf_pobre(url, titulo, kind=""):
    """True si esta entrada del indice perdio su "[Cap.301]" y por tanto no
    sirve para agrupar (ver cabecera del arreglo de los indices envenenados)."""
    u = (url or "").lower()
    t = titulo or ""
    if not _WF_EP_URL_RE.search(u):
        return False                     # peli o ficha de serie: normal
    if (kind or "").startswith("movie"):
        return False
    return not _WF_CAP_RE.search(t)


def _wfidx_learn(items):
    """Guarda en el indice lo que una caja acaba de traer de WolfMax."""
    if not items:
        return
    try:
        idx = _wfidx_load()
        n = 0
        for it in items:
            if (it or {}).get("source") != "wf":
                continue
            u = it.get("url") or it.get("content_id")
            t = it.get("title")
            if not u or not t or "wolfmax" not in u.lower():
                continue
            rec = {"t": t[:160], "k": ("tvshow" if it.get("kind") == "serie"
                                       else "movie"),
                   "q": (it.get("quality") or "")[:12]}
            # una entrada POBRE no pisa a una buena (ver _wf_pobre)
            _ant = idx.get(u)
            if _ant and _wf_pobre(u, rec["t"], rec["k"]) and \
                    not _wf_pobre(u, _ant.get("t"), _ant.get("k")):
                continue
            if _ant != rec:
                idx[u] = rec
                n += 1
        if n:
            _WFIDX_TS[0] = _t.time()
            _WFIDX_CAMBIOS[0] += n
            _wfidx_save()
            _wfidx_nube_sube()
    except Exception:
        pass


@app.post("/wffeed")
def wffeed():
    """Una caja empuja su indice local de WolfMax. Se fusiona con lo que haya."""
    body = request.get_json(silent=True) or {}
    ent = body.get("entries") or {}
    if not isinstance(ent, dict) or not ent:
        items = body.get("items") or []
        ent = {}
        for it in (items if isinstance(items, list) else [])[:_WFIDX_MAX]:
            u = (it or {}).get("url")
            if u:
                ent[u] = {"t": it.get("title") or "", "k": it.get("kind") or "",
                          "q": it.get("quality") or ""}
    if not ent:
        return jsonify({"ok": False}), 400
    idx = _wfidx_load()
    n = 0
    for u, e in list(ent.items())[:_WFIDX_MAX]:
        if not isinstance(u, str) or "wolfmax" not in u.lower():
            continue
        if not isinstance(e, dict):
            continue
        rec = {"t": (e.get("t") or e.get("title") or "")[:160],
               "k": (e.get("k") or e.get("kind") or "")[:16],
               "q": (e.get("q") or e.get("quality") or "")[:12]}
        if not rec["t"]:
            continue
        # lo que empuja una caja tampoco puede DEGRADAR lo que ya hay: sus
        # indices locales arrastran el dano viejo (ver cabecera del arreglo).
        _ant = idx.get(u)
        if _ant and _wf_pobre(u, rec["t"], rec["k"]) and \
                not _wf_pobre(u, _ant.get("t"), _ant.get("k")):
            continue
        if _ant != rec:
            idx[u] = rec
            n += 1
    if len(idx) > _WFIDX_MAX:      # poda simple: nos quedamos con las primeras
        for k in list(idx.keys())[_WFIDX_MAX:]:
            idx.pop(k, None)
    _WFIDX_TS[0] = _t.time()
    _wfidx_save()
    if n:
        _WFIDX_CAMBIOS[0] += n
        _wfidx_nube_sube()
    return jsonify({"ok": True, "n": len(idx), "nuevas": n})


@app.get("/catetbox")
def catetbox():
    """Busqueda/estrenos en fuentes que necesitan el box (EliteTorrent, DivxTotal,
    WolfMax). op=search|latest, srcs=csv (et,dx,wf)."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    q = (request.args.get("q") or "").strip()
    op = (request.args.get("op") or "search").strip()
    srcs = (request.args.get("srcs") or "et").strip()
    # CACHE (antes de tocar ninguna caja): repetir la misma busqueda es gratis e
    # INSTANTANEO. Es lo que hace que "buscar en todas las fuentes" no salga caro:
    # la 2a vez que alguien busca lo mismo, EliteTorrent/WolfMax ya estan ahi.
    ckey = "%s|%s|%s" % (op, ",".join(sorted(
        s for s in srcs.split(",") if s)), q.lower())
    _hit = _catbox_get(ckey)
    if _hit is not None:
        return jsonify({"items": _hit, "cached": True})
    # WOLFMAX AL INSTANTE: si el indice local del relay tiene el titulo, se
    # responde sin cola, sin caja y sin red (ver _wf_idx_search). Si el indice
    # esta vacio (deploy reciente) se le pide a una caja y se sigue por el
    # camino de siempre para no dejar al usuario sin nada.
    _idx_respaldo = []
    if op == "search" and q and srcs.replace(" ", "") == "wf":
        _idx = _wf_idx_search(q)
        # Las entradas degradadas se descartan SIEMPRE, no solo cuando lo son
        # todas: en cuanto hubo mezcla (las buenas que traen las cajas 2.9.64 y
        # las viejas) se colaban otra vez -> "silo" daba DIECIOCHO tarjetas sin
        # capitulos. Quedan de respaldo, UNA por titulo+calidad, por si la caja
        # tampoco contesta.
        if _idx:
            _malas = [it for it in _idx
                      if _wf_pobre(it.get("url") or it.get("content_id"),
                                   it.get("title"), it.get("kind"))]
            _idx = [it for it in _idx if it not in _malas]
            if _malas:
                _vistos, _uno = set(), []
                for it in _malas:
                    k = ((it.get("title") or "").lower().strip(),
                         it.get("quality") or _wf_quality_from_url(
                             it.get("url") or it.get("content_id")) or "")
                    if k in _vistos:
                        continue
                    _vistos.add(k)
                    _uno.append(it)
                _idx_respaldo = _uno
            if not _idx:
                _wfidx_ask_box()
        if _idx:
            _idx = _cat_group_episodes(_idx)
            _idx = [it for it in _idx if _q_relevant(it.get("title", ""), q)]
            for it in _idx:
                disp, ql = _cat_clean_quality(it.get("title", ""))
                it["title"] = disp
                if not it.get("quality"):
                    it["quality"] = ql or _wf_quality_from_url(it.get("url"))
            _idx = _wf_completa_partidas(_une_series_partidas(_wf_colapsa(_idx)))
            if _idx:
                _idx = _bounded(lambda: _cat_enrich(_idx, limit=40), 6.0,
                                default=_idx) or _idx
                _catbox_put(ckey, _idx)
                return jsonify({"items": _idx, "idx": True})
        else:
            _wfidx_ask_box()
    box = _box_for(code)     # la suya si esta viva; si no, cualquier caja viva
    if not box or (op == "search" and not q):
        return jsonify({"items": [], "off": True})
    # WolfMax es lento (catalogo->brave). Para et/dx, 20s: las cajas LENTAS (TV)
    # no terminaban en 10s -> el salon devolvia 0 ("solo DonTorrent"). _catjob_wait
    # devuelve en cuanto el box responde, asi que las cajas rapidas (PC) NO se
    # penalizan; solo da margen a las lentas. DonTorrent sale ya; el box rellena.
    wait = 24.0 if "wf" in srcs else 20.0
    # Caja PRESTADA: tope de concurrencia -> el hilo del relay se libera y no se
    # acumulan. Si no hay hueco se responde YA (lo mismo que se respondia antes de
    # existir el prestamo), sin colgar a nadie. El tope es global (protege los 8
    # hilos del relay) Y por caja (protege a cada Kodi) -> ver _lend_acquire.
    prestada = (box != code)
    _lsem = _lend_acquire(box) if prestada else None
    if prestada and _lsem is None:
        return jsonify({"items": [], "off": True})
    # NO se recorta la espera cuando la caja es prestada: el semaforo ya acota
    # los hilos ocupados, que era el problema. Recortarla a 14s ademas (dtbk37)
    # hacia inutil el prestamo: la caja elegida puede ser una TV lenta y
    # expiraba antes de que contestara (medido: 14,3s sin arrancar el trabajo).
    _wf = "wf" in srcs.split(",")
    if _wf and not _WF_SEM.acquire(blocking=False):
        if _lsem is not None:      # sin hueco para la lenta -> responder YA
            _lend_release(_lsem)   # (las otras fuentes no dependen de esta)
        return jsonify({"items": [], "off": True})
    try:
        def _ask(b):
            j = "et" + os.urandom(5).hex()
            _kb_enqueue(b, {"c": "etjob", "job": j, "op": op, "q": q,
                            "srcs": srcs})
            return j
        _jobs = [_ask(box)]
        # HEDGE (mismo patron que la busqueda de DonTorrent): si la caja no ha
        # contestado NADA a los 8s, se lo pedimos tambien a otra caja viva. Las
        # cajas no son iguales (una TV vieja tarda el triple que un PC) y con
        # varias encendidas no tiene sentido que la busqueda la marque la lenta.
        # Si contesto —aunque sea vacia— se acepta: no lo tiene y otra tampoco
        # (es la MISMA web), asi que ahi no se molesta a nadie mas.
        res = _catjob_wait_any(_jobs, min(8.0, wait))
        if res is None:
            b2 = next((b for b in _live_boxes() if b != box), None)
            if b2:
                _jobs.append(_ask(b2))
            if _jobs:
                res = _catjob_wait_any(_jobs, max(0.0, wait - 8.0))
    finally:
        if _lsem is not None:
            _lend_release(_lsem)
        if _wf:
            _WF_SEM.release()
    if res is None:
        # TIMEOUT: NO se cachea (puede ser un pico puntual; la proxima reintenta).
        # Si el indice tenia algo (aunque fuera pobre), mejor eso que nada.
        if _idx_respaldo:
            for it in _idx_respaldo:
                disp, ql = _cat_clean_quality(it.get("title", ""))
                it["title"] = disp
                if not it.get("quality"):
                    it["quality"] = ql or _wf_quality_from_url(
                        it.get("url") or it.get("content_id"))
            return jsonify({"items": _idx_respaldo, "idx": True})
        return jsonify({"items": [], "timeout": True})
    items = res.get("items") or []
    if request.args.get("raw") == "1":
        # DIAGNOSTICO: el dato TAL CUAL lo manda la caja, sin filtros ni enrich
        # (para ver como vienen las series de EliteTorrent/WolfMax y decidir si
        # se pueden agrupar en una tarjeta). No cachea: es una sonda manual.
        return jsonify({"items": items, "raw": True})
    # AL INDICE, EL DATO CRUDO. Debajo se agrupan los capitulos y se LIMPIA el
    # titulo ("Silo [4k 2160p][Cap.301]" -> "Silo"), y aprender eso envenenaba
    # el indice: las siguientes busquedas salian de ahi sin ningun "Cap." que
    # agrupar -> once fichas identicas "Silo" que la web funde en una (la barra
    # decia "WolfMax 11" y el dueno veia UNA tarjeta). El crudo es lo unico con
    # lo que se pueden agrupar los capitulos.
    _wf_crudo = [dict(it) for it in items if (it or {}).get("source") == "wf"]
    # ET/WF dan 1 tarjeta por CAPITULO -> se agrupan en una tarjeta de serie con
    # sus episodios dentro (antes se tiraban y esas fuentes no daban series).
    items = _cat_group_episodes(items)
    # relevancia ESTRICTA en busqueda (las fuentes-box traen sueltos/"ultimos")
    if op == "search" and q:
        items = [it for it in items if _q_relevant(it.get("title", ""), q)]
    for it in items:   # titulos de fuentes-box pueden venir verbosos (WolfMax)
        disp, ql = _cat_clean_quality(it.get("title", ""))
        it["title"] = disp
        if not it.get("quality"):
            it["quality"] = ql
        if not it.get("quality") and it.get("source") == "wf":
            it["quality"] = _wf_quality_from_url(
                it.get("url") or it.get("content_id"))
    if items:
        # TOPE DURO al enrich. `_cat_enrich` dispara hasta 60 consultas a TMDB
        # con ex.map y SIN limite de tiempo, y TMDB banea la IP de Render: la
        # peticion se quedaba colgada MINUTOS (medido: >120s con el relay por lo
        # demas sano, /ping en 200). Ademas cada una de esas se come un hilo de
        # los 8 -> es el camino recto a tumbar el relay entero. Si expira se
        # devuelven los items TAL CUAL: salen igual (con su caratula propia),
        # solo sin poster HD ni nota, que es infinitamente mejor que colgarse.
        items = _bounded(lambda: _cat_enrich(items, limit=60), 8.0,
                         default=items)
    # La caja CONTESTO (con o sin resultados) -> a la cache. El vacio tambien
    # (TTL corto): "WolfMax no tiene esta peli" es un dato estable y ahorra 24s
    # de espera la proxima vez que alguien la busque.
    items = _wf_completa_partidas(_une_series_partidas(_wf_colapsa(items)))
    _catbox_put(ckey, items)
    _wfidx_learn(_wf_crudo)   # el CRUDO (ver arriba): la proxima vez va en 10ms
    return jsonify({"items": items})


@app.get("/catetboxresolve")
def catetboxresolve():
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    url = (request.args.get("url") or "").strip()
    src = (request.args.get("src") or "et").strip()
    box = _box_for(code)     # ver _box_for: la suya, o cualquier caja viva
    if not box or not url.lower().startswith("http"):
        return jsonify({"link": ""}), 400
    job = "et" + os.urandom(5).hex()
    _kb_enqueue(box, {"c": "etjob", "job": job, "op": "resolve",
                      "src": src, "url": url})
    res = _catjob_wait(job, 18.0)
    return jsonify({"link": (res or {}).get("link", "") or ""})


@app.get("/catboxrar")
def catboxrar():
    """¿Un item de fuente-box (DivxTotal) viene en RAR? Lo decide el box."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    url = (request.args.get("url") or "").strip()
    src = (request.args.get("src") or "dx").strip()
    box = _box_for(code)     # ver _box_for: la suya, o cualquier caja viva
    if not box or not url.lower().startswith("http"):
        return jsonify({"rar": False}), 400
    job = "et" + os.urandom(5).hex()
    _kb_enqueue(box, {"c": "etjob", "job": job, "op": "rarcheck",
                      "src": src, "url": url})
    res = _catjob_wait(job, 16.0)
    return jsonify({"rar": bool((res or {}).get("rar")),
                    "quality": (res or {}).get("quality") or ""})


def _box_eps_by_title(code, src, title, wait=None, cache_only=False):
    """Capitulos de una serie de ET/WF buscandola POR TITULO en su fuente.
    Devuelve la lista de capitulos (ya agrupados) o [] si no sale.

    Por que hace falta: la tarjeta no siempre trae los capitulos dentro. WolfMax
    responde a veces desde su INDICE LOCAL con la ficha de la SERIE
    (/serie-online-4k/<id>, sin capitulos), y el addon no sabe sacar episodios de
    WolfMax. Buscar el titulo SI funciona (es lo que hace la busqueda) y la
    respuesta ya esta cacheada si el usuario acaba de buscar eso mismo."""
    q = (title or "").strip()
    if not q or src not in ("wf", "et"):
        return []
    ckey = "search|%s|%s" % (src, q.lower())
    items = _catbox_get(ckey)
    if items is not None and not any((it or {}).get("eps") for it in items):
        # cache de BUSQUEDA (p.ej. la del indice de WolfMax, que guarda titulos
        # pero no episodios): no sirve para esto -> se busca de verdad.
        items = None
    if items is None:
        if cache_only:     # solo mirar, sin tocar ninguna caja
            return []
        box = _box_for(code)
        if not box:
            return []
        prestada = (box != code)
        sem = _lend_acquire(box) if prestada else None
        if prestada and sem is None:
            return []
        wf = (src == "wf")
        if wf and not _WF_SEM.acquire(blocking=False):
            if sem is not None:
                _lend_release(sem)
            return []
        try:
            job = "et" + os.urandom(5).hex()
            _kb_enqueue(box, {"c": "etjob", "job": job, "op": "search",
                              "q": q, "srcs": src})
            res = _catjob_wait(job, wait or (24.0 if wf else 20.0))
        finally:
            if sem is not None:
                _lend_release(sem)
            if wf:
                _WF_SEM.release()
        if res is None:
            return []
        items = _cat_group_episodes(res.get("items") or [])
        for it in items:
            if not it.get("quality") and it.get("source") == "wf":
                it["quality"] = _wf_quality_from_url(
                    it.get("url") or it.get("content_id"))
        # MISMA cache que /catetbox (misma clave) -> hay que dejarlos igual de
        # enriquecidos, o abrir una serie antes de buscarla dejaria la busqueda
        # sin posters 10 min. Acotado: TMDB banea a Render (§9).
        if items:
            items = _bounded(lambda: _cat_enrich(items, limit=60), 8.0,
                             default=items) or items
        _catbox_put(ckey, items)
        _wfidx_learn(items)
    # la tarjeta que mejor case con el titulo pedido
    qn = _et_norm(q)
    mejor, mejor_n = None, -1
    for it in items:
        if not it.get("eps"):
            continue
        tn = _et_norm(it.get("title") or "")
        if tn == qn or tn in qn or qn in tn:
            if len(it["eps"]) > mejor_n:
                mejor, mejor_n = it, len(it["eps"])
    return (mejor or {}).get("eps") or []


@app.get("/catboxeps")
def catboxeps():
    """Episodios de una serie de una fuente-box (EliteTorrent/WolfMax) resueltos
    por el box, o de DivxTotal DIRECTO desde el relay (sin box, sin code: el
    relay alcanza DivxTotal igual que en la busqueda). Enriquece con TMDB."""
    url = (request.args.get("url") or "").strip()
    src = (request.args.get("src") or "dx").strip()
    if not url.lower().startswith("http"):
        return jsonify({"episodes": []}), 400
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    # DivxTotal: resolucion DIRECTA desde el relay (sin box) -> arregla "los
    # episodios no cargan" cuando el box esta apagado. Si DivxTotal banea la IP
    # de Render (directo vacio) Y hay box emparejado, cae al box (DoH de casa,
    # NO baneado) -> robusto pase lo que pase.
    box = _box_for(code)     # ver _box_for: la suya, o cualquier caja viva
    if src == "dx" and "divxtotal" in url.lower():
        try:
            payload = _dx_episodes_payload(url)
        except Exception:
            payload = {"episodes": []}
        if payload.get("episodes") or not box:
            return jsonify(payload)
        # directo sin episodios + hay caja viva -> resolver via caja (abajo)
    _t_ser = (request.args.get("t") or "").strip()[:120]
    _t0 = _t.time()
    _via = []

    def _por_titulo(cache_only=False):
        """PLAN B de ET/WF: buscar los capitulos por TITULO y agruparlos. Sirve
        para cajas con addon viejo (su op=episodes no sabe de WolfMax) y para
        cuando la ficha no da nada. Con cache_only NO toca ninguna caja: solo
        mira si ya lo tenemos (una busqueda reciente de esa serie lo deja ahi)."""
        if src not in ("wf", "et") or not _t_ser:
            return None
        _eps = _box_eps_by_title(code, src, _t_ser, wait=12.0,
                                 cache_only=cache_only)
        _via.append(("titulo-cache" if cache_only else "titulo") +
                    ":%d/%.1fs" % (len(_eps), _t.time() - _t0))
        if not _eps:
            return None
        _meta = (_bounded(lambda: _cat_tmdb(_t_ser, "tv"), 2.5, {}) or {})
        return jsonify({"title": _t_ser, "poster": _meta.get("poster"),
                        "year": _meta.get("year"),
                        "rating": _meta.get("rating"),
                        "episodes": _eps, "via": ">".join(_via)})
    # full=1: NO sirvas lo que dejo la busqueda (el indice suele tener menos
    # capitulos); ve a la caja, que desde el addon 2.9.65 lee las paginas de la
    # serie y trae todas las temporadas. Lo usa la web para completar la ficha
    # por detras, cuando ya ha pintado lo rapido.
    _full = request.args.get("full") == "1"
    if not _full:
        _ya = _por_titulo(cache_only=True)
        if _ya is not None:
            return _ya
    if not box:
        return _por_titulo() or (jsonify({"episodes": []}), 400)
    # Mismo tope que en /catetbox cuando la caja es prestada (ver _lend_acquire).
    prestada = (box != code)
    _lsem = _lend_acquire(box) if prestada else None
    if prestada and _lsem is None:
        return _por_titulo() or jsonify({"episodes": []})
    try:
        def _pide(b):
            j = "et" + os.urandom(5).hex()
            _kb_enqueue(b, {"c": "etjob", "job": j, "op": "episodes",
                            "src": src, "url": url})
            return j
        _okeps = lambda r: bool(((r or {}).get("eps") or {}).get("episodes"))
        _jobs = [_pide(box)]
        if _t_ser and src in ("wf", "et"):
            # 1a caja: 5s. Si no trae capitulos, se le pregunta a DOS mas a la
            # vez (cada caja tiene su propio catalogo local de WolfMax y su
            # propia suerte con el ISP; el que traiga algo, gana).
            res = _catjob_wait_any(_jobs, 5.0, _okeps)
            _via.append("caja1:%s/%.1fs" % (
                len(((res or {}).get("eps") or {}).get("episodes") or []),
                _t.time() - _t0))
            if not _okeps(res):
                for _b2 in [b for b in _live_boxes() if b != box][:2]:
                    _jobs.append(_pide(_b2))
                if _jobs:
                    r2 = _catjob_wait_any(_jobs, 7.0, _okeps)
                    if _okeps(r2):
                        res = r2
                    _via.append("caja2:%s/%.1fs" % (
                        len(((r2 or {}).get("eps") or {}).get("episodes") or []),
                        _t.time() - _t0))
        else:
            res = _catjob_wait_any(_jobs, 22.0, _okeps)
            _via.append("caja:%s/%.1fs" % (
                len(((res or {}).get("eps") or {}).get("episodes") or []),
                _t.time() - _t0))
    finally:
        if _lsem is not None:
            _lend_release(_lsem)
    if res is None:
        return _por_titulo() or jsonify({"episodes": [], "timeout": True,
                                         "via": ">".join(_via)})
    eps = res.get("eps") or {}
    if not (eps.get("episodes") or []):
        # la caja no supo (addon viejo con WolfMax, o ficha sin capitulos)
        _pb = _por_titulo()
        if _pb is not None:
            return _pb
    title = _cat_clean_quality(eps.get("title") or "")[0]
    # TOPE al enrich: TMDB banea a Render y una llamada colgada se come uno
    # de los 8 hilos (ver §9). Sin poster salen igual; colgados, no.
    meta = (_bounded(lambda: _cat_tmdb(title, "tv"), 2.5, {}) or {}) if title else {}
    _via.append("fin:%.1fs" % (_t.time() - _t0))
    return jsonify({"via": ">".join(_via),
                    "title": title or "Serie", "poster": meta.get("poster"),
                    "year": meta.get("year"), "rating": meta.get("rating"),
                    "episodes": eps.get("episodes") or []})


@app.post("/catjob/done")
def catjob_done():
    """El box deja aqui el resultado de un etjob (busqueda o resolucion)."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    job = (str(body.get("job") or ""))[:40]
    if not job:
        return jsonify({"ok": False}), 400
    now = _t.time()
    with _FileLock(_CATJOB_FILE):   # serializa con _catjob_wait (no perder el resultado)
        d = _catjob_load()
        d = {k: v for k, v in d.items() if (now - v.get("ts", 0)) < _CATJOB_TTL}
        d[job] = {"items": body.get("items"), "link": body.get("link"),
                  "rar": body.get("rar"), "quality": body.get("quality"),
                  "eps": body.get("eps"), "ih": body.get("ih"),
                  "html": body.get("html"), "ts": now}
        _catjob_save(d)
    # AUNQUE YA NO LO ESPERE NADIE: al indice de WolfMax. Un titulo que la caja
    # no tenia indexado le cuesta ~88s de crawl (medido con "the last of us");
    # el relay se rinde a los 24s y esto se quedaba aqui muerto hasta caducar,
    # asi que la siguiente busqueda volvia a pagar los 88s. Aprendiendolo, la
    # web lo pide otra vez a los 35s (ver el reintento de WolfMax) y sale.
    try:
        _wfidx_learn(body.get("items"))
    except Exception:
        pass
    return jsonify({"ok": True})


# ===========================================================================
# SEMILLAS universales (cualquier fuente). Por info_hash/magnet directo (rapido,
# scrape UDP desde Render) o por code+src+url/link (el box deriva el info_hash
# desde su IP residencial: extrae del magnet o descarga el .torrent). Cacheado.
# ===========================================================================
_SEEDS_FILE = "/tmp/mw_seeds.json"
_SEEDS_TTL2 = 2700   # 45 min


def _seeds_load():
    try:
        with open(_SEEDS_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _seeds_save(d):
    try:
        tmp = _SEEDS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _SEEDS_FILE)
    except Exception:
        pass


# Cache url -> info_hash para DivxTotal: derivar el hash de un item DX cuesta 2
# descargas (la ficha + el .torrent). El .torrent es estatico, asi que mapeamos
# la url de la ficha a su hash UNA vez -> la 2a apertura salta las descargas y va
# directa a la cache de seeders. No toca DonTorrent/TMDB -> sin riesgo de baneo.
_DXIH_FILE = "/tmp/mw_dxih.json"
_DXIH_TTL = 7 * 86400   # 7 dias


def _dxih_load():
    try:
        with open(_DXIH_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _dxih_save(d):
    try:
        tmp = _DXIH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _DXIH_FILE)
    except Exception:
        pass


def _ih_from_magnet(s):
    m = re.search(r"btih:([a-fA-F0-9]{40}|[A-Za-z2-7]{32})", s or "")
    if not m:
        return ""
    h = m.group(1)
    if len(h) == 32:
        try:
            import base64
            h = base64.b32decode(h.upper()).hex()
        except Exception:
            return ""
    return h.lower()


def _ih_from_link(link):
    """info_hash hex desde un magnet (extrae) o un .torrent http (lo baja en el
    relay -el CDN de dx/wf/DonTorrent SI es alcanzable desde Render- y calcula el
    hash con _dt_infohash). '' si no se puede."""
    if not link:
        return ""
    if link.startswith("magnet:"):
        return _ih_from_magnet(link)
    if link.startswith("http") and host_allowed(link):
        try:
            rr = requests.get(link, headers=BROWSER_HEADERS, timeout=20,
                              allow_redirects=True)
            if rr.status_code == 200 and rr.content[:1] == b"d":
                dg = _dt_infohash(rr.content)
                if dg:
                    return dg.hex()
        except Exception:
            pass
    return ""


@app.get("/seeds")
def seeds_ep():
    ih = re.sub(r"[^a-f0-9]", "", request.args.get("ih", "").lower())[:40]
    link = request.args.get("link", "") or request.args.get("magnet", "")
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    src = re.sub(r"[^a-z]", "", request.args.get("src", "").lower())[:4]
    url = request.args.get("url", "")
    now = _t.time()
    d = _seeds_load()
    # 1) link directo (magnet o .torrent) -> el relay deriva el hash (sin box).
    if len(ih) != 40 and link:
        ih = _ih_from_link(link)
    # 1b) DivxTotal: el relay alcanza la ficha y el .torrent -> deriva el hash
    # DIRECTO, sin box ni code (asi las semillas salen tambien en DivxTotal).
    # Cacheamos url->ih: la 2a apertura salta las 2 descargas (ficha + .torrent).
    if len(ih) != 40 and src == "dx" and "divxtotal" in (url or "").lower():
        dih = _dxih_load()
        c = dih.get(url)
        if c and len(c.get("ih", "")) == 40 and (now - c.get("ts", 0) < _DXIH_TTL):
            ih = c["ih"]
        else:
            try:
                dls = _dx_detail(url).get("downloads") or []
                if dls:
                    ih = _ih_from_link(dls[0].get("torrent_url") or "")
                if len(ih) == 40:
                    dih[url] = {"ih": ih, "ts": now}
                    if len(dih) > 2000:
                        for k in sorted(dih, key=lambda k: dih[k].get("ts", 0))[
                                :len(dih) - 2000]:
                            dih.pop(k, None)
                    _dxih_save(dih)
            except Exception:
                pass
    # 2) solo src+url (ficha): una caja RESUELVE el link y el relay deriva el
    # hash. Vale CUALQUIER caja viva (_box_for), no solo la del visitante: las
    # semillas deben salir en TODAS las fuentes, tambien para quien no tiene
    # codigo puesto — que es justo quien ve EliteTorrent/WolfMax desde que la
    # busqueda los pide siempre. Se cachea url->infohash (7 dias, mismo almacen
    # que DivxTotal): la 2a vez que alguien abre esa ficha, gratis y sin caja.
    if len(ih) != 40 and src and url:
        _dih = _dxih_load()
        _c = _dih.get(url)
        if _c and len(_c.get("ih", "")) == 40 and (now - _c.get("ts", 0) < _DXIH_TTL):
            ih = _c["ih"]
        else:
            _sbox = _box_for(code)
            _ssem = _lend_acquire(_sbox) if (_sbox and _sbox != code) else None
            if _sbox and not (_sbox != code and _ssem is None):
                try:
                    job = "ih" + os.urandom(5).hex()
                    _kb_enqueue(_sbox, {"c": "etjob", "job": job,
                                        "op": "infohash", "src": src,
                                        "url": url})
                    res = _catjob_wait(job, 20.0)
                finally:
                    if _ssem is not None:
                        _lend_release(_ssem)
                ih = _ih_from_link((res or {}).get("link") or "")
                if len(ih) == 40:
                    _dih[url] = {"ih": ih, "ts": now}
                    if len(_dih) > 2000:
                        for k in sorted(_dih, key=lambda k: _dih[k].get("ts", 0))[
                                :len(_dih) - 2000]:
                            _dih.pop(k, None)
                    _dxih_save(_dih)
    if len(ih) != 40:
        return jsonify({"seeds": None})
    ent = d.get(ih)
    if ent and (now - ent.get("ts", 0) < _SEEDS_TTL2):
        return jsonify({"seeds": ent.get("s"), "cached": True})
    sc = _dt_seed_count(bytes.fromhex(ih))
    s = sc if sc >= 0 else None
    if s is not None:
        d[ih] = {"s": s, "ts": now}
        if len(d) > 4000:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[:len(d) - 4000]:
                d.pop(k, None)
        _seeds_save(d)
    return jsonify({"seeds": s})


_CAT_BROWSE = {"estrenos": "/", "peliculas": "/peliculas", "series": "/series"}
_CATBROWSE_CACHE = {}      # "kind:page" -> {"items": [...], "ts": ...}
_CATBROWSE_TTL = 900       # 15 min: los listados cambian despacio
_CATBROWSE_DX_TTL = 300    # 5 min para el fallback DivxTotal -> reintenta DT pronto
_CATBROWSE_FILE = "/tmp/mw_catbrowse.json"   # persiste entre workers y deploys
_CATFEED_LAST = {}         # kind -> ts del ultimo empuje del box (diagnostico)

# Cache de BUSQUEDA. Una busqueda repetida o refinada (el usuario escribe, borra,
# reintenta, o vuelve atras) re-escrapeaba DonTorrent cada vez (~8s + suma riesgo
# de BANEO de la IP de Render = el dolor historico nº1). Con cache: el 2o hit es
# INSTANTANEO y NO toca DonTorrent/TMDB. TTL corto: un torrent nuevo no aparece
# minuto a minuto, 10 min es seguro. COMPARTIDA via disco (/tmp) como /catbrowse:
# con gthread hay 2 WORKERS (procesos) y la cache solo-memoria fallaba ~50% de las
# repeticiones (cada peticion puede caer en otro worker con su memoria vacia).
_CATSEARCH_CACHE = {}      # q.lower() -> {"items": [...], "ts": ...}
_CATSEARCH_TTL = 600       # 10 min
_CATSEARCH_MAX = 80        # tope de entradas -> no crece sin limite (Render 512MB)
_CATSEARCH_FILE = "/tmp/mw_catsearch.json"   # compartida entre workers
# Single-flight: query EN CURSO -> Event. Las peticiones identicas concurrentes
# (reintentos del front, varias pestañas) esperan a ESA en vez de abrir otro
# fan-out -> el relay no se satura. Por-worker (basta: los reintentos del mismo
# usuario caen casi siempre en el mismo worker; si no, paga 1 fan-out de mas, no se cuelga).
_CATSEARCH_INFLIGHT = {}
_CATSEARCH_INFLIGHT_LOCK = _thr.Lock()


def _catbrowse_load():
    try:
        with open(_CATBROWSE_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _catbrowse_save(d):
    try:
        tmp = _CATBROWSE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _CATBROWSE_FILE)
    except Exception:
        pass


# SEMILLA del catalogo (DonTorrent REAL) versionada en el repo. Es el "suelo"
# garantizado del Inicio: sobrevive a deploys, a /tmp borrado y a que el box este
# apagado -> el Inicio arranca SIEMPRE con DonTorrent (no con DivxTotal). Se
# refresca en vivo cuando el box empuja /catfeed o cuando Render alcanza DT; la
# semilla solo es el respaldo de ultimo-conocido. Cacheada en memoria (no cambia
# en runtime). Se regenera con GET /catdump tras un /catfeed bueno -> commit.
_CATBROWSE_SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "catalog_seed.json")
_CATBROWSE_SEED_CACHE = [None]


def _catbrowse_seed():
    if _CATBROWSE_SEED_CACHE[0] is None:
        try:
            with open(_CATBROWSE_SEED_FILE, "r", encoding="utf-8") as f:
                _CATBROWSE_SEED_CACHE[0] = _json.load(f) or {}
        except Exception:
            _CATBROWSE_SEED_CACHE[0] = {}
    return _CATBROWSE_SEED_CACHE[0]


_SEED_META_INDEX = [None]


def _seed_meta_index():
    """Indice content_id -> {poster TMDB, year, rating} extraido de la semilla.
    Es una cache TMDB PERSISTENTE: cuando TMDB banea la IP de Render, el enrich
    reusa estos datos por content_id en vez de degradar el Inicio a la caratula
    no-HD. Solo guarda entradas con poster de TMDB (no thumbs de DonTorrent)."""
    if _SEED_META_INDEX[0] is None:
        idx = {}
        try:
            for ent in (_catbrowse_seed() or {}).values():
                for it in (ent or {}).get("items", []):
                    cid = it.get("content_id")
                    pos = it.get("poster") or ""
                    if cid and "image.tmdb.org" in pos:
                        idx[cid] = {"poster": pos, "year": it.get("year"),
                                    "rating": it.get("rating")}
        except Exception:
            pass
        _SEED_META_INDEX[0] = idx
    return _SEED_META_INDEX[0]


# Cache de ENRICH por content_id CONSTRUIDA desde lo que el BOX enriquece (su IP
# residencial SI alcanza TMDB; la de Render esta baneada). El box recibe `pending`
# en la respuesta de /catfeed, enriquece esos titulos y empuja /catenrich con
# {content_id: meta}. Aqui se acumula -> los siguientes /catfeed rellenan poster HD
# + nota + año al INSTANTE por content_id (sin tocar TMDB desde Render), aunque el
# box tarde en re-enriquecer. Persistente en /tmp (compartida entre workers); se
# pierde en deploy (la SEMILLA del repo es el suelo que sobrevive) -> promovible a
# la semilla con /catdump. Solo memoriza entradas con poster de TMDB.
_CAT_ENRICH_FILE = "/tmp/mw_cat_enrich.json"
_CAT_ENRICH_MAX = 4000     # tope de content_ids (Render 512MB)
_CAT_ENRICH_KEYS = ("poster", "year", "rating", "overview",
                    "backdrop", "genres", "tmdb_id",
                    # "title": el titulo OFICIAL que manda el box (addon 2.9.57+).
                    # Sin persistirlo aqui se perdia al guardar y las tildes no
                    # sobrevivian al siguiente /catfeed.
                    "title",
                    # addon 2.9.58+: leidos de la FICHA de DonTorrent (la fuente
                    # ORIGINAL). "dtok" marca que ese meta ya se resolvio contra
                    # la ficha —titulo real y, si habia homonimos, desempatados
                    # por director— asi que MANDA sobre cualquier match que el
                    # relay saque por su cuenta de TMDB, y no hay que re-pedirlo.
                    "dt_title", "dt_year", "dtok")


def _cat_enrich_load():
    try:
        with open(_CAT_ENRICH_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _cat_enrich_store(meta):
    """Mezcla {content_id: meta} (solo entradas con poster TMDB) en la cache de
    disco, bajo lock entre procesos. Devuelve cuantas entradas se guardaron."""
    n = 0
    with _FileLock(_CAT_ENRICH_FILE):
        d = _cat_enrich_load()
        for cid, m in (meta or {}).items():
            if not isinstance(m, dict):
                continue
            if "image.tmdb.org" not in (m.get("poster") or ""):
                continue
            # NO DEGRADAR: si lo guardado ya se resolvio contra la ficha de
            # DonTorrent (`dtok`) y esto no, viene de un box ANTIGUO que solo
            # sabe preguntar a TMDB por el titulo -> es justo el match a ciegas
            # que pone el cartel del homonimo mas famoso. Con 7 cajas en el
            # sistema y actualizaciones escalonadas, sin esto una caja vieja
            # desharia en su siguiente vuelta lo que arreglo una nueva.
            if (d.get(str(cid)) or {}).get("dtok") and not m.get("dtok"):
                continue
            d[str(cid)] = {k: m[k] for k in _CAT_ENRICH_KEYS if m.get(k) is not None}
            n += 1
        if len(d) > _CAT_ENRICH_MAX:   # no crecer sin limite
            for k in list(d.keys())[:-_CAT_ENRICH_MAX]:
                d.pop(k, None)
        try:
            tmp = _CAT_ENRICH_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(d, f)
            os.replace(tmp, _CAT_ENRICH_FILE)
        except Exception:
            pass
    return n


def _dt_title_fix(cur, dt_title, serie=False):
    """Titulo del listado (`cur`, mutilado por el slug de DonTorrent) corregido con
    el que publica la FICHA de DonTorrent (`dt_title`, con tildes). Devuelve None
    si no corresponden -> nunca se sustituye una peli por otra.

    SOLO restaura lo que el slug se come (acentos y puntuacion). NUNCA reescribe
    el formato del titulo: las fichas de SERIE titulan 'Sandokan - 1ª Temporada
    [1080p]' mientras el listado dice 'Sandokan 1 Temporada', y adoptar eso
    cambiaria el nombre de TODAS las series de golpe — con el titulo de clave
    que es (dedup de la busqueda, 'Mi lista' y capitulos vistos del movil), eso
    es justo lo que no se debe tocar. En series se sustituye unicamente el
    PREFIJO (lo anterior al ' - '), que es donde estan los acentos."""
    cur = (cur or "").strip()
    cand = _re_dt.sub(r"\s+", " ", (dt_title or "")).strip()
    if not cur or not cand or cand.lower() == cur.lower():
        return None          # igual salvo mayusculas -> no hay nada que ganar

    def _loose(s):
        # Mismo destrozo que hace el slug de DonTorrent (vocal acentuada FUERA)
        # y ademas sin la puntuacion que el slug tampoco conserva.
        return _re_dt.sub(r"[^a-z0-9]+", "", _dt_mutila(s).lower())

    if serie:
        base = cand.split(" - ")[0].strip()
        if not base or _dt_mutila(base) == base:
            return None                          # sin acentos que restaurar
        mb = _dt_mutila(base)                    # el prefijo TAL COMO llega hoy
        if _loose(cur[:len(mb)]) == _loose(mb):
            return base + cur[len(mb):]
        return None
    if _loose(cand) == _loose(cur):
        return cand
    return None


def _cat_apply_meta(it, sm):
    """Aplica un meta TMDB (de la semilla o de la cache de enrich del box) a un
    item del Inicio. El meta va POR content_id (es la identidad de ESTE item),
    asi que sus campos MANDAN: antes solo se rellenaban los huecos y, si el item
    traia una ficha EQUIVOCADA de un match malo anterior (caso real: 'La
    residencia' con sinopsis/generos/backdrop/duracion de 'Asterix y la
    residencia de los dioses'), se corregia el poster pero la ficha ajena se
    quedaba PEGADA. Ahora se sobreescribe todo, y lo que el meta no traiga se
    QUITA: mejor sin dato que con el de otra peli. Devuelve True si puso un
    poster de TMDB."""
    if not sm or "image.tmdb.org" not in (sm.get("poster") or ""):
        return False
    it["poster"] = sm["poster"]
    # TILDES: DonTorrent no publica el titulo completo en sus listados (cada
    # ficha es un <a> con la imagen, sin title ni alt, y su propio slug viene
    # sin la vocal acentuada) -> el Inicio salia con 'Obsesin', 'Admisin
    # imposible', 'Cmplices hasta el final'. El titulo OFICIAL lo trae el BOX en
    # este meta (el relay no puede pedirlo: TMDB le banea la IP). Se aplica SOLO
    # si es el MISMO titulo salvo acentos (_dt_mutila) -> nunca sustituye una
    # peli por otra ni traduce al ingles (norma §0: espejo de la web original).
    _tt = (sm.get("title") or "").strip()
    _cur = (it.get("title") or "").strip()
    if _tt and _cur and _tt != _cur and _dt_mutila(_tt).lower() == _cur.lower():
        it["title"] = _tt
    # Mejor aun: el titulo de la FICHA de DonTorrent (addon 2.9.58+). Ese no hay
    # que validarlo contra TMDB porque viene de la web ORIGINAL, y cubre lo que
    # la via TMDB no podia: los titulos con PUNTUACION que el slug se come
    # ('Una Milla: Capítulo Uno' llegaba como 'Una Milla Captulo Uno' y la
    # comparacion letra a letra fallaba por los dos puntos).
    _fx = _dt_title_fix(it.get("title"), sm.get("dt_title"),
                        (it.get("kind") == "serie"))
    if _fx:
        it["title"] = _fx
    if sm.get("year"):
        it["year"] = sm["year"]
    if sm.get("rating") is not None:
        it["rating"] = sm["rating"]
    for k in ("overview", "backdrop", "genres", "tmdb_id"):
        if sm.get(k) is not None:
            it[k] = sm[k]
        else:
            it.pop(k, None)
    return True


# --- Cache de las fuentes que van POR CAJA (/catetbox) ----------------------
# Mismo motivo que _CATSEARCH_CACHE pero para EliteTorrent/WolfMax/DivxTotal-por
# -caja: cada peticion ocupa un hilo del relay ESPERANDO a un Kodi de casa (hasta
# 24s con WolfMax) y encima le da trabajo a la caja de otro. Repetir la misma
# busqueda (volver atras, otro amigo buscando lo mismo, un reintento) no puede
# volver a pagar eso. Compartida por disco: hay 2 workers.
_CATBOX_CACHE = {}
_CATBOX_TTL = 600          # 10 min con resultados
_CATBOX_TTL_EMPTY = 300    # 5 min si la caja contesto "no lo tengo" (es estable)
# ...salvo en WolfMax, donde el vacio NO es estable: la caja contesta [] cuando
# su crawl del catalogo no llega a tiempo (~88s en un titulo no indexado) y la
# serie SI esta en la web. Con 300s la joya de la casa quedaba tapada 5 minutos.
_CATBOX_TTL_EMPTY_WF = 60
_CATBOX_MAX = 120
_CATBOX_FILE = "/tmp/mw_catbox.json"


def _catbox_load():
    try:
        with open(_CATBOX_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _catbox_save(d):
    try:
        tmp = _CATBOX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _CATBOX_FILE)
    except Exception:
        pass


def _catbox_get(key):
    ent = _CATBOX_CACHE.get(key)
    if not ent:
        ent = _catbox_load().get(key)
        if ent:
            _CATBOX_CACHE[key] = ent
    if ent and (_t.time() - ent.get("ts", 0)) < ent.get("ttl", _CATBOX_TTL):
        return ent.get("items") or []
    return None


def _catbox_put(key, items):
    # la clave es "op|fuentes|consulta" (ver ckey en /catetbox)
    _srcs = (key.split("|") + ["", ""])[1].split(",")
    _empty_ttl = (_CATBOX_TTL_EMPTY_WF if "wf" in _srcs
                  else _CATBOX_TTL_EMPTY)
    rec = {"items": items, "ts": _t.time(),
           "ttl": _CATBOX_TTL if items else _empty_ttl}
    _CATBOX_CACHE[key] = rec
    try:
        disk = _catbox_load()
        disk[key] = rec
        if len(disk) > _CATBOX_MAX:
            for k in sorted(disk, key=lambda k: disk[k].get("ts", 0))[
                    :len(disk) - _CATBOX_MAX]:
                disk.pop(k, None)
        _catbox_save(disk)
    except Exception:
        pass
    if len(_CATBOX_CACHE) > _CATBOX_MAX:
        try:
            old = min(_CATBOX_CACHE, key=lambda k: _CATBOX_CACHE[k]["ts"])
            _CATBOX_CACHE.pop(old, None)
        except Exception:
            _CATBOX_CACHE.clear()


def _catsearch_load():
    try:
        with open(_CATSEARCH_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _catsearch_save(d):
    try:
        tmp = _CATSEARCH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _CATSEARCH_FILE)
    except Exception:
        pass


# === LISTA DE DESEADOS sincronizada (sin tocar fuentes -> CERO baneo) ========
# La lista vivia SOLO en localStorage del movil: si limpias el navegador,
# reinstalas la app o cambias de movil, se PERDIA. Aqui se guarda una copia
# ligada al codigo del box -> sobrevive y se puede ver desde otro dispositivo.
# El movil sigue siendo la copia MAESTRA (localStorage) y esto es espejo
# best-effort: la web hace UNION al cargar (NUNCA borra items -> imposible perder
# la lista por un fallo de sync). Solo guarda metadatos (titulo/poster/ids), nada
# de fuentes -> no puede contribuir a ningun baneo. /tmp se borra en deploy, pero
# el movil vuelve a subir su lista al cargar -> la copia se restaura sola.
_MYLIST_FILE = "/tmp/mw_mylist.json"
_MYLIST_MAX_ITEMS = 600      # tope por lista
_MYLIST_MAX_CODES = 50       # tope de codigos guardados
_MYLIST_LOCK = _thr.Lock()


def _mylist_load():
    try:
        with open(_MYLIST_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


@app.get("/mylist")
def mylist_get():
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"list": [], "ts": 0})
    ent = _mylist_load().get(code) or {}
    # `listas` = los nombres de las listas propias del usuario (Mis listas). Va
    # por el mismo canal que la lista de siempre para que el movil y la tablet
    # vean los mismos nombres; si un cliente viejo no lo manda, se conserva.
    return jsonify({"list": ent.get("list", []), "ts": ent.get("ts", 0),
                    "listas": ent.get("listas", [])})


@app.post("/mylist")
def mylist_post():
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"ok": False}), 400
    body = request.get_json(silent=True) or {}
    lst = body.get("list")
    if not isinstance(lst, list):
        return jsonify({"ok": False}), 400
    lst = lst[:_MYLIST_MAX_ITEMS]
    listas = body.get("listas")
    if not isinstance(listas, list):
        listas = None                      # cliente viejo: no tocar lo guardado
    with _MYLIST_LOCK:
        d = _mylist_load()
        _ant = d.get(code) or {}
        d[code] = {"list": lst, "ts": _t.time(),
                   "listas": (listas[:40] if listas is not None
                              else _ant.get("listas", []))}
        if len(d) > _MYLIST_MAX_CODES:   # no crecer sin limite
            for k in list(d.keys())[:-_MYLIST_MAX_CODES]:
                d.pop(k, None)
        try:
            tmp = _MYLIST_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(d, f)
            os.replace(tmp, _MYLIST_FILE)
        except Exception:
            pass
    return jsonify({"ok": True, "n": len(lst)})


def _bounded(fn, secs, default=None):
    """Ejecuta fn() con TOPE DURO de `secs` (hilo daemon). Si se cuelga (conexion
    que ignora el timeout de requests por bloqueo de IP de Render), devuelve
    `default` y abandona el hilo -> la peticion NUNCA se cuelga."""
    import threading
    box = {"v": default}

    def _w():
        try:
            box["v"] = fn()
        except Exception:
            box["v"] = default
    th = threading.Thread(target=_w, daemon=True)
    th.start()
    th.join(secs)
    return box["v"]


@app.post("/catfeed")
def catfeed():
    """El box PRE-CARGA los listados de DonTorrent (que Render no alcanza por el
    baneo de IP) desde su IP residencial y los empuja aqui; los parseamos +
    cacheamos -> catbrowse los sirve al INSTANTE. Inicio funciona y rapido aunque
    Render este baneado, sin esperar a ningun on-demand."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    kind = (body.get("kind") or "").strip().lower()
    html = body.get("html") or ""
    # El box 2.9.58+ sabe leer la FICHA de DonTorrent (titulo real + director) y
    # lo anuncia aqui. Cambia a quien se le pide enrich (ver mas abajo): a un box
    # que NO sabe no tiene sentido pedirle lo que no puede resolver.
    box_ficha = bool(body.get("ficha"))
    if kind not in _CAT_BROWSE or len(html) < 500:
        return jsonify({"ok": False}), 400
    try:
        raw = _cat_parse_items(html)
    except Exception:
        raw = []
    if not raw:
        return jsonify({"ok": False, "items": 0})
    # Cachea YA con la caratula PROPIA de DonTorrent (poster=thumb) -> catbrowse
    # sirve al INSTANTE en cuanto el box empuja. El enrich TMDB (en frio ~40s, si
    # TMDB ralentiza la IP de Render) va en 2o plano y MEJORA posters/notas SIN
    # bloquear el POST del box (antes el POST esperaba el enrich -> ReadTimeout
    # -> 0/3 -> Inicio vacio). Asi el POST responde en ~1s y nunca falla por eso.
    key = "%s:1" % kind
    # Rellena con el TMDB de la SEMILLA por content_id (caratula HD + nota + año)
    # de forma SINCRONA: el box empuja HTML crudo y Render no puede enriquecer
    # (TMDB le banea la IP) -> sin esto el Inicio se DEGRADABA a la caratula propia
    # no-HD. No depende del hilo de fondo (que en Render free puede no completar).
    seed_idx = _seed_meta_index()
    enr_idx = _cat_enrich_load()    # cache TMDB por content_id construida por el BOX
    pending = []                    # items sin poster TMDB -> el box los enriquece
    for it in raw:
        cid = it.get("content_id")
        sm = seed_idx.get(cid) or enr_idx.get(str(cid))
        _ok = _cat_apply_meta(it, sm)
        # Se pide enrich al box si falta el POSTER o si el meta guardado aun no
        # esta resuelto contra la FICHA de DonTorrent. Sin lo segundo nada de lo
        # que solo publica la ficha llegaria jamas: los metas viejos (cache
        # acumulada) ya tenian poster, asi que NINGUN item entraba en `pending`
        # y el box no enriquecia nada. Es autolimitado —en cuanto el meta trae
        # la marca deja de pedirse— y la marca depende de LO QUE SEPA el box:
        # a uno antiguo se le sigue pidiendo solo hasta que manda `title`, que
        # es todo lo que puede dar (si no, se le pediria en cada vuelta).
        _done = ((sm or {}).get("dtok")
                 or (not box_ficha and (sm or {}).get("title")))
        if (not _ok) or not _done:
            if not it.get("poster"):
                it["poster"] = it.get("thumb")
            if len(pending) < 80:    # candidatos a enrich por el box (TMDB no baneado)
                p = {"cid": cid, "title": it.get("title"),
                     "kind": it.get("kind")}
                if it.get("dtpath") or it.get("path"):
                    # Ruta de la ficha CON SLUG (DonTorrent da 404 por id pelado).
                    p["dtpath"] = it.get("dtpath") or it.get("path")
                pending.append(p)
    rec = {"items": raw, "ts": _t.time()}
    _CATBROWSE_CACHE[key] = rec
    _CATFEED_LAST[kind] = _t.time()
    try:
        disk = _catbrowse_load()
        disk[key] = rec
        _catbrowse_save(disk)
    except Exception:
        pass

    def _bg_enrich(_html=html, _key=key, _wait=(45.0 if box_ficha else 0.0)):
        try:
            # Si el box sabe leer la FICHA, se le DEJA LLEGAR PRIMERO: tarda unos
            # 25 s (ficha + TMDB desde su IP) y trae el match bueno. Sin esta
            # espera los dos caminos corrian a la vez y, cuando ganaba este, el
            # Inicio se quedaba varios minutos con el cartel del homonimo famoso
            # hasta el siguiente /catfeed. Con la espera, cuando este hilo
            # arranca los metas del box ya estan puestos y _cat_enrich ni toca
            # TMDB para ellos. Se mantiene como PLAN B (§9: ninguna funcion de
            # red con un solo camino) por si el box no logra enriquecer.
            if _wait:
                _t.sleep(_wait)
            # Va en 2o plano (no bloquea el POST del box), pero igualmente
            # acotado: con TMDB baneando a Render este hilo se eternizaba
            # consumiendo CPU/red del proceso. 40s y a otra cosa.
            _its = _cat_parse_items(_html)
            en = _bounded(lambda: _cat_enrich(_its), 40.0, default=None)
            if en:
                # No DEGRADAR: mientras este hilo corria (~40s con TMDB baneado),
                # el box pudo empujar /catenrich con posters HD. _cat_enrich solo
                # mira la semilla, asi que sin esto su resultado PISABA esos
                # posters al escribir la cache -> el Inicio parpadeaba a no-HD.
                enr = _cat_enrich_load()
                for it in en:
                    _m = enr.get(str(it.get("content_id"))) or {}
                    # El meta con `dtok` esta resuelto contra la FICHA de
                    # DonTorrent (titulo real y homonimos desempatados por
                    # director): MANDA siempre sobre lo que el relay saque de
                    # TMDB a ciegas, que es justo lo que ponia el cartel de la
                    # peli equivocada ('La odisea' -> la de Nolan).
                    if _m.get("dtok") or "image.tmdb.org" not in (it.get("poster") or ""):
                        _cat_apply_meta(it, _m)
                r2 = {"items": en, "ts": _t.time()}
                _CATBROWSE_CACHE[_key] = r2
                d = _catbrowse_load()
                d[_key] = r2
                _catbrowse_save(d)
        except Exception:
            pass
    if pending:   # sin pendientes = todo ya en HD por semilla/cache del box ->
        # el re-enrich de fondo (que ademas toca TMDB desde Render) no aporta nada
        _thr.Thread(target=_bg_enrich, daemon=True).start()
    # `pending`: titulos sin poster TMDB para que el BOX (IP residencial) los
    # enriquezca y los empuje a /catenrich. El box ANTIGUO ignora este campo
    # (sigue funcionando con la semilla + el bg-enrich) -> backward-compatible.
    return jsonify({"ok": True, "items": len(raw), "bg": True, "pending": pending})


@app.post("/catenrich")
def catenrich():
    """El BOX enriquece con SU TMDB (IP residencial; Render lo tiene baneado) los
    titulos que /catfeed devolvio en `pending` y los empuja aqui:
        {kind, meta:{content_id: {poster, year, rating, overview, backdrop,
                                  genres, tmdb_id}}}
    Lo aplicamos por content_id a la cache del Inicio de ese kind (sin tocar TMDB
    desde Render) y lo acumulamos en la cache de enrich -> el Inicio sale en HD
    aunque la IP de Render este baneada por TMDB, y los proximos /catfeed se
    rellenan solos por content_id."""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    kind = (body.get("kind") or "").strip().lower()
    meta = body.get("meta")
    if kind not in _CAT_BROWSE or not isinstance(meta, dict):
        return jsonify({"ok": False}), 400
    saved = _cat_enrich_store(meta)             # acumula (persistente)
    # Lo que quedo guardado MANDA: _cat_enrich_store rechaza que un box antiguo
    # pise un meta ya resuelto contra la ficha de DonTorrent, y aqui hay que
    # respetar la misma decision (si no, el rechazo valdria para el disco pero el
    # Inicio en memoria se quedaria igualmente con el poster equivocado).
    stored = _cat_enrich_load()
    key = "%s:1" % kind
    applied = 0
    with _FileLock(_CATBROWSE_FILE):            # RMW seguro de la cache del Inicio
        rec = _CATBROWSE_CACHE.get(key) or _catbrowse_load().get(key)
        if rec and rec.get("items"):
            for it in rec["items"]:
                cid = str(it.get("content_id"))
                m = meta.get(cid)
                sd = stored.get(cid) or {}
                if sd.get("dtok") and not (m or {}).get("dtok"):
                    m = sd
                if _cat_apply_meta(it, m):
                    applied += 1
            _CATBROWSE_CACHE[key] = rec
            try:
                disk = _catbrowse_load()
                disk[key] = rec
                _catbrowse_save(disk)
            except Exception:
                pass
    return jsonify({"ok": True, "saved": saved, "applied": applied})


_CAT_META_CACHE = {}


def _tmdb_detail(ep, tid):
    """runtime + trailer (YouTube) + nº temporadas de TMDB. Cacheado + breaker.
    {} si TMDB no responde (-> la ficha no muestra trailer/duracion, sin colgar)."""
    ck = (ep, str(tid))
    if ck in _CAT_META_CACHE:
        return _CAT_META_CACHE[ck]
    if _tmdb_is_down():
        return {}
    try:
        r = _TMDB_SESS.get(
            f"https://api.themoviedb.org/3/{ep}/{tid}",
            params={"api_key": _CAT_TMDB_KEY, "language": "es-ES",
                    "append_to_response": "videos"},
            timeout=(3, 5))
        if r.status_code != 200:
            _tmdb_mark(False)
            return {}
        d = r.json() or {}
        rt = d.get("runtime") or 0
        if not rt and d.get("episode_run_time"):
            rt = (d.get("episode_run_time") or [0])[0]
        vids = (d.get("videos") or {}).get("results") or []
        trailer = ""
        for v in vids:                       # trailer oficial primero
            if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                trailer = v.get("key")
                break
        if not trailer:                       # si no, cualquier video de YouTube
            for v in vids:
                if v.get("site") == "YouTube":
                    trailer = v.get("key")
                    break
        out = {"runtime": rt, "trailer": trailer,
               "seasons": d.get("number_of_seasons") or 0}
        _tmdb_mark(True)
        _CAT_META_CACHE[ck] = out
        if len(_CAT_META_CACHE) > 2000:   # poda FIFO: no crecer sin limite
            for k in list(_CAT_META_CACHE)[:200]:
                _CAT_META_CACHE.pop(k, None)
        return out
    except Exception:
        _tmdb_mark(False)
        return {}


@app.get("/catmeta")
def catmeta():
    """Detalle PEREZOSO para la ficha enriquecida: duracion + trailer (YouTube).
    Una sola llamada TMDB POR FICHA ABIERTA (no por cuadricula), cacheada en
    memoria y protegida por el MISMO breaker del enrich -> si TMDB banea la IP,
    devuelve {} al instante y la ficha simplemente no muestra trailer/duracion
    (la sinopsis/generos/backdrop ya vienen gratis con el item). Cero baneo."""
    tid = (request.args.get("id") or "").strip()
    kind = (request.args.get("kind") or "movie").strip().lower()
    if not tid.isdigit():
        return jsonify({})
    ep = "tv" if kind in ("tv", "serie") else "movie"
    return jsonify(_tmdb_detail(ep, tid))


@app.get("/cattitlemeta")
def cattitlemeta():
    """Enriquece un item POR TITULO (para 'Mi lista': los favoritos guardados
    antes de la mejora no llevan sinopsis/generos/backdrop/tmdb_id). Resuelve TODO
    con el MISMO matching del catalogo (_cat_tmdb, cacheado) + detalle perezoso.
    Lo llama el front SOLO al abrir un favorito sin enriquecer (1 vez, luego se
    persiste en el movil) -> de uno en uno, cero rafagas, cero baneo."""
    title = (request.args.get("title") or "").strip()
    year = (request.args.get("year") or "").strip()
    kind = (request.args.get("kind") or "movie").strip().lower()
    if not title:
        return jsonify({})
    ep = "tv" if kind in ("tv", "serie") else "movie"
    # El año (si no esta ya en el titulo) afina el matching de titulos comunes
    # ("Perdida", "Venganza"...). _cat_tmdb lo extrae del propio string.
    q = title if (not year or year in title) else f"{title} {year}"
    meta = _bounded(lambda: _cat_tmdb(q, ep), 8.0, {}) or {}
    # SOLO los campos de la BUSQUEDA (1 llamada TMDB -> sinopsis/generos salen
    # rapido). El trailer/duracion los pide el front aparte via /catmeta(tmdb_id)
    # -> la ficha no espera al detalle para mostrar lo principal.
    return jsonify({"overview": meta.get("overview") or "",
                    "backdrop": meta.get("backdrop"),
                    "genres": meta.get("genres") or [],
                    "tmdb_id": meta.get("tmdb_id"),
                    "year": meta.get("year"),
                    "rating": meta.get("rating")})


@app.get("/catbrowse")
def catbrowse():
    kind = (request.args.get("kind") or "estrenos").strip().lower()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except Exception:
        page = 1
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    key = f"{kind}:{page}"          # entrada DonTorrent (fuente PRINCIPAL = la web)
    dxkey = key + ":dx"             # entrada DivxTotal (ULTIMO recurso; NO pisa DT)
    now = _t.time()

    def _load(k, seed=False):
        # memoria -> disco -> (solo DT) SEMILLA del repo
        e = _CATBROWSE_CACHE.get(k)
        if not e:
            e = _catbrowse_load().get(k)
            if not e and seed:
                e = _catbrowse_seed().get(k)
            if e:
                _CATBROWSE_CACHE[k] = e
        return e

    def _store(k, rec):
        _CATBROWSE_CACHE[k] = rec
        try:
            disk = _catbrowse_load()
            disk[k] = rec
            _catbrowse_save(disk)
        except Exception:
            pass

    dt_ent = _load(key, seed=True)
    # 1) DonTorrent FRESCO en cache -> al instante, sin tocar ninguna fuente.
    if dt_ent and (now - dt_ent.get("ts", 0)) < _CATBROWSE_TTL:
        return jsonify({"items": dt_ent["items"], "cached": True, "src": "dt"})

    # 2) Intentar REFRESCAR DonTorrent (directo; el box solo si no hay stale).
    #    Tope CORTO (4s) si ya hay DT-stale: no hacemos esperar al usuario -> si
    #    Render esta baneado el breaker devuelve al instante y servimos el stale.
    #    Sin stale (1a vez, sin semilla) damos el presupuesto completo (8s) + box.
    bp = _CAT_BROWSE.get(kind, "/")
    path = bp if page <= 1 else (bp.rstrip("/") + f"/page/{page}")
    budget = 4.0 if dt_ent else 8.0
    html = _bounded(lambda: (_cat_dt_session_get(path) or ("", None))[0],
                    budget, "") or ""
    if not html and not dt_ent:
        # Inicio frio SIN nada DT que servir: marca el baneo (el slow-drip evade
        # el timeout de requests) para que el resto del Inicio salte DT al instante.
        # Con stale NO marcamos: el budget corto puede abortar a un DT solo lento.
        _dt_mark(False)
        if len(code) == 6:
            # Render bloqueado por DonTorrent -> traer el listado VIA EL BOX.
            job = "db" + os.urandom(5).hex()
            _kb_enqueue(code, {"c": "etjob", "job": job, "op": "dthtml",
                               "path": path})
            res = _catjob_wait(job, 9.0)
            html = (res or {}).get("html") or ""
    if html:
        # TOPE DURO al enrich (ver /catetbox): `_cat_enrich` consulta TMDB, que
        # BANEA la IP de Render, y sin limite se queda colgado ocupando uno de
        # los 8 hilos de gunicorn. Esta es la ruta del INICIO, la que carga TODO
        # el mundo: unas pocas visitas con la cache vencida bastaban para dejar
        # el relay sordo (2026-08-07: el box no podia ni empujar /catfeed,
        # ReadTimeout de 45s, y las busquedas devolvian 0).
        items = _cat_parse_items(html)
        items = _bounded(lambda: _cat_enrich(items), 10.0, default=items)
        rec = {"items": items, "ts": now}
        _store(key, rec)
        return jsonify({"items": items, "src": "dt"})

    # 3) DonTorrent no disponible AHORA -> servir DonTorrent STALE (de hace un
    #    rato) ANTES que DivxTotal. La web original es DonTorrent y los listados
    #    cambian despacio -> DT viejo >> DX fresco. (Pedido explicito del usuario.)
    if dt_ent:
        return jsonify({"items": dt_ent["items"], "stale": True, "src": "dt"})

    # 4) Nunca hubo DonTorrent (ni cache, ni disco, ni semilla) -> DivxTotal como
    #    ULTIMO recurso, en su PROPIA clave para no pisar nunca una entrada DT.
    dx_ent = _load(dxkey)
    if not (dx_ent and (now - dx_ent.get("ts", 0)) < _CATBROWSE_DX_TTL):
        dxit = _bounded(lambda: _dx_browse_items(kind, page), 6.0, []) or []
        if dxit:
            # mismo tope que arriba: TMDB no puede colgar un hilo del relay
            dx_ent = {"items": _bounded(lambda: _cat_enrich(dxit), 10.0,
                                        default=dxit),
                      "ts": now, "dx": True}
            _store(dxkey, dx_ent)
    if dx_ent:
        return jsonify({"items": dx_ent["items"], "dx": True, "src": "dx"})
    return jsonify({"items": []})


@app.get("/catdump")
def catdump():
    """Vuelca la cache ACTUAL de DonTorrent (claves kind:1) en formato semilla.
    Se usa para regenerar catalog_seed.json tras un /catfeed bueno: capturar este
    JSON y commitearlo al repo. NO toca ninguna fuente (solo lee cache/disco)."""
    out = {}
    src = dict(_catbrowse_load())
    for k, v in _CATBROWSE_CACHE.items():
        src[k] = v
    for k, v in src.items():
        # solo entradas DonTorrent de 1a pagina, con items (lo que siembra el Inicio)
        if k.endswith(":1") and not (v or {}).get("dx") and (v or {}).get("items"):
            out[k] = {"items": v["items"], "ts": v.get("ts", 0)}
    return jsonify(out)


@app.get("/catdiag")
def catdiag():
    """Radiografia del estado INTERNO del relay para diagnosticar por que el Inicio
    sale solo-DX. NO toca DonTorrent/DivxTotal/TMDB (cero riesgo de baneo): solo lee
    cache en memoria/disco, el breaker y contadores ya conocidos. Una sola peticion."""
    now = _t.time()
    out = {"build": "dtbl05", "now": int(now)}   # MISMO valor que /ping (app.py:355)
    # 0) Cajas VIVAS: sin esto no habia forma de saber si el sistema tiene alguna
    #    Kodi encendida (el 2026-08-06 se perdio tiempo creyendo que no habia
    #    ninguna porque /kb/list devolvia vacio — pero /kb/list es el espejo de
    #    pantalla de UNA caja, no un listado). Se publican EDADES, nunca los
    #    codigos: un code de 6 cifras permite mandar ordenes a esa tele.
    try:
        _now_b = _t.time()
        _ages = sorted(int(_now_b - e.get("ts", 0))
                       for e in _kbstatus_load().values()
                       if (_now_b - e.get("ts", 0)) < 90)
        # Versiones del addon en las cajas vivas (sin exponer codigos): saber
        # cuantas se han actualizado explica por que unas cosas van rapidas y
        # otras no (p.ej. los capitulos de WolfMax necesitan 2.9.61+).
        _vers = {}
        for e in _kbstatus_load().values():
            if (_now_b - e.get("ts", 0)) < 90:
                _vers[e.get("v") or "?"] = _vers.get(e.get("v") or "?", 0) + 1
        out["boxes_live"] = {"n": len(_ages), "ages_s": _ages[:8], "vers": _vers}
    except Exception:
        out["boxes_live"] = {"n": -1}
    # 1) Breaker de DonTorrent: ¿esta Render saltando DT (baneado)?
    down = _dt_is_down()
    out["dt_breaker"] = {
        "down": down,
        "remaining_s": max(0, int(_DT_DOWN_UNTIL[0] - now)) if down else 0,
        "cooldown_s": _DT_DOWN_COOLDOWN,
    }
    # 2) Estado de la cache del Inicio (memoria + disco): origen (dx vs DT real),
    #    antiguedad y nº de items. Aqui se ve si esta "pegada" en DX.
    def _snap(cache, src):
        d = {}
        for k, v in (cache or {}).items():
            try:
                d[k] = {"src": "dx" if v.get("dx") else "dt",
                        "n": len(v.get("items") or []),
                        "age_s": int(now - v.get("ts", 0)),
                        "stale": (now - v.get("ts", 0)) >= (
                            _CATBROWSE_DX_TTL if v.get("dx") else _CATBROWSE_TTL)}
            except Exception:
                pass
        return d
    out["catbrowse_mem"] = _snap(_CATBROWSE_CACHE, "mem")
    try:
        out["catbrowse_disk"] = _snap(_catbrowse_load(), "disk")
    except Exception:
        out["catbrowse_disk"] = {}
    # 2b) Estado de DivxTotal en ESTE worker: breaker, semaforo (si sem_free=0,
    # hay hilos zombis de intentos abandonados ocupando los 2 huecos) y dominio
    # (memoria + persistido). Con esto un "DX no sale" se diagnostica en una
    # peticion en vez de a ciegas.
    dxd = _dx_is_down()
    out["dx"] = {
        "down": dxd,
        "remaining_s": max(0, int(_DX_DOWN_UNTIL[0] - now)) if dxd else 0,
        "sem_free": getattr(_DX_SEM, "_value", None),
        "domain_mem": _DX_DOM_CACHE.get("dom"),
        "domain_saved": _dx_load_domain(),
        "trace": dict(_DX_TRACE),
    }
    # 3) ¿Esta el box EMPUJANDO /catfeed? (segundos desde el ultimo empuje por kind)
    out["catfeed_last_s"] = {k: int(now - v) for k, v in _CATFEED_LAST.items()}
    # 4) Memoria del worker (Render free = 512MB; el usuario sospechaba OOM).
    try:
        with open("/proc/self/status") as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    out["mem_rss_mb"] = round(int(ln.split()[1]) / 1024, 1)
                    break
    except Exception:
        out["mem_rss_mb"] = None
    # 5) Contadores baratos ya cacheados (NO consultan la API).
    out["catsearch_cached"] = len(_CATSEARCH_CACHE)
    # Fuentes-por-caja (EliteTorrent/WolfMax/DivxTotal-plan-B): cuanto hay
    # cacheado y cuantos huecos de prestamo quedan. Si lend_free=0 de forma
    # sostenida, las busquedas se estan quedando sin ET/WF por saturacion.
    try:
        out["wfidx"] = {"n": len(_wfidx_load()),
                        "age_s": int(now - _WFIDX_TS[0]) if _WFIDX_TS[0] else None}
    except Exception:
        out["wfidx"] = {"n": -1}
    out["catbox"] = {
        "cached": len(_CATBOX_CACHE),
        "lend_free": getattr(_BOX_LEND_SEM, "_value", None),
        "lend_boxes": len(_BOX_LEND_PER),
    }
    out["sapi_credits_left"] = _SAPI_CRED.get("left")
    # Diagnostico de la semilla: si seed_meta=0 el blindaje no puede actuar (el
    # Inicio se degradaria a la caratula no-HD) -> archivo no cargado / ruta mala.
    try:
        out["seed_meta"] = len(_seed_meta_index())
        out["seed_kinds"] = {k: len(v.get("items", []))
                             for k, v in (_catbrowse_seed() or {}).items()}
    except Exception as e:
        out["seed_meta"] = "ERR:%s" % e
    out["pid"] = os.getpid()
    return jsonify(out)


# --- Caché de fichas de serie (episodios DonTorrent) -----------------------
# Las series cambian despacio -> reaperturas instantaneas y, sobre todo, MENOS
# peticiones a DonTorrent (clave para que la IP de Render NO se banee). Igual
# patron que _CATBROWSE_CACHE: memoria + disco (sobrevive a reinicios de worker).
_CATDETAIL_CACHE = {}
_CATDETAIL_TTL = 1800            # 30 min
_CATDETAIL_FILE = "/tmp/mw_catdetail.json"


def _catdetail_load():
    try:
        with open(_CATDETAIL_FILE, "r", encoding="utf-8") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _catdetail_save(d):
    try:
        # poda: no dejar crecer el disco indefinidamente (quedarse las 200 mas
        # recientes basta de sobra para reaperturas).
        if len(d) > 200:
            d = dict(sorted(d.items(), key=lambda kv: kv[1].get("ts", 0))[-200:])
        tmp = _CATDETAIL_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(d, f)
        os.replace(tmp, _CATDETAIL_FILE)
    except Exception:
        pass


# SEMILLA de episodios (series del Inicio) versionada en el repo: las series del
# catalogo ABREN AL INSTANTE sin tocar DonTorrent ni el box. Es el suelo; se
# refresca cuando DonTorrent/box responden. Misma idea que catalog_seed.json.
_CATDETAIL_SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "episodes_seed.json")
_CATDETAIL_SEED_CACHE = [None]


def _catdetail_seed():
    if _CATDETAIL_SEED_CACHE[0] is None:
        try:
            with open(_CATDETAIL_SEED_FILE, "r", encoding="utf-8") as f:
                _CATDETAIL_SEED_CACHE[0] = _json.load(f) or {}
        except Exception:
            _CATDETAIL_SEED_CACHE[0] = {}
    return _CATDETAIL_SEED_CACHE[0]


def _cat_parse_detail(html):
    """Parsea el HTML de una ficha de serie DonTorrent -> (title, [eps]). Sirve
    igual para el HTML traido por Render o por el box (mismo parser)."""
    tm = _re_dt.search(r"<title>([^<]*)</title>", html)
    raw = (tm.group(1).split(" - ")[0].strip() if tm else "Serie")
    # El H1/title viene como 'Descargar Ted Lasso' -> sin ese prefijo TMDB acierta.
    title = _re_dt.sub(r"^\s*Descargar\s+", "", raw, flags=_re_dt.I).strip() or raw
    eps = []
    for m in _re_dt.finditer(r"<tr\b.*?</tr>", html, _re_dt.S | _re_dt.I):
        row = m.group(0)
        dm = _re_dt.search(
            r'data-content-id=["\'](\d+)["\'][^>]*data-tabla=["\']([^"\']+)["\']',
            row)
        if dm:
            cid, tabla = dm.group(1), dm.group(2)
        else:
            dm = _re_dt.search(
                r'data-tabla=["\']([^"\']+)["\'][^>]*data-content-id=["\'](\d+)["\']',
                row)
            if not dm:
                continue
            cid, tabla = dm.group(2), dm.group(1)
        cells = _re_dt.findall(r"<td\b[^>]*>(.*?)</td>", row, _re_dt.S | _re_dt.I)
        # 1a celda = columna 'Episodios' (conserva rangos reales: '1x01 al 1x03')
        cell0 = (_re_dt.sub(r"\s+", " ", _re_dt.sub(r"<[^>]+>", " ", cells[0]))
                 .strip() if cells else "")
        text = _re_dt.sub(r"\s+", " ", _re_dt.sub(r"<[^>]+>", " ", row)).strip()
        sm = _re_dt.search(r"\b(\d{1,2})\s*x\s*(\d{1,3})\b", cell0 or text)
        season = int(sm.group(1)) if sm else 0
        episode = int(sm.group(2)) if sm else 0
        qm = _CAT_QRE.search(text)
        label = cell0.strip(" .-–—") or (
            ("%dx%02d" % (season, episode)) if sm else "Descargar")
        eps.append({"content_id": cid, "tabla": tabla, "label": label,
                    "season": season, "episode": episode,
                    "quality": (_cat_norm_quality(qm.group(1)) if qm else "")})
    return title, eps


@app.get("/catdetail")
def catdetail():
    """Episodios de una serie DonTorrent. path=/serie/ID/slug -> JSON.
    ROBUSTO ante el baneo de la IP de Render (lo que rompia los capitulos):
    1) cache fresca -> instantaneo, sin tocar DonTorrent;
    2) DonTorrent directo desde Render (tope duro, marca el breaker si falla);
    3) si Render esta baneado y hay code -> el BOX trae el HTML (IP residencial,
       no baneada) via dthtml, igual que catbrowse/catsearch;
    4) si todo falla pero hay cache vieja, se sirve (mejor lo ultimo conocido)."""
    path = (request.args.get("path") or "").strip()
    if not _re_dt.match(r"^/serie/\d+/", path):
        return jsonify({"error": "bad path", "episodes": []}), 400
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    now = _t.time()
    # 1) cache fresca (memoria -> disco -> SEMILLA del repo). La semilla hace que
    #    las series del Inicio ABRAN AL INSTANTE sin tocar DonTorrent ni el box
    #    (era justo lo que fallaba: con el box apagado los capitulos no cargaban).
    ent = _CATDETAIL_CACHE.get(path)
    if ent is None:
        ent = _catdetail_load().get(path) or _catdetail_seed().get(path)
        if ent:
            _CATDETAIL_CACHE[path] = ent
    if ent and (now - ent.get("ts", 0)) < _CATDETAIL_TTL:
        return jsonify(ent["data"])
    # 2-3) Render casi NUNCA alcanza DonTorrent (IP de datacenter baneada). Antes
    #    se probaba el directo (8s) y SOLO si fallaba se iba al box (14s) -> 22s en
    #    SERIE, por encima del AbortController de 20s del front -> "no se pudieron
    #    leer / lento". Ahora lanzamos el job al BOX EN PARALELO con el intento
    #    directo: el box (IP residencial, no baneada) ya va trabajando y, si el
    #    directo falla, su HTML llega antes -> la serie abre en ~14s, no 22s.
    # DEADLINE TOTAL. Medido el 12-09-2026: esta ruta llego a estar 90s SIN
    # contestar con un path que no estaba cacheado (el front se rinde a los 20s
    # y, peor, cada peticion colgada se come uno de los 8 hilos del relay: es
    # exactamente como se tumbo el relay el 07-08). Ahora TODAS las etapas
    # (incluidos el parseo y TMDB, que antes no tenian tope) comen del mismo
    # presupuesto y la peticion SIEMPRE responde.
    _ddl = now + 24.0     # el front espera 26s
    _drem = lambda: max(0.0, _ddl - _t.time())
    box = _box_for(code)      # la suya si esta viva; si no, cualquiera (§_box_for)
    # A DOS cajas: traer esta ficha cuesta 21-38s en una caja con el ISP
    # tumbando el DoH y ~1s en una que lo tenga sano. Preguntando a dos, casi
    # siempre contesta una rapido (antes salia 0 capitulos en TODAS las series
    # del Inicio).
    _jobs = []
    if box:
        def _pide_html(b):
            j = "dd" + os.urandom(5).hex()
            _kb_enqueue(b, {"c": "etjob", "job": j, "op": "dthtml",
                            "path": path})
            return j
        _jobs.append(_pide_html(box))
        _b2 = next((b for b in _live_boxes() if b != box), None)
        if _b2:
            _jobs.append(_pide_html(_b2))
    # El intento DIRECTO, corto: la IP de Render esta baneada casi siempre.
    html = _bounded(lambda: (_cat_dt_session_get(path) or ("", None))[0],
                    min(2.5, _drem()), "") or ""
    if not html:
        _dt_mark(False)        # marca el baneo -> siguientes aperturas saltan DT ya
        if _jobs:              # las cajas ya llevan ~2,5s adelantadas
            res = _catjob_wait_any(_jobs, _drem(),
                                   lambda r: bool((r or {}).get("html")))
            html = (res or {}).get("html") or ""
    if not html:
        # 4) stale: mejor lo ultimo conocido que una lista vacia.
        if ent:
            d = dict(ent["data"])
            d["stale"] = True
            return jsonify(d)
        return jsonify({"episodes": []})
    title, eps = _bounded(lambda: _cat_parse_detail(html),
                          max(1.0, min(4.0, _drem())), ("", []))
    meta = (_bounded(lambda: _cat_tmdb(title, "tv"),
                     max(1.0, min(6.0, _drem())), {}) or {}) if title else {}
    data = {"title": title, "poster": meta.get("poster"),
            "year": meta.get("year"), "rating": meta.get("rating"),
            "episodes": eps}
    if eps:
        rec = {"data": data, "ts": now}
        _CATDETAIL_CACHE[path] = rec
        try:
            disk = _catdetail_load()
            disk[path] = rec
            _catdetail_save(disk)
        except Exception:
            pass
    return jsonify(data)


_CAT_PAGE = r"""<!doctype html><html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<title>MejorWolf</title>
<meta name="theme-color" content="#06070c">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/icon-512.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="MejorWolf">
<style>
:root{--bg:#06070c;--card:rgba(255,255,255,.06);--stroke:rgba(255,255,255,.10);--txt:#f4f6fb;--sub:#8a93a6;--blue:#0a84ff;--blue2:#409cff;--green:#30d158}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
/* RED DE SEGURIDAD: pase lo que pase, la pagina no se va de ancho ni se puede
   arrastrar de lado. Las barras que SI se deslizan a proposito (listas,
   temporadas, ultimas busquedas) llevan su propio scroll y no se ven afectadas.
   Ademas, nada puede ser mas ancho que su sitio. */
html,body{max-width:100%;overflow-x:hidden}
img,video,svg,iframe{max-width:100%}
html,body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;-webkit-user-select:none;-moz-user-select:none;user-select:none;-webkit-touch-callout:none}
input,textarea{-webkit-user-select:text;-moz-user-select:text;user-select:text}
body{min-height:100vh;background:radial-gradient(1100px 600px at 50% -10%,#1b2740 0,transparent 60%),var(--bg)}
.wrap{max-width:760px;margin:0 auto;padding:16px 14px 96px}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.brand{font-weight:800;font-size:19px;display:flex;align-items:center;gap:8px;letter-spacing:.3px}
.brand .d{width:26px;height:26px;border-radius:9px;background:linear-gradient(145deg,var(--blue2),var(--blue));display:flex;align-items:center;justify-content:center;font-size:15px}
.code{width:96px;letter-spacing:3px;text-align:center;font-weight:600;background:rgba(255,255,255,.07);border:1px solid var(--stroke);color:var(--txt);border-radius:12px;padding:9px 8px;outline:0}
/* Selector "Mis Kodis": varios codigos guardados con nombre (Salon, Tablet, PC) */
.devbtn{display:flex;align-items:center;gap:6px;cursor:pointer;max-width:150px;letter-spacing:.3px;font-weight:600;font-size:13px;background:rgba(255,255,255,.07);border:1px solid var(--stroke);color:var(--txt);border-radius:12px;padding:9px 11px;outline:0}
.devbtn:active{transform:scale(.98)}
.devbtn #devname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.devbtn .devcar{opacity:.55;font-size:11px;flex:none}
.devsheet-h{display:flex;align-items:center;justify-content:space-between;padding:16px 18px 6px;font-size:17px;font-weight:800}
.devsheet-x{border:1px solid var(--stroke);background:rgba(255,255,255,.05);color:var(--sub);width:34px;height:34px;border-radius:50%;font-size:15px;cursor:pointer;flex:none}
.devsub{color:var(--sub);font-size:12.5px;padding:0 18px 8px;margin-top:-2px}
.devlist{padding:6px 14px 4px}
.devempty{color:var(--sub);text-align:center;font-size:14px;padding:14px 8px 18px;line-height:1.6}
.devrow{display:flex;align-items:center;gap:12px;background:rgba(255,255,255,.05);border:1px solid var(--stroke);border-radius:14px;padding:12px 14px;margin-bottom:9px;cursor:pointer;transition:.15s}
.devrow:active{transform:scale(.99);background:rgba(255,255,255,.1)}
.devrow.on{border-color:var(--blue);background:rgba(10,132,255,.14)}
.devdot{width:10px;height:10px;border-radius:50%;flex:none;background:#52607a}
.devdot.on{background:#30d158;box-shadow:0 0 7px rgba(48,209,88,.7)}
.devdot.off{background:#ff453a}
.devmeta{flex:1;min-width:0}
.devnm{font-size:15px;font-weight:700;line-height:1.2;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.devcc{font-size:12px;color:var(--sub);letter-spacing:2px;font-variant-numeric:tabular-nums;margin-top:2px}
.deved,.devdel{border:0;background:transparent;color:var(--sub);font-size:15px;cursor:pointer;flex:none;width:34px;height:34px;border-radius:50%}
.deved:active{background:rgba(255,255,255,.14)}
.devdel:active{background:rgba(255,69,58,.18)}
.devadd{padding:8px 14px 20px;display:flex;flex-direction:column;gap:9px}
.devin{width:100%;background:rgba(255,255,255,.06);border:1px solid var(--stroke);border-radius:12px;color:var(--txt);padding:13px 14px;font-size:15px;outline:0;box-sizing:border-box}
.devin::placeholder{color:var(--sub)}
.devin.devc{letter-spacing:3px;text-align:center;font-variant-numeric:tabular-nums}
.devsave{border:0;border-radius:12px;padding:14px;font-size:15px;font-weight:700;color:#fff;background:linear-gradient(145deg,var(--blue2),var(--blue));cursor:pointer}
.devsave:active{transform:scale(.99)}
.tabs{display:flex;background:rgba(255,255,255,.05);border:1px solid var(--stroke);border-radius:14px;padding:4px;margin-bottom:14px}
.tab{flex:1;border:0;background:transparent;color:var(--sub);font-weight:600;font-size:14px;padding:9px;border-radius:10px;cursor:pointer}
.tab.on{color:#0b1020;background:#f4f6fb}
.pane.hidden{display:none}
.chips{display:flex;gap:8px;margin-bottom:14px}
.chip{border:1px solid var(--stroke);background:var(--card);color:var(--txt);font-weight:600;font-size:13px;padding:8px 14px;border-radius:999px;cursor:pointer}
.chip.on{background:linear-gradient(145deg,var(--blue2),var(--blue));border-color:transparent;color:#fff}
.search{display:flex;gap:8px;margin-bottom:16px}
/* min-width:0 -> sin esto el campo NO baja de lo que mide su texto y el boton
   "Buscar" se sale de la pantalla en moviles estrechos (320-360 px), que era lo
   que obligaba a arrastrar la pagina de lado. */
.search input{flex:1 1 0;min-width:0;background:var(--card);border:1px solid var(--stroke);border-radius:14px;color:var(--txt);font-size:16px;padding:13px 14px;outline:0}
.search button{flex:0 0 auto;border:0;border-radius:14px;padding:0 16px;font-weight:700;color:#fff;background:linear-gradient(145deg,var(--blue2),var(--blue));white-space:nowrap}
@media(max-width:360px){.search button{padding:0 13px;font-size:14px}
 .search input{font-size:15px;padding:12px}}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}
/* Entrada suave: la cuadricula aparecia de golpe. Solo las 12 primeras llevan
   retardo (las de abajo ya entran con el scroll). */
@keyframes cardIn{from{opacity:0;transform:translateY(9px)}to{opacity:1;transform:none}}
.grid>.card{animation:cardIn .26s ease-out both}
.grid>.card:nth-child(-n+12){animation-delay:calc((var(--i,0)) * .022s)}
@media (prefers-reduced-motion:reduce){.grid>.card{animation:none}}
@media(max-width:430px){.grid{grid-template-columns:repeat(2,1fr)}}
/* Mi lista: barra con el boton de cambiar vista (cuadricula <-> lista) */
.listbar{display:flex;justify-content:flex-end;margin:0 0 12px}
.vtog{border:1px solid var(--stroke);background:var(--card);color:var(--txt);font-size:13px;font-weight:600;padding:9px 14px;border-radius:999px;cursor:pointer}
.vtog:active{transform:scale(.96)}
/* Vista LISTA compacta: 1 columna, portada mini a la izquierda */
.grid.lv{grid-template-columns:1fr!important;gap:8px}
.grid.lv .card{display:flex;align-items:stretch}
.grid.lv .ph{aspect-ratio:auto;width:48px;min-height:72px;flex:none}
.grid.lv .ph .tl,.grid.lv .ph .kindtag,.grid.lv .ph .srctag{display:none}
.grid.lv .ph .fav{width:24px;height:24px;font-size:13px;top:3px;right:3px}
.grid.lv .m{flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center;padding:8px 14px}
.grid.lv .m .t{-webkit-line-clamp:1;font-size:14px}
.card{background:var(--card);border:1px solid var(--stroke);border-radius:14px;overflow:hidden;transition:.15s}
.card:active{transform:scale(.97)}
.card .ph{position:relative;aspect-ratio:2/3;background:#0e1320;cursor:pointer;
 transition:transform .12s ease-out}
.card .ph:active{transform:scale(.975)}
/* carga PEREZOSA real: el navegador solo baja los posters visibles (loading=lazy);
   antes iban como background-image inline -> el Inicio (scroll infinito) descargaba
   TODAS las caratulas aunque estuvieran fuera de pantalla. */
.card .ph .pimg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;border:0}
.card .noimg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:8px;text-align:center;font-size:12px;color:var(--sub)}
.card .tl{position:absolute;top:6px;left:6px;display:flex;flex-direction:column;gap:4px;align-items:flex-start;z-index:1}
.card .q{background:rgba(10,132,255,.85);border-radius:6px;padding:2px 7px;font-size:10px;font-weight:700}
.card .rartag{background:rgba(255,159,110,.95);color:#1a0d06;border-radius:6px;padding:2px 7px;font-size:10px;font-weight:800;letter-spacing:.2px}
.card .fav{position:absolute;top:4px;right:4px;width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:17px;color:#fff;background:rgba(0,0,0,.4);border-radius:50%;cursor:pointer}
.card .m{padding:8px 9px;cursor:pointer}
.card .t{font-size:12.5px;font-weight:600;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .y{font-size:11px;color:var(--sub);margin-top:2px}
.msg{color:var(--sub);text-align:center;padding:34px 10px;font-size:14px}
.sheet{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:flex-end;z-index:30}
.sheet.on{display:flex}
.sheet .box{width:100%;max-width:760px;margin:0 auto;background:#0e1320;border-top:1px solid var(--stroke);border-radius:20px 20px 0 0;padding:0;animation:up .2s ease;max-height:92vh;overflow-y:auto;overflow-x:hidden}
@keyframes up{from{transform:translateY(30px)}to{transform:none}}
.sh-poster{width:min(68vw,260px);aspect-ratio:2/3;margin:2px auto 14px;border-radius:14px;background:#0e1320 center/cover no-repeat;border:1px solid var(--stroke);box-shadow:0 12px 34px rgba(0,0,0,.6)}
.sh-poster.hidden{display:none}
.sh-meta{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin:2px 0 6px;min-height:0}
.sh-meta:empty{margin:0}
.seedtag{font-size:12.5px;font-weight:700;padding:4px 12px;border-radius:999px;border:1px solid var(--stroke)}
.seedtag.s-ok{background:rgba(48,209,88,.16);color:#62e08c;border-color:rgba(48,209,88,.4)}
.seedtag.s-low{background:rgba(255,196,0,.14);color:#ffce4d;border-color:rgba(255,196,0,.4)}
.seedtag.s-zero{background:rgba(255,77,77,.16);color:#ff8a8a;border-color:rgba(255,77,77,.42)}
.sheet h3{margin:0 0 4px;font-size:17px;text-align:center}
.sheet .sy{text-align:center}
.sheet .sy{color:var(--sub);font-size:13px;margin-bottom:14px}
.btn{display:block;width:100%;border:0;border-radius:14px;padding:15px;font-size:16px;font-weight:700;margin-top:10px;cursor:pointer}
.btn.play{color:#06140a;background:linear-gradient(145deg,#3dd46a,#27c257)}
.btn.fav{background:rgba(255,255,255,.08);color:var(--txt);border:1px solid var(--stroke)}
.btn.share{background:rgba(255,255,255,.08);color:var(--txt);border:1px solid var(--stroke)}
.btn.cancel{background:transparent;color:var(--sub)}
.shared{display:none;margin:0 0 14px}
.shared.on{display:block}
.shared-in{display:flex;align-items:center;gap:10px;background:linear-gradient(145deg,rgba(48,209,88,.16),rgba(48,209,88,.05));border:1px solid rgba(48,209,88,.36);border-radius:14px;padding:12px 14px}
.shared-lab{font-size:11px;color:#8fe0a6;font-weight:700;text-transform:uppercase;letter-spacing:.4px}
.shared-t{font-size:15px;font-weight:700;color:#eafff0;line-height:1.25}
.shared-btn{margin-left:auto;border:0;border-radius:12px;padding:11px 15px;font-weight:700;color:#06140a;background:linear-gradient(145deg,#3dd46a,#27c257);cursor:pointer;white-space:nowrap}
.shared-x{border:1px solid var(--stroke);background:rgba(255,255,255,.05);color:var(--sub);width:34px;height:34px;border-radius:50%;font-size:18px;cursor:pointer;flex:none}
.ov{position:fixed;inset:0;background:var(--bg);z-index:35;overflow-y:auto;display:none;padding-bottom:96px}
.ov.on{display:block}
.remote{position:fixed;inset:0;background:radial-gradient(900px 500px at 50% -10%,#1b2740 0,transparent 60%),var(--bg);z-index:38;display:none;overflow-y:auto}
.remote.on{display:block}
/* Boton flotante para abrir el mando (abajo-derecha, mano diestra). Queda por
   debajo de los overlays (z 38+) -> se oculta solo cuando el mando/visor estan abiertos. */
.fab{position:fixed;right:18px;bottom:calc(20px + env(safe-area-inset-bottom));z-index:25;width:60px;height:60px;border-radius:50%;border:0;cursor:pointer;color:#fff;background:linear-gradient(145deg,var(--blue2),var(--blue));box-shadow:0 8px 22px rgba(10,107,255,.45),0 2px 6px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;transition:transform .15s,box-shadow .15s}
.fab:active{transform:scale(.92)}
.fab svg{display:block}
.fab .fabl{position:absolute;bottom:-1px;right:-1px;background:#0e1320;border:1px solid var(--stroke);border-radius:8px;font-size:9px;font-weight:700;padding:1px 4px;color:var(--blue2)}
/* Boton Cerrar fijo abajo-derecha dentro del mando (sobre el mando, z 40 > 38) */
.rm-close{position:fixed;right:18px;bottom:calc(18px + env(safe-area-inset-bottom));z-index:40;border:1px solid var(--stroke);background:rgba(14,19,32,.95);backdrop-filter:blur(8px);color:var(--txt);font-size:15px;font-weight:700;padding:13px 20px;border-radius:999px;cursor:pointer;box-shadow:0 8px 22px rgba(0,0,0,.5)}
.rm-close:active{transform:scale(.95)}
.ovbar{display:flex;align-items:center;gap:10px;padding:14px;position:sticky;top:0;background:rgba(6,7,12,.85);backdrop-filter:blur(8px);border-bottom:1px solid var(--stroke)}
.ovback{border:0;background:transparent;color:var(--blue2);font-size:16px;font-weight:600;cursor:pointer}
.ovt{font-weight:700;font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#ov-body{padding:14px}
.ovhead{display:flex;gap:14px;margin-bottom:16px}
.ovposter{width:min(42vw,170px);aspect-ratio:2/3;border-radius:12px;background:#0e1320 center/cover;flex:none;border:1px solid var(--stroke);box-shadow:0 8px 22px rgba(0,0,0,.5)}
.ovh-t{font-size:18px;font-weight:700}
.ovh-y{color:var(--sub);font-size:13px;margin-top:4px}
.seas{font-size:13px;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:.4px;margin:16px 0 8px}
.ep{background:var(--card);border:1px solid var(--stroke);border-radius:12px;padding:14px;margin-bottom:8px;cursor:pointer;transition:.12s}
.ep:active{transform:scale(.98);background:rgba(255,255,255,.12)}
.epl{font-size:15px;font-weight:600}
.epq{font-size:10.5px;font-weight:800;margin-left:7px;padding:2px 7px;border-radius:6px;
 background:rgba(255,255,255,.08);color:var(--sub);letter-spacing:.2px;vertical-align:1px}
/* Ahora una misma serie puede traer capitulos de varias calidades (WolfMax no
   tiene todas en 4K), asi que la calidad DEBE verse de un vistazo. */
.epq[data-q="4K"]{background:rgba(191,90,242,.20);color:#d9a6ff;
 box-shadow:inset 0 0 0 1px rgba(191,90,242,.45)}
.epq[data-q="1080p"]{background:rgba(10,132,255,.16);color:#7db8ff}
.epb{display:inline-flex;gap:5px;margin-left:7px;vertical-align:middle}
.ep-rar{font-size:10px;background:rgba(255,159,110,.95);color:#1a0d06;border-radius:5px;padding:1px 6px;font-weight:800}
.ep-seed{font-size:10px;border-radius:5px;padding:1px 6px;font-weight:700;border:1px solid var(--stroke)}
.ep-seed.s-ok{color:#62e08c;background:rgba(48,209,88,.14);border-color:rgba(48,209,88,.4)}
.ep-seed.s-low{color:#ffce4d;background:rgba(255,196,0,.12);border-color:rgba(255,196,0,.4)}
.ep-seed.s-zero{color:#ff8a8a;background:rgba(255,77,77,.14);border-color:rgba(255,77,77,.42)}
.rmwrap{padding:30px 22px;text-align:center;max-width:520px;margin:0 auto}
.rm-t{font-size:20px;font-weight:700;margin-top:10px}
.rm-time{color:var(--sub);font-size:13px;margin:10px 0 6px}
.prog{height:5px;background:rgba(255,255,255,.12);border-radius:5px;overflow:hidden}
.prog>i{display:block;height:100%;background:linear-gradient(90deg,var(--blue2),var(--blue));width:0;border-radius:5px;transition:width .5s}
.prog.big{height:7px}
.rmctl{display:flex;align-items:center;justify-content:center;gap:18px;margin:28px 0}
.rmctl button{border:1px solid var(--stroke);background:var(--glass,rgba(255,255,255,.07));color:var(--txt);border-radius:50%;width:64px;height:64px;font-size:14px;font-weight:700;cursor:pointer}
.rmctl button.big{width:82px;height:82px;font-size:26px;background:linear-gradient(145deg,var(--blue2),var(--blue));border:0;color:#fff}
.npbar{position:fixed;left:0;right:0;bottom:0;z-index:20;background:rgba(14,19,32,.96);backdrop-filter:blur(10px);border-top:1px solid var(--stroke);display:none;cursor:pointer}
.npbar.on{display:block}
.np-prog-wrap{height:3px;background:rgba(255,255,255,.1)}
.np-prog{height:100%;width:0;background:linear-gradient(90deg,var(--blue2),var(--blue));transition:width .5s}
.np-row{display:flex;align-items:center;gap:10px;padding:11px 14px calc(11px + env(safe-area-inset-bottom))}
.np-t{flex:1;font-size:13.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.np-pp{border:0;background:var(--blue);color:#fff;width:40px;height:40px;border-radius:50%;cursor:pointer;flex:none;display:flex;align-items:center;justify-content:center;padding:0;line-height:0}
.np-pp svg{display:block}
.toast{position:fixed;left:50%;bottom:90px;transform:translateX(-50%) translateY(20px);background:#0e1320;border:1px solid var(--stroke);color:var(--txt);padding:12px 18px;border-radius:14px;font-size:14px;opacity:0;transition:.25s;z-index:45;box-shadow:0 10px 30px rgba(0,0,0,.5);max-width:90%}
.toast.on{opacity:1;transform:translateX(-50%)}
.spin{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:r .7s linear infinite;vertical-align:-3px}
@keyframes r{to{transform:rotate(360deg)}}
/* botones mas grandes (movil) */
.tab{padding:11px 4px;font-size:15px;white-space:nowrap}
/* Con cuatro pestanas ("Mis listas" es la larga) el texto se partia en dos
   lineas y descolocaba la barra entera: en pantallas estrechas encoge la letra
   en vez de romper la palabra. */
@media (max-width:430px){.tab{font-size:13.5px;padding:11px 2px;letter-spacing:-.2px}}
@media (max-width:360px){.tab{font-size:12.5px}}
.chip{padding:10px 18px;font-size:14px}
.search input{padding:15px 16px}
.search button{padding:0 20px;font-size:15px}
.card .fav{width:36px;height:36px;font-size:19px;top:5px;right:5px}
.btn{padding:16px}
/* modo MANDO (igual disposicion que el mando /kb) */
/* El mando NO tiene que llenar la pantalla: con 520 px los botones se
   estiraban en los moviles grandes (124x56 px para un icono de 18). Un ancho
   de mando de verdad, centrado, y el mismo aire entre todos los bloques. */
.rmwrap{--aire:14px;padding:10px 18px 46px;max-width:400px;margin:0 auto}
.rnp{margin:6px 0 var(--aire,14px)}
.rnp .nplab{font-size:11px;color:var(--sub);font-weight:700;letter-spacing:.5px;margin-bottom:6px}
.rnp .npttl{font-size:18px;font-weight:700;line-height:1.3;margin-bottom:12px}
.rmbar{height:6px;border-radius:4px;background:rgba(255,255,255,.1);overflow:hidden}
.rmbar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue2),var(--blue));transition:width .9s linear}
.nprow{display:flex;justify-content:space-between;margin-top:8px;font-size:13px;color:var(--sub);font-variant-numeric:tabular-nums}
.media{display:flex;justify-content:center;align-items:center;gap:14px;
 margin:0 0 var(--aire,14px)}
.rb{width:64px;height:64px;border-radius:50%;border:1px solid var(--stroke);background:rgba(255,255,255,.07);color:var(--txt);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:17px;transition:.15s}
.rb:active{transform:scale(.92);background:rgba(255,255,255,.16)}
.rb.play{width:82px;height:82px;background:linear-gradient(145deg,#3dd46a,#27c257);border-color:transparent;box-shadow:0 8px 22px rgba(48,209,88,.4);color:#06140a;font-size:30px}
.rb.stop{color:#ff453a;font-size:22px}
.rb.sk{font-size:15px;font-weight:700;line-height:1}.rb.sk small{font-size:10px;opacity:.75}
.row3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;
 margin:0 0 var(--aire,14px)}
@media(max-width:360px){.row3{gap:8px}}
/* Altura propia en vez de estirarse: asi el boton guarda la proporcion en
   cualquier movil y el icono no se pierde dentro. */
.pill{min-width:0;overflow:hidden;height:54px;display:flex;align-items:center;
 justify-content:center;gap:7px;border:1px solid var(--stroke);
 background:rgba(255,255,255,.07);color:var(--txt);border-radius:14px;
 padding:0 8px;font-size:21px;text-align:center;cursor:pointer;font-weight:600}
.pill:active{transform:scale(.97);background:rgba(255,255,255,.14)}
@media(max-width:360px){.pill{height:50px;font-size:19px;padding:0 5px}
 .rsub .pill span{font-size:10.5px}}
.padwrap{display:flex;justify-content:center;margin:calc(var(--aire,14px) + 4px) 0 var(--aire,14px)}
/* Proporcional: apretaba a 320 px y se quedaba pequena a 430. */
.pad{position:relative;width:min(246px,72vw);height:min(246px,72vw);border-radius:50%;border:1px solid var(--stroke);background:radial-gradient(circle at 50% 32%,rgba(255,255,255,.10),transparent 55%),conic-gradient(from 0deg,rgba(255,255,255,.05),rgba(255,255,255,.02),rgba(255,255,255,.05));box-shadow:inset 0 -20px 40px rgba(0,0,0,.5),0 18px 40px rgba(0,0,0,.45)}
.arrow{position:absolute;color:var(--sub);font-size:22px;width:clamp(50px,17vw,58px);
 height:clamp(50px,17vw,58px);display:flex;align-items:center;justify-content:center;
 cursor:pointer;border-radius:50%}
.arrow:active{background:rgba(255,255,255,.12);color:#fff}
.arrow.up{top:8px;left:50%;transform:translateX(-50%)}.arrow.down{bottom:8px;left:50%;transform:translateX(-50%)}
.arrow.left{left:8px;top:50%;transform:translateY(-50%)}.arrow.right{right:8px;top:50%;transform:translateY(-50%)}
.ok{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
 width:min(106px,31vw);height:min(106px,31vw);border-radius:50%;border:1px solid var(--stroke);background:radial-gradient(circle at 50% 35%,#2a3346,#161c2a);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;letter-spacing:1px;cursor:pointer;box-shadow:0 8px 20px rgba(0,0,0,.5)}
.ok:active{transform:translate(-50%,-50%) scale(.95)}
.navrow2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;
 margin:0 0 6px}
.navrow2 .pill{font-size:15px;font-weight:700}
.rar{color:#ff9f6e;font-size:13px;font-weight:600;margin:0 0 12px}
.ovfav{display:inline-flex;align-items:center;min-height:44px;border:1px solid var(--stroke);
 background:rgba(255,255,255,.07);color:var(--txt);border-radius:12px;padding:0 15px;
 font-size:14px;font-weight:600;cursor:pointer;margin-top:8px}
/* etiquetas tipo / fuente en la tarjeta */
.card .kindtag{position:absolute;bottom:6px;left:6px;background:rgba(0,0,0,.6);border:1px solid var(--stroke);border-radius:6px;padding:2px 7px;font-size:10px;font-weight:700;color:#dfe6f2}
.card .srctag{position:absolute;bottom:6px;right:6px;border-radius:6px;padding:2px 7px;font-size:9px;font-weight:800;letter-spacing:.4px;box-shadow:0 2px 6px rgba(0,0,0,.45)}
.srctag.s-dt{background:#4a9eff;color:#04122b}
.srctag.s-et{background:#ff9f6e;color:#2a1206}
.srctag.s-dx{background:#34d36a;color:#04210f}
.srctag.s-wf{background:#c77dff;color:#1e0833}
.srclegend{display:flex;flex-wrap:wrap;gap:9px 14px;justify-content:center;margin:-2px 0 14px;font-size:11px;color:var(--sub)}
.srclegend span{display:flex;align-items:center;gap:5px}
.srclegend i{width:11px;height:11px;border-radius:3px;display:inline-block;flex:none}
/* salto a minuto en el mando */
.jump{display:flex;gap:10px;margin:0 0 var(--aire,14px)}
/* mismo caso que el buscador: sin min-width:0 el "Saltar a" se salia 54 px */
.jump input{flex:1 1 0;min-width:0;background:rgba(255,255,255,.07);border:1px solid var(--stroke);border-radius:14px;color:var(--txt);padding:15px;font-size:16px;outline:0;text-align:center}
.jump input::placeholder{color:var(--sub);font-size:13px}
.jump .jbtn{width:auto;min-width:88px;padding:0 18px;flex:0 0 auto;white-space:nowrap;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,var(--blue2),var(--blue));color:#fff;border-radius:14px;font-weight:700;font-size:15px;cursor:pointer}
.jump .jbtn:active{transform:scale(.97)}
.rb svg{display:block}
/* episodios: marcar visto (elegante) */
.ep{display:flex;align-items:center;gap:10px;position:relative;overflow:hidden;transition:opacity .2s}
.ep .epmain{flex:1;min-width:0}
.ep .epl .chk{color:var(--green);margin-right:7px;font-weight:800;display:none}
.ep .eye{flex:none;width:44px;height:44px;border-radius:11px;border:1px solid var(--stroke);background:rgba(255,255,255,.05);color:var(--sub);display:flex;align-items:center;justify-content:center;cursor:pointer;transition:.15s}
.ep .eye svg{display:block}
.ep .eye:active{transform:scale(.86)}
.ep.seen{opacity:.62}
.ep.seen::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--green)}
.ep.seen .epl{text-decoration:line-through;text-decoration-color:rgba(48,209,88,.55)}
.ep.seen .epl .chk{display:inline}
.ep.seen .eye{color:var(--green);border-color:rgba(48,209,88,.5);background:rgba(48,209,88,.14)}
.seas{display:flex;align-items:center;justify-content:space-between}
.seasmark{color:var(--blue2);font-size:12px;font-weight:600;text-transform:none;letter-spacing:0;cursor:pointer;padding:5px 10px;border-radius:9px;border:1px solid var(--stroke);background:rgba(255,255,255,.04)}
.seasmark:active{transform:scale(.95)}
.nprow .npf{color:#cfd6e4;font-weight:600}
.morebar{text-align:center;color:var(--sub);font-size:13px;padding:18px 10px}
.appfoot{text-align:center;color:var(--sub);font-size:12px;opacity:.6;padding:26px 10px 10px}
.appfoot a{color:var(--blue2);text-decoration:none}
/* ===== Esqueletos de carga (Inicio) ===== */
.skgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}
@media(max-width:430px){.skgrid{grid-template-columns:repeat(2,1fr)}}
.skcard{background:var(--card);border:1px solid var(--stroke);border-radius:14px;overflow:hidden}
.skph{aspect-ratio:2/3;background:#0e1320;position:relative;overflow:hidden}
.skln{height:11px;margin:9px 9px 0;border-radius:6px;background:#0e1320;position:relative;overflow:hidden}
.skln.s2{width:60%;height:9px;margin-bottom:9px}
.shim::after{content:"";position:absolute;inset:0;transform:translateX(-100%);background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent);animation:shm 1.15s infinite}
@keyframes shm{100%{transform:translateX(100%)}}
/* ===== Ficha enriquecida (hero + sinopsis + generos + trailer) ===== */
.sh-hero{position:relative;min-height:172px;background:#0e1320 center/cover no-repeat;border-radius:20px 20px 0 0}
.sh-hero .grad{position:absolute;inset:0;border-radius:20px 20px 0 0;background:linear-gradient(180deg,rgba(14,19,32,.12) 0%,rgba(14,19,32,.55) 55%,#0e1320 100%)}
.sh-hero .row{position:relative;display:flex;gap:14px;align-items:flex-end;padding:96px 16px 14px}
.sh-pst{width:84px;aspect-ratio:2/3;border-radius:10px;background:#0e1320 center/cover no-repeat;border:1px solid var(--stroke);box-shadow:0 8px 22px rgba(0,0,0,.6);flex:none;cursor:zoom-in}
.sh-pst.hidden{display:none}
.sh-htext{min-width:0;flex:1}
.sheet .sh-htext h3{margin:0;font-size:19px;font-weight:800;text-align:left;line-height:1.2;text-shadow:0 2px 10px rgba(0,0,0,.7)}
.sheet .sh-htext .sy{text-align:left;color:#cfd6e4;font-size:13px;font-weight:600;margin:5px 0 0}
.sh-body{padding:12px 18px calc(20px + env(safe-area-inset-bottom))}
.sh-body .sh-meta{justify-content:flex-start;margin:0 0 10px}
.sh-body .sh-meta:empty{margin:0}
.gtag{font-size:12px;font-weight:600;color:#cfd6e4;background:rgba(255,255,255,.06);border:1px solid var(--stroke);border-radius:999px;padding:5px 12px}
.runt{font-size:12.5px;color:var(--sub);font-weight:600;align-self:center}
.sh-ov{font-size:14px;line-height:1.5;color:#d4dae6;margin:2px 0 4px}
.sh-ov.clamp{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.sh-ovload{color:var(--sub);font-size:13px;margin:4px 0 8px}
.sh-more{color:var(--blue2);font-size:13px;font-weight:600;cursor:pointer;display:inline-block;margin:0 0 4px}
.btnrow{display:flex;gap:10px}
.btnrow .btn{margin-top:10px;flex:1}
.btn.trailer{background:rgba(255,255,255,.08);color:var(--txt);border:1px solid var(--stroke)}
/* zoom de portada (tocar la portada -> a pantalla) */
.zoom{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:48;padding:24px;cursor:zoom-out}
.zoom.on{display:flex}
/* Vuelta suave al sitio cuando se suelta el arrastre sin llegar al umbral */
.mwback{transition:transform .18s ease-out,opacity .18s ease-out}
/* Buscar dentro de una lista (con 57 titulos, bajar scrolleando era un rollo) */
.lfiltro{position:relative;margin:0 0 10px}
.lfiltro input{width:100%;background:var(--card);border:1px solid var(--stroke);
 color:var(--txt);border-radius:13px;padding:11px 38px 11px 14px;font-size:15px;
 font-family:inherit;outline:none;transition:border-color .15s}
.lfiltro input:focus{border-color:var(--blue)}
.lfiltro input::-webkit-search-cancel-button{display:none}
.lfx{position:absolute;right:6px;top:50%;transform:translateY(-50%);background:none;
 border:0;color:var(--sub);font-size:22px;line-height:1;padding:4px 8px;cursor:pointer}
/* Ultimas busquedas: repetir una sin volver a escribirla en el movil */
.rec{display:none;flex-wrap:wrap;gap:7px;margin:0 0 14px}
.rec.on{display:flex}
.rec-t{width:100%;font-size:12px;color:var(--sub);font-weight:600;margin-bottom:2px}
.recb{border:1px solid var(--stroke);background:var(--card);color:var(--txt);
 border-radius:999px;padding:8px 14px;font-size:13.5px;font-weight:600;cursor:pointer;
 display:inline-flex;align-items:center;gap:8px;max-width:100%}
.recb span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recb i{font-style:normal;color:var(--sub);font-size:15px;line-height:1;opacity:.7}
.recb:active{background:rgba(255,255,255,.12)}
/* Pestanas de temporada (la ficha trae la serie entera: hasta 35 capitulos) */
.tmp{display:flex;gap:7px;overflow-x:auto;-webkit-overflow-scrolling:touch;
 padding:2px 0 10px;scrollbar-width:none}
.tmp::-webkit-scrollbar{display:none}
.tmpb{flex:0 0 auto;min-height:40px;border:1px solid var(--stroke);background:var(--card);
 color:var(--sub);border-radius:999px;padding:0 16px;font-size:13.5px;font-weight:700;cursor:pointer;
 white-space:nowrap;transition:background .15s,color .15s,border-color .15s}
.tmpb.on{background:var(--blue);border-color:var(--blue);color:#fff}
.tmpb i{font-style:normal;opacity:.6;font-weight:600;margin-left:5px;font-size:12px}
.tmpb.on i{opacity:.85}
.tmpb.vista{color:var(--green)}
.tmpb.vista.on{color:#08210f}
/* Cuadros PROPIOS: los del navegador ensenan "...onrender.com dice" */
/* Fila de subtitulos/audio del mando: texto pequeno para que quepa */
.rsub .pill{font-size:15px;display:flex;align-items:center;justify-content:center;gap:6px}
.rsub .pill span{font-size:11.5px;font-weight:700;letter-spacing:-.2px}
.mwd{align-items:center;justify-content:center;padding:22px}
.mwd .box{max-width:420px;border-radius:22px;border:1px solid var(--stroke);
 padding:20px 20px 16px;animation:mwdIn .18s ease-out;overflow:visible}
@keyframes mwdIn{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:none}}
.mwd-t{font-size:17px;font-weight:800;line-height:1.3}
.mwd-s{font-size:13.5px;color:var(--sub);line-height:1.5;margin-top:7px}
.mwd-in{width:100%;margin-top:14px;background:rgba(255,255,255,.06);color:var(--txt);
 border:1px solid var(--stroke);border-radius:13px;padding:13px 14px;font-size:16px;
 font-family:inherit;outline:none;transition:border-color .15s}
.mwd-in:focus{border-color:var(--blue)}
.mwd-b{display:flex;gap:9px;justify-content:flex-end;margin-top:16px}
.mwd-b button{border:0;border-radius:13px;padding:12px 20px;font-size:14.5px;font-weight:700;cursor:pointer}
.mwd-x{background:rgba(255,255,255,.08);color:var(--txt)}
.mwd-ok{background:var(--blue);color:#fff;min-width:104px}
.mwd-ok.peligro{background:#ff453a}
/* Seleccion multiple en Mis listas (dejar pulsado) */
.selbar{display:none;align-items:center;justify-content:space-between;gap:10px;
 margin-bottom:10px;padding:9px 11px;border-radius:14px;background:rgba(10,132,255,.14);
 border:1px solid rgba(10,132,255,.42);font-size:13.5px;font-weight:700}
.selbar.on{display:flex}
.selb{display:flex;gap:7px;flex:0 0 auto}
.selb button{border:0;border-radius:11px;padding:9px 13px;font-size:13px;font-weight:700;cursor:pointer}
.selmv{background:var(--blue);color:#fff}
.selq{background:rgba(255,255,255,.10);color:var(--txt)}
.selx{background:transparent;color:var(--sub);font-size:17px;padding:6px 8px}
/* la cuadricula en modo seleccion: sin menu del navegador al dejar pulsado */
.picking .card{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
.card.picked .ph{outline:3px solid var(--blue);outline-offset:-3px;border-radius:12px}
.card.picked{opacity:.97}
/* la marca va CENTRADA sobre la caratula: en las esquinas se pisaba con la
   calidad, el corazon, la fuente y la etiqueta Pelicula/Serie */
.card .tick{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
 width:38px;height:38px;border-radius:50%;background:rgba(8,12,22,.62);
 border:2.5px solid rgba(255,255,255,.92);display:flex;align-items:center;
 justify-content:center;font-size:20px;font-weight:800;color:#fff;z-index:3;
 box-shadow:0 4px 14px rgba(0,0,0,.45)}
.card.picked .tick{background:var(--blue);border-color:var(--blue)}
/* Copia de seguridad (entrar con Google) */
.cuenta{margin:4px 14px 14px;padding:13px 14px;border:1px solid var(--stroke);
 border-radius:16px;background:var(--card)}
.cta-t{font-size:14.5px;font-weight:800;margin-bottom:4px}
.cta-s{font-size:12.5px;color:var(--sub);line-height:1.45;margin-bottom:11px}
.gbtn{min-height:44px;display:flex;justify-content:center}
.cta-who{display:flex;align-items:center;gap:11px}
.cta-who img{width:38px;height:38px;border-radius:50%;background:#131a2a;flex:0 0 auto}
.cta-nom{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.cta-nom b{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cta-nom span{font-size:11.5px;color:var(--sub)}
.cta-out{flex:0 0 auto;background:rgba(255,255,255,.08);border:1px solid var(--stroke);
 color:var(--txt);border-radius:12px;padding:9px 15px;font-size:13px;font-weight:700;cursor:pointer}
/* El corazón */
.hsvg{width:20px;height:20px;display:block;fill:none;stroke:#fff;stroke-width:1.9;
 stroke-linejoin:round;transition:fill .18s ease,stroke .18s ease}
.hsvg.on{fill:#ff375f;stroke:#ff375f}
.hsvg.pop{animation:hbeat .42s cubic-bezier(.2,1.6,.4,1)}
@keyframes hbeat{0%{transform:scale(.75)}45%{transform:scale(1.32)}70%{transform:scale(.94)}100%{transform:scale(1)}}
/* Hoja "Guardar en...": elegir lista de un toque */
.ls-head{display:flex;align-items:center;justify-content:space-between;
 padding:15px 16px 11px;border-bottom:1px solid var(--stroke)}
.ls-t{font-size:16px;font-weight:800}
.ls-x{background:none;border:0;color:var(--sub);font-size:26px;line-height:1;cursor:pointer;padding:0 4px}
.ls-body{padding:9px 12px 4px;max-height:52vh;overflow-y:auto}
.ls-row{display:flex;align-items:center;gap:12px;width:100%;text-align:left;
 background:var(--card);border:1px solid var(--stroke);border-radius:14px;
 padding:13px 14px;margin-bottom:8px;color:var(--txt);font-size:15px;font-weight:600;cursor:pointer}
.ls-row.on{border-color:#ff375f;background:rgba(255,55,95,.10)}
.ls-row .lsk{flex:0 0 auto;width:22px;height:22px;border-radius:50%;border:2px solid var(--stroke);
 display:flex;align-items:center;justify-content:center;font-size:13px;color:#fff}
.ls-row.on .lsk{background:#ff375f;border-color:#ff375f}
.ls-row .lsn{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ls-row .lsc{flex:0 0 auto;font-size:12px;color:var(--sub);font-weight:600}
.ls-quita{width:100%;background:transparent;border:1px solid var(--stroke);color:#ff6b6b;
 border-radius:14px;padding:12px;font-size:13.5px;font-weight:700;cursor:pointer;margin-top:2px}
.ls-foot{display:flex;gap:9px;padding:6px 12px 16px}
.ls-new{flex:1;background:rgba(255,255,255,.07);border:1px solid var(--stroke);color:var(--txt);
 border-radius:14px;padding:13px;font-size:14.5px;font-weight:700;cursor:pointer}
.ls-done{flex:0 0 auto;background:var(--blue);border:0;color:#fff;border-radius:14px;
 padding:13px 24px;font-size:14.5px;font-weight:800;cursor:pointer}
/* Mis listas: la barra de listas propias (se desliza si no caben) */
.lstbar{display:flex;gap:7px;overflow-x:auto;-webkit-overflow-scrolling:touch;
 padding:2px 0 8px;scrollbar-width:none}
.lstbar::-webkit-scrollbar{display:none}
.lstc{flex:0 0 auto;border:1px solid var(--stroke);background:var(--card);color:var(--sub);
 border-radius:999px;padding:8px 14px;font-size:13.5px;font-weight:600;cursor:pointer;
 white-space:nowrap;transition:background .15s,color .15s,border-color .15s}
.lstc.on{background:var(--blue);border-color:var(--blue);color:#fff}
.lstc b{font-weight:700;opacity:.65;margin-left:5px;font-size:12px}
.lstc.on b{opacity:.85}
.lstc.add{color:var(--blue);font-weight:700}
/* Poner nombre / quitar la lista abierta: al lado de ella, sin menus */
.lstb{flex:0 0 auto;border:1px solid var(--stroke);background:var(--card);
 border-radius:999px;width:38px;height:38px;font-size:15px;cursor:pointer;
 color:var(--sub);display:flex;align-items:center;justify-content:center;padding:0}
.lstb:active{background:rgba(255,255,255,.12)}
/* Historial: lo último mandado a la tele, para repetirlo de un toque */
.histbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px;
 font-size:12.5px;color:var(--sub);min-height:30px}
.hitem{display:flex;align-items:center;gap:11px;padding:9px;border-radius:14px;
 background:var(--card);border:1px solid var(--stroke);margin-bottom:9px}
.hitem .hp{width:44px;height:64px;flex:0 0 auto;border-radius:8px;background:#131a2a center/cover no-repeat;
 display:flex;align-items:center;justify-content:center;font-size:17px;opacity:.98}
.hitem .hp.np::after{content:'🎬';opacity:.35}
.hitem .hm{flex:1;min-width:0}
.hitem .ht{font-size:14px;font-weight:700;line-height:1.25;
 display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.hitem .hs{font-size:11.5px;color:var(--sub);margin-top:3px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.hitem .hsrc{font-weight:700;font-size:10.5px;padding:1px 7px;border-radius:999px;background:rgba(255,255,255,.08)}
.hitem .hsrc.dt{color:#0a84ff}.hitem .hsrc.et{color:#ff9f0a}
.hitem .hsrc.dx{color:#30d158}.hitem .hsrc.wf{color:#bf5af2}
.hitem .hb{flex:0 0 auto;display:flex;gap:7px}
.hitem button{border:0;border-radius:12px;padding:10px 13px;font-size:15px;cursor:pointer;
 background:rgba(255,255,255,.08);color:var(--txt)}
.hitem button.play{background:var(--green);color:#08210f;font-weight:800}
/* Progreso de la búsqueda: qué fuente ha contestado y cuánto lleva.
   OJO: se llama `srcp` y NO `prog` porque `.prog` ya es la barra del
   reproductor (height:5px + overflow:hidden) y recortaba esto a una rayita. */
.srcp{display:none;margin:10px 0 14px;padding:11px 12px 12px;border-radius:14px;
 background:rgba(255,255,255,.045);border:1px solid var(--stroke)}
.srcp.on{display:block;animation:srcpIn .22s ease}
@keyframes srcpIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.srcp-top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:9px}
.srcp-ttl{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;color:var(--txt)}
.srcp-sec{font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums;flex:0 0 auto}
.srcp-x{color:var(--sub);font-weight:500}
.srcp-bar{height:5px;border-radius:5px;background:rgba(255,255,255,.09);overflow:hidden}
.srcp-bar i{display:block;height:100%;width:0;border-radius:5px;
 background:linear-gradient(90deg,var(--blue2),var(--blue));
 box-shadow:0 0 10px rgba(10,132,255,.5);transition:width .45s cubic-bezier(.4,0,.2,1)}
/* Rejilla: en el movil salen 2x2 (alineados, legibles) y en pantalla
   ancha las cuatro en fila. Con flex-wrap quedaban 3+1, descolocados. */
.srcp-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
 gap:6px;margin-top:10px}
.srcp-f{display:inline-flex;align-items:center;gap:5px;padding:4px 9px;border-radius:999px;
 font-size:11px;font-weight:600;letter-spacing:.1px;white-space:nowrap;
 background:rgba(255,255,255,.05);color:var(--sub);
 border:1px solid transparent;transition:all .25s ease}
.srcp-f i{width:7px;height:7px;border-radius:50%;background:var(--c);flex:0 0 auto}
.srcp-f.on{color:var(--txt);background:rgba(255,255,255,.09);border-color:rgba(255,255,255,.12)}
.srcp-f b{color:var(--c);font-weight:800;margin-left:1px}
.srcp-f.wait i{animation:srcpPulse 1.1s ease-in-out infinite}
@keyframes srcpPulse{0%,100%{opacity:.3;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
.srcp-f.dt{--c:#0a84ff}.srcp-f.et{--c:#ff9f0a}
.srcp-f.dx{--c:#30d158}.srcp-f.wf{--c:#bf5af2}
.srcp-f.zero i{opacity:.35}
/* Versiones de la misma peli en otras fuentes (la 4K de WolfMax, sobre todo) */
.sh-alts{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 10px}
.sh-alts .altb{min-height:38px;display:inline-flex;align-items:center;gap:5px;
 background:rgba(255,255,255,.07);border:1px solid var(--stroke);
 color:var(--txt);border-radius:999px;padding:6px 11px;font-size:12.5px;font-weight:600;
 cursor:pointer;display:flex;align-items:center;gap:5px}
.sh-alts .altb.on{background:var(--blue);border-color:var(--blue);color:#fff}
.sh-alts .altq{opacity:.75;font-weight:500}
.zoom img{max-width:100%;max-height:100%;border-radius:14px;box-shadow:0 16px 50px rgba(0,0,0,.7)}
/* modal de trailer */
.trm{position:fixed;inset:0;background:rgba(0,0,0,.95);display:none;align-items:center;justify-content:center;z-index:50;padding:14px}
.trm.on{display:flex}
.trm .frame{width:100%;max-width:980px;aspect-ratio:16/9;border-radius:14px;overflow:hidden;background:#000;position:relative}
.trm #trm-mount{position:absolute;inset:0}
.trm iframe{position:absolute;inset:0;width:100%;height:100%;border:0;display:block}
.trm .x{position:fixed;top:calc(12px + env(safe-area-inset-top));right:14px;z-index:51;border:1px solid var(--stroke);background:rgba(14,19,32,.96);color:#fff;font-weight:700;border-radius:999px;padding:9px 17px;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.5)}
/* ===== Ficha de SERIE enriquecida (overlay) ===== */
.ovhero{position:relative;margin:-14px -14px 14px;min-height:188px;background:#0e1320 center/cover no-repeat}
.ovhero .grad{position:absolute;inset:0;background:linear-gradient(180deg,rgba(6,7,12,.10) 0%,rgba(6,7,12,.55) 55%,var(--bg) 100%)}
.ovhero-row{position:relative;display:flex;gap:14px;align-items:flex-end;padding:104px 14px 14px}
.ovhero .ovposter{width:96px;flex:none;margin:0;cursor:zoom-in}
.ovhero-txt{min-width:0;flex:1}
.ovhero-txt .ovh-t{text-shadow:0 2px 10px rgba(0,0,0,.75)}
.ovgen{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}
.ovsyn{margin:0 0 14px}
.ovactions{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
/* "Seguir por 2x04": lo que uno quiere hacer casi siempre al abrir una serie */
.ovseguir{display:flex;align-items:center;gap:9px;background:var(--green);color:#08210f;
 border:0;border-radius:14px;padding:13px 18px;font-size:15px;font-weight:800;
 cursor:pointer;margin:0 0 12px;box-shadow:0 6px 18px rgba(48,209,88,.22);
 transition:transform .12s ease-out}
.ovseguir:active{transform:scale(.98)}
.ovseguir b{font-weight:800}
.ovseguir span{font-weight:600;opacity:.75;font-size:13px}
</style></head><body>
<div class="wrap">
 <div class="top">
  <div class="brand"><span class="d">🐺</span> MejorWolf</div>
  <button id="devbtn" class="devbtn" type="button" onclick="openDevs()" title="Mis Kodis (guarda varios códigos con nombre)">
   <span id="devname">código</span><span class="devcar">▾</span></button>
  <input id="code" type="hidden">
 </div>
 <div class="tabs">
  <button id="tab-inicio" class="tab on" onclick="goView('inicio')">Inicio</button>
  <button id="tab-buscar" class="tab" onclick="goView('buscar')">Buscar</button>
  <button id="tab-lista" class="tab" onclick="goView('lista')">Mis listas</button>
  <button id="tab-hist" class="tab" onclick="goView('hist')">Historial</button>
 </div>
 <div class="srclegend">
  <span><i style="background:#4a9eff"></i>DonTorrent</span>
  <span><i style="background:#ff9f6e"></i>EliteTorrent</span>
  <span><i style="background:#34d36a"></i>DivxTotal</span>
  <span><i style="background:#c77dff"></i>WolfMax</span>
 </div>
 <div id="shared" class="shared"><div class="shared-in">
  <div><div class="shared-lab">Te han compartido</div><div class="shared-t" id="shared-t"></div></div>
  <button class="shared-btn" onclick="playShared()">▶ Reproducir</button>
  <button class="shared-x" onclick="closeShared()">&times;</button>
 </div></div>
 <section id="pane-inicio" class="pane">
  <div class="chips">
   <button class="chip on" data-k="estrenos" onclick="chip('estrenos')">Estrenos</button>
   <button class="chip" data-k="peliculas" onclick="chip('peliculas')">Cine</button>
   <button class="chip" data-k="series" onclick="chip('series')">Series</button>
  </div>
  <div id="inicio-grid" class="msg"></div>
 </section>
 <section id="pane-buscar" class="pane hidden">
  <div class="search">
   <input id="q" type="search" placeholder="Buscar película o serie..." autocomplete="off">
   <button onclick="go()">Buscar</button>
  </div>
  <div id="buscar-rec" class="rec"></div>
  <div id="buscar-prog" class="srcp"></div>
  <div id="buscar-grid" class="msg">Busca pelis y series y envíalas a tu tele 📺</div>
  <div id="buscar-more" class="morebar"></div>
 </section>
 <section id="pane-hist" class="pane hidden">
  <div class="histbar"><span id="hist-n"></span>
   <button class="vtog" id="hist-clear" onclick="histClear()" style="display:none">🗑 Vaciar historial</button></div>
  <div id="hist-list" class="msg"></div>
 </section>
 <section id="pane-lista" class="pane hidden">
  <div class="lstbar" id="lstbar"></div>
  <div class="selbar" id="selbar">
   <span id="sel-n">0 seleccionados</span>
   <div class="selb">
    <button class="selmv" onclick="selMover()">Mover a…</button>
    <button class="selq" onclick="selQuitar()">Quitar</button>
    <button class="selx" onclick="selSalir()">✕</button>
   </div>
  </div>
  <div class="lfiltro" id="lfiltro" style="display:none">
   <input id="lfin" type="search" placeholder="Buscar en esta lista…" autocomplete="off"
    oninput="lstFiltra()" onsearch="lstFiltra()">
   <button class="lfx" id="lfx" onclick="lstFiltraLimpia()" style="display:none">×</button>
  </div>
  <div class="listbar">
   <button class="vtog" id="vtog" onclick="toggleView()" style="display:none">☰ Vista lista</button></div>
  <div id="lista-grid" class="msg"></div>
 </section>
 <div class="appfoot">MejorWolf · <a href="/kb/clasico">Mando clásico</a></div>
</div>
<div class="npbar" id="npbar" onclick="openRemote()">
 <div class="np-prog-wrap"><div class="np-prog" id="np-prog"></div></div>
 <div class="np-row"><div class="np-t" id="np-t"></div>
  <button class="np-pp" id="np-pp" onclick="event.stopPropagation();pp()"><svg width="15" height="15" viewBox="0 0 24 24"><rect x="6" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/></svg></button></div>
</div>
<div class="sheet mwd" id="mwdlg" onclick="if(event.target===this)mwdNo()">
 <div class="box">
  <div class="mwd-t" id="mwd-t"></div>
  <div class="mwd-s" id="mwd-s"></div>
  <input id="mwd-in" class="mwd-in" autocomplete="off" spellcheck="false">
  <div class="mwd-b">
   <button class="mwd-x" onclick="mwdNo()">Cancelar</button>
   <button class="mwd-ok" id="mwd-ok" onclick="mwdSi()">Aceptar</button>
  </div>
 </div>
</div>
<div class="sheet" id="lsheet" onclick="if(event.target===this)closeLS()">
 <div class="box">
  <div class="ls-head"><div class="ls-t" id="ls-t">Guardar en…</div>
   <button class="ls-x" onclick="closeLS()">&times;</button></div>
  <div class="ls-body" id="ls-body"></div>
  <div class="ls-foot">
   <button class="ls-new" onclick="lsNueva()">+ Crear lista nueva</button>
   <button class="ls-done" onclick="closeLS()">Hecho</button>
  </div>
 </div>
</div>
<div class="sheet" id="sheet" onclick="if(event.target===this)closeSheet()">
 <div class="box">
  <div class="sh-hero" id="sh-hero"><div class="grad"></div>
   <div class="row">
    <div class="sh-pst hidden" id="sh-poster" onclick="zoomPoster()"></div>
    <div class="sh-htext"><h3 id="sh-t"></h3><div class="sy" id="sh-y"></div></div>
   </div>
  </div>
  <div class="sh-body">
   <div class="sh-meta" id="sh-seeds"></div>
   <div class="sh-alts" id="sh-alts"></div>
   <div class="sh-meta" id="sh-genres"></div>
   <div id="sh-ovwrap"></div>
   <div class="rar" id="sh-rar"></div>
   <button class="btn play" onclick="play()">▶ Reproducir en la tele</button>
   <div class="btnrow">
    <button class="btn fav" id="sh-fav" onclick="sheetFav()">♡ Guardar en una lista</button>
    <button class="btn trailer" id="sh-trailer" style="display:none" onclick="openTrailer()">🎬 Tráiler</button>
   </div>
   <button class="btn share" onclick="shareSheet()">📤 Compartir enlace</button>
   <button class="btn cancel" onclick="closeSheet()">Cancelar</button>
  </div>
 </div>
</div>
<div class="ov" id="ov">
 <div class="ovbar"><button class="ovback" onclick="closeOv()">‹ Volver</button>
  <div class="ovt" id="ov-title"></div></div>
 <div id="ov-body"></div>
</div>
<div class="remote" id="remote">
 <div class="ovbar"><button class="ovback" onclick="closeRemote()">‹ Catálogo</button>
  <div class="ovt">Mando</div></div>
 <div class="rmwrap">
  <div class="rnp"><div class="nplab">ESTÁS VIENDO</div>
   <div class="npttl" id="rm-t">—</div>
   <div class="rmbar"><i id="rm-prog"></i></div>
   <div class="nprow"><span id="rm-time">0:00</span><span id="rm-fin" class="npf"></span></div>
  </div>
  <div class="media">
   <div class="rb sk" onclick="cmd('seek_back')">-10<small>s</small></div>
   <div class="rb stop" onclick="cmd('stop')"><svg width="22" height="22" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/></svg></div>
   <div class="rb play" id="rm-pp" onclick="pp()"><svg width="30" height="30" viewBox="0 0 24 24"><path d="M8 6 L18 12 L8 18 Z" fill="currentColor"/></svg></div>
   <div class="rb sk" onclick="cmd('seek_fwd')">+30<small>s</small></div>
  </div>
  <div class="jump">
   <input id="rm-min" type="number" min="0" inputmode="numeric" placeholder="ir al minuto exacto...">
   <div class="jbtn" onclick="seekTo()">Saltar a</div>
  </div>
  <div class="row3">
   <div class="pill" onclick="cmd('voldown')">🔉</div>
   <div class="pill" onclick="cmd('mute')">🔇</div>
   <div class="pill" onclick="cmd('volup')">🔊</div>
  </div>
  <div class="row3 rsub">
   <div class="pill" onclick="cmd('subs')" title="Quitar o poner los subtitulos">💬 <span>Subtítulos</span></div>
   <div class="pill" onclick="cmd('subsnext')" title="Otra pista de subtitulos">🔄 <span>Otro sub</span></div>
   <div class="pill" onclick="cmd('audionext')" title="Otra pista de audio">🗣️ <span>Audio</span></div>
  </div>
  <div class="padwrap"><div class="pad">
   <div class="arrow up" onclick="cmd('up')">▲</div>
   <div class="arrow left" onclick="cmd('left')">◀</div>
   <div class="ok" onclick="cmd('ok')">OK</div>
   <div class="arrow right" onclick="cmd('right')">▶</div>
   <div class="arrow down" onclick="cmd('down')">▼</div>
  </div></div>
  <div class="navrow2">
   <div class="pill" onclick="cmd('home')">🏠 Inicio</div>
   <div class="pill" onclick="cmd('back')">↩ Atrás</div>
  </div>
 </div>
 <button class="rm-close" onclick="closeRemote()">✕ Cerrar</button>
</div>
<button class="fab" id="fab" onclick="openRemote()" aria-label="Abrir mando"><svg width="26" height="26" viewBox="0 0 24 24" fill="none"><rect x="7" y="2" width="10" height="20" rx="3" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="6.5" r="1.5" fill="currentColor"/><line x1="9.6" y1="11" x2="14.4" y2="11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><line x1="9.6" y1="14" x2="14.4" y2="14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><line x1="9.6" y1="17" x2="14.4" y2="17" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
<div class="zoom" id="zoom" onclick="closeZoom()"><img id="zoom-img" alt=""></div>
<div class="trm" id="trm" onclick="if(event.target===this)closeTrailer()">
 <div class="frame"><button class="x" onclick="closeTrailer()">✕ Cerrar</button><div id="trm-mount"></div></div>
</div>
<div class="sheet" id="devsheet" onclick="if(event.target===this)closeDevs()">
 <div class="box">
  <div class="devsheet-h">Mis Kodis <button class="devsheet-x" onclick="closeDevs()">✕</button></div>
  <div class="devsub">Guarda el código de cada tele/dispositivo y elige a cuál mandar.</div>
  <div class="cuenta" id="cuenta">
   <div class="cta-t">Copia de seguridad</div>
   <div class="cta-s" id="cta-s">Entra con Google y tus listas, tu historial y tus Kodis
    quedan guardados: los recuperas en cualquier móvil.</div>
   <div id="gbtn" class="gbtn"></div>
   <div class="cta-who" id="cta-who" style="display:none">
    <img id="cta-img" alt="">
    <div class="cta-nom"><b id="cta-nom"></b><span id="cta-est"></span></div>
    <button class="cta-out" onclick="syncSalir()">Salir</button>
   </div>
  </div>
  <div class="devlist" id="devlist"></div>
  <div class="devadd">
   <input id="devn" class="devin" placeholder="Nombre (Salón, Tablet, PC…)" maxlength="24" autocomplete="off">
   <input id="devc" class="devin devc" inputmode="numeric" maxlength="6" placeholder="código de 6 cifras" autocomplete="off">
   <button class="devsave" onclick="addDev()">Guardar Kodi</button>
  </div>
 </div>
</div>
<div class="toast" id="toast"></div>
<script>
var $=function(s){return document.getElementById(s)};
var SVG_PLAY='<svg width="30" height="30" viewBox="0 0 24 24"><path d="M8 6 L18 12 L8 18 Z" fill="currentColor"/></svg>';
var SVG_PAUSE='<svg width="28" height="28" viewBox="0 0 24 24"><rect x="6" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/></svg>';
var NP_PLAY='<svg width="16" height="16" viewBox="0 0 24 24"><path d="M8 6 L18 12 L8 18 Z" fill="currentColor"/></svg>';
var NP_PAUSE='<svg width="15" height="15" viewBox="0 0 24 24"><rect x="6" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/></svg>';
var EYE_OFF='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
var EYE_ON='<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 4.5C6 4.5 2.2 11.2 2.05 11.5a1 1 0 0 0 0 .9C2.2 12.8 6 19.5 12 19.5s9.8-6.7 9.95-7a1 1 0 0 0 0-.9C21.8 11.2 18 4.5 12 4.5Zm0 11a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7Z"/></svg>';
function clk(d){return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}
var code=$('code'), favs=[], LISTS={inicio:[],buscar:[],lista:[]}, sel=null, npTimer=null, EPS={}, SHOW='', lastPlayTs=0, npPaused=false;
var ZPOSTER='', TRK='';   // portada para el zoom + clave del trailer (peli/serie)
var INI={kind:'estrenos',page:1,loading:false,more:true}, OVDATA=null;
try{var u=new URLSearchParams(location.search).get('c');if(u)localStorage.setItem('mw_code',u.replace(/\D/g,'').slice(0,6));}catch(e){}
code.value=(localStorage.getItem('mw_code')||'').replace(/\D/g,'').slice(0,6);
// Migracion suave: si ya habia un codigo de siempre pero aun no hay lista de
// Kodis, lo sembramos como "Mi Kodi" -> el dispositivo no se pierde al estrenar
// el selector (loadDevs/saveDevs/refreshDevBtn son declaraciones -> ya hoisted).
(function(){try{var d=loadDevs();if(!d.length&&code.value.length===6)saveDevs([{name:'Mi Kodi',code:code.value}]);}catch(e){}})();
refreshDevBtn();
try{favs=JSON.parse(localStorage.getItem('mw_fav')||'[]')||[]}catch(e){favs=[]}
function saveFavs(){try{localStorage.setItem('mw_fav',JSON.stringify(favs))}catch(e){}
 try{syncSubirPronto()}catch(e){}}
// ===== MIS LISTAS =====================================================
// Varias listas propias. El almacen sigue siendo `favs` (que ya se sincroniza
// entre dispositivos por el codigo): lo unico nuevo es a que listas pertenece
// cada titulo, en `f.ls`. Un favorito de antes, sin `ls`, es de la primera
// lista -> ninguno se pierde al estrenar esto.
var LST=[];try{LST=JSON.parse(localStorage.getItem('mw_listas')||'[]')||[]}catch(e){LST=[]}
if(!LST.length)LST=[{id:'def',n:'Mi lista'}];
function lstSave(){try{localStorage.setItem('mw_listas',JSON.stringify(LST))}catch(e){}
 try{syncSubirPronto()}catch(e){}}
var LSTSEL='';try{LSTSEL=localStorage.getItem('mw_lista_sel')||''}catch(e){}
if(!LST.some(function(l){return l.id===LSTSEL}))LSTSEL=LST[0].id;
function lstSel(id){LSTSEL=id;try{localStorage.setItem('mw_lista_sel',id)}catch(e){}}
function lstNombre(id){for(var i=0;i<LST.length;i++)if(LST[i].id===id)return LST[i].n;return ''}
// A que listas pertenece un titulo. Solo cuentan las listas que EXISTEN aqui:
// si llega de otro movil con una lista que este no conoce (o se borro), cae en
// la primera en vez de quedarse invisible. Sin `ls` (favorito de antes) = la
// primera tambien. Asi es imposible que un guardado desaparezca de la vista.
function lstDe(f){
 var a=((f&&f.ls)||[]).filter(function(id){
  return LST.some(function(l){return l.id===id})});
 return a.length?a:[LST[0].id]}
function lstItems(id){return favs.filter(function(f){return lstDe(f).indexOf(id)>=0})}
// Lo guardado ANTES de que las listas fueran exclusivas puede estar en varias:
// se queda en la ULTIMA a la que se mando (la que el usuario eligio despues).
(function(){try{var c=false;
 favs.forEach(function(f){if(f&&f.ls&&f.ls.length>1){f.ls=[f.ls[f.ls.length-1]];c=true}});
 if(c)saveFavs();}catch(e){}})();
function lstNueva(nombre){
 var n=(nombre||'').trim();if(!n)return null;
 var id='l'+Date.now().toString(36)+Math.random().toString(36).slice(2,5);
 LST.push({id:id,n:n.slice(0,40)});lstSave();return id;}
lstSave();
var seen=[];try{seen=JSON.parse(localStorage.getItem('mw_seen')||'[]')||[]}catch(e){seen=[]}
function saveSeen(){try{localStorage.setItem('mw_seen',JSON.stringify(seen))}catch(e){}}
function isSeen(id){return seen.indexOf(String(id))>=0}
function toggleSeen(id){id=String(id);var i=seen.indexOf(id);if(i>=0)seen.splice(i,1);else seen.unshift(id);saveSeen()}
function kindLabel(k){return k==='serie'?'Serie':(k==='doc'?'Documental':'Película')}
function fk(x){return x.kind+':'+x.content_id}
function isFav(x){return favs.some(function(f){return fk(f)===fk(x)})}
// Los capítulos que ya venían con la tarjeta se guardan CON el favorito: si no,
// abrirlo desde Mi lista obliga a pedirlos otra vez por red (29s medidos, al
// borde de lo que espera la web -> "nunca carga"). Recortados a lo justo.
function slimEps(a){return (a||[]).slice(0,60).map(function(e){
 return {label:e.label,season:e.season,episode:e.episode,quality:e.quality,
         url:e.url,content_id:e.content_id,link:e.link,src:e.src}})}
function slimAlts(a){return (a||[]).slice(0,4).map(function(z){
 return {source:z.source,quality:z.quality,url:z.url,content_id:z.content_id,
         tabla:z.tabla,path:z.path,kind:z.kind,title:z.title,year:z.year,
         poster:z.poster,rating:z.rating,eps:slimEps(z.eps)}})}
// Copia guardable de un item (lo mismo que guardaba toggleFav).
function favCopia(x,ls){return {kind:x.kind,content_id:x.content_id,tabla:x.tabla,path:x.path,title:x.title,poster:x.poster,year:x.year,rating:x.rating,source:x.source,url:x.url,quality:x.quality,overview:x.overview,backdrop:x.backdrop,genres:x.genres,tmdb_id:x.tmdb_id,trailer:x.trailer,runtime:x.runtime,eps:slimEps(x.eps),epsAlt:slimEps(x.epsAlt),alts:slimAlts(x.alts),ls:ls}}
function favBuscar(x){for(var i=0;i<favs.length;i++)if(fk(favs[i])===fk(x))return favs[i];return null}
function favEnLista(x,id){var f=favBuscar(x);return !!f&&lstDe(f).indexOf(id)>=0}
// Guardar en una lista = MOVER a esa lista. Un titulo esta en UNA lista y solo
// una: si estaba en "Mi lista" y lo mandas a "Pendiente", desaparece de "Mi
// lista" en el acto (es como lo quiere el dueno, y es lo que uno espera).
function favAdd(x,id){
 var f=favBuscar(x);
 if(!f){favs.unshift(favCopia(x,[id]))}
 else{f.ls=[id];}
 saveFavs();mlPushSoon();}
// Quitar de UNA lista; si no queda en ninguna, deja de estar guardado.
function favQuitar(x,id){
 var f=favBuscar(x);if(!f)return;
 var a=lstDe(f).filter(function(z){return z!==id});
 if(a.length){f.ls=a}else{favs=favs.filter(function(z){return fk(z)!==fk(x)})}
 saveFavs();mlPushSoon();}
function favQuitarTodo(x){favs=favs.filter(function(z){return fk(z)!==fk(x)});saveFavs();mlPushSoon();}
function toggleFav(x){if(isFav(x)){favs=favs.filter(function(f){return fk(f)!==fk(x)})}else{favs.unshift({kind:x.kind,content_id:x.content_id,tabla:x.tabla,path:x.path,title:x.title,poster:x.poster,year:x.year,rating:x.rating,source:x.source,url:x.url,quality:x.quality,overview:x.overview,backdrop:x.backdrop,genres:x.genres,tmdb_id:x.tmdb_id,trailer:x.trailer,runtime:x.runtime,eps:slimEps(x.eps),epsAlt:slimEps(x.epsAlt),alts:slimAlts(x.alts)})}saveFavs();mlPushSoon()}
// --- Sincronizacion de la lista de deseados (espejo en el relay, ligado al
// codigo). El movil es la COPIA MAESTRA: al cargar hacemos UNION (nunca borra ->
// imposible perder la lista). Cero peticiones a fuentes -> cero baneo. ---
var _mlPushT=null;
function mlPush(){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6)return;
 // Sin los capítulos: la lista compartida entre dispositivos se queda ligera
 // (y el que la reciba los pide por red, que funciona igual).
 var slim=favs.map(function(f){var c={};for(var k in f)if(k!=='eps'&&k!=='epsAlt'&&k!=='alts')c[k]=f[k];return c});
 try{fetch('/mylist?code='+cd,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({list:slim,listas:LST})}).catch(function(){})}catch(e){}}
function mlPushSoon(){clearTimeout(_mlPushT);_mlPushT=setTimeout(mlPush,1500)}
function mlSync(){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6)return;
 fetch('/mylist?code='+cd).then(function(r){return r.json()}).then(function(d){
  var rl=(d&&d.list)||[];var changed=false;
  rl.forEach(function(it){if(it&&it.content_id&&!favs.some(function(f){return fk(f)===fk(it)})){favs.push(it);changed=true}});
  // Los nombres de las listas tambien: si esta tablet no conoce una lista que
  // el movil creo, se anade (nunca se borra ninguna -> imposible perder).
  var rls=(d&&d.listas)||[];
  rls.forEach(function(l){if(l&&l.id&&l.n&&!LST.some(function(z){return z.id===l.id})){
    LST.push({id:l.id,n:l.n});changed=true}});
  if(changed){saveFavs();lstSave();if(CURVIEW==='lista')renderFavs()}
  mlPush();
 }).catch(function(){})}
function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function toast(t){var e=$('toast');e.textContent=t;e.classList.add('on');clearTimeout(e._t);e._t=setTimeout(function(){e.classList.remove('on')},2800)}
// ---- Mis Kodis: varios codigos guardados con nombre (Salon, Tablet, PC...) ----
// El codigo ACTIVO sigue en localStorage 'mw_code' y en el input oculto #code, asi
// TODA la logica de siempre (play/lista/mando/mlSync) no cambia. Aqui solo gestionamos
// la lista 'mw_devices' y cual esta activo. Cero peticiones a fuentes -> cero baneo.
function loadDevs(){try{return JSON.parse(localStorage.getItem('mw_devices')||'[]')||[]}catch(e){return []}}
function saveDevs(d){try{localStorage.setItem('mw_devices',JSON.stringify(d))}catch(e){}
 try{syncSubirPronto()}catch(e){}}
function devName(c){var d=loadDevs();for(var i=0;i<d.length;i++){if(d[i].code===c)return d[i].name||''}return ''}
function refreshDevBtn(){var el=$('devname');if(!el)return;var c=(code.value||'').replace(/\D/g,'');
 el.textContent=devName(c)||(c.length===6?c:'código')}
function setActiveCode(c){c=(c||'').replace(/\D/g,'').slice(0,6);code.value=c;
 try{localStorage.setItem('mw_code',c)}catch(e){}
 refreshDevBtn();if(c.length===6){try{mlSync()}catch(e){}}}
function openDevs(){var cur=(code.value||'').replace(/\D/g,'');
 try{syncPinta();if(!syncOn())gsiCarga()}catch(e){}
 var dc=$('devc');if(dc)dc.value=(cur.length===6&&!devName(cur))?cur:'';
 var dn=$('devn');if(dn)dn.value='';
 renderDevs();$('devsheet').classList.add('on');mwOpen('devs',$('devsheet').querySelector('.box'),_closeDevs)}
function _closeDevs(){$('devsheet').classList.remove('on')}
function closeDevs(){mwBack('devs')}
function liveDot(dot,c){fetch('/kb/status?code='+c).then(function(r){return r.json()}).then(function(j){
 dot.className='devdot '+((j&&j.connected)?'on':'off')}).catch(function(){})}
function renderDevs(){var wrap=$('devlist');if(!wrap)return;var d=loadDevs();var cur=(code.value||'').replace(/\D/g,'');
 if(!d.length){wrap.innerHTML='<div class="devempty">Aún no has guardado ningún Kodi.<br>Añade tu salón, tablet o PC aquí abajo 👇</div>';return}
 wrap.innerHTML='';
 d.forEach(function(dev){
  var row=document.createElement('div');row.className='devrow'+(dev.code===cur?' on':'');
  row.onclick=function(){pickDev(dev.code)};
  var dot=document.createElement('span');dot.className='devdot';row.appendChild(dot);
  var meta=document.createElement('div');meta.className='devmeta';
  var nm=document.createElement('div');nm.className='devnm';nm.textContent=dev.name||'Kodi';meta.appendChild(nm);
  var cc=document.createElement('div');cc.className='devcc';cc.textContent=dev.code;meta.appendChild(cc);
  row.appendChild(meta);
  var ed=document.createElement('button');ed.className='deved';ed.title='Editar nombre';ed.textContent='✏️';
  ed.onclick=function(e){e.stopPropagation();editDev(dev.code)};
  row.appendChild(ed);
  var del=document.createElement('button');del.className='devdel';del.title='Borrar';del.textContent='🗑';
  del.onclick=function(e){e.stopPropagation();delDev(dev.code)};
  row.appendChild(del);
  wrap.appendChild(row);liveDot(dot,dev.code)})}
function editDev(c){var d=loadDevs(),dev=null;
 for(var i=0;i<d.length;i++){if(d[i].code===c){dev=d[i];break}}
 if(!dev)return;
 mwPrompt('Nombre de este Kodi',dev.name||'','Salón, Comedor, Tablet…',
  function(nn){dev.name=nn.slice(0,24);saveDevs(d);refreshDevBtn();renderDevs();
   toast('Nombre actualizado ✓')});}
function pickDev(c){setActiveCode(c);renderDevs();toast('Kodi activo: '+(devName(c)||c));setTimeout(closeDevs,220)}
function addDev(){var n=($('devn').value||'').trim();var c=($('devc').value||'').replace(/\D/g,'').slice(0,6);
 if(c.length!==6){toast('El código debe tener 6 cifras');return}
 if(!n){toast('Ponle un nombre (Salón, Tablet…)');try{$('devn').focus()}catch(e){}return}
 var d=loadDevs(),found=false;
 d.forEach(function(dev){if(dev.code===c){dev.name=n;found=true}});
 if(!found)d.push({name:n,code:c});
 saveDevs(d);$('devn').value='';$('devc').value='';
 setActiveCode(c);renderDevs();toast(found?'Kodi actualizado ✓':'Kodi guardado ✓')}
function delDev(c){
 mwConfirm('¿Quitar este Kodi?','Solo se quita de la lista de este móvil; el Kodi sigue funcionando.',
  'Quitar',function(){
   saveDevs(loadDevs().filter(function(dev){return dev.code!==c}));
   renderDevs();refreshDevBtn();toast('Kodi quitado')},1);}
function star(x){return (x.year||'')+(x.rating?(' · ★'+(Math.round(x.rating*10)/10)):'')}
// ====== GESTO / BOTON DE RETROCESO (movil) ======
// Cada panel que se abre EMPUJA un estado en el historial, asi el gesto de
// volver atras del movil (y el boton Atras de Android) CIERRA ese panel en vez
// de salir de la app. Ademas se puede arrastrar desde el borde izquierdo con el
// panel siguiendo al dedo: en iOS instalada como app no hay gesto del sistema,
// y asi el comportamiento es el mismo en los dos.
var MWL=[];   // pila de paneles abiertos (el ultimo es el de arriba)
var MWC={};   // nombre -> cierre CRUDO (sin tocar el historial)
function mwTop(){return MWL.length?MWL[MWL.length-1]:null}
function mwHas(n){for(var i=0;i<MWL.length;i++)if(MWL[i].n===n)return true;return false}
function mwOpen(n,el,closer){
 MWC[n]=closer;
 if(mwHas(n))return;                 // reabrir el mismo panel no apila otro estado
 var h=1;try{history.pushState({mw:n},'')}catch(e){h=0}
 MWL.push({n:n,el:el||null,c:closer,h:h});}
// Cierre desde la INTERFAZ (boton, tocar fuera, enviar a la tele...): si ese
// panel es el ultimo y tiene estado, se deshace el estado y el popstate hace el
// cierre -> el historial NUNCA se desincroniza (sin esto quedarian estados
// muertos y habria que dar varias veces atras para salir de la app).
function mwBack(n){
 var t=mwTop();
 if(t&&t.n===n&&t.h){try{history.back();return}catch(e){}}
 for(var i=MWL.length-1;i>=0;i--)if(MWL[i].n===n){var c=MWL[i].c;MWL.splice(i,1);if(c)c();return}
 if(MWC[n])MWC[n]();}
function mwPop(){var t=MWL.pop();if(t&&t.c)t.c();return !!t}
window.addEventListener('popstate',function(){if(MWL.length)mwPop()});
// --- Arrastrar desde el borde izquierdo para cerrar --------------------------
var MWD=null;
function mwDragEnd(el,anim){if(!el)return;
 if(anim){el.classList.add('mwback');setTimeout(function(){el.classList.remove('mwback');el.style.transform='';el.style.opacity=''},190)}
 else{el.style.transform='';el.style.opacity=''}}
document.addEventListener('touchstart',function(e){
 MWD=null;if(!MWL.length||!e.touches||e.touches.length!==1)return;
 var t=mwTop();if(!t||!t.el)return;
 if(e.touches[0].clientX>26)return;                   // solo desde el borde
 MWD={x:e.touches[0].clientX,y:e.touches[0].clientY,el:t.el,n:t.n,on:0};},{passive:true});
document.addEventListener('touchmove',function(e){
 if(!MWD||!e.touches||!e.touches.length)return;
 var dx=e.touches[0].clientX-MWD.x,dy=Math.abs(e.touches[0].clientY-MWD.y);
 if(!MWD.on){if(dx>12&&dx>dy){MWD.on=1;MWD.el.style.transition='none'}
  else if(dy>12||dx<-12){MWD=null;return}else return}
 dx=Math.max(0,dx);
 MWD.el.style.transform='translateX('+dx+'px)';
 MWD.el.style.opacity=String(Math.max(.3,1-dx/Math.max(1,window.innerWidth)));},{passive:true});
document.addEventListener('touchend',function(e){
 if(!MWD)return;var d=MWD;MWD=null;if(!d.on)return;
 var x=(e.changedTouches&&e.changedTouches.length)?e.changedTouches[0].clientX:d.x;
 d.el.style.transition='';
 if((x-d.x)>Math.min(110,window.innerWidth*0.28)){mwDragEnd(d.el,0);mwBack(d.n)}
 else{mwDragEnd(d.el,1)}});
document.addEventListener('touchcancel',function(){if(MWD){mwDragEnd(MWD.el,1);MWD=null}});
// Pestanas: estar en Buscar / Mi lista es tambien un "sitio del que se vuelve".
// Una sola entrada en la pila por mucho que se salte entre pestanas.
var CURVIEW='inicio';
function setView(v){CURVIEW=v;
 ['inicio','buscar','lista','hist'].forEach(function(k){
  var pa=$('pane-'+k),tb=$('tab-'+k);
  if(pa)pa.classList.toggle('hidden',k!==v);
  if(tb)tb.classList.toggle('on',k===v)});
 if(v==='lista')renderFavs();
 if(v==='hist')renderHist();
 if(v==='buscar')try{recPinta()}catch(e){}}
function goView(v){
 if(v==='inicio'){if(mwHas('tab')){mwBack('tab');return}setView('inicio');return}
 mwOpen('tab',null,function(){setView('inicio')});setView(v);}
function chip(kind){document.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.dataset.k===kind)});
 // el Inicio vuelve donde lo dejaste (antes siempre a Estrenos)
 try{localStorage.setItem('mw_chip',kind)}catch(e){}
 INI={kind:kind,page:1,loading:false,more:true};
 var g=$('inicio-grid');g.className='';g.innerHTML=skelGrid();
 var slow=setTimeout(function(){if(g.querySelector('.skph')){g.className='msg';g.innerHTML='<span class="spin"></span> Despertando el servidor… (solo la primera vez)';}},7000);
 // Timeout duro: si DonTorrent va lento/caido NUNCA dejamos la app colgada.
 var ctrl=(window.AbortController?new AbortController():null);var done=false;
 var to=setTimeout(function(){if(!done&&ctrl)ctrl.abort();},12000);
 function ensureGrid(){if(!g.querySelector('.grid')){g.className='';g.innerHTML='<div class="grid"></div>';}}
 function box(){if(kind==='estrenos'){boxMerge('inicio',g,'latest','','et,dx');boxMerge('inicio',g,'latest','','wf');}}
 function retry(){g.className='msg';g.innerHTML='No se pudo cargar ahora. <a href="javascript:void(0)" onclick="chip(\''+kind+'\')">Reintentar</a>';}
 function fallback(){ // DonTorrent vacio/lento/caido: que el box (Estrenos) llene; si no, reintento
  LISTS.inicio=[];ensureGrid();box();
  if(kind==='estrenos'){setTimeout(function(){if(!g.querySelector('.card'))retry();},14000);}else{retry();}}
 fetch('/catbrowse?kind='+kind+'&page=1&code='+(code.value||'').replace(/\D/g,''),ctrl?{signal:ctrl.signal}:{}).then(function(r){return r.json()}).then(function(d){
  done=true;clearTimeout(slow);clearTimeout(to);
  LISTS.inicio=(d&&d.items)||[];
  if(!LISTS.inicio.length){fallback();return}
  renderGrid(g,'inicio');box();
 }).catch(function(){done=true;clearTimeout(slow);clearTimeout(to);fallback()})}
function loadMoreInicio(){if(INI.loading||!INI.more)return;INI.loading=true;var next=INI.page+1;
 fetch('/catbrowse?kind='+INI.kind+'&page='+next+'&code='+(code.value||'').replace(/\D/g,'')).then(function(r){return r.json()}).then(function(d){
  var items=(d&&d.items)||[];var have={};LISTS.inicio.forEach(function(x){have[x.kind+':'+x.content_id]=1});
  var fresh=items.filter(function(x){var k=x.kind+':'+x.content_id;if(have[k])return false;have[k]=1;return true});
  if(!fresh.length){INI.more=false;INI.loading=false;return}
  var from=LISTS.inicio.length;LISTS.inicio=LISTS.inicio.concat(fresh);INI.page=next;
  appendGrid($('inicio-grid'),'inicio',from);INI.loading=false;
 }).catch(function(){INI.loading=false})}
window.addEventListener('scroll',function(){
 if($('pane-inicio').classList.contains('hidden'))return;
 if($('ov').classList.contains('on')||$('remote').classList.contains('on')||$('sheet').classList.contains('on'))return;
 if(window.innerHeight+window.scrollY>=document.body.offsetHeight-700)loadMoreInicio();});
function mergeResults(list,g,items){
 if(!items||!items.length)return;
 var norm=function(s){return (s||'').toLowerCase().replace(/\s+/g,' ').trim()};
 // Dedup por TÍTULO+AÑO (no solo título): los remakes del MISMO título salen LAS DOS
 // (Suspiria 1977 vs 2018, Dune 1984 vs 2021...) porque el año los separa; la misma
 // peli repetida entre fuentes (mismo título+año) se funde. Un item SIN año se funde
 // en cualquier homónimo del mismo título ya presente (evita una tarjeta "pelada" de
 // otra fuente sin enriquecer). Antes el dedup por título a secas TIRABA el remake
 // -> en la web solo salía una aunque el relay devolviera las dos.
 // Y cuando la MISMA peli viene de varias fuentes, se queda la MEJOR, no la que
 // llegue antes: gana la calidad mas alta y, a igualdad, DonTorrent (es la fuente
 // principal, con aviso de RAR, semillas y reproduccion propia). Antes ganaba la
 // primera en llegar, y como DivxTotal sale directo del relay (~2s) y DonTorrent
 // va por una caja (~4s), 'matrix' acababa mostrando la tarjeta de DivxTotal.
 var byKey={},titles={},at={};
 LISTS[list].forEach(function(x,i){var t=norm(x.title);if(!t)return;titles[t]=1;
  var k=t+'|'+(x.year||'');byKey[k]=1;if(at[k]===undefined)at[k]=i;if(at[t+'|*']===undefined)at[t+'|*']=i;});
 var swapped=[];
 var fresh=items.filter(function(x){var t=norm(x.title);if(!t)return true;var y=String(x.year||'');
  var k=t+'|'+y;
  if(y){if(byKey[k]){upgrade(list,k,x,at,swapped);return false}byKey[k]=1;titles[t]=1;return true;}
  if(titles[t]){upgrade(list,t+'|*',x,at,swapped);return false}titles[t]=1;byKey[t+'|']=1;return true;});
 if(fresh.length){var from=LISTS[list].length;LISTS[list]=LISTS[list].concat(fresh);
  for(var j=0;j<fresh.length;j++){var tt=norm(fresh[j].title);if(!tt)continue;
   var kk=tt+'|'+(fresh[j].year||'');if(at[kk]===undefined)at[kk]=from+j;if(at[tt+'|*']===undefined)at[tt+'|*']=from+j;}
  if(g.querySelector('.grid'))appendGrid(g,list,from);else renderGrid(g,list);}
 // Repintar SOLO las tarjetas sustituidas (no toda la cuadricula: el usuario
 // puede estar haciendo scroll y no se le mueve nada de sitio).
 for(var s2=0;s2<swapped.length;s2++)repaintCard(g,list,swapped[s2]);}
var QRANK={'4k':5,'2160p':5,'uhd':5,'1080p':4,'1080':4,'bdremux':4,'720p':2,'720':2,'480p':1};
// A igualdad de etiqueta de calidad: DonTorrent (fuente principal, con aviso
// de RAR, semillas y reproducción propia) > WolfMax (su 4K es el de mayor
// bitrate que hay: capítulos de 9 GB) > DivxTotal > EliteTorrent.
var SRANK={dt:3,wf:2,dx:1,et:0};
function srcScore(x){var q=QRANK[((x.quality||'')+'').toLowerCase()]||0;
 return q*10+(SRANK[x.source||'dt']||0);}
// Une dos listas de capítulos sin repetir, en orden.
function mergeEps(a,b){var by={},out=[];
 (a||[]).concat(b||[]).forEach(function(e){var kk=e.label||((e.season||0)+'x'+(e.episode||0));
  if(!by[kk]){by[kk]=1;out.push(e)}});
 out.sort(function(p,q){return ((p.season||0)-(q.season||0))||((p.episode||0)-(q.episode||0))});
 return out;}
function upgrade(list,k,x,at,swapped){var i=at[k];if(i===undefined)return;
 var cur=LISTS[list][i];if(!cur)return;
 // CAPÍTULOS: si las dos fuentes traen, se FUSIONAN (cada capítulo recuerda de
 // quién es y se resuelve por ahí); si solo trae la perdedora, la tarjeta que
 // queda se los guarda como PLAN B -> si su propia fuente no da capítulos, la
 // ficha usa estos en vez de decir "no se pudieron leer".
 var ce=(cur.eps&&cur.eps.length)?cur.eps:null,xe=(x.eps&&x.eps.length)?x.eps:null;
 var union=(ce&&xe)?mergeEps(ce,xe):null;
 if(srcScore(x)>srcScore(cur)){
  // conservamos lo que la otra fuente SI trajo (poster/nota/sinopsis de TMDB)
  ['poster','rating','year','overview','genres','backdrop','tmdb_id'].forEach(function(f){
   if(x[f]===undefined||x[f]===null||x[f]==='')if(cur[f]!==undefined)x[f]=cur[f];});
  if(union)x.eps=union;
  else if(!xe&&ce)x.epsAlt=ce;
  else if(!xe&&cur.epsAlt)x.epsAlt=cur.epsAlt;
  x.alts=addAlt(x.alts,cur,x);
  LISTS[list][i]=x;if(swapped.indexOf(i)<0)swapped.push(i);}
 else if(altKey(x)===altKey(cur)&&altMejor(cur,x)===x){
  // MISMA fuente y MISMA calidad, pero la que llega es mas completa (trae
  // capitulos): se queda ella. Antes se descartaba y la serie abria vacia.
  ['poster','rating','year','overview','genres','backdrop','tmdb_id'].forEach(function(f){
   if(x[f]===undefined||x[f]===null||x[f]==='')if(cur[f]!==undefined)x[f]=cur[f];});
  if(union)x.eps=union;
  else if(!xe&&ce)x.epsAlt=ce;
  else if(!xe&&cur.epsAlt)x.epsAlt=cur.epsAlt;
  x.alts=addAlt(cur.alts,x,x);
  LISTS[list][i]=x;if(swapped.indexOf(i)<0)swapped.push(i);}
 else{
  if(union){cur.eps=union;if(swapped.indexOf(i)<0)swapped.push(i);}
  else if(!ce&&xe)cur.epsAlt=xe;
  cur.alts=addAlt(cur.alts,x,cur);}}
// Guarda la version que PIERDE la tarjeta (otra fuente, quizá mejor calidad)
// para poder elegirla luego en la ficha. Sin anidar: las alternativas de la
// perdedora se suben al mismo nivel, y nunca se repite una fuente.
// Clave de una versión: fuente + calidad. Antes se deduplicaba por
// (fuente, content_id) y salían SEIS chips "WolfMax 4K" de la misma peli.
function altKey(a){return (a&&a.source||'dt')+'|'+((a&&a.quality)||'');}
// Entre dos versiones de la misma fuente+calidad, la más completa: la que trae
// capítulos, y a igualdad la que tenga póster/nota.
function altMejor(a,b){
 var ea=(a.eps||[]).length,eb=(b.eps||[]).length;
 if(ea!==eb)return ea>eb?a:b;
 if(!!a.poster!==!!b.poster)return a.poster?a:b;
 return a;}
// `activa` es la tarjeta que se esta mostrando: su version NO puede figurar
// tambien como alternativa (salia un chip repetido que al pulsarlo no hacia
// nada, porque ya estabas en el).
function addAlt(alts,otra,activa){
 var by={},orden=[],ka=activa?altKey(activa):null;
 (alts||[]).concat([otra]).concat(otra&&otra.alts||[]).forEach(function(a){
  if(!a||!a.source)return;
  var c={};for(var k in a)if(k!=='alts')c[k]=a[k];
  var kk=altKey(c);
  if(kk===ka)return;
  if(by[kk]){by[kk]=altMejor(by[kk],c)}else{by[kk]=c;orden.push(kk)}});
 return orden.slice(0,6).map(function(k){return by[k]});}
function repaintCard(g,list,i){var grid=g.querySelector('.grid');if(!grid)return;
 var c=grid.children[i];if(!c)return;
 var tmp=document.createElement('div');tmp.innerHTML=cardHTML(LISTS[list][i],list,i);
 if(tmp.firstChild)grid.replaceChild(tmp.firstChild,c);}
// ---- Progreso de la búsqueda -------------------------------------------
// 0 = buscando · 1 = ha traído cosas · 2 = no tiene nada · 3 = no contestó
var PROG={t0:0,st:{},n:{},tick:null,seq:0};
var PROGN={dt:'DonTorrent',et:'EliteTorrent',dx:'DivxTotal',wf:'WolfMax'};
function progStart(seq){PROG={t0:Date.now(),st:{dt:0,et:0,dx:0,wf:0},n:{dt:0,et:0,dx:0,wf:0},tick:null,seq:seq};
 var el=$('buscar-prog');if(el)el.classList.add('on');
 PROG.tick=setInterval(function(){if(PROG.seq!==_searchSeq){progStop();return}progPaint()},150);
 progPaint();}
function progSet(src,estado,n){if(PROG.seq!==_searchSeq)return;
 PROG.st[src]=estado;if(n!=null)PROG.n[src]=n;progPaint();
 var fin=['dt','et','dx','wf'].every(function(k){return PROG.st[k]>0});
 if(fin)setTimeout(function(){if(PROG.seq===_searchSeq)progStop(1)},1400);}
function progPaint(){var el=$('buscar-prog');if(!el)return;
 var ks=['dt','dx','et','wf'],hechas=0,total=0;
 var chips=ks.map(function(k){var e=PROG.st[k]||0;if(e>0)hechas++;if(e===1)total+=PROG.n[k]||0;
  var cls='srcp-f '+k+(e>0?' on':' wait')+(e===2?' zero':'');
  var txt=PROGN[k]+(e===1?(' <b>'+PROG.n[k]+'</b>'):(e===2?' <b>0</b>':(e===3?' —':'')));
  return '<span class="'+cls+'"><i></i>'+txt+'</span>'}).join('');
 var seg=((Date.now()-PROG.t0)/1000).toFixed(1).replace('.',',');
 var fin=(hechas===4);
 // Lo que se CUENTA arriba son las tarjetas que hay en pantalla, no la suma
 // de las fuentes: la misma peli en cuatro fuentes es UNA tarjeta (las otras
 // tres viven dentro, en "Tambien en"). Sumando salia "34 resultados" con 11
 // tarjetas a la vista y parecia que se perdian.
 var vis=((window.LISTS&&LISTS.buscar)||[]).length;
 var nf=ks.filter(function(k){return PROG.st[k]===1}).length;
 // Las versiones que se pueden ELEGIR de verdad (los chips "Tambien en"), no la
 // resta contra la suma de las fuentes: esa incluye copias identicas de la
 // misma fuente y calidad, que no se muestran porque no aportan nada.
 var extra=0;((window.LISTS&&LISTS.buscar)||[]).forEach(function(x){extra+=((x&&x.alts)||[]).length});
 var ttl=fin?('✓ '+vis+' título'+(vis===1?'':'s')+' en '+nf+' fuente'+(nf===1?'':'s')+
   (extra?(' <span class="srcp-x">+'+extra+(extra===1?' versión':' versiones')+'</span>'):''))
            :('<span class="spin"></span> Buscando en '+(4-hechas)+' fuente'+((4-hechas)===1?'':'s')+'…');
 el.innerHTML='<div class="srcp-top"><span class="srcp-ttl">'+ttl+'</span>'+
  '<span class="srcp-sec">'+seg+' s</span></div>'+
  '<div class="srcp-bar"><i style="width:'+Math.round(hechas/4*100)+'%"></i></div>'+
  '<div class="srcp-row">'+chips+'</div>';}
function progStop(suave){if(PROG.tick){clearInterval(PROG.tick);PROG.tick=null}
 var el=$('buscar-prog');if(!el)return;
 if(suave){setTimeout(function(){if(!PROG.tick)el.classList.remove('on')},2200)}
 else el.classList.remove('on');}
// ---- Ultimas busquedas (en este movil) ---------------------------------
var REC=[];try{REC=JSON.parse(localStorage.getItem('mw_rec')||'[]')||[]}catch(e){REC=[]}
function recSave(){try{localStorage.setItem('mw_rec',JSON.stringify(REC.slice(0,8)))}catch(e){}}
function recAdd(q){
 q=(q||'').trim();if(!q)return;
 REC=REC.filter(function(z){return z.toLowerCase()!==q.toLowerCase()});
 REC.unshift(q);REC=REC.slice(0,8);recSave();recPinta();}
function recQuita(i){REC.splice(i,1);recSave();recPinta()}
function recIr(i){var q=REC[i];if(!q)return;$('q').value=q;go()}
function recPinta(){
 var el=$('buscar-rec');if(!el)return;
 var hayResultados=((LISTS&&LISTS.buscar)||[]).length>0;
 if(!REC.length||hayResultados){el.className='rec';el.innerHTML='';return}
 el.className='rec on';
 el.innerHTML='<div class="rec-t">\u00daltimas b\u00fasquedas</div>'+
  REC.map(function(q,i){
   return '<button class="recb" onclick="recIr('+i+')"><span>'+esc(q)+'</span>'+
    '<i onclick="event.stopPropagation();recQuita('+i+')">\u00d7</i></button>'}).join('');}
var _searchSeq=0;
// Peticiones de la busqueda EN CURSO. Cambiar de busqueda no basta con ignorar
// las respuestas: mientras siguen en vuelo ocupan un hilo del relay (5 por
// busqueda, de 8 que hay). Se apuntan aqui y se abortan al empezar otra.
var SREQ=[];
function sreqAdd(c){if(c)SREQ.push(c)}
function sreqAbort(){var l=SREQ;SREQ=[];l.forEach(function(c){try{c.abort()}catch(e){}})}
// fetch con TIMEOUT real (AbortController): un relay dormido (Render free, cold
// start ~50s) NO deja la promesa colgada -> abortamos y reintentamos.
function tfetch(url,ms){var c=('AbortController'in window)?new AbortController():null;
 sreqAdd(c);
 var to=c?setTimeout(function(){try{c.abort()}catch(e){}},ms):0;
 return fetch(url,c?{signal:c.signal}:{}).then(function(r){if(to)clearTimeout(to);if(!r.ok)throw new Error('http'+r.status);return r;},function(e){if(to)clearTimeout(to);throw e;});}
function go(){var q=$('q').value.trim();if(!q)return;var g=$('buscar-grid');g.className='';g.innerHTML=skelGrid();
 var cd=(code.value||'').replace(/\D/g,'');LISTS.buscar=[];
 // se guarda la busqueda, y los chips se quitan de en medio mientras se busca
 try{recAdd(q);var _r=$('buscar-rec');if(_r)_r.className='rec'}catch(e){}
 sreqAbort();            // devuelve al relay los hilos de la busqueda anterior
 _searchSeq++;var seq=_searchSeq;
 progStart(seq);
 // TODAS las fuentes para TODO EL MUNDO: EliteTorrent y WolfMax necesitan una IP
 // residencial (un Kodi), pero el relay ya PRESTA cualquier caja encendida del
 // sistema (_box_for/_any_live_box), asi que no hace falta tener codigo puesto
 // para verlas. Antes solo se pedian con codigo de 6 cifras -> un amigo sin Kodi
 // configurado se quedaba sin EliteTorrent ni WolfMax en cada busqueda.
 var more=$('buscar-more');var boxPend=1;var boxAdded=0;var boxTO=false;var wfPend=true;
 var dxPend=1;   // DivxTotal DIRECTO via Render (siempre; el box/ISP lo bloquea)
 var dtPend=0;   // 2ª pasada para recoger DonTorrent si vino parcial
 var catState='pending';var wakeAtt=0;  // pending|ok|fail ; intentos de despertar
 function paint(){if(seq!==_searchSeq)return;
  var waiting=(catState==='pending')||boxPend>0||dxPend>0||dtPend>0;
  if(!LISTS.buscar.length){
   // AÚN sin resultados: distinguir relay dormido (reintentando) de vacío real.
   if(catState==='pending'&&wakeAtt>=2){g.className='msg';g.innerHTML='<span class="spin"></span> Despertando el servidor…';if(more)more.textContent='';return;}
   // Sin NADA que enseñar aun: esperamos tambien a WolfMax (es la mas lenta)
   // antes de decir "Sin resultados" -> nunca se descarta una fuente en falso.
   if(catState==='pending'||waiting||wfPend){if(!g.querySelector('.skgrid')){g.className='';g.innerHTML=skelGrid();}if(more)more.innerHTML='<span class="spin"></span> Buscando en más fuentes…';return;}
   if(catState==='fail'){g.className='msg';g.innerHTML='⚠️ El servidor estaba dormido y no respondió a tiempo.<br><br><button onclick="go()" style="background:#1c64f2;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-size:15px;cursor:pointer">↻ Reintentar</button>';if(more)more.textContent='';return;}
   g.className='msg';g.textContent='Sin resultados para "'+q+'".';if(more)more.textContent='';return;}
  // YA hay resultados:
  if(waiting){if(more)more.innerHTML='<span class="spin"></span> Buscando en más fuentes…';return;}
  // Ya NO es "para ver ET/DX/WF" (se ven siempre, por cualquier caja viva):
  // encender la suya solo hace que su búsqueda vaya por su propia casa y sea
  // más rápida y fiable.
  if(boxTO&&!boxAdded&&cd.length===6){if(more)more.innerHTML='💡 Enciende tu Kodi ('+cd+') y las búsquedas irán más rápidas';}
  else if(wfPend){if(more)more.innerHTML='<span class="spin" style="opacity:.5"></span> <span style="opacity:.6">buscando también en WolfMax…</span>';}
  else if(more)more.textContent='';}
 function done(r){if(seq!==_searchSeq)return;if(r){if(r.timeout)boxTO=true;if(r.added)boxAdded+=r.added;}boxPend--;paint();}
 function doneWf(r){if(seq!==_searchSeq)return;wfPend=false;paint();}
 // catsearch (relay) con REINTENTO AUTO: si Render está dormido, la 1ª llamada lo
 // despierta (~50s) -> reintentamos hasta que conteste. Así un relay frío acaba
 // dando resultados y muestra "Despertando…", NUNCA "Sin resultados" en falso.
 // Timeout 1er intento 20s: una busqueda normal pero lenta (DonTorrent con muchas
 // paginas o Anubis recien refrescado) tarda 8-15s; con 14s saltaba el reintento y
 // mostraba "Despertando..." EN FALSO con el relay vivo. 20s cubre el caso lento
 // sin alargar de mas el aviso si el relay esta de verdad dormido (cold ~50s -> los
 // reintentos lo cubren). Reintentos 16s (ya en modo "despertando").
 // La respuesta puede venir PARCIAL: el relay contesta rápido con lo que tiene
 // y el buscador de DonTorrent (que va por una caja de casa y a veces tarda 15s)
 // deja su resultado listo para el siguiente vistazo. Se vuelve a preguntar UNA
 // vez, sin rueda eterna: mientras tanto ya hay resultados en pantalla.
 var csDone=0;
 function csTry(att){if(seq!==_searchSeq)return;wakeAtt=att;
  tfetch('/catsearch?q='+encodeURIComponent(q)+'&code='+cd,att===1?20000:16000).then(function(r){return r.json()}).then(function(d){
   if(seq!==_searchSeq)return;
   // `retry`: otra búsqueda igual está en curso; NO es un fallo del servidor.
   if(d&&d.retry&&!(d.items&&d.items.length)&&att<6){
    setTimeout(function(){csTry(att+1)},2500);paint();return;}
   catState='ok';mergeResults('buscar',g,(d&&d.items)||[]);
   var _ndt=((d&&d.items)||[]).filter(function(z){return (z.source||'dt')==='dt'}).length;
   // se marca SIEMPRE con lo que traiga: si viene parcial, la 2ª pasada
   // actualiza el número. (Antes, si era parcial, DonTorrent no se marcaba
   // nunca y el chip se quedaba "buscando" con sus resultados ya en pantalla.)
   progSet('dt',_ndt?1:2,_ndt);
   if(d&&d.partial&&!csDone){csDone=1;dtPend=1;
    setTimeout(function(){if(seq!==_searchSeq)return;
     tfetch('/catsearch?q='+encodeURIComponent(q)+'&code='+cd,16000).then(function(r){return r.json()})
      .then(function(d2){if(seq!==_searchSeq)return;mergeResults('buscar',g,(d2&&d2.items)||[]);dtPend=0;
        var n2=((d2&&d2.items)||[]).filter(function(z){return (z.source||'dt')==='dt'}).length;
        progSet('dt',n2?1:2,n2);paint();})
      .catch(function(){dtPend=0;progSet('dt',3);paint()});},7000);}
   paint();
  }).catch(function(){if(seq!==_searchSeq)return;
   if(att<6){setTimeout(function(){csTry(att+1)},1200);paint();}
   else{catState='fail';progSet('dt',3);paint();}});}
 // WolfMax, SOLO y el primero. Se resuelve con el índice del relay (214 ms
 // medidos) y es lo que más se quiere ver; lanzado a la vez que las demás, el
 // navegador lo serializaba detrás de /catsearch y tardaba 7s en aparecer.
 // El resto arranca en cuanto WolfMax contesta, o a los 1,2s como mucho.
 var _resto=0;
 function lanzarResto(){if(_resto||seq!==_searchSeq)return;_resto=1;arrancaResto();}
 // Si WolfMax EXPIRA es casi siempre un titulo que la caja no tenia indexado:
 // le cuesta ~88s de crawl y el relay corta a los 24s. La caja termina igual y
 // el relay ya aprende ese resultado (ver /catjob/done), asi que se vuelve a
 // preguntar UNA vez a los 35s: para entonces esta en el indice y contesta en
 // milisegundos. Sin esto, WolfMax "unas veces salia y otras no".
 var wfRe=0;
 function wfPide(){
  boxMerge('buscar',g,'search',q,'wf',function(r){
    if(seq!==_searchSeq)return;
    // DOS reintentos, no uno: un titulo que ninguna caja tiene indexado le
    // cuesta ~88s de rastreo y el primer reintento (35s) llegaba demasiado
    // pronto. Al segundo (95s) ya esta en el indice y contesta en milisegundos.
    if(!(r&&r.got)&&wfRe<2){wfRe++;
     progSet('wf',0);                       // sigue buscando, no es un cero
     if(more)paint();
     setTimeout(function(){if(seq===_searchSeq)wfPide()},wfRe===1?35000:60000);
     lanzarResto();return;}
    progSet('wf',(r&&r.got)?1:((r&&r.timeout)?3:2),(r&&r.got)||0);
    // llega DESPUES de que la barra se cerrara: se vuelve a asomar un momento
    // con la cifra buena, para que se vea de donde han salido esas tarjetas.
    if(wfRe&&r&&r.got){var _b=$('buscar-prog');if(_b){_b.classList.add('on');
      setTimeout(function(){if(!PROG.tick)_b.classList.remove('on')},4000)}}
    doneWf(r);lanzarResto();},seq,1);}
 wfPide();
 setTimeout(lanzarResto,1200);
 function arrancaResto(){
 csTry(1);
 // DivxTotal DIRECTO via Render, EN PARALELO: llega tarde (~6s, Cloudflare) y se
 // fusiona cuando esté -> DX aparece sin frenar a DT. Siempre (no necesita box).
 // PLAN B de DivxTotal por caja (IP residencial). Cloudflare "tarpitea" al azar a
 // la IP de Render: el camino directo puede tardar 30s y volver VACÍO (medido hoy
 // con 'dune'), y ninguna fuente puede depender de un solo camino. Se dispara si
 // el directo vuelve vacío O si a los 7s aún no ha contestado — sin cancelarlo:
 // corren los dos y pinta el primero que llegue. Si el directo trae resultados,
 // NO se molesta a ninguna caja (sería trabajo duplicado).
 var dxAns=0,dxB=0;
 function dxPlanB(){if(dxB)return;dxB=1;boxPend++;boxMerge('buscar',g,'search',q,'dx',done,seq,1);}
 dxMerge('buscar',g,q,seq,function(n){if(seq!==_searchSeq)return;dxAns=1;dxPend=0;
  progSet('dx',n?1:2,n);
  if(!n)dxPlanB();
  paint();});
 setTimeout(function(){if(seq!==_searchSeq)return;if(!dxAns)dxPlanB();},7000);
 // SALVAVIDAS solo para BOX+DX: cerramos SU espera y pintamos lo que haya. 26s
 // porque WolfMax por caja puede tardar ~24s (su tope en el relay); los resultados
 // se fusionan igual cuando llegan, esto solo apaga el "Buscando…".
 // El catsearch tiene su propio reintento, no lo toca este salvavidas.
 setTimeout(function(){if(seq!==_searchSeq)return;if(boxPend>0||wfPend||dxPend>0){boxPend=0;wfPend=false;dxPend=0;paint();}
  ['dt','et','dx','wf'].forEach(function(k){if(!PROG.st[k])progSet(k,3)});},26000);
 // ...pero si a los 12s TODO lo demás terminó SIN NADA, no hacemos esperar al
 // usuario los 26s de WolfMax para decirle que no hay resultados: se lo decimos
 // ya. Si WolfMax llega después con algo, la cuadrícula se pinta igual (el
 // merge repinta sobre el mensaje). Rapidez sin perder fuentes.
 setTimeout(function(){if(seq!==_searchSeq)return;
  if(wfPend&&!LISTS.buscar.length&&boxPend<=0&&dxPend<=0&&catState!=='pending'){wfPend=false;paint();}},12000);
 // EliteTorrent y WolfMax via caja (propia o PRESTADA): SIEMPRE, con o sin código.
 // DivxTotal NO se le pide a la caja aquí: ya va directo por /catdxsearch (más
 // rápido) y arriba está su plan B.
 boxMerge('buscar',g,'search',q,'et',function(r){progSet('et',(r&&r.got)?1:((r&&r.timeout)?3:2),(r&&r.got)||0);done(r)},seq,1);
 }}
function dxMerge(list,g,q,seq,cb){
 // 14s de tope: Cloudflare "tarpitea" a la IP de Render y este endpoint ha
 // llegado a tardar 40s. Si no llega, el plan B por caja ya se habrá disparado.
 tfetch('/catdxsearch?q='+encodeURIComponent(q),14000).then(function(r){return r.json()}).then(function(d){
  var n=((d&&d.items)||[]).length;
  if(seq!==_searchSeq){if(cb)cb(n);return;}mergeResults(list,g,(d&&d.items)||[]);if(cb)cb(n);
 }).catch(function(){if(cb)cb(0)})}
// always=1 -> no exige código: el relay presta una caja viva del sistema. El
// Inicio NO lo usa (lo carga todo el mundo al abrir: serían 2 trabajos de caja
// por visita); la BÚSQUEDA sí, que es donde importa tener todas las fuentes.
function boxMerge(list,g,op,q,srcs,cb,seq,always){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6&&!always){if(cb)cb({});return;}
 var u='/catetbox?code='+cd+'&op='+op+'&srcs='+(srcs||'et,dx')+(q?('&q='+encodeURIComponent(q)):'');
 var _c=('AbortController'in window)?new AbortController():null;
 if(op==='search')sreqAdd(_c);   // las del INICIO no: no las cancela nadie
 fetch(u,_c?{signal:_c.signal}:{}).then(function(r){return r.json()}).then(function(d){if(seq!==_searchSeq){if(cb)cb({});return;}var b=LISTS[list].length;var got=((d&&d.items)||[]).length;mergeResults(list,g,(d&&d.items)||[]);if(cb)cb({timeout:!!(d&&d.timeout),added:LISTS[list].length-b,got:got})}).catch(function(){if(cb)cb({})})}
// Un favorito guardado ANTES de que las tarjetas trajeran capitulos no los
// tiene. La primera vez que se abren por red, se los quedamos -> la proxima vez
// abre al instante. (Sin esto habria que quitarlo y volver a añadirlo a mano.)
function favLearnEps(x,eps){
 if(!x||!eps||!eps.length)return;
 for(var i=0;i<favs.length;i++){
  if(fk(favs[i])!==fk(x))continue;
  if(!(favs[i].eps&&favs[i].eps.length)){favs[i].eps=slimEps(eps);saveFavs()}
  return;}}
// ===== HISTORIAL =====================================================
// Lo último que se ha mandado a la tele, para volver a ponerlo de UN TOQUE
// (guardamos la propia referencia de reproducción: no hay que buscar de nuevo
// ni abrir la ficha). Vive en este móvil (localStorage), como los vistos.
var hist=[];try{hist=JSON.parse(localStorage.getItem('mw_hist')||'[]')||[]}catch(e){hist=[]}
function histSave(){try{localStorage.setItem('mw_hist',JSON.stringify(hist.slice(0,60)))}catch(e){}
 try{syncSubirPronto()}catch(e){}}
function histKey(h){return (h.ref&&(h.ref.u||((h.ref.c||'')+':'+(h.ref.tb||''))))||h.t||''}
// Foto de lo que se esta mandando. Hay que tomarla EN EL MOMENTO del envio:
// para cuando contesta la tele, `sel` ya puede ser otra cosa.
function histSnap(ref){
 if(!ref||!ref.t)return null;
 var x=sel||{};
 return {t:ref.t,ref:ref,ts:Date.now(),src:x.source||'dt',q:ref.q||x.quality||'',
         poster:x.poster||'',kind:x.kind||'movie'};}
function histPush(e){
 if(!e)return;
 e.ts=Date.now();
 var k=histKey(e);
 hist=hist.filter(function(h){return histKey(h)!==k});   // sin repetir: sube arriba
 hist.unshift(e);hist=hist.slice(0,60);histSave();
 if(CURVIEW==='hist')renderHist();}
function histAdd(ref){histPush(histSnap(ref))}
function histDel(i){hist.splice(i,1);histSave();renderHist()}
function histClear(){if(!hist.length)return;
 mwConfirm('\u00bfVaciar el historial?',
  'Se borran los '+hist.length+' t\u00edtulos de esta lista. Lo guardado en Mis listas no se toca.',
  'Vaciar',function(){hist=[];histSave();renderHist();toast('Historial vac\u00edo')},1)}
function histHace(ts){var s=Math.max(0,(Date.now()-ts)/1000);
 if(s<90)return 'hace un momento';
 var m=Math.round(s/60);if(m<60)return 'hace '+m+' min';
 var h=Math.round(m/60);if(h<24)return 'hace '+h+' h';
 var d=Math.round(h/24);return d===1?'ayer':('hace '+d+' días')}
function histPlay(i){var e=hist[i];if(!e||!e.ref)return;
 var g=sel;sel=e;          // asi la foto del registro conserva fuente/calidad/poster
 try{sendPlay(e.ref)}finally{sel=g}}
function renderHist(){
 var el=$('hist-list'),bar=$('hist-n'),bt=$('hist-clear');if(!el)return;
 if(!hist.length){el.className='msg';
  el.textContent='Aquí aparecerá lo que mandes a la tele, para repetirlo de un toque.';
  if(bar)bar.textContent='';if(bt)bt.style.display='none';return}
 if(bar)bar.textContent=hist.length+(hist.length===1?' título':' títulos');
 if(bt)bt.style.display='';
 var SL={dt:'DonTorrent',dx:'DivxTotal',et:'EliteTorrent',wf:'WolfMax'};
 el.className='';
 el.innerHTML=hist.map(function(h,i){
  var ph=h.poster?(' style="background-image:url('+esc(h.poster)+')"'):'';
  return '<div class="hitem"><div class="hp'+(h.poster?'':' np')+'"'+ph+'></div>'+
   '<div class="hm"><div class="ht">'+esc(h.t)+'</div>'+
   '<div class="hs"><span class="hsrc '+(h.src||'dt')+'">'+esc(SL[h.src]||'?')+'</span>'+
   (h.q?('<span>'+esc(h.q)+'</span>'):'')+'<span>'+esc(histHace(h.ts))+'</span></div></div>'+
   '<div class="hb"><button class="play" onclick="histPlay('+i+')" title="Volver a poner">▶</button>'+
   '<button onclick="histDel('+i+')" title="Quitar del historial">✕</button></div></div>'}).join('');}
// ===== COPIA DE SEGURIDAD (entrar con Google) ==========================
// Worker propio (Cloudflare + D1). Sin sesion no se llama a nada de esto y la
// web funciona igual que siempre.
var SYNC='https://mw-sync.israeldm93.workers.dev';
var GTOK='',GUSER=null;
try{GTOK=localStorage.getItem('mw_gtok')||''}catch(e){}
try{GUSER=JSON.parse(localStorage.getItem('mw_guser')||'null')}catch(e){}
function syncOn(){return !!GTOK}
// --- juntar movil + nube SIN BORRAR NADA -------------------------------
function syncFusiona(r){
 if(!r||typeof r!=='object')return false;
 var cambio=false;
 // listas: por id (las que falten aqui se anaden)
 (r.listas||[]).forEach(function(l){
  if(l&&l.id&&l.n&&!LST.some(function(z){return z.id===l.id})){LST.push({id:l.id,n:l.n});cambio=true}});
 // guardados: por clave; si ya esta, se UNEN las listas a las que pertenece
 (r.favs||[]).forEach(function(it){
  if(!it||!it.content_id)return;
  var mio=null;for(var i=0;i<favs.length;i++)if(fk(favs[i])===fk(it))mio=favs[i];
  if(!mio){favs.push(it);cambio=true;return}
  var a=(mio.ls||[]).slice();
  (it.ls||[]).forEach(function(id){if(a.indexOf(id)<0){a.push(id);cambio=true}});
  if(a.length)mio.ls=a;});
 // historial: por su referencia, lo mas reciente primero, tope 60
 (r.hist||[]).forEach(function(h){
  if(!h||!h.t)return;
  var k=histKey(h),yo=null;
  for(var i=0;i<hist.length;i++)if(histKey(hist[i])===k)yo=hist[i];
  if(!yo){hist.push(h);cambio=true}
  else if((h.ts||0)>(yo.ts||0)){yo.ts=h.ts;cambio=true}});
 hist.sort(function(a,b){return (b.ts||0)-(a.ts||0)});
 if(hist.length>60)hist=hist.slice(0,60);
 // Kodis: por codigo
 var devs=loadDevs(),dc=false;
 (r.devs||[]).forEach(function(d){
  if(d&&d.code&&!devs.some(function(z){return z.code===d.code})){devs.push(d);dc=true}});
 if(dc){saveDevs(devs);renderDevs();refreshDevBtn();cambio=true}
 if(cambio){saveFavs();lstSave();histSave()}
 return cambio;}
function syncPaquete(){
 return {listas:LST,favs:favs.map(function(f){
   var c={};for(var k in f)if(k!=='eps'&&k!=='epsAlt'&&k!=='alts')c[k]=f[k];return c}),
  hist:hist,devs:loadDevs(),v:1};}
var _syT=null,_syPend=false;
function syncSubirPronto(){if(!syncOn())return;clearTimeout(_syT);_syT=setTimeout(syncSubir,2000)}
function syncSubir(){
 if(!syncOn())return;
 fetch(SYNC+'/data',{method:'POST',headers:{'Content-Type':'application/json',
   'Authorization':'Bearer '+GTOK},body:JSON.stringify({data:syncPaquete()})})
  .then(function(r){return r.json()}).then(function(d){
   if(d&&d.ok){syncEstado('Guardado')}
   else if(d&&(d.error||'').indexOf('sesion')>=0){syncSalir(1)}})
  .catch(function(){});}
function syncBajar(cb){
 if(!syncOn()){if(cb)cb(false);return}
 syncEstado('Sincronizando\u2026');
 fetch(SYNC+'/data',{headers:{'Authorization':'Bearer '+GTOK}})
  .then(function(r){return r.json()}).then(function(d){
   if(d&&d.ok){
    var hubo=syncFusiona(d.data);
    if(CURVIEW==='lista')renderFavs();
    if(CURVIEW==='hist')renderHist();
    syncSubir();                 // devolvemos lo unido: las dos partes iguales
    if(cb)cb(hubo);return;}
   if(d&&(d.error||'').indexOf('sesion')>=0)syncSalir(1);
   if(cb)cb(false);})
  .catch(function(){syncEstado('Sin conexi\u00f3n');if(cb)cb(false)});}
function syncEstado(t){var e=$('cta-est');if(e)e.textContent=t||''}
function syncPinta(){
 var w=$('cta-who'),b=$('gbtn'),sub=$('cta-s');
 if(!w)return;
 if(syncOn()&&GUSER){
  w.style.display='';if(b)b.style.display='none';
  if(sub)sub.textContent='Tus listas, tu historial y tus Kodis se guardan solos.';
  $('cta-nom').textContent=GUSER.name||GUSER.email||'Tu cuenta';
  var im=$('cta-img');if(im){if(GUSER.picture){im.src=GUSER.picture;im.style.display=''}else im.style.display='none'}
 }else{
  w.style.display='none';if(b)b.style.display='';
  if(sub)sub.textContent='Entra con Google y tus listas, tu historial y tus Kodis quedan guardados: los recuperas en cualquier m\u00f3vil.';}}
function syncSalir(callado){
 GTOK='';GUSER=null;
 try{localStorage.removeItem('mw_gtok');localStorage.removeItem('mw_guser')}catch(e){}
 syncPinta();gsiPinta();
 if(!callado)toast('Sesi\u00f3n cerrada (lo guardado en este m\u00f3vil se queda)');}
// --- Google Identity Services (se carga solo al abrir Mis Kodis) --------
var _gsiCargando=false,_gsiListo=false,_gsiCID='';
function gsiPinta(){
 var b=$('gbtn');if(!b||syncOn())return;
 if(!_gsiListo||!window.google||!google.accounts||!google.accounts.id)return;
 b.innerHTML='';
 try{google.accounts.id.renderButton(b,{theme:'filled_black',size:'large',
   shape:'pill',text:'continue_with',locale:'es',width:280});}catch(e){}}
function gsiCarga(){
 if(_gsiCargando||syncOn())return;_gsiCargando=true;
 fetch(SYNC+'/config').then(function(r){return r.json()}).then(function(d){
  _gsiCID=(d&&d.client_id)||'';
  if(!_gsiCID){var e=$('cta-s');if(e)e.textContent='La copia de seguridad a\u00fan no est\u00e1 configurada.';return}
  var sc=document.createElement('script');
  sc.src='https://accounts.google.com/gsi/client';sc.async=true;sc.defer=true;
  sc.onload=function(){
   try{google.accounts.id.initialize({client_id:_gsiCID,callback:gsiEntra,
     auto_select:false,cancel_on_tap_outside:true});_gsiListo=true;gsiPinta();}catch(e){}};
  sc.onerror=function(){var e=$('cta-s');if(e)e.textContent='No se pudo cargar el acceso de Google.'};
  document.head.appendChild(sc);
 }).catch(function(){_gsiCargando=false});}
function gsiEntra(resp){
 var cred=resp&&resp.credential;if(!cred)return;
 toast('Entrando\u2026');
 fetch(SYNC+'/session',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({credential:cred})})
  .then(function(r){return r.json()}).then(function(d){
   if(!d||!d.ok||!d.token){toast('No se pudo entrar');return}
   GTOK=d.token;GUSER=d.user||null;
   try{localStorage.setItem('mw_gtok',GTOK);localStorage.setItem('mw_guser',JSON.stringify(GUSER))}catch(e){}
   syncPinta();
   syncBajar(function(){toast('Copia de seguridad activada')});})
  .catch(function(){toast('No se pudo entrar')});}
// ---- Hoja "Guardar en...": elegir a que lista va el titulo -------------
// LSVAR = varios titulos a la vez (seleccion multiple de Mis listas).
var LSX=null,LSCB=null,LSVAR=null;
function guardarVarios(items){
 if(!items||!items.length)return;
 LSVAR=items;LSX=null;LSCB=null;
 renderLS();
 $('lsheet').classList.add('on');
 mwOpen('lsheet',$('lsheet').querySelector('.box'),_closeLS);}
function guardarEn(x,cb){
 if(!x)return;
 // Con UNA sola lista no hay nada que elegir: un toque y ya (lo de siempre).
 if(LST.length<2){
  var id=LST[0].id;
  if(favEnLista(x,id)){favQuitar(x,id);toast('Quitado de \u00ab'+LST[0].n+'\u00bb')}
  else{favAdd(x,id);toast('Guardado en \u00ab'+LST[0].n+'\u00bb')}
  if(cb)cb();if(CURVIEW==='lista')renderFavs();return;}
 LSX=x;LSCB=cb||null;
 $('ls-t').textContent=isFav(x)?'En tus listas':'Guardar en\u2026';
 renderLS();
 $('lsheet').classList.add('on');
 mwOpen('lsheet',$('lsheet').querySelector('.box'),_closeLS);}
function renderLS(){
 var b=$('ls-body');if(!b)return;
 if(LSVAR){          // varios a la vez
  b.innerHTML=LST.map(function(l){
   var n=lstItems(l.id).length;
   var todos=LSVAR.every(function(x){return favEnLista(x,l.id)});
   return '<button class="ls-row'+(todos?' on':'')+'" onclick="lsTog(\''+l.id+'\')">'+
    '<span class="lsk">'+(todos?'\u2713':'')+'</span>'+
    '<span class="lsn">'+esc(l.n)+'</span>'+
    '<span class="lsc">'+n+'</span></button>'}).join('');
  $('ls-t').textContent='Mover '+LSVAR.length+' a\u2026';
  return;}
 if(!LSX)return;
 // ELECCION UNICA: cada titulo esta en UNA lista. Se marca en la que esta y
 // tocar otra lo MUEVE alli.
 b.innerHTML=LST.map(function(l){
  var on=favEnLista(LSX,l.id),n=lstItems(l.id).length;
  return '<button class="ls-row'+(on?' on':'')+'" onclick="lsTog(\''+l.id+'\')">'+
   '<span class="lsk">'+(on?'\u2713':'')+'</span>'+
   '<span class="lsn">'+esc(l.n)+'</span>'+
   '<span class="lsc">'+n+'</span></button>'}).join('')+
  (isFav(LSX)?('<button class="ls-quita" onclick="lsQuita()">Quitar de mis listas</button>'):'');
 $('ls-t').textContent=isFav(LSX)?'Mover a\u2026':'Guardar en\u2026';}
function lsQuita(){
 if(!LSX)return;
 favQuitarTodo(LSX);
 if(LSCB)LSCB();
 if(CURVIEW==='lista')renderFavs();
 toast('Quitado de tus listas');closeLS();}
function lsTog(id){
 if(LSVAR){
  var n=LSVAR.length;
  LSVAR.forEach(function(x){favAdd(x,id)});     // MOVER: salen de donde estaban
  if(CURVIEW==='lista')renderFavs();
  toast(n+' en \u00ab'+lstNombre(id)+'\u00bb');
  closeLS();return;}
 if(!LSX)return;
 if(favEnLista(LSX,id)){closeLS();return}       // ya esta ahi: nada que hacer
 favAdd(LSX,id);                                // MOVER a esta lista
 toast('En \u00ab'+lstNombre(id)+'\u00bb');
 if(LSCB)LSCB();
 if(CURVIEW==='lista')renderFavs();
 closeLS();}
function lsNueva(){
 mwPrompt('\u00bfC\u00f3mo se llama la lista?','','Pendiente, Vistas, Para el finde\u2026',
  function(n){_lsNueva(n)});}
function _lsNueva(n){
 var id=lstNueva(n);if(!id)return;
 if(LSVAR){var k=LSVAR.length;LSVAR.forEach(function(x){favAdd(x,id)});
  if(CURVIEW==='lista')renderFavs();toast(k+' en \u00ab'+n+'\u00bb');closeLS();return}
 if(LSX)favAdd(LSX,id);
 renderLS();if(LSCB)LSCB();
 if(CURVIEW==='lista'){lstSel(id);renderFavs()}
 toast('Guardado en \u00ab'+n+'\u00bb');}
function _closeLS(){$('lsheet').classList.remove('on');LSX=null;LSCB=null;
 if(LSVAR){LSVAR=null;selSalir()}}
function closeLS(){mwBack('lsheet')}
// Sin codigo no se puede mandar nada a la tele. Antes era un aviso de paso
// (un toast) que se iba solo y no decia que hacer; ahora lleva de la mano a
// "Mis Kodis", que es donde se pone.
function avisaCodigo(){
 mwConfirm('Falta tu código',
  'Para mandar algo a la tele hace falta el código de 6 cifras que sale en tu Kodi (MejorWolf → Mando).',
  'Ponerlo ahora',function(){openDevs();setTimeout(function(){try{$('devc').focus()}catch(e){}},350)});}
// ===== CUADROS PROPIOS =================================================
// `prompt`/`confirm` del navegador ensenan "...onrender.com dice", rompen la
// estetica y no se cierran con el gesto de volver. Estos si.
var MWDOK=null;
function _mwdCierra(){$('mwdlg').classList.remove('on');MWDOK=null}
function mwdNo(){mwBack('mwdlg')}
function mwdSi(){var f=MWDOK,v=$('mwd-in').value;mwBack('mwdlg');
 // la accion va DESPUES de que el cuadro termine de cerrarse: si abre otro
 // panel (por ejemplo Mis Kodis), se pisaba con el cierre y no se abria nada
 if(f)setTimeout(function(){f(v)},190);}
function _mwdAbre(t,sub,ok,conInput,valor,peligro){
 $('mwd-t').textContent=t||'';
 var e=$('mwd-s');e.textContent=sub||'';e.style.display=sub?'':'none';
 var i=$('mwd-in');
 if(conInput){i.style.display='';i.value=valor||''}else{i.style.display='none';i.value=''}
 var b=$('mwd-ok');b.textContent=ok||'Aceptar';b.classList.toggle('peligro',!!peligro);
 $('mwdlg').classList.add('on');
 mwOpen('mwdlg',$('mwdlg').querySelector('.box'),_mwdCierra);
 if(conInput)setTimeout(function(){try{i.focus();i.select()}catch(e2){}},60);}
function mwPrompt(titulo,valor,ayuda,alAceptar){
 MWDOK=function(v){v=(v||'').trim();if(v)alAceptar(v)};
 _mwdAbre(titulo,ayuda,'Guardar',1,valor,0);}
function mwConfirm(titulo,texto,etiqueta,alAceptar,peligro){
 MWDOK=function(){alAceptar()};
 _mwdAbre(titulo,texto,etiqueta||'S\u00ed',0,'',peligro);}
document.addEventListener('keydown',function(e){
 var d=$('mwdlg');if(!d||!d.classList.contains('on'))return;
 if(e.key==='Enter'){e.preventDefault();mwdSi()}
 else if(e.key==='Escape'){e.preventDefault();mwdNo()}});
// ===== SELECCION MULTIPLE (Mis listas) =================================
// Dejar pulsado medio segundo marca el primero; luego cada toque marca o
// desmarca. Sirve para mandar varios titulos a una lista de una vez.
var PICK=false,PICKED={};
function pickN(){var n=0;for(var k in PICKED)if(PICKED[k])n++;return n}
function pickPinta(){
 var b=$('selbar');if(b){b.classList.toggle('on',PICK);
  var e=$('sel-n');if(e){var n=pickN();e.textContent=n===1?'1 seleccionado':(n+' seleccionados')}}
 var g=$('lista-grid');if(g)g.classList.toggle('picking',PICK);
 // marcas en las tarjetas, sin repintar la cuadricula entera
 var cards=(g&&g.querySelectorAll('.card'))||[];
 for(var i=0;i<cards.length;i++){
  var x=LISTS.lista[i];if(!x)continue;
  var on=!!PICKED[fk(x)];
  cards[i].classList.toggle('picked',on);
  var ph=cards[i].querySelector('.ph'),t=cards[i].querySelector('.tick');
  if(PICK){if(!t&&ph){t=document.createElement('div');t.className='tick';ph.appendChild(t)}
   if(t)t.textContent=on?'\u2713':''}
  else if(t)t.remove();}}
function pickEntra(i){
 if(PICK)return;
 PICK=true;PICKED={};
 var x=LISTS.lista[i];if(x)PICKED[fk(x)]=1;
 mwOpen('pick',null,function(){PICK=false;PICKED={};pickPinta()});
 pickPinta();
 try{if(navigator.vibrate)navigator.vibrate(12)}catch(e){}}
function pickTog(i){
 var x=LISTS.lista[i];if(!x)return;
 var k=fk(x);
 if(PICKED[k])delete PICKED[k];else PICKED[k]=1;
 if(!pickN()){selSalir();return}
 pickPinta();}
function selSalir(){if(!PICK)return;mwBack('pick')}
function selItems(){return LISTS.lista.filter(function(x){return !!PICKED[fk(x)]})}
function selMover(){
 var its=selItems();if(!its.length)return;
 guardarVarios(its);}
function selQuitar(){
 var its=selItems();if(!its.length)return;
 var l=lstNombre(LSTSEL);
 mwConfirm('\u00bfQuitar '+its.length+' t\u00edtulo'+(its.length===1?'':'s')+'?',
  'Salen de \u00ab'+l+'\u00bb. Puedes volver a guardarlos cuando quieras.',
  'Quitar',function(){
   its.forEach(function(x){favQuitar(x,LSTSEL)});
   selSalir();renderFavs();toast('Quitados de \u00ab'+l+'\u00bb')},1);}
// Los toques de la cuadricula de Mis listas (delegado: sobrevive a repintados)
function pickEngancha(){
 var g=$('lista-grid');if(!g||g._pick)return;g._pick=1;
 var t0=0,tid=null,idx=-1,movido=false;
 function indiceDe(ev){
  var c=ev.target&&ev.target.closest?ev.target.closest('.card'):null;
  if(!c||!c.parentNode)return -1;
  return Array.prototype.indexOf.call(c.parentNode.children,c);}
 g.addEventListener('touchstart',function(ev){
  idx=indiceDe(ev);movido=false;if(idx<0)return;
  clearTimeout(tid);
  tid=setTimeout(function(){if(!movido&&idx>=0)pickEntra(idx)},480);},{passive:true});
 g.addEventListener('touchmove',function(){movido=true;clearTimeout(tid)},{passive:true});
 g.addEventListener('touchend',function(){clearTimeout(tid)},{passive:true});
 g.addEventListener('touchcancel',function(){clearTimeout(tid)},{passive:true});
 // raton (para probar desde el PC)
 g.addEventListener('mousedown',function(ev){
  idx=indiceDe(ev);movido=false;if(idx<0)return;
  clearTimeout(tid);tid=setTimeout(function(){if(idx>=0)pickEntra(idx)},480);});
 g.addEventListener('mouseup',function(){clearTimeout(tid)});
 g.addEventListener('mouseleave',function(){clearTimeout(tid)});
 // en modo seleccion, el toque marca en vez de abrir
 g.addEventListener('click',function(ev){
  if(!PICK)return;
  var i=indiceDe(ev);if(i<0)return;
  ev.preventDefault();ev.stopPropagation();
  pickTog(i);},true);
 g.addEventListener('contextmenu',function(ev){if(PICK)ev.preventDefault()});}
function renderLstBar(){var b=$('lstbar');if(!b)return;
 b.innerHTML=LST.map(function(l){
  var n=lstItems(l.id).length;
  return '<button class="lstc'+(l.id===LSTSEL?' on':'')+'" onclick="lstIr(\''+l.id+'\')">'+
   esc(l.n)+(n?('<b>'+n+'</b>'):'')+'</button>'}).join('')+
  // el lapiz le pone nombre a la lista que estas viendo; la papelera solo
  // aparece si hay mas de una (la ultima no se puede quedar sin listas)
  '<button class="lstb" onclick="lstRenombrar()" title="Poner nombre">✏️</button>'+
  (LST.length>1?'<button class="lstb" onclick="lstBorrar(LSTSEL)" title="Quitar lista">🗑️</button>':'')+
  '<button class="lstc add" onclick="lstCrear()">+ Nueva lista</button>';}
function lstIr(id){lstSel(id);renderFavs()}
// "Nueva lista": la crea y abre el lapiz para ponerle nombre. Si no escribe
// nada, se queda con el nombre automatico (nunca se pierde el paso).
// El nombre PRIMERO: antes se creaba la lista y luego se pedia el nombre, asi
// que cancelar dejaba una "Lista 2" vacia de recuerdo.
function lstCrear(){
 mwPrompt('\u00bfC\u00f3mo se llama la lista?','','Pendiente, Vistas, Para el finde\u2026',
  function(n){
   var id=lstNueva(n);if(!id)return;
   lstSel(id);renderFavs();mlPushSoon();
   toast('Lista \u00ab'+n.slice(0,40)+'\u00bb creada')});}
function lstRenombrar(nueva){
 var l=null;for(var i=0;i<LST.length;i++)if(LST[i].id===LSTSEL)l=LST[i];
 if(!l)return;
 mwPrompt(nueva?'\u00bfC\u00f3mo se llama la lista?':'Cambiar el nombre',
  nueva?'':l.n, nueva?'Pendiente, Vistas, Para el finde\u2026':'',
  function(q){l.n=q.slice(0,40);lstSave();renderFavs();mlPushSoon();
   toast(nueva?('Lista \u00ab'+l.n+'\u00bb creada'):'Lista renombrada')});}
function lstBorrar(id){
 if(LST.length<2){toast('Es tu \u00fanica lista');return}
 var l=null;for(var i=0;i<LST.length;i++)if(LST[i].id===id)l=LST[i];
 if(!l)return;
 var _n=lstItems(id).length;
 mwConfirm('\u00bfQuitar la lista \u00ab'+l.n+'\u00bb?',
  _n?(_n===1?'Su t\u00edtulo NO se borra: pasa a la primera lista.'
          :('Sus '+_n+' t\u00edtulos NO se borran: pasan a la primera lista.'))
    :'Est\u00e1 vac\u00eda, no se pierde nada.',
  'Quitar lista',function(){_lstBorra(id)},1);}
function _lstBorra(id){
 LST=LST.filter(function(z){return z.id!==id});
 var destino=LST[0].id;
 favs.forEach(function(f){
  var a=lstDe(f).filter(function(z){return z!==id});
  f.ls=a.length?a:[destino];});
 saveFavs();lstSave();lstSel(destino);renderFavs();toast('Lista borrada');}
var LFILTRO='';
function lstFiltra(){
 var i=$('lfin');LFILTRO=(i&&i.value||'').trim().toLowerCase();
 var x=$('lfx');if(x)x.style.display=LFILTRO?'':'none';
 renderFavs(1);}
function lstFiltraLimpia(){var i=$('lfin');if(i)i.value='';LFILTRO='';renderFavs(1);
 var x=$('lfx');if(x)x.style.display='none';}
function renderFavs(_mantenFiltro){
 renderLstBar();
 if(!_mantenFiltro){LFILTRO='';var _i=$('lfin');if(_i)_i.value='';var _x=$('lfx');if(_x)_x.style.display='none';}
 var g=$('lista-grid'),b=$('vtog');
 var _todos=lstItems(LSTSEL);
 // el campo solo estorba en listas cortas
 var _f=$('lfiltro');if(_f)_f.style.display=(_todos.length>8||LFILTRO)?'':'none';
 LISTS.lista=LFILTRO
  ? _todos.filter(function(x){return (x.title||'').toLowerCase().indexOf(LFILTRO)>=0})
  : _todos;
 if(!LISTS.lista.length){g.className='msg';
  g.innerHTML=LFILTRO?('Nada con \u00ab'+esc(LFILTRO)+'\u00bb en esta lista.')
   :(favs.length?('\u00ab'+esc(lstNombre(LSTSEL))+'\u00bb est\u00e1 vac\u00eda. Toca el coraz\u00f3n en cualquier t\u00edtulo y elige esta lista.')
    :'Tu lista est\u00e1 vac\u00eda. Toca el coraz\u00f3n en cualquier t\u00edtulo.');
  if(b)b.style.display='none';return}
 if(b)b.style.display='';renderGrid(g,'lista');applyView();
 pickEngancha();
 // si al repintar ya no queda nada marcado (se movio todo), se sale del modo
 if(PICK){var vivos=0;for(var i=0;i<LISTS.lista.length;i++)if(PICKED[fk(LISTS.lista[i])])vivos++;
  if(!vivos){selSalir();return}pickPinta();}}
function applyView(){var lv=localStorage.getItem('mw_lv')==='1';var g=$('lista-grid');if(g){var grid=g.querySelector('.grid');if(grid)grid.classList.toggle('lv',lv)}var b=$('vtog');if(b)b.innerHTML=lv?'▦ Vista cuadrícula':'☰ Vista lista'}
function toggleView(){localStorage.setItem('mw_lv',localStorage.getItem('mw_lv')==='1'?'0':'1');applyView()}
function cardHTML(x,list,i){
 var img=x.poster?('<img class="pimg" loading="lazy" decoding="async" alt="" src="'+esc(x.poster)+'">'):'';
 var noimg=x.poster?'':('<div class="noimg">'+esc(x.title)+'</div>');
 var q='<div class="tl">'+(x.quality?('<span class="q">'+esc(x.quality)+'</span>'):'')+'</div>';
 // Las series de EliteTorrent/WolfMax llegan agrupadas con sus capítulos
 // dentro: decir cuántos trae evita la duda de "¿esto es la serie entera?".
 var _nc=(x.eps&&x.eps.length)||0;
 var kt='<div class="kindtag">'+kindLabel(x.kind)+(_nc?(' · '+_nc+' cap.'):'')+'</div>';
 var SL={dt:'DT',et:'ET',dx:'DX',wf:'WF'};var s=x.source||'dt';
 var src='<div class="srctag s-'+s+'">'+(SL[s]||s.toUpperCase())+'</div>';
 return '<div class="card" style="--i:'+(i%12)+'"><div class="ph" onclick="openItem(\''+list+'\','+i+')">'+img+noimg+q+kt+src+
    '<div class="fav" onclick="favTap(\''+list+'\','+i+',event)">'+heartSVG(isFav(x))+'</div></div>'+
    '<div class="m" onclick="openItem(\''+list+'\','+i+')"><div class="t">'+esc(x.title)+'</div><div class="y">'+star(x)+'</div></div></div>';}
function renderGrid(el,list){var items=LISTS[list];var h='<div class="grid">';for(var i=0;i<items.length;i++)h+=cardHTML(items[i],list,i);h+='</div>';el.className='';el.innerHTML=h;lazyRar(el,list,0)}
function appendGrid(el,list,from){var g=el.querySelector('.grid');if(!g){renderGrid(el,list);return}var items=LISTS[list],h='';for(var i=from;i<items.length;i++)h+=cardHTML(items[i],list,i);g.insertAdjacentHTML('beforeend',h);lazyRar(el,list,from)}
// ---- Badge RAR (📦) perezoso para items de DonTorrent (vía /dtpacked) ----
var _rarCache={},_rarQ=[],_rarActive=0;
function lazyRar(el,list,from){var items=LISTS[list];var cd=(code.value||'').replace(/\D/g,'');
 // SOLIDEZ: NO pedir /dtpacked por cada peli DT de la cuadricula. Hacia ~30 PoW a
 // DonTorrent desde la IP de Render en CADA carga (Inicio, pelis, series, busqueda,
 // mi lista) -> es lo que mas BANEABA la IP de Render. La calidad ya sale del titulo
 // (x.quality) y el badge 📦 RAR se muestra en la FICHA (openItem pide /dtpacked 1
 // sola vez, cuando el usuario ABRE el item). Asi Render casi no toca DonTorrent ->
 // no se autobanea. El badge RAR de DivxTotal va por el BOX (IP residencial, no
 // banea a Render) y solo con codigo -> se mantiene.
 for(var i=from;i<items.length;i++){var x=items[i];if(x.kind!=='movie')continue;var s=x.source||'dt';
  if(s==='dx'&&cd.length===6)_rarQ.push({el:el,list:list,i:i,key:'dx:'+(x.url||x.content_id),f:'rar',url:'/catboxrar?code='+cd+'&src=dx&url='+encodeURIComponent(x.url||x.content_id)});}
 pumpRar()}
function pumpRar(){while(_rarActive<2&&_rarQ.length){var job=_rarQ.shift();
 var c=_rarCache[job.key];
 if(c!==undefined){if(c.rar)rarBadge(job);if(c.q)qualBadge(job,c.q);continue}
 _rarActive++;(function(job){fetch(job.url).then(function(r){return r.json()}).then(function(p){_rarActive--;
   var rar=!!(p&&p[job.f]===true);var q=(p&&p.quality)||'';
   _rarCache[job.key]={rar:rar,q:q};
   if(rar)rarBadge(job);if(q)qualBadge(job,q);pumpRar()}).catch(function(){_rarActive--;pumpRar()})})(job)}}
function rarBadge(job){var g=job.el.querySelector('.grid');if(!g)return;var cards=g.children;if(!cards||!cards[job.i])return;var tl=cards[job.i].querySelector('.tl');if(!tl||tl.querySelector('.rartag'))return;var b=document.createElement('span');b.className='rartag';b.textContent='📦 RAR';tl.appendChild(b)}
function qualBadge(job,q){if(!q)return;var g=job.el.querySelector('.grid');if(!g)return;var cards=g.children;if(!cards||!cards[job.i])return;var tl=cards[job.i].querySelector('.tl');if(!tl||tl.querySelector('.q'))return;var b=document.createElement('span');b.className='q';b.textContent=q;tl.insertBefore(b,tl.firstChild)}
// El corazón: un SVG (el ♡ de texto se ve distinto en cada móvil y no se puede
// animar). `on` = guardado; al marcarlo late una vez.
function heartSVG(on){return '<svg class="hsvg'+(on?' on':'')+'" viewBox="0 0 24 24" aria-hidden="true">'+
 '<path d="M12 20.6l-1.1-1C5.6 14.8 2.6 12.1 2.6 8.8 2.6 6.2 4.6 4.2 7.2 4.2c1.5 0 2.9.7 3.8 1.8L12 7.2l1-1.2c.9-1.1 2.3-1.8 3.8-1.8 2.6 0 4.6 2 4.6 4.6 0 3.3-3 6-8.3 10.8l-1.1 1z"/></svg>'}
function heartPaint(el,on){if(!el)return;el.innerHTML=heartSVG(on);
 if(on){var v=el.querySelector('.hsvg');if(v){v.classList.add('pop');
  setTimeout(function(){v.classList.remove('pop')},420)}}}
function favTap(list,i,ev){ev.stopPropagation();
 var x=LISTS[list][i];
 var el=ev.currentTarget||ev.target;
 guardarEn(x,function(){heartPaint(el,isFav(x));if(list==='lista')renderFavs()});}
function openItem(list,i){openCard(LISTS[list][i])}
// Versiones de la MISMA peli en otras fuentes. El 4K de WolfMax (capítulos de
// 9 GB) es lo mejor que da el sistema y antes se perdía en cuanto otra fuente
// ganaba la tarjeta: aquí se puede elegir a mano, siempre.
var ALTLBL={dt:'DonTorrent',dx:'DivxTotal',et:'EliteTorrent',wf:'WolfMax'};
function altsInner(x){
 // la actual + sus alternativas, SIN repetir fuente+calidad (ver addAlt)
 var by={},all=[];
 [x].concat(x.alts||[]).forEach(function(a){if(!a||!a.source)return;
  var k=altKey(a);if(by[k])return;by[k]=1;all.push(a)});
 if(all.length<2)return '';
 // ORDEN FIJO (mejor calidad primero, y a igualdad la fuente preferida). La
 // activa iba siempre delante, asi que los botones SALTABAN de sitio a cada
 // toque: en el movil acabas pulsando el que no era.
 all.sort(function(a,b){var d=srcScore(b)-srcScore(a);if(d)return d;
  return (altKey(a)<altKey(b))?-1:1});
 var kx=altKey(x);
 return '<span class="altq" style="align-self:center">También en:</span>'+
  all.map(function(a){var k=altKey(a);
   return '<button class="altb'+(k===kx?' on':'')+'" onclick="pickAlt(\''+esc(k)+'\')">'+
   esc(ALTLBL[a.source||'dt']||'?')+(a.quality?('<span class="altq">'+esc(a.quality)+'</span>'):'')+'</button>'}).join('');}
function altsHTML(x){var h=altsInner(x);return h?('<div class="sh-alts">'+h+'</div>'):'';}
function renderAlts(x){var el=$('sh-alts');if(el)el.innerHTML=altsInner(x);}
function pickAlt(clave){
 // el item que se está viendo: el del overlay de serie si está abierto, si no
 // el de la ficha. (Antes se usaba `sel` siempre y en las series no se
 // actualizaba -> no se podía volver a la fuente anterior.)
 var ov=$('ov')&&$('ov').classList.contains('on');
 var act=(ov&&OVDATA&&OVDATA.x)?OVDATA.x:sel;
 if(!act)return;
 var all=[act].concat(act.alts||[]);
 var a=null;
 for(var j=0;j<all.length;j++){if(altKey(all[j])===clave){a=all[j];break}}
 // por compatibilidad: si viene solo la fuente, la primera de esa fuente
 if(!a)for(var j2=0;j2<all.length;j2++){if((all[j2].source||'dt')===clave){a=all[j2];break}}
 if(!a||a===act)return;
 var resto=all.filter(function(z){return z!==a});
 var c={};for(var k in a)if(k!=='alts')c[k]=a[k];c.alts=resto;
 sel=c;
 if(c.kind==='serie'){openSeries(c);return}
 openCard(c);}
// Abre la FICHA de un item (sheet de peli u overlay de serie) a partir del
// OBJETO -> sirve igual para una tarjeta de la cuadricula que para un enlace
// COMPARTIDO reconstruido (abrir directo la peli/serie, sin buscar).
function openCard(x){if(!x)return;sel=x;if(x.kind==='serie'){openSeries(x);return}
 var SL={dt:'DonTorrent',et:'EliteTorrent',dx:'DivxTotal',wf:'WolfMax4K'};var s2=x.source||'dt';
 var sy=star(x);if(x.quality)sy+=(sy?' · ':'')+x.quality;if(SL[s2])sy+=' · '+SL[s2];
 var pst=$('sh-poster');if(x.poster){pst.style.backgroundImage='url("'+x.poster+'")';pst.classList.remove('hidden')}else{pst.style.backgroundImage='';pst.classList.add('hidden')}
 ZPOSTER=x.poster||'';
 $('sh-t').textContent=x.title;$('sh-y').textContent=sy;
 renderAlts(x);$('sh-fav').textContent=favLabel(x);$('sh-rar').textContent='';
 // backdrop + géneros + sinopsis + tráiler. shEnrich pinta lo que el item TENGA;
 // enrichItem rellena los favoritos GUARDADOS sin enriquecer (1 vez, se persiste).
 shEnrich(x);
 if(!(x.overview&&x.genres&&x.genres.length)){$('sh-ovwrap').innerHTML='<div class="sh-ovload"><span class="spin"></span> Cargando detalles…</div>';}
 enrichItem(x,function(){if(sel===x)shEnrich(x);});
 // SEMILLAS: SIEMPRE se muestran -> "comprobando" y luego numero / "sin semillas"
 // (0) / aviso claro. DT y DivxTotal: directo (relay). ET/WF: via box (con codigo).
 $('sh-seeds').innerHTML='<span class="seedtag" style="opacity:.6">🌱 comprobando…</span>';$('sheet').classList.add('on');var _bx=$('sheet').querySelector('.box');if(_bx)_bx.scrollTop=0;mwOpen('sheet',_bx,_closeSheet);
 var _cd=(code.value||'').replace(/\D/g,'');
 var seedShow=function(p){if(sel!==x)return;$('sh-seeds').innerHTML=(p&&typeof p.seeds==='number')?seedTag(p.seeds):seedFail(s2,_cd);};
 if(s2==='dt'){fetch('/dtpacked?c='+encodeURIComponent(x.content_id)+'&tb='+encodeURIComponent(x.tabla||'peliculas')).then(function(r){return r.json()}).then(function(p){if(sel!==x)return;if(p&&p.packed===true)$('sh-rar').textContent='📦 Viene comprimido (RAR) — puede que no se reproduzca.';seedShow(p)}).catch(function(){seedShow(null)})}
 else if(s2==='dx'){fetch('/seeds?src=dx&url='+encodeURIComponent(x.url||x.content_id)).then(function(r){return r.json()}).then(seedShow).catch(function(){seedShow(null)})}
 // ET/WF: SIEMPRE, con o sin código — el relay resuelve el enlace por cualquier
 // caja viva. Las semillas tienen que verse en TODAS las fuentes.
 else{fetch('/seeds?code='+_cd+'&src='+encodeURIComponent(s2)+'&url='+encodeURIComponent(x.url||x.content_id)).then(function(r){return r.json()}).then(seedShow).catch(function(){seedShow(null)})}}
function seedTag(n){var c,t;if(n<=0){c='s-zero';t='⚠ Sin semillas';}else if(n<3){c='s-low';t='🌱 '+n+' semilla'+(n===1?'':'s');}else{c='s-ok';t='🌱 '+n+' semillas';}return '<span class="seedtag '+c+'">'+t+'</span>';}
// Ya no se pide el código para las semillas de ET/WF (el relay usa cualquier
// caja viva), así que el aviso es el mismo para todas las fuentes.
function seedFail(src,cd){return '<span class="seedtag" style="opacity:.6">🌱 semillas no disponibles</span>';}
// INSTANTÁNEO: el play sale a la tele YA; las semillas se comprueban EN PARALELO
// y solo avisan (sin bloquear ni preguntar) si el enjambre está muerto. Antes
// esto esperaba hasta 6s ANTES de enviar -> delay regalado (la IP de Render
// baneada hacía que /dtseeds tardase). Reproducir debe sentirse instantáneo.
function seedGate(ci,tb,proceed){proceed();
 fetch('/dtseeds?c='+encodeURIComponent(ci)+'&tb='+encodeURIComponent(tb)).then(function(r){return r.json()}).then(function(d){var s=d&&d.seeds;
  if(s===0)toast('⚠ Esta versión no tiene semillas; si no arranca, prueba otra');
  else if(typeof s==='number'&&s>0&&s<3)toast('Pocas semillas ('+s+') — puede tardar en arrancar');
 }).catch(function(){})}
function favLabel(x){
 if(!isFav(x))return '♡ Guardar en una lista';
 var f=favBuscar(x),ls=lstDe(f).map(lstNombre).filter(Boolean);
 return '♥ En \u00ab'+(ls[0]||'')+'\u00bb';}
function sheetFav(){guardarEn(sel,function(){var b=$('sh-fav');if(b)b.textContent=favLabel(sel)})}
function ovFav(){guardarEn(sel,function(){var b=$('ov-fav');if(b)b.textContent=favLabel(sel)})}
function _closeSheet(){$('sheet').classList.remove('on')}
function closeSheet(){mwBack('sheet')}
// Pinta la parte enriquecida de la ficha de PELI con lo que el item tenga
// (backdrop + generos + duracion + sinopsis + trailer). Reentrante: se vuelve a
// llamar cuando enrichItem/catmeta rellenan datos -> rerender suave.
function shEnrich(x){
 var hb=x.backdrop||x.poster||'';$('sh-hero').style.backgroundImage=hb?('url("'+hb+'")'):'';
 var gh=(x.genres||[]).map(function(g){return '<span class="gtag">'+esc(g)+'</span>'}).join('');
 if(x.runtime){var hh=Math.floor(x.runtime/60),mm=x.runtime%60,rt=(hh?hh+'h ':'')+(mm?mm+'m':'');if(rt)gh+='<span class="runt">'+rt+'</span>';}
 $('sh-genres').innerHTML=gh;
 $('sh-ovwrap').innerHTML=x.overview?('<div class="sh-ov clamp" id="sh-ov">'+esc(x.overview)+'</div><span class="sh-more" onclick="toggleOv()">Leer más</span>'):'';
 var tb=$('sh-trailer');if(x.trailer){TRK=x.trailer;tb.style.display='';}else{TRK='';tb.style.display='none';}
 // duracion/trailer perezosos via /catmeta si tenemos tmdb_id y aun no el trailer
 if(!x.trailer&&x.tmdb_id&&!x._mt){x._mt=1;fetch('/catmeta?id='+encodeURIComponent(x.tmdb_id)+'&kind='+(x.kind==='serie'?'tv':'movie')).then(function(r){return r.json()}).then(function(m){if(!m)return;if(m.trailer)x.trailer=m.trailer;if(m.runtime&&!x.runtime)x.runtime=m.runtime;persistFavMeta(x);if(sel===x)shEnrich(x);}).catch(function(){});}
}
// Enriquece un item por TITULO si le faltan los campos nuevos (favoritos viejos
// de "Mi lista"). 1 sola llamada, y persiste en la lista -> la proxima vez instantaneo.
function enrichItem(x,cb){
 // Enriquece si FALTAN los campos visibles (sinopsis/generos), aunque ya tenga
 // tmdb_id (un favorito viejo puede llevar tmdb_id pero no sinopsis). _tried evita
 // repetir en la misma sesion si TMDB no encontro nada. El trailer lo trae shEnrich.
 if((x.overview&&x.genres&&x.genres.length)||x._tried){cb&&cb();return;}
 x._tried=1;
 fetch('/cattitlemeta?title='+encodeURIComponent(x.title||'')+(x.year?('&year='+encodeURIComponent(x.year)):'')+'&kind='+(x.kind==='serie'?'tv':'movie')).then(function(r){return r.json()}).then(function(m){
  if(m){if(m.overview)x.overview=m.overview;if(m.backdrop)x.backdrop=m.backdrop;if(m.genres&&m.genres.length)x.genres=m.genres;if(m.tmdb_id)x.tmdb_id=m.tmdb_id;persistFavMeta(x);}
  cb&&cb();
 }).catch(function(){cb&&cb();});
}
// Si el item es un favorito, copia los campos enriquecidos al guardado y persiste
// (localStorage + sync con el relay) -> "Mi lista" se enriquece sola y para siempre.
function persistFavMeta(x){var i=-1;for(var j=0;j<favs.length;j++){if(fk(favs[j])===fk(x)){i=j;break;}}
 if(i<0)return;var f=favs[i];   // copia (idempotente; f puede SER x si se abrio desde la lista)
 ['overview','backdrop','genres','tmdb_id','trailer','runtime'].forEach(function(k){if(x[k]!=null)f[k]=x[k];});
 saveFavs();mlPushSoon();
}
// Esqueletos de carga (Inicio): tarjetas con brillo mientras llega TMDB.
function skelGrid(n){n=n||9;var c='<div class="skcard"><div class="skph shim"></div><div class="skln shim"></div><div class="skln s2 shim"></div></div>';var h='<div class="skgrid">';for(var i=0;i<n;i++)h+=c;return h+'</div>'}
// Sinopsis: alternar recortada/completa.
function toggleOv(){var o=$('sh-ov');if(!o)return;var cl=o.classList.toggle('clamp');var m=o.nextElementSibling;if(m)m.textContent=cl?'Leer más':'Leer menos'}
// Zoom de portada: tocar el póster de la ficha lo agranda a pantalla completa.
function zoomPoster(){var p=ZPOSTER||(sel&&sel.poster);if(!p)return;event&&event.stopPropagation&&event.stopPropagation();$('zoom-img').src=p.replace('/w342','/w500');$('zoom').classList.add('on');mwOpen('zoom',$('zoom'),_closeZoom)}
function _closeZoom(){$('zoom').classList.remove('on');$('zoom-img').src=''}
function closeZoom(){mwBack('zoom')}
// Tráiler: reproduce el vídeo de YouTube en un modal (clave de /catmeta).
function openTrailer(){if(!TRK)return;$('trm-mount').innerHTML='<iframe src="https://www.youtube.com/embed/'+TRK+'?autoplay=1&rel=0&playsinline=1" allow="autoplay; encrypted-media; fullscreen" allowfullscreen></iframe>';$('trm').classList.add('on');mwOpen('trm',$('trm'),_closeTrailer)}
function toggleOvSyn(){var o=$('ov-syn');if(!o)return;var cl=o.classList.toggle('clamp');var m=o.nextElementSibling;if(m)m.textContent=cl?'Leer más':'Leer menos'}
function _closeTrailer(){$('trm').classList.remove('on');$('trm-mount').innerHTML=''}
function closeTrailer(){mwBack('trm')}
// ---- Compartir enlace directo (como el mando): link que reproduce al abrirlo ----
function doShare(t,yr,qs){var link=location.origin+'/cat?'+qs+'&t='+encodeURIComponent(t)+(yr?('&yr='+encodeURIComponent(yr)):'');
 var nice=t+(yr?(' ('+yr+')'):'');
 if(navigator.share){navigator.share({title:'MejorWolf',text:'Te recomiendo «'+nice+'» en MejorWolf',url:link}).then(function(){},function(){})}
 else if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(link).then(function(){toast('Enlace copiado')},function(){_linkAMano(link)})}
 else{_linkAMano(link)}}
// Si el navegador no deja copiar solo, se enseña el enlace en NUESTRO cuadro
// (seleccionable), en vez del prompt del navegador.
function _linkAMano(link){mwPrompt('Copia el enlace',link,'Mantén pulsado para copiarlo',function(){});}
// Compartir PELÍCULA: el enlace abre la FICHA de esa peli en la web del amigo
// (póster, calidad, semillas, reproducir con SU código) -> NO una búsqueda.
function shareSheet(){if(!sel)return;var x=sel,t=x.title||'',yr=x.year||'',src=x.source||'dt';
 var qs='open=peli&src='+encodeURIComponent(src);
 if(src==='dt')qs+='&ci='+encodeURIComponent(x.content_id||'')+'&tb='+encodeURIComponent(x.tabla||'peliculas');
 else qs+='&url='+encodeURIComponent(x.url||x.content_id||'');
 if(x.quality)qs+='&q='+encodeURIComponent(x.quality);
 if(x.poster)qs+='&ps='+encodeURIComponent(x.poster);
 doShare(t,yr,qs);}
// Compartir SERIE: el enlace abre directamente la ficha de la serie con sus
// temporadas/episodios (openSeries), NO una búsqueda del título.
function shareSeries(){if(!OVDATA)return;var x=OVDATA.x,d=OVDATA.d;var t=(d.title||x.title||'');if(!t)return;
 var src=x.source||'dt';
 var qs='open=serie&src='+encodeURIComponent(src);
 if(src==='dt')qs+='&path='+encodeURIComponent(x.path||'');
 else qs+='&url='+encodeURIComponent(x.url||x.content_id||'');
 var ps=(d.poster||x.poster||'');if(ps)qs+='&ps='+encodeURIComponent(ps);
 doShare(t,(d.year||x.year||''),qs);}
var sharedPlay=null;
function showShared(t){$('shared-t').textContent=t||'Compartido';$('shared').classList.add('on')}
function playShared(){if(sharedPlay&&sendPlay(sharedPlay))$('shared').classList.remove('on')}
function closeShared(){$('shared').classList.remove('on')}
function play(){if(!sel)return;
 if(sel.source&&sel.source!=='dt'){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){avisaCodigo();return}
  toast('Resolviendo en tu box…');
  fetch('/catetboxresolve?code='+cd+'&src='+encodeURIComponent(sel.source)+'&url='+encodeURIComponent(sel.url||sel.content_id)).then(function(r){return r.json()}).then(function(d){
   if(d&&d.link){if(sendPlay({a:'pl',u:d.link,t:sel.title}))closeSheet()}else{toast('No se pudo (¿box encendido?)')}}).catch(function(){toast('No se pudo obtener el enlace')});
  return}
 var _x=sel;seedGate(_x.content_id,_x.tabla||'peliculas',function(){if(sendPlay({a:'dt',c:_x.content_id,tb:_x.tabla,t:_x.title}))closeSheet()})}
function sendPlay(ref){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){avisaCodigo();return false}
 var body={code:cd,cmd:'play_ref',a:ref.a||'dt',t:ref.t};
 if((ref.a||'dt')==='pl'){body.u=ref.u}else{body.c=ref.c;body.tb=ref.tb}
 toast('Enviando a la tele...');
 var hsnap=null;try{hsnap=histSnap(ref)}catch(e){}   // ver histSnap: `sel` cambia
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(function(r){return r.json()}).then(function(d){if(d&&d.ok){lastPlayTs=Date.now();toast('▶ En la tele');try{histPush(hsnap)}catch(e){};closeSheet();closeOv();openRemote();setTimeout(pollNow,1500)}else{toast('Error: '+((d&&d.error)||'?'))}}).catch(function(){toast('No se pudo enviar')});
 return true}
function openSeries(x){SHOW=x.title;EPS={};OVDATA=null;OVSEASON=null;sel=x;$('ov').classList.add('on');mwOpen('ov',$('ov'),_closeOv);$('ov-title').textContent=x.title;
 // Favorito GUARDADO sin enriquecer: rellena por titulo y, al volver, re-render del hero.
 enrichItem(x,function(){if(OVDATA&&OVDATA.x===x)renderEpisodes();});
 $('ov-body').innerHTML='<div class="msg"><span class="spin"></span> Cargando episodios...</div>';
 var src=x.source||'dt';var cd=(code.value||'').replace(/\D/g,'');
 // EliteTorrent y WolfMax publican una ficha POR CAPÍTULO, así que el relay ya
 // los agrupa y manda la lista DENTRO de la tarjeta: aquí no hay nada que pedir,
 // los capítulos salen al instante (0 red, 0 trabajo para ninguna caja).
 if(x.eps&&x.eps.length){OVDATA={d:{title:x.title,episodes:x.eps,poster:x.poster,
   year:x.year,rating:x.rating,backdrop:x.backdrop,overview:x.overview,genres:x.genres},x:x};
  renderEpisodes();
  // ...y ademas se pide la lista COMPLETA a la caja, por detras. Lo que trae la
  // tarjeta es lo que el indice tiene indexado (en Ted Lasso, 15 capitulos);
  // la caja lee las paginas de la serie y da las cuatro temporadas (35). Si
  // llega mas, se funde y la ficha se repinta sola; si no llega nada, se queda
  // lo que ya habia.
  completaFicha(x);
  return;}
 // DT lleva el code -> si Render esta baneado por DonTorrent, el relay trae los
 // episodios por TU box (IP de casa). Sin code igualmente intenta directo.
 var u=(src==='dt')?('/catdetail?path='+encodeURIComponent(x.path||'')+(cd.length===6?('&code='+cd):'')):('/catboxeps?code='+cd+'&src='+src+'&url='+encodeURIComponent(x.url||x.content_id)+'&t='+encodeURIComponent(x.title||''));
 // Salvavidas: nunca dejar "Cargando episodios..." para siempre (relay saturado).
 var ac=(window.AbortController?new AbortController():null);var opt=ac?{signal:ac.signal}:undefined;
 var kill=setTimeout(function(){if(ac)try{ac.abort()}catch(e){}},26000);
 fetch(u,opt).then(function(r){return r.json()}).then(function(d){clearTimeout(kill);
  var eps=(d&&d.episodes)||[];
  // PLAN B: los capítulos que trajo otra fuente en la búsqueda (ver upgrade).
  if(!eps.length&&x.epsAlt&&x.epsAlt.length){OVDATA={d:{title:x.title,episodes:x.epsAlt,
    poster:x.poster,year:x.year,rating:x.rating,backdrop:x.backdrop,overview:x.overview,
    genres:x.genres},x:x};renderEpisodes();return;}
  if(!eps.length){OVRETRY=x;
   // CON LAS ALTERNATIVAS: sin ellas no se puede volver a la fuente que si
   // tenia los capitulos y hay que cerrar la ficha entera.
   $('ov-body').innerHTML=altsHTML(x)+'<div class="msg">No se pudieron leer los episodios'+((src!=='dx')?' (enciende tu Kodi e inténtalo de nuevo)':'')+'. <a href="javascript:void(0)" onclick="openSeries(OVRETRY)">Reintentar</a>'+
    (((x.alts||[]).length)?'<br><br>O prueba otra fuente aquí arriba ↑':'')+'</div>';return}
  OVDATA={d:d,x:x};favLearnEps(x,eps);renderEpisodes();
 }).catch(function(){clearTimeout(kill);
  if(x.epsAlt&&x.epsAlt.length){OVDATA={d:{title:x.title,episodes:x.epsAlt,poster:x.poster,
    year:x.year,rating:x.rating,backdrop:x.backdrop,overview:x.overview,genres:x.genres},x:x};
   renderEpisodes();return;}
  OVRETRY=x;$('ov-body').innerHTML=altsHTML(x)+'<div class="msg">No se pudieron cargar los episodios. <a href="javascript:void(0)" onclick="openSeries(OVRETRY)">Reintentar</a>'+
    (((x.alts||[]).length)?'<br><br>O prueba otra fuente aquí arriba ↑':'')+'</div>'})}
var OVRETRY=null;
// El PRIMER capitulo sin ver de TODA la serie, en orden. Es "por donde ibas".
// Mira la serie ENTERA (OVDATA), no solo la temporada abierta: con las pestanas
// EPS solo tiene la que se esta viendo, y asi el boton se equivocaba (decia
// "Empezar por 2x01" con la T1 entera vista, y desaparecia estando en una
// temporada vista aunque quedaran capitulos en otra).
function _todosLosEps(){
 var eps=((OVDATA&&OVDATA.d&&OVDATA.d.episodes)||[]).slice();
 eps.sort(function(a,b){return ((a.season||0)-(b.season||0))||((a.episode||0)-(b.episode||0))});
 return eps;}
function _primerSinVer(){
 var eps=_todosLosEps();
 for(var i=0;i<eps.length;i++)if(!isSeen(eps[i].content_id))return eps[i];
 return null;}
function _seguirHTML(){
 // OJO: EPS se llena mas abajo, al pintar los capitulos; por eso este boton se
 // rellena DESPUES (ver el final de renderEpisodes).
 return '<button class="ovseguir" id="ov-seguir" style="display:none" onclick="seguirViendo()"></button>';}
function _seguirPinta(){
 var b=$('ov-seguir');if(!b)return;
 var e=_primerSinVer();
 if(!e){b.style.display='none';return}
 var vistos=_todosLosEps().filter(function(z){return isSeen(z.content_id)}).length;
 b.style.display='';
 b.innerHTML='\u25b6 <b>'+(vistos?'Seguir por ':'Empezar por ')+esc(e.label)+'</b>'+
  (e.quality?(' <span>'+esc(e.quality)+'</span>'):'');}
function seguirViendo(){
 var e=_primerSinVer();if(!e)return;
 // puede estar en otra temporada (no pintada): se le da un hueco en EPS para
 // que playEp lo resuelva igual que a cualquier otro capitulo
 var id=null;
 for(var k in EPS)if(EPS[k]===e){id=k;break}
 if(!id){id='eseguir';EPS[id]=e}
 playEp(id);}
// Completa la ficha por detras con lo que tenga la caja (ver openSeries).
// Solo para fuentes-box (WolfMax/EliteTorrent), que son las que publican una
// ficha por capitulo y donde el indice se queda corto.
var _COMPLETANDO='';
function completaFicha(x){
 var src=x.source||'dt';
 if(src!=='wf'&&src!=='et')return;
 var u=x.url||x.content_id;if(!u)return;
 var k=src+':'+u;if(_COMPLETANDO===k)return;_COMPLETANDO=k;
 var cd=(code.value||'').replace(/\D/g,'');
 fetch('/catboxeps?full=1&code='+cd+'&src='+src+'&url='+encodeURIComponent(u)+
       '&t='+encodeURIComponent(x.title||''))
  .then(function(r){return r.json()}).then(function(d){
   var eps=(d&&d.episodes)||[];
   if(!eps.length)return;
   if(!OVDATA||OVDATA.x!==x)return;            // ya no esta abierta esa ficha
   var antes=(OVDATA.d.episodes||[]).length;
   var union=mergeEps(OVDATA.d.episodes||[],eps);
   if(union.length<=antes)return;              // no aporta nada nuevo
   OVDATA.d.episodes=union;
   x.eps=slimEps(union);                       // y la tarjeta se lo queda
   try{favLearnEps(x,union)}catch(e){}
   renderEpisodes();
   toast('Serie completa: '+union.length+' cap\u00edtulos');
  }).catch(function(){});}
var OVSEASON=null;      // temporada abierta en la ficha (ver las pestanas)
function ovTemp(s){OVSEASON=s;renderEpisodes();
 try{var b=$('ov-body');if(b)b.scrollTop=0}catch(e){}}
function renderEpisodes(){if(!OVDATA)return;var d=OVDATA.d,x=OVDATA.x;EPS={};var _epi=0;
 var eps=(d&&d.episodes)||[];var poster=(d&&d.poster)||x.poster;
 var seasons={};eps.forEach(function(e){var s=e.season||0;(seasons[s]=seasons[s]||[]).push(e)});
 var keys=Object.keys(seasons).map(Number).sort(function(a,b){return a-b});
 var ph=poster?(' style="background-image:url('+poster+')"'):'';
 // Hero enriquecido (igual estilo que la ficha de peli): backdrop + portada
 // (con zoom) + genero + sinopsis. Degrada elegante si el item no trae datos.
 ZPOSTER=poster||'';TRK='';
 var bd=x.backdrop||d.backdrop||poster||'';
 var genh=(x.genres||d.genres||[]).map(function(g){return '<span class="gtag">'+esc(g)+'</span>'}).join('');
 var ovw=x.overview||d.overview||'';
 var h='<div class="ovhero"'+(bd?(' style="background-image:url('+bd+')"'):'')+'><div class="grad"></div>'+
   '<div class="ovhero-row"><div class="ovposter"'+ph+' onclick="zoomPoster()"></div>'+
   '<div class="ovhero-txt"><div class="ovh-t">'+esc(d.title||x.title)+'</div>'+
   '<div class="ovh-y">'+esc(star({year:d.year||x.year,rating:d.rating}))+'</div>'+
   (genh?('<div class="ovgen">'+genh+'</div>'):'')+'</div></div></div>'+
   (ovw?('<div class="ovsyn"><div class="sh-ov clamp" id="ov-syn">'+esc(ovw)+'</div><span class="sh-more" onclick="toggleOvSyn()">Leer más</span></div>'):'')+
   altsHTML(x)+
   _seguirHTML()+
   '<div class="ovactions"><button class="ovfav" id="ov-fav" onclick="ovFav()">'+favLabel(x)+'</button> <button class="ovfav" onclick="shareSeries()">📤 Compartir</button> <button class="ovfav" id="ov-trailer" style="display:none" onclick="openTrailer()">🎬 Tráiler</button></div>';
 // PESTANAS DE TEMPORADA: la ficha trae la serie entera, y verlas todas
 // seguidas era un scroll eterno. Se abre por la primera con algo sin ver.
 if(keys.length>1){
  if(OVSEASON===null||keys.indexOf(OVSEASON)<0){
   var _pend=null;
   for(var _k=0;_k<keys.length;_k++){
    var _l=seasons[keys[_k]];
    if(!_l.every(function(e){return isSeen(e.content_id)})){_pend=keys[_k];break}}
   OVSEASON=(_pend!==null)?_pend:keys[keys.length-1];}
  h+='<div class="tmp">'+keys.map(function(s){
   var _l=seasons[s],_v=_l.every(function(e){return isSeen(e.content_id)});
   return '<button class="tmpb'+(s===OVSEASON?' on':'')+(_v?' vista':'')+'" onclick="ovTemp('+s+')">'+
    'T'+(s||'?')+'<i>'+_l.length+'</i></button>'}).join('')+'</div>';}
 keys.forEach(function(s){
  if(keys.length>1&&s!==OVSEASON)return;
  var list=seasons[s];list.sort(function(a,b){return (a.episode||0)-(b.episode||0)});var allseen=list.every(function(e){return isSeen(e.content_id)});
  if(keys.length>1||s>0)h+='<div class="seas"><span>Temporada '+(s||'?')+'</span><span class="seasmark" onclick="markSeason('+s+')">'+(allseen?'Marcar no vista':'Marcar toda vista')+'</span></div>';
  list.forEach(function(e){var id='e'+(_epi++);EPS[id]=e;var sn=isSeen(e.content_id);
   h+='<div class="ep'+(sn?' seen':'')+'" id="row-'+id+'"><div class="epmain" onclick="playEp(\''+id+'\')"><span class="epl"><span class="chk">✓</span>'+esc(e.label)+(e.quality?(' <span class="epq" data-q="'+esc(e.quality)+'">'+esc(e.quality)+'</span>'):'')+'<span class="epb" id="epb-'+id+'"></span></span></div>'+
     '<div class="eye" onclick="event.stopPropagation();markSeen(\''+id+'\')" title="Marcar como visto">'+(sn?EYE_ON:EYE_OFF)+'</div></div>'});
 });$('ov-body').innerHTML=h;_seguirPinta();lazyEps();
 // Tráiler de la serie: si ya lo tenemos (de enrichItem) lo mostramos; si no,
 // perezoso via /catmeta (kind=tv) y se persiste en la lista.
 if(x.trailer){TRK=x.trailer;var _ovb=$('ov-trailer');if(_ovb)_ovb.style.display='';}
 else if(x.tmdb_id){fetch('/catmeta?id='+encodeURIComponent(x.tmdb_id)+'&kind=tv').then(function(r){return r.json()}).then(function(m){if(!OVDATA||OVDATA.x!==x||!m||!m.trailer)return;x.trailer=m.trailer;TRK=m.trailer;var b=$('ov-trailer');if(b)b.style.display='';persistFavMeta(x);}).catch(function(){});}}
// ---- Semillas + RAR por capitulo (DonTorrent), perezoso y cacheado ----
var _epQ=[],_epActive=0,_epCache={};
function lazyEps(){_epQ=[];var cd=(code.value||'').replace(/\D/g,'');var src=(OVDATA&&OVDATA.x&&(OVDATA.x.source||'dt'))||'dt';
 Object.keys(EPS).forEach(function(id){var e=EPS[id];if(!e)return;
  if(e.link){var u='/seeds?link='+encodeURIComponent(e.link)+(cd.length===6?('&code='+cd+'&src='+encodeURIComponent(src)):'');_epQ.push({id:id,key:'lk:'+e.link,url:u});}
  else if(e.src==='et'||e.src==='wf'){var _u2=e.url||e.content_id;
   if(_u2)_epQ.push({id:id,key:e.src+':'+_u2,url:'/seeds?code='+cd+'&src='+encodeURIComponent(e.src)+'&url='+encodeURIComponent(_u2)});}
  else if(e.content_id&&e.tabla){_epQ.push({id:id,key:'dt:'+e.tabla+':'+e.content_id,url:'/dtpacked?c='+encodeURIComponent(e.content_id)+'&tb='+encodeURIComponent(e.tabla)});}
 });pumpEp();}
function pumpEp(){while(_epActive<2&&_epQ.length){var job=_epQ.shift();var c=_epCache[job.key];
  if(c!==undefined){epBadge(job,c);continue;}
  _epActive++;(function(job){fetch(job.url).then(function(r){return r.json()}).then(function(p){_epActive--;
   var info={seeds:(p&&typeof p.seeds==='number')?p.seeds:null,rar:!!(p&&p.packed===true)};_epCache[job.key]=info;epBadge(job,info);pumpEp();}).catch(function(){_epActive--;pumpEp();});})(job);}}
function epBadge(job,info){var el=document.getElementById('epb-'+job.id);if(!el)return;var h='';
  if(info.rar)h+='<span class="ep-rar">📦 RAR</span>';
  if(typeof info.seeds==='number'){var cls=info.seeds<=0?'s-zero':(info.seeds<3?'s-low':'s-ok');h+='<span class="ep-seed '+cls+'">🌱 '+info.seeds+'</span>';}
  el.innerHTML=h;}
function _closeOv(){$('ov').classList.remove('on')}
function closeOv(){mwBack('ov')}
function markSeen(id){var e=EPS[id];if(!e)return;toggleSeen(e.content_id);var row=$('row-'+id);
 if(row){var sn=isSeen(e.content_id);row.classList.toggle('seen',sn);var ey=row.querySelector('.eye');if(ey)ey.innerHTML=sn?EYE_ON:EYE_OFF;}
 // el boton "Seguir por..." apunta al primero sin ver: hay que recalcularlo
 try{_seguirPinta()}catch(e2){}}
function markSeason(s){if(!OVDATA)return;var eps=(OVDATA.d.episodes||[]).filter(function(e){return (e.season||0)===s});
 var allseen=eps.every(function(e){return isSeen(e.content_id)});
 eps.forEach(function(e){var cur=isSeen(e.content_id);if(allseen&&cur)toggleSeen(e.content_id);else if(!allseen&&!cur)toggleSeen(e.content_id)});renderEpisodes();}
function playEp(id){var e=EPS[id];if(!e)return;
 // `q`: la calidad de ESTE capitulo. Una serie mezcla calidades (el 1x01 de
 // Silo es 1080p aunque la tarjeta sea 4K), y el historial ensenaba la de la
 // tarjeta, que podia no ser la que se esta viendo.
 if(e.link){if(sendPlay({a:'pl',u:e.link,t:(SHOW+' '+e.label).trim(),q:e.quality}))closeOv();return}
 // Capítulo de EliteTorrent/WolfMax: su enlace vive en la ficha del capítulo y
 // lo resuelve una caja (IP residencial). Se avisa porque tarda 1-3s, y NO se
 // cierra la ficha hasta que sale de verdad -> nada de "he pulsado y no pasa nada".
 if(e.src==='et'||e.src==='wf'){var _t2=(SHOW+' '+e.label).trim();
  var _cd2=(code.value||'').replace(/\D/g,'');
  if(_cd2.length!==6){avisaCodigo();return}
  toast('Preparando '+e.label+'…');
  fetch('/catetboxresolve?code='+_cd2+'&src='+encodeURIComponent(e.src)+'&url='+encodeURIComponent(e.url||e.content_id))
   .then(function(r){return r.json()}).then(function(d){
    if(d&&d.link){if(sendPlay({a:'pl',u:d.link,t:_t2,q:e.quality}))closeOv();}
    else toast('No se pudo obtener el enlace de ese capítulo');
   }).catch(function(){toast('No se pudo obtener el enlace de ese capítulo')});
  return}
 var ttl=(SHOW+' '+e.label).trim();seedGate(e.content_id,e.tabla||'series',function(){if(sendPlay({a:'dt',c:e.content_id,tb:e.tabla,t:ttl,q:e.quality}))closeOv()})}
function seekTo(){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){toast('Pon tu código');return}
 var v=($('rm-min').value||'').trim();if(v===''){toast('Pon un minuto');return}var mn=parseInt(v,10);if(isNaN(mn)||mn<0){toast('Minuto no válido');return}
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:cd,cmd:'seekto',min:mn})}).then(function(r){return r.json()}).then(function(d){if(d&&d.ok){toast('Saltando al minuto '+mn);$('rm-min').value='';setTimeout(pollNow,700)}else{toast('Error: '+((d&&d.error)||'?'))}}).catch(function(){toast('No se pudo')})}
function fmt(s){s=Math.max(0,s||0);var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);return (h?h+':':'')+(h?('0'+m).slice(-2):m)+':'+('0'+x).slice(-2)}
// Play/pausa OPTIMISTA: al pulsar, volteamos el icono YA (sin esperar al sondeo)
// -> el mando se siente instantaneo. El proximo pollNow (<=0.5s, lo dispara cmd)
// confirma el estado real y corrige si hiciera falta.
function applyPP(){var a=$('np-pp');if(a)a.innerHTML=npPaused?NP_PLAY:NP_PAUSE;var b=$('rm-pp');if(b)b.innerHTML=npPaused?SVG_PLAY:SVG_PAUSE;}
function pp(){npPaused=!npPaused;applyPP();cmd('playpause');}
function pollNow(){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){clearTimeout(npTimer);npTimer=setTimeout(pollNow,4000);return}
 fetch('/kb/now?code='+cd).then(function(r){return r.json()}).then(function(d){var np=d&&d.np;var bar=$('npbar');
  if(np&&np.title){bar.classList.add('on');npPaused=!!np.paused;var _fb=$('fab');if(_fb)_fb.style.display='none';var pct=np.total?Math.min(100,Math.round(np.elapsed/np.total*100)):0;
   var fin='';if(!np.paused&&np.total>0)fin=clk(new Date(Date.now()+(np.total-np.elapsed)*1000));
   $('np-t').textContent=np.title+(fin?(' · Finaliza '+fin):'');
   $('np-prog').style.width=pct+'%';$('np-pp').innerHTML=np.paused?NP_PLAY:NP_PAUSE;
   $('rm-t').textContent=np.title;$('rm-time').textContent=fmt(np.elapsed)+(np.total?(' / '+fmt(np.total)):'');
   $('rm-fin').textContent=np.paused?'En pausa':(np.total>0?('Finaliza a las '+fin):'');
   $('rm-prog').style.width=pct+'%';$('rm-pp').innerHTML=np.paused?SVG_PLAY:SVG_PAUSE;}
  else{bar.classList.remove('on');var _fb2=$('fab');if(_fb2)_fb2.style.display='';if($('remote').classList.contains('on')){$('rm-t').textContent='Preparando en la tele…';$('rm-time').textContent='';$('rm-fin').textContent='';}}
  // Sondeo AGIL (3s) solo si hay algo en marcha o el mando esta abierto (el usuario
  // espera ver arrancar). IDLE navegando el catalogo -> 12s: menos bateria, menos
  // datos y menos carga al relay gratis. Un play vuelve a 3s (cmd dispara pollNow).
  var act=(np&&np.title)||$('remote').classList.contains('on');
  clearTimeout(npTimer);npTimer=setTimeout(pollNow,act?3000:12000);
 }).catch(function(){var act=$('npbar').classList.contains('on')||$('remote').classList.contains('on');clearTimeout(npTimer);npTimer=setTimeout(pollNow,act?4000:12000)})}
function openRemote(){$('remote').classList.add('on');mwOpen('remote',$('remote'),_closeRemote)}
function _closeRemote(){$('remote').classList.remove('on')}
function closeRemote(){mwBack('remote')}
function cmd(c){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){toast('Pon tu código');return}
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:cd,cmd:c})}).catch(function(){});
 if(c==='stop'){setTimeout(function(){closeRemote();pollNow()},700)}else{setTimeout(pollNow,500)}}
$('q').addEventListener('keydown',function(e){if(e.key==='Enter')go()});
(function(){try{var p=new URLSearchParams(location.search);var pl=p.get('play');var t=p.get('t')||'';var op=p.get('open');
 if(op==='serie'){openCard({kind:'serie',source:p.get('src')||'dt',path:p.get('path')||'',url:p.get('url')||'',title:t,poster:p.get('ps')||'',year:p.get('yr')||''});}
 else if(op==='peli'){openCard({kind:'movie',source:p.get('src')||'dt',content_id:p.get('ci')||'',tabla:p.get('tb')||'peliculas',url:p.get('url')||'',quality:p.get('q')||'',title:t,poster:p.get('ps')||'',year:p.get('yr')||''});}
 else if(pl==='dt'&&p.get('ci')){sharedPlay={a:'dt',c:p.get('ci'),tb:p.get('tb')||'peliculas',t:t};showShared(t);}
 else if(pl==='pl'&&p.get('u')){sharedPlay={a:'pl',u:p.get('u'),t:t};showShared(t);}
 else if(p.get('find')){goView('buscar');$('q').value=p.get('find');go();}
}catch(e){}})();
(function(){var _k='estrenos';
 try{var g=localStorage.getItem('mw_chip');if(g==='peliculas'||g==='series')_k=g}catch(e){}
 chip(_k)})();pollNow();
try{mlSync()}catch(e){}   // trae/respalda la lista de deseados si ya hay codigo
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){})}
</script></body></html>"""


@app.get("/cat")
def cat_page():
    return _serve_page(_CAT_PAGE)   # alias del catalogo (compat con links viejos)


# ===========================================================================
# AUTO-KEEPALIVE: el relay se pinguea a si mismo para no dormirse
# ===========================================================================
# Render (free) duerme el servicio tras 15 min SIN peticiones ENTRANTES. Si el
# propio servicio hace una peticion a su URL publica cada ~10 min, esa entra
# por el router de Render y reinicia el contador -> nunca se duerme. Asi el QR
# y las busquedas responden al instante aunque Kodi lleve horas apagado.
# Sin cuentas externas ni Supabase. 24/7 ~= 730h/mes < 750h gratis de Render.
import threading as _kth
import datetime as _kdt

def _warm_dt():
    """Mantiene la sesion Anubis de DonTorrent SIEMPRE lista, asi el usuario nunca
    paga el ~5-13s de re-resolver Anubis (tras deploy o expiracion). Reusa la
    cookie del disco COMPARTIDO -> si el otro worker ya la resolvio, no la repaga.
    Refresca proactivamente a las ~5h (antes del TTL de 6h), forzando para saltar
    tambien la copia del disco si esa tambien es vieja."""
    try:
        if _dt_is_down():   # DonTorrent caido: no machacar (el breaker reintenta)
            return
        dom = _dt_load_domain() or (DT_FALLBACK[0] if DT_FALLBACK
                                    else "dontorrent.review")
        ent = _DT_COOKIES.get(dom) or _dt_cookies_load().get(dom)
        old = bool(ent) and (_t.time() - ent.get("ts", 0)) > 3600 * 5
        _dt_anubis_session(dom, force=old)
    except Exception:
        pass


def _self_keepalive():
    # 1) Anubis de DonTorrent siempre caliente (en proceso, por worker).
    # 2) self-ping a /ping cada 4 min para que Render NO se duerma. (8 min era
    #    arriesgado: Render duerme a los 15 min; si UN ping falla, el siguiente
    #    caia a 16 min -> se dormia. 4 min deja margen para 2-3 fallos seguidos.)
    #    OJO: el self-ping mantiene despierto MIENTRAS vive; si el proceso muere
    #    (deploy/OOM/crash) NO puede resucitarse solo -> de eso se encarga el
    #    keepalive EXTERNO de GitHub Actions (.github/workflows/keepalive.yml).
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    while True:
        _warm_dt()
        try:
            # Descubrir el dominio de DivxTotal en 2o plano tras cada deploy
            # (/tmp borrado): asi el sondeo-loteria (tarpit de Cloudflare) lo
            # paga este hilo, nunca la busqueda de un usuario.
            if not _dx_load_domain():
                _dx_domain(force_probe=True)
        except Exception:
            pass
        if url:
            try:
                requests.get(url + "/ping", timeout=20,
                             headers={"User-Agent": "mw-keepalive"})
            except Exception:
                pass
        _t.sleep(240)


def _start_keepalive():
    try:
        _kth.Thread(target=_self_keepalive, daemon=True).start()
    except Exception:
        pass


_start_keepalive()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
