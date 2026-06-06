"""Inspect listing HTML for inline JSON/data containing series catalog."""
import requests, re
from urllib.parse import quote
PROXY='https://mw-relay.israeldm93.workers.dev'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
def get(u):
    return requests.get(PROXY+'/?u='+quote(u,safe=''), headers=H, timeout=25)

URLS = [
    'https://www.wolfmax4k.com/series/1080p/',
    'https://www.wolfmax4k.com/series/',
    'https://www.wolfmax4k.com/buscar/arcane',
]

for u in URLS:
    print('='*70)
    print(u)
    r = get(u)
    txt = r.content.decode('utf-8','ignore')
    print(f'len={len(txt)}')
    # Look for promising patterns
    for pat in [
        r'<script[^>]*type=["\']application/json["\'][^>]*>',
        r'window\.__\w+__\s*=',
        r'var\s+\w+\s*=\s*\[\{',
        r'data-(?:guid|id|url|slug)=',
        r'data\.find\.php',
        r'/online/\d+',
        r'/capitulo/\d+',
        r'/episodio/\d+',
        r'datafinds',
        r'torrentName',
        r'<a[^>]+href=["\'][^"\']*\d{4,}',
        r'getElementById\(["\']ffind',
        r'name=["\']token["\']',
        r'arcane',
    ]:
        hits = re.findall(pat, txt, re.IGNORECASE)
        if hits:
            print(f'  {pat!r:55s} -> {len(hits)} hits, sample: {hits[0][:80]!r}')
    # Also dump first <script> tags
    scripts = re.findall(r'<script[^>]*>(.{0,500})', txt, re.DOTALL)
    print(f'  scripts (first 500 chars): {len(scripts)}')
