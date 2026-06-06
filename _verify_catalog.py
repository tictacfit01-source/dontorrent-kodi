"""Verify _search_via_catalog actually finds 'arcane' and 'comunidad del anillo'."""
import sys, os, types, time

_settings = {
    "proxy_url":   "https://mw-relay.israeldm93.workers.dev",
    "wf_base_url": "https://www.wolfmax4k.com",
    "use_proxy":   "true",
}
class _Addon:
    def getSetting(self, k): return _settings.get(k, "")
    def getAddonInfo(self, k):
        if k == "profile": return r"C:\Users\israe\_mw_test_profile_cat"
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
os.makedirs(r"C:\Users\israe\_mw_test_profile_cat", exist_ok=True)

PKG = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources"
sys.path.insert(0, PKG)
import importlib
sw = importlib.import_module("lib.scraper_wolfmax")

print("=== _build_catalog ===")
t0=time.time()
cat = sw._build_catalog()
print(f"catalog: {len(cat)} entradas en {time.time()-t0:.1f}s")
# Mostrar samples por kind
from collections import Counter
c = Counter(it["kind"] for it in cat)
print(f"kinds: {dict(c)}")
# Buscar arcane
arc = [it for it in cat if "arcane" in (it.get("title") or "").lower()
       or "arcane" in (it.get("url") or "").lower()]
anil = [it for it in cat if "anillo" in (it.get("title") or "").lower()
        or "anillo" in (it.get("url") or "").lower()]
print(f"\narcane in catalog: {len(arc)}")
for it in arc[:5]: print(f"  {it['kind']:8s} {it['url']} | {it['title']!r}")
print(f"\nanillo in catalog: {len(anil)}")
for it in anil[:5]: print(f"  {it['kind']:8s} {it['url']} | {it['title']!r}")

print("\n=== _search_via_catalog('arcane') ===")
t0=time.time()
items = sw._search_via_catalog("arcane")
print(f"-> {len(items)} items en {time.time()-t0:.1f}s")
for it in items[:10]:
    print(f"  {it['kind']:8s} {it['url']} | {it['title']!r}")

print("\n=== _search_via_catalog('comunidad del anillo') ===")
t0=time.time()
items = sw._search_via_catalog("comunidad del anillo")
print(f"-> {len(items)} items en {time.time()-t0:.1f}s")
for it in items[:10]:
    print(f"  {it['kind']:8s} {it['url']} | {it['title']!r}")
