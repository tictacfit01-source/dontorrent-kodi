"""Test directo: cookie de Supabase → series.ly API"""
import re
import requests

SUPABASE_URL = "https://yddgjpjyldgvuswcsxci.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlkZGdqcGp5bGRndnVzd2NzeGNpIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNTIwMzAsImV4cCI6MjA5MzgyODAzMH0."
    "bpIkjXUowHhhJKz_HVFkGj1WogD5dpyi_JGL2yLOYl0"
)

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

# 2. Test directly against series.ly
s = requests.Session()
s.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://series.ly/",
})
s.cookies.set("seriesly_session", cookie, domain=".series.ly", path="/")

# Get CSRF
print("\n--- Getting CSRF ---")
try:
    s.get("https://series.ly/sanctum/csrf-cookie", timeout=15)
except Exception as e:
    print(f"  sanctum error: {e}")

r2 = s.get("https://series.ly/", timeout=15)
print(f"  Homepage status: {r2.status_code}")
m = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', r2.text)
csrf = m.group(1) if m else ""
print(f"  CSRF: {csrf[:30]}..." if csrf else "  NO CSRF")
print(f"  Session cookies: {[(c.name, c.domain) for c in s.cookies]}")

# 3. Test search API
print("\n--- Testing search API ---")
r3 = s.post(
    "https://series.ly/api/search/posts",
    json={"query": "test"},
    headers={
        "Accept": "application/json",
        "X-CSRF-TOKEN": csrf,
        "X-Requested-With": "XMLHttpRequest",
    },
    timeout=15,
)
print(f"  Status: {r3.status_code}")
print(f"  Body: {r3.text[:300]}")
