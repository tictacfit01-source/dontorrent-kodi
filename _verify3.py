"""Verify REAL: simula el caso 'Brave devuelve 1 sola semilla'
(escenario que el usuario ve en su Kodi: solo aparece Cap.204).
Comprobamos que la expansion proximity encuentra los OTROS caps
y que el grupo final tiene los 3.
"""
import sys, os, types, time

_settings = {
    "proxy_url":   "https://mw-relay.israeldm93.workers.dev",
    "wf_base_url": "https://www.wolfmax4k.com",
    "use_proxy":   "true",
}

class _Addon:
    def getSetting(self, k): return _settings.get(k, "")
    def getAddonInfo(self, k):
        if k == "profile": return r"C:\Users\israe\_mw_test_profile3"
        return ""

xbmc_m = types.ModuleType("xbmc")
xbmc_m.log = lambda msg, level=0: print(f"  [LOG] {msg}")
for n in ("LOGDEBUG","LOGINFO","LOGWARNING","LOGERROR"): setattr(xbmc_m, n, 0)
sys.modules["xbmc"] = xbmc_m
xbmcaddon_m = types.ModuleType("xbmcaddon")
xbmcaddon_m.Addon = lambda *a, **k: _Addon()
sys.modules["xbmcaddon"] = xbmcaddon_m
xbmcvfs_m = types.ModuleType("xbmcvfs")
xbmcvfs_m.translatePath = lambda p: p
xbmcvfs_m.exists = os.path.exists
xbmcvfs_m.mkdirs = lambda p: os.makedirs(p, exist_ok=True)
sys.modules["xbmcvfs"] = xbmcvfs_m
sys.modules["xbmcgui"] = types.ModuleType("xbmcgui")
sys.modules["xbmcplugin"] = types.ModuleType("xbmcplugin")
os.makedirs(r"C:\Users\israe\_mw_test_profile3", exist_ok=True)
# Empezar con indice limpio
idx_path = r"C:\Users\israe\_mw_test_profile3\wf_index.json"
if os.path.exists(idx_path): os.remove(idx_path)

PKG = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources"
sys.path.insert(0, PKG)
import importlib
sw = importlib.import_module("lib.scraper_wolfmax")

# Inyectar 1 sola semilla (el caso del usuario: Cap.204 solamente).
single_seed = [{
    "title": "Arcane",
    "url":   "https://www.wolfmax4k.com/online/235629",
    "kind":  "movie",
    "image": None, "quality": None, "source": "wf",
}]

# Hack: monkey-parchear _search_brave_wrapped para devolver SOLO esa semilla
_orig = sw._search_brave_wrapped
sw._search_brave_wrapped = lambda q: (single_seed, True)
print("=== search('arcane') con 1 seed inyectada ===")
t0 = time.time()
items = sw.search("arcane")
print(f"\n--- {len(items)} items en {time.time()-t0:.1f}s ---")
for it in items:
    print(f"  {it.get('kind'):8s} {it.get('url')}  {it.get('title')!r}")

# Cargar grouping
main_src = open(r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources\lib\main.py",
                encoding="utf-8").read()
import re, unicodedata
ns = {"re": re, "unicodedata": unicodedata}
a = main_src.index("_SHOW_MARKERS_RE = re.compile")
b = main_src.index("def home", a)
exec(main_src[a:b], ns)
print(f"\n=== _group_results sobre {len(items)} items ===")
groups = ns["_group_results"](items)
for g in groups:
    print(f"  * {g['base']!r} kind={g['kind']} -> {len(g['items'])} items")
    for it in g["items"]:
        s, e = ns["_ep_key"](it["title"])
        print(f"      {s:02d}x{e:02d}  {it['title']!r}")

ok = (len(groups) == 1 and groups[0]["kind"] == "tvshow"
      and len(groups[0]["items"]) >= 3)
print("\n" + ("EUREKA: 1 grupo Arcane con >=3 caps" if ok else "FAIL: revisar"))
sys.exit(0 if ok else 1)
