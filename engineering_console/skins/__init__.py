from .base import Skin
from .classic import ClassicSkin
from .apple import AppleSkin

_SKINS = {"classic": ClassicSkin(), "apple": AppleSkin()}
_ALIASES = {"cockpit": "classic"}       # themes saved by older versions named the original skin "cockpit"


def skin_for(theme):
    """Skin named by theme['skin']; anything unknown/missing falls back to the original look."""
    name = (theme or {}).get("skin", "classic")
    return _SKINS.get(_ALIASES.get(name, name), _SKINS["classic"])
