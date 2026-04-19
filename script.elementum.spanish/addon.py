"""Elementum provider entry point.

Registers `search`, `search_movie`, `search_episode` callbacks. Each callback
fans out to every enabled provider in parallel and merges the results.

The Elementum provider API expects a list of dicts with keys:
    name, uri, info_hash, size (str), seeds, peers, language, provider, icon

Reference: https://elementum.surge.sh/providers/
"""
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

from resources.lib import util  # noqa: E402
from resources.lib.providers import enabled_providers  # noqa: E402

try:
    from elementum.provider import register_search, register_movie, register_episode
except ImportError:  # pragma: no cover - only on dev machines
    util.log("Elementum not installed - registration skipped")
    register_search = register_movie = register_episode = None


def _gather(query, kind):
    providers = enabled_providers()
    if not providers:
        return []
    util.debug(f"query={query!r} kind={kind} providers={[p.name for p in providers]}")
    out = []
    timeout = util.setting("resolve_timeout", 15, int) * 2
    with ThreadPoolExecutor(max_workers=len(providers)) as ex:
        futs = {ex.submit(_safe_search, p, query, kind): p for p in providers}
        for fut in as_completed(futs, timeout=timeout):
            p = futs[fut]
            try:
                items = fut.result() or []
            except Exception as exc:
                util.log(f"{p.name}: {exc}")
                items = []
            util.debug(f"{p.name}: {len(items)} results")
            for it in items:
                if it.is_rar:
                    continue
                out.append(it.to_elementum())
    return out


def _safe_search(provider, query, kind):
    try:
        return provider.search(query, kind=kind)
    except Exception as exc:
        util.log(f"{provider.name} crashed: {exc}")
        return []


# ----------------------------------------------------------------- callbacks

def search(query):
    return _gather(query, "movie")


def search_movie(movie):
    """movie dict from Elementum: {title, year, imdb_id, ...}"""
    title = movie.get("title") or movie.get("original_title") or ""
    year = movie.get("year") or ""
    q = f"{title} {year}".strip()
    return _gather(q, "movie")


def search_episode(episode):
    """episode dict from Elementum:
        {show_title, season, episode, ...}"""
    show = episode.get("show_title") or episode.get("title") or ""
    s = int(episode.get("season") or 0)
    e = int(episode.get("episode") or 0)
    q = f"{show} {s}x{e:02d}".strip()
    return _gather(q, "tvshow")


# ----------------------------------------------------------------- register

if register_search:
    register_search(search)
if register_movie:
    register_movie(search_movie)
if register_episode:
    register_episode(search_episode)
