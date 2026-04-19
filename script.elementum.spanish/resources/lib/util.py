"""Small helpers: logging + settings + parent-addon access."""
import xbmc
import xbmcaddon

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")

try:
    PARENT = xbmcaddon.Addon("plugin.video.dontorrent")
except Exception:
    PARENT = None


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f"[{ADDON_ID}] {msg}", level)


def debug(msg):
    if ADDON.getSetting("debug_log") == "true":
        log(msg, xbmc.LOGINFO)


def setting(key, default=None, cast=str):
    val = ADDON.getSetting(key)
    if val == "" or val is None:
        return default
    if cast is bool:
        return val == "true"
    if cast is int:
        try:
            return int(val)
        except ValueError:
            return default
    return val


def parent_setting(key, default=None):
    """Read a setting from plugin.video.dontorrent if installed."""
    if not PARENT:
        return default
    try:
        v = PARENT.getSetting(key)
        return v if v else default
    except Exception:
        return default


def is_provider_enabled(name):
    return setting(f"provider_{name}", default=True, cast=bool)
