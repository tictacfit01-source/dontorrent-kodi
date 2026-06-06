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

PING_INTERVAL = 300         # keep-warm relay: cada 5 min
TICK = 3                    # ciclo base del servicio (s)
FA_GAP = 6                  # separacion entre notas FA (~10/min: ritmo humano)
# Backoff EXPONENCIAL ante bloqueos de FA. Reintentar a menudo cuando FA ya
# te bloquea solo PERPETUA el bloqueo (no deja recuperar la IP). Por eso al
# primer bloqueo esperamos 10 min, y se duplica con cada bloqueo seguido hasta
# 2h. Asi la IP tiene tiempo real de recuperarse.
FA_BACKOFF_BASE = 600       # 10 min al primer bloqueo
FA_BACKOFF_MAX = 7200       # tope 2h
FA_GAP_EMPTY = 30           # cola vacia: revisar en 30s


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


# Comando del movil -> builtin de Kodi (acciones de un disparo)
_KB_ACTIONS = {
    "back": "Action(Back)",
    "playpause": "Action(PlayPause)",
    "stop": "Action(Stop)",
    "volup": "Action(VolumeUp)",
    "voldown": "Action(VolumeDown)",
    "mute": "Mute",
    "subs": "Action(ShowSubtitles)",
}
_ADDON_HOME = 'ActivateWindow(videos,"plugin://plugin.video.mejorwolf/",return)'


def _poll_remote_kb():
    """Sondea el Teclado Remoto y ejecuta lo que el movil haya enviado:
    una busqueda (la abre en la tele) o comandos (play/pausa/stop/vol/...)."""
    try:
        from resources.lib import remote_kb as rkb
        events = rkb.poll(timeout=8)
        if not events:
            return False
        from urllib.parse import quote
        for ev in events:
            q = (ev.get("q") or "").strip()
            c = (ev.get("c") or "").strip()
            if q:
                url = ("plugin://plugin.video.mejorwolf/?action=remote_search"
                       "&q=" + quote(q))
                xbmc.log(f"[MejorWolf/service] teclado remoto -> buscar '{q}'",
                         xbmc.LOGINFO)
                xbmc.executebuiltin('ActivateWindow(videos,"%s",return)' % url)
            elif c == "home":
                xbmc.executebuiltin(_ADDON_HOME)
            elif c in _KB_ACTIONS:
                xbmc.executebuiltin(_KB_ACTIONS[c])
            xbmc.sleep(150)   # pequeña separacion entre acciones
        return True
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] KB poll error: {e}", xbmc.LOGDEBUG)
    return False


def main():
    monitor = xbmc.Monitor()
    xbmc.log("[MejorWolf/service] iniciado (keep-warm + notas FA + teclado "
             "remoto)", xbmc.LOGINFO)

    base = _relay_base()
    if base:
        _ping(base)
    last_ping = time.time()
    next_fa = 0.0          # cuando podemos resolver la proxima nota FA
    consec_blocks = 0      # bloqueos seguidos (para el backoff exponencial)

    while not monitor.abortRequested():
        if monitor.waitForAbort(TICK):
            break
        now = time.time()

        # 0) Teclado Remoto: si el movil envio una busqueda, abrirla ya
        _poll_remote_kb()

        # 1) keep-warm relay
        if now - last_ping >= PING_INTERVAL:
            base = _relay_base()
            if base:
                _ping(base)
            last_ping = now

        # 2) notas FilmAffinity, espaciadas (ritmo humano) con backoff
        if now >= next_fa:
            status = _drain_fa()
            if status == "blocked":
                consec_blocks += 1
                wait = min(FA_BACKOFF_MAX,
                           FA_BACKOFF_BASE * (2 ** (consec_blocks - 1)))
                next_fa = now + wait
                xbmc.log(f"[MejorWolf/service] FA bloqueado (x{consec_blocks}); "
                         f"reintento en {int(wait // 60)} min", xbmc.LOGINFO)
            elif status == "ok":
                consec_blocks = 0
                next_fa = now + FA_GAP
            else:   # 'empty'
                consec_blocks = 0
                next_fa = now + FA_GAP_EMPTY

    xbmc.log("[MejorWolf/service] detenido", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
