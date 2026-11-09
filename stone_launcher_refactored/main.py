import sys
import threading
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QIcon
from .gui import LauncherGUI
from .oauth_server import oauth_server
from .updater import run_update_check
from .widgets import resource_path

def main() -> int:
    oauth_server.start()
    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    window = LauncherGUI()
    pixmap = QPixmap(resource_path("img/logo.png"))
    if not pixmap.isNull(): window.setWindowIcon(QIcon(pixmap))
    window.show()
    threading.Thread(target=run_update_check, args=(window,), daemon=True).start()
    return qt_app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
