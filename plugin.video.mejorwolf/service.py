"""Servicio en segundo plano de MejorWolf.

Dos tareas, mientras Kodi esta abierto:

1) KEEP-WARM del relay de Render. El free tier se duerme tras ~15 min de
   inactividad y la 1a busqueda tras dormir tarda ~50s en arrancar. Pingueando
   cada 5 min, el relay no se duerme durante la sesion -> DonTorrent rapido.

2) NOTAS de FilmAffinity a RITMO HUMANO. FilmAffinity bloquea rafagas de
   peticiones (incluso desde IP residencial), asi que el plugin NO consulta al
   pintar listas: solo encola los titulos que faltan. Aqui los resolvemos uno
   a uno, espaciados, de forma que la cobertura crece sin disparar el anti-bot.
   Lo que se resuelve queda en cache y aparece en las siguientes navegaciones.

No depende de GitHub ni de cuentas externas: corre dentro de Kodi.
"""
import time
import xbmc

PING_INTERVAL = 300    # keep-warm relay: cada 5 min
TICK = 3               # ciclo base del servicio (s)
FA_GAP = 6             # separacion entre notas FA (~10/min: ritmo humano)
FA_GAP_BLOCKED = 120   # si FA bloquea, espera 2 min antes de reintentar


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


def _drain_fa():
    """Resuelve un titulo encolado. Devuelve el estado ('ok'/'empty'/'blocked')."""
    try:
        from resources.lib import filmaffinity as fa
        return fa.drain_one()
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] FA drain error: {e}", xbmc.LOGDEBUG)
        return "empty"


def main():
    monitor = xbmc.Monitor()
    xbmc.log("[MejorWolf/service] iniciado (keep-warm + notas FA)",
             xbmc.LOGINFO)

    base = _relay_base()
    if base:
        _ping(base)
    last_ping = time.time()
    next_fa = 0.0          # cuando podemos resolver la proxima nota FA

    while not monitor.abortRequested():
        if monitor.waitForAbort(TICK):
            break
        now = time.time()

        # 1) keep-warm relay
        if now - last_ping >= PING_INTERVAL:
            base = _relay_base()
            if base:
                _ping(base)
            last_ping = now

        # 2) notas FilmAffinity, espaciadas (ritmo humano)
        if now >= next_fa:
            status = _drain_fa()
            if status == "blocked":
                next_fa = now + FA_GAP_BLOCKED   # FA capa la IP: esperar
            elif status == "ok":
                next_fa = now + FA_GAP           # resuelta: pequeña pausa
            # 'empty' -> no fijamos espera larga; reintenta en el proximo tick

    xbmc.log("[MejorWolf/service] detenido", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
