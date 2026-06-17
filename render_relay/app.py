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
    "dontorrent.review", "dontorrent.support", "dontorrent.science",
    "dontorrent.irish", "dontorrent.club", "dontorrent.info",
    "dontorrent.istanbul", "dontorrent.lighting", "dontorrent.reisen",
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
                           timeout=30, allow_redirects=False)
            if rr.status_code in (301, 302, 303, 307, 308):
                newdom = _urlparse(rr.headers.get("Location") or "").hostname
                if newdom and newdom != domain:
                    ns, _ = _dt_anubis_session(newdom)
                    rr = ns.post(f"https://{newdom}/buscar", data=data,
                                 timeout=30, allow_redirects=False)
                else:
                    rr = sess.post(f"https://{domain}/buscar", data=data,
                                   timeout=30, allow_redirects=True)
            if "anubis_challenge" in rr.text:
                _DT_COOKIES.pop(domain, None)
                ns, _ = _dt_anubis_session(domain)
                rr = ns.post(f"https://{domain}/buscar", data=data, timeout=30,
                             allow_redirects=False)
            return rr

        domain = None
        sess = None
        solved = False
        r = None
        full_html = ""
        tried = []
        got_results = False
        for cand in dom_candidates[:5]:
            try:
                s2, sv = _dt_anubis_session(cand)
                rr = _post_page_on(cand, s2, 1)
                html = rr.text
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

    # Los content_id se comparten entre mirrors de DonTorrent, pero NO todos
    # exponen api_validate_pow.php (ej. support da 405). Probamos varios
    # dominios hasta que uno resuelva la descarga.
    dom_candidates = []
    if domain:
        dom_candidates.append(domain)
    for d in DT_FALLBACK:
        if d not in dom_candidates:
            dom_candidates.append(d)

    last_err = "unknown"
    tried = []
    for dom in dom_candidates[:6]:
        try:
            sess, _ = _dt_anubis_session(dom)
            api = f"https://{dom}/api_validate_pow.php"

            r1 = sess.post(api, json={
                "action": "generate",
                "content_id": int(content_id),
                "tabla": tabla,
            }, timeout=20)
            # 405/404 -> este mirror no tiene el endpoint, siguiente
            if r1.status_code in (404, 405):
                tried.append(f"{dom}:no-endpoint")
                last_err = f"{dom}: {r1.status_code}"
                continue
            try:
                gen = r1.json()
            except Exception:
                tried.append(f"{dom}:non-json[{r1.status_code}]")
                last_err = f"{dom}: non-json"
                continue
            challenge = gen.get("challenge")
            if not gen.get("success") or not challenge:
                tried.append(f"{dom}:no-challenge")
                last_err = f"{dom}: {gen.get('error', 'no challenge')}"
                continue

            # El challenge actual de DonTorrent es un STRING hex; el PoW es
            # sha256(challenge + nonce) con dificultad 3 (ver _dl_pow del
            # addon). El formato dict legacy (randomData/difficulty) tambien
            # se soporta por compatibilidad.
            if isinstance(challenge, dict):
                rand = challenge.get("randomData", "")
                diff = challenge.get("difficulty", 3)
            else:
                rand = str(challenge)
                diff = 3
            h, nonce, elapsed = _dt_solve_pow(rand, diff)

            r2 = sess.post(api, json={
                "action": "validate",
                "challenge": challenge,
                "nonce": nonce,
            }, timeout=20)
            try:
                val = r2.json()
            except Exception:
                tried.append(f"{dom}:validate-non-json")
                continue
            if not val.get("success") or not val.get("download_url"):
                tried.append(f"{dom}:validate-fail")
                last_err = f"{dom}: {val.get('error', 'validate failed')}"
                continue

            url = val["download_url"]
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = f"https://{dom}{url}"

            return jsonify({
                "success": True,
                "download_url": url,
                "domain": dom,
                "elapsed": elapsed,
                "tried": tried,
            })
        except Exception as e:
            tried.append(f"{dom}:ERR")
            last_err = f"{dom}: {e.__class__.__name__}"
            continue

    return jsonify({"error": last_err, "tried": tried,
                    "phase": "all-domains-failed"}), 502


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


def _dt_download_url(domain, content_id, tabla):
    """Resuelve la URL del .torrent (mismo PoW que /dtpow). Devuelve url o None."""
    dom_candidates = []
    if domain:
        dom_candidates.append(domain)
    for d in DT_FALLBACK:
        if d not in dom_candidates:
            dom_candidates.append(d)
    for dom in dom_candidates[:6]:
        try:
            sess, _ = _dt_anubis_session(dom)
            api = f"https://{dom}/api_validate_pow.php"
            r1 = sess.post(api, json={"action": "generate",
                                      "content_id": int(content_id),
                                      "tabla": tabla}, timeout=20)
            if r1.status_code in (404, 405):
                continue
            gen = r1.json()
            challenge = gen.get("challenge")
            if not gen.get("success") or not challenge:
                continue
            if isinstance(challenge, dict):
                rand = challenge.get("randomData", "")
                diff = challenge.get("difficulty", 3)
            else:
                rand = str(challenge)
                diff = 3
            _h, nonce, _e = _dt_solve_pow(rand, diff)
            r2 = sess.post(api, json={"action": "validate",
                                      "challenge": challenge,
                                      "nonce": nonce}, timeout=20)
            val = r2.json()
            if not val.get("success") or not val.get("download_url"):
                continue
            url = val["download_url"]
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                url = f"https://{dom}{url}"
            return url
        except Exception:
            continue
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
        return jsonify({"packed": ent.get("p"), "cached": True})
    url = _dt_download_url(request.args.get("domain", "").strip(), cid, tb)
    if not url:
        return jsonify({"packed": None})
    packed = None
    try:
        from urllib.parse import urlparse
        sess, _ = _dt_anubis_session(urlparse(url).hostname)
        r = sess.get(url, timeout=25, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 100:
            packed = _torrent_packed(r.content)
    except Exception:
        packed = None
    if packed is not None:
        d[key] = {"p": bool(packed), "ts": now}
        if len(d) > 3000:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[:len(d) - 3000]:
                d.pop(k, None)
        _dtpacked_save(d)
    return jsonify({"packed": packed})


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


def _dx_get(url):
    """HTML de una URL de DivxTotal: requests plano y, si hay challenge de
    Cloudflare, reintenta con cloudscraper. None si no se pudo."""
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=20,
                         allow_redirects=True)
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


def _dx_domain():
    now = _t.time()
    if _DX_DOM_CACHE["dom"] and now - _DX_DOM_CACHE["ts"] < _DX_DOM_TTL:
        return _DX_DOM_CACHE["dom"]
    for d in _DX_DOMAINS:
        t = _dx_get(f"https://{d}/")
        if t and "/peliculas/" in t.lower():
            _DX_DOM_CACHE["dom"] = d
            _DX_DOM_CACHE["ts"] = now
            return d
    return _DX_DOM_CACHE["dom"] or _DX_DOMAINS[0]


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
        # un reintento resolviendo dominio de cero (por si rotó)
        _DX_DOM_CACHE["ts"] = 0.0
        dom = _dx_domain()
        html1 = _dx_get(f"https://{dom}/?s={qq}")
    if not html1:
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


# ===========================================================================
# TECLADO REMOTO (escribir busquedas desde el movil)
# ===========================================================================
# El movil abre /kb (escaneando un QR que lleva el codigo del box), escribe la
# busqueda y la envia. El servicio del addon sondea /kb/poll con su codigo y
# abre los resultados en la tele. Almacen en fichero (compartido entre workers,
# efimero: las busquedas son de un solo uso).
_KB_FILE = "/tmp/mw_kb.json"
_KB_TTL = 600   # una busqueda pendiente caduca a los 10 min


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


_KB_ALLOWED_CMDS = {"home", "back", "playpause", "stop",
                    "volup", "voldown", "mute",
                    "seek_fwd", "seek_back", "seekto",
                    "up", "down", "left", "right", "ok",
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

_KB_PAGE = r"""<!doctype html><html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>MejorWolf</title>
<style>
:root{--bg0:#06070c;--bg1:#0e1320;--card:rgba(255,255,255,.06);--stroke:rgba(255,255,255,.10);
--txt:#f4f6fb;--sub:#8a93a6;--blue:#0a84ff;--blue2:#409cff;--green:#30d158;--red:#ff453a;--glass:rgba(255,255,255,.07)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:#06070c}
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
    return Response(_KB_PAGE, mimetype="text/html; charset=utf-8")


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
    if len(_RL) > 800:        # poda para no crecer sin limite
        _RL.clear()
    return True


@app.post("/kb/send")
def kb_send():
    ip = (request.headers.get("X-Forwarded-For", "")
          or request.remote_addr or "?").split(",")[0].strip()
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
    d = _kb_clean(_kb_load())
    entry = d.get(code) or {"ev": [], "ts": _t.time()}
    evs = entry.get("ev", [])
    evs.append(ev)
    entry["ev"] = evs[-20:]   # tope para no acumular
    entry["ts"] = _t.time()
    d[code] = entry
    _kb_save(d)
    return jsonify({"ok": True})


@app.get("/kb/poll")
def kb_poll():
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    if len(code) != 6:
        return jsonify({"events": []})
    d = _kb_clean(_kb_load())
    entry = d.pop(code, None)
    if entry:
        _kb_save(d)   # consumo: devolvemos los eventos pendientes y limpiamos
        return jsonify({"events": entry.get("ev", [])})
    return jsonify({"events": []})


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
_CAT_TMDB_CACHE = {}


def _cat_clean_title(title):
    t = (title or "").split(" - ")[0]   # "Show - 1ª Temporada [720p]" -> "Show"
    t = _re_dt.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = _re_dt.sub(r"\b\d{1,2}\s*[ªºoa]\b", " ", t)   # ordinales sueltos (1ª, 2º)
    t = _re_dt.sub(r"\b(temporada|parte|cap\w*|capitulo|\d{1,2}\s*x\s*\d{1,3})\b.*",
                   "", t, flags=_re_dt.I)
    t = _re_dt.sub(r"\b(1080p|720p|480p|2160p|4k|bluray|blu-?ray|brrip|bdrip|"
                   r"web-?dl|webrip|hdtv|microhd|dvdrip|hdrip|x264|x265|hevc|"
                   r"dual|castellano|latino|vose?)\b.*", "", t, flags=_re_dt.I)
    return t


def _cat_tmdb(title, kind="movie"):
    """Poster/año/nota de TMDB. kind='movie'|'tv'. Cache en memoria."""
    clean = _cat_clean_title(title)
    ym = _re_dt.search(r"\b(19|20)\d{2}\b", title)
    year = ym.group(0) if ym else None
    clean = _re_dt.sub(r"\b(19|20)\d{2}\b", "", clean)
    clean = _re_dt.sub(r"\s+", " ", clean).strip(" -.:")
    ckey = (kind, clean.lower(), year or "")
    if ckey in _CAT_TMDB_CACHE:
        return _CAT_TMDB_CACHE[ckey]
    out = {"poster": None, "year": year, "rating": None}
    try:
        ep = "tv" if kind == "tv" else "movie"
        params = {"api_key": _CAT_TMDB_KEY, "language": "es-ES",
                  "query": clean, "include_adult": "false"}
        if year and ep == "movie":
            params["year"] = year
        r = requests.get(f"https://api.themoviedb.org/3/search/{ep}",
                         params=params, timeout=8)
        res = (r.json() or {}).get("results") or []
        if res:
            top = res[0]
            pp = top.get("poster_path")
            d = top.get("release_date") or top.get("first_air_date") or ""
            out = {"poster": (f"https://image.tmdb.org/t/p/w342{pp}" if pp else None),
                   "year": d[:4] or year, "rating": top.get("vote_average")}
    except Exception:
        pass
    _CAT_TMDB_CACHE[ckey] = out
    return out


def _cat_dt_html(q):
    """POST de busqueda a DonTorrent (dominio APRENDIDO) -> HTML de TODAS las
    paginas concatenado (asi salen TODAS las temporadas, no solo la 1a pagina).
    Reusa Anubis + auto-curativo. Aislado de /dtsearch."""
    from urllib.parse import urlparse as _up
    data = {"valor": q, "Buscar": "Buscar"}
    for dom in [_dt_load_domain()] + DT_FALLBACK:
        if not dom:
            continue
        try:
            s, _ = _dt_anubis_session(dom)

            def _post(page, _s=s, _dom=dom):
                dd = dict(data)
                if page > 1:
                    dd["p"] = str(page)
                rr = _s.post(f"https://{_dom}/buscar", data=dd, timeout=30,
                             allow_redirects=False)
                if "anubis_challenge" in rr.text:
                    _DT_COOKIES.pop(_dom, None)
                    ns, _ = _dt_anubis_session(_dom)
                    rr = ns.post(f"https://{_dom}/buscar", data=dd, timeout=30,
                                 allow_redirects=False)
                return rr

            r1 = _post(1)
            if r1.status_code in (301, 302, 303, 307, 308):
                nd = _up(r1.headers.get("Location") or "").hostname
                if nd and nd != dom:
                    continue   # dominio viejo: deja que el bucle pruebe el actual
            if not _re_dt.search(r"/(?:pelicula|serie|documental)/\d+/", r1.text):
                continue
            full = r1.text
            pgs = [int(n) for n in _re_dt.findall(r"buscarPagina\((\d+)\)", full)]
            mx = min(max(pgs) if pgs else 1, 12)   # tope de seguridad
            if mx > 1:
                from concurrent.futures import ThreadPoolExecutor as _TPE

                def _pg(p):
                    try:
                        return _post(p).text
                    except Exception:
                        return ""
                with _TPE(max_workers=min(8, mx - 1)) as ex:
                    full += "".join(ex.map(_pg, range(2, mx + 1)))
            return full
        except Exception:
            continue
    return ""


def _cat_dt_session_get(path):
    """GET a una ruta de DonTorrent (dominio APRENDIDO) con sesion Anubis.
    Devuelve (html, domain) o ('', None). Para listados y fichas de serie."""
    for dom in [_dt_load_domain()] + DT_FALLBACK:
        if not dom:
            continue
        try:
            s, _ = _dt_anubis_session(dom)
            rr = s.get(f"https://{dom}{path}", timeout=30, allow_redirects=True)
            if "anubis_challenge" in rr.text:
                _DT_COOKIES.pop(dom, None)
                s, _ = _dt_anubis_session(dom)
                rr = s.get(f"https://{dom}{path}", timeout=30)
            if rr.status_code == 200 and _re_dt.search(
                    r"/(?:pelicula|serie|documental)/\d+/", rr.text):
                return rr.text, dom
        except Exception:
            continue
    return "", None


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
        itxt = _re_dt.sub(r"\s+", " ", _re_dt.sub(r"<[^>]+>", " ", inner)).strip()
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
        e = best.get(key)
        if e is None:
            order.append(key)
            best[key] = {"title": title, "score": score, "path": path,
                         "thumb": thumb}
        else:
            if score > e["score"]:
                e["title"], e["score"] = title, score
            if path and not e["path"]:
                e["path"] = path
            if thumb and not e["thumb"]:
                e["thumb"] = thumb
    out = []
    for k in order:
        kind, cid = k
        e = best[k]
        disp, qual = _cat_clean_quality(e["title"])
        it = {"title": disp, "content_id": cid, "kind": kind,
              "thumb": e["thumb"], "quality": qual, "source": "dt"}
        if kind == "serie":
            it["path"] = e["path"] or f"/serie/{cid}/"
        else:   # movie / doc -> descarga directa con su tabla
            it["tabla"] = _CAT_KIND_TABLA.get(kind, "peliculas")
        out.append(it)
    return out


def _cat_enrich(items, limit=36):
    items = items[:limit]
    from concurrent.futures import ThreadPoolExecutor as _TPE

    def _go(it):
        meta = _cat_tmdb(it["title"],
                         "tv" if it.get("kind") == "serie" else "movie")
        it["poster"] = meta.get("poster") or it.get("thumb")   # TMDB > DT propia
        it["year"] = meta.get("year") or it.get("year")
        it["rating"] = meta.get("rating")
        return it
    try:
        with _TPE(max_workers=8) as ex:
            return list(ex.map(_go, items))
    except Exception:
        return items


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


@app.get("/catsearch")
def catsearch():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": []})
    from concurrent.futures import ThreadPoolExecutor as _TPE
    with _TPE(max_workers=2) as ex:
        f_et = ex.submit(_et_search, q)
        f_dt = ex.submit(lambda: _cat_parse_items(_cat_dt_html(q)))
        try:
            dt_items = f_dt.result() or []
        except Exception:
            dt_items = []
        try:
            et_items = f_et.result() or []
        except Exception:
            et_items = []
    if not dt_items and not et_items:
        return jsonify({"items": []})
    return jsonify({"items": _cat_enrich(_cat_merge(dt_items, et_items))})


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
    """Mete un evento en la cola del box (la que consume /kb/poll)."""
    d = _kb_clean(_kb_load())
    entry = d.get(code) or {"ev": [], "ts": _t.time()}
    evs = entry.get("ev", [])
    evs.append(ev)
    entry["ev"] = evs[-20:]
    entry["ts"] = _t.time()
    d[code] = entry
    _kb_save(d)


def _catjob_wait(job, secs):
    end = _t.time() + secs
    while _t.time() < end:
        d = _catjob_load()
        if job in d:
            r = d.pop(job, None)
            _catjob_save(d)
            return r
        _t.sleep(0.4)
    return None


@app.get("/catetbox")
def catetbox():
    """Busqueda/estrenos en fuentes que necesitan el box (EliteTorrent, DivxTotal,
    WolfMax). op=search|latest, srcs=csv (et,dx,wf)."""
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    q = (request.args.get("q") or "").strip()
    op = (request.args.get("op") or "search").strip()
    srcs = (request.args.get("srcs") or "et").strip()
    if len(code) != 6 or (op == "search" and not q):
        return jsonify({"items": [], "off": True})
    job = "et" + os.urandom(5).hex()
    _kb_enqueue(code, {"c": "etjob", "job": job, "op": op, "q": q,
                       "srcs": srcs})
    res = _catjob_wait(job, 14.0)
    if res is None:
        return jsonify({"items": [], "timeout": True})
    items = res.get("items") or []
    if items:
        items = _cat_enrich(items, limit=60)
    return jsonify({"items": items})


@app.get("/catetboxresolve")
def catetboxresolve():
    code = re.sub(r"\D", "", request.args.get("code", ""))[:6]
    url = (request.args.get("url") or "").strip()
    src = (request.args.get("src") or "et").strip()
    if len(code) != 6 or not url.lower().startswith("http"):
        return jsonify({"link": ""}), 400
    job = "et" + os.urandom(5).hex()
    _kb_enqueue(code, {"c": "etjob", "job": job, "op": "resolve",
                       "src": src, "url": url})
    res = _catjob_wait(job, 18.0)
    return jsonify({"link": (res or {}).get("link", "") or ""})


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
    d = _catjob_load()
    now = _t.time()
    d = {k: v for k, v in d.items() if (now - v.get("ts", 0)) < _CATJOB_TTL}
    d[job] = {"items": body.get("items"), "link": body.get("link"), "ts": now}
    _catjob_save(d)
    return jsonify({"ok": True})


_CAT_BROWSE = {"estrenos": "/", "peliculas": "/peliculas", "series": "/series"}


@app.get("/catbrowse")
def catbrowse():
    kind = (request.args.get("kind") or "estrenos").strip().lower()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except Exception:
        page = 1
    bp = _CAT_BROWSE.get(kind, "/")
    path = bp if page <= 1 else (bp.rstrip("/") + f"/page/{page}")
    html, _d = _cat_dt_session_get(path)
    if not html:
        return jsonify({"items": []})
    return jsonify({"items": _cat_enrich(_cat_parse_items(html))})


@app.get("/catdetail")
def catdetail():
    """Episodios de una serie DonTorrent. path=/serie/ID/slug -> JSON."""
    path = (request.args.get("path") or "").strip()
    if not _re_dt.match(r"^/serie/\d+/", path):
        return jsonify({"error": "bad path", "episodes": []}), 400
    html, _d = _cat_dt_session_get(path)
    if not html:
        return jsonify({"episodes": []})
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
    meta = _cat_tmdb(title, "tv")
    return jsonify({"title": title, "poster": meta.get("poster"),
                    "year": meta.get("year"), "rating": meta.get("rating"),
                    "episodes": eps})


_CAT_PAGE = r"""<!doctype html><html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<title>MejorWolf</title>
<style>
:root{--bg:#06070c;--card:rgba(255,255,255,.06);--stroke:rgba(255,255,255,.10);--txt:#f4f6fb;--sub:#8a93a6;--blue:#0a84ff;--blue2:#409cff;--green:#30d158}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif}
body{min-height:100vh;background:radial-gradient(1100px 600px at 50% -10%,#1b2740 0,transparent 60%),var(--bg)}
.wrap{max-width:760px;margin:0 auto;padding:16px 14px 96px}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
.brand{font-weight:800;font-size:19px;display:flex;align-items:center;gap:8px;letter-spacing:.3px}
.brand .d{width:26px;height:26px;border-radius:9px;background:linear-gradient(145deg,var(--blue2),var(--blue));display:flex;align-items:center;justify-content:center;font-size:15px}
.code{width:96px;letter-spacing:3px;text-align:center;font-weight:600;background:rgba(255,255,255,.07);border:1px solid var(--stroke);color:var(--txt);border-radius:12px;padding:9px 8px;outline:0}
.tabs{display:flex;background:rgba(255,255,255,.05);border:1px solid var(--stroke);border-radius:14px;padding:4px;margin-bottom:14px}
.tab{flex:1;border:0;background:transparent;color:var(--sub);font-weight:600;font-size:14px;padding:9px;border-radius:10px;cursor:pointer}
.tab.on{color:#0b1020;background:#f4f6fb}
.pane.hidden{display:none}
.chips{display:flex;gap:8px;margin-bottom:14px}
.chip{border:1px solid var(--stroke);background:var(--card);color:var(--txt);font-weight:600;font-size:13px;padding:8px 14px;border-radius:999px;cursor:pointer}
.chip.on{background:linear-gradient(145deg,var(--blue2),var(--blue));border-color:transparent;color:#fff}
.search{display:flex;gap:8px;margin-bottom:16px}
.search input{flex:1;background:var(--card);border:1px solid var(--stroke);border-radius:14px;color:var(--txt);font-size:16px;padding:13px 14px;outline:0}
.search button{border:0;border-radius:14px;padding:0 16px;font-weight:700;color:#fff;background:linear-gradient(145deg,var(--blue2),var(--blue))}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:11px}
@media(max-width:430px){.grid{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--card);border:1px solid var(--stroke);border-radius:14px;overflow:hidden;transition:.15s}
.card:active{transform:scale(.97)}
.card .ph{position:relative;aspect-ratio:2/3;background:#0e1320 center/cover no-repeat;cursor:pointer}
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
.sheet .box{width:100%;max-width:760px;margin:0 auto;background:#0e1320;border-top:1px solid var(--stroke);border-radius:20px 20px 0 0;padding:18px 18px calc(20px + env(safe-area-inset-bottom));animation:up .2s ease}
@keyframes up{from{transform:translateY(30px)}to{transform:none}}
.sheet h3{margin:0 0 4px;font-size:17px}
.sheet .sy{color:var(--sub);font-size:13px;margin-bottom:14px}
.btn{display:block;width:100%;border:0;border-radius:14px;padding:15px;font-size:16px;font-weight:700;margin-top:10px;cursor:pointer}
.btn.play{color:#06140a;background:linear-gradient(145deg,#3dd46a,#27c257)}
.btn.fav{background:rgba(255,255,255,.08);color:var(--txt);border:1px solid var(--stroke)}
.btn.cancel{background:transparent;color:var(--sub)}
.ov{position:fixed;inset:0;background:var(--bg);z-index:35;overflow-y:auto;display:none;padding-bottom:96px}
.ov.on{display:block}
.remote{position:fixed;inset:0;background:radial-gradient(900px 500px at 50% -10%,#1b2740 0,transparent 60%),var(--bg);z-index:38;display:none;overflow-y:auto}
.remote.on{display:block}
.ovbar{display:flex;align-items:center;gap:10px;padding:14px;position:sticky;top:0;background:rgba(6,7,12,.85);backdrop-filter:blur(8px);border-bottom:1px solid var(--stroke)}
.ovback{border:0;background:transparent;color:var(--blue2);font-size:16px;font-weight:600;cursor:pointer}
.ovt{font-weight:700;font-size:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#ov-body{padding:14px}
.ovhead{display:flex;gap:14px;margin-bottom:16px}
.ovposter{width:96px;height:144px;border-radius:12px;background:#0e1320 center/cover;flex:none;border:1px solid var(--stroke)}
.ovh-t{font-size:18px;font-weight:700}
.ovh-y{color:var(--sub);font-size:13px;margin-top:4px}
.seas{font-size:13px;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:.4px;margin:16px 0 8px}
.ep{background:var(--card);border:1px solid var(--stroke);border-radius:12px;padding:14px;margin-bottom:8px;cursor:pointer;transition:.12s}
.ep:active{transform:scale(.98);background:rgba(255,255,255,.12)}
.epl{font-size:15px;font-weight:600}
.epq{font-size:11px;color:var(--sub);font-weight:600;margin-left:6px}
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
.np-pp{border:0;background:var(--blue);color:#fff;width:38px;height:38px;border-radius:50%;font-size:15px;cursor:pointer;flex:none}
.toast{position:fixed;left:50%;bottom:90px;transform:translateX(-50%) translateY(20px);background:#0e1320;border:1px solid var(--stroke);color:var(--txt);padding:12px 18px;border-radius:14px;font-size:14px;opacity:0;transition:.25s;z-index:45;box-shadow:0 10px 30px rgba(0,0,0,.5);max-width:90%}
.toast.on{opacity:1;transform:translateX(-50%)}
.spin{display:inline-block;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:r .7s linear infinite;vertical-align:-3px}
@keyframes r{to{transform:rotate(360deg)}}
/* botones mas grandes (movil) */
.tab{padding:11px;font-size:15px}
.chip{padding:10px 18px;font-size:14px}
.search input{padding:15px 16px}
.search button{padding:0 20px;font-size:15px}
.card .fav{width:36px;height:36px;font-size:19px;top:5px;right:5px}
.btn{padding:16px}
/* modo MANDO (igual disposicion que el mando /kb) */
.rmwrap{padding:8px 18px 50px;max-width:520px;margin:0 auto}
.rnp{margin:8px 0 18px}
.rnp .nplab{font-size:11px;color:var(--sub);font-weight:700;letter-spacing:.5px;margin-bottom:6px}
.rnp .npttl{font-size:18px;font-weight:700;line-height:1.3;margin-bottom:12px}
.rmbar{height:6px;border-radius:4px;background:rgba(255,255,255,.1);overflow:hidden}
.rmbar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue2),var(--blue));transition:width .9s linear}
.nprow{display:flex;justify-content:space-between;margin-top:8px;font-size:13px;color:var(--sub);font-variant-numeric:tabular-nums}
.media{display:flex;justify-content:center;align-items:center;gap:16px;margin:8px 0 18px}
.rb{width:64px;height:64px;border-radius:50%;border:1px solid var(--stroke);background:rgba(255,255,255,.07);color:var(--txt);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:17px;transition:.15s}
.rb:active{transform:scale(.92);background:rgba(255,255,255,.16)}
.rb.play{width:82px;height:82px;background:linear-gradient(145deg,#3dd46a,#27c257);border-color:transparent;box-shadow:0 8px 22px rgba(48,209,88,.4);color:#06140a;font-size:30px}
.rb.stop{color:#ff453a;font-size:22px}
.rb.sk{font-size:15px;font-weight:700;line-height:1}.rb.sk small{font-size:10px;opacity:.75}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:4px}
.pill{border:1px solid var(--stroke);background:rgba(255,255,255,.07);color:var(--txt);border-radius:14px;padding:16px;font-size:18px;text-align:center;cursor:pointer;font-weight:600}
.pill:active{transform:scale(.97);background:rgba(255,255,255,.14)}
.padwrap{display:flex;justify-content:center;margin:22px 0 8px}
.pad{position:relative;width:240px;height:240px;border-radius:50%;border:1px solid var(--stroke);background:radial-gradient(circle at 50% 32%,rgba(255,255,255,.10),transparent 55%),conic-gradient(from 0deg,rgba(255,255,255,.05),rgba(255,255,255,.02),rgba(255,255,255,.05));box-shadow:inset 0 -20px 40px rgba(0,0,0,.5),0 18px 40px rgba(0,0,0,.45)}
.arrow{position:absolute;color:var(--sub);font-size:22px;width:58px;height:58px;display:flex;align-items:center;justify-content:center;cursor:pointer;border-radius:50%}
.arrow:active{background:rgba(255,255,255,.12);color:#fff}
.arrow.up{top:8px;left:50%;transform:translateX(-50%)}.arrow.down{bottom:8px;left:50%;transform:translateX(-50%)}
.arrow.left{left:8px;top:50%;transform:translateY(-50%)}.arrow.right{right:8px;top:50%;transform:translateY(-50%)}
.ok{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:106px;height:106px;border-radius:50%;border:1px solid var(--stroke);background:radial-gradient(circle at 50% 35%,#2a3346,#161c2a);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;letter-spacing:1px;cursor:pointer;box-shadow:0 8px 20px rgba(0,0,0,.5)}
.ok:active{transform:translate(-50%,-50%) scale(.95)}
.navrow2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}
.rar{color:#ff9f6e;font-size:13px;font-weight:600;margin:0 0 12px}
.ovfav{border:1px solid var(--stroke);background:rgba(255,255,255,.07);color:var(--txt);border-radius:12px;padding:9px 13px;font-size:14px;font-weight:600;cursor:pointer;margin-top:8px}
/* etiquetas tipo / fuente en la tarjeta */
.card .kindtag{position:absolute;bottom:6px;left:6px;background:rgba(0,0,0,.6);border:1px solid var(--stroke);border-radius:6px;padding:2px 7px;font-size:10px;font-weight:700;color:#dfe6f2}
.card .srctag{position:absolute;bottom:6px;right:6px;background:rgba(255,159,110,.92);border-radius:6px;padding:2px 6px;font-size:9px;font-weight:800;color:#1a0d06;letter-spacing:.3px}
/* salto a minuto en el mando */
.jump{display:flex;gap:10px;margin:2px 0 6px}
.jump input{flex:1;background:rgba(255,255,255,.07);border:1px solid var(--stroke);border-radius:14px;color:var(--txt);padding:15px;font-size:16px;outline:0;text-align:center}
.jump input::placeholder{color:var(--sub);font-size:13px}
.jump .jbtn{width:120px;flex:none;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,var(--blue2),var(--blue));color:#fff;border-radius:14px;font-weight:700;font-size:15px;cursor:pointer}
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
</style></head><body>
<div class="wrap">
 <div class="top">
  <div class="brand"><span class="d">🐺</span> MejorWolf</div>
  <input id="code" class="code" inputmode="numeric" maxlength="6" placeholder="código">
 </div>
 <div class="tabs">
  <button id="tab-inicio" class="tab on" onclick="setView('inicio')">Inicio</button>
  <button id="tab-buscar" class="tab" onclick="setView('buscar')">Buscar</button>
  <button id="tab-lista" class="tab" onclick="setView('lista')">Mi lista</button>
 </div>
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
  <div id="buscar-grid" class="msg">Busca pelis y series y envíalas a tu tele 📺</div>
 </section>
 <section id="pane-lista" class="pane hidden">
  <div id="lista-grid" class="msg"></div>
 </section>
</div>
<div class="npbar" id="npbar" onclick="openRemote()">
 <div class="np-prog-wrap"><div class="np-prog" id="np-prog"></div></div>
 <div class="np-row"><div class="np-t" id="np-t"></div>
  <button class="np-pp" id="np-pp" onclick="event.stopPropagation();cmd('playpause')">⏸</button></div>
</div>
<div class="sheet" id="sheet" onclick="if(event.target===this)closeSheet()">
 <div class="box">
  <h3 id="sh-t"></h3><div class="sy" id="sh-y"></div>
  <div class="rar" id="sh-rar"></div>
  <button class="btn play" onclick="play()">▶ Reproducir en la tele</button>
  <button class="btn fav" id="sh-fav" onclick="sheetFav()">♡ Añadir a mi lista</button>
  <button class="btn cancel" onclick="closeSheet()">Cancelar</button>
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
   <div class="rb play" id="rm-pp" onclick="cmd('playpause')"><svg width="30" height="30" viewBox="0 0 24 24"><path d="M8 6 L18 12 L8 18 Z" fill="currentColor"/></svg></div>
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
</div>
<div class="toast" id="toast"></div>
<script>
var $=function(s){return document.getElementById(s)};
var SVG_PLAY='<svg width="30" height="30" viewBox="0 0 24 24"><path d="M8 6 L18 12 L8 18 Z" fill="currentColor"/></svg>';
var SVG_PAUSE='<svg width="28" height="28" viewBox="0 0 24 24"><rect x="6" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/><rect x="13.8" y="5" width="4.2" height="14" rx="1.4" fill="currentColor"/></svg>';
var EYE_OFF='<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
var EYE_ON='<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 4.5C6 4.5 2.2 11.2 2.05 11.5a1 1 0 0 0 0 .9C2.2 12.8 6 19.5 12 19.5s9.8-6.7 9.95-7a1 1 0 0 0 0-.9C21.8 11.2 18 4.5 12 4.5Zm0 11a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7Z"/></svg>';
function clk(d){return ('0'+d.getHours()).slice(-2)+':'+('0'+d.getMinutes()).slice(-2)}
var code=$('code'), favs=[], LISTS={inicio:[],buscar:[],lista:[]}, sel=null, npTimer=null, EPS={}, SHOW='', lastPlayTs=0;
var INI={kind:'estrenos',page:1,loading:false,more:true}, OVDATA=null;
try{var u=new URLSearchParams(location.search).get('c');if(u)localStorage.setItem('mw_code',u.replace(/\D/g,'').slice(0,6));}catch(e){}
code.value=localStorage.getItem('mw_code')||'';
code.oninput=function(){code.value=code.value.replace(/\D/g,'').slice(0,6);localStorage.setItem('mw_code',code.value)};
try{favs=JSON.parse(localStorage.getItem('mw_fav')||'[]')||[]}catch(e){favs=[]}
function saveFavs(){try{localStorage.setItem('mw_fav',JSON.stringify(favs))}catch(e){}}
var seen=[];try{seen=JSON.parse(localStorage.getItem('mw_seen')||'[]')||[]}catch(e){seen=[]}
function saveSeen(){try{localStorage.setItem('mw_seen',JSON.stringify(seen))}catch(e){}}
function isSeen(id){return seen.indexOf(String(id))>=0}
function toggleSeen(id){id=String(id);var i=seen.indexOf(id);if(i>=0)seen.splice(i,1);else seen.unshift(id);saveSeen()}
function kindLabel(k){return k==='serie'?'Serie':(k==='doc'?'Documental':'Película')}
function fk(x){return x.kind+':'+x.content_id}
function isFav(x){return favs.some(function(f){return fk(f)===fk(x)})}
function toggleFav(x){if(isFav(x)){favs=favs.filter(function(f){return fk(f)!==fk(x)})}else{favs.unshift({kind:x.kind,content_id:x.content_id,tabla:x.tabla,path:x.path,title:x.title,poster:x.poster,year:x.year,rating:x.rating,source:x.source,url:x.url,quality:x.quality})}saveFavs()}
function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function toast(t){var e=$('toast');e.textContent=t;e.classList.add('on');clearTimeout(e._t);e._t=setTimeout(function(){e.classList.remove('on')},2800)}
function star(x){return (x.year||'')+(x.rating?(' · ★'+(Math.round(x.rating*10)/10)):'')}
function setView(v){['inicio','buscar','lista'].forEach(function(k){$('pane-'+k).classList.toggle('hidden',k!==v);$('tab-'+k).classList.toggle('on',k===v)});if(v==='lista')renderFavs()}
function chip(kind){document.querySelectorAll('.chip').forEach(function(c){c.classList.toggle('on',c.dataset.k===kind)});
 INI={kind:kind,page:1,loading:false,more:true};
 var g=$('inicio-grid');g.className='msg';g.innerHTML='<span class="spin"></span> Cargando...';
 fetch('/catbrowse?kind='+kind+'&page=1').then(function(r){return r.json()}).then(function(d){
  LISTS.inicio=(d&&d.items)||[];if(!LISTS.inicio.length){g.className='msg';g.textContent='Nada por aquí ahora mismo.';INI.more=false;return}
  renderGrid(g,'inicio');
  if(kind==='estrenos')boxMerge('inicio',g,'latest','');
 }).catch(function(){g.className='msg';g.textContent='Error de conexión.'})}
function loadMoreInicio(){if(INI.loading||!INI.more)return;INI.loading=true;var next=INI.page+1;
 fetch('/catbrowse?kind='+INI.kind+'&page='+next).then(function(r){return r.json()}).then(function(d){
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
function go(){var q=$('q').value.trim();if(!q)return;var g=$('buscar-grid');g.className='msg';g.innerHTML='<span class="spin"></span> Buscando...';
 var cd=(code.value||'').replace(/\D/g,'');
 fetch('/catsearch?q='+encodeURIComponent(q)).then(function(r){return r.json()}).then(function(d){
  LISTS.buscar=(d&&d.items)||[];
  if(!LISTS.buscar.length){g.className='msg';g.textContent='Sin resultados para "'+q+'".';}else renderGrid(g,'buscar');
  if(cd.length===6)boxMerge('buscar',g,'search',q);
 }).catch(function(){g.className='msg';g.textContent='Error de conexión.'})}
function boxMerge(list,g,op,q){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6)return;
 var u='/catetbox?code='+cd+'&op='+op+'&srcs=et,dx'+(q?('&q='+encodeURIComponent(q)):'');
 fetch(u).then(function(r){return r.json()}).then(function(d){
  var ex=(d&&d.items)||[];if(!ex.length)return;
  var norm=function(s){return (s||'').toLowerCase().replace(/\s+/g,' ').trim()};
  var have={};LISTS[list].forEach(function(x){have[norm(x.title)]=1});
  var fresh=ex.filter(function(x){var k=norm(x.title);if(!k||have[k])return false;have[k]=1;return true});
  if(!fresh.length)return;
  var from=LISTS[list].length;LISTS[list]=LISTS[list].concat(fresh);
  if(g.querySelector('.grid'))appendGrid(g,list,from);else renderGrid(g,list);
 }).catch(function(){})}
function renderFavs(){var g=$('lista-grid');LISTS.lista=favs.slice();if(!favs.length){g.className='msg';g.textContent='Tu lista está vacía. Toca el ♡ en cualquier título.';return}renderGrid(g,'lista')}
function cardHTML(x,list,i){
 var bg=x.poster?(' style="background-image:url('+x.poster+')"'):'';
 var noimg=x.poster?'':('<div class="noimg">'+esc(x.title)+'</div>');
 var q='<div class="tl">'+(x.quality?('<span class="q">'+esc(x.quality)+'</span>'):'')+'</div>';
 var kt='<div class="kindtag">'+kindLabel(x.kind)+'</div>';
 var SL={et:'ET',dx:'DX',wf:'WF'};
 var src=(x.source&&x.source!=='dt')?('<div class="srctag">'+(SL[x.source]||x.source.toUpperCase())+'</div>'):'';
 return '<div class="card"><div class="ph"'+bg+' onclick="openItem(\''+list+'\','+i+')">'+noimg+q+kt+src+
    '<div class="fav" onclick="favTap(\''+list+'\','+i+',event)">'+(isFav(x)?'♥':'♡')+'</div></div>'+
    '<div class="m" onclick="openItem(\''+list+'\','+i+')"><div class="t">'+esc(x.title)+'</div><div class="y">'+star(x)+'</div></div></div>';}
function renderGrid(el,list){var items=LISTS[list];var h='<div class="grid">';for(var i=0;i<items.length;i++)h+=cardHTML(items[i],list,i);h+='</div>';el.className='';el.innerHTML=h;lazyRar(el,list,0)}
function appendGrid(el,list,from){var g=el.querySelector('.grid');if(!g){renderGrid(el,list);return}var items=LISTS[list],h='';for(var i=from;i<items.length;i++)h+=cardHTML(items[i],list,i);g.insertAdjacentHTML('beforeend',h);lazyRar(el,list,from)}
// ---- Badge RAR (📦) perezoso para items de DonTorrent (vía /dtpacked) ----
var _rarCache={},_rarQ=[],_rarActive=0;
function lazyRar(el,list,from){var items=LISTS[list];for(var i=from;i<items.length;i++){var x=items[i];if((x.source||'dt')!=='dt'||x.kind!=='movie')continue;_rarQ.push({el:el,list:list,i:i,c:x.content_id,tb:x.tabla||'peliculas'})}pumpRar()}
function pumpRar(){while(_rarActive<2&&_rarQ.length){var job=_rarQ.shift();var key='dt:'+job.tb+':'+job.c;
 if(_rarCache[key]!==undefined){if(_rarCache[key])rarBadge(job);continue}
 _rarActive++;(function(job,key){fetch('/dtpacked?c='+encodeURIComponent(job.c)+'&tb='+encodeURIComponent(job.tb)).then(function(r){return r.json()}).then(function(p){_rarActive--;_rarCache[key]=!!(p&&p.packed===true);if(_rarCache[key])rarBadge(job);pumpRar()}).catch(function(){_rarActive--;pumpRar()})})(job,key)}}
function rarBadge(job){var g=job.el.querySelector('.grid');if(!g)return;var cards=g.children;if(!cards||!cards[job.i])return;var tl=cards[job.i].querySelector('.tl');if(!tl||tl.querySelector('.rartag'))return;var b=document.createElement('span');b.className='rartag';b.textContent='📦 RAR';tl.appendChild(b)}
function favTap(list,i,ev){ev.stopPropagation();var x=LISTS[list][i];toggleFav(x);ev.target.textContent=isFav(x)?'♥':'♡';if(list==='lista')renderFavs()}
function openItem(list,i){var x=LISTS[list][i];sel=x;if(x.kind==='serie'){openSeries(x);return}
 var SL={et:'EliteTorrent',dx:'DivxTotal',wf:'WolfMax4K'};
 var sy=star(x);if(x.quality)sy+=(sy?' · ':'')+x.quality;if(x.source&&SL[x.source])sy+=' · '+SL[x.source];
 $('sh-t').textContent=x.title;$('sh-y').textContent=sy;$('sh-fav').textContent=isFav(x)?'♥ En mi lista':'♡ Añadir a mi lista';$('sh-rar').textContent='';$('sheet').classList.add('on');
 if((x.source||'dt')==='dt')fetch('/dtpacked?c='+encodeURIComponent(x.content_id)+'&tb='+encodeURIComponent(x.tabla||'peliculas')).then(function(r){return r.json()}).then(function(p){if(sel===x&&p&&p.packed===true)$('sh-rar').textContent='📦 Viene comprimido (RAR) — puede que no se reproduzca.'}).catch(function(){})}
function sheetFav(){toggleFav(sel);$('sh-fav').textContent=isFav(sel)?'♥ En mi lista':'♡ Añadir a mi lista'}
function ovFav(){toggleFav(sel);var b=$('ov-fav');if(b)b.textContent=isFav(sel)?'♥ En mi lista':'♡ Añadir a mi lista'}
function closeSheet(){$('sheet').classList.remove('on')}
function play(){if(!sel)return;
 if(sel.source&&sel.source!=='dt'){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){toast('Pon tu código de 6 cifras arriba');return}
  toast('Resolviendo en tu box…');
  fetch('/catetboxresolve?code='+cd+'&src='+encodeURIComponent(sel.source)+'&url='+encodeURIComponent(sel.url||sel.content_id)).then(function(r){return r.json()}).then(function(d){
   if(d&&d.link){if(sendPlay({a:'pl',u:d.link,t:sel.title}))closeSheet()}else{toast('No se pudo (¿box encendido?)')}}).catch(function(){toast('No se pudo obtener el enlace')});
  return}
 if(sendPlay({a:'dt',c:sel.content_id,tb:sel.tabla,t:sel.title}))closeSheet()}
function sendPlay(ref){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){toast('Pon tu código de 6 cifras arriba');return false}
 var body={code:cd,cmd:'play_ref',a:ref.a||'dt',t:ref.t};
 if((ref.a||'dt')==='pl'){body.u=ref.u}else{body.c=ref.c;body.tb=ref.tb}
 toast('Enviando a la tele...');
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  .then(function(r){return r.json()}).then(function(d){if(d&&d.ok){lastPlayTs=Date.now();toast('▶ En la tele');closeSheet();closeOv();openRemote();setTimeout(pollNow,1500)}else{toast('Error: '+((d&&d.error)||'?'))}}).catch(function(){toast('No se pudo enviar')});
 return true}
function openSeries(x){SHOW=x.title;EPS={};OVDATA=null;$('ov').classList.add('on');$('ov-title').textContent=x.title;
 $('ov-body').innerHTML='<div class="msg"><span class="spin"></span> Cargando episodios...</div>';
 fetch('/catdetail?path='+encodeURIComponent(x.path||'')).then(function(r){return r.json()}).then(function(d){
  var eps=(d&&d.episodes)||[];if(!eps.length){$('ov-body').innerHTML='<div class="msg">No se pudieron leer los episodios.</div>';return}
  OVDATA={d:d,x:x};renderEpisodes();
 }).catch(function(){$('ov-body').innerHTML='<div class="msg">Error de conexión.</div>'})}
function renderEpisodes(){if(!OVDATA)return;var d=OVDATA.d,x=OVDATA.x;EPS={};
 var eps=(d&&d.episodes)||[];var poster=(d&&d.poster)||x.poster;
 var seasons={};eps.forEach(function(e){var s=e.season||0;(seasons[s]=seasons[s]||[]).push(e)});
 var keys=Object.keys(seasons).map(Number).sort(function(a,b){return a-b});
 var ph=poster?(' style="background-image:url('+poster+')"'):'';
 var h='<div class="ovhead"><div class="ovposter"'+ph+'></div><div><div class="ovh-t">'+esc(d.title||x.title)+'</div><div class="ovh-y">'+esc(star({year:d.year||x.year,rating:d.rating}))+'</div><button class="ovfav" id="ov-fav" onclick="ovFav()">'+(isFav(x)?'♥ En mi lista':'♡ Añadir a mi lista')+'</button></div></div>';
 keys.forEach(function(s){var list=seasons[s];var allseen=list.every(function(e){return isSeen(e.content_id)});
  if(keys.length>1||s>0)h+='<div class="seas"><span>Temporada '+(s||'?')+'</span><span class="seasmark" onclick="markSeason('+s+')">'+(allseen?'Marcar no vista':'Marcar toda vista')+'</span></div>';
  list.forEach(function(e){var id='e'+e.content_id;EPS[id]=e;var sn=isSeen(e.content_id);
   h+='<div class="ep'+(sn?' seen':'')+'" id="row-'+id+'"><div class="epmain" onclick="playEp(\''+id+'\')"><span class="epl"><span class="chk">✓</span>'+esc(e.label)+(e.quality?(' <span class="epq">'+esc(e.quality)+'</span>'):'')+'</span></div>'+
     '<div class="eye" onclick="event.stopPropagation();markSeen(\''+id+'\')" title="Marcar como visto">'+(sn?EYE_ON:EYE_OFF)+'</div></div>'});
 });$('ov-body').innerHTML=h;}
function closeOv(){$('ov').classList.remove('on')}
function markSeen(id){var e=EPS[id];if(!e)return;toggleSeen(e.content_id);var row=$('row-'+id);
 if(row){var sn=isSeen(e.content_id);row.classList.toggle('seen',sn);var ey=row.querySelector('.eye');if(ey)ey.innerHTML=sn?EYE_ON:EYE_OFF;}}
function markSeason(s){if(!OVDATA)return;var eps=(OVDATA.d.episodes||[]).filter(function(e){return (e.season||0)===s});
 var allseen=eps.every(function(e){return isSeen(e.content_id)});
 eps.forEach(function(e){var cur=isSeen(e.content_id);if(allseen&&cur)toggleSeen(e.content_id);else if(!allseen&&!cur)toggleSeen(e.content_id)});renderEpisodes();}
function playEp(id){var e=EPS[id];if(!e)return;if(sendPlay({a:'dt',c:e.content_id,tb:e.tabla,t:(SHOW+' '+e.label).trim()}))closeOv()}
function seekTo(){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){toast('Pon tu código');return}
 var v=($('rm-min').value||'').trim();if(v===''){toast('Pon un minuto');return}var mn=parseInt(v,10);if(isNaN(mn)||mn<0){toast('Minuto no válido');return}
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:cd,cmd:'seekto',min:mn})}).then(function(r){return r.json()}).then(function(d){if(d&&d.ok){toast('Saltando al minuto '+mn);$('rm-min').value='';setTimeout(pollNow,700)}else{toast('Error: '+((d&&d.error)||'?'))}}).catch(function(){toast('No se pudo')})}
function fmt(s){s=Math.max(0,s||0);var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=Math.floor(s%60);return (h?h+':':'')+(h?('0'+m).slice(-2):m)+':'+('0'+x).slice(-2)}
function pollNow(){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){clearTimeout(npTimer);npTimer=setTimeout(pollNow,4000);return}
 fetch('/kb/now?code='+cd).then(function(r){return r.json()}).then(function(d){var np=d&&d.np;var bar=$('npbar');
  if(np&&np.title){bar.classList.add('on');var pct=np.total?Math.min(100,Math.round(np.elapsed/np.total*100)):0;
   var fin='';if(!np.paused&&np.total>0)fin=clk(new Date(Date.now()+(np.total-np.elapsed)*1000));
   $('np-t').textContent=np.title+(fin?(' · Finaliza '+fin):'');
   $('np-prog').style.width=pct+'%';$('np-pp').textContent=np.paused?'▶':'⏸';
   $('rm-t').textContent=np.title;$('rm-time').textContent=fmt(np.elapsed)+(np.total?(' / '+fmt(np.total)):'');
   $('rm-fin').textContent=np.paused?'En pausa':(np.total>0?('Finaliza a las '+fin):'');
   $('rm-prog').style.width=pct+'%';$('rm-pp').innerHTML=np.paused?SVG_PLAY:SVG_PAUSE;}
  else{bar.classList.remove('on');if($('remote').classList.contains('on')){$('rm-t').textContent='Preparando en la tele…';$('rm-time').textContent='';$('rm-fin').textContent='';}}
  clearTimeout(npTimer);npTimer=setTimeout(pollNow,3000);
 }).catch(function(){clearTimeout(npTimer);npTimer=setTimeout(pollNow,4000)})}
function openRemote(){$('remote').classList.add('on')}
function closeRemote(){$('remote').classList.remove('on')}
function cmd(c){var cd=(code.value||'').replace(/\D/g,'');if(cd.length!==6){toast('Pon tu código');return}
 fetch('/kb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:cd,cmd:c})}).catch(function(){});
 if(c==='stop'){setTimeout(function(){closeRemote();pollNow()},700)}else{setTimeout(pollNow,500)}}
$('q').addEventListener('keydown',function(e){if(e.key==='Enter')go()});
chip('estrenos');pollNow();
</script></body></html>"""


@app.get("/cat")
def cat_page():
    return Response(_CAT_PAGE, mimetype="text/html; charset=utf-8")


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

# Franja horaria (UTC) en la que se mantiene caliente. Render corre en UTC.
# Dormimos UTC 01:00-07:00 == Madrid ~03:00-09:00 (verano) / 02:00-08:00
# (invierno): nadie ve nada de madrugada. Asi ~18h/dia despierto (~558h/mes,
# muy por debajo de las 750h gratis) e instantaneo en horario normal.
_KEEPALIVE_SLEEP_FROM_UTC = 1   # incl.
_KEEPALIVE_SLEEP_TO_UTC = 7     # excl.


def _self_keepalive():
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not url:
        return   # solo en Render; en local no hace nada
    while True:
        try:
            _t.sleep(600)   # 10 min (< 15 min de Render)
            h = _kdt.datetime.utcnow().hour
            if _KEEPALIVE_SLEEP_FROM_UTC <= h < _KEEPALIVE_SLEEP_TO_UTC:
                continue   # de madrugada NO pinguear: se deja dormir (ahorro)
            requests.get(url + "/", timeout=20,
                         headers={"User-Agent": "mw-keepalive"})
        except Exception:
            pass


def _start_keepalive():
    try:
        _kth.Thread(target=_self_keepalive, daemon=True).start()
    except Exception:
        pass


_start_keepalive()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
