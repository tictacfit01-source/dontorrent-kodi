import requests, re
from urllib.parse import quote
PROXY='https://mw-relay.israeldm93.workers.dev'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
def get(u): return requests.get(PROXY+'/?u='+quote(u,safe=''), headers=H, timeout=25)

for q in ['arcane','la-comunidad-del-anillo','comunidad+del+anillo']:
    u = f'https://www.wolfmax4k.com/buscar/{q}'
    print('='*70); print(u)
    r = get(u); txt = r.content.decode('utf-8','ignore')
    print(f'len={len(txt)}')
    # Find positions of 'arcane' / 'anillo' (case-insensitive)
    needle = 'arcane' if 'arcane' in q else 'anillo'
    for m in re.finditer(needle, txt, re.IGNORECASE):
        s = max(0, m.start()-200); e = min(len(txt), m.end()+200)
        print('---')
        print(txt[s:e])
    # Also extract every href
    print('\nALL HREFS containing digits >=4:')
    for h in set(re.findall(r'href=["\']([^"\']*\d{4,}[^"\']*)["\']', txt)):
        print(' ',h)
