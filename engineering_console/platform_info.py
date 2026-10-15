"""Platform facts in one place (keeps platform branches out of the UI code)."""
import os
import shutil
import sys

APP_NAME = "Engineering Console"
APP_SLUG = "engineering-console"

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
MONO = "Menlo" if IS_MAC else "Consolas" if IS_WIN else "DejaVu Sans Mono"

# Folder names used by the application before it was renamed (settings are migrated, never deleted).
_LEGACY_NAME, _LEGACY_SLUG = "System Cockpit", "system-cockpit"


def _dirs(name, slug):
    """-> (config, cache, data) directories for an application called `name` / `slug`."""
    home = os.path.expanduser("~")
    if IS_MAC:
        support = os.path.join(home, "Library", "Application Support", name)
        return support, os.path.join(home, "Library", "Caches", name), support
    if IS_WIN:
        roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return (os.path.join(roaming, name), os.path.join(local, name, "Cache"), os.path.join(local, name))
    return (os.path.join(home, ".config", slug), os.path.join(home, ".cache", slug),
            os.path.join(home, ".local", "share", slug))


def config_dir():
    return _dirs(APP_NAME, APP_SLUG)[0]


def cache_dir():
    return _dirs(APP_NAME, APP_SLUG)[1]


def data_dir():
    return _dirs(APP_NAME, APP_SLUG)[2]


def log_dir():
    return os.path.join(data_dir(), "logs")


def legacy_config_dir():
    return _dirs(_LEGACY_NAME, _LEGACY_SLUG)[0]


def migrate_legacy_config():
    """Copy settings from the pre-rename config folder the first time the new one is used.
    The old folder is left untouched. Returns True if something was copied."""
    new, old = os.path.join(config_dir(), "config.json"), os.path.join(legacy_config_dir(), "config.json")
    if os.path.exists(new) or not os.path.exists(old):
        return False
    try:
        os.makedirs(config_dir(), exist_ok=True)
        shutil.copy2(old, new)
        return True
    except OSError:
        return False


def system_root():
    """Root of the system drive/volume for the 'DISK USED' gauge."""
    if IS_WIN:
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def hidden_subprocess_kwargs():
    """Keyword arguments that stop a console window flashing up when a helper process starts on Windows."""
    if IS_WIN:
        import subprocess
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
