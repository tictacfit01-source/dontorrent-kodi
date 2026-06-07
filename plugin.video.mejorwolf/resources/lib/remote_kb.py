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

try:
    import xbmcvfs
    import xbmcaddon
    _PROFILE = xbmcvfs.translatePath(
        "special://profile/addon_data/plugin.video.mejorwolf/")
except Exception:
    _PROFILE = ""

_CODE_FILE = os.path.join(_PROFILE, "remote_code.txt") if _PROFILE else ""
_SNAP_FILE = os.path.join(_PROFILE, "last_screen.json") if _PROFILE else ""


def read_screen():
    """Lee la 'foto' de la ultima pantalla pintada por el addon. Devuelve la
    lista de items [{label, file, poster, dir}] (instantaneo, sin re-buscar)."""
    if not _SNAP_FILE or not os.path.exists(_SNAP_FILE):
        return []
    try:
        with open(_SNAP_FILE, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("items", [])
    except Exception:
        return []


def snap_mtime():
    """Fecha de modificacion de la 'foto' (para detectar cambios de pantalla)."""
    try:
        if _SNAP_FILE and os.path.exists(_SNAP_FILE):
            return os.path.getmtime(_SNAP_FILE)
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
        requests.post(f"{base}/kb/list",
                      json={"code": get_code(), "items": items, "title": title},
                      timeout=10)
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
        r = requests.get(f"{base}/kb/poll", params={"code": get_code()},
                         timeout=timeout)
        if r.status_code == 200:
            return r.json().get("events") or []
    except Exception:
        pass
    return []
