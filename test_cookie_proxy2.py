"""Test 2: proteger cookie de sobreescritura"""
import re
import requests
from urllib.parse import quote as urlquote, unquote

SUPABASE_URL = "https://yddgjpjyldgvuswcsxci.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlkZGdqcGp5bGRndnVzd2NzeGNpIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNTIwMzAsImV4cCI6MjA5MzgyODAzMH0."
    "bpIkjXUowHhhJKz_HVFkGj1WogD5dpyi_JGL2yLOYl0"
)
PROXY = "https://mw-relay.israeldm93.workers.dev"

# 1. Get cookie from Supabase
r = requests.get(
    f"{SUPABASE_URL}/rest/v1/mw_config?key=eq.seriesly_cookie&select=value",
    headers={"apikey": SUPABASE_ANON_KEY,
             "Authorization": f"Bearer {SUPABASE_ANON_KEY}"},
    timeout=10,
)
data = r.json()
cookie = data[0]["value"]["cookie"] if data else ""
print(f"Cookie from Supabase: {len(cookie)} chars")

# 2. Get CSRF through proxy - but PROTECT seriesly_session
s = requests.Session()
s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://series.ly/",
})

# Store ALL other cookies from responses, but PROTECT seriesly_session
upstream_cookies = {"seriesly_session": cookie}

def store_cookies(response, protect_session=True):
    try:
        raw_cookies = response.raw.headers.getlist("Set-Cookie")
        for sc in raw_cookies:
            parts = sc.split(";")
            if "=" in parts[0]:
                cn, cv = parts[0].split("=", 1)
                cn, cv = cn.strip(), cv.strip()
                if cn and cv and "Max-Age=0" not in sc:
                    if protect_session and cn == "seriesly_session":
                        print(f"  [PROTECTED] server tried to set new seriesly_session")
                        continue
                    upstream_cookies[cn] = cv
    except Exception:
        pass

def proxy_get(url, protect_session=True):
    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies.items())
    proxied_url = f"{PROXY}/?u={urlquote(url, safe='')}"
    r = s.get(proxied_url, allow_redirects=True, timeout=30,
              headers={"Cookie": cookie_header})
    store_cookies(r, protect_session)
    return r

def proxy_post(url, **kwargs):
    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies.items())
    hdrs = kwargs.get("headers", {})
    hdrs["Cookie"] = cookie_header
    kwargs["headers"] = hdrs
    proxied_url = f"{PROXY}/?u={urlquote(url, safe='')}"
    r = s.post(proxied_url, allow_redirects=True, timeout=30, **kwargs)
    return r

# Step 1: CSRF (protecting our session cookie)
print("\n--- Getting CSRF (protecting session cookie) ---")
try:
    proxy_get("https://series.ly/sanctum/csrf-cookie")
except Exception as e:
    print(f"  sanctum error: {e}")

r2 = proxy_get("https://series.ly/")
print(f"  Homepage status: {r2.status_code}")

# Get CSRF from meta tag
m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r2.text)
csrf_meta = m.group(1) if m else ""

# Get CSRF from XSRF-TOKEN cookie
xsrf_cookie = upstream_cookies.get("XSRF-TOKEN", "")
csrf_from_cookie = unquote(xsrf_cookie) if xsrf_cookie else ""

print(f"  CSRF meta: {csrf_meta[:30]}..." if csrf_meta else "  NO CSRF meta")
print(f"  XSRF cookie: {xsrf_cookie[:30]}..." if xsrf_cookie else "  NO XSRF cookie")
print(f"  All cookies: {list(upstream_cookies.keys())}")
print(f"  seriesly_session preserved: {upstream_cookies['seriesly_session'] == cookie}")

# Step 2: Test with meta CSRF
print("\n--- Test A: meta CSRF + protected session ---")
r3 = proxy_post(
    "https://series.ly/api/search/posts",
    json={"query": "test"},
    headers={
        "Accept": "application/json",
        "X-CSRF-TOKEN": csrf_meta,
        "X-Requested-With": "XMLHttpRequest",
    },
)
print(f"  Status: {r3.status_code}")
print(f"  Body: {r3.text[:200]}")

# Step 3: Test with XSRF cookie
if csrf_from_cookie and r3.status_code != 200:
    print("\n--- Test B: XSRF cookie + protected session ---")
    r4 = proxy_post(
        "https://series.ly/api/search/posts",
        json={"query": "test"},
        headers={
            "Accept": "application/json",
            "X-XSRF-TOKEN": csrf_from_cookie,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    print(f"  Status: {r4.status_code}")
    print(f"  Body: {r4.text[:200]}")

# Step 4: Test with NO protection (let server overwrite)
if r3.status_code != 200:
    print("\n--- Test C: let server cookies flow naturally ---")
    upstream_cookies2 = {"seriesly_session": cookie}

    def store_all(response):
        try:
            raw_cookies = response.raw.headers.getlist("Set-Cookie")
            for sc in raw_cookies:
                parts = sc.split(";")
                if "=" in parts[0]:
                    cn, cv = parts[0].split("=", 1)
                    cn, cv = cn.strip(), cv.strip()
                    if cn and cv and "Max-Age=0" not in sc:
                        upstream_cookies2[cn] = cv
        except Exception:
            pass

    # Get CSRF fresh
    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies2.items())
    r5 = s.get(f"{PROXY}/?u={urlquote('https://series.ly/sanctum/csrf-cookie', safe='')}",
               headers={"Cookie": cookie_header}, timeout=30)
    store_all(r5)

    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies2.items())
    r6 = s.get(f"{PROXY}/?u={urlquote('https://series.ly/', safe='')}",
               headers={"Cookie": cookie_header}, timeout=30)
    store_all(r6)

    xsrf2 = unquote(upstream_cookies2.get("XSRF-TOKEN", ""))
    m2 = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r6.text)
    csrf2 = m2.group(1) if m2 else xsrf2

    # Check if homepage shows logged in user
    logged_in_check = re.search(r'(perfil|logout|cerrar.sesion|mi.cuenta)', r6.text[:5000], re.I)
    print(f"  Homepage logged in? {'YES: ' + logged_in_check.group(1) if logged_in_check else 'NO'}")
    print(f"  Session cookie changed: {upstream_cookies2['seriesly_session'] != cookie}")

    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies2.items())
    r7 = s.post(
        f"{PROXY}/?u={urlquote('https://series.ly/api/search/posts', safe='')}",
        json={"query": "test"},
        headers={
            "Cookie": cookie_header,
            "Accept": "application/json",
            "X-CSRF-TOKEN": csrf2,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    print(f"  Status: {r7.status_code}")
    print(f"  Body: {r7.text[:200]}")
