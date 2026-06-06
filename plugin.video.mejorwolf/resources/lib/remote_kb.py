"""Teclado Remoto — escribir busquedas desde el movil.

El box tiene un CODIGO estable de 6 cifras. El movil abre la web del relay
(/kb, normalmente escaneando un QR que ya lleva el codigo), escribe la
busqueda y la envia. El servicio del addon sondea /kb/poll con ese codigo y
abre los resultados en la tele.
"""
import os
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


def poll(timeout=10):
    """Devuelve la busqueda pendiente del movil (y la consume), o ''."""
    base = relay_base()
    if not base:
        return ""
    try:
        r = requests.get(f"{base}/kb/poll", params={"code": get_code()},
                         timeout=timeout)
        if r.status_code == 200:
            return (r.json().get("query") or "").strip()
    except Exception:
        pass
    return ""
