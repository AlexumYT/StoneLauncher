import os
import sys
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QColor, QPainter, QBrush
from PyQt6.QtCore import Qt, QTimer

def resource_path(relative_path: str) -> str:
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    return os.path.join(base, relative_path)

# ---------------------------------------------------------------------------
# Pulsing dot widget
# ---------------------------------------------------------------------------
class PulseDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._opacity = 1.0
        self._color   = QColor("#ff4444")
        self._timer   = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._step    = 0

    def set_active(self, active: bool) -> None:
        if active:
            self._color = QColor("#00e5a0")
            self._timer.start(40)
        else:
            self._timer.stop()
            self._color   = QColor("#ff4444")
            self._opacity = 1.0
            self.update()

    def _tick(self) -> None:
        self._step    = (self._step + 1) % 50
        self._opacity = 0.3 + 0.7 * abs((self._step - 25) / 25)
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._color)
        color.setAlphaF(self._opacity)
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 10, 10)

