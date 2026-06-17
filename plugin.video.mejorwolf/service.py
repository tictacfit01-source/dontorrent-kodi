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
        r = requests.get(f"{base}/", timeout=60,
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
                _do_etjob(ev)
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
    out = {"rar": False, "quality": ""}
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
                    t.start()
                for t in ths:
                    t.join(timeout=22)
                allit = []
                for src in srcs:
                    for it in res.get(src, []):
                        k = (it.get("kind") or "movie")
                        is_serie = k.startswith("tvshow") or k == "serie"
                        if is_serie and src not in ("dx", "et"):
                            continue   # WolfMax: solo pelis de momento
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
            elif op == "dtmeta":
                cid = re.sub(r"\D", "", str(ev.get("cid") or ""))
                tb = (ev.get("tb") or "peliculas").strip()
                m = _dt_meta(cid, tb) if cid else {"rar": False, "quality": ""}
                out["rar"] = m["rar"]
                out["quality"] = m["quality"]
            else:
                return
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
        # Al reproducir, sondeo un pelin mas espaciado (menos carga -> sin cortes)
        if monitor.waitForAbort(KB_POLL_GAP_PLAYING if playing else KB_POLL_GAP):
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
