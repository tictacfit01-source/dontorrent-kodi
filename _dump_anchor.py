"""Extract anchor blocks containing posters from listing pages."""
import requests, re
from urllib.parse import quote
PROXY='https://mw-relay.israeldm93.workers.dev'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
def get(u): return requests.get(PROXY+'/?u='+quote(u,safe=''), headers=H, timeout=25)

for u in ['https://www.wolfmax4k.com/series/1080p/',
          'https://www.wolfmax4k.com/peliculas/bluray-1080p/']:
    print('='*70); print(u)
    r = get(u); txt = r.content.decode('utf-8','ignore')
    print(f'len={len(txt)}')
    # Find blocks: <a href="..."> ... <img src="...assets/u/p/c/..."> ...</a>
    blocks = re.findall(
        r'<a[^>]+href="([^"]+)"[^>]*>(?:(?!</a>).){0,800}?<img[^>]+src="([^"]+)"',
        txt, re.DOTALL)
    print(f'anchor+img blocks: {len(blocks)}')
    for href, src in blocks[:10]:
        print(f'  {href:40s} -> {src[-80:]}')
    # Also search for "arcane" (case-insensitive) anywhere
    n = txt.lower().count('arcane')
    print(f'\n"arcane" occurrences: {n}')
    if n:
        for m in re.finditer(r'arcane', txt, re.IGNORECASE):
            s,e = max(0,m.start()-150), min(len(txt),m.end()+150)
            print('---'); print(txt[s:e])
