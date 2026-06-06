"""Test: login directo email+password en series.ly via proxy.
Probar si Turnstile se valida server-side o solo client-side."""
import re
import requests
from urllib.parse import quote as urlquote, unquote

PROXY = "https://mw-relay.israeldm93.workers.dev"

s = requests.Session()
s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Linux; Android 12; SHIELD Android TV) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
})

upstream = {}

def store(response):
    try:
        for sc in response.raw.headers.getlist("Set-Cookie"):
            parts = sc.split(";")
            if "=" in parts[0]:
                cn, cv = parts[0].split("=", 1)
                cn, cv = cn.strip(), cv.strip()
                if cn and cv and "Max-Age=0" not in sc:
                    upstream[cn] = cv
    except:
        pass

def pget(url):
    hdrs = {}
    if upstream:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in upstream.items())
    r = s.get(f"{PROXY}/?u={urlquote(url, safe='')}", headers=hdrs,
              allow_redirects=True, timeout=30)
    store(r)
    return r

def ppost(url, **kwargs):
    hdrs = kwargs.pop("headers", {})
    if upstream:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in upstream.items())
    r = s.post(f"{PROXY}/?u={urlquote(url, safe='')}", headers=hdrs,
               allow_redirects=True, timeout=30, **kwargs)
    store(r)
    return r

# Step 1: Get CSRF
print("=== Step 1: CSRF ===")
try:
    pget("https://series.ly/sanctum/csrf-cookie")
except:
    pass
r1 = pget("https://series.ly/ingresar")
print(f"  Login page status: {r1.status_code}")

m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r1.text)
csrf = m.group(1) if m else ""
print(f"  CSRF: {csrf[:30]}..." if csrf else "  NO CSRF")

# Check if Turnstile is present
has_turnstile = "turnstile" in r1.text.lower() or "cf-turnstile" in r1.text.lower()
print(f"  Turnstile detected: {has_turnstile}")

# Find Turnstile sitekey if present
tk = re.search(r'data-sitekey=["\']([^"\']+)', r1.text)
if tk:
    print(f"  Turnstile sitekey: {tk.group(1)}")

# Check for form action
form_action = re.search(r'<form[^>]*action=["\']([^"\']*ingresar[^"\']*)', r1.text, re.I)
print(f"  Form action: {form_action.group(1) if form_action else 'not found'}")

# Look for turnstile field name
turnstile_field = re.search(r'name=["\']([^"\']*turnstile[^"\']*)', r1.text, re.I)
if turnstile_field:
    print(f"  Turnstile field: {turnstile_field.group(1)}")
turnstile_field2 = re.search(r'name=["\']cf-turnstile-response["\']', r1.text, re.I)
print(f"  cf-turnstile-response field: {'YES' if turnstile_field2 else 'NO'}")

# Step 2: Try login WITHOUT email (just to see what the server responds)
print("\n=== Step 2: POST login (sin credenciales - solo ver respuesta) ===")
r2 = ppost("https://series.ly/ingresar", data={
    "_token": csrf,
    "email": "test@test.com",
    "password": "testtest",
    "remember": "1",
}, headers={
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://series.ly/ingresar",
    "Origin": "https://series.ly",
})
print(f"  Status: {r2.status_code}")
final_url = r2.headers.get("x-mw-relay-final", r2.url)
print(f"  Final URL: {final_url}")

# Check response for error messages
errors = re.findall(r'class="[^"]*error[^"]*"[^>]*>([^<]+)', r2.text, re.I)
if errors:
    print(f"  Errors: {errors[:3]}")

# Check for validation messages
validation = re.findall(r'class="[^"]*invalid-feedback[^"]*"[^>]*>([^<]+)', r2.text, re.I)
if validation:
    print(f"  Validation: {validation[:3]}")

# Check for "turnstile" in error response
if "turnstile" in r2.text.lower():
    print("  [!] Server mentions Turnstile in response")

# Check for generic error patterns
alert = re.findall(r'class="[^"]*alert[^"]*"[^>]*>(.*?)</div>', r2.text, re.I | re.S)
if alert:
    for a in alert[:2]:
        clean = re.sub(r'<[^>]+>', '', a).strip()
        if clean:
            print(f"  Alert: {clean[:100]}")

# Step 3: Try API endpoints
print("\n=== Step 3: Probar endpoints API alternativos ===")
for endpoint in ["/api/login", "/api/auth/login", "/api/v1/login",
                 "/api/auth", "/api/sanctum/token", "/oauth/token"]:
    try:
        r3 = ppost(f"https://series.ly{endpoint}", json={
            "email": "test@test.com",
            "password": "testtest",
        }, headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        status = r3.status_code
        body = r3.text[:150]
        # Only show interesting responses (not 404)
        if status != 404:
            print(f"  {endpoint}: {status} -> {body}")
    except Exception as e:
        print(f"  {endpoint}: ERROR {e}")

# Step 4: Check if there's a Livewire login component
print("\n=== Step 4: Livewire/AJAX login ===")
livewire = re.search(r'wire:submit|wire:click|livewire', r1.text, re.I)
print(f"  Livewire detected: {'YES' if livewire else 'NO'}")

# Check for any API routes in JS
api_routes = re.findall(r'["\']/(api/[^"\']+)["\']', r1.text)
if api_routes:
    print(f"  API routes found: {list(set(api_routes))[:10]}")
