from urllib.parse import quote


def elementum_url(torrent_or_magnet):
    """Build a plugin:// URL that hands the torrent off to Elementum."""
    return f"plugin://plugin.video.elementum/play?uri={quote(torrent_or_magnet, safe='')}"


def direct_url(url):
    """Real-Debrid (and similar) returns a plain HTTPS URL that Kodi's
    built-in player handles directly with no extra addon."""
    return url
