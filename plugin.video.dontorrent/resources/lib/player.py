from urllib.parse import quote


def elementum_url(torrent_or_magnet):
    return f"plugin://plugin.video.elementum/play?uri={quote(torrent_or_magnet, safe='')}"
