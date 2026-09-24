"""Servicio en segundo plano de MejorWolf.

Dos tareas, mientras Kodi esta abierto:

1) KEEP-WARM del relay de Render. El free tier se duerme tras ~15 min de
   inactividad y la 1a busqueda tras dormir tarda ~50s en arrancar. Pingueando
   cada 5 min, el relay no se duerme durante la sesion -> DonTorrent rapido.

2) TECLADO/MANDO REMOTO: sondea el relay (/kb/poll) y ejecuta las ordenes del
   movil (buscar, navegar, reproducir, controlar el reproductor), sube la "foto"
   de la pantalla y el "Estas viendo", y manda el latido de estado.

No depende de GitHub ni de cuentas externas: corre dentro de Kodi.
"""
import re
import time
import xbmc

PING_INTERVAL = 300         # keep-warm relay: cada 5 min
MAIN_TICK = 5               # ciclo del bucle principal (keep-warm)
KB_POLL_GAP = 0.3           # sondeo del teclado remoto al NAVEGAR: agil
KB_POLL_GAP_PLAYING = 0.5   # al REPRODUCIR: un pelin mas espaciado para no
                            # robarle CPU/red al reproductor (evita micro-cortes)
KB_POLL_GAP_IDLE = 1.2      # SIN movil delante (hint "fast" del relay a False):
                            # sondeo espaciado -> ~4x menos trafico 24/7 (Render
                            # solo da 5 GB/mes de egress; el 19/07/2026 se agoto
                            # y suspendio el servicio). En cuanto el movil abre
                            # la web vuelve a 0.3s en <=1.2s.
NOW_GAP = 3                 # cada cuanto subimos "Estas viendo" (la web interpola
                            # los segundos localmente, asi que se ve igual de fino)
CONT_GAP = 15               # cada cuanto guardamos la posicion de 'Continuar'
HEARTBEAT_GAP = 30          # latido de estado (tele conectada + Continuar)


def _relay_base():
    try:
        from resources.lib import scraper_dontorrent as dt
        return dt._render_relay_url()
    except Exception:
        return ""


def _ping(base):
    try:
        import requests
        # /ping (~60 B), NUNCA / (la pagina entera, ~80 KB): este keep-warm
        # corre 24/7 y con / gastaba ~0,7 GB/mes de egress por caja.
        r = requests.get(f"{base}/ping", timeout=60,
                         headers={"User-Agent": "MejorWolf/service"})
        xbmc.log(f"[MejorWolf/service] keep-warm ping -> HTTP "
                 f"{r.status_code}", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] ping error: {e}", xbmc.LOGDEBUG)


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
    # Subtitulos y audio: lo que mas se echa en falta viendo series (poner los
    # subtitulos o pasar el audio a version original sin levantarse del sofa).
    "subs": "Action(ShowSubtitles)",        # quitar/poner los subtitulos
    "subsnext": "Action(NextSubtitle)",     # otra pista de subtitulos
    "audionext": "Action(AudioNextLanguage)",  # otra pista de audio
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


def _item_ref(file_url):
    """Referencia de reproduccion de un item (para Compartir/badge), sacada de
    su URL plugin://. Devuelve {a:'dt',c,tb} o {a:'pl',u} o None si no es
    reproducible directo (carpeta/navegacion)."""
    try:
        from urllib.parse import urlparse, parse_qs
        if not file_url or "plugin.video.mejorwolf" not in file_url:
            return None
        qs = parse_qs(urlparse(file_url).query)
        action = (qs.get("action") or [""])[0]
        if action == "dt_play":
            cid = (qs.get("content_id") or [""])[0]
            tb = (qs.get("tabla") or [""])[0]
            if cid and tb:
                return {"a": "dt", "c": cid, "tb": tb}
        elif action == "play":
            u = (qs.get("torrent") or [""])[0]
            if u:
                return {"a": "pl", "u": u}
    except Exception:
        pass
    return None


def _read_screen_and_push():
    """Lee la 'foto' de la pantalla ACTUAL (por su ruta) y la sube al relay.
    INSTANTANEO y siempre en sync: usa la ruta real en pantalla."""
    global _LAST_LIST
    try:
        from resources.lib import remote_kb as rkb
        path = xbmc.getInfoLabel("Container.FolderPath") or ""
        items = rkb.read_screen(path) if "plugin.video.mejorwolf" in path else []
        _LAST_LIST = items
        compact = []
        for it in items:
            ci = {"label": it.get("label", ""),
                  "poster": it.get("poster", ""),
                  "dir": bool(it.get("dir")),
                  "rating": it.get("rating", 0)}
            ref = _item_ref(it.get("file", ""))
            if ref:
                ci["ref"] = ref
            compact.append(ci)
        title = xbmc.getInfoLabel("Container.PluginCategory") or "MejorWolf"
        rkb.push_list(compact, title)
        xbmc.log(f"[MejorWolf/service] Lista empujada: {len(compact)} items "
                 f"[{path[-40:]}]", xbmc.LOGINFO)
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] leer pantalla error: {e}", xbmc.LOGDEBUG)


def _open_index(i, label=""):
    """Abre (o reproduce) el elemento N de la ultima lista, como pulsar OK.
    VERIFICA la etiqueta para no abrir lo que no es si la pantalla cambio
    justo al tocar (modo en vivo). Devuelve True si navego a una carpeta."""
    try:
        i = int(i)
        it = None
        if 0 <= i < len(_LAST_LIST):
            cand = _LAST_LIST[i]
            if not label or cand.get("label") == label:
                it = cand
        if it is None and label:
            # el indice no casa (la pantalla cambio): buscar por etiqueta unica
            matches = [x for x in _LAST_LIST if x.get("label") == label]
            if len(matches) == 1:
                it = matches[0]
        if it is None:
            xbmc.log("[MejorWolf/service] abrir: la lista cambio, ignorado "
                     "(no abro lo que no es)", xbmc.LOGINFO)
            _read_screen_and_push()   # re-sincronizar el movil
            return False
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
            if q and not c:
                url = ("plugin://plugin.video.mejorwolf/?action=remote_search"
                       "&q=" + quote(q))
                xbmc.log(f"[MejorWolf/service] teclado remoto -> buscar '{q}'",
                         xbmc.LOGINFO)
                xbmc.executebuiltin('ActivateWindow(videos,"%s",return)' % url)
            elif c == "list":
                _read_screen_and_push()
            elif c == "open":
                old_path = xbmc.getInfoLabel("Container.FolderPath") or ""
                if _open_index(ev.get("i"), ev.get("label", "")):
                    _push_after_nav(old_path)
            elif c == "play_ref":
                _play_ref(ev)
            elif c == "etjob":
                _do_etjob(ev)   # ya lanza su propio hilo (no bloquea el sondeo)
            elif c == "wfidx":
                # el relay se ha quedado sin indice de WolfMax (deploy) y lo pide
                import threading as _th2
                _th2.Thread(target=_push_wf_index, daemon=True).start()
            elif c == "home":
                _go_home()
            elif c == "seek_fwd":
                _seek(30)
            elif c == "seek_back":
                _seek(-10)
            elif c == "seekto":
                _seek_to(ev.get("min"))
            elif c == "codigo_nuevo":
                _codigo_nuevo(ev.get("nuevo"))
            elif c in _KB_ACTIONS:
                xbmc.executebuiltin(_KB_ACTIONS[c])
            xbmc.sleep(120)   # pequeña separacion entre acciones
        return True
    except Exception as e:
        xbmc.log(f"[MejorWolf/service] KB poll error: {e}", xbmc.LOGDEBUG)
    return False


_FUENTE_HOSTS = {"wf": ("wolfmax4k.com",), "et": ("elitetorrent.com",)}
_CODIGOS_CAIDA = (521, 522, 523, 524, 525, 526, 530)


def _salud_en(out, t0):
    """Añade al resultado de un trabajo lo que la caja VIO de WolfMax y
    EliteTorrent mientras lo hacia (ver http_session._salud): `caidas` si su
    web dio un 52x de Cloudflare (su servidor no contesta) y `vivas` si
    contesto bien. El relay no lo puede ver desde su IP (24-09: las dos webs le
    ponen un reto antes de intentar nada) y con esto se entera -- tambien de
    cuando vuelven -- sin una sola peticion de mas."""
    try:
        from resources.lib import http_session as _hs
        vistos = _hs.salud_desde(t0)
    except Exception:
        return
    caidas, vivas = [], []
    for src, hosts in _FUENTE_HOSTS.items():
        sts = [st for h, st in vistos.items() if any(h.endswith(x) for x in hosts)]
        if any(200 <= s < 400 for s in sts):
            vivas.append(src)
        elif any(s in _CODIGOS_CAIDA for s in sts):
            caidas.append(src)
    if caidas:
        out["caidas"] = caidas
    if vivas:
        out["vivas"] = vivas


def _src_mod(src):
    """Modulo scraper para una fuente del catalogo (et/dx/wf)."""
    from resources.lib import scraper_elitetorrent as et
    from resources.lib import scraper_divxtotal as dx
    from resources.lib import scraper_wolfmax as wf
    return {"et": et, "dx": dx, "wf": wf}.get(src)


def _src_item_compact(it, src):
    """Mapea un item de cualquier scraper al formato del catalogo web (la web
    enriquece luego con TMDB). Peliculas y series; las series del box abren sus
    episodios via op=episodes (la web usa la url de la ficha)."""
    k = (it.get("kind") or "movie")
    kind = "serie" if (k.startswith("tvshow") or k == "serie") else "movie"
    return {"title": it.get("title", ""), "kind": kind, "source": src,
            "url": it.get("url", ""), "content_id": it.get("url", ""),
            "thumb": it.get("thumb") or it.get("image") or None,
            "quality": it.get("quality") or "", "tabla": src}


def _src_episodes(src, url):
    """Episodios de una serie de una fuente-box. Devuelve {title, episodes:[{
    label, season, episode, quality, link}]}. El link (magnet/.torrent) ya sirve
    para reproducir directo (play_ref a='pl')."""
    mod = _src_mod(src)
    if not mod or not url:
        return {"title": "", "episodes": []}
    import re as _re
    eps = []
    try:
        if src == "dx":
            d = mod.detail(url) or {}
            title = d.get("title") or ""
            for dl in d.get("downloads", []):
                link = dl.get("torrent_url")
                if not link:
                    continue
                s, e = dl.get("season"), dl.get("episode")
                label = ("%dx%02d" % (s, e)) if (s and e) else (
                    dl.get("label") or "Episodio")
                eps.append({"label": label, "season": s or 0,
                            "episode": e or 0, "quality": dl.get("quality") or "",
                            "link": link, "content_id": link})
            return {"title": title, "episodes": eps}
        if src == "wf":
            import concurrent.futures as _cf
            # El titulo (detail) y el slug de la serie son dos paginas
            # distintas: se piden A LA VEZ para no encadenar esperas.
            with _cf.ThreadPoolExecutor(max_workers=2) as _ex0:
                _f_det = _ex0.submit(lambda: mod.detail(url) or {})
                _f_slug = _ex0.submit(lambda: mod.serie_slug(url))
                try:
                    d0 = _f_det.result()
                except Exception:
                    d0 = {}
                try:
                    _slug = _f_slug.result()
                except Exception:
                    _slug = ""
            t0 = (d0.get("title") or "")
            base = ""
            try:
                base = mod._strip_show_markers(t0) or ""
            except Exception:
                base = ""
            if not base:
                base = t0.split(" - ")[0].strip() or t0
            # LA VIA BUENA: las paginas de la serie dan TODAS las temporadas y
            # todas las calidades en menos de un segundo. Si rinde, se acabo:
            # ni indice (que puede tardar 40 s) ni pedir la ficha de cada
            # capitulo (la web resuelve el enlace al pulsarlo, como ya hace con
            # los capitulos que vienen de una busqueda).
            _comp = []
            if _slug:
                try:
                    _comp = mod.episodios_completos("", _slug) or []
                except Exception as _e0:
                    xbmc.log("[MejorWolf/service] wf completos: %s" % _e0,
                             xbmc.LOGWARNING)
            if len(_comp) >= 3:
                for c in _comp:
                    ss, ee = c.get("season") or 0, c.get("episode") or 0
                    eps.append({"label": "%dx%02d" % (ss, ee),
                                "season": ss, "episode": ee,
                                "quality": c.get("quality") or "",
                                "url": c.get("url"),
                                "content_id": c.get("url"),
                                "src": "wf"})
                eps.sort(key=lambda x: (x["season"], x["episode"]))
                xbmc.log("[MejorWolf/service] wf ficha rapida: %d caps" % len(eps),
                         xbmc.LOGINFO)
                return {"title": base, "episodes": eps}
            # LA SERIE COMPLETA, JUNTANDO CALIDADES. WolfMax publica cada
            # capitulo en varias y NINGUNA las tiene todas (medido en "La
            # maldicion de Widows Bay": el 1x06 solo esta en 4K, y el 1x02 y el
            # 1x07 no estan en 4K pero si en 1080p). Antes la lista salia del
            # indice y quedaban huecos; ahora sale de las paginas de la serie,
            # que renderizan TODOS sus capitulos, y cada uno se queda con la
            # mejor calidad que exista.
            # LAS DOS VIAS, QUE NINGUNA BASTA SOLA (medido):
            #   paginas de la serie -> todas las CALIDADES, solo lo reciente
            #     ("Widows Bay" entera: el 1x02 y el 1x07 solo estan en 1080p)
            #   indice/catalogo     -> el HISTORICO, solo la calidad rastreada
            #     ("Silo": sin esto se quedaba en 10 capitulos de 18)
            # OJO: las dos vias dan la MISMA pagina con distinta grafia (una
            # con "www." y otra sin el), asi que se comparan normalizadas o se
            # pide cada capitulo dos veces.
            _clave = getattr(mod, "_wf_url_clave", lambda z: (z or "").lower())
            calidad_de = {}
            vistas = set()
            urls = []

            def _anade(u, q=""):
                if not u:
                    return
                k = _clave(u)
                if k in vistas:
                    if q and not calidad_de.get(k):
                        calidad_de[k] = q
                    return
                vistas.add(k)
                urls.append(u)
                if q:
                    calidad_de[k] = q

            _anade(url)
            try:
                for c in (mod.episodios_completos(url) or []):
                    _anade(c.get("url"), c.get("quality") or "")
            except Exception as ex1:
                xbmc.log("[MejorWolf/service] wf completos: %s" % ex1,
                         xbmc.LOGWARNING)
            try:
                for it in (mod.search(base) or []):
                    _anade(it.get("url"))
            except Exception as ex2:
                xbmc.log("[MejorWolf/service] wf eps search: %s" % ex2,
                         xbmc.LOGWARNING)
            urls = urls[:70]          # una serie larga entera, por las dos vias

            _WFQR = {"4K": 4, "1080p": 3, "720p": 2, "480p": 1}

            def _wfrank(q):
                return _WFQR.get(q or "", 0)

            def _wfq(u):
                # Solo el SEGMENTO de la ruta: buscar "720" en todo el path
                # colaba con los ids (/online/172021 daba "720p") y, como la
                # calidad decide que version se queda, podia ganar la peor.
                u = (u or "").lower().split("://", 1)[-1]
                trozos = [t for t in u.split("/") if t][1:2]
                seg = trozos[0] if trozos else ""
                if "4k" in seg or "2160" in seg:
                    return "4K"
                if "1080" in seg:
                    return "1080p"
                if "720" in seg or seg.endswith("-hd"):
                    return "720p"
                if "480" in seg:
                    return "480p"
                return ""

            def _wfone(u):
                try:
                    dd = mod.detail(u) or {}
                    tt = dd.get("title") or ""
                    ss, ee = mod._parse_season_episode(tt)
                    dls = dd.get("downloads") or dd.get("links") or []
                    lk = (dls[0].get("torrent_url") or dls[0].get("magnet")
                          if dls else "") or ""
                    return (ss, ee, lk, tt, u)
                except Exception:
                    return None
            # PRESUPUESTO POR TIEMPO, no por numero de fichas: el navegador
            # corta a los 26 s y el relay reparte el trabajo a otras casas si la
            # primera tarda. Mejor media lista a tiempo que la lista entera
            # cuando ya no la espera nadie.
            _tope = time.time() + 8.0
            with _cf.ThreadPoolExecutor(max_workers=6) as _ex:
                _futs = [_ex.submit(_wfone, u) for u in urls]
                for _f in _futs:
                    if time.time() > _tope:
                        _f.cancel()
                        continue
                    try:
                        r = _f.result(timeout=max(0.1, _tope - time.time()))
                    except Exception:
                        continue
                    if not r:
                        continue
                    ss, ee, lk, tt, u = r
                    if not lk:
                        continue
                    label = ("%dx%02d" % (ss, ee)) if (ss and ee) else (
                        (tt or "Episodio")[:40])
                    q = calidad_de.get(_clave(u)) or _wfq(u)
                    nuevo = {"label": label, "season": ss or 0,
                             "episode": ee or 0, "quality": q,
                             "link": lk, "content_id": lk}
                    # Repetido: gana la MEJOR CALIDAD, no el primero que llegue
                    # (venian de dos vias y en hilos: era una loteria).
                    viejo = next((x for x in eps if x["label"] == label), None)
                    if viejo is None:
                        eps.append(nuevo)
                    elif _wfrank(q) > _wfrank(viejo.get("quality")):
                        eps[eps.index(viejo)] = nuevo
            eps.sort(key=lambda x: (x["season"], x["episode"]))
            return {"title": base, "episodes": eps}
        if src == "et":
            results, info = mod.detail(url)
            title = (info or {}).get("title") or ""
            for r in results:
                link = r.get("magnet")
                if not link:
                    continue
                lbl = r.get("label") or ""
                m = _re.search(r"(\d{1,2})\s*x\s*(\d{1,3})", lbl)
                s = int(m.group(1)) if m else 0
                e = int(m.group(2)) if m else 0
                label = ("%dx%02d" % (s, e)) if m else (lbl[:40] or "Episodio")
                eps.append({"label": label, "season": s, "episode": e,
                            "quality": r.get("quality") or "", "link": link,
                            "content_id": link})
            return {"title": title, "episodes": eps}
    except Exception as ex:
        xbmc.log("[MejorWolf/service] episodes %s err: %s" % (src, ex),
                 xbmc.LOGWARNING)
    return {"title": "", "episodes": eps}


def _src_resolve(src, url):
    """Ficha de la fuente -> mejor enlace (magnet o .torrent). '' si nada."""
    mod = _src_mod(src)
    if not mod or not url:
        return ""
    try:
        if src == "et":
            results, _info = mod.detail(url)
            for r in results:
                if r.get("is_magnet"):
                    return r.get("magnet") or ""
            return (results[0].get("magnet") if results else "") or ""
        if src == "dx":
            d = mod.detail(url)
            dls = (d or {}).get("downloads") or []
            return (dls[0].get("torrent_url") if dls else "") or ""
        if src == "wf":
            d = mod.detail(url)
            if isinstance(d, dict):
                dls = d.get("downloads") or d.get("links") or []
                if dls:
                    return (dls[0].get("torrent_url")
                            or dls[0].get("magnet") or "") or ""
                return d.get("magnet") or d.get("torrent_url") or ""
    except Exception as e:
        xbmc.log("[MejorWolf/service] resolve %s err: %s" % (src, e),
                 xbmc.LOGWARNING)
    return ""


def _src_rar(src, url):
    """¿El item viene comprimido (RAR)? DivxTotal lo marca en el nombre del
    .torrent (p.ej. 'Pelicula-(ARCHIVO).torrent'). Barato: solo abre la ficha."""
    if src != "dx" or not url:
        return False
    try:
        mod = _src_mod("dx")
        d = mod.detail(url) if mod else {}
        dls = (d or {}).get("downloads") or []
        if not dls:
            return False
        name = (dls[0].get("torrent_url") or "").lower()
        return ("comprimido" in name or "(archivo" in name
                or "-archivo" in name or name.endswith(".rar"))
    except Exception:
        return False


def _src_meta(src, url):
    """RAR + calidad de un item DivxTotal en UNA sola apertura de ficha (para el
    badge perezoso del catalogo)."""
    out = {"rar": False, "quality": ""}
    if src != "dx" or not url:
        return out
    try:
        mod = _src_mod("dx")
        d = mod.detail(url) if mod else {}
        dls = (d or {}).get("downloads") or []
        if dls:
            name = (dls[0].get("torrent_url") or "").lower()
            out["rar"] = ("comprimido" in name or "(archivo" in name
                          or "-archivo" in name or name.endswith(".rar"))
            out["quality"] = dls[0].get("quality") or ""
    except Exception as e:
        xbmc.log("[MejorWolf/service] meta dx err: %s" % e, xbmc.LOGWARNING)
    return out


_DTQ_RE = None


def _dt_meta(content_id, tabla):
    """Calidad + RAR de un item DonTorrent, resueltos por el BOX (la IP de Render
    no puede con el PoW de descarga). Resuelve el .torrent (cacheado) y lee sus
    bytes: is_packed -> RAR, y busca el token de calidad en el nombre."""
    global _DTQ_RE
    import re as _re
    if _DTQ_RE is None:
        _DTQ_RE = _re.compile(
            rb"(4K|2160p|1080p|720p|HDRip|BluRay|BDRemux|BDRip|WEB-?DL|WEBRip|"
            rb"MicroHD|HDTV|DVDRip|Remux|UHD)", _re.I)
    out = {"rar": False, "quality": "", "ih": ""}
    try:
        from resources.lib import scraper_dontorrent as dt
        from resources.lib import torrent as tparse
        from resources.lib import http_session as hs
        url = dt.resolve_torrent(content_id, tabla, prefer_direct=True)
        if not url:
            return out
        m = _DTQ_RE.search(url.encode("utf-8", "ignore"))
        if m:
            out["quality"] = _dt_norm_q(m.group(1).decode("ascii", "ignore"))
        try:
            sess = hs.make_session()
            data = hs.get(sess, url, timeout=25).content
        except Exception:
            data = None
        if data:
            try:
                out["rar"] = bool(tparse.is_packed(data))
            except Exception:
                pass
            try:
                # info_hash -> el relay deriva los seeders por scrape UDP (lo unico
                # que SI puede desde la IP de Render aunque DonTorrent la banee).
                out["ih"] = tparse.info_hash_hex(data)
            except Exception:
                pass
            if not out["quality"]:
                bm = _DTQ_RE.search(data)
                if bm:
                    out["quality"] = _dt_norm_q(
                        bm.group(1).decode("ascii", "ignore"))
    except Exception as e:
        xbmc.log("[MejorWolf/service] dtmeta err: %s" % e, xbmc.LOGWARNING)
    return out


def _dt_norm_q(q):
    t = (q or "").lower()
    if t in ("4k", "2160p", "uhd"):
        return "4K"
    if t == "1080p":
        return "1080p"
    if t == "720p":
        return "720p"
    return (q or "").upper() if t in ("hdrip", "hdtv", "dvdrip") else q


def _do_etjob(ev):
    """El catalogo web pide buscar/listar/resolver en fuentes que Render no
    alcanza (Cloudflare/ISP). El box SI (IP residencial). En hilo aparte para no
    frenar el mando; sube el resultado al relay. op: search|latest|resolve.
    srcs: csv de fuentes (et,dx,wf); por compat, vacio = solo 'et'."""
    import threading

    def _run():
        try:
            from resources.lib import remote_kb as rkb
            op = (ev.get("op") or "search").strip()
            out = {"job": ev.get("job") or "", "op": op}
            t_ini = time.time()          # para _salud_en (lo visto en ESTE trabajo)
            if op in ("search", "latest"):
                srcs = [s for s in (ev.get("srcs") or "et").split(",") if s]
                q = (ev.get("q") or "").strip()
                # Cada fuente en su propio hilo -> EliteTorrent y DivxTotal en
                # PARALELO (antes secuencial). Mas rapido.
                res = {}

                def _one(src):
                    mod = _src_mod(src)
                    if not mod:
                        res[src] = []
                        return
                    try:
                        if op == "search":
                            items = mod.search(q) if q else []
                        else:
                            items = mod.latest("movie", 1)
                            if isinstance(items, tuple):
                                items = items[0]
                    except Exception as e:
                        xbmc.log("[MejorWolf/service] %s/%s err: %s"
                                 % (op, src, e), xbmc.LOGWARNING)
                        items = []
                    res[src] = items or []

                ths = [threading.Thread(target=_one, args=(s,)) for s in srcs]
                for t in ths:
                    t.daemon = True    # que un hilo colgado no frene el cierre
                    t.start()
                # DEADLINE GLOBAL, no 22s POR HILO. El join en serie sumaba hasta
                # 66s con 3 fuentes y NADA se subia hasta que acababa la ultima:
                # medido 2026-08-06, EliteTorrent respondia en 0,5s (4 resultados)
                # y DivxTotal en 10s (5), pero WolfMax tardaba 43s y se rendia
                # VACIO -> el relay corta a los 24s (`wait` de /catetbox) -> se
                # perdia la busqueda ENTERA y la web mostraba solo DonTorrent.
                # Ahora se sube lo que haya al llegar al deadline: las fuentes
                # rapidas llegan SIEMPRE y la lenta deja de arrastrar a las demas.
                # Las que no lleguen quedan en [] (res.get abajo).
                # 21s y no 18s: WolfMax CUANDO SI encuentra algo tarda ~20s
                # (medido con "Disforia": brave da 429, reintenta por proxy y
                # cierra en 20,3s) -> con 18s se perdia por 2 segundos justo
                # cuando iba bien. 21s deja ~3s de margen para que el push llegue
                # dentro de los 24s que espera /catetbox. Si WolfMax se atasca
                # (43s cuando NO tiene el titulo), se corta y et/dx se salvan.
                # Tope duro 21s, pero con CORTE BLANDO a los 10s: si a esas
                # alturas ya han terminado todas menos UNA, no se espera a la
                # rezagada. Antes, con et listo en 0,5s y dx en 10s, la busqueda
                # tardaba los 21s enteros por esperar a WolfMax; y WolfMax, si
                # tiene el titulo, ahora responde INSTANTANEO desde su indice
                # local (fast-exit nuevo), asi que quedarse esperandolo solo
                # pasa cuando NO lo tiene y no iba a aportar nada.
                _dl = time.time() + 21
                _soft = time.time() + 10
                while time.time() < _dl:
                    if not any(t.is_alive() for t in ths):
                        break                       # todas han terminado
                    if time.time() >= _soft and len(res) >= len(srcs) - 1:
                        break                       # solo falta la rezagada
                    time.sleep(0.2)
                allit = []
                for src in srcs:
                    for it in res.get(src, []):
                        # Antes se TIRABAN las series de WolfMax porque no habia
                        # forma de abrir sus capitulos. Ya la hay: WolfMax (igual
                        # que EliteTorrent) publica una ficha por CAPITULO y el
                        # relay las agrupa en UNA tarjeta de serie con los
                        # capitulos dentro, que se resuelven al reproducir. Un
                        # relay viejo las ignora, asi que esto no rompe nada.
                        allit.append(_src_item_compact(it, src))
                out["items"] = allit[:60]
            elif op == "resolve":
                src = (ev.get("src") or "et").strip()
                out["link"] = _src_resolve(src, (ev.get("url") or "").strip())
            elif op == "rarcheck":
                m = _src_meta((ev.get("src") or "").strip(),
                              (ev.get("url") or "").strip())
                out["rar"] = m["rar"]
                out["quality"] = m["quality"]
            elif op == "episodes":
                src = (ev.get("src") or "").strip()
                out["eps"] = _src_episodes(src, (ev.get("url") or "").strip())
            elif op == "infohash":
                lk = (ev.get("link") or "").strip()
                lk = lk or _src_resolve(
                    (ev.get("src") or "").strip(),
                    (ev.get("url") or "").strip())
                # WolfMax no entrega un magnet: entrega un "enlacito"
                # ofuscado, y de ahi el relay no puede sacar el info_hash. Sin
                # esto, sus capitulos se quedan SIN SEMILLAS -- y las semillas
                # tienen que verse en todas las fuentes. Aqui si se puede:
                # la caja sabe descifrarlo (lo hace al reproducir) y ademas
                # tiene IP residencial para bajar el .torrent, que es lo que
                # el relay no puede.
                try:
                    from resources.lib import enlacito as _enl
                    if lk and _enl.is_enlacito_url(lk):
                        _real = _enl.resolve(lk)
                        if _real:
                            lk = _real
                except Exception as _e:
                    xbmc.log("[MejorWolf/service] enlacito: %s" % _e,
                             xbmc.LOGWARNING)
                # Y si ya tenemos el .torrent, se calcula el hash AQUI: asi el
                # relay no necesita alcanzar wolfmax4k.com (que le bloquea).
                # OJO con la query: WolfMax sirve el fichero como
                # ".../silo--4K...torrent?md5=t1QWSk9" y un endswith(".torrent")
                # NO casa -> se saltaba el calculo, el relay intentaba bajarlo
                # el (wolfmax le bloquea) y la peticion moria a los 60 s.
                if lk and lk.split("?", 1)[0].lower().endswith(".torrent"):
                    try:
                        from resources.lib import http_session as _hs
                        from resources.lib import torrent as _tp
                        _sess = _hs.make_session()
                        _r = _hs.get(_sess, lk, timeout=25)
                        _data = getattr(_r, "content", b"") or b""
                        if _data:
                            out["ih"] = _tp.info_hash_hex(_data) or ""
                    except Exception as _e2:
                        xbmc.log("[MejorWolf/service] ih de wolfmax: %s" % _e2,
                                 xbmc.LOGWARNING)
                out["link"] = lk
            elif op == "dthtml":
                from resources.lib import scraper_dontorrent as dt
                out["html"] = dt.fetch_html(path=ev.get("path"),
                                            q=(ev.get("q") or "").strip())
            elif op == "dtmeta":
                cid = re.sub(r"\D", "", str(ev.get("cid") or ""))
                tb = (ev.get("tb") or "peliculas").strip()
                m = _dt_meta(cid, tb) if cid else {"rar": False,
                                                   "quality": "", "ih": ""}
                out["rar"] = m["rar"]
                out["quality"] = m["quality"]
                out["ih"] = m.get("ih", "")
            else:
                return
            _salud_en(out, t_ini)        # WolfMax/EliteTorrent: caidas o vivas
            rkb.push_etjob(out)
            xbmc.log("[MejorWolf/service] srcjob %s -> ok (%d)"
                     % (op, len(out.get("items", []))), xbmc.LOGINFO)
        except Exception as e:
            xbmc.log("[MejorWolf/service] srcjob error: %s" % e,
                     xbmc.LOGWARNING)

    threading.Thread(target=_run, daemon=True).start()


def _play_ref(ev):
    """Reproduce DIRECTAMENTE una referencia compartida (enlace de un amigo):
    lanza el plugin de play/dt_play -> Elementum, sin pasar por la busqueda."""
    try:
        from urllib.parse import quote
        a = (ev.get("a") or "").strip()
        t = (ev.get("t") or "").strip()
        base = "plugin://plugin.video.mejorwolf/?action="
        if a == "dt":
            cid = re.sub(r"\D", "", str(ev.get("cid") or ""))
            tb = re.sub(r"[^a-z0-9_]", "", str(ev.get("tb") or "").lower())
            if not (cid and tb):
                return
            url = (base + "dt_play&content_id=%s&tabla=%s&t=%s"
                   % (cid, tb, quote(t)))
        elif a == "pl":
            u = (ev.get("u") or "").strip()
            if not (u.startswith("magnet:") or u.startswith("http")
                    or u.endswith(".torrent")):
                return
            url = base + "play&torrent=%s&t=%s" % (quote(u, safe=""), quote(t))
        else:
            return
        xbmc.log("[MejorWolf/service] play_ref -> %s" % url[:120],
                 xbmc.LOGINFO)
        xbmc.executebuiltin('PlayMedia("%s")' % url)
        try:
            resume = int(ev.get("resume") or 0)
        except (TypeError, ValueError):
            resume = 0
        if resume > 5:
            _resume_seek_async(resume)
    except Exception as e:
        xbmc.log("[MejorWolf/service] play_ref error: %s" % e, xbmc.LOGWARNING)


def _resume_seek_async(resume):
    """Tras lanzar la reproduccion, espera a que Elementum cargue y hace un
    seek ABSOLUTO al segundo `resume` (para 'Continuar viendo'). Best-effort:
    si no llega a tiempo, simplemente empieza desde el principio."""
    import threading

    def _worker():
        try:
            import json
            deadline = time.time() + 90
            while time.time() < deadline:
                xbmc.sleep(1000)
                res = xbmc.executeJSONRPC(
                    '{"jsonrpc":"2.0","id":1,'
                    '"method":"Player.GetActivePlayers"}')
                players = (json.loads(res).get("result") or [])
                vid = next((p for p in players
                            if p.get("type") == "video"), None)
                if not vid:
                    continue
                pid = vid["playerid"]
                pr = json.loads(xbmc.executeJSONRPC(json.dumps({
                    "jsonrpc": "2.0", "id": 1,
                    "method": "Player.GetProperties",
                    "params": {"playerid": pid,
                               "properties": ["totaltime"]}})))
                tt = (pr.get("result") or {}).get("totaltime") or {}
                total = (int(tt.get("hours", 0)) * 3600
                         + int(tt.get("minutes", 0)) * 60
                         + int(tt.get("seconds", 0)))
                if total <= 0:
                    continue   # aun cargando metadata
                h, m, s = resume // 3600, (resume % 3600) // 60, resume % 60
                xbmc.executeJSONRPC(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "Player.Seek",
                    "params": {"playerid": pid,
                               "value": {"time": {"hours": h, "minutes": m,
                                                  "seconds": s}}}}))
                xbmc.log("[MejorWolf/service] resume seek -> %ds" % resume,
                         xbmc.LOGINFO)
                return
        except Exception as e:
            xbmc.log("[MejorWolf/service] resume seek error: %s" % e,
                     xbmc.LOGDEBUG)

    threading.Thread(target=_worker, daemon=True).start()


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


def _read_np_title():
    """Titulo que el addon guardo al lanzar la reproduccion (special://temp).
    Solo lo damos por bueno si es reciente (< 6h) para no mostrar restos."""
    try:
        import os
        import xbmcvfs
        p = xbmcvfs.translatePath("special://temp/mejorwolf_np.txt")
        if not os.path.exists(p):
            return ""
        if time.time() - os.path.getmtime(p) > 6 * 3600:
            return ""
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


_CONTINUE_FILE = "special://temp/mejorwolf_continue.json"


def _addon_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon("plugin.video.mejorwolf").getAddonInfo("version")
    except Exception:
        return ""


def _codigo_nuevo(nuevo):
    """El movil pide cambiar el codigo de esta tele (Mis Kodis -> 'Cambiar
    codigo'), p.ej. porque el viejo se ha visto donde no debia (22-09-2026: los
    codigos del salon y del PC estuvieron publicados en GitHub).

    Se guarda en disco antes de adoptarlo (remote_kb.set_code) y se manda un
    latido YA con el codigo nuevo: es lo que espera el movil para dar el cambio
    por hecho. Si algo falla, la tele se queda con el que tenia -- el movil no
    ve el latido nuevo y no cambia nada por su lado."""
    from resources.lib import remote_kb as rkb
    nuevo = "".join(ch for ch in str(nuevo or "") if ch.isdigit())
    if len(nuevo) != 6 or nuevo == rkb.get_code():
        return
    if not rkb.set_code(nuevo):
        xbmc.log("[MejorWolf/service] codigo nuevo: no se pudo guardar",
                 xbmc.LOGWARNING)
        return
    xbmc.log("[MejorWolf/service] codigo del mando cambiado", xbmc.LOGINFO)
    try:
        rkb.push_status(_addon_version())
    except Exception:
        pass
    try:     # que se vea en la tele: si el movil no se enterase, esta ahi
        import xbmcgui
        xbmcgui.Dialog().notification(
            "MejorWolf", "Código nuevo del mando: %s" % nuevo,
            xbmcgui.NOTIFICATION_INFO, 10000)
    except Exception:
        pass


def _update_continue(elapsed, total):
    """Actualiza la posicion del fichero 'Continuar viendo' mientras se ve.
    El fichero lo CREA el addon (play/dt_play) con titulo+referencia; aqui solo
    refrescamos elapsed/total/ts."""
    try:
        import os
        import json
        import xbmcvfs
        p = xbmcvfs.translatePath(_CONTINUE_FILE)
        if not os.path.exists(p):
            return
        with open(p, "r", encoding="utf-8") as f:
            rec = json.load(f) or {}
        rec["elapsed"] = int(elapsed or 0)
        rec["total"] = int(total or 0)
        rec["ts"] = time.time()
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f)
    except Exception:
        pass


def _read_continue_push():
    """Devuelve el 'Continuar viendo' para el latido, o None. Solo si es
    reciente (< 14 dias), tiene duracion y NO esta casi terminado (>92%)."""
    try:
        import os
        import json
        import xbmcvfs
        p = xbmcvfs.translatePath(_CONTINUE_FILE)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            rec = json.load(f) or {}
        total = int(rec.get("total", 0) or 0)
        elapsed = int(rec.get("elapsed", 0) or 0)
        ts = rec.get("ts", 0)
        if total <= 0 or elapsed < 30:
            return None
        if time.time() - ts > 14 * 86400:
            return None
        if elapsed >= total * 0.92:       # practicamente terminado
            return None
        return {"title": rec.get("title", ""), "a": rec.get("a", ""),
                "ci": rec.get("ci", ""), "tb": rec.get("tb", ""),
                "u": rec.get("u", ""), "elapsed": elapsed, "total": total}
    except Exception:
        return None


def _secs_from_clock(s):
    """'1:23:45' o '23:45' o '45' -> segundos."""
    s = (s or "").strip()
    if not s:
        return 0
    tot = 0
    for part in s.split(":"):
        if not part.isdigit():
            return 0
        tot = tot * 60 + int(part)
    return tot


def _get_now_playing():
    """Estado de reproduccion para 'Estas viendo'. Usa INFOLABELS (NO JSON-RPC):
    leen info ya cacheada por Kodi y NO bloquean el reproductor, asi que no
    provocan micro-cortes de audio/video durante la peli. Devuelve
    {title, elapsed, total, paused} o None si no hay video."""
    try:
        if not xbmc.getCondVisibility("Player.HasVideo"):
            return None

        def _int(s):
            try:
                return int(s)
            except (TypeError, ValueError):
                return 0

        elapsed = _secs_from_clock(xbmc.getInfoLabel("VideoPlayer.Time"))
        total = _secs_from_clock(xbmc.getInfoLabel("VideoPlayer.Duration"))
        paused = xbmc.getCondVisibility("Player.Paused")

        show = (xbmc.getInfoLabel("VideoPlayer.TVShowTitle") or "").strip()
        title = (xbmc.getInfoLabel("VideoPlayer.Title") or "").strip()
        season = _int(xbmc.getInfoLabel("VideoPlayer.Season"))
        ep = _int(xbmc.getInfoLabel("VideoPlayer.Episode"))
        # El addon guarda el titulo limpio al lanzar (fiable para PELICULAS,
        # donde Elementum no rellena VideoPlayer.Title de forma fiable).
        file_title = _read_np_title()
        # Si el titulo parece un nombre de fichero, lo dejamos legible.
        if title and " " not in title and ("." in title or "_" in title):
            title = re.sub(r"\.(mkv|mp4|avi|m4v|mov|ts)$", "", title,
                           flags=re.I)
            title = title.replace(".", " ").replace("_", " ").strip()
        if show and (season or ep):
            label = "%s · %dx%02d" % (show, season, ep)
        elif show:
            label = show
        else:
            label = file_title or title or "Reproduciendo"
        return {"title": label, "elapsed": elapsed, "total": total,
                "paused": paused}
    except Exception:
        return None


def _push_wf_index(force=False):
    """Sube el indice local de WolfMax al relay (POST /wffeed).

    El relay NO puede construirlo (WolfMax le bloquea la IP de datacenter), pero
    las cajas lo tienen completo de los sitemaps. Subiendolo, la busqueda de
    WolfMax en la web pasa de ~10s (ida y vuelta a una caja) a milisegundos, y
    deja de depender de que haya alguna caja despierta.

    Va CON las imagenes (320 KB -> 537 KB, 30 KB -> 52 KB comprimido). Se mandaban
    sin ellas porque "la caratula la pone TMDB", y para el 90% es verdad; pero hay
    titulos que TMDB no tiene ("La maldicion de Widows Bay" no esta, ni con tilde
    ni sin ella) y esos se quedaban en gris para siempre. La caratula propia de
    WolfMax ya la tenemos aqui (89% de las entradas): mandarla cuesta 22 KB
    comprimidos y es el unico modo de que esos titulos tengan cara.
    El relay la usa SOLO de respaldo, cuando TMDB no da poster.
    """
    import requests
    try:
        base = _relay_base()
        if not base:
            return 0
        from resources.lib import wf_index
        entries = wf_index._load() or {}
        if not entries:
            return 0
        slim = {}
        for u, e in entries.items():
            t = (e or {}).get("title")
            if not u or not t:
                continue
            rec = {"t": t[:160], "k": (e.get("kind") or "")[:16],
                   "q": (e.get("quality") or "")[:12]}
            img = e.get("image") or ""
            if isinstance(img, str) and img.startswith("http"):
                rec["i"] = img[:220]
            slim[u] = rec
        if not slim:
            return 0
        r = requests.post(base + "/wffeed", json={"entries": slim}, timeout=45,
                          headers={"Content-Type": "application/json"})
        n = 0
        try:
            n = (r.json() or {}).get("n", 0)
        except Exception:
            pass
        xbmc.log("[MejorWolf/service] wfidx -> relay: %d entradas (HTTP %s)"
                 % (len(slim), r.status_code), xbmc.LOGINFO)
        return n
    except Exception as e:
        xbmc.log("[MejorWolf/service] wfidx error: %s" % e, xbmc.LOGWARNING)
        return 0


def _wf_index_loop(monitor):
    """Empuja el indice al arrancar (tras 90s, sin estorbar el arranque) y cada
    6 horas. El relay ademas lo pide expresamente cuando se queda sin el."""
    if monitor.waitForAbort(90):
        return
    while not monitor.abortRequested():
        try:
            _push_wf_index()
        except Exception:
            pass
        if monitor.waitForAbort(6 * 3600):
            return


def _kb_thread(monitor):
    """Hilo dedicado al Teclado Remoto: sondea rapido para que el mando vaya
    agil, sin que el bucle principal (FA/keep-warm) lo frene. Tambien sube el
    estado 'Estas viendo...' al relay (throttled), solo cuando hay video."""
    from resources.lib import remote_kb as rkb
    last_now = 0.0
    last_cont = 0.0
    was_playing = False
    while not monitor.abortRequested():
        try:
            _poll_remote_kb()
        except Exception:
            pass
        playing = xbmc.getCondVisibility("Player.HasVideo")
        # 'Estas viendo': red solo cuando hay video (o un ultimo aviso al parar)
        try:
            t = time.time()
            if t - last_now >= NOW_GAP:
                last_now = t
                np = _get_now_playing()
                if np:
                    rkb.push_now(np)
                    was_playing = True
                    if t - last_cont >= CONT_GAP:
                        last_cont = t
                        _update_continue(np.get("elapsed"), np.get("total"))
                elif was_playing:
                    rkb.push_now(None)
                    was_playing = False
        except Exception:
            pass
        # Ritmo del sondeo: agil SOLO si hay un movil usando el mando (hint
        # "fast" del relay; si el relay es viejo y no lo manda, se asume que
        # si). Sin movil: espaciado (ahorro de egress). Al reproducir, un
        # pelin mas espaciado (menos carga -> sin cortes).
        if rkb.LAST_POLL_FAST:
            gap = KB_POLL_GAP_PLAYING if playing else KB_POLL_GAP
        else:
            gap = KB_POLL_GAP_IDLE
        if monitor.waitForAbort(gap):
            break


def _settings_get(setting):
    """Lee un ajuste de Kodi por JSON-RPC. Solo se usa al ARRANCAR (no en bucle
    ni durante la reproduccion)."""
    import json
    try:
        res = xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Settings.GetSettingValue",
            "params": {"setting": setting}}))
        return (json.loads(res).get("result") or {}).get("value")
    except Exception:
        return None


def _settings_set(setting, value):
    import json
    try:
        xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Settings.SetSettingValue",
            "params": {"setting": setting, "value": value}}))
        return True
    except Exception:
        return False


# Config optima de reproduccion para que el video vaya a SUS fps (sin judder ni
# audio "ondeando"):
#  - adjustrefreshrate=2 (Al iniciar/detener): la pantalla iguala su Hz al video.
#  - usedisplayasclock=False: sincroniza por audio, NO lo resamplea (no ondea).
# Se aplica de forma IDEMPOTENTE al arrancar -> cero toques del usuario, y vale
# para todos los boxes (tuyo y de tus amigos).
_OPTIMAL_PLAYER = {
    "videoplayer.adjustrefreshrate": 2,
    "videoplayer.usedisplayasclock": False,
    # Decodificacion por hardware (para que el HD no tire de CPU -> sin lag).
    # Es el valor por defecto en Android; lo aseguramos. NO tocamos la variante
    # 'surface' (forzarla puede romper subtitulos/OSD en algun box).
    "videoplayer.usemediacodec": True,
}
_PLAYER_CFG = {}    # valores aplicados (para la telemetria)


def _ensure_playback_settings():
    """Deja la config de reproduccion optima. Idempotente: solo escribe si
    difiere. Devuelve los cambios hechos."""
    changed = {}
    for setting, want in _OPTIMAL_PLAYER.items():
        cur = _settings_get(setting)
        if cur is None:
            continue   # ese ajuste no existe en este Kodi -> lo ignoramos
        if cur != want and _settings_set(setting, want):
            changed[setting] = [cur, want]
            cur = want
        _PLAYER_CFG[setting] = cur
    if changed:
        xbmc.log("[MejorWolf/service] reproduccion ajustada: %s" % changed,
                 xbmc.LOGINFO)
    return changed


def _playback_diag():
    """Telemetria de reproduccion (solo si hay video) con INFOLABELS baratos +
    los ajustes ya cacheados. Para confirmar/diagnosticar con datos reales."""
    if not xbmc.getCondVisibility("Player.HasVideo"):
        return None
    g = xbmc.getInfoLabel
    return {
        "fps": (g("Player.Process(videofps)")
                or g("VideoPlayer.VideoFps") or ""),
        "dec": g("Player.Process(videodecoder)") or "",
        "res": (g("Player.Process(videowidth)") + "x"
                + g("Player.Process(videoheight)")),
        "cache": g("VideoPlayer.CacheLevel") or "",
        "arr": _PLAYER_CFG.get("videoplayer.adjustrefreshrate"),
        "clk": _PLAYER_CFG.get("videoplayer.usedisplayasclock"),
    }


_DTPATH_RE = re.compile(
    r'href=["\'](/(?:pelicula|serie|documental)/(\d+)/[^"\'#?]+)["\']', re.I)
# Fichas NUEVAS de DonTorrent por vuelta de pre-carga (cada ~8 min). El catalogo
# entero se cubre en unas pocas vueltas y luego el coste es CERO (la ficha se
# cachea en disco para siempre): nunca hay una rafaga contra DonTorrent.
_FICHA_BUDGET = 10


def _dtpaths_from_html(html):
    """{content_id: ruta de su FICHA} del HTML del listado que acabamos de
    empujar. El relay parsea el mismo HTML, pero el `pending` que devuelve solo
    trae cid/titulo; el path con SLUG (que DonTorrent exige) lo sacamos aqui sin
    pedir nada, y asi esto funciona tambien con un relay antiguo."""
    out = {}
    for path, cid in _DTPATH_RE.findall(html or ""):
        out.setdefault(cid, path)
    return out


def _enrich_pending(base, kind, resp, html="", budget=None):
    """Tras empujar /catfeed, el relay devuelve `pending`: titulos SIN poster TMDB
    (la IP de datacenter de Render tiene TMDB baneado). Los enriquecemos con
    NUESTRO TMDB (IP RESIDENCIAL del box, no baneada) y los empujamos a /catenrich
    -> el Inicio sale en HD (poster + nota + año) aunque Render no pueda con TMDB.
    Best-effort, acotado y CACHEADO (tmdb del box guarda en disco) -> tras la 1a
    vuelta el relay rellena solo por content_id y `pending` llega vacio (0 llamadas
    TMDB). Cualquier fallo: el Inicio degrada a la semilla, igual que hoy.

    Antes de preguntar a TMDB se lee la FICHA de DonTorrent (2.9.58), lo unico que
    publica el titulo COMPLETO y el DIRECTOR. Con eso:
      · la query a TMDB lleva el titulo de verdad ('Una Milla: Capítulo Uno' en
        vez del slug mutilado 'Una Milla Captulo Uno', que no matcheaba), y
      · los HOMONIMOS se resuelven por director en vez de por popularidad
        ('La odisea' de The Asylum ya no se lleva el cartel de la de Nolan).
    """
    try:
        pending = (resp.json() or {}).get("pending") or []
    except Exception:
        pending = []
    if not pending:
        return 0
    try:
        from resources.lib import tmdb
        from resources.lib import scraper_dontorrent as dt
    except Exception:
        return 0
    import requests
    dtpaths = _dtpaths_from_html(html)
    if budget is None:
        budget = [_FICHA_BUDGET]
    meta = {}
    for p in pending[:80]:
        cid = p.get("cid")
        title = p.get("title") or ""
        if not cid or not title:
            continue
        k = "tv" if p.get("kind") == "serie" else "movie"
        # 1) Ficha de DonTorrent: gratis si ya la tenemos; si no, solo mientras
        #    quede presupuesto de esta vuelta (el resto cae en la siguiente).
        ficha = {}
        path = p.get("dtpath") or dtpaths.get(str(cid))
        if path:
            try:
                ficha = dt.detail_info(path, cid, fetch=False)
                if not ficha and budget[0] > 0:
                    budget[0] -= 1
                    ficha = dt.detail_info(path, cid)
            except Exception:
                ficha = {}
        # 2) TMDB con el titulo REAL y, si hay homonimos, desempatando por
        #    director/reparto de la ficha.
        try:
            info = tmdb.enrich(ficha.get("title") or title, k,
                               hint=(ficha or None))
        except Exception:
            info = None
        if not info or not info.get("poster"):
            continue
        m = {"poster": info.get("poster"), "year": info.get("year"),
             "rating": info.get("rating"), "overview": info.get("plot"),
             "backdrop": info.get("fanart"),
             # El titulo OFICIAL (con tildes). DonTorrent no las publica en sus
             # listados —cada ficha es un <a> con la imagen y sin title/alt, y su
             # propio slug viene sin la vocal ('La-ambicin-de-los-Savage')— asi
             # que el Inicio salia con 'Obsesin' o 'Cmplices hasta el final'. El
             # relay NO puede pedirlo (TMDB le banea la IP): tiene que venir de
             # aqui. El relay solo lo aplica si coincide letra por letra salvo
             # los acentos, nunca sustituye un titulo por otro distinto.
             "title": info.get("title")}
        if info.get("id"):
            m["tmdb_id"] = info["id"]
        if ficha.get("title"):
            # El titulo TAL CUAL lo publica DonTorrent en su ficha: es la web
            # ORIGINAL (norma §0, la app es su espejo), asi que el relay lo
            # aplica sin validarlo contra TMDB -> restaura tildes Y puntuacion
            # ('Una Milla: Capítulo Uno'), cosa que la via TMDB no podia porque
            # los dos puntos no estan en el slug y la comparacion fallaba.
            m["dt_title"] = ficha["title"]
            m["dtok"] = 1        # resuelto con la fuente: no hay que re-pedirlo
        if ficha.get("year"):
            m["dt_year"] = ficha["year"]
        meta[str(cid)] = m
    if not meta:
        return 0
    try:
        requests.post(base + "/catenrich",
                      json={"kind": kind, "meta": meta}, timeout=45)
        xbmc.log("[MejorWolf/service] catenrich %s -> %d posters HD"
                 % (kind, len(meta)), xbmc.LOGINFO)
    except Exception as e:
        xbmc.log("[MejorWolf/service] catenrich %s ERR: %r" % (kind, e),
                 xbmc.LOGWARNING)
    return len(meta)


def _prefetch_catalog():
    """Pre-carga los listados de DonTorrent (IP RESIDENCIAL del box) y los empuja
    al relay -> catbrowse los sirve al INSTANTE de cache, aunque DonTorrent tenga
    baneada la IP de datacenter de Render. Asi Inicio funciona y va rapido.
    Ademas ENRIQUECE con TMDB (IP residencial) lo que el relay no pudo -> Inicio HD."""
    try:
        from resources.lib import scraper_dontorrent as dt
        base = _relay_base()
        if not base:
            return
        import requests
        n = 0
        # Presupuesto de fichas COMPARTIDO por los 3 listados de esta vuelta.
        budget = [_FICHA_BUDGET]
        for kind, path in (("estrenos", "/"), ("peliculas", "/peliculas"),
                           ("series", "/series")):
            try:
                html = dt.fetch_html(path=path)
                if html and len(html) > 500:
                    # `ficha`: le dice al relay que este box sabe leer la FICHA
                    # de DonTorrent, para que le pida enrich hasta resolverla
                    # (a un box antiguo no tiene sentido pedirselo).
                    r = requests.post(base + "/catfeed",
                                      json={"kind": kind, "html": html,
                                            "ficha": 1},
                                      timeout=45)
                    if r.status_code == 200:
                        n += 1
                        # Inicio HD via TMDB del box; `html` le da el path de la
                        # ficha de cada item (titulo real + director).
                        _enrich_pending(base, kind, r, html, budget)
                    else:
                        xbmc.log("[MejorWolf/service] catfeed %s HTTP %d: %s"
                                 % (kind, r.status_code, (r.text or "")[:140]),
                                 xbmc.LOGWARNING)
                else:
                    xbmc.log("[MejorWolf/service] catfeed %s html corto (%d)"
                             % (kind, len(html or "")), xbmc.LOGWARNING)
            except Exception as e:
                xbmc.log("[MejorWolf/service] catfeed %s ERR: %r"
                         % (kind, e), xbmc.LOGWARNING)
        xbmc.log("[MejorWolf/service] prefetch catalogo -> %d/3" % n,
                 xbmc.LOGINFO)
    except Exception as e:
        xbmc.log("[MejorWolf/service] prefetch err: %s" % e, xbmc.LOGWARNING)


def _prefetch_loop(monitor):
    """Mantiene los listados de DonTorrent precargados en el relay (cada ~8 min;
    el TTL de cache del relay es 15 min). Asi Inicio nunca depende del on-demand."""
    if monitor.waitForAbort(20):   # deja arrancar el servicio + Anubis
        return
    while not monitor.abortRequested():
        _prefetch_catalog()
        if monitor.waitForAbort(480):
            break


def main():
    monitor = xbmc.Monitor()
    xbmc.log("[MejorWolf/service] iniciado (keep-warm + teclado remoto)",
             xbmc.LOGINFO)

    base = _relay_base()
    if base:
        _ping(base)

    import threading
    # Pre-calentar DonTorrent (Anubis) en segundo plano: 1a busqueda rapida.
    threading.Thread(target=_warm_dt, daemon=True).start()
    # Teclado Remoto en su propio hilo (sondeo rapido, respuesta agil).
    threading.Thread(target=_kb_thread, args=(monitor,), daemon=True).start()
    # Pre-carga del catalogo DonTorrent al relay (Inicio instantaneo aunque
    # DonTorrent banee la IP de Render).
    threading.Thread(target=_prefetch_loop, args=(monitor,), daemon=True).start()
    threading.Thread(target=_wf_index_loop, args=(monitor,), daemon=True).start()  # indice WolfMax -> relay

    last_ping = time.time()
    last_beat = 0.0        # ultimo latido de estado al relay
    _addon_ver = _addon_version()
    cfg_done = False       # ajustes de reproduccion aplicados (una vez)
    from resources.lib import remote_kb as rkb

    while not monitor.abortRequested():
        if monitor.waitForAbort(MAIN_TICK):
            break
        now = time.time()

        # 0) Al arrancar (Kodi ya listo): dejar la reproduccion a sus fps.
        if not cfg_done:
            cfg_done = True
            try:
                _ensure_playback_settings()
            except Exception:
                pass

        # 1) keep-warm relay
        if now - last_ping >= PING_INTERVAL:
            base = _relay_base()
            if base:
                _ping(base)
            last_ping = now

        # 1b) latido de estado: 'tele conectada' + version + 'Continuar viendo'
        if now - last_beat >= HEARTBEAT_GAP:
            last_beat = now
            try:
                rkb.push_status(_addon_ver, _read_continue_push(),
                                _playback_diag())
            except Exception:
                pass

    xbmc.log("[MejorWolf/service] detenido", xbmc.LOGINFO)


if __name__ == "__main__":
    main()
