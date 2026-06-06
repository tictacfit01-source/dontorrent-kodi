"""Provider registry for the cross-search feature in the main addon.

DonTorrent is intentionally NOT here: the main addon already navigates
DonTorrent natively via scraper.py / download.py. These providers cover
the *additional* Spanish sources exposed under the "Otras fuentes" menu.
"""
from .. import util  # noqa: F401
from .base import BaseProvider, Result  # noqa: F401
from .wolfmax4k import WolfMax4k
from .mejortorrent import MejorTorrent
from .divxtotal import DivxTotal

ALL = [WolfMax4k(), MejorTorrent(), DivxTotal()]


def enabled_providers():
    return [p for p in ALL if util.is_provider_enabled(p.name)]


def get_provider(name):
    for p in ALL:
        if p.name == name:
            return p
    return None
