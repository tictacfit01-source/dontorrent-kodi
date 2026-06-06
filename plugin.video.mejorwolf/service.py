"""Servicio en segundo plano de MejorWolf.

Mantiene CALIENTE el relay de Render mientras Kodi esta abierto. El free
tier de Render se duerme tras ~15 min de inactividad y la primera busqueda
tras dormir tarda ~50s en arrancar (por eso DonTorrent "no salia" o tardaba
30s). Pingueando cada 5 min, el relay nunca se duerme durante la sesion, asi
DonTorrent responde en 2-3s.

No depende de GitHub Actions ni de ninguna cuenta externa: corre dentro de
Kodi, justo cuando el usuario lo va a usar.
"""
import xbmc

PING_INTERVAL = 300   # 5 min (Render duerme a los 15 -> margen de sobra)


def _relay_base():
    try:
        from resources.lib import scraper_dontorrent as dt
        return dt._render_relay_url()
    except Exception:
        return ""


def _ping(base):
    try:
        import requests
        r = requests.get(f"{base}/", timeout=60,
                         headers={"User-Agent": "MejorWolf/service"})
        xbmc.log(f"[MejorWolf/service] keep-warm ping -> HTTP "
                 f"{r.status_code}", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] ping error: {e}", xbmc.LOGDEBUG)


def main():
    monitor = xbmc.Monitor()
    xbmc.log("[MejorWolf/service] iniciado (keep-warm relay)", xbmc.LOGINFO)
    # Primer ping al arrancar Kodi para despertar el relay cuanto antes.
    base = _relay_base()
    if base:
        _ping(base)
    while not monitor.abortRequested():
        if monitor.waitForAbort(PING_INTERVAL):
            break
        base = _relay_base()
        if base:
            _ping(base)
    xbmc.log("[MejorWolf/service] detenido", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
