"""Test: POST login sin Turnstile token - ver si el servidor lo valida."""
import re
import requests
from urllib.parse import quote, unquote

PROXY = "https://mw-relay.israeldm93.workers.dev"

s = requests.Session()
s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Linux; Android 12; SHIELD Android TV) "
                   "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
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
    r = s.get(f"{PROXY}/?u={quote(url, safe='')}", headers=hdrs, timeout=30)
    store(r)
    return r

def ppost(url, **kwargs):
    hdrs = kwargs.pop("headers", {})
    if upstream:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in upstream.items())
    r = s.post(f"{PROXY}/?u={quote(url, safe='')}", headers=hdrs, timeout=30, **kwargs)
    store(r)
    return r

# Step 1: Get CSRF + cookies
print("=== Getting CSRF ===")
try:
    pget("https://series.ly/sanctum/csrf-cookie")
except:
    pass

r1 = pget("https://series.ly/ingresar")
m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r1.text)
csrf = m.group(1) if m else ""
print(f"  CSRF: {csrf[:30]}..." if csrf else "  NO CSRF")

# Get _token from form
token_m = re.search(r'name="_token"\s+value="([^"]+)"', r1.text)
form_token = token_m.group(1) if token_m else csrf
print(f"  Form _token: {form_token[:30]}..." if form_token else "  NO _token")
print(f"  Cookies: {list(upstream.keys())}")

# Step 2: POST WITHOUT turnstile, with fake credentials
print("\n=== Test A: POST sin cf-turnstile-response (fake creds) ===")
r2 = ppost("https://series.ly/ingresar", data={
    "_token": form_token,
    "email": "test@example.com",
    "password": "fakepassword123",
    "remember": "1",
}, headers={
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://series.ly/ingresar",
    "Origin": "https://series.ly",
})
print(f"  Status: {r2.status_code}")
final = r2.headers.get("x-mw-relay-final", "")
print(f"  Final URL: {final}")

# Check for specific error messages
errors = []
for pattern in [
    r'"message"\s*:\s*"([^"]+)"',
    r'class="[^"]*error[^"]*"[^>]*>([^<]+)',
    r'class="[^"]*alert[^"]*"[^>]*>(.*?)</div>',
    r'class="[^"]*invalid[^"]*"[^>]*>([^<]+)',
    r'<li>([^<]*(?:turnstle|captcha|verificaci|credencial|contrase|email|password)[^<]*)</li>',
]:
    for match in re.finditer(pattern, r2.text, re.I | re.S):
        text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        if text and len(text) > 3:
            errors.append(text)
if errors:
    for e in set(errors):
        print(f"  Error: {e[:150]}")
else:
    print("  No specific errors found in response")

# Check if we're still on login page
if "ingresar" in r2.text.lower()[:1000]:
    print("  Still on login page (login failed)")
else:
    print("  NOT on login page (might have succeeded?)")

# Check for Turnstile-specific error
if "turnstile" in r2.text.lower():
    print("  [!] Turnstile mentioned in response")
    # Find the context
    idx = r2.text.lower().find("turnstile")
    context = r2.text[max(0, idx-100):idx+200]
    clean = re.sub(r'<[^>]+>', ' ', context).strip()
    print(f"  Context: {clean[:200]}")
else:
    print("  [OK] Turnstile NOT mentioned in response")

# Step 3: POST WITH empty turnstile
print("\n=== Test B: POST con cf-turnstile-response vacio ===")
# Re-get CSRF (might have changed)
upstream.clear()
try:
    pget("https://series.ly/sanctum/csrf-cookie")
except:
    pass
r3_page = pget("https://series.ly/ingresar")
m3 = re.search(r'name="_token"\s+value="([^"]+)"', r3_page.text)
form_token3 = m3.group(1) if m3 else ""

r3 = ppost("https://series.ly/ingresar", data={
    "_token": form_token3,
    "email": "test@example.com",
    "password": "fakepassword123",
    "remember": "1",
    "cf-turnstile-response": "",
}, headers={
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://series.ly/ingresar",
    "Origin": "https://series.ly",
})
print(f"  Status: {r3.status_code}")
if "turnstile" in r3.text.lower():
    print("  [!] Turnstile mentioned")
else:
    print("  [OK] Turnstile NOT mentioned")

# Check errors
for match in re.finditer(r'<li>([^<]+)</li>', r3.text):
    text = match.group(1).strip()
    if text and len(text) > 5:
        print(f"  Error: {text}")
