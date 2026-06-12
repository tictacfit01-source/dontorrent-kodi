"""Teclado Remoto — escribir busquedas desde el movil.

El box tiene un CODIGO estable de 6 cifras. El movil abre la web del relay
(/kb, normalmente escaneando un QR que ya lleva el codigo), escribe la
busqueda y la envia. El servicio del addon sondea /kb/poll con ese codigo y
abre los resultados en la tele.
"""
import os
import json
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


def get_code():
    """Codigo estable de 6 cifras de este box (se genera una vez y persiste)."""
    if _CODE_FILE and os.path.exists(_CODE_FILE):
        try:
            with open(_CODE_FILE, "r", encoding="utf-8") as f:
                c = "".join(ch for ch in f.read() if ch.isdigit())[:6]
            if len(c) == 6:
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
    return c


def relay_base():
    try:
        from . import scraper_dontorrent as dt
        return (dt._render_relay_url() or "").rstrip("/")
    except Exception:
        return ""


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


def push_status(version, cont=None):
    """Latido del box al relay: version del addon + (opcional) 'Continuar
    viendo' (cont = dict con title/a/ci/tb/u/elapsed/total)."""
    base = relay_base()
    if not base:
        return
    try:
        _SESSION.post(f"{base}/kb/status",
                      json={"code": get_code(), "v": version, "cont": cont},
                      timeout=8)
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
