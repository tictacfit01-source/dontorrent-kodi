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
import requests
import cloudscraper
from urllib.parse import urlencode, quote as urlquote
from flask import Flask, request, Response, jsonify

app = Flask(__name__)

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


ALLOWED_HOSTS = (
    "mejortorrent",
    "wolfmax4k",
    "dontorrent",
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


@app.get("/")
def root():
    return Response(
        "MejorWolf Render relay OK. ScraperAPI=" +
        ("ON" if SCRAPERAPI_KEY else "OFF"),
        mimetype="text/plain",
    )


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
    try:
        if is_wolf and SCRAPERAPI_KEY:
            wrapped = _scraperapi_url(target, session_number=None)
            r = requests.request(request.method, wrapped, headers=fwd,
                                 data=body, timeout=60, allow_redirects=True)
        elif is_wolf:
            cs = _make_scraper()
            r = cs.request(request.method, target, headers=fwd, data=body,
                           timeout=25, allow_redirects=True)
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
            if SCRAPERAPI_KEY:
                # Catalogo = paginas de listado, NO necesitan premium.
                # 1 credito/seccion en vez de 25 -> sostenible en plan free.
                wrapped = _scraperapi_url(url, session_number=None,
                                          premium=False)
                r = requests.get(wrapped, headers=BROWSER_HEADERS, timeout=70)
            else:
                cs = _make_scraper()
                r = cs.get(url, headers=BROWSER_HEADERS, timeout=25)
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
    "dontorrent.science", "dontorrent.irish", "dontorrent.club",
    "dontorrent.info", "dontorrent.istanbul", "dontorrent.lighting",
    "dontorrent.reisen",
]

# Cache de cookies Anubis en memoria (proceso). Funciona porque Render mantiene
# la misma IP de salida — las cookies IP-bound siguen siendo validas.
_DT_COOKIES = {}   # domain -> {"cookies": {}, "ts": ...}
_DT_TTL = 3600 * 6  # 6h


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
    empiece con N ceros en hex."""
    prefix = "0" * difficulty
    t0 = _t.time()
    for nonce in range(50_000_000):
        h = _hl.sha256(f"{random_data}{nonce}".encode()).hexdigest()
        if h.startswith(prefix):
            return h, nonce, _t.time() - t0
    raise RuntimeError("PoW: nonce no encontrado")


def _dt_anubis_session(domain):
    """Devuelve session con cookies Anubis resueltas para `domain`.
    Resuelve PoW si no hay cache."""
    cached = _DT_COOKIES.get(domain)
    if cached and (_t.time() - cached["ts"]) < _DT_TTL:
        s = requests.Session()
        s.cookies.update(cached["cookies"])
        s.headers.update(BROWSER_HEADERS)
        return s, False  # False = no resolvio

    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    r = s.get(f"https://{domain}/", timeout=30)
    if "anubis_challenge" not in r.text:
        # No hay Anubis activo
        _DT_COOKIES[domain] = {"cookies": dict(s.cookies), "ts": _t.time()}
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
    s.get(pass_url, timeout=15, allow_redirects=False)

    if "browser-pow-auth" not in s.cookies:
        raise RuntimeError("Anubis: pass-challenge no devolvio cookie auth")

    _DT_COOKIES[domain] = {"cookies": dict(s.cookies), "ts": _t.time()}
    return s, True


def _dt_pick_domain(preferred=None):
    """Selecciona un dominio DT activo. preferred va primero."""
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(d for d in DT_FALLBACK if d != preferred)
    for d in candidates:
        try:
            r = requests.head(f"https://{d}/", timeout=8,
                              headers=BROWSER_HEADERS, allow_redirects=False)
            if r.status_code in (200, 302, 301):
                return d
        except Exception:
            continue
    return preferred or candidates[0]


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

    diag = {"phase": "init", "preferred": preferred}
    try:
        domain = _dt_pick_domain(preferred or None)
        diag["domain"] = domain
        diag["phase"] = "anubis"

        sess, solved = _dt_anubis_session(domain)
        diag["anubis_solved"] = solved

        diag["phase"] = "search"

        def _post_page(page):
            """POST /buscar para una pagina concreta (campo p=N).
            Reintenta una vez si aparece Anubis."""
            data = {"valor": q, "Buscar": "Buscar"}
            if page > 1:
                data["p"] = str(page)
            rr = sess.post(f"https://{domain}/buscar", data=data,
                           timeout=30, allow_redirects=True)
            if "anubis_challenge" in rr.text:
                _DT_COOKIES.pop(domain, None)
                ns, _ = _dt_anubis_session(domain)
                rr = ns.post(f"https://{domain}/buscar", data=data, timeout=30)
            return rr

        # Pagina 1
        r = _post_page(1)
        full_html = r.text

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
            "X-MW-Dt-Domain": domain,
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
            _DT_COOKIES.pop(domain, None)
            sess, _ = _dt_anubis_session(domain)
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

    if not domain:
        domain = _dt_pick_domain()

    try:
        sess, _ = _dt_anubis_session(domain)
        api = f"https://{domain}/api_validate_pow.php"

        # 1) generate
        r1 = sess.post(api, json={
            "action": "generate",
            "content_id": int(content_id),
            "tabla": tabla,
        }, timeout=20)
        gen = r1.json()
        if not gen.get("success"):
            return jsonify({"error": gen.get("error") or "no challenge",
                            "phase": "generate"}), 502
        challenge = gen["challenge"]

        # 2) solve PoW (download es difficulty=3, mas facil que browser=5)
        rand = challenge.get("randomData", "")
        diff = challenge.get("difficulty", 3)
        h, nonce, elapsed = _dt_solve_pow(rand, diff)

        # 3) validate
        r2 = sess.post(api, json={
            "action": "validate",
            "challenge": challenge,
            "nonce": nonce,
        }, timeout=20)
        val = r2.json()
        if not val.get("success") or not val.get("download_url"):
            return jsonify({"error": val.get("error") or "validate failed",
                            "phase": "validate"}), 502

        url = val["download_url"]
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = f"https://{domain}{url}"

        return jsonify({
            "success": True,
            "download_url": url,
            "domain": domain,
            "elapsed": elapsed,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
