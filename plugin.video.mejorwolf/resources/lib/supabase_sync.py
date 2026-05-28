"""
Supabase Sync — configuracion centralizada para addons MejorWolf/DonTorrent.

Lee dominios activos, version del addon, y config general desde una tabla
Supabase (mw_config) usando la REST API publica (PostgREST). No requiere
librerias externas mas alla de `requests`.

Uso tipico:
    from . import supabase_sync as sb
    domain = sb.get_domain("wolfmax")          # "wolfmax4k.com"
    cfg    = sb.get_config("seriesly")         # {"enabled": True, ...}
    sb.check_addon_update("addon_mejorwolf", "0.9.3")  # notifica si hay nueva
"""

import time
import requests

try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    _KODI = True
except ImportError:
    _KODI = False

# ---------------------------------------------------------------------------
# Configuracion — hardcoded para zero-config
# ---------------------------------------------------------------------------

_SUPABASE_URL = "https://yddgjpjyldgvuswcsxci.supabase.co"
_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlkZGdqcGp5bGRndnVzd2NzeGNpIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NzgyNTIwMzAsImV4cCI6MjA5MzgyODAzMH0."
    "bpIkjXUowHhhJKz_HVFkGj1WogD5dpyi_JGL2yLOYl0"
)

def _addon():
    if _KODI:
        return xbmcaddon.Addon()
    return None

def _setting(key, default=""):
    a = _addon()
    if a:
        return (a.getSetting(key) or "").strip() or default
    return default

def _log(msg):
    if _KODI:
        xbmc.log(f"[SupabaseSync] {msg}", xbmc.LOGINFO)

# ---------------------------------------------------------------------------
# Cache en memoria (evita peticiones repetidas en la misma sesion)
# ---------------------------------------------------------------------------

_cache = {}         # key -> (value, timestamp)
_CACHE_TTL = 300    # 5 minutos

def _cached(key):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return val
    return None

def _set_cache(key, val):
    _cache[key] = (val, time.time())

def invalidate_cache():
    """Limpia toda la cache en memoria."""
    _cache.clear()

# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

def _headers():
    anon_key = _setting("supabase_anon_key", _SUPABASE_ANON_KEY)
    return {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Accept": "application/json",
    }

def _base_url():
    url = _setting("supabase_url", _SUPABASE_URL).rstrip("/")
    if url and not url.startswith("http"):
        url = "https://" + url
    return url

def get_config(key):
    """Lee una fila de mw_config por su key. Devuelve el campo `value` (dict)
    o None si no existe o falla la conexion."""
    cached = _cached(f"config:{key}")
    if cached is not None:
        return cached

    base = _base_url()
    if not base:
        _log("Supabase URL no configurada")
        return None

    url = f"{base}/rest/v1/mw_config?key=eq.{key}&select=value"
    try:
        r = requests.get(url, headers=_headers(), timeout=8)
        if r.status_code == 200:
            rows = r.json()
            if rows and isinstance(rows, list) and len(rows) > 0:
                val = rows[0].get("value")
                _set_cache(f"config:{key}", val)
                _log(f"Config '{key}' cargada desde Supabase")
                return val
        else:
            _log(f"Supabase HTTP {r.status_code} para key={key}")
    except Exception as e:
        _log(f"Error Supabase: {e}")
    return None

def get_all_config():
    """Lee toda la tabla mw_config. Devuelve dict {key: value}."""
    cached = _cached("config:__all__")
    if cached is not None:
        return cached

    base = _base_url()
    if not base:
        return {}

    url = f"{base}/rest/v1/mw_config?select=key,value"
    try:
        r = requests.get(url, headers=_headers(), timeout=8)
        if r.status_code == 200:
            rows = r.json()
            result = {row["key"]: row["value"] for row in rows}
            _set_cache("config:__all__", result)
            return result
    except Exception as e:
        _log(f"Error Supabase get_all: {e}")
    return {}

# ---------------------------------------------------------------------------
# Domain resolution
# ---------------------------------------------------------------------------

def get_domain(source, fallback=None):
    """Devuelve el dominio activo para una fuente ('dontorrent', 'wolfmax').

    Consulta Supabase primero; si falla, devuelve el fallback hardcodeado.
    """
    cfg = get_config(source)
    if cfg and isinstance(cfg, dict):
        domain = cfg.get("domain", "")
        if domain:
            return domain
    return fallback or ""

def get_fallback_domains(source):
    """Devuelve la lista de dominios fallback para una fuente."""
    cfg = get_config(source)
    if cfg and isinstance(cfg, dict):
        fb = cfg.get("fallbacks", [])
        if isinstance(fb, list):
            return fb
    return []

# ---------------------------------------------------------------------------
# Addon update check
# ---------------------------------------------------------------------------

def check_addon_update(config_key, current_version):
    """Comprueba si hay una version mas nueva del addon en Supabase.

    config_key: 'addon_mejorwolf' o 'addon_dontorrent'
    current_version: version actual del addon (ej. '0.9.3')

    Si hay nueva version, muestra notificacion en Kodi.
    Devuelve (new_version, changelog) o (None, None).
    """
    cfg = get_config(config_key)
    if not cfg or not isinstance(cfg, dict):
        return None, None

    remote_ver = cfg.get("version", "")
    if not remote_ver:
        return None, None

    # Comparacion simple de versiones (split por puntos)
    def ver_tuple(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except (ValueError, AttributeError):
            return (0,)

    if ver_tuple(remote_ver) > ver_tuple(current_version):
        changelog = cfg.get("changelog", "")
        _log(f"Nueva version disponible: {remote_ver} (actual: {current_version})")

        if _KODI:
            xbmcgui.Dialog().notification(
                "Actualizacion disponible",
                f"v{remote_ver}: {changelog[:60]}",
                xbmcgui.NOTIFICATION_INFO,
                5000,
            )
        return remote_ver, changelog

    return None, None

# ---------------------------------------------------------------------------
# Series.ly config
# ---------------------------------------------------------------------------

def get_seriesly_config():
    """Devuelve la config de series.ly desde Supabase, o defaults."""
    cfg = get_config("seriesly")
    if cfg and isinstance(cfg, dict):
        return cfg
    return {"enabled": False, "base_url": "https://series.ly"}


def get_seriesly_cookie():
    """Lee la cookie de sesion de Series.ly desde Supabase.

    El script sync_sly_cookie.py (ejecutado en PC) sube la cookie a
    Supabase bajo key='seriesly_cookie'. El addon en el TV Box la lee
    de aqui para autenticarse sin necesidad de captcha.

    Devuelve el valor de la cookie (string) o None.
    """
    cfg = get_config("seriesly_cookie")
    if cfg and isinstance(cfg, dict):
        cookie = cfg.get("cookie", "")
        if cookie:
            _log("Cookie Series.ly obtenida de Supabase")
            return cookie
    return None

# ---------------------------------------------------------------------------
# Relay URL
# ---------------------------------------------------------------------------

def get_relay_url():
    """Devuelve la URL del Render relay desde Supabase."""
    cfg = get_config("relay")
    if cfg and isinstance(cfg, dict):
        return cfg.get("url", "")
    return ""

# ---------------------------------------------------------------------------
# Update config (para uso desde scripts de admin, no desde Kodi)
# ---------------------------------------------------------------------------

def update_config(key, value_dict):
    """Actualiza un valor en mw_config. Requiere service_role key.

    Solo para uso administrativo (script local, no desde Kodi).
    """
    base = _base_url()
    if not base:
        return False

    import json
    url = f"{base}/rest/v1/mw_config?key=eq.{key}"
    headers = _headers()
    headers["Content-Type"] = "application/json"
    headers["Prefer"] = "return=minimal"

    try:
        r = requests.patch(
            url, headers=headers, timeout=10,
            json={"value": value_dict},
        )
        if r.status_code in (200, 204):
            invalidate_cache()
            _log(f"Config '{key}' actualizada")
            return True
        _log(f"Error actualizando config: HTTP {r.status_code}")
    except Exception as e:
        _log(f"Error update_config: {e}")
    return False
