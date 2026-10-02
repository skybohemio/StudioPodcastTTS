"""Dossiers de données de l'application (%LOCALAPPDATA%\StudioPodcastTTS)."""
import os

APP_NAME = "StudioPodcastTTS"


def appdata_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d
