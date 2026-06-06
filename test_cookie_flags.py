"""Check if seriesly_session cookie is HttpOnly."""
import requests
from urllib.parse import quote

PROXY = "https://mw-relay.israeldm93.workers.dev"

r = requests.get(f"{PROXY}/?u={quote('https://series.ly/', safe='')}",
                 timeout=15)

print("Set-Cookie headers:")
try:
    for sc in r.raw.headers.getlist("Set-Cookie"):
        name = sc.split("=")[0].strip()
        httponly = "httponly" in sc.lower()
        secure = "secure" in sc.lower()
        print(f"  {name}: HttpOnly={httponly}, Secure={secure}")
        if "seriesly_session" in name:
            print(f"    >>> FULL: {sc[:200]}")
except:
    sc_header = r.headers.get("Set-Cookie", "")
    print(f"  Raw: {sc_header[:300]}")
