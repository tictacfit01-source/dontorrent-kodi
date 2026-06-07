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
MAIN_TICK = 5               # ciclo del bucle principal (FA + keep-warm)
KB_POLL_GAP = 0.3           # sondeo del teclado remoto en su propio hilo:
                            # rapido para que el mando responda agil (~0.5s)
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
    "up": "Action(Up)",
    "down": "Action(Down)",
    "left": "Action(Left)",
    "right": "Action(Right)",
    "ok": "Action(Select)",
}
_HOME_URL = "plugin://plugin.video.mejorwolf/?action=home"


def _go_home():
    """Va a la portada del addon de forma ROBUSTA desde cualquier estado.
    Causa del 'se queda pillado': un dialogo abierto (barra de carga de una
    busqueda, notificacion) o estar reproduciendo bloqueaban el ActivateWindow.
    Solucion: cerrar dialogos primero y elegir la navegacion segun el estado."""
    try:
        # 1) Cerrar cualquier dialogo modal que pueda bloquear la navegacion.
        xbmc.executebuiltin("Dialog.Close(all,true)")
        xbmc.sleep(150)
        # 2) Si hay un video a pantalla completa, salir del reproductor primero.
        if xbmc.getCondVisibility("Player.HasVideo") and \
           xbmc.getCondVisibility("VideoPlayer.IsFullscreen"):
            xbmc.executebuiltin("Action(FullScreen)")
            xbmc.sleep(150)
        # 3) Si ya estamos en la ventana de Videos, navegar dentro (replace,
        #    fiable y sin acumular historial); si no, abrir Videos en la portada.
        if xbmc.getCondVisibility("Window.IsVisible(10025)"):
            xbmc.executebuiltin('Container.Update("%s",replace)' % _HOME_URL)
        else:
            xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % _HOME_URL)
        xbmc.log("[MejorWolf/service] Home -> portada del addon", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] home error: {e}", xbmc.LOGWARNING)


def _seek(seconds):
    """Salto relativo en la reproduccion (segundos, +/-) via JSON-RPC."""
    try:
        import json
        res = xbmc.executeJSONRPC(
            '{"jsonrpc":"2.0","id":1,"method":"Player.GetActivePlayers"}')
        players = (json.loads(res).get("result") or [])
        vid = next((p for p in players if p.get("type") == "video"),
                   players[0] if players else None)
        if not vid:
            return
        req = {"jsonrpc": "2.0", "id": 1, "method": "Player.Seek",
               "params": {"playerid": vid["playerid"],
                          "value": {"seconds": int(seconds)}}}
        xbmc.executeJSONRPC(json.dumps(req))
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] seek error: {e}", xbmc.LOGDEBUG)


# Espejo de la pantalla: ultima lista (indice -> item con su 'file')
_LAST_LIST = []


def _read_screen_and_push():
    """Lee la 'foto' de la pantalla ACTUAL (por su ruta) y la sube al relay.
    INSTANTANEO y siempre en sync: usa la ruta real en pantalla."""
    global _LAST_LIST
    try:
        from resources.lib import remote_kb as rkb
        path = xbmc.getInfoLabel("Container.FolderPath") or ""
        items = rkb.read_screen(path) if "plugin.video.mejorwolf" in path else []
        _LAST_LIST = items
        compact = [{"label": it.get("label", ""),
                    "poster": it.get("poster", ""),
                    "dir": bool(it.get("dir"))} for it in items]
        title = xbmc.getInfoLabel("Container.PluginCategory") or "MejorWolf"
        rkb.push_list(compact, title)
        xbmc.log(f"[MejorWolf/service] Lista empujada: {len(compact)} items "
                 f"[{path[-40:]}]", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] leer pantalla error: {e}", xbmc.LOGDEBUG)


def _open_index(i):
    """Abre (o reproduce) el elemento N de la ultima lista, como pulsar OK.
    Devuelve True si navego a una carpeta (hay nueva pantalla que reflejar)."""
    try:
        i = int(i)
        if not (0 <= i < len(_LAST_LIST)):
            return False
        it = _LAST_LIST[i]
        url = it.get("file") or ""
        if not url:
            return False
        if it.get("dir"):
            xbmc.executebuiltin('Container.Update("%s")' % url)
            return True
        xbmc.executebuiltin('PlayMedia("%s")' % url)
        return False
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] abrir item error: {e}", xbmc.LOGDEBUG)
    return False


def _push_after_nav(old_path):
    """Tras navegar, espera a que la carpeta en pantalla CAMBIE y a que su foto
    este lista, y la empuja. Robusto tanto si la carpeta es nueva (se pinta)
    como si viene de cache (cambia la ruta pero no se re-pinta)."""
    try:
        from resources.lib import remote_kb as rkb
        new_path = old_path
        for _ in range(30):          # esperar cambio de carpeta (~hasta 4.5s)
            xbmc.sleep(150)
            cur = xbmc.getInfoLabel("Container.FolderPath") or ""
            if cur and cur != old_path:
                new_path = cur
                break
        # esperar a que exista la foto de la nueva ruta (primera visita: se pinta)
        for _ in range(20):
            if rkb.read_screen(new_path):
                break
            xbmc.sleep(150)
        _read_screen_and_push()
    except Exception:
        pass


def _seek_to(minutes):
    """Salto ABSOLUTO al minuto indicado en la reproduccion (via JSON-RPC)."""
    try:
        import json
        m = int(minutes)
        res = xbmc.executeJSONRPC(
            '{"jsonrpc":"2.0","id":1,"method":"Player.GetActivePlayers"}')
        players = (json.loads(res).get("result") or [])
        vid = next((p for p in players if p.get("type") == "video"),
                   players[0] if players else None)
        if not vid:
            return
        req = {"jsonrpc": "2.0", "id": 1, "method": "Player.Seek",
               "params": {"playerid": vid["playerid"],
                          "value": {"time": {"hours": m // 60,
                                             "minutes": m % 60, "seconds": 0}}}}
        xbmc.executeJSONRPC(json.dumps(req))
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] seek_to error: {e}", xbmc.LOGDEBUG)


def _poll_remote_kb():
    """Sondea el Teclado Remoto y ejecuta lo que el movil haya enviado:
    busqueda, comandos, saltos, cruceta, o la vista Lista (espejo)."""
    try:
        from resources.lib import remote_kb as rkb
        events = rkb.poll(timeout=6)
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
            elif c == "list":
                _read_screen_and_push()
            elif c == "open":
                old_path = xbmc.getInfoLabel("Container.FolderPath") or ""
                if _open_index(ev.get("i")):
                    _push_after_nav(old_path)
            elif c == "home":
                _go_home()
            elif c == "seek_fwd":
                _seek(30)
            elif c == "seek_back":
                _seek(-10)
            elif c == "seekto":
                _seek_to(ev.get("min"))
            elif c in _KB_ACTIONS:
                xbmc.executebuiltin(_KB_ACTIONS[c])
            xbmc.sleep(120)   # pequeña separacion entre acciones
        return True
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] KB poll error: {e}", xbmc.LOGDEBUG)
    return False


def _warm_dt():
    """Pre-calienta la sesion Anubis de DonTorrent en el relay (1 peticion) para
    que la PRIMERA busqueda no tenga que resolverla. Asi DonTorrent sale ya en
    la primera, sin el 'despertando'."""
    try:
        base = _relay_base()
        if not base:
            return
        import requests
        requests.get(f"{base}/dtsearch", params={"q": "matrix"}, timeout=60)
        xbmc.log("[MejorWolf/service] DonTorrent precalentado", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] warm DT error: {e}", xbmc.LOGDEBUG)


def _kb_thread(monitor):
    """Hilo dedicado al Teclado Remoto: sondea rapido para que el mando vaya
    agil, sin que el bucle principal (FA/keep-warm) lo frene."""
    while not monitor.abortRequested():
        try:
            _poll_remote_kb()
        except Exception:
            pass
        if monitor.waitForAbort(KB_POLL_GAP):
            break


def main():
    monitor = xbmc.Monitor()
    xbmc.log("[MejorWolf/service] iniciado (keep-warm + notas FA + teclado "
             "remoto)", xbmc.LOGINFO)

    base = _relay_base()
    if base:
        _ping(base)

    import threading
    # Pre-calentar DonTorrent (Anubis) en segundo plano: 1a busqueda rapida.
    threading.Thread(target=_warm_dt, daemon=True).start()
    # Teclado Remoto en su propio hilo (sondeo rapido, respuesta agil).
    threading.Thread(target=_kb_thread, args=(monitor,), daemon=True).start()

    last_ping = time.time()
    next_fa = 0.0          # cuando podemos resolver la proxima nota FA
    consec_blocks = 0      # bloqueos seguidos (para el backoff exponencial)

    while not monitor.abortRequested():
        if monitor.waitForAbort(MAIN_TICK):
            break
        now = time.time()

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
