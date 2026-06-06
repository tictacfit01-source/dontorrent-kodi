"""Verify DETERMINISTICO: testea enrichment + grouping con items
inyectados (sin depender de Brave/DDG/Bing, que estan inestables
desde redes de datacenter). Objetivo Eureka:
  1 grupo 'Arcane' kind=tvshow con N capitulos ordenados por (s,e).
"""
import sys, os, types, time

_settings = {
    "proxy_url":    "https://mw-relay.israeldm93.workers.dev",
    "wf_base_url":  "https://www.wolfmax4k.com",
    "use_proxy":    "true",
}

class _Addon:
    def getSetting(self, k): return _settings.get(k, "")
    def getAddonInfo(self, k):
        if k == "profile": return r"C:\Users\israe\_mw_test_profile"
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

os.makedirs(r"C:\Users\israe\_mw_test_profile", exist_ok=True)

PKG = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources"
sys.path.insert(0, PKG)
import importlib
sw = importlib.import_module("lib.scraper_wolfmax")

# Items "como si Brave hubiera respondido" — titulos SIN marca de episodio
# (caso real observado). El enrichment debe completar H1 al titulo real.
mock_items = [
    {"title": "Arcane", "url": "https://www.wolfmax4k.com/online/235629",
     "kind": "movie", "image": None, "quality": None, "source": "wf"},
    {"title": "Arcane", "url": "https://www.wolfmax4k.com/online/235630",
     "kind": "movie", "image": None, "quality": None, "source": "wf"},
    {"title": "Arcane [HDTV 1080p][Cap.206]",
     "url": "https://www.wolfmax4k.com/online/235631",
     "kind": "tvshow", "image": None, "quality": None, "source": "wf"},
]

print("=== Pre-enrichment ===")
for it in mock_items:
    print(f"  {it['kind']:8s} {it['url']}  {it['title']!r}")

print("\n=== Running _enrich_titles_inplace ===")
t0 = time.time()
sw._enrich_titles_inplace(mock_items)
print(f"elapsed: {time.time()-t0:.1f}s")

print("\n=== Post-enrichment ===")
for it in mock_items:
    print(f"  {it['kind']:8s} {it['url']}  {it['title']!r}")

# Cargar helpers de grouping
main_src = open(r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources\lib\main.py",
                encoding="utf-8").read()
import re, unicodedata
ns = {"re": re, "unicodedata": unicodedata}
a = main_src.index("_SHOW_MARKERS_RE = re.compile")
b = main_src.index("def home", a)
exec(main_src[a:b], ns)

print(f"\n=== main._group_results over {len(mock_items)} items ===")
groups = ns["_group_results"](mock_items)
print(f"GROUPS: {len(groups)}")
for g in groups:
    print(f"  * [{g['source']}] {g['base']!r}  kind={g['kind']} -> {len(g['items'])} items")
    for it in g["items"]:
        s, e = ns["_ep_key"](it["title"])
        print(f"      {s:02d}x{e:02d}  {it['title']!r}")

# EUREKA check
print("\n=== EUREKA CHECK ===")
ok = True
if len(groups) != 1:
    print(f"  FAIL: esperado 1 grupo, obtenidos {len(groups)}"); ok = False
elif groups[0]["kind"] != "tvshow":
    print(f"  FAIL: kind={groups[0]['kind']}, esperado tvshow"); ok = False
elif len(groups[0]["items"]) != 3:
    print(f"  FAIL: items={len(groups[0]['items'])}, esperado 3"); ok = False
else:
    keys = [ns["_ep_key"](it["title"]) for it in groups[0]["items"]]
    if any(k == (99,99) for k in keys):
        print(f"  FAIL: algun item sin clave de episodio: {keys}"); ok = False
    elif keys != sorted(keys):
        print(f"  FAIL: no ordenado: {keys}"); ok = False
    else:
        print(f"  OK: 1 grupo Arcane kind=tvshow con 3 capitulos ordenados {keys}")

print("\n" + ("EUREKA CONFIRMADO" if ok else "NO EUREKA, seguir afinando"))
sys.exit(0 if ok else 1)
