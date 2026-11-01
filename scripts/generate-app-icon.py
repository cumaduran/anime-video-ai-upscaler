"""Build the Windows icon from this project's original vector artwork."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

app = QApplication([])
source = Path(__file__).resolve().parents[1] / "app" / "anime_video_upscaler" / "assets" / "icon.svg"
if len(sys.argv) != 2 or not QIcon(str(source)).pixmap(256, 256).save(sys.argv[1], "ICO"):
    raise SystemExit("Could not create the Windows application icon")
