# -*- coding: utf-8 -*-
"""Small, purpose-built charts painted with QPainter (no chart library)."""

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from seo_audit_pro.theme import C, score_color


class ScoreRing(QWidget):
    """Overall score as a ring with the number and grade in the centre."""

    def __init__(self, size=132, parent=None):
        super().__init__(parent)
        self.score, self.grade = None, ""
        self.setFixedSize(size, size)

    def set_score(self, score, grade=""):
        self.score, self.grade = score, grade or ""
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 10
        r = QRectF(w / 2 + 1, w / 2 + 1, self.width() - w - 2, self.height() - w - 2)
        p.setPen(QPen(QColor("#EDEDF0"), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, 0, 360 * 16)
        if self.score is not None:
            p.setPen(QPen(QColor(score_color(self.score)), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(r, 90 * 16, int(-360 * 16 * max(0, min(100, self.score)) / 100))
        f = QFont(self.font())
        f.setPixelSize(int(self.height() * 0.27))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(0, 0, self.width(), self.height() * 0.62), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   "—" if self.score is None else str(self.score))
        f2 = QFont(self.font())
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(C["text_3"]))
        p.drawText(QRectF(0, self.height() * 0.62, self.width(), 20), Qt.AlignmentFlag.AlignHCenter,
                   f"Grade {self.grade}" if self.grade else "of 100")
        p.end()


class DailyBars(QWidget):
    """Counts per day for the last N days (e.g. completed audits)."""

    def __init__(self, days=14, parent=None):
        super().__init__(parent)
        self.days = days
        self.values = {}
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values):
        self.values = values or {}
        self.update()

    def sizeHint(self):
        return QSize(400, 150)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # The server groups by UTC calendar day; key the bars the same way.
        today = datetime.now(timezone.utc).date()
        keys = [(today - timedelta(days=i)).isoformat() for i in range(self.days - 1, -1, -1)]
        vals = [int(self.values.get(k, 0)) for k in keys]
        peak = max(vals) if any(vals) else 0
        f = QFont(self.font())
        f.setPixelSize(11)
        p.setFont(f)
        bottom = self.height() - 20
        top = 14
        n = len(keys)
        gap = 6
        bw = max(4, (self.width() - gap * (n - 1)) / n)
        for i, (k, v) in enumerate(zip(keys, vals)):
            x = i * (bw + gap)
            h = 0 if not peak else (bottom - top) * v / peak
            track = QPainterPath()
            track.addRoundedRect(QRectF(x, top, bw, bottom - top), 3, 3)
            p.fillPath(track, QColor("#F4F4F5"))
            if v:
                bar = QPainterPath()
                bar.addRoundedRect(QRectF(x, bottom - h, bw, h), 3, 3)
                p.fillPath(bar, QColor(C["ink"]))
                p.setPen(QColor(C["text_2"]))
                p.drawText(QRectF(x - 4, bottom - h - 14, bw + 8, 12), Qt.AlignmentFlag.AlignCenter, str(v))
            if i % 2 == (n - 1) % 2:
                p.setPen(QColor(C["text_3"]))
                lbl = k[8:10]
                p.drawText(QRectF(x - 6, bottom + 4, bw + 12, 14), Qt.AlignmentFlag.AlignCenter, lbl)
        if not peak:
            p.setPen(QColor(C["text_3"]))
            msg = "No completed audits in the last 14 days"
            p.drawText(QRectF(0, top, self.width(), bottom - top), Qt.AlignmentFlag.AlignCenter, msg)
        p.end()


class BarList(QWidget):
    """Labelled horizontal bars: [(label, value, max, color, suffix)]."""

    def __init__(self, row_height=30, parent=None):
        super().__init__(parent)
        self.rows = []
        self.row_height = row_height
        self.label_width = 150

    def set_rows(self, rows, label_width=150):
        self.rows = rows
        self.label_width = label_width
        self.setMinimumHeight(max(1, len(rows)) * self.row_height)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(360, max(1, len(self.rows)) * self.row_height)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(13)
        fm = QFontMetrics(f)
        val_w = 64
        for i, (text, value, maximum, color, suffix) in enumerate(self.rows):
            y = i * self.row_height
            mid = y + self.row_height / 2
            p.setFont(f)
            p.setPen(QColor(C["text_2"]))
            p.drawText(QRectF(0, y, self.label_width - 8, self.row_height), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, self.label_width - 12))
            bx = self.label_width
            bw = max(10, self.width() - self.label_width - val_w)
            track = QPainterPath()
            track.addRoundedRect(QRectF(bx, mid - 3, bw, 6), 3, 3)
            p.fillPath(track, QColor("#EDEDF0"))
            if value:
                frac = 0 if not maximum else max(0.0, min(1.0, value / maximum))
                bar = QPainterPath()
                bar.addRoundedRect(QRectF(bx, mid - 3, max(6, bw * frac), 6), 3, 3)
                p.fillPath(bar, QColor(color or C["ink"]))
            fb = QFont(f)
            fb.setWeight(QFont.Weight.DemiBold)
            p.setFont(fb)
            p.setPen(QColor(C["text"]))
            shown = "—" if value is None else f"{value}{suffix or ''}"
            p.drawText(QRectF(bx + bw + 8, y, val_w - 8, self.row_height),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, shown)
        p.end()
