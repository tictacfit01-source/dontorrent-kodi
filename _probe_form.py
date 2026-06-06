"""Try POST /buscar with full form data (token from GET) — bypass JS AJAX."""
import requests, re
from urllib.parse import quote
PROXY='https://mw-relay.israeldm93.workers.dev'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}

s = requests.Session()
s.headers.update(H)

# Step 1: GET shell to harvest token + cookies
shell_url = 'https://www.wolfmax4k.com/buscar/arcane'
r = s.get(PROXY+'/?u='+quote(shell_url,safe=''), timeout=25)
txt = r.content.decode('utf-8','ignore')
m = re.search(r'name="token"\s+value="([^"]+)"', txt)
token = m.group(1) if m else None
print(f'shell {r.status_code} len={len(txt)} token={token!r}')
print(f'cookies: {dict(s.cookies)}')

# Step 2: POST to /buscar with form data
post_url = 'https://www.wolfmax4k.com/buscar'
form = {
    'token': token,
    'q': 'arcane',
    'l': '100',
    'pg': '1',
    '_ACTION': 'buscar',
}
# Try via proxy POST (worker must support POST forwarding via /?u=)
r2 = s.post(PROXY+'/?u='+quote(post_url,safe=''),
            data=form,
            headers={'Referer': shell_url, 'Origin':'https://www.wolfmax4k.com'},
            timeout=25)
print(f'\nPOST /buscar -> {r2.status_code} len={len(r2.content)}')
out = r2.content.decode('utf-8','ignore')
# Look for results
print(f'  arcane occurrences: {out.lower().count("arcane")}')
print(f'  hrefs with /online/ : {len(re.findall(r"/online/\d+", out))}')
print(f'  hrefs with /capitulo/ : {len(re.findall(r"/capitulo/\d+", out))}')
print(f'  hrefs with /episodio/ : {len(re.findall(r"/episodio/\d+", out))}')
# Dump first 3KB
print('\n--- first 3KB ---')
print(out[:3000])
