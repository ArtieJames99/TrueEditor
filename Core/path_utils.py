from pathlib import Path
import sys

def app_base_path() -> Path:
    """
    Returns the application root directory.

    Dev:
        C:\\TrueEdits-7
    Frozen (PyInstaller):
        %TEMP%\\_MEIxxxxx
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent
