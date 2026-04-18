"""Real-Debrid client.

Sends a magnet (or .torrent) to RD, waits for it to be cached/processed,
selects the largest video file (or all files), unrestricts the resulting
hosted link, and returns a plain HTTPS URL Kodi can play directly with the
built-in player. RAR releases are extracted by RD on the server side, so
this is the path that lets us stream microHD-style RAR torrents without
ever downloading them locally.

API docs: https://api.real-debrid.com/
"""
import time
import requests

API = "https://api.real-debrid.com/rest/1.0"
TIMEOUT = 25
UA = "DonTorrent-Kodi/0.5"

# Extensions Kodi's player handles directly.
VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv",
              ".ts", ".m2ts", ".webm")


class DebridError(Exception):
    pass


def _h(token):
    return {"Authorization": f"Bearer {token}", "User-Agent": UA}


def _check_token(token):
    if not token:
        raise DebridError("Real-Debrid: token vacio (configura en Ajustes)")


def user_info(token):
    _check_token(token)
    r = requests.get(API + "/user", headers=_h(token), timeout=TIMEOUT)
    if r.status_code == 401:
        raise DebridError("Real-Debrid: token invalido o caducado")
    r.raise_for_status()
    return r.json()


def add_magnet(token, magnet):
    r = requests.post(API + "/torrents/addMagnet", headers=_h(token),
                      data={"magnet": magnet}, timeout=TIMEOUT)
    if r.status_code == 401:
        raise DebridError("Real-Debrid: token invalido")
    if r.status_code == 503:
        raise DebridError("Real-Debrid: servicio no disponible (503)")
    r.raise_for_status()
    return r.json()["id"]


def add_torrent_file(token, content_bytes):
    r = requests.put(API + "/torrents/addTorrent", headers=_h(token),
                     data=content_bytes, timeout=TIMEOUT)
    if r.status_code == 401:
        raise DebridError("Real-Debrid: token invalido")
    r.raise_for_status()
    return r.json()["id"]


def torrent_info(token, tid):
    r = requests.get(f"{API}/torrents/info/{tid}", headers=_h(token), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def select_files(token, tid, file_ids="all"):
    """file_ids = 'all' or comma-separated ids of files to download."""
    r = requests.post(f"{API}/torrents/selectFiles/{tid}",
                      headers=_h(token),
                      data={"files": file_ids}, timeout=TIMEOUT)
    if r.status_code not in (202, 204):
        raise DebridError(f"selectFiles fallo (HTTP {r.status_code}): {r.text[:200]}")


def unrestrict_link(token, link):
    r = requests.post(API + "/unrestrict/link", headers=_h(token),
                      data={"link": link}, timeout=TIMEOUT)
    if r.status_code == 401:
        raise DebridError("Real-Debrid: token invalido")
    r.raise_for_status()
    return r.json()  # contains "download" with the streamable URL


def delete_torrent(token, tid):
    try:
        requests.delete(f"{API}/torrents/delete/{tid}",
                        headers=_h(token), timeout=TIMEOUT)
    except Exception:
        pass


def _pick_video_file_ids(files):
    """From RD's file list, return the ids of video files. If none look like
    videos (e.g. only RAR parts visible at this point), return 'all' so RD
    extracts and exposes whatever ends up inside."""
    ids = []
    for f in files:
        name = (f.get("path") or "").lower()
        if name.endswith(VIDEO_EXTS):
            ids.append(str(f["id"]))
    return ",".join(ids) if ids else "all"


def _pick_largest_video_link(info):
    """After RD finishes, info has 'files' (with 'selected' flag) and
    'links' (one entry per selected file, in the SAME order as the selected
    files in 'files'). Pick the largest selected video, return its hosted
    link."""
    files = info.get("files") or []
    links = info.get("links") or []
    selected = [f for f in files if f.get("selected")]
    # Order of links matches order of selected files.
    pairs = list(zip(selected, links))
    if not pairs:
        return None
    # Prefer real video extensions, then largest size.
    def score(pair):
        f, _ = pair
        name = (f.get("path") or "").lower()
        is_video = name.endswith(VIDEO_EXTS)
        return (1 if is_video else 0, f.get("bytes") or 0)
    pairs.sort(key=score, reverse=True)
    return pairs[0][1]


def stream_url(token, magnet=None, torrent_bytes=None,
               progress_cb=None, poll_interval=2.0, max_wait=180):
    """High-level helper: send magnet to RD, wait until it's ready, return
    a directly-playable HTTPS URL (or raise DebridError).

    progress_cb(percent:int, status:str) is invoked while waiting.
    max_wait is in seconds; for already-cached torrents RD typically
    finishes in 2-5 seconds.
    """
    _check_token(token)
    if not magnet and not torrent_bytes:
        raise DebridError("stream_url: ni magnet ni torrent_bytes")

    tid = (add_magnet(token, magnet) if magnet
           else add_torrent_file(token, torrent_bytes))

    try:
        # Phase 1: wait until RD has the file list (status 'waiting_files_selection').
        waited = 0.0
        info = torrent_info(token, tid)
        while info.get("status") in ("magnet_conversion", "queued"):
            if waited >= max_wait:
                raise DebridError("Real-Debrid: timeout convirtiendo magnet")
            if progress_cb:
                progress_cb(5, f"RD: {info.get('status')}")
            time.sleep(poll_interval)
            waited += poll_interval
            info = torrent_info(token, tid)

        if info.get("status") == "waiting_files_selection":
            file_ids = _pick_video_file_ids(info.get("files") or [])
            select_files(token, tid, file_ids)
            info = torrent_info(token, tid)

        # Phase 2: wait until RD finishes (status 'downloaded'). Cached
        # torrents jump straight to downloaded; uncached ones may show
        # 'downloading' with a progress percentage.
        while info.get("status") not in ("downloaded", "error", "magnet_error",
                                          "virus", "dead"):
            if waited >= max_wait:
                raise DebridError(
                    f"Real-Debrid: no esta cacheado, llevaria {info.get('seeders', '?')} seeders en RD"
                )
            pct = info.get("progress") or 0
            if progress_cb:
                progress_cb(int(pct), f"RD: {info.get('status')} ({pct}%)")
            time.sleep(poll_interval)
            waited += poll_interval
            info = torrent_info(token, tid)

        if info.get("status") != "downloaded":
            raise DebridError(f"Real-Debrid: estado final {info.get('status')}")

        hosted = _pick_largest_video_link(info)
        if not hosted:
            raise DebridError("Real-Debrid: ningun fichero seleccionado tras procesar")

        un = unrestrict_link(token, hosted)
        url = un.get("download")
        if not url:
            raise DebridError("Real-Debrid: unrestrict sin download URL")
        if progress_cb:
            progress_cb(100, "RD: listo")
        return url
    except Exception:
        # On error try to clean up the slot so it doesn't pile up. On
        # success we keep it (RD users normally want their torrents listed).
        delete_torrent(token, tid)
        raise


def ping(token):
    """Lightweight check used by the Diagnostico screen."""
    try:
        u = user_info(token)
        return True, f"{u.get('username','?')} - {u.get('type','?')} - exp {u.get('expiration','?')[:10]}"
    except Exception as e:
        return False, str(e)
