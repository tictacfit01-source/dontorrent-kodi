"""Dump ALL hrefs from /series/1080p/ to find item URL pattern."""
import requests, re
from urllib.parse import quote, urljoin
from collections import Counter
PROXY='https://mw-relay.israeldm93.workers.dev'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
def get(u): return requests.get(PROXY+'/?u='+quote(u,safe=''), headers=H, timeout=25)

for u in ['https://www.wolfmax4k.com/series/1080p/',
          'https://www.wolfmax4k.com/']:
    print('='*70); print(u)
    r = get(u); txt = r.content.decode('utf-8','ignore')
    print(f'len={len(txt)}')
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', txt)
    # Group by first 2 path segments
    pref = Counter()
    for h in hrefs:
        if h.startswith('/'):
            parts = h.split('?')[0].strip('/').split('/')
            key = '/'+'/'.join(parts[:2]) if len(parts)>=2 else '/'+parts[0]
            pref[key]+=1
    print('Top hrefs prefixes:')
    for p,c in pref.most_common(30):
        print(f'  {c:>4}  {p}')
    # Unique full hrefs that look like content
    print('\nSample numeric hrefs:')
    for h in sorted(set(re.findall(r'href=["\'](/[a-z][^"\']*\d{4,}[^"\']*)["\']', txt)))[:15]:
        print(' ', h)
    # Look for img src with "tmdb" or content image
    print('\nImg srcs sample:')
    for s in sorted(set(re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', txt)))[:8]:
        print(' ', s)
