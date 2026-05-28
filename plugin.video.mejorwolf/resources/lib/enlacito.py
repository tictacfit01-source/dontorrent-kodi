"""
Enlacito.com resolver.

WolfMax4K hides the direct .torrent URL behind an `enlacito.com/s.php?i=<b64>`
shortener. The browser flow is:

    1. User clicks link  -> browser opens `enlacito.com/s.php?i=<ihash>`
    2. That page POSTs a small form with `linkser=jbysznk4x.pbz` (ROT13 of
       "wolfmax4k.com") to `https://enlacito.com/#<ihash>`, with a PHPSESSID
       cookie obtained on step 1.
    3. The server replies with a huge obfuscated HTML + JS page that contains:
          var api_key  = "w0lfm4x4k1";       // red herring
          var link_out = "<base64>";         // AES-256-CBC ciphertext
       (the real link is server-side re-encrypted per-request, different
        from the original `i=` param.)
    4. The obfuscated JS decrypts `link_out` with a STATIC password
       (`fee631d2cffda38a78b96ee6d2dfb43a`) that is hidden inside an
       obfuscator.io string array. We extracted it once by running the JS
       in a sandbox and observing `GibberishAES.dec(ciphertext, password)`.

So our resolver repeats steps 1-3 via the Cloudflare Worker (forwards
Cookie/Set-Cookie), then does the AES-256-CBC decryption in pure Python.

If enlacito.com ever rotates the password, `WF_CIPHER_PASSWORD` is the one
knob to turn. The addon also ships a setting to override it without
publishing a new version.
"""

import re
from urllib.parse import urlparse, parse_qs

import xbmc
import xbmcaddon

from . import http_session as hs
from . import aes_pure

# Static cipher password baked into enlacito.com's obfuscated JS for
# wolfmax4k.com referrals. Extracted by running the obfuscated script in
# a Node vm sandbox with a stubbed `GibberishAES.dec` that captures its
# second argument. If enlacito updates this, override via settings.
_CIPHER_PASSWORD = "fee631d2cffda38a78b96ee6d2dfb43a"

# ROT13 of "wolfmax4k.com". Enlacito checks this to allow the POST.
_LINKSER = "jbysznk4x.pbz"

_LINK_OUT_RE = re.compile(r'var\s+link_out\s*=\s*"([^"]+)"')
_LOG = lambda m: xbmc.log("[MejorWolf/enlacito] " + m, xbmc.LOGINFO)


def _cfg_password():
    try:
        addon = xbmcaddon.Addon()
        pw = (addon.getSetting("enlacito_password") or "").strip()
        if pw:
            return pw
    except Exception:
        pass
    return _CIPHER_PASSWORD


def is_enlacito_url(url):
    if not url:
        return False
    u = url.lower()
    return "enlacito.com" in u


def resolve(enlacito_url, referer="https://wolfmax4k.com/"):
    """Resolve an enlacito s.php?i=... URL to the real torrent URL.

    Returns the decoded URL string, or None if anything fails.
    """
    if not enlacito_url:
        return None

    _LOG(f"resolve {enlacito_url[:120]}")
    sess = hs.make_session()

    # Step 1: hit /s.php?i=... so the server sets PHPSESSID / PHPINFO.
    try:
        hs.get(sess, enlacito_url, headers={"Referer": referer}, timeout=25)
    except Exception as e:
        _LOG(f"step1 error: {e.__class__.__name__}: {e}")
        # Continue anyway; some reverse-proxy setups still accept the POST.

    # Step 2: POST / with linkser, carrying cookies from step 1.
    try:
        r = hs.post(
            sess,
            "https://enlacito.com/",
            data={"linkser": _LINKSER},
            headers={
                "Referer":     enlacito_url,
                "Origin":      "https://enlacito.com",
                "Content-Type":"application/x-www-form-urlencoded",
            },
            timeout=25,
        )
    except Exception as e:
        _LOG(f"step2 error: {e.__class__.__name__}: {e}")
        return None

    text = r.text or ""
    if len(text) < 500:
        _LOG(f"enlacito root too short ({len(text)} bytes); probably blocked")
        return None

    m = _LINK_OUT_RE.search(text)
    if not m:
        _LOG("link_out not found in enlacito response")
        return None

    b64 = m.group(1)
    password = _cfg_password()
    try:
        pt = aes_pure.decrypt_openssl_salted(b64, password)
    except Exception as e:
        _LOG(f"decrypt error: {e.__class__.__name__}: {e}")
        return None

    try:
        url = pt.decode("utf-8")
    except UnicodeDecodeError:
        url = pt.decode("latin-1", "replace")

    url = url.strip()
    if not url.lower().startswith(("http://", "https://", "magnet:")):
        _LOG(f"decrypted but does not look like URL: {url[:80]!r}")
        return None

    _LOG(f"resolved -> {url[:120]}")
    return url


def extract_ihash(url):
    """Return the `i=` parameter of a /s.php URL, or None."""
    try:
        q = parse_qs(urlparse(url).query or "")
        val = q.get("i", [None])[0]
        return val or None
    except Exception:
        return None
