"""Full search verification: queries that SHOULD hit catalog (recent)
and queries that fall back to brave/proximity (older)."""
import sys, os, types, time

_settings = {
    "proxy_url":   "https://mw-relay.israeldm93.workers.dev",
    "wf_base_url": "https://www.wolfmax4k.com",
    "use_proxy":   "true",
}
class _Addon:
    def getSetting(self, k): return _settings.get(k, "")
    def getAddonInfo(self, k):
        if k == "profile": return r"C:\Users\israe\_mw_test_profile_full"
        return ""

xbmc_m = types.ModuleType("xbmc")
xbmc_m.log = lambda msg, level=0: None  # silenciar
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
os.makedirs(r"C:\Users\israe\_mw_test_profile_full", exist_ok=True)

PKG = r"C:\Users\israe\Desktop\Nueva App Kodi\plugin.video.mejorwolf\resources"
sys.path.insert(0, PKG)
import importlib
sw = importlib.import_module("lib.scraper_wolfmax")

# Pick queries: one recent series (likely in top-100) + arcane + comunidad
for q in ["the rookie", "arcane", "comunidad del anillo"]:
    print("="*70); print(f"QUERY: {q!r}")
    t0 = time.time()
    items = sw.search(q)
    dt = time.time() - t0
    print(f"  -> {len(items)} items en {dt:.1f}s")
    # Group by kind + show samples
    from collections import Counter
    kinds = Counter(it.get("kind") for it in items)
    print(f"  kinds: {dict(kinds)}")
    for it in items[:8]:
        print(f"    {it.get('kind'):8s} {it.get('url')} | {(it.get('title') or '')[:80]!r}")
    if len(items) > 8:
        print(f"    ... ({len(items)-8} mas)")
