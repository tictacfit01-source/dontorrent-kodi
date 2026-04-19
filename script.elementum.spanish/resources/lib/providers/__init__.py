"""Provider registry. Disabled providers are filtered at search time."""
from .. import util
from .base import BaseProvider, Result  # noqa: F401
from .wolfmax4k import WolfMax4k
from .dontorrent import DonTorrent
from .mejortorrent import MejorTorrent
from .divxtotal import DivxTotal

ALL = [WolfMax4k(), DonTorrent(), MejorTorrent(), DivxTotal()]


def enabled_providers():
    return [p for p in ALL if util.is_provider_enabled(p.name)]
