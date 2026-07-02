"""Teclado Remoto — escribir busquedas desde el movil.

El box tiene un CODIGO estable de 6 cifras. El movil abre la web del relay
(/kb, normalmente escaneando un QR que ya lleva el codigo), escribe la
busqueda y la envia. El servicio del addon sondea /kb/poll con ese codigo y
abre los resultados en la tele.
"""
import os
import json
import time
import random
import requests

# Sesion HTTP PERSISTENTE (keep-alive): reutiliza la conexion TCP/TLS con el
# relay en vez de abrir una nueva (handshake TLS) en CADA sondeo. El mando se
# sondea cada ~0.3-0.5s, asi que esto ahorra muchisima CPU/red en el box y
# evita que el reproductor sufra micro-cortes durante la peli.
_SESSION = requests.Session()

try:
    import xbmcvfs
    import xbmcaddon
    _PROFILE = xbmcvfs.translatePath(
        "special://profile/addon_data/plugin.video.mejorwolf/")
except Exception:
    _PROFILE = ""

_CODE_FILE = os.path.join(_PROFILE, "remote_code.txt") if _PROFILE else ""
_SCREENS_FILE = os.path.join(_PROFILE, "screens.json") if _PROFILE else ""


def _norm_path(p):
    return (p or "").rstrip("/")


def read_screen(path):
    """Lee la 'foto' de la pantalla con ESA ruta (instantaneo, sin re-buscar).
    Devuelve [{label, file, poster, dir}] o [] si no hay foto para esa ruta."""
    if not _SCREENS_FILE or not os.path.exists(_SCREENS_FILE):
        return []
    try:
        with open(_SCREENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        entry = data.get(_norm_path(path))
        if entry:
            return entry.get("items", [])
    except Exception:
        pass
    return []


def snap_mtime():
    """Fecha de modificacion del fichero de fotos (para detectar cambios)."""
    try:
        if _SCREENS_FILE and os.path.exists(_SCREENS_FILE):
            return os.path.getmtime(_SCREENS_FILE)
    except Exception:
        pass
    return 0


# El codigo NO cambia una vez creado -> se cachea en memoria. Antes se releia el
# fichero de disco en CADA llamada, y el mando llama ~3 veces/segundo 24/7:
# I/O constante que le roba CPU al reproductor sin aportar nada.
_CODE_CACHE = [""]


def get_code():
    """Codigo estable de 6 cifras de este box (se genera una vez y persiste)."""
    if _CODE_CACHE[0]:
        return _CODE_CACHE[0]
    if _CODE_FILE and os.path.exists(_CODE_FILE):
        try:
            with open(_CODE_FILE, "r", encoding="utf-8") as f:
                c = "".join(ch for ch in f.read() if ch.isdigit())[:6]
            if len(c) == 6:
                _CODE_CACHE[0] = c
                return c
        except Exception:
            pass
    c = f"{random.randint(0, 999999):06d}"
    try:
        os.makedirs(_PROFILE, exist_ok=True)
        with open(_CODE_FILE, "w", encoding="utf-8") as f:
            f.write(c)
    except Exception:
        pass
    _CODE_CACHE[0] = c
    return c


# La URL del relay casi nunca cambia -> cache con TTL corto (60s): se ahorra
# la lectura del setting + posible fallback a Supabase en cada sondeo, pero un
# cambio de URL en Ajustes se sigue cogiendo en <1 min sin reiniciar Kodi.
_RELAY_CACHE = {"url": "", "ts": 0.0}


def relay_base():
    now = time.time()
    if _RELAY_CACHE["url"] and (now - _RELAY_CACHE["ts"]) < 60:
        return _RELAY_CACHE["url"]
    try:
        from . import scraper_dontorrent as dt
        url = (dt._render_relay_url() or "").rstrip("/")
    except Exception:
        url = ""
    if url:
        _RELAY_CACHE["url"] = url
        _RELAY_CACHE["ts"] = now
    return url or _RELAY_CACHE["url"]


def web_url():
    base = relay_base()
    return f"{base}/kb?c={get_code()}" if base else ""


def qr_url():
    base = relay_base()
    return f"{base}/kb/qr?c={get_code()}" if base else ""


def push_list(items, title=""):
    """Sube al relay la lista (espejo) de la pantalla actual para que el movil
    la muestre. `items`: lista de dicts {label, poster, dir}."""
    base = relay_base()
    if not base:
        return
    try:
        _SESSION.post(f"{base}/kb/list",
                      json={"code": get_code(), "items": items, "title": title},
                      timeout=10)
    except Exception:
        pass


def push_now(np):
    """Sube al relay el estado de reproduccion actual.
    `np`: dict {title, elapsed, total, paused} o None para limpiar el panel."""
    base = relay_base()
    if not base:
        return
    try:
        _SESSION.post(f"{base}/kb/now",
                      json={"code": get_code(), "np": np}, timeout=8)
    except Exception:
        pass


def push_status(version, cont=None, diag=None):
    """Latido del box al relay: version del addon + (opcional) 'Continuar
    viendo' + (opcional) telemetria de reproduccion (diag)."""
    base = relay_base()
    if not base:
        return
    try:
        _SESSION.post(f"{base}/kb/status",
                      json={"code": get_code(), "v": version, "cont": cont,
                            "diag": diag},
                      timeout=8)
    except Exception:
        pass


def push_etjob(out):
    """Sube al relay el resultado de un trabajo de EliteTorrent pedido por el
    catalogo web (busqueda o resolucion de enlace). `out`: {job, op, items|link}."""
    base = relay_base()
    if not base:
        return
    try:
        body = dict(out)
        body["code"] = get_code()
        _SESSION.post(f"{base}/catjob/done", json=body, timeout=25)
    except Exception:
        pass


def poll(timeout=10):
    """Devuelve los eventos pendientes del movil (y los consume).

    Cada evento es un dict: {"q": "<busqueda>"} o {"c": "<comando>"}.
    Lista vacia si no hay nada o falla la conexion.
    """
    base = relay_base()
    if not base:
        return []
    try:
        r = _SESSION.get(f"{base}/kb/poll", params={"code": get_code()},
                         timeout=timeout)
        if r.status_code == 200:
            return r.json().get("events") or []
    except Exception:
        pass
    return []
