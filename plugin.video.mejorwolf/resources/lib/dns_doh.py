"""DNS-over-HTTPS resolver, IP-pinned end-to-end.

Spanish ISPs that block torrent sites by DNS often also block the
hostnames of public DoH resolvers (`cloudflare-dns.com`, `dns.google`).
To survive that, we hit the resolver **by IP address**, with TLS SNI
set explicitly so the TLS handshake still succeeds against the real cert.

Endpoints tried (in order):
  - https://1.1.1.1/dns-query        (Cloudflare, IP-direct)
  - https://1.0.0.1/dns-query        (Cloudflare secondary)
  - https://9.9.9.9:5053/dns-query   (Quad9, alt port)
  - https://8.8.8.8/resolve          (Google, IP-direct)
  - https://8.8.4.4/resolve          (Google secondary)

If all of those fail too, the user is on a network that does deep
packet inspection on TLS SNI for these hosts. In that case only a VPN
or changing the device's DNS at OS level will help.
"""

import time
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_CACHE = {}
_TTL = 300

# (ip, port, path, sni_hostname)
ENDPOINTS = (
    ("1.1.1.1",  443,  "/dns-query", "cloudflare-dns.com"),
    ("1.0.0.1",  443,  "/dns-query", "cloudflare-dns.com"),
    ("9.9.9.9",  5053, "/dns-query", "dns.quad9.net"),
    ("8.8.8.8",  443,  "/resolve",   "dns.google"),
    ("8.8.4.4",  443,  "/resolve",   "dns.google"),
)

_HEADERS = {
    "accept": "application/dns-json",
    "user-agent": "Mozilla/5.0 (compatible; KodiAddon/1.0)",
}


def _query_endpoint(ip, port, path, sni, host, qtype, timeout=6):
    """Make a DoH JSON query directly to the given IP, with TLS SNI=sni."""
    # urllib3 PoolManager with server_hostname pinning, verify off.
    pool = urllib3.HTTPSConnectionPool(
        ip,
        port=port,
        cert_reqs="CERT_NONE",
        assert_hostname=False,
        server_hostname=sni,
    )
    url = f"{path}?name={host}&type={qtype}"
    try:
        r = pool.request(
            "GET", url,
            headers={"Host": sni, **_HEADERS},
            timeout=timeout,
        )
    except Exception:
        return []
    if r.status != 200:
        return []
    try:
        import json
        data = json.loads(r.data.decode("utf-8", "replace"))
    except Exception:
        return []
    out = []
    for a in (data.get("Answer") or []):
        if a.get("type") in (1, 28):
            ip_ans = (a.get("data") or "").strip()
            if ip_ans:
                out.append(ip_ans)
    return out


def resolve(host, prefer_v4=True):
    """Return a list of IPs for `host` (v4 first by default), 5-min cache."""
    if not host:
        return []
    now = time.time()
    cached = _CACHE.get(host)
    if cached and cached[0] > now:
        return cached[1]

    v4, v6 = [], []
    for ip, port, path, sni in ENDPOINTS:
        if not v4:
            v4 = _query_endpoint(ip, port, path, sni, host, "A")
        if not v6:
            v6 = _query_endpoint(ip, port, path, sni, host, "AAAA")
        if v4:
            break  # v4 is enough; AAAA is best-effort

    ips = (v4 + v6) if prefer_v4 else (v6 + v4)
    if ips:
        _CACHE[host] = (now + _TTL, ips)
    return ips


def diagnose_endpoints():
    """Return a list of (label, status) tuples for each DoH endpoint, so
    the addon can show which resolvers reach the user and which don't."""
    results = []
    test_host = "example.com"
    for ip, port, path, sni in ENDPOINTS:
        ips = _query_endpoint(ip, port, path, sni, test_host, "A", timeout=4)
        label = f"DoH {ip}:{port} ({sni})"
        if ips:
            results.append((label, f"OK ({ips[0]})"))
        else:
            results.append((label, "fallo"))
    return results
