import requests, re
from urllib.parse import quote
PROXY='https://mw-relay.israeldm93.workers.dev'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
def get(u):
    return requests.get(PROXY+'/?u='+quote(u,safe=''), headers=H, timeout=25)

PAT = re.compile(r"href=['\"](/(?:online|capitulo|episodio|movie|pelicula|serie-online[\w-]*)/\d+)")
candidates = [
    'https://www.wolfmax4k.com/buscar/arcane',
    'https://www.wolfmax4k.com/buscar/arcane/',
    'https://www.wolfmax4k.com/buscar/arcane/1',
    'https://www.wolfmax4k.com/buscar/arcane/0',
    'https://www.wolfmax4k.com/buscar?q=arcane',
    'https://wolfmax4k.com/buscar/arcane',
    'https://www.wolfmax4k.com/buscar?q=arcane&_ACTION=buscar',
]
for u in candidates:
    try:
        r = get(u)
        txt = r.content.decode('utf-8','ignore')
        n_hrefs = len(PAT.findall(txt))
        n_arcane = txt.lower().count('arcane')
        n_caps = len(re.findall(r'\bCap\.', txt))
        print(f'{r.status_code} len={len(r.content):>6} hrefs={n_hrefs:>3} arc={n_arcane:>3} caps={n_caps:>3} {u}')
    except Exception as e:
        print(f'ERR {u} {e}')
