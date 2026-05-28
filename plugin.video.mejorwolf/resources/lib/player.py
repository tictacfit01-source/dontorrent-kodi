from urllib.parse import quote


def elementum_url(torrent_or_magnet):
    """Build an Elementum play URI from a .torrent URL or magnet link."""
    return f"plugin://plugin.video.elementum/play?uri={quote(torrent_or_magnet, safe='')}"
