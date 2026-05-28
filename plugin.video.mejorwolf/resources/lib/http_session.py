"""HTTP session module for MejorWolf.

Real browser headers, persistent cookies, retries on transient errors,
SSL fallback to verify=False if Kodi's cert bundle is stale, AND - the
key for Spanish ISPs that DNS-block torrent sites - automatic retry via
DNS-over-HTTPS + IP pinning when a name fails to resolve or the
connection is reset.

Public API:
    make_session(base_url) -> requests.Session
    get(session, url, ...) -> response (with DoH fallback)
    post(session, url, ...) -> response (with DoH fallback)
    follow_redirect(url) -> final torrent/magnet URL
    diagnose(url) -> (status, bytes, final_url, blocked_msg)
"""

import re
import socket
from urllib.parse import urlparse, urlunparse, quote as urlquote

import requests
import urllib3
import xbmcaddon
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util.retry import Retry

from . import dns_doh

_ADDON = xbmcaddon.Addon()


class CloudflareChallengeError(requests.exceptions.HTTPError):
    """Raised when Cloudflare serves a 'Just a moment...' challenge page.

    These challenges require JavaScript execution in a real browser, so
    they cannot be bypassed via proxy or Python.
    """
    pass


def _proxy_base():
    """Return the Cloudflare Worker URL configured in settings, or None."""
    raw = (_ADDON.getSetting("proxy_url") or "").strip().rstrip("/")
    return raw or None


def _proxy_force():
    return (_ADDON.getSetting("proxy_force") or "").lower() == "true"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "DNT": "1",
}


# ---------------------------------------------------------------- DNS pinning

class _DNSPinPoolManager(PoolManager):
    """PoolManager that resolves the URL hostname to a fixed IP while
    keeping SNI/Host = original hostname (TLS still works against the
    site's real cert)."""

    def __init__(self, host_to_ip, **kw):
        self._host_to_ip = host_to_ip
        super().__init__(**kw)

    def _new_pool(self, scheme, host, port=None, request_context=None):
        ip = self._host_to_ip.get(host)
        if ip:
            # Force connection to IP, but tell TLS the real hostname.
            kw = dict(self.connection_pool_kw)
            kw["server_hostname"] = host
            kw["assert_hostname"] = host
            pool_cls = self.pool_classes_by_scheme[scheme]
            return pool_cls(ip, port=port, **kw)
        return super()._new_pool(scheme, host, port, request_context)


class _DNSPinAdapter(HTTPAdapter):
    """Adapter that uses a hostname->IP map for connection target."""

    def __init__(self, host_to_ip, **kw):
        self._host_to_ip = host_to_ip
        super().__init__(**kw)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = _DNSPinPoolManager(
            self._host_to_ip,
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )

    def send(self, request, **kw):
        # Make absolutely sure the Host header is the original hostname
        # (Connection-Pool already gets the IP via _new_pool above).
        host = urlparse(request.url).hostname
        if host:
            request.headers["Host"] = host
        return super().send(request, **kw)


# ----------------------------------------------------------------- session

def _build_session(base_url="", verify=True):
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    if base_url:
        sess.headers["Referer"] = base_url.rstrip("/") + "/"
    sess.verify = verify

    retry = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "HEAD"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def make_session(base_url=""):
    return _build_session(base_url, verify=True)


# ------------------------------------------------------ DoH fallback wrapper

def _is_dns_error(exc):
    msg = (str(exc) or "").lower()
    return (
        isinstance(exc, (requests.exceptions.ConnectionError,
                         requests.exceptions.ConnectTimeout,
                         socket.gaierror))
        or "name or service not known" in msg
        or "nodename nor servname" in msg
        or "getaddrinfo failed" in msg
        or "name resolution" in msg
        or "connection reset" in msg
        or "connection refused" in msg
        or "no route to host" in msg
        or "max retries exceeded" in msg
    )


def _retry_with_doh(method, session, url, **kwargs):
    """Resolve URL host via DoH and retry the request pinning that IP.

    Returns the response or re-raises if DoH yields no IPs.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise requests.exceptions.ConnectionError("No hostname in URL")

    ips = dns_doh.resolve(host)
    if not ips:
        raise requests.exceptions.ConnectionError(
            f"DoH no IPs for {host}"
        )

    # Build a one-shot session with a DNS-pinning adapter for this host.
    # We try each IP in order until one works.
    last_exc = None
    for ip in ips:
        pinned = requests.Session()
        pinned.headers.update(session.headers)
        pinned.cookies = session.cookies
        pinned.verify = False  # cert may not match IP; SNI still set
        adapter = _DNSPinAdapter(
            {host: ip},
            max_retries=Retry(
                total=1, backoff_factor=0.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET", "POST", "HEAD"),
                raise_on_status=False,
            ),
        )
        pinned.mount("https://", adapter)
        pinned.mount("http://", adapter)
        kwargs.setdefault("timeout", 20)
        kwargs.setdefault("allow_redirects", True)
        try:
            r = pinned.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            continue
    raise last_exc or requests.exceptions.ConnectionError(
        f"All DoH IPs failed for {host}"
    )


def _via_proxy(method, url, session=None, **kwargs):
    """Route the request through the configured Cloudflare Worker.

    IMPORTANT: any `params=` kwarg must be folded into the target URL BEFORE
    encoding, otherwise requests would append them to the worker URL (after
    `?u=`) and the worker would never see them. That broke search: callers
    do `hs.get(url, params={"q": "the lord of the rings"})` and the upstream
    received `/busqueda` with no query string -> empty results.

    If `session` is provided, its cookies are forwarded to the upstream
    site (the worker reads our Cookie header and sends it along) and any
    Set-Cookie returned by the upstream is stored back in the session.
    This is required for stateful flows like WolfMax4K's CSRF-protected
    POST search (GET homepage -> token + cookie -> POST /buscar).
    """
    base = _proxy_base()
    if not base:
        raise requests.exceptions.ConnectionError("proxy_url not configured")

    extra = kwargs.pop("params", None)
    if extra:
        from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
        if isinstance(extra, dict):
            extra_pairs = list(extra.items())
        else:
            extra_pairs = list(extra)
        p = urlparse(url)
        merged = parse_qsl(p.query, keep_blank_values=True) + [
            (k, v) for k, v in extra_pairs if v is not None
        ]
        url = urlunparse(p._replace(query=urlencode(merged, doseq=True)))

    proxied = f"{base}/?u={urlquote(url, safe='')}"

    # Build the cookie header from the caller's session. We MUST include
    # cookies that were stored under the proxy's hostname too: when the
    # upstream sets `Set-Cookie: PHPSESSID=...; Path=/` with no Domain
    # attribute, the cookie jar scopes it to whichever host served the
    # response — i.e. the worker, not the upstream. So we forward every
    # cookie name/value pair from the session and let the upstream pick
    # what it understands.
    cookie_header = ""
    if session is not None and session.cookies:
        try:
            seen = {}
            for c in session.cookies:
                if c.value and c.name not in seen:
                    seen[c.name] = c.value
            cookie_header = "; ".join(f"{k}={v}" for k, v in seen.items())
        except Exception:
            cookie_header = ""

    sess = requests.Session()
    sess.headers.update(_HEADERS)
    # IMPORTANT: ask Cloudflare for an uncompressed body. Otherwise CF's
    # edge re-gzips the worker response on the wire and (in some Kodi
    # builds of urllib3) decompression silently fails, leaving us with
    # a tiny gzipped blob that BeautifulSoup parses as 0 items.
    sess.headers["Accept-Encoding"] = "identity"
    if cookie_header:
        sess.headers["Cookie"] = cookie_header
    sess.verify = True
    # Heavy listing pages on WolfMax (full /series index) can take 30s+ to
    # render upstream; bump the proxy timeout so we don't fail with
    # ReadTimeout on the slow-but-correct path.
    kwargs.setdefault("timeout", 60)
    kwargs.setdefault("allow_redirects", True)
    r = sess.request(method, proxied, **kwargs)
    # Persist any Set-Cookie returned (the worker forwards them under the
    # worker's hostname; we keep them as-is and forward them on the next
    # request — the upstream will only echo back its own).
    if session is not None:
        try:
            for c in r.cookies:
                if c.value:
                    session.cookies.set(c.name, c.value)
        except Exception:
            pass
    # Preserve final upstream URL (worker exposes it via header) so callers
    # building absolute URLs from r.url do the right thing.
    final = r.headers.get("x-mw-relay-final")
    if final:
        try:
            r.url = final  # requests allows reassigning .url
        except Exception:
            pass

    # Detect Cloudflare "Just a moment..." challenge pages BEFORE
    # raise_for_status().  These come as 403 with a JS challenge that
    # cannot be solved server-side.  We raise a dedicated exception so
    # callers can show a user-friendly message instead of a generic
    # "403 Forbidden".
    if r.status_code == 403:
        body_start = (r.text or "")[:2000].lower()
        if "just a moment" in body_start or "checking your browser" in body_start:
            raise CloudflareChallengeError(
                f"Cloudflare browser challenge on {url} — cannot bypass",
                response=r,
            )

    r.raise_for_status()
    return r


def _try_with_fallbacks(method, session, url, **kwargs):
    """Direct -> DoH+IP-pin -> Cloudflare Worker proxy. First success wins.

    If proxy_force=True the direct/DoH steps are skipped entirely (useful
    on networks that DPI-block by SNI: skipping saves several seconds of
    timeouts per request).
    """
    if _proxy_force() and _proxy_base():
        return _via_proxy(method, url, session=session, **kwargs)

    kwargs.setdefault("timeout", 20)
    kwargs.setdefault("allow_redirects", True)

    # 1) direct
    try:
        r = session.request(method, url, **kwargs)
        r.raise_for_status()
        return r
    except requests.exceptions.SSLError:
        session.verify = False
        try:
            r = session.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            err = e
    except Exception as e:
        err = e

    # 2) DoH + IP pinning (only if it looks like a network/DNS failure)
    if _is_dns_error(err):
        try:
            return _retry_with_doh(method, session, url, **kwargs)
        except Exception as e2:
            err = e2

    # 3) proxy
    if _proxy_base():
        try:
            return _via_proxy(method, url, session=session, **kwargs)
        except Exception as e3:
            err = e3

    raise err


def get(session, url, **kwargs):
    return _try_with_fallbacks("GET", session, url, **kwargs)


def post(session, url, **kwargs):
    return _try_with_fallbacks("POST", session, url, **kwargs)


# ----------------------------------------------------------- shortlink chain

_META_REFRESH_RE = re.compile(
    r'<meta[^>]*http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*?url=([^"\'>\s]+)',
    re.IGNORECASE,
)
_JS_REDIRECT_RES = (
    re.compile(r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)', re.IGNORECASE),
    re.compile(r'location\.replace\(\s*["\']([^"\']+)', re.IGNORECASE),
    re.compile(r'document\.location(?:\.href)?\s*=\s*["\']([^"\']+)', re.IGNORECASE),
)
_MAGNET_RE = re.compile(r'(magnet:\?[^"\'<>\s]+)', re.IGNORECASE)
_TORRENT_HREF_RE = re.compile(r'href=["\']([^"\']+\.torrent[^"\']*)["\']', re.IGNORECASE)


def follow_redirect(url, timeout=15, max_hops=4):
    if not url:
        return url
    if url.lower().startswith("magnet:") or url.lower().endswith(".torrent"):
        return url

    sess = _build_session(verify=False)
    sess.headers["Referer"] = url
    current = url

    for _ in range(max_hops):
        try:
            r = get(sess, current, timeout=timeout)
        except Exception:
            return current
        landed = r.url
        if landed.lower().startswith("magnet:") or landed.lower().endswith(".torrent"):
            return landed
        body = r.text or ""
        m = _MAGNET_RE.search(body)
        if m:
            return m.group(1)
        m = _TORRENT_HREF_RE.search(body)
        if m:
            href = m.group(1)
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                p = urlparse(landed)
                href = f"{p.scheme}://{p.netloc}{href}"
            return href
        m = _META_REFRESH_RE.search(body)
        if m:
            current = _absolutize(m.group(1), landed)
            continue
        nxt = None
        for pat in _JS_REDIRECT_RES:
            mm = pat.search(body)
            if mm:
                nxt = _absolutize(mm.group(1), landed)
                break
        if nxt and nxt != current:
            current = nxt
            continue
        return landed

    return current


def _absolutize(href, base):
    href = (href or "").strip().strip('"\'')
    if not href:
        return base
    if href.startswith(("http://", "https://", "magnet:")):
        return href
    if href.startswith("//"):
        return "https:" + href
    from urllib.parse import urljoin
    return urljoin(base, href)


# --------------------------------------------------------------- diagnose

def diagnose(url, timeout=12):
    """Return (status, bytes, final_url, info_msg) for one GET.

    Tries direct first; if that fails with a network error, retries via
    DoH+IP-pinning and reports which path succeeded.
    """
    sess = _build_session(verify=True)
    try:
        r = sess.get(url, timeout=timeout, allow_redirects=True)
        body = r.text or ""
        low = body.lower()
        info = ""
        if "just a moment" in low or "checking your browser" in low:
            info = "Cloudflare challenge"
        elif "attention required" in low and "cloudflare" in low:
            info = "Cloudflare blocked"
        elif r.status_code in (403, 429, 503):
            info = f"HTTP {r.status_code}"
        return r.status_code, len(r.content), r.url, info
    except requests.exceptions.SSLError as e:
        sess.verify = False
        try:
            r = sess.get(url, timeout=timeout, allow_redirects=True)
            return r.status_code, len(r.content), r.url, "SSL bypass"
        except Exception as e2:
            err = e2
    except Exception as e:
        err = e

    # Direct path failed. Try DoH.
    if _is_dns_error(err):
        host = urlparse(url).hostname or ""
        ips = dns_doh.resolve(host) if host else []
        if ips:
            try:
                r = _retry_with_doh("GET", sess, url, timeout=timeout, allow_redirects=True)
                return r.status_code, len(r.content), r.url, f"OK via DoH ({ips[0]})"
            except Exception:
                pass

    # Try proxy as last resort
    if _proxy_base():
        try:
            r = _via_proxy("GET", url, timeout=timeout, allow_redirects=True)
            return r.status_code, len(r.content), r.url, "OK via proxy"
        except Exception as e4:
            return 0, 0, url, f"directo+DoH+proxy fallaron: {e4.__class__.__name__}"
    return 0, 0, url, f"{err.__class__.__name__} (sin proxy configurado)"


def diagnose_proxy():
    """Probe the configured proxy URL itself. Returns (ok, msg)."""
    base = _proxy_base()
    if not base:
        return False, "no configurado"
    try:
        r = requests.get(base + "/", timeout=10)
        if r.status_code == 200 and "relay" in (r.text or "").lower():
            return True, "OK"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"
