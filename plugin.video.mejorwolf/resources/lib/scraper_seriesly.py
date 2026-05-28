"""
Scraper para Series.ly — streaming via hosters (Filemoon, VOE, Streamtape, etc.)

Autenticacion automatica: el addon pide email+contrasenya la primera vez,
luego reutiliza la sesion guardada y hace re-login automatico si caduca.

Flujo:
  1. Login via POST /ingresar (email + password + CSRF)
  2. Buscar via POST /api/search/posts  (JSON, requiere sesion)
  3. Listar episodios scrapeando /{category}/{slug}  (HTML)
  4. Extraer enlaces de streaming scrapeando /{category}/{slug}/{SxE}  (HTML)
  5. Resolver hoster a URL directa de video
"""

import re
import json
import requests
from urllib.parse import urljoin, quote as urlquote
from bs4 import BeautifulSoup

try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    _KODI = True
except ImportError:
    _KODI = False

SOURCE = "seriesly"
_BASE = "https://series.ly"


# ---------------------------------------------------------------------------
# Proxy support — el ISP español bloquea series.ly por SNI
# ---------------------------------------------------------------------------

# Cookie store for upstream cookies forwarded through the proxy.
# requests.Session stores cookies per-domain, but the proxy changes the
# domain from series.ly to the Worker URL. Cookies with Domain=.series.ly
# get stored for .series.ly and are never sent to the Worker URL.
# We manually capture Set-Cookie headers and inject them on every request.
_upstream_cookies = {}


def _store_upstream_cookies(response):
    """Extract Set-Cookie headers from a proxied response and store them.

    Through the proxy, response.cookies is empty because the Domain attribute
    doesn't match the proxy host.  And response.headers.items() combines all
    Set-Cookie headers into ONE string, losing individual cookies.  We use
    response.raw.headers.getlist() which preserves each Set-Cookie separately.
    """
    # Method 1 (reliable): urllib3 raw headers — each Set-Cookie is separate
    try:
        raw_cookies = response.raw.headers.getlist("Set-Cookie")
        for sc in raw_cookies:
            parts = sc.split(";")
            if "=" in parts[0]:
                cn, cv = parts[0].split("=", 1)
                cn, cv = cn.strip(), cv.strip()
                if cn and cv and "Max-Age=0" not in sc \
                        and "01 Jan 1970" not in sc:
                    _upstream_cookies[cn] = cv
    except Exception:
        # Fallback: iterate response.headers (may miss cookies)
        for key, value in response.headers.items():
            if key.lower() == "set-cookie":
                parts = value.split(";")
                if "=" in parts[0]:
                    cn, cv = parts[0].split("=", 1)
                    cn, cv = cn.strip(), cv.strip()
                    if cn and cv and "Max-Age=0" not in value \
                            and "01 Jan 1970" not in value:
                        _upstream_cookies[cn] = cv
    # Also grab from response.cookies (requests built-in jar)
    for cn, cv in response.cookies.items():
        if cn and cv:
            _upstream_cookies[cn] = cv


def _inject_upstream_cookies(kwargs):
    """Inject stored upstream cookies into the request's headers dict."""
    if not _upstream_cookies:
        return
    hdrs = kwargs.get("headers")
    if hdrs is None:
        hdrs = {}
        kwargs["headers"] = hdrs
    # Merge with any existing Cookie header
    existing = {}
    raw = hdrs.get("Cookie", "")
    if raw:
        for pair in raw.split(";"):
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                existing[k.strip()] = v.strip()
    existing.update(_upstream_cookies)
    hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in existing.items())


def _proxy_base():
    """Return the Cloudflare Worker URL from settings, or None."""
    if not _KODI:
        return None
    a = _addon()
    if not a:
        return None
    raw = (a.getSetting("proxy_url") or "").strip().rstrip("/")
    return raw or None


def _proxy_force():
    if not _KODI:
        return False
    a = _addon()
    if not a:
        return False
    return (a.getSetting("proxy_force") or "").lower() == "true"


def _proxied_get(session, url, **kwargs):
    """GET a traves del proxy si esta configurado, sino directo."""
    base = _proxy_base()
    if base and _proxy_force():
        _inject_upstream_cookies(kwargs)
        proxied_url = f"{base}/?u={urlquote(url, safe='')}"
        kwargs.setdefault("timeout", 30)
        kwargs.pop("allow_redirects", None)
        r = session.get(proxied_url, allow_redirects=True, **kwargs)
        _store_upstream_cookies(r)
        return r
    kwargs.setdefault("timeout", 15)
    return session.get(url, **kwargs)


def _proxied_post(session, url, **kwargs):
    """POST a traves del proxy si esta configurado, sino directo."""
    base = _proxy_base()
    if base and _proxy_force():
        _inject_upstream_cookies(kwargs)
        proxied_url = f"{base}/?u={urlquote(url, safe='')}"
        kwargs.setdefault("timeout", 30)
        kwargs.pop("allow_redirects", None)
        r = session.post(proxied_url, allow_redirects=True, **kwargs)
        _store_upstream_cookies(r)
        return r
    kwargs.setdefault("timeout", 15)
    return session.post(url, **kwargs)

# Sesion global reutilizable (persiste entre llamadas dentro de una ejecucion)
_session_obj = None
_session_ok = False


def _log(msg):
    if _KODI:
        xbmc.log(f"[MejorWolf/SLY] {msg}", xbmc.LOGINFO)


def _addon():
    return xbmcaddon.Addon() if _KODI else None


# ---------------------------------------------------------------------------
# Session / Auth
# ---------------------------------------------------------------------------

def _get_credentials():
    """Lee email y password de los settings del addon."""
    a = _addon()
    if not a:
        return "", ""
    email = (a.getSetting("seriesly_email") or "").strip()
    password = (a.getSetting("seriesly_password") or "").strip()
    return email, password


def _save_credentials(email, password):
    """Guarda email y password en los settings del addon."""
    a = _addon()
    if a:
        a.setSetting("seriesly_email", email)
        a.setSetting("seriesly_password", password)


def _new_session():
    """Crea una requests.Session limpia."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
        "Referer": _BASE + "/",
    })
    return s


def _get_csrf(session):
    """Obtiene CSRF token de la pagina de login o cualquier pagina HTML."""
    try:
        # Sanctum CSRF cookie
        _proxied_get(session, _BASE + "/sanctum/csrf-cookie")
    except Exception:
        pass
    # CSRF meta tag de una pagina HTML
    try:
        r = _proxied_get(session, _BASE + "/")
        m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r.text)
        if m:
            _log(f"CSRF token obtenido de meta tag")
            return m.group(1)
    except Exception as e:
        _log(f"Error obteniendo CSRF: {e}")
    # XSRF-TOKEN cookie (decodificado) — check both session jar and our
    # upstream cookie store (proxy changes the domain so session jar may
    # not have it).
    token = session.cookies.get("XSRF-TOKEN", "")
    if not token:
        token = _upstream_cookies.get("XSRF-TOKEN", "")
    if token:
        from urllib.parse import unquote
        _log(f"CSRF token obtenido de cookie XSRF-TOKEN")
        return unquote(token)
    return ""


def login_with_cookie(session_cookie):
    """Login usando cookie de sesion copiada del navegador.

    Series.ly usa Cloudflare Turnstile en el formulario de login, lo que
    impide login automatizado. El usuario debe:
    1. Hacer login en series.ly en su navegador
    2. Copiar el valor de la cookie 'seriesly_session'
    3. Pegarlo en los ajustes del addon

    Devuelve (session, ok, error_msg).
    """
    global _session_obj, _session_ok, _upstream_cookies

    _upstream_cookies = {}
    s = _new_session()

    # Inyectar la cookie de sesion en AMBOS sitios:
    # 1. _upstream_cookies  → para requests a traves del proxy (Cloudflare Worker)
    # 2. s.cookies          → para requests directas a series.ly
    cookie_val = session_cookie.strip()
    _upstream_cookies["seriesly_session"] = cookie_val
    s.cookies.set("seriesly_session", cookie_val, domain=".series.ly", path="/")
    _log("login_with_cookie: intentando con cookie de sesion")

    # Verificar que la sesion es valida: obtener CSRF y hacer una peticion API
    try:
        csrf = _get_csrf(s)
        if not csrf:
            return s, False, "No se pudo obtener CSRF token con la cookie"

        _log(f"login_with_cookie: CSRF={csrf[:20]}... "
             f"upstream_cookies={list(_upstream_cookies.keys())} "
             f"session_cookies={[c.name for c in s.cookies]}")
        test = _proxied_post(
            s,
            _BASE + "/api/search/posts",
            json={"query": "test"},
            headers={
                "Accept": "application/json",
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        _log(f"login_with_cookie verify: status={test.status_code} "
             f"body={test.text[:200]}")

        if test.status_code == 200:
            _session_obj = s
            _session_ok = True
            _log("login_with_cookie: sesion valida!")
            return s, True, ""
        elif test.status_code in (401, 419):
            return s, False, "Cookie de sesion expirada o invalida"
        else:
            return s, False, f"API devuelve {test.status_code}"
    except Exception as e:
        _log(f"login_with_cookie error: {e}")
        return s, False, f"Error verificando sesion: {e}"


def login(email, password):
    """Intenta login con email+password. Devuelve (session, ok, error_msg).

    NOTA: Series.ly ahora usa Cloudflare Turnstile (captcha) que bloquea
    login automatizado. Este metodo probablemente fallara. Usar
    login_with_cookie() en su lugar.
    """
    global _session_obj, _session_ok, _upstream_cookies

    _upstream_cookies = {}  # Clear stale cookies before fresh login
    s = _new_session()
    csrf = _get_csrf(s)
    if not csrf:
        return s, False, "No se pudo obtener CSRF token"

    _log(f"Pre-login cookies: {list(_upstream_cookies.keys())}")

    try:
        r = _proxied_post(
            s,
            _BASE + "/ingresar",
            data={
                "email": email,
                "password": password,
                "_token": csrf,
                "remember": "1",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/html,application/xhtml+xml",
                "Referer": _BASE + "/ingresar",
                "Origin": _BASE,
            },
        )
        final_url = r.headers.get("x-mw-relay-final", r.url)
        _log(f"Login POST status: {r.status_code}, final_url: {final_url}")
        _log(f"Post-login cookies: {list(_upstream_cookies.keys())}")
    except Exception as e:
        _log(f"Login POST error: {e}")
        return s, False, f"Error de conexion: {e}"

    # Verificar login exitoso
    still_on_login = "/ingresar" in final_url and "mw-relay" not in final_url
    has_ingresar_title = bool(re.search(
        r'<title>[^<]*[Ii]ngresar[^<]*</title>', r.text[:2000]
    ))

    if r.status_code == 200 and (still_on_login or has_ingresar_title):
        _log(f"Login failed: still_on_login={still_on_login}, "
             f"has_ingresar_title={has_ingresar_title}")
        # Series.ly usa Cloudflare Turnstile - el login por form no funciona
        if "turnstile" in r.text.lower():
            return s, False, ("Series.ly usa captcha (Turnstile). "
                              "Usa 'Login con cookie' en su lugar.")
        return s, False, "Login fallido (verifica email y contrasenya)"

    # Verificar sesion funcional
    try:
        csrf2 = _get_csrf(s)
        test = _proxied_post(
            s,
            _BASE + "/api/search/posts",
            json={"query": "test"},
            headers={
                "Accept": "application/json",
                "X-CSRF-TOKEN": csrf2,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        _log(f"Login verify status: {test.status_code}")
        if test.status_code == 200:
            _session_obj = s
            _session_ok = True
            _log("Login exitoso")
            return s, True, ""
        else:
            return s, False, f"Login parece OK pero API devuelve {test.status_code}"
    except Exception as e:
        _log(f"Login verify error: {e}")
        return s, False, f"Error verificando sesion: {e}"


def _get_session_cookie():
    """Lee la cookie de sesion de varias fuentes, en orden de prioridad:
    1. Settings locales del addon (pegada manualmente)
    2. Supabase (subida desde PC via sync_sly_cookie.py)
    """
    # 1) Settings locales
    a = _addon()
    if a:
        local = (a.getSetting("seriesly_session_cookie") or "").strip()
        if local:
            return local

    # 2) Supabase (cookie sincronizada desde PC)
    try:
        from . import supabase_sync as sb
        remote = sb.get_seriesly_cookie()
        if remote:
            _log("Usando cookie Series.ly de Supabase")
            return remote
    except Exception as e:
        _log(f"Error leyendo cookie de Supabase: {e}")

    return ""


def _ensure_session():
    """Asegura que tenemos una sesion autenticada.

    Prioridad:
    1. Sesion activa en memoria
    2. Cookie de sesion (local o Supabase)
    3. Login con email+password (fallback, falla por Turnstile)

    Devuelve (session, csrf) o lanza excepcion si no hay credenciales.
    """
    global _session_obj, _session_ok

    # Si ya tenemos sesion, verificar que sigue activa
    if _session_obj and _session_ok:
        csrf = _get_csrf(_session_obj)
        try:
            r = _proxied_post(
                _session_obj,
                _BASE + "/api/search/posts",
                json={"query": "test"},
                headers={
                    "Accept": "application/json",
                    "X-CSRF-TOKEN": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            if r.status_code == 200:
                return _session_obj, csrf
        except Exception:
            pass
        _log("Sesion expirada, intentando re-login...")
        _session_ok = False

    # 1) Intentar cookie de sesion (local o Supabase)
    session_cookie = _get_session_cookie()
    if session_cookie:
        s, ok, err = login_with_cookie(session_cookie)
        if ok:
            csrf = _get_csrf(s)
            return s, csrf
        _log(f"Cookie fallida: {err}")

    # 2) En Android: intentar WebView login automatico (Turnstile funciona
    #    en WebView nativo porque usamos loadDataWithBaseURL con series.ly)
    try:
        from . import webview_login as wvl
        if wvl.is_available():
            _log("Intentando WebView login automatico...")
            ok = wvl.show_webview_login()
            if ok:
                # El WebView guardo la cookie en Supabase -> leerla
                import time as _time
                _time.sleep(2)  # Dar tiempo a Supabase
                session_cookie = _get_session_cookie()
                if session_cookie:
                    s, ok2, err2 = login_with_cookie(session_cookie)
                    if ok2:
                        csrf = _get_csrf(s)
                        return s, csrf
                    _log(f"WebView login OK pero cookie fallo: {err2}")
            else:
                _log("WebView login cancelado/fallido")
    except Exception as e:
        _log(f"WebView login error: {e}")

    # 3) Intentar login con email+password (probablemente falla por Turnstile)
    email, password = _get_credentials()
    if email and password:
        s, ok, err = login(email, password)
        if ok:
            csrf = _get_csrf(s)
            return s, csrf
        _log(f"Login email/pass fallido: {err}")

    raise AuthRequired(
        "Se requiere login en Series.ly.\n"
        "Usa el WebView de Android o escanea el QR desde tu movil."
    )


class AuthRequired(Exception):
    """El usuario necesita proporcionar credenciales."""
    pass


def prompt_login():
    """Muestra dialogo de login en Kodi. Devuelve True si login OK.

    Series.ly usa Cloudflare Turnstile, por lo que el login con
    email+password falla. El metodo principal es pegar la cookie
    'seriesly_session' obtenida manualmente del navegador.
    """
    if not _KODI:
        return False

    dlg = xbmcgui.Dialog()

    # Ofrecer metodos de login
    choice = dlg.select("Series.ly - Login", [
        "Login con cookie de sesion (recomendado)",
        "Login con email y contrasenya",
    ])

    if choice == 0:
        return _prompt_cookie_login(dlg)
    elif choice == 1:
        return _prompt_email_login(dlg)
    return False


def _prompt_cookie_login(dlg):
    """Login pegando la cookie seriesly_session del navegador."""
    dlg.ok(
        "Series.ly - Instrucciones",
        "1. Abre series.ly en tu navegador\n"
        "2. Inicia sesion normalmente\n"
        "3. Pulsa F12 > Application > Cookies\n"
        "4. Copia el valor de 'seriesly_session'\n"
        "5. Pegalo en el siguiente dialogo"
    )

    cookie = dlg.input(
        "Pega la cookie 'seriesly_session'",
        type=xbmcgui.INPUT_ALPHANUM,
    )
    if not cookie:
        return False

    progress = xbmcgui.DialogProgressBG()
    progress.create("Series.ly", "Verificando cookie de sesion...")

    s, ok, err = login_with_cookie(cookie)

    try:
        progress.close()
    except Exception:
        pass

    if ok:
        # Guardar la cookie en settings
        a = _addon()
        if a:
            a.setSetting("seriesly_session_cookie", cookie.strip())
        dlg.notification(
            "Series.ly", "Sesion iniciada correctamente",
            xbmcgui.NOTIFICATION_INFO, 3000,
        )
        return True
    else:
        dlg.ok("Series.ly - Error", err)
        return False


def _prompt_email_login(dlg):
    """Login con email y contrasenya (puede fallar por Turnstile)."""
    email, password = _get_credentials()

    if not email:
        email = dlg.input(
            "Series.ly - Email o usuario",
            type=xbmcgui.INPUT_ALPHANUM,
        )
        if not email:
            return False

    if not password:
        password = dlg.input(
            "Series.ly - Contrasenya",
            type=xbmcgui.INPUT_ALPHANUM,
            option=xbmcgui.ALPHANUM_HIDE_INPUT,
        )
        if not password:
            return False

    progress = xbmcgui.DialogProgressBG()
    progress.create("Series.ly", "Iniciando sesion...")

    s, ok, err = login(email, password)

    try:
        progress.close()
    except Exception:
        pass

    if ok:
        _save_credentials(email, password)
        dlg.notification(
            "Series.ly", "Sesion iniciada correctamente",
            xbmcgui.NOTIFICATION_INFO, 3000,
        )
        return True
    else:
        dlg.ok("Series.ly - Error", err)
        return False


def is_logged_in():
    """Comprueba si hay credenciales o cookie de sesion guardadas."""
    session_cookie = _get_session_cookie()
    if session_cookie:
        return True
    email, password = _get_credentials()
    return bool(email and password)


def auto_login():
    """Login silencioso — sin dialogos. Para Android TV Boxes.

    Intenta _ensure_session() (cookie local → Supabase → email/pass).
    Devuelve True si la sesion esta activa, False si falla.
    No muestra nada al usuario salvo un error critico.
    """
    try:
        _ensure_session()
        return True
    except AuthRequired:
        _log("auto_login: no hay cookie ni credenciales validas")
        return False
    except Exception as e:
        _log(f"auto_login: error inesperado: {e}")
        return False


# ---------------------------------------------------------------------------
# Browse catalog by category (peliculas, series, animes)
# ---------------------------------------------------------------------------

def browse_category(category, page=1):
    """Navega el catalogo de Series.ly por categoria.

    category: 'peliculas', 'series', 'animes', 'estrenos'
    page: numero de pagina (1-based)

    Devuelve (lista_items, has_next_page)
    Cada item: {title, slug, category, url, image, year, type, source}
    """
    sess, _ = _ensure_session()

    # "estrenos" → pagina principal de Series.ly (contenido reciente)
    if category == "estrenos":
        url = _BASE + "/"
        if page > 1:
            url = f"{_BASE}/?page={page}"
    else:
        url = f"{_BASE}/{category}"
        if page > 1:
            url += f"?page={page}"

    try:
        r = _proxied_get(sess, url)
        if r.status_code != 200:
            _log(f"browse_category HTTP {r.status_code} for {category} page {page}")
            return [], False
    except AuthRequired:
        raise
    except Exception as e:
        _log(f"Error browse_category: {e}")
        return [], False

    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    # Series.ly muestra cards con enlaces a /{cat}/{slug}
    # Para "estrenos" (homepage) aceptamos cualquier categoria conocida
    if category == "estrenos":
        cat_re = re.compile(r'/(peliculas|series|animes)/([^/?\s"]+)', re.I)
    else:
        cat_re = re.compile(rf'/{re.escape(category)}/([^/?\s"]+)', re.I)

    seen_slugs = set()

    # Buscar cards/items — Series.ly usa diferentes estructuras HTML
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        m = cat_re.search(href)
        if not m:
            continue

        if category == "estrenos":
            item_cat = m.group(1).lower()
            slug = m.group(2)
        else:
            item_cat = category
            slug = m.group(1)

        # Filtrar slugs invalidos (paginas, assets, etc.)
        if slug in seen_slugs or slug in ("page", "create", "edit"):
            continue
        # Evitar enlaces de episodios (contienen patron NxN)
        if re.search(r'/\d+x\d+', href):
            continue
        seen_slugs.add(slug)

        # Extraer titulo
        title_text = ""
        # Buscar titulo dentro del enlace o su contenedor
        title_el = a_tag.find(["h2", "h3", "h4", "h5", "span", "p"])
        if title_el:
            title_text = title_el.get_text(strip=True)
        if not title_text:
            title_text = a_tag.get("title", "") or a_tag.get_text(strip=True)
        if not title_text:
            # Intentar el contenedor padre
            parent = a_tag.find_parent(["div", "article", "li"])
            if parent:
                heading = parent.find(["h2", "h3", "h4", "h5"])
                if heading:
                    title_text = heading.get_text(strip=True)
        if not title_text:
            title_text = slug.replace("-", " ").title()

        # Extraer imagen
        image = ""
        img_tag = a_tag.find("img")
        if not img_tag:
            parent = a_tag.find_parent(["div", "article", "li"])
            if parent:
                img_tag = parent.find("img")
        if img_tag:
            image = img_tag.get("src", "") or img_tag.get("data-src", "")
            if image and image.startswith("/"):
                image = _BASE + image

        # Extraer año si visible
        year = ""
        parent = a_tag.find_parent(["div", "article", "li"])
        if parent:
            year_match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', parent.get_text())
            if year_match:
                year = year_match.group(1)

        item_url = href if href.startswith("http") else _BASE + href
        item_type = "pelicula" if item_cat == "peliculas" else (
            "anime" if item_cat == "animes" else "serie"
        )

        items.append({
            "title": title_text,
            "slug": slug,
            "category": item_cat,
            "url": item_url,
            "image": image,
            "year": year,
            "type": item_type,
            "source": SOURCE,
        })

    # Detectar si hay pagina siguiente
    has_next = False
    # Series.ly usa paginacion Laravel con ?page=N
    next_page = page + 1
    if soup.find("a", href=re.compile(rf'[?&]page={next_page}\b')):
        has_next = True
    # Alternativa: boton "Siguiente" o "Next"
    for a_tag in soup.find_all("a", href=True):
        if re.search(r'(?:next|siguiente|»)', a_tag.get_text(strip=True), re.I):
            has_next = True
            break

    _log(f"browse_category {category} page {page}: {len(items)} items, has_next={has_next}")
    return items, has_next


# ---------------------------------------------------------------------------
# Hoster URL resolvers
# ---------------------------------------------------------------------------

_HOSTER_RESOLVERS = {
    "filemoon": re.compile(r"file\s*:\s*[\"']([^\"']+\.m3u8[^\"']*)", re.I),
    "streamtape": re.compile(r"document\.getElementById\('robotlink'\)\.innerHTML\s*=\s*['\"]([^'\"]+)", re.I),
    "voe": re.compile(r"'hls'\s*:\s*'([^']+)'|source\s*=\s*['\"]([^'\"]+\.m3u8)", re.I),
    "mixdrop": re.compile(r"MDCore\.wurl\s*=\s*[\"']([^\"']+)", re.I),
    "streamwish": re.compile(r"file\s*:\s*[\"']([^\"']+\.m3u8[^\"']*)", re.I),
    "vidmoly": re.compile(r"file\s*:\s*[\"']([^\"']+\.m3u8[^\"']*)", re.I),
    "lulustream": re.compile(r"file\s*:\s*[\"']([^\"']+\.m3u8[^\"']*)", re.I),
    "netu": re.compile(r"var\s+player\s*=.*?file\s*:\s*[\"']([^\"']+)", re.I | re.S),
    "powvideo": re.compile(r"file\s*:\s*[\"']([^\"']+\.m3u8[^\"']*)", re.I),
    "doodstream": re.compile(r"file\s*:\s*[\"']([^\"']+\.m3u8[^\"']*)", re.I),
}


def resolve_hoster_url(page_url):
    """Intenta resolver una pagina de hoster a una URL directa de video."""
    try:
        r = requests.get(page_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Referer": _BASE + "/",
        })
        if r.status_code != 200:
            return None

        html = r.text
        host = re.search(r'https?://([^/]+)', page_url)
        hostname = host.group(1).lower() if host else ""

        # Identificar hoster por nombre
        for name, pattern in _HOSTER_RESOLVERS.items():
            if name in hostname:
                m = pattern.search(html)
                if m:
                    url = m.group(1) or (m.group(2) if m.lastindex and m.lastindex >= 2 else None)
                    if url:
                        if url.startswith("//"):
                            url = "https:" + url
                        return url

        # Fallback generico: buscar .m3u8 o .mp4
        m = re.search(
            r'(?:file|src|source)\s*[:=]\s*["\']?(https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*)',
            html, re.I,
        )
        if m:
            return m.group(1)

    except Exception as e:
        _log(f"Error resolviendo hoster {page_url}: {e}")
    return None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query):
    """Busca en series.ly. Requiere sesion activa.

    Cada resultado: {
        'title', 'url', 'slug', 'type', 'category', 'image',
        'year', 'rating', 'source'
    }
    """
    sess, csrf = _ensure_session()

    try:
        r = _proxied_post(
            sess,
            _BASE + "/api/search/posts",
            json={"query": query},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CSRF-TOKEN": csrf,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        if r.status_code != 200:
            _log(f"Search API HTTP {r.status_code}")
            return []

        data = r.json()
    except AuthRequired:
        raise
    except Exception as e:
        _log(f"Error search API: {e}")
        return []

    posts = data.get("posts", [])
    items = []
    for p in posts:
        slug = p.get("slug", "")
        ptype = (p.get("type") or "").lower()
        category = p.get("category", "")

        if not category:
            if ptype in ("serie", "series"):
                category = "series"
            elif ptype in ("pelicula", "peliculas", "movie"):
                category = "peliculas"
            elif ptype in ("anime", "animes"):
                category = "animes"
            else:
                category = "peliculas"

        url = p.get("link") or f"{_BASE}/{category}/{slug}"
        poster = p.get("poster", "")

        items.append({
            "title": p.get("title", slug),
            "url": url,
            "slug": slug,
            "type": ptype,
            "category": category,
            "image": poster,
            "year": p.get("item_date", "")[:4] if p.get("item_date") else "",
            "rating": p.get("vote_average", 0),
            "source": SOURCE,
        })

    _log(f"Search '{query}': {len(items)} resultados")
    return items


# ---------------------------------------------------------------------------
# Episodes listing
# ---------------------------------------------------------------------------

def list_episodes(category, slug, season=None):
    """Lista episodios de una serie/anime."""
    sess, _ = _ensure_session()
    url = f"{_BASE}/{category}/{slug}"

    try:
        r = _proxied_get(sess, url)
        if r.status_code != 200:
            _log(f"list_episodes HTTP {r.status_code}")
            return []
    except AuthRequired:
        raise
    except Exception as e:
        _log(f"Error list_episodes: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    episodes = []

    ep_re = re.compile(
        rf'/{re.escape(category)}/{re.escape(slug)}/(\d+)x(\d+)', re.I,
    )
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = ep_re.search(href)
        if not m:
            continue

        s_num = int(m.group(1))
        e_num = int(m.group(2))
        key = (s_num, e_num)
        if key in seen:
            continue
        seen.add(key)

        if season is not None and s_num != season:
            continue

        title_text = a.get_text(strip=True)
        if not title_text or len(title_text) < 3:
            parent = a.find_parent("div") or a.find_parent("li")
            if parent:
                title_text = parent.get_text(" ", strip=True)[:100]

        ep_url = href if href.startswith("http") else _BASE + href

        episodes.append({
            "title": title_text or f"Episodio {s_num}x{e_num:02d}",
            "url": ep_url,
            "season": s_num,
            "episode": e_num,
            "image": "",
            "description": "",
        })

    episodes.sort(key=lambda e: (e["season"], e["episode"]))
    _log(f"list_episodes {category}/{slug}: {len(episodes)} episodios")
    return episodes


def list_seasons(category, slug):
    """Devuelve lista de numeros de temporada disponibles."""
    sess, _ = _ensure_session()
    url = f"{_BASE}/{category}/{slug}"

    try:
        r = _proxied_get(sess, url)
        if r.status_code != 200:
            return []
    except AuthRequired:
        raise
    except Exception:
        return []

    seasons = set()
    for m in re.finditer(r'changeSeason\((\d+)\)', r.text):
        seasons.add(int(m.group(1)))
    for m in re.finditer(r'[Tt]emporada\s+(\d+)', r.text):
        seasons.add(int(m.group(1)))

    return sorted(seasons)


# ---------------------------------------------------------------------------
# Links extraction
# ---------------------------------------------------------------------------

def get_links(category, slug, episode_code=None):
    """Extrae enlaces de streaming/descarga.

    episode_code: "1x1", "2x5", etc. None para peliculas.
    """
    sess, _ = _ensure_session()
    path = f"/{category}/{slug}"
    if episode_code:
        path += f"/{episode_code}"
    url = _BASE + path

    try:
        r = _proxied_get(sess, url)
        if r.status_code != 200:
            _log(f"get_links HTTP {r.status_code} for {path}")
            return []
    except AuthRequired:
        raise
    except Exception as e:
        _log(f"Error get_links: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    links = []

    for row in soup.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        server = cells[0].get_text(strip=True).upper()
        quality = cells[1].get_text(strip=True)

        lang_cell = cells[2]
        lang_img = lang_cell.find("img")
        language = ""
        if lang_img:
            language = lang_img.get("alt", "") or lang_img.get("title", "")
        if not language:
            language = lang_cell.get_text(strip=True)

        btn = row.find("a", href=True)
        link_url = btn["href"] if btn else ""

        if not link_url:
            wire_btn = row.find(attrs={"wire:click": True})
            if wire_btn:
                wc = wire_btn.get("wire:click", "")
                m = re.search(r"['\"]?(https?://[^'\"]+)['\"]?", wc)
                if m:
                    link_url = m.group(1)

        if not link_url or not server:
            continue

        if link_url.startswith("/"):
            link_url = _BASE + link_url

        link_type = "online"
        if any(kw in server.lower() for kw in ("krakenfiles", "katfile", "nitroflare")):
            link_type = "download"

        if not language:
            language = "Desconocido"
        lang_lower = language.lower()
        if "espa" in lang_lower and ("lat" in lang_lower or "mx" in lang_lower):
            language = "Latino"
        elif "espa" in lang_lower:
            language = "Castellano"
        elif "sub" in lang_lower:
            language = "Subtitulado"

        links.append({
            "server": server,
            "quality": quality,
            "language": language,
            "url": link_url,
            "type": link_type,
        })

    _log(f"get_links {path}: {len(links)} enlaces")
    return links
