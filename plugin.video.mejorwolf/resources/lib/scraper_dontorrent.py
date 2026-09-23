"""
Scraper para DonTorrent (dontorrent.science y dominios alternativos).

Catalogo enorme: 30K+ peliculas, 14K+ series, 4K+ documentales.
Todo en castellano. Protegido por Anubis PoW anti-bot.

Estructura del sitio:
  Estrenos:        /                (homepage)
  Peliculas:       /peliculas       (+ /peliculas/hd, /peliculas/4K)
  Series:          /series          (+ /series/hd, /series/4k)
  Documentales:    /documentales
  Busqueda:        POST /buscar     {valor: query, Buscar: "Buscar"}
  Detalle pelicula:  /pelicula/ID/SLUG/
  Detalle serie:     /serie/ID/SEASON/SLUG/
  Detalle documental:/documental/ID/SEASON/SLUG/

Descargas: boton protegido con PoW (api_validate_pow.php).
"""

import re
import hashlib
import json
import time
import socket
import contextlib
from urllib.parse import urljoin, urlparse, quote as urlquote
from bs4 import BeautifulSoup
import requests
import os
import xbmc
import xbmcaddon
import xbmcvfs

from . import anubis
from . import dns_doh

SOURCE = "dt"
_ADDON = xbmcaddon.Addon()
_LOG = lambda msg: xbmc.log(f"[MejorWolf/DT] {msg}", xbmc.LOGINFO)

# ── Cache en disco de torrents resueltos ────────────────────────────────────
# El download_url de DonTorrent es un fichero .torrent ESTATICO (sin token que
# caduque), asi que cachear (content_id, tabla) -> url permite reproducir de
# nuevo SIN repetir todo el PoW. Acelera reintentos y re-visionados.
try:
    _DT_PROFILE = xbmcvfs.translatePath(_ADDON.getAddonInfo("profile"))
except Exception:
    _DT_PROFILE = ""
_RESOLVE_CACHE_FILE = (os.path.join(_DT_PROFILE, "dt_resolve_cache.json")
                       if _DT_PROFILE else "")
_RESOLVE_TTL = 7 * 24 * 3600   # 7 dias


def _resolve_cache_get(content_id, tabla):
    if not _RESOLVE_CACHE_FILE or not os.path.exists(_RESOLVE_CACHE_FILE):
        return None
    try:
        with open(_RESOLVE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ent = data.get(f"{tabla}:{content_id}")
        if ent and (time.time() - ent.get("t", 0)) < _RESOLVE_TTL:
            return ent.get("u")
    except Exception:
        pass
    return None


def _resolve_cache_put(content_id, tabla, url):
    if not _RESOLVE_CACHE_FILE or not url:
        return
    try:
        data = {}
        if os.path.exists(_RESOLVE_CACHE_FILE):
            with open(_RESOLVE_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        # Poda de entradas viejas para que el fichero no crezca sin limite.
        now = time.time()
        data = {k: v for k, v in data.items()
                if (now - v.get("t", 0)) < _RESOLVE_TTL}
        data[f"{tabla}:{content_id}"] = {"u": url, "t": now}
        os.makedirs(_DT_PROFILE, exist_ok=True)
        tmp = _RESOLVE_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _RESOLVE_CACHE_FILE)
    except Exception:
        pass

# ── Dominios conocidos ──────────────────────────────────────────────────
# Probados 2026-05: science, irish, reisen, club, info, istanbul, lighting
# son los que respondian. rocks/onl/live/kiwi/phd/pink suelen estar caidos.
# Lista actualizada periodicamente desde Supabase (mw_config.dontorrent).
# dontorrent.management es el dominio CANONICO vigente (2026-07). Va primero:
# los mirrors viejos (.science, .review...) hacen 301 -> .management y el
# redirect convierte el POST del PoW en GET -> api_validate_pow.php devuelve 405
# ("Metodo no permitido"). Usando el canonico directo no hay redirect.
# OJO: DonTorrent ROTA de dominio; cuando este caduque tambien redirigira. Para
# que se auto-cure, _probe_domain ADOPTA el destino del 301 (abajo) y hay que
# anadir el viejo a _STALE en resolve_domain(). Historial: .science -> .review
# -> .management -> .supply (12-09-2026) -> .moi (17-09-2026, segun su
# Telegram; las cajas ya iban por .moi el 23-09).
_CANONICAL_DOMAIN = "dontorrent.moi"
FALLBACK_DOMAINS = [
    "dontorrent.moi",
    "dontorrent.supply",
    "dontorrent.management",
    "dontorrent.review",
    "dontorrent.science",
    "dontorrent.irish",
    "dontorrent.club",
    "dontorrent.info",
    "dontorrent.istanbul",
    "dontorrent.lighting",
    "dontorrent.reisen",
    "dontorrent.onl",
    "dontorrent.live",
    "dontorrent.kiwi",
    "dontorrent.pink",
]

DOMAIN_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(don[a-z0-9\-]*torrent[a-z0-9\-]*\.[a-z]{2,8})',
    re.IGNORECASE,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# ── Cache de dominio y cookies ──────────────────────────────────────────
_cached_domain = None
_cached_domain_ts = 0
_DOMAIN_TTL = 3600 * 6  # 6 horas


# ── DoH DNS bypass (contra bloqueo DNS del ISP) ──────────────────────
# ISPs españoles bloquean DonTorrent via DNS.  Resolvemos por DoH
# (DNS-over-HTTPS) y conectamos directamente.  Al no usar el Worker,
# todas las peticiones salen con la IP real del usuario → las cookies
# Anubis (que son IP-bound) funcionan correctamente.
#
# Usamos dns_doh.resolve() que conecta a los resolvers POR IP (1.1.1.1,
# 8.8.8.8, etc.) con TLS SNI pinning — inmune a bloqueo DNS del ISP
# incluso si el ISP también bloquea los dominios de los resolvers DoH.
# ──────────────────────────────────────────────────────────────────────


def _resolve_doh(domain):
    """Resuelve dominio via DoH (dns_doh con IP pinning robusto)."""
    ips = dns_doh.resolve(domain)
    if ips:
        _LOG(f"DoH: {domain} -> {ips[0]}")
        return ips[0]
    _LOG(f"DoH: fallo resolver {domain}")
    return None


@contextlib.contextmanager
def _dns_override(domain, ip):
    """Parchea socket.getaddrinfo para redirigir *domain* a *ip*.

    Con esto Python conecta al IP correcto pero el TLS handshake envía
    el SNI original → Cloudflare/Anubis aceptan la conexión.
    """
    _original = socket.getaddrinfo

    def _patched(host, port, *args, **kwargs):
        if host == domain:
            return _original(ip, port, *args, **kwargs)
        return _original(host, port, *args, **kwargs)

    socket.getaddrinfo = _patched
    try:
        yield
    finally:
        socket.getaddrinfo = _original


def _direct_anubis_solve(session, html, domain, init_cookies):
    """Resuelve Anubis PoW y envía pass-challenge directamente (sin proxy).

    Devuelve dict de cookies resueltas, o {} si falla.
    """
    ch_data = anubis._parse_challenge(html)
    if not ch_data:
        _LOG("Anubis: no se encontró challenge JSON")
        return {}

    rules = ch_data.get("rules", {})
    ch = ch_data.get("challenge", {})
    random_data = ch.get("randomData", "")
    difficulty = rules.get("difficulty", ch.get("difficulty", 5))
    challenge_id = ch.get("id", "")
    if not random_data or not challenge_id:
        _LOG("Anubis: datos de challenge incompletos")
        return {}

    _LOG(f"Anubis: resolviendo PoW difficulty={difficulty}")
    hex_hash, nonce, elapsed = anubis._solve_pow(random_data, difficulty)
    elapsed_ms = int(elapsed * 1000)

    pass_url = (
        f"https://{domain}/.within.website/x/cmd/anubis/api/pass-challenge"
        f"?response={hex_hash}&nonce={nonce}"
        f"&id={urlquote(challenge_id, safe='')}"
        f"&elapsedTime={elapsed_ms}&redir=/"
    )
    try:
        r = session.get(pass_url, timeout=15, allow_redirects=False,
                        cookies=init_cookies)
    except Exception as e:
        _LOG(f"Anubis: pass-challenge falló: {e}")
        return {}

    cookies = dict(init_cookies)
    cookies.update(dict(r.cookies))

    if "browser-pow-auth" in cookies:
        anubis._cookie_cache[domain] = {"cookies": cookies, "ts": time.time()}
        anubis._persist_cookies(domain, cookies)
        _LOG(f"Anubis resuelto OK para {domain} (nonce={nonce}, "
             f"t={elapsed:.2f}s)")
    else:
        _LOG(f"Anubis: pass-challenge no devolvió cookie auth "
             f"(status={r.status_code})")
    return cookies


def _doh_fetch(method, url, data=None, json_data=None, max_anubis=2,
               **kwargs):
    """Petición HTTP con bypass DoH + resolución automática de Anubis.

    Estrategia principal para DonTorrent: evita bloqueo DNS del ISP
    mientras mantiene la misma IP del usuario para que los JWT Anubis
    (IP-bound) funcionen correctamente.
    """
    parsed = urlparse(url)
    domain = parsed.hostname
    ip = _resolve_doh(domain)
    if not ip:
        raise RuntimeError(f"DoH: no se pudo resolver {domain}")

    timeout = kwargs.pop("timeout", 25)

    with _dns_override(domain, ip):
        s = requests.Session()
        s.headers.update(HEADERS)

        # Cargar cookies Anubis cacheadas
        cached = _anubis_cookies_for(url)
        if cached:
            s.cookies.update(cached)

        for attempt in range(max_anubis + 1):
            if method.upper() == "GET":
                r = s.get(url, timeout=timeout)
            else:
                if json_data is not None:
                    r = s.post(url, json=json_data, timeout=timeout)
                else:
                    r = s.post(url, data=data, timeout=timeout,
                               allow_redirects=True)

            if not anubis.is_anubis(r.text):
                _mira_mantenimiento(r)
                r.raise_for_status()
                return r

            if attempt >= max_anubis:
                break

            _LOG(f"Anubis (intento {attempt + 1}), resolviendo...")
            init_cookies = _extract_response_cookies(r)
            solved = _direct_anubis_solve(s, r.text, domain, init_cookies)
            if not solved:
                break
            s.cookies.update(solved)

        # Agoté reintentos — devolver lo que sea
        r.raise_for_status()
        return r


def _mira_mantenimiento(r):
    """Si DonTorrent contesta con su pagina de mantenimiento, se apunta: asi
    resolve_domain deja de sondear un rato y la tele dice lo que pasa (ver
    _DTCaido y en_mantenimiento)."""
    try:
        if _es_mantenimiento(r):
            _apunta_fallo(False, mant=True)
    except Exception:
        pass


def _proxy_base():
    raw = (_ADDON.getSetting("proxy_url") or "").strip().rstrip("/")
    return raw or "https://mw-relay.israeldm93.workers.dev"


def _anubis_cookies_for(url):
    """Busca cookies Anubis cacheadas para el dominio de url."""
    try:
        host = urlparse(url).hostname
        if not host:
            return {}
        cached = anubis.get_cached_cookies(host)
        if cached:
            return cached
        if "dontorrent" in host:
            for dk in list(anubis._cookie_cache.keys()):
                if "dontorrent" in dk:
                    c = anubis.get_cached_cookies(dk)
                    if c:
                        return c
        return {}
    except Exception:
        return {}


def _extract_response_cookies(response):
    """Extrae cookies de la respuesta HTTP."""
    cookies = {}
    try:
        raw_cookies = response.raw.headers.getlist("Set-Cookie")
        for sc in raw_cookies:
            parts = sc.split(";")
            if "=" in parts[0]:
                cn, cv = parts[0].split("=", 1)
                cn, cv = cn.strip(), cv.strip()
                if cn and cv:
                    cookies[cn] = cv
    except Exception:
        for key, value in response.headers.items():
            if key.lower() == "set-cookie":
                parts = value.split(";")
                if "=" in parts[0]:
                    cn, cv = parts[0].split("=", 1)
                    cn, cv = cn.strip(), cv.strip()
                    if cn and cv:
                        cookies[cn] = cv
    for name, value in response.cookies.items():
        cookies[name] = value
    return cookies


def _handle_anubis(response, original_url):
    """Si la respuesta es un challenge Anubis, resolverlo y reintentar."""
    if not anubis.is_anubis(response.text):
        return response

    base = _proxy_base()
    _LOG(f"Anubis challenge para {original_url}")

    init_cookies = _extract_response_cookies(response)
    final_url = response.headers.get("x-mw-relay-final", original_url)

    cookies = anubis.solve_and_get_cookie(
        response.text, final_url, base, init_cookies=init_cookies
    )
    if not cookies:
        _LOG("Anubis solve FALLO")
        return response

    proxied = f"{base}/?u={urlquote(original_url, safe='')}"
    h = dict(HEADERS)
    h["Accept-Encoding"] = "identity"
    h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    r = requests.get(proxied, timeout=30, headers=h)
    r.raise_for_status()

    if anubis.is_anubis(r.text):
        _LOG("Anubis sigue activo tras resolver")
        return r

    _LOG("Anubis resuelto OK")
    return r


def _ensure_anubis(url):
    """Pre-flight: obtener cookies Anubis si no las tenemos."""
    cached = _anubis_cookies_for(url)
    if cached:
        return cached

    base = _proxy_base()
    parsed = urlparse(url)
    root_url = f"{parsed.scheme}://{parsed.hostname}/"
    proxied = f"{base}/?u={urlquote(root_url, safe='')}"
    h = dict(HEADERS)
    h["Accept-Encoding"] = "identity"

    try:
        _LOG(f"Pre-flight Anubis para {root_url}")
        r = requests.get(proxied, timeout=30, headers=h)
        if anubis.is_anubis(r.text):
            _handle_anubis(r, root_url)
    except Exception as e:
        _LOG(f"Pre-flight fallo: {e}")

    return _anubis_cookies_for(url)


def _proxy_get(url, **kwargs):
    """GET con proxy + Anubis auto-solver."""
    base = _proxy_base()
    proxied = f"{base}/?u={urlquote(url, safe='')}"
    kwargs.setdefault("timeout", 30)
    h = dict(HEADERS)
    h["Accept-Encoding"] = "identity"
    cached = _anubis_cookies_for(url)
    if cached:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cached.items())
    kwargs["headers"] = h

    r = requests.get(proxied, **kwargs)
    if anubis.is_anubis(r.text):
        r = _handle_anubis(r, url)

    final = r.headers.get("x-mw-relay-final")
    if final:
        try:
            r.url = final
        except Exception:
            pass

    _mira_mantenimiento(r)
    r.raise_for_status()
    return r


def _proxy_post(url, data=None, json_data=None, **kwargs):
    """POST con proxy + Anubis."""
    base = _proxy_base()
    proxied = f"{base}/?u={urlquote(url, safe='')}"
    kwargs.setdefault("timeout", 30)
    h = dict(HEADERS)
    h["Accept-Encoding"] = "identity"

    cached = _anubis_cookies_for(url)
    if not cached:
        cached = _ensure_anubis(url)
    if cached:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cached.items())
    kwargs["headers"] = h

    if json_data is not None:
        h["Content-Type"] = "application/json"
        h["Accept"] = "application/json,*/*;q=0.8"
        r = requests.post(proxied, data=json.dumps(json_data), **kwargs)
    else:
        r = requests.post(proxied, data=data, **kwargs)

    if anubis.is_anubis(r.text):
        # OJO: hay que TIRAR la cookie cacheada antes de re-pedirla. El JWT de
        # Anubis va atado a la IP y el CF Worker rota de IP de salida, asi que
        # la cookie guardada puede haber dejado de valer -- que es justo lo que
        # nos ha devuelto el reto. Sin este clear, _ensure_anubis ve la cookie
        # en cache, se sale sin resolver nada y el reintento repite la MISMA
        # cookie muerta: el POST por proxy no se recuperaba jamas.
        _LOG("Anubis en POST - cookie caducada, resolviendo de nuevo")
        try:
            anubis.clear_cache(urlparse(url).hostname)
        except Exception:
            pass
        _ensure_anubis(url)
        cached = _anubis_cookies_for(url)
        if cached:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cached.items())
            kwargs["headers"] = h
            if json_data is not None:
                r = requests.post(proxied, data=json.dumps(json_data), **kwargs)
            else:
                r = requests.post(proxied, data=data, **kwargs)

    final = r.headers.get("x-mw-relay-final")
    if final:
        try:
            r.url = final
        except Exception:
            pass

    _mira_mantenimiento(r)
    r.raise_for_status()
    return r


# ── Resolucion de dominio ───────────────────────────────────────────────

def _resolve_via_telegram():
    """Obtiene dominios disponibles del canal de Telegram."""
    try:
        url = "https://t.me/s/DonTorrent"
        r = requests.get(url, timeout=12, headers={"User-Agent": UA})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        posts = soup.select(".tgme_widget_message_text") or soup.select(".tgme_widget_message")

        available, censored = [], set()
        for msg in posts:
            text = msg.get_text(" ", strip=True)
            low = text.lower()
            hosts = []
            for m in DOMAIN_RE.finditer(text):
                hosts.append(m.group(1).lower())
            for a in msg.select("a[href]"):
                for m in DOMAIN_RE.finditer(a.get("href", "")):
                    hosts.append(m.group(1).lower())
            hosts = list(dict.fromkeys(hosts))
            if not hosts:
                continue
            is_avail = ("✅" in text) or ("disponible" in low)
            is_cens = ("❌" in text) or ("censurad" in low) or ("caido" in low)
            for h in hosts:
                if is_avail and not is_cens:
                    available.append(h)
                elif is_cens:
                    censored.add(h)

        available = list(reversed(available))
        seen, out = set(), []
        for h in available:
            if h not in seen:
                seen.add(h)
                out.append(h)
        return out, censored
    except Exception:
        return [], set()


# --- DonTorrent CAIDO (su servidor, no el ISP) --------------------------------
# 23-09-2026: su centro de datos se quedo sin conexion y TODOS sus dominios
# contestaban un 503 con su pagina de mantenimiento ("La web volvera
# enseguida"). resolve_domain probaba entonces los 14 dominios (DoH 15 s + proxy
# 30 s cada uno, mas Telegram y Supabase) y, como no guardaba el fallo, lo
# repetia en CADA trabajo: medido en el PC, mas de un minuto por intento, hilos
# ocupados y Kodi tardando 14 s de mas en cerrarse. Si es su pagina de
# mantenimiento no hay otro dominio que probar (es la misma casa), y si nada
# contesta se deja de buscar un rato: 3 min, apuntado en disco porque cada
# accion de la tele es un proceso nuevo.
_MANT_RE = re.compile(
    r"volver[aá] enseguida|problema puntual en el servidor|__proxy-dok", re.I)
_RESUELVE_FALLO = [0.0]
_RESUELVE_MANT = [False]       # el ultimo fallo fue SU pagina de mantenimiento
_RESUELVE_FALLO_TTL = 180
_FALLO_FILE = (os.path.join(_DT_PROFILE, "dt_dominio_fallo.json")
               if _DT_PROFILE else "")


class _DTCaido(Exception):
    """DonTorrent contesta con su pagina de mantenimiento."""


def _es_mantenimiento(resp):
    try:
        return (resp is not None and resp.status_code >= 500
                and bool(_MANT_RE.search(resp.text or "")))
    except Exception:
        return False


def _fallo_reciente(ttl=None):
    ttl = _RESUELVE_FALLO_TTL if ttl is None else ttl
    if (time.time() - _RESUELVE_FALLO[0]) < ttl:
        return True
    if not _FALLO_FILE:
        return False
    try:
        with open(_FALLO_FILE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        ts = float(d.get("ts") or 0)
        if (time.time() - ts) < ttl:
            _RESUELVE_FALLO[0] = ts
            _RESUELVE_MANT[0] = bool(d.get("mant"))
            return True
    except Exception:
        pass
    return False


def _apunta_fallo(ok, mant=False):
    """Apunta (o borra) que resolver el dominio acaba de fallar del todo, y si
    fue porque DonTorrent enseñaba su pagina de mantenimiento."""
    if ok:
        if not _RESUELVE_FALLO[0] and not (_FALLO_FILE and os.path.exists(_FALLO_FILE)):
            return          # lo normal: nada que borrar, ni una escritura
        _RESUELVE_FALLO[0] = 0.0
        _RESUELVE_MANT[0] = False
        try:
            os.remove(_FALLO_FILE)
        except Exception:
            pass
        return
    _RESUELVE_FALLO[0] = time.time()
    _RESUELVE_MANT[0] = bool(mant)
    if _FALLO_FILE:
        try:
            with open(_FALLO_FILE, "w", encoding="utf-8") as f:
                json.dump({"ts": _RESUELVE_FALLO[0], "mant": bool(mant)}, f)
        except Exception:
            pass


def en_mantenimiento():
    """True si hace poco DonTorrent contestaba con su pagina de mantenimiento:
    para decirlo tal cual en la tele en vez de "intentalo en un minuto"."""
    return _fallo_reciente(600) and _RESUELVE_MANT[0]


def _probe_domain(host):
    """Verifica si un dominio de DonTorrent esta activo. Si contesta con su
    pagina de mantenimiento lanza _DTCaido: no hay otro dominio que probar."""
    url = f"https://{host}/"
    # DoH directo (evita ISP + resuelve Anubis correctamente)
    try:
        r = _doh_fetch("GET", url, timeout=15)
        body = r.text.lower()
        if any(k in body for k in ("torrent", "pelicula", "serie")):
            # Auto-curativo: si el dominio redirigio (301) a otro dontorrent.*
            # (rotacion de dominio), adoptamos el DESTINO. Asi el POST del PoW
            # va directo al canonico vigente y no sufre el POST->GET->405.
            final_host = (urlparse(r.url).hostname or host).lower()
            if final_host != host and DOMAIN_RE.search(final_host):
                _LOG(f"_probe_domain DoH: {host} -> redirect -> {final_host}")
                return final_host
            _LOG(f"_probe_domain DoH OK: {host}")
            return host
    except Exception as e:
        _LOG(f"_probe_domain DoH fallo {host}: {e}")
        if _es_mantenimiento(getattr(e, "response", None)):
            raise _DTCaido(host)
    # Fallback: proxy
    try:
        r = _proxy_get(url)
        body = r.text.lower()
        if any(k in body for k in ("torrent", "pelicula", "serie")):
            final = r.headers.get("x-mw-relay-final", r.url)
            m = re.match(r'https?://([^/]+)', final)
            return m.group(1).lower() if m else host
    except Exception as e:
        if _es_mantenimiento(getattr(e, "response", None)):
            raise _DTCaido(host)
    return None


def resolve_domain(force=False):
    """Resuelve el dominio activo de DonTorrent.

    Prueba candidatos EN PARALELO y devuelve el PRIMERO que responda.
    Esto es critico en Android donde DoH puede fallar para algunos dominios:
    si probamos secuencialmente uno-a-uno tardamos demasiado y el usuario
    se queda sin DT en la busqueda. En paralelo, en cuanto uno responde,
    devolvemos esa URL.
    """
    global _cached_domain, _cached_domain_ts

    # Manual override (setting opcional dt_base_url)
    manual = ""
    try:
        manual = (_ADDON.getSetting("dt_base_url") or "").strip().rstrip("/")
    except Exception:
        pass
    if manual:
        host = manual.replace("https://", "").replace("http://", "").rstrip("/")
        host = host.split("/")[0]
        # Ignorar overrides que apuntan a mirrors VIEJOS que redirigen (301): el
        # redirect convierte el POST del PoW en GET -> api_validate_pow.php = 405.
        # Asi caemos a la auto-resolucion (fast-path canonico .review).
        _STALE = {"dontorrent.science", "www.dontorrent.science",
                  "dontorrent.support", "www.dontorrent.support",
                  "dontorrent.review", "www.dontorrent.review",
                  "dontorrent.management", "www.dontorrent.management"}
        if host and host not in _STALE:
            return host

    # Cache valida
    if not force and _cached_domain and (time.time() - _cached_domain_ts) < _DOMAIN_TTL:
        return _cached_domain

    # Acaba de fallar del todo (o DonTorrent esta en mantenimiento): no se
    # vuelve a sondear hasta dentro de _RESUELVE_FALLO_TTL (ver _DTCaido).
    if not force and _fallo_reciente():
        return _cached_domain or FALLBACK_DOMAINS[0]

    # Fast path: dominio CANONICO vigente. Evita que la carrera paralela (o un
    # Supabase/cache con un mirror viejo) elija un dominio que redirige el POST
    # del PoW (.science -> 301 .review -> POST se vuelve GET -> 405).
    try:
        canon = _probe_domain(_CANONICAL_DOMAIN)
        if canon:
            _cached_domain = canon
            _cached_domain_ts = time.time()
            _LOG(f"resolve_domain: canonico {canon}")
            _apunta_fallo(True)
            return canon
    except _DTCaido:
        _LOG("resolve_domain: DonTorrent en MANTENIMIENTO (su pagina de "
             "'volvera enseguida'); no se prueban mas dominios en %d s"
             % _RESUELVE_FALLO_TTL)
        _apunta_fallo(False, mant=True)
        return _cached_domain or FALLBACK_DOMAINS[0]
    except Exception:
        pass

    # Construir lista de candidatos: Supabase + Telegram + Fallback
    candidates = []
    seen = set()

    # 1) Supabase: el dominio "oficial" del usuario va primero
    sb_main = None
    sb_fallbacks = []
    try:
        from . import supabase_sync as sb
        sb_main = sb.get_domain("dontorrent")
        sb_fallbacks = sb.get_fallback_domains("dontorrent")
    except Exception:
        pass
    if sb_main and sb_main not in seen:
        seen.add(sb_main)
        candidates.append(sb_main)
    for h in sb_fallbacks:
        if h and h not in seen:
            seen.add(h)
            candidates.append(h)

    # 2) Telegram (puede ser lento, lo dejamos no-bloqueante via timeout corto)
    try:
        tg_avail, tg_cens = _resolve_via_telegram()
        for h in tg_avail:
            if h and h not in seen and h not in tg_cens:
                seen.add(h)
                candidates.append(h)
    except Exception:
        pass

    # 3) Fallback hardcoded
    for h in FALLBACK_DOMAINS:
        if h and h not in seen:
            seen.add(h)
            candidates.append(h)

    if not candidates:
        return _cached_domain or FALLBACK_DOMAINS[0]

    # Probar todos en PARALELO — el primero que responda gana.
    # SIN `with`: al salir de un `with ThreadPoolExecutor` se ESPERA a todos los
    # sondeos que sigan en marcha (15 s de DoH + 30 s de proxy cada uno), asi
    # que hasta un acierto rapido tardaba lo que el mas lento. Y si ninguno
    # contestaba en 20 s, el TimeoutError caia en el "secuencial" de abajo, que
    # volvia a probar TODOS uno a uno: mas de un minuto (medido el 23-09).
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import TimeoutError as _FuturosTarde
    _LOG(f"resolve_domain: probando {len(candidates)} candidatos en paralelo")
    ex = None
    caido = False
    try:
        ex = ThreadPoolExecutor(max_workers=min(8, len(candidates)))
        futures = {ex.submit(_probe_domain, h): h for h in candidates}
        try:
            for fut in as_completed(futures, timeout=20):
                try:
                    result = fut.result()
                except _DTCaido:
                    caido = True        # mantenimiento: es la misma casa
                    break
                except Exception:
                    continue
                if result:
                    _cached_domain = result
                    _cached_domain_ts = time.time()
                    _LOG(f"Dominio confirmado (paralelo): {result}")
                    _apunta_fallo(True)
                    return result
        except _FuturosTarde:
            _LOG("resolve_domain: ningun candidato contesto en 20 s")
    except Exception as e:
        # El pool en si fallo (no los sondeos): secuencial, como siempre.
        _LOG(f"resolve_domain paralelo falló: {e}, secuencial...")
        for h in candidates:
            try:
                confirmed = _probe_domain(h)
            except _DTCaido:
                caido = True
                break
            except Exception:
                continue
            if confirmed:
                _cached_domain = confirmed
                _cached_domain_ts = time.time()
                _apunta_fallo(True)
                return confirmed
    finally:
        if ex is not None:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except TypeError:           # Python < 3.9
                ex.shutdown(wait=False)
            except Exception:
                pass

    if caido:
        _LOG("resolve_domain: DonTorrent en MANTENIMIENTO; no se prueban mas "
             "dominios en %d s" % _RESUELVE_FALLO_TTL)
    else:
        _LOG(f"resolve_domain: ningun candidato respondio, usando cache/fallback")
    _apunta_fallo(False, mant=caido)
    return _cached_domain or FALLBACK_DOMAINS[0]


# --- Breaker del DoH ---------------------------------------------------------
# El ISP de casa no solo bloquea por DNS: RESETEA la conexion contra las IPs de
# Cloudflare de DonTorrent (WinError 10054 / errno 104). Cuando eso pasa, el
# camino DoH esta muerto para todo el mundo en esa casa, pero cada peticion
# seguia gastando 15-30s en descubrirlo (timeout 25s + reintentos) antes de
# tirar del proxy, que responde en ~6s. Medido: la ficha de una serie, 21-38s;
# el relay espera 14s -> el Inicio se quedaba SIN capitulos en todas las series.
# Con el breaker: el primer fallo marca el DoH caido 10 min y el resto va
# directo al proxy.
_DOH_DOWN_UNTIL = [0.0]
_DOH_COOLDOWN = 600


def _doh_alive():
    return time.time() >= _DOH_DOWN_UNTIL[0]


def _doh_mark(ok):
    if ok:
        _DOH_DOWN_UNTIL[0] = 0.0
    else:
        _DOH_DOWN_UNTIL[0] = time.time() + _DOH_COOLDOWN
        _LOG("DoH marcado CAIDO %ds (el ISP esta reseteando): se tira de proxy"
             % _DOH_COOLDOWN)


_FICHA_RE = re.compile(r'href=["\']/(?:pelicula|serie|documental)/\d+')


def fetch_html(path=None, q=None):
    """HTML de un listado o de una BUSQUEDA de DonTorrent.

    Para las busquedas, ademas de traer el HTML se comprueba que traiga fichas:
    el buscador de DonTorrent es LITERAL y el guion cuenta, asi que "x men"
    devuelve su pagina de resultados VACIA mientras que "x-men" devuelve diez
    (con la pelicula original entre ellas). Sin esto, quien escribe el titulo
    separado no encuentra nada de esa pelicula -- y este es el camino que usa
    la web, no `search()`."""
    html = _fetch_html_una(path=path, q=q)
    if not q or (html and _FICHA_RE.search(html)):
        return html
    for alt in _variantes_query(q):
        h2 = _fetch_html_una(path=path, q=alt)
        if h2 and _FICHA_RE.search(h2):
            _LOG("fetch_html: %r sin fichas, %r -> %d bytes con resultados"
                 % (q, alt, len(h2)))
            return h2
    return html


def _fetch_html_una(path=None, q=None):
    """HTML CRUDO de un listado (path: '/', '/peliculas', '/series', '/page/N')
    o de una busqueda (q) de DonTorrent, desde la IP RESIDENCIAL del box (DoH,
    resuelve Anubis). Lo usa el relay cuando DonTorrent le bloquea su IP de
    datacenter: el box lo trae y el relay lo parsea con su parser. '' si falla.

    Los LISTADOS tienen DOS caminos, igual que _get/_post/download_torrent: DoH
    y, si falla, el proxy (CF Worker). Antes solo habia DoH y ningun plan B:
    cuando el ISP tumba el rango de IPs de DonTorrent (RESET de conexion, visto
    2026-08-01: 4/4 fallos por DoH y el proxy sirviendo las 39 KB en 0,3 s) esta
    funcion devolvia '' EN SILENCIO y con ella se caia la PRE-CARGA del catalogo
    (Inicio se quedaba stale para todos).

    La BUSQUEDA (q) se queda solo con DoH a proposito: es un POST y el JWT de
    Anubis va atado a la IP, pero el CF Worker sale por una IP distinta en cada
    peticion, asi que /buscar por proxy SIEMPRE responde con el reto (verificado
    2026-08-01, incluso resolviendo un PoW nuevo por intento). Intentarlo solo
    gastaria un PoW -- carisimo en una caja Android -- para acabar en '' igual."""
    host = resolve_domain()
    if q:
        url = f"https://{host}/buscar"
        data = {"valor": q, "Buscar": "Buscar"}
    else:
        p = path or "/"
        if not p.startswith("/"):
            p = "/" + p
        url = f"https://{host}{p}"
        data = None
    etq = f"q={q}" if q else (path or "/")

    # 1) DoH: conexion residencial directa (el camino normal, ~1 s). Se salta
    #    si esta marcado caido: con el ISP reseteando, insistir cuesta 15-30s
    #    para acabar en el proxy igual (ver breaker arriba).
    if _doh_alive():
        try:
            # 10s basta: el DoH sano responde en ~1s. Con 25s, cada fallo se
            # llevaba media vida y el relay ya se habia rendido.
            r = (_doh_fetch("POST", url, data=data, timeout=10)
                 if data is not None else _doh_fetch("GET", url, timeout=10))
            html = r.text or ""
            if len(html) > 500:
                _doh_mark(True)
                _LOG(f"fetch_html ({etq}) -> {len(html)} bytes")
                return html
            _LOG(f"fetch_html ({etq}) DoH: respuesta corta ({len(html)})")
        except Exception as e:
            _LOG(f"fetch_html ({etq}) DoH fallo: {e}")
            if isinstance(e, (requests.exceptions.ConnectionError,
                              requests.exceptions.Timeout)):
                _doh_mark(False)   # el ISP esta tumbando el rango: al proxy
    else:
        _LOG(f"fetch_html ({etq}): DoH en cuarentena -> proxy directo")

    if data is not None:      # busqueda: el proxy no puede (ver docstring)
        return ""

    # 2) Proxy CF Worker (residencial, resuelve Anubis). Mismo plan B que ya
    #    usan _get/_post/download_torrent, aqui faltaba.
    try:
        html = _proxy_get(url).text or ""
        if anubis.is_anubis(html):
            _LOG(f"fetch_html ({etq}) proxy: sigue el reto Anubis")
            return ""
        _LOG(f"fetch_html ({etq}) -> {len(html)} bytes (via proxy)")
        return html
    except Exception as e:
        _LOG(f"fetch_html ({etq}) proxy fallo: {e}")
    return ""


def base_url():
    return f"https://{resolve_domain()}"


# ── Helpers ─────────────────────────────────────────────────────────────

def _get(path):
    url = urljoin(base_url() + "/", path.lstrip("/"))
    # 0) Render relay /dtfetch (PRIORIDAD): resuelve Anubis server-side y
    #    funciona en Android donde DoH/proxy CF fallan. Es lo que hace que
    #    los LISTADOS de DonTorrent (Cine/Series/Documentales) aparezcan
    #    en el TV box, no solo la busqueda.
    r = _render_fetch(url)
    if r is not None:
        return BeautifulSoup(r.text, "html.parser"), url
    # 1) DoH directo (PC / redes sin bloqueo)
    try:
        r = _doh_fetch("GET", url)
        return BeautifulSoup(r.text, "html.parser"), r.url
    except Exception as e:
        _LOG(f"_get DoH fallo: {e}, fallback a proxy")
    # 2) Proxy CF Worker
    r = _proxy_get(url)
    return BeautifulSoup(r.text, "html.parser"), r.url


def _post(path, data):
    url = urljoin(base_url() + "/", path.lstrip("/"))
    # DoH directo
    try:
        r = _doh_fetch("POST", url, data=data)
        return BeautifulSoup(r.text, "html.parser"), r.url
    except Exception as e:
        _LOG(f"_post DoH fallo: {e}, fallback a proxy")
    r = _proxy_post(url, data=data)
    return BeautifulSoup(r.text, "html.parser"), r.url


ITEM_PATTERNS = {
    "movie":       re.compile(r"^/pelicula/\d+/"),
    "tvshow":      re.compile(r"^/serie/\d+/\d+/"),
    "documentary": re.compile(r"^/documental/\d+/\d+/"),
}

SECTION_PATH = {
    "movie":        "peliculas",
    "movie_hd":     "peliculas/hd",
    "movie_4k":     "peliculas/4K",
    "tvshow":       "series",
    "tvshow_hd":    "series/hd",
    "tvshow_4k":    "series/4k",
    "documentary":  "documentales",
    "estrenos":     "",
}

_QUALITY_RE = re.compile(
    r"\(([^)]*(?:1080p|720p|2160p|4K|HDRip|BluRay|BDRip|BDremux|BRRip|"
    r"WEB-?DL|WEBRip|microHD|HDTV|DVDRip)[^)]*)\)",
    re.IGNORECASE,
)


def _classify(href):
    for kind, pat in ITEM_PATTERNS.items():
        if pat.match(href):
            return kind
    return None


def _base_kind(kind):
    if kind.startswith("movie"):
        return "movie"
    if kind.startswith("tvshow"):
        return "tvshow"
    return kind


def _upgrade_weserv(src, width=500):
    """Mejora la calidad de thumbnails de weserv.nl."""
    if "images.weserv.nl" not in src:
        return src
    src = re.sub(r"([?&])w=\d+", r"\g<1>w=" + str(width), src)
    src = re.sub(r"([?&])h=\d+&?", r"\g<1>", src).rstrip("&?")
    src = src.replace("[", "%5B").replace("]", "%5D")
    return src


def _img_src(a):
    img = a.find("img")
    if not img:
        return None
    src = img.get("src") or img.get("data-src") or img.get("data-original")
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    return _upgrade_weserv(src, width=500)


def _quality_near(a):
    """Extrae calidad de un span hermano del anchor."""
    for sib in a.next_siblings:
        get_attr = getattr(sib, "get", None)
        if callable(get_attr):
            classes = get_attr("class") or []
            if "badge" in classes:
                break
            txt = sib.get_text(" ", strip=True)
        else:
            txt = str(sib).strip()
        if not txt:
            continue
        m = _QUALITY_RE.search(txt)
        if m:
            return m.group(1).strip()
    parent = a.parent
    if parent is not None:
        m = _QUALITY_RE.search(parent.get_text(" ", strip=True))
        if m:
            return m.group(1).strip()
    return None


def _title_from_slug(href):
    m = re.search(r"/(?:pelicula|serie|documental)/\d+/(?:\d+/)?(.+?)/?$", href)
    if not m:
        return None
    slug = m.group(1).split("/")[-1]
    txt = slug.replace("-", " ").replace("_", " ").strip()
    return re.sub(r"\s+", " ", txt) or None


def _parse_items(soup, page_url, kind_filter=None):
    """Extrae items de una pagina de listado de DonTorrent."""
    items, seen = [], set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        kind = _classify(href)
        if not kind:
            continue
        if kind_filter and kind != kind_filter:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = (a.get("title") or "").strip()
        if not title:
            title = (a.get_text() or "").strip()
        if not title:
            title = _title_from_slug(href) or ""
        if not title:
            continue
        title = re.sub(r"\s+", " ", title).rstrip(" .")
        # Extraer calidad del propio titulo si la tiene entre parentesis
        quality = _quality_near(a)
        if not quality:
            qm = _QUALITY_RE.search(title)
            if qm:
                quality = qm.group(1).strip()
        items.append({
            "title": title,
            "url": urljoin(page_url, href),
            "kind": kind,
            "thumb": _img_src(a),
            "quality": quality,
            "source": SOURCE,
        })
    return items


# ── Listados ────────────────────────────────────────────────────────────

def latest(kind="movie", page=1):
    """Lista items de una seccion."""
    if kind in ("estrenos", "estrenos_movie", "estrenos_tvshow"):
        path = "" if page <= 1 else f"page/{page}"
        soup, url = _get(path)
        kf = None
        if kind == "estrenos_movie":
            kf = "movie"
        elif kind == "estrenos_tvshow":
            kf = "tvshow"
        items = _parse_items(soup, url, kind_filter=kf)
        _LOG(f"latest kind={kind} -> {len(items)} items")
        return items
    section = SECTION_PATH.get(kind, "peliculas")
    path = section if page <= 1 else f"{section}/page/{page}"
    soup, url = _get(path)
    items = _parse_items(soup, url, kind_filter=_base_kind(kind))
    _LOG(f"latest kind={kind} page={page} -> {len(items)} items")
    return items


def _direct_post(url, data=None, json_data=None, **kwargs):
    """POST directo (sin proxy). El proxy CF Worker no reenvía cuerpos POST,
    así que para búsqueda y PoW necesitamos conexión directa."""
    kwargs.setdefault("timeout", 25)
    h = dict(HEADERS)
    cached = _anubis_cookies_for(url)
    if cached:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cached.items())
    if json_data is not None:
        h["Content-Type"] = "application/json"
        h["Accept"] = "application/json,*/*;q=0.8"
        kwargs["headers"] = h
        return requests.post(url, data=json.dumps(json_data), **kwargs)
    else:
        kwargs["headers"] = h
        return requests.post(url, data=data, allow_redirects=True, **kwargs)


def _robust_post_json(url, json_data):
    """POST JSON con fallback: DoH -> directo -> proxy.
    Devuelve dict parseado o lanza RuntimeError."""
    errors = []

    # 1) POST via DoH (bypass ISP + Anubis fiable)
    try:
        r = _doh_fetch("POST", url, json_data=json_data)
        text = (r.text or "").strip()
        if text and len(text) > 2 and text[0] in ('{', '['):
            data = json.loads(text)
            _LOG(f"_robust_post_json DoH OK -> {list(data.keys())[:5]}")
            return data
        _LOG(f"_robust_post_json DoH: no es JSON (len={len(text)} "
             f"preview={text[:120]!r})")
        errors.append(f"doh: respuesta no-JSON ({len(text)} bytes)")
    except Exception as e:
        _LOG(f"_robust_post_json DoH error: {e}")
        errors.append(f"doh: {e}")

    # 2) POST directo (sin proxy)
    try:
        r = _direct_post(url, json_data=json_data)
        text = (r.text or "").strip()
        if text and len(text) > 2 and text[0] in ('{', '['):
            data = json.loads(text)
            _LOG(f"_robust_post_json directo OK -> {list(data.keys())[:5]}")
            return data
        _LOG(f"_robust_post_json directo: no es JSON (len={len(text)} "
             f"preview={text[:120]!r})")
        errors.append(f"direct: respuesta no-JSON ({len(text)} bytes)")
    except requests.exceptions.ConnectionError as e:
        _LOG(f"_robust_post_json directo: conexion rechazada (ISP?): {e}")
        errors.append(f"direct: conexion rechazada")
    except Exception as e:
        _LOG(f"_robust_post_json directo error: {e}")
        errors.append(f"direct: {e}")

    # 3) POST via proxy
    try:
        r = _proxy_post(url, json_data=json_data)
        text = (r.text or "").strip()
        if text and len(text) > 2 and text[0] in ('{', '['):
            data = json.loads(text)
            _LOG(f"_robust_post_json proxy OK -> {list(data.keys())[:5]}")
            return data
        _LOG(f"_robust_post_json proxy: no es JSON (len={len(text)} "
             f"preview={text[:120]!r})")
        errors.append(f"proxy: respuesta no-JSON ({len(text)} bytes)")
    except Exception as e:
        _LOG(f"_robust_post_json proxy error: {e}")
        errors.append(f"proxy: {e}")

    raise RuntimeError("POST JSON fallo: " + " | ".join(errors))


def _worker_search(query):
    """Busca via Worker /dtsearch.

    El Worker resuelve Anubis PoW + hace POST /buscar TODO en UNA sola
    invocacion (misma IP de salida → JWT Anubis valido).
    Funciona incluso con ISP que bloquea DonTorrent.
    """
    domain = resolve_domain()
    proxy = _proxy_base()
    url = f"{proxy}/dtsearch"

    payload = {"domain": domain, "q": query}
    _LOG(f"worker_search: POST {url} domain={domain} q={query!r}")
    r = requests.post(url, json=payload, timeout=45,
                      headers={"Content-Type": "application/json",
                               "User-Agent": UA})

    ct = r.headers.get("content-type", "")

    # Si el Worker devuelve JSON → error
    if "application/json" in ct:
        try:
            data = r.json()
        except Exception:
            data = {}
        _LOG(f"worker_search: error JSON: {data.get('error', 'unknown')}")
        return []

    # Parsear HTML de búsqueda
    if r.status_code == 200 and len(r.text) > 500:
        if anubis.is_anubis(r.text):
            _LOG("worker_search: respuesta sigue siendo Anubis")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        items = _parse_items(soup, f"https://{domain}/buscar")
        _LOG(f"worker_search -> {len(items)} items")
        return items

    _LOG(f"worker_search: HTTP {r.status_code} len={len(r.text)}")
    return []


def _render_relay_url():
    """URL del Render relay (preferred backend - sin limite CPU, fuera de
    bloqueo ISP español). Lee setting local primero, despues Supabase."""
    try:
        url = (_ADDON.getSetting("render_relay_url") or "").strip().rstrip("/")
        if url:
            return url
    except Exception:
        pass
    try:
        from . import supabase_sync as sb
        return (sb.get_relay_url() or "").strip().rstrip("/")
    except Exception:
        return ""


def _render_search(query):
    """Busca via Render relay /dtsearch. Solucion definitiva: Anubis
    PoW resuelto server-side, sin limite CPU, fuera de bloqueo ISP."""
    global _cached_domain, _cached_domain_ts
    base = _render_relay_url()
    if not base:
        return []
    domain = _cached_domain or ""
    url = f"{base}/dtsearch"
    _LOG(f"render_search: POST {url} q={query!r} domain={domain!r}")
    try:
        r = requests.post(url, json={"q": query, "domain": domain},
                          timeout=60,
                          headers={"Content-Type": "application/json",
                                   "User-Agent": UA})
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                err = r.json()
                _LOG(f"render_search: error JSON: {err.get('error')}")
            except Exception:
                pass
            return []
        if r.status_code == 200 and len(r.text) > 500:
            if anubis.is_anubis(r.text):
                _LOG("render_search: respuesta sigue siendo Anubis")
                return []
            # Render envia el dominio usado en X-MW-Dt-Domain
            actual_domain = r.headers.get("X-MW-Dt-Domain", "")
            if actual_domain:
                _cached_domain = actual_domain
                _cached_domain_ts = time.time()
            soup = BeautifulSoup(r.text, "html.parser")
            items = _parse_items(soup, f"https://{actual_domain or 'dontorrent.science'}/buscar")
            _LOG(f"render_search -> {len(items)} items")
            return items
        _LOG(f"render_search: HTTP {r.status_code} len={len(r.text)}")
    except Exception as e:
        _LOG(f"render_search error: {e.__class__.__name__}: {e}")
    return []


# Se pone a True cuando una estrategia recibe una pagina de resultados de
# verdad (HTTP 200, sin reto de Anubis). Si la web contesto y aun asi no habia
# nada, el problema NO es el transporte sino COMO esta escrito el termino: no
# tiene sentido seguir probando caminos de red, hay que cambiar la consulta.
# Va POR HILO: el addon resuelve cada fuente en el suyo y dos busquedas a la
# vez se pisarian la senal (una acabaria saltandose un camino de red que si
# necesitaba, y eso no da error: solo devuelve menos resultados).
import threading as _thr_dt

_WEB_LOCAL = _thr_dt.local()


class _WebContesto(object):
    def __getitem__(self, i):
        return getattr(_WEB_LOCAL, "ok", False)

    def __setitem__(self, i, v):
        _WEB_LOCAL.ok = bool(v)


_WEB_CONTESTO = _WebContesto()


def _variantes_query(q):
    """Como hay que escribirle a DonTorrent para que encuentre lo mismo.

    Su buscador es LITERAL: los guiones cuentan. Medido contra la web real el
    20-09-2026 con el dominio vigente:
        "x men"  ->  0 fichas
        "x-men"  -> 10 fichas (entre ellas X-Men-4K, la original)
        "xmen"   ->  0 fichas
    Asi que quien escribe "x men" en el movil no encuentra NINGUNA pelicula de
    X-Men, aunque DonTorrent las tenga todas. Lo mismo con Spider-Man, Wall-E o
    cualquier titulo con guion. Estas son las otras formas de escribir lo mismo,
    en orden, y solo se prueban si la primera vuelve vacia."""
    q = (q or "").strip()
    out = []
    if " " in q:
        out.append(q.replace(" ", "-"))         # "x men"  -> "x-men"
    if "-" in q:
        out.append(q.replace("-", " "))         # "x-men"  -> "x men"
        out.append(q.replace("-", ""))          # "x-men"  -> "xmen"
    vistas, limpias = {q.lower()}, []
    for v in out:
        if v and v.lower() not in vistas:
            vistas.add(v.lower())
            limpias.append(v)
    return limpias[:2]                          # dos intentos como mucho


def search(query):
    """Busca en DonTorrent probando tambien como lo escribe SU buscador.

    `_search_una` hace el trabajo de siempre; esto solo se encarga de que un
    guion de mas o de menos no deje al usuario sin resultados."""
    _WEB_CONTESTO[0] = False
    items = _search_una(query)
    if items:
        return items
    # Si la web contesto, los reintentos van RAPIDOS (sin el Worker, que para
    # el POST de busqueda no sirve -- el reto de Anubis va atado a la IP y
    # siempre responde el challenge; son ~20 s tirados en cada intento).
    rapido = _WEB_CONTESTO[0]
    for alt in _variantes_query(query):
        _LOG("search: sin resultados con %r, probando %r (rapido=%s)"
             % (query, alt, rapido))
        items = _search_una(alt, rapido=rapido)
        if items:
            _LOG("search: %r -> %d items" % (alt, len(items)))
            return items
    return []


def _search_una(query, rapido=False):
    """Busca en DonTorrent con multiples estrategias.

    Orden de prioridad:
      0) Render relay /dtsearch  ← PRIORIDAD (Python sin limites, fuera ISP)
      1) DoH DNS bypass (conexion directa, IP real del usuario)
      2) POST directo sin DoH (si ISP no bloquea)
      3) Worker /dtsearch (Cloudflare, proxy ligero)
    """
    _LOG(f"search: {query}")

    # ── Estrategia 0: Render relay (Anubis solver server-side) ───────
    # En modo rapido se salta: si la web ya nos ha contestado desde aqui, pedir
    # lo mismo al relay -cuya IP lleva baneada por DonTorrent desde siempre- es
    # esperar 20 s para nada. Era casi todo lo que tardaba reintentar.
    try:
        items = [] if rapido else _render_search(query)
        if items:
            _LOG(f"search Render relay -> {len(items)} items")
            return items
    except Exception as e:
        _LOG(f"search Render relay error: {e}")

    search_url = base_url() + "/buscar"
    form_data = {"valor": query, "Buscar": "Buscar"}

    # ── Estrategia 1: POST directo via DoH (bypass ISP + Anubis) ───
    try:
        r = _doh_fetch("POST", search_url, data=form_data)
        _LOG(f"search DoH POST: HTTP {r.status_code} len={len(r.text)}")
        if not anubis.is_anubis(r.text):
            _WEB_CONTESTO[0] = True
            soup = BeautifulSoup(r.text, "html.parser")
            items = _parse_items(soup, r.url)
            _LOG(f"search DoH POST -> {len(items)} items")
            if items:
                return items
    except Exception as e:
        _LOG(f"search DoH POST error: {e}")

    # ── Estrategia 2: POST directo sin DoH ─────────────────────────
    # Funciona si el ISP no bloquea DonTorrent.
    try:
        # Pre-flight Anubis
        try:
            _ensure_anubis(search_url)
        except Exception:
            pass
        r = _direct_post(search_url, data=form_data)
        _LOG(f"search direct POST: HTTP {r.status_code} len={len(r.text)}")
        if r.status_code == 200 and len(r.text) > 500:
            if anubis.is_anubis(r.text):
                r = _handle_anubis(r, search_url)
                if not anubis.is_anubis(r.text):
                    r = _direct_post(search_url, data=form_data)
            if not anubis.is_anubis(r.text):
                _WEB_CONTESTO[0] = True
                soup = BeautifulSoup(r.text, "html.parser")
                items = _parse_items(soup, r.url)
                _LOG(f"search direct POST -> {len(items)} items")
                if items:
                    return items
    except requests.exceptions.ConnectionError:
        _LOG("search direct POST: conexion rechazada (ISP bloquea?)")
    except Exception as e:
        _LOG(f"search direct POST error: {e}")

    # ── Estrategia 3: Worker /dtsearch ─────────────────────────────
    # El Worker resuelve Anubis + hace POST /buscar en UNA invocación
    # (misma IP de salida). Funciona incluso con ISP que bloquea.
    # Tambien se salta cuando la web YA HA CONTESTADO en este mismo intento:
    # si /buscar devolvio su pagina de resultados y no habia nada, el Worker va
    # a preguntar exactamente lo mismo y va a recibir lo mismo (cuando no el
    # reto de Anubis). Eran ~20 s de espera por cada busqueda sin resultados.
    if rapido or _WEB_CONTESTO[0]:
        _LOG("search: salto el Worker (la web ya contesto; era cosa del termino)")
        return []
    try:
        items = _worker_search(query)
        _LOG(f"search Worker /dtsearch -> {len(items)} items")
        if items:
            return items
    except Exception as e:
        _LOG(f"search Worker /dtsearch error: {e}")

    _LOG("search: TODAS las estrategias fallaron")
    return []


# ── Cache de titulo de detalle ──────────────────────────────────────────
_TITLE_CACHE = {}


def fetch_detail_title(url):
    """Obtiene el titulo H1 de la pagina de detalle (con acentos)."""
    if not url:
        return None
    if url in _TITLE_CACHE:
        return _TITLE_CACHE[url]
    try:
        r = _proxy_get(url)
        s = BeautifulSoup(r.text, "html.parser")
        h1 = s.select_one("h1.descargarTitulo, h1")
        txt = h1.get_text(" ", strip=True) if h1 else None
    except Exception:
        txt = None
    _TITLE_CACHE[url] = txt
    return txt


# ── Ficha de DonTorrent: la VERDAD de la fuente ─────────────────────────
# El LISTADO de DonTorrent no publica ni el titulo completo ni el director: cada
# tarjeta es un <a> con la imagen dentro, sin `title` ni `alt`, y su unico texto
# es el slug, que DonTorrent genera ya SIN las vocales acentuadas
# ('/pelicula/30780/La-ambicin-de-los-Savage'). La FICHA si los publica:
#
#   <h1 class="descargarTitulo">N121: Última parada</h1>
#   <p><b>Año:</b> 2026</p>  <p><b>Dirección:</b> Marcel Walz</p>
#   <p><b>Reparto:</b> Myrom Kingery, Morgan Flanagan, ...</p>
#
# Sirve para DOS cosas que no se pueden resolver de ninguna otra forma:
#   1) TILDES: el h1 trae el titulo entero ('Una Milla: Capítulo Uno').
#   2) HOMONIMOS: el DIRECTOR desempata dos peliculas del MISMO titulo y año.
#      Caso real (2026-08-10, lo vio el dueño): 'La odisea' de DonTorrent es el
#      mockbuster de The Asylum (Marcel Walz) y la app le pegaba el poster de
#      'La Odisea' de Nolan, que TMDB devuelve primero por popularidad. Con el
#      director el desempate es EXACTO (ver tmdb._pick_by_credits).
#
# El parseo NO depende de tildes ni entidades HTML (la ficha mezcla encodings:
# se ven 'A�o' y '&eacute;' en la misma pagina): se lee del onclick de cada
# enlace, que es ASCII puro y estable -> post('/peliculas/buscar', {campo:
# 'director', valor: 'Marcel Walz'}).
_DETAIL_INFO_CACHE = {}
_DETAIL_FAIL = {}          # key -> ts del ultimo fallo (solo en memoria)
_DETAIL_FAIL_TTL = 3600
_DETAIL_INFO_FILE = (os.path.join(_DT_PROFILE, "dt_fichas.json")
                     if _DT_PROFILE else "")
_DETAIL_FIELD_RE = re.compile(
    r"campo:\s*'(anyo|director|actores|genero)'\s*,\s*valor2?\s*:\s*'([^']*)'")
# Las fichas de PELICULA/DOCUMENTAL titulan con <h1 class="descargarTitulo"> y
# publican Año/Dirección/Reparto. Las de SERIE usan <h2 class="descargarTitulo">
# y NO traen ficha artistica (solo Formato/Tamaño), y su titulo incluye la
# temporada: "Sandokan - 1ª Temporada [1080p]". De ahi solo se aprovechan los
# ACENTOS (el relay sustituye el prefijo, nunca el titulo entero).
_DETAIL_H1_RE = re.compile(
    r"<h([12])[^>]*descargarTitulo[^>]*>(.*?)</h\1>", re.S | re.I)


def _detail_info_load():
    if _DETAIL_INFO_CACHE or not _DETAIL_INFO_FILE:
        return _DETAIL_INFO_CACHE
    try:
        with open(_DETAIL_INFO_FILE, "r", encoding="utf-8") as f:
            _DETAIL_INFO_CACHE.update(json.load(f) or {})
    except Exception:
        pass
    return _DETAIL_INFO_CACHE


def _detail_info_save():
    # Titulo, año, director y reparto de una peli NO cambian nunca -> se guarda
    # para siempre: cada ficha se pide UNA sola vez en la vida del box. Es lo que
    # hace que esto no suponga trafico extra contra DonTorrent (§9: machacarlo
    # escala a baneo de la IP).
    if not _DETAIL_INFO_FILE:
        return
    try:
        os.makedirs(os.path.dirname(_DETAIL_INFO_FILE), exist_ok=True)
        tmp = _DETAIL_INFO_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(list(_DETAIL_INFO_CACHE.items())[-4000:]), f)
        os.replace(tmp, _DETAIL_INFO_FILE)
    except Exception:
        pass


def parse_detail_info(html):
    """{title, year, director, cast} del HTML de una ficha. {} si no es una."""
    if not html or len(html) < 500:
        return {}
    out = {}
    m = _DETAIL_H1_RE.search(html)
    if m:
        try:
            t = BeautifulSoup(m.group(2), "html.parser").get_text(" ", strip=True)
        except Exception:
            t = re.sub(r"<[^>]+>", " ", m.group(2))
        t = re.sub(r"\s+", " ", t or "").strip()
        if t:
            out["title"] = t
    cast = []
    for campo, valor in _DETAIL_FIELD_RE.findall(html):
        v = (valor or "").strip()
        if not v:
            continue
        if campo == "anyo" and re.fullmatch(r"(19|20)\d{2}", v):
            out["year"] = v
        elif campo == "director" and "director" not in out:
            out["director"] = v
        elif campo == "actores" and len(cast) < 8:
            cast.append(v)
    if cast:
        out["cast"] = cast
    return out


def detail_info(path, cid=None, fetch=True):
    """Ficha de DonTorrent (titulo con tildes + año + director + reparto).

    `path` es la ruta con slug ('/pelicula/30825/La-odisea'); DonTorrent EXIGE el
    slug (por id pelado da 404). Cachea en disco PARA SIEMPRE bajo `cid` (o el
    path). Devuelve {} si no se pudo traer -> el llamante sigue como hasta ahora.

    `fetch=False` = solo mirar la cache (no pedir nada a DonTorrent): permite al
    servicio gastar un PRESUPUESTO de fichas por vuelta y repartir el catalogo
    en varias vueltas en vez de disparar 60 peticiones seguidas (§9: machacar
    DonTorrent escala a baneo)."""
    if not path:
        return {}
    key = str(cid or path)
    cache = _detail_info_load()
    ent = cache.get(key)
    if ent is not None:
        return ent
    if not fetch:
        return {}
    if (time.time() - _DETAIL_FAIL.get(key, 0)) < _DETAIL_FAIL_TTL:
        return {}      # fallo reciente: no gastar el presupuesto de esta vuelta
    html = ""
    try:
        html = fetch_html(path=path) or ""     # DoH y, si falla, proxy (2 caminos)
    except Exception as e:
        _LOG(f"detail_info ({path}) fallo: {e}")
    info = parse_detail_info(html)
    if not info:
        # NO se cachea en disco como "sin datos" (seria permanente y el titulo
        # real se perderia para siempre), pero SI se aparta un rato: si no, unas
        # pocas fichas rotas se comerian el presupuesto en CADA vuelta y las
        # demas no llegarian nunca a pedirse.
        _DETAIL_FAIL[key] = time.time()
        return {}
    cache[key] = info
    _detail_info_save()
    _LOG(f"detail_info ({path}) -> {info.get('title')!r} "
         f"{info.get('year')} dir={info.get('director')!r}")
    return info


# ── Detalle + Descargas ─────────────────────────────────────────────────

_last_warm_ts = 0.0


def warm_relay_async():
    """Despierta el relay de Render en segundo plano (no bloquea).

    Render free tier se duerme tras 15 min de inactividad y el primer
    request tarda ~50s en arrancar. Llamar a esto en cuanto el usuario
    inicia una busqueda da al relay una ventaja de arranque. Se auto-limita
    a 1 ping cada 60s para no saturar.
    """
    global _last_warm_ts
    now = time.time()
    if now - _last_warm_ts < 60:
        return
    _last_warm_ts = now
    base = _render_relay_url()
    if not base:
        return

    def _ping():
        try:
            requests.get(f"{base}/", timeout=60,
                         headers={"User-Agent": UA})
        except Exception:
            pass

    try:
        import threading
        threading.Thread(target=_ping, daemon=True).start()
    except Exception:
        pass


def relay_warmth(timeout=3.0):
    """Sondea el relay y de paso lo despierta. Devuelve:
      'warm' -> responde rapido (listo para usar)
      'cold' -> dormido/despertando (la propia peticion lo activa)
      'down' -> sin URL configurada o error de conexion inmediato
    """
    base = _render_relay_url()
    if not base:
        return "down"
    try:
        r = requests.get(f"{base}/", timeout=timeout,
                         headers={"User-Agent": UA})
        # 200 rapido = listo. Un Render dormido NO responde rapido con error:
        # la peticion se queda colgada hasta que arranca (-> Timeout) o da 200.
        # Por eso un non-200 rapido = URL mala/caida (down), no "despertando".
        return "warm" if r.status_code == 200 else "down"
    except requests.exceptions.Timeout:
        return "cold"   # dormido: la peticion ya ha iniciado el arranque
    except Exception:
        return "down"


def _render_fetch(url):
    """GET una URL DT via Render relay. Resuelve Anubis automaticamente."""
    base = _render_relay_url()
    if not base:
        return None
    try:
        r = requests.get(f"{base}/dtfetch", params={"u": url},
                         timeout=60, headers={"User-Agent": UA})
        if r.status_code == 200 and len(r.text) > 500 and not anubis.is_anubis(r.text):
            _LOG(f"render_fetch OK ({len(r.text)} bytes)")
            return r
    except Exception as e:
        _LOG(f"render_fetch error: {e.__class__.__name__}: {e}")
    return None


def detail(url):
    """Obtiene la ficha de detalle con sus enlaces de descarga.

    Devuelve dict con: title, plot, image, year, downloads.
    Cada download tiene: content_id, tabla, label, season, episode.
    """
    _LOG(f"detail: {url}")
    # Estrategia 0: Render relay (mismo motivo que en search)
    r = _render_fetch(url)
    if r is None:
        r = _proxy_get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    # Titulo
    title = None
    h1 = soup.select_one("h1.descargarTitulo, h1, h2.descargarTitulo")
    if h1:
        title = h1.get_text(" ", strip=True)

    # Sinopsis
    plot = None
    for p in soup.select("p.text-justify, p"):
        txt = p.get_text(" ", strip=True)
        if txt.lower().startswith("descripci") or txt.lower().startswith("sinopsis"):
            plot = re.sub(r"^[^:]+:\s*", "", txt)
            break
        if not plot and len(txt) > 140:
            plot = txt

    # Poster
    image = None
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        image = _upgrade_weserv(og["content"], width=500)

    # Anno
    year = None
    m = re.search(r"\b(19|20)\d{2}\b", soup.get_text(" ", strip=True))
    if m:
        year = m.group(0)

    # Descargas: <a class="protected-download" data-content-id data-tabla>
    downloads = []
    for a in soup.select("a.protected-download"):
        cid = a.get("data-content-id")
        tabla = a.get("data-tabla")
        if not cid or not tabla:
            continue
        label = ""
        quality = ""
        season = episode = None
        tr = a.find_parent("tr")
        if tr:
            tds = [t.get_text(" ", strip=True) for t in tr.find_all("td")]
            for t in tds:
                m2 = re.match(r"^(\d{1,2})\s*x\s*(\d{1,3})\b", t)
                if m2:
                    season, episode = int(m2.group(1)), int(m2.group(2))
                    label = f"{season:02d}x{episode:02d}"
                    extra = t[m2.end():].strip(" -()[]")
                    if extra:
                        label += f" {extra}"
                    break
            # Filtrar celdas inútiles: "descargar...por torrent", vacías, etc.
            useful = []
            for t in tds:
                tl = t.lower().strip()
                if not tl:
                    continue
                if "descargar" in tl and "torrent" in tl:
                    continue
                if tl == label.lower():
                    continue
                useful.append(t)
            # Extraer calidad de las celdas
            for t in useful:
                qm = _QUALITY_RE.search(t)
                if qm:
                    quality = qm.group(1).strip()
                    break
            if not quality:
                row_text = " ".join(useful)
                qm = re.search(
                    r"\b(4K|2160p|1080p|720p|HDRip|BluRay|BDRemux|BDRip|"
                    r"WEB-?DL|WEBRip|microHD|HDTV|DVDRip|Remux)\b",
                    row_text, re.I,
                )
                if qm:
                    quality = qm.group(1)
            if not label and useful:
                label = " · ".join(useful[:2])
            elif season is not None and useful:
                label = f"{label} - " + " · ".join(useful[:2])
        downloads.append({
            "content_id": cid,
            "tabla": tabla,
            "label": label,
            "quality": quality,
            "season": season,
            "episode": episode,
        })

    _LOG(f"detail -> {len(downloads)} downloads")
    return {
        "title": title,
        "plot": plot,
        "image": image,
        "year": year,
        "downloads": downloads,
    }


# ── Resolucion de torrent (PoW) ─────────────────────────────────────────

API = "/api_validate_pow.php"
DL_DIFFICULTY = 3


def _dl_pow(challenge):
    """Resuelve el PoW de descarga de DonTorrent."""
    target = "0" * DL_DIFFICULTY
    nonce = 0
    while True:
        h = hashlib.sha256((challenge + str(nonce)).encode()).hexdigest()
        if h.startswith(target):
            return nonce
        nonce += 1


def _render_resolve_torrent(content_id, tabla, domain="", timeout=8):
    """Resuelve PoW de descarga via Render relay."""
    base = _render_relay_url()
    if not base:
        return None
    try:
        r = requests.post(f"{base}/dtpow", json={
            "domain": domain,
            "content_id": int(content_id),
            "tabla": tabla,
        }, timeout=timeout, headers={"Content-Type": "application/json",
                                     "User-Agent": UA})
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and data.get("download_url"):
                _LOG(f"render_resolve_torrent OK ({data.get('elapsed', 0):.1f}s)")
                return data["download_url"]
        _LOG(f"render_resolve_torrent: HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        _LOG(f"render_resolve_torrent error: {e.__class__.__name__}: {e}")
    return None


def resolve_torrent(content_id, tabla, page_url=None, prefer_direct=False):
    """Devuelve la URL del .torrent, usando cache en disco si esta disponible.

    El .torrent es estatico, asi que un acierto de cache evita repetir el PoW
    por completo (reproduccion casi instantanea en re-visionados/reintentos).
    prefer_direct=True salta el relay (que ya no puede con el PoW desde su IP) e
    intenta directo desde el box, util para el catalogo (calidad/RAR)."""
    cached = _resolve_cache_get(content_id, tabla)
    if cached:
        _LOG(f"resolve_torrent: cache HIT {tabla}:{content_id}")
        return cached
    url = _resolve_torrent_uncached(content_id, tabla, page_url=page_url,
                                    prefer_direct=prefer_direct)
    if url:
        _resolve_cache_put(content_id, tabla, url)
    return url


def _resolve_torrent_uncached(content_id, tabla, page_url=None,
                              prefer_direct=False):
    """Ejecuta el handshake PoW para obtener la URL del .torrent.

    Estrategia 0: Render relay (Python sin limites de CPU).
    Estrategia 1+: directo / proxy.
    """
    host = resolve_domain()

    # Estrategia 0: Render relay (sin Anubis IP issues). Se salta con
    # prefer_direct (el PoW de descarga falla desde la IP de Render).
    if not prefer_direct:
        url = _render_resolve_torrent(content_id, tabla, domain=host)
        if url:
            return url

    api_url = f"https://{host}{API}"

    # Pre-flight: obtener cookies Anubis
    cached = _anubis_cookies_for(api_url)
    if not cached:
        cached = _ensure_anubis(api_url)

    # 1) Generar challenge (directo -> proxy)
    _LOG(f"resolve_torrent: generando challenge para {content_id}/{tabla}")
    try:
        res = _robust_post_json(api_url, {
            "action": "generate",
            "content_id": int(content_id),
            "tabla": tabla,
        })
    except Exception:
        # ULTIMO CARTUCHO: el relay OTRA VEZ, con mas paciencia. Cuando el ISP
        # resetea el DoH (WinError 10054 / errno 104) los tres caminos locales
        # mueren a la vez y el relay es el UNICO vivo -> si antes fallo pudo ser
        # solo porque estaba ARRANCANDO (Render free: frio ~50s) o reiniciandose
        # por un despliegue, y 8s no le daban. 30s si. Sin esto, un parpadeo del
        # relay = "no se puede reproducir" en la tele (visto en vivo 12-09-2026).
        if not prefer_direct:
            url = _render_resolve_torrent(content_id, tabla, domain=host,
                                          timeout=30)
            if url:
                _LOG("resolve_torrent: rescatado por el relay al 2o intento")
                return url
        raise
    if not res.get("success"):
        raise RuntimeError(res.get("error") or "PoW: sin challenge")
    challenge = res["challenge"]
    _LOG(f"resolve_torrent: challenge OK, resolviendo PoW...")

    # 2) Resolver PoW
    nonce = _dl_pow(challenge)
    _LOG(f"resolve_torrent: PoW resuelto, nonce={nonce}")

    # 3) Validar y obtener URL (directo -> proxy)
    res = _robust_post_json(api_url, {
        "action": "validate",
        "challenge": challenge,
        "nonce": nonce,
    })
    if res.get("status") == "captcha_required":
        raise RuntimeError("DonTorrent pide captcha. Espera unos minutos.")
    if not res.get("success") or not res.get("download_url"):
        raise RuntimeError(res.get("error") or "PoW: validacion fallida")

    url = res["download_url"]
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = f"https://{host}{url}"

    _LOG(f"resolve_torrent -> {url[:100]}")
    return url


def download_torrent(url):
    """Descarga los BYTES del .torrent (estatico) desde el BOX, por el camino que
    SI alcanza DonTorrent aunque el ISP bloquee el DNS y aunque el .torrent este
    tras un reto Anubis: DoH -> proxy (ambos resuelven Anubis). Devuelve bytes o
    b''. Necesario porque la IP de Render esta bloqueada por DonTorrent y un GET
    generico (sin Anubis) devolveria la pagina del reto, no el .torrent."""
    def _ok(data):
        return (bool(data) and len(data) > 100 and data[:1] == b"d"
                and b"<html" not in data[:300].lower())
    # 1) DoH: conexion residencial directa (IP resuelta por DoH) + Anubis
    try:
        r = _doh_fetch("GET", url, timeout=25)
        data = r.content if r is not None else b""
        if _ok(data):
            _LOG(f"download_torrent OK via DoH ({len(data)} bytes)")
            return data
        _LOG(f"download_torrent DoH: no parece .torrent (len={len(data)})")
    except Exception as e:
        _LOG(f"download_torrent DoH error: {e}")
    # 2) proxy (CF Worker, residencial) + Anubis
    try:
        r = _proxy_get(url, timeout=30)
        data = r.content if r is not None else b""
        if _ok(data):
            _LOG(f"download_torrent OK via proxy ({len(data)} bytes)")
            return data
        _LOG(f"download_torrent proxy: no parece .torrent (len={len(data)})")
    except Exception as e:
        _LOG(f"download_torrent proxy error: {e}")
    return b""
