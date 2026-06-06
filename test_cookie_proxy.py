"""Test: cookie de Supabase → series.ly API via proxy"""
import re
import requests
from urllib.parse import quote as urlquote

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
print(f"  Start: {cookie[:50]}...")

# 2. Test through proxy
s = requests.Session()
s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://series.ly/",
})

upstream_cookies = {"seriesly_session": cookie}

def proxy_get(url, **kwargs):
    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies.items())
    hdrs = kwargs.get("headers", {})
    hdrs["Cookie"] = cookie_header
    kwargs["headers"] = hdrs
    proxied_url = f"{PROXY}/?u={urlquote(url, safe='')}"
    r = s.get(proxied_url, allow_redirects=True, timeout=30, **kwargs)
    # Store cookies from response
    try:
        raw_cookies = r.raw.headers.getlist("Set-Cookie")
        for sc in raw_cookies:
            parts = sc.split(";")
            if "=" in parts[0]:
                cn, cv = parts[0].split("=", 1)
                cn, cv = cn.strip(), cv.strip()
                if cn and cv and "Max-Age=0" not in sc:
                    upstream_cookies[cn] = cv
                    print(f"  [cookie] {cn} = {cv[:30]}...")
    except Exception:
        pass
    return r

def proxy_post(url, **kwargs):
    cookie_header = "; ".join(f"{k}={v}" for k, v in upstream_cookies.items())
    hdrs = kwargs.get("headers", {})
    hdrs["Cookie"] = cookie_header
    kwargs["headers"] = hdrs
    proxied_url = f"{PROXY}/?u={urlquote(url, safe='')}"
    r = s.post(proxied_url, allow_redirects=True, timeout=30, **kwargs)
    return r

# CSRF via proxy
print("\n--- Getting CSRF via proxy ---")
try:
    proxy_get("https://series.ly/sanctum/csrf-cookie")
except Exception as e:
    print(f"  sanctum error: {e}")

r2 = proxy_get("https://series.ly/")
print(f"  Homepage status: {r2.status_code}")
m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r2.text)
csrf = m.group(1) if m else ""
print(f"  CSRF: {csrf[:30]}..." if csrf else "  NO CSRF")

# Also check XSRF-TOKEN cookie
xsrf = upstream_cookies.get("XSRF-TOKEN", "")
print(f"  XSRF-TOKEN cookie: {xsrf[:30]}..." if xsrf else "  NO XSRF-TOKEN cookie")
print(f"  All upstream cookies: {list(upstream_cookies.keys())}")

# Check if seriesly_session got overwritten
sly = upstream_cookies.get("seriesly_session", "")
print(f"  seriesly_session still original? {sly == cookie}")
if sly != cookie:
    print(f"  OVERWRITTEN! new value: {sly[:50]}...")

# 3. Test search API via proxy
print("\n--- Testing search API via proxy ---")
r3 = proxy_post(
    "https://series.ly/api/search/posts",
    json={"query": "test"},
    headers={
        "Accept": "application/json",
        "X-CSRF-TOKEN": csrf,
        "X-Requested-With": "XMLHttpRequest",
    },
)
print(f"  Status: {r3.status_code}")
print(f"  Body: {r3.text[:300]}")
