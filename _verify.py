"""End-to-end REAL: imita el entorno Kodi, llama a scraper_wolfmax.search()
y aplica _group_results como hace main.py. Objetivo: ver UN grupo Arcane
con TODOS los capitulos y SIN contaminacion tipo 'En temporada baja'."""
import sys, os, types, time

# Stubs Kodi
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

xbmcgui_m = types.ModuleType("xbmcgui"); sys.modules["xbmcgui"] = xbmcgui_m
xbmcplugin_m = types.ModuleType("xbmcplugin"); sys.modules["xbmcplugin"] = xbmcplugin_m

# Ensure profile dir exists
os.makedirs(r"C:\Users\israe\_mw_test_profile", exist_ok=True)

# Import as package
PKG = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources"
sys.path.insert(0, PKG)
import importlib
sw = importlib.import_module("lib.scraper_wolfmax")

# Also load just the grouping helpers from main.py (without running xbmcplugin side)
main_src = open(r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources\lib\main.py",
                encoding="utf-8").read()
import re, unicodedata
ns = {"re": re, "unicodedata": unicodedata}
a = main_src.index("_SHOW_MARKERS_RE = re.compile")
b = main_src.index("def home", a)
exec(main_src[a:b], ns)

query = "arcane"
print(f"=== scraper_wolfmax.search({query!r}) ===")
t0 = time.time()
items = sw.search(query)
dt = time.time() - t0
print(f"\n--- Raw scraper returned {len(items)} items in {dt:.1f}s ---")
for it in items:
    print(f"  [{it.get('source')}] {it.get('kind'):10s} {it.get('url')}")
    print(f"     title: {it.get('title')!r}")

print(f"\n=== main._group_results over {len(items)} items ===")
groups = ns["_group_results"](items)
print(f"GROUPS: {len(groups)}")
for g in groups:
    print(f"  * [{g['source']}] {g['base']!r}  kind={g['kind']} -> {len(g['items'])} items")
    for it in g["items"]:
        s, e = ns["_ep_key"](it["title"])
        lbl = ns["_ep_label"](it) if "_display_title" in ns else it["title"]
        print(f"      {s:02d}x{e:02d}  {lbl!r}")
