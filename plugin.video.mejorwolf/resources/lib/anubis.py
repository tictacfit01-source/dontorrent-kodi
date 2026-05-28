"""
Anubis anti-bot proof-of-work solver.

DonTorrent uses Anubis (https://github.com/TecharoHQ/anubis) to protect
against bots.  The challenge requires computing SHA-256 hashes until one
is found whose hex representation starts with *difficulty* zeros.

This module:
 1. Detects Anubis challenge pages in HTTP responses.
 2. Solves the proof-of-work entirely in Python (hashlib is C-optimised).
 3. Submits the solution via the relay proxy, forwarding the initial
    cookies (critical — Anubis rejects requests without them).
 4. Returns the solved cookies so subsequent requests bypass the challenge.

Typical solve time for difficulty 5 (~1 M iterations): < 2 s on any modern
device, including Android TV boxes.
"""

import hashlib
import json
import re
import time
import requests
import xbmc
import xbmcaddon

_ANUBIS_MARKER = "anubis_challenge"
_CHALLENGE_RE = re.compile(
    r'<script\s+id="anubis_challenge"\s+type="application/json">\s*'
    r'(.*?)\s*</script>',
    re.DOTALL,
)

# Cache: {domain: {"cookies": {...}, "ts": ...}}
_cookie_cache = {}
_COOKIE_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Persist cookies across Kodi plugin invocations.
#
# Each click in Kodi's video addon spawns a new Python process, so the
# in-memory _cookie_cache is empty.  We store the last solved cookies in
# Kodi's addon settings so they survive.
# ---------------------------------------------------------------------------

def _persist_cookies(domain, cookies):
    """Save cookies to Kodi settings so they survive across invocations."""
    try:
        addon = xbmcaddon.Addon()
        blob = json.dumps({"domain": domain, "cookies": cookies,
                           "ts": time.time()})
        addon.setSetting("anubis_cookies", blob)
    except Exception:
        pass


def _load_persisted_cookies():
    """Load cookies from Kodi settings into _cookie_cache on startup."""
    try:
        addon = xbmcaddon.Addon()
        raw = addon.getSetting("anubis_cookies")
        if not raw:
            return
        data = json.loads(raw)
        domain = data.get("domain", "")
        cookies = data.get("cookies", {})
        ts = data.get("ts", 0)
        if not domain or not cookies:
            return
        if time.time() - ts > _COOKIE_TTL:
            return
        _cookie_cache[domain] = {"cookies": cookies, "ts": ts}
        xbmc.log(f"[anubis] Restored persisted cookies for {domain} "
                 f"(age {int(time.time() - ts)}s)", xbmc.LOGINFO)
    except Exception:
        pass


# Auto-load persisted cookies when the module is first imported.
_load_persisted_cookies()


def is_anubis(text):
    """Quick check whether *text* is an Anubis challenge page."""
    return _ANUBIS_MARKER in text


def _parse_challenge(html):
    """Extract challenge dict from the Anubis HTML page."""
    m = _CHALLENGE_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, KeyError):
        return None


def _solve_pow(random_data, difficulty):
    """Brute-force the proof-of-work.

    Returns (hex_hash, nonce) such that SHA-256(random_data + str(nonce))
    starts with *difficulty* hex zeros.

    Difficulty 5 means:
      - bytes[0] == 0 and bytes[1] == 0  (first 2 full bytes = 4 hex zeros)
      - bytes[2] >> 4 == 0               (upper nibble of 3rd byte = 5th zero)
    """
    full_bytes = difficulty // 2
    check_nibble = difficulty % 2 != 0

    nonce = 0
    t0 = time.time()
    while True:
        candidate = (random_data + str(nonce)).encode("utf-8")
        digest = hashlib.sha256(candidate).digest()

        ok = True
        for i in range(full_bytes):
            if digest[i] != 0:
                ok = False
                break
        if ok and check_nibble and (digest[full_bytes] >> 4) != 0:
            ok = False

        if ok:
            hex_hash = digest.hex()
            elapsed = time.time() - t0
            xbmc.log(
                f"[anubis] PoW solved: nonce={nonce}, "
                f"hash={hex_hash[:12]}..., "
                f"difficulty={difficulty}, "
                f"time={elapsed:.2f}s, "
                f"rate={nonce / max(elapsed, 0.001):.0f} H/s",
                xbmc.LOGINFO,
            )
            return hex_hash, nonce, elapsed

        nonce += 1

        # Safety: log progress every 500 K iterations
        if nonce % 500000 == 0:
            xbmc.log(
                f"[anubis] Still solving... nonce={nonce}, "
                f"elapsed={time.time() - t0:.1f}s",
                xbmc.LOGINFO,
            )


def _extract_cookies_from_response(response):
    """Extract cookies from a requests.Response as a dict."""
    cookies = {}
    # requests stores response cookies in response.cookies
    for name, value in response.cookies.items():
        cookies[name] = value
    return cookies


def solve_and_get_cookie(html, target_url, proxy_base, init_cookies=None,
                         session=None):
    """Solve the Anubis challenge and return {cookie_name: cookie_value}.

    Parameters
    ----------
    html : str
        The Anubis challenge HTML page.
    target_url : str
        The original URL we tried to fetch (e.g. https://dontorrent.irish/).
    proxy_base : str
        The relay proxy base (e.g. https://mw-relay.israeldm93.workers.dev).
    init_cookies : dict, optional
        Cookies received from the initial challenge page fetch.
        CRITICAL: Anubis requires these to validate the pass-challenge.
    session : requests.Session, optional
        Reuse an existing session.

    Returns
    -------
    dict  {cookie_name: cookie_value}  or empty dict on failure.
    """
    from urllib.parse import urlparse, quote as urlquote

    challenge_data = _parse_challenge(html)
    if not challenge_data:
        xbmc.log("[anubis] Could not parse challenge JSON", xbmc.LOGERROR)
        return {}

    rules = challenge_data.get("rules", {})
    challenge = challenge_data.get("challenge", {})
    random_data = challenge.get("randomData", "")
    difficulty = rules.get("difficulty", challenge.get("difficulty", 5))
    challenge_id = challenge.get("id", "")

    if not random_data or not challenge_id:
        xbmc.log("[anubis] Missing randomData or challenge id", xbmc.LOGERROR)
        return {}

    xbmc.log(
        f"[anubis] Solving PoW: difficulty={difficulty}, "
        f"id={challenge_id[:16]}...",
        xbmc.LOGINFO,
    )

    hex_hash, nonce, elapsed = _solve_pow(random_data, difficulty)
    elapsed_ms = int(elapsed * 1000)

    # Build the pass-challenge URL on the target domain
    parsed = urlparse(target_url)
    origin = f"{parsed.scheme}://{parsed.hostname}"
    pass_url = (
        f"{origin}/.within.website/x/cmd/anubis/api/pass-challenge"
        f"?id={urlquote(challenge_id, safe='')}"
        f"&response={hex_hash}"
        f"&nonce={nonce}"
        f"&redir=/"
        f"&elapsedTime={elapsed_ms}"
    )

    # Route through proxy with nr=1 (no redirect) to capture Set-Cookie
    proxied = f"{proxy_base}/?u={urlquote(pass_url, safe='')}&nr=1"

    # CRITICAL: Include the cookies from the initial challenge page fetch.
    # Anubis requires the browser-pow-cookie-verification cookie to validate.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Encoding": "identity",
    }
    if init_cookies:
        headers["Cookie"] = "; ".join(
            f"{k}={v}" for k, v in init_cookies.items()
        )

    s = session or requests.Session()
    try:
        r = s.get(proxied, timeout=20, allow_redirects=False, headers=headers)
    except Exception as e:
        xbmc.log(f"[anubis] pass-challenge request failed: {e}", xbmc.LOGERROR)
        return {}

    xbmc.log(
        f"[anubis] pass-challenge status={r.status_code}, "
        f"location={r.headers.get('location', 'none')}",
        xbmc.LOGINFO,
    )

    # Extract new cookies from the pass-challenge response
    cookies = {}
    for key, value in r.headers.items():
        if key.lower() == "set-cookie":
            parts = value.split(";")
            if "=" in parts[0]:
                cn, cv = parts[0].split("=", 1)
                cn = cn.strip()
                cv = cv.strip()
                # Skip cookie-clearing (Max-Age=0 or empty value)
                if cn and cv and "Max-Age=0" not in value:
                    cookies[cn] = cv

    if cookies:
        domain = parsed.hostname
        # Combine with init cookies for the full set
        all_cookies = dict(init_cookies or {})
        all_cookies.update(cookies)
        _cookie_cache[domain] = {
            "cookies": all_cookies,
            "ts": time.time(),
        }
        # Persist to Kodi settings so cookies survive across invocations
        _persist_cookies(domain, all_cookies)
        xbmc.log(
            f"[anubis] Success! cookies={list(cookies.keys())} for {domain}",
            xbmc.LOGINFO,
        )
        return all_cookies
    else:
        xbmc.log(
            f"[anubis] No valid cookies from pass-challenge "
            f"(status={r.status_code})",
            xbmc.LOGWARNING,
        )
        return {}


def get_cached_cookies(domain):
    """Return cached Anubis cookies for *domain*, or empty dict if expired."""
    entry = _cookie_cache.get(domain)
    if not entry:
        return {}
    if time.time() - entry["ts"] > _COOKIE_TTL:
        del _cookie_cache[domain]
        return {}
    return dict(entry["cookies"])


def clear_cache(domain=None):
    """Clear cached cookies for *domain* (or all if None)."""
    if domain:
        _cookie_cache.pop(domain, None)
    else:
        _cookie_cache.clear()
