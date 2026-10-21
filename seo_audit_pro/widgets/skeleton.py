# -*- coding: utf-8 -*-
"""Skeleton placeholders with a soft shimmer, shown while data loads."""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from seo_audit_pro.theme import C

_BASE = QColor("#ECECEF")
_SHINE = QColor("#F7F7F8")


class Skeleton(QWidget):
    """kind: "table" (rows), "cards" (metric strip + panels) or "detail"."""

    def __init__(self, kind="table", parent=None):
        super().__init__(parent)
        self.kind = kind
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def showEvent(self, e):
        self._timer.start(30)
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self._phase = (self._phase + 0.018) % 1.6
        self.update()

    def _brush(self):
        w = max(1, self.width())
        x = (self._phase - 0.3) * w
        g = QLinearGradient(x, 0, x + w * 0.3, 0)
        g.setColorAt(0.0, _BASE)
        g.setColorAt(0.5, _SHINE)
        g.setColorAt(1.0, _BASE)
        return g

    def _bar(self, p, x, y, w, h, r=4):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), r, r)
        p.fillPath(path, self._brush())

    def _card(self, p, rect):
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        p.fillPath(path, QColor(C["surface"]))
        p.setPen(QColor(C["border"]))
        p.drawPath(path)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if self.kind == "cards":
            strip = QRectF(0.5, 0.5, W - 1, 100)
            self._card(p, strip)
            n = 5
            for i in range(n):
                x = 20 + i * (W - 40) / n
                self._bar(p, x, 24, 90, 10)
                self._bar(p, x, 50, 60, 24)
            cw = (W - 20) / 2
            for i in range(2):
                r = QRectF(0.5 + i * (cw + 20), 120.5, cw - 1, min(320, H - 130))
                self._card(p, r)
                self._bar(p, r.x() + 20, r.y() + 24, 140, 12)
                for j in range(6):
                    self._bar(p, r.x() + 20, r.y() + 64 + j * 34, r.width() * (0.55 + 0.06 * (j % 3)), 10)
        elif self.kind == "detail":
            self._bar(p, 0, 8, 280, 22)
            self._bar(p, 0, 42, 180, 12)
            r = QRectF(0.5, 80.5, W - 1, min(360, H - 90))
            self._card(p, r)
            for j in range(8):
                self._bar(p, r.x() + 20, r.y() + 24 + j * 34, r.width() * (0.35 + 0.08 * (j % 4)), 10)
        else:
            r = QRectF(0.5, 0.5, W - 1, H - 1)
            self._card(p, r)
            p.fillRect(QRectF(1, 1, W - 2, 34), QColor(C["surface_alt"]))
            widths = (0.22, 0.16, 0.10, 0.14, 0.10, 0.12)
            x = 16
            for w in widths:
                self._bar(p, x, 13, max(40, (W - 32) * w * 0.5), 9)
                x += (W - 32) * w + 12
            rows = int((H - 40) // 44)
            for i in range(rows):
                y = 36 + i * 44
                p.fillRect(QRectF(1, y + 43, W - 2, 1), QColor(C["divider"]))
                x = 16
                for k, w in enumerate(widths):
                    span = (W - 32) * w
                    self._bar(p, x, y + 16, span * (0.55 + 0.25 * ((i + k) % 3) / 2), 11)
                    x += span + 12
        p.end()
