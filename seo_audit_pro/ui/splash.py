# -*- coding: utf-8 -*-
"""Startup splash: brand, real startup steps, progress, version."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from seo_audit_pro import APP_NAME, APP_TAGLINE, APP_VERSION, brand
from seo_audit_pro.theme import C


class Splash(QWidget):
    W, H = 600, 360

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W + 40, self.H + 40)
        self.lines = []
        self.progress = 0.0
        self._shown_progress = 0.0
        self._title_alpha = 0.0
        self._logo = brand.logo_pixmap(64, 2.0)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def show_centered(self):
        screen = self.screen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)
        self.show()
        a = QPropertyAnimation(self._fx, b"opacity", self)
        a.setDuration(220)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.start()
        self._fade_in = a

    def step(self, text, progress):
        self.lines.append(text)
        self.lines = self.lines[-4:]
        self.progress = max(self.progress, progress)
        self.update()

    def finish(self, on_done):
        self._timer.stop()
        a = QPropertyAnimation(self._fx, b"opacity", self)
        a.setDuration(200)
        a.setStartValue(self._fx.opacity())
        a.setEndValue(0.0)
        a.finished.connect(lambda: (self.close(), on_done()))
        a.start()
        self._fade_out = a

    def _tick(self):
        changed = False
        if self._shown_progress < self.progress:
            self._shown_progress = min(self.progress, self._shown_progress + max(0.004, (self.progress - self._shown_progress) * 0.18))
            changed = True
        if self._title_alpha < 1.0:
            self._title_alpha = min(1.0, self._title_alpha + 0.06)
            changed = True
        if changed:
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(20, 20, self.W, self.H)
        # Soft shadow
        for i, alpha in enumerate((10, 7, 4, 2)):
            sp = QPainterPath()
            sp.addRoundedRect(card.adjusted(-i * 3, -i * 3 + 4, i * 3, i * 3 + 4), 14 + i * 2, 14 + i * 2)
            p.fillPath(sp, QColor(0, 0, 0, alpha))
        path = QPainterPath()
        path.addRoundedRect(card, 12, 12)
        p.fillPath(path, QColor(C["surface"]))
        p.setPen(QPen(QColor(C["border"]), 1))
        p.drawPath(path)

        cx = card.center().x()
        logo_y = card.y() + 78
        p.setOpacity(min(1.0, self._title_alpha * 1.6))
        p.drawPixmap(int(cx - 32), int(logo_y), 64, 64, self._logo)
        p.setOpacity(self._title_alpha)
        f = QFont(self.font())
        f.setPixelSize(22)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.3)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(card.x(), logo_y + 80, card.width(), 30), Qt.AlignmentFlag.AlignHCenter, APP_NAME)
        f2 = QFont(self.font())
        f2.setPixelSize(13)
        p.setFont(f2)
        p.setPen(QColor(C["text_3"]))
        p.drawText(QRectF(card.x(), logo_y + 110, card.width(), 22), Qt.AlignmentFlag.AlignHCenter, APP_TAGLINE)
        p.setOpacity(1.0)

        # Startup log (bottom-left), newest line darkest.
        f3 = QFont(self.font())
        f3.setPixelSize(11)
        p.setFont(f3)
        base_y = card.bottom() - 28
        for i, line in enumerate(reversed(self.lines)):
            alpha = max(70, 255 - i * 70)
            col = QColor(C["text_2"])
            col.setAlpha(alpha)
            p.setPen(col)
            p.drawText(QRectF(card.x() + 24, base_y - (i + 1) * 15, card.width() - 140, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(card.right() - 124, base_y - 15, 100, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"v{APP_VERSION}")
        # Progress hairline.
        track = QRectF(card.x() + 24, card.bottom() - 18, card.width() - 48, 2)
        p.fillRect(track, QColor("#EDEDF0"))
        p.fillRect(QRectF(track.x(), track.y(), track.width() * self._shown_progress, 2), QColor(C["ink"]))
        p.end()
