import os
import sys

# Correctif SSL : forcer le chargement des DLL OpenSSL embarquées par PyInstaller
# AVANT tout import de ssl (évite « DLL load failed : _ssl » si une DLL OpenSSL
# plus ancienne traîne dans le PATH ou System32).
if getattr(sys, "frozen", False):
    import ctypes
    _base = os.path.join(os.path.dirname(sys.executable), "_internal")
    try:
        os.add_dll_directory(_base)
    except OSError:
        pass
    for _dll in ("libcrypto-3-x64.dll", "libssl-3-x64.dll"):
        try:
            ctypes.WinDLL(os.path.join(_base, _dll))
        except OSError:
            pass

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

# rendre les imports relatifs au dossier app/ possibles
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def selftest():
    """Vérifie SSL et le SDK Mistral (sortie JSON sur stdout) — utile après un build."""
    import json
    out = {}
    try:
        import ssl
        out["openssl"] = ssl.OPENSSL_VERSION
    except Exception as e:
        out["openssl"] = f"ERREUR: {e}"
    try:
        from mistralai.client import Mistral  # noqa: F401
        out["mistralai"] = "OK"
    except Exception as e:
        out["mistralai"] = f"ERREUR: {e}"
    try:
        import imageio_ffmpeg
        out["ffmpeg"] = "OK" if os.path.exists(imageio_ffmpeg.get_ffmpeg_exe()) else "introuvable"
    except Exception as e:
        out["ffmpeg"] = f"ERREUR: {e}"
    print(json.dumps(out), flush=True)


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    try:  # icône propre dans la barre des tâches (sinon celle de python.exe)
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StudioPodcastTTS.App")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Studio Podcast TTS")
    app.setOrganizationName("StudioPodcastTTS")
    # exe PyInstaller : les données sont dans _internal (sys._MEIPASS), pas à côté de app/
    res_root = getattr(sys, "_MEIPASS", os.path.join(BASE, ".."))
    icon = os.path.join(res_root, "assets", "icon.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    from gui.main_window import MainWindow
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
