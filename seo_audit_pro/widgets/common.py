# -*- coding: utf-8 -*-
"""Base UI components shared by every screen."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal, Property
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from seo_audit_pro import icons
from seo_audit_pro.theme import C, STATUS, SEVERITY


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def vbox(parent=None, margins=(0, 0, 0, 0), spacing=0):
    lay = QVBoxLayout(parent) if parent is not None else QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(parent=None, margins=(0, 0, 0, 0), spacing=0):
    lay = QHBoxLayout(parent) if parent is not None else QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def label(text="", role=None, wrap=False, selectable=False):
    """Plain-text label. Server and engine text (e.g. "add <link rel=canonical>")
    must never be interpreted as markup; callers opt into rich text explicitly."""
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        lbl.setObjectName(role)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def divider():
    line = QFrame()
    line.setObjectName("Divider")
    return line


def button(text, variant=None, icon_name=None, size=None, tooltip=None):
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if size:
        btn.setProperty("size", size)
    if icon_name:
        color = "#FFFFFF" if variant == "primary" else (C["danger"] if variant == "danger" else C["text_2"])
        btn.setIcon(icons.icon(icon_name, color, 16))
        btn.setIconSize(QSize(15, 15))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def icon_button(icon_name, tooltip, size=16, color=None):
    btn = QToolButton()
    btn.setIcon(icons.icon(icon_name, color or C["text_2"], size, active_color=C["text"]))
    btn.setIconSize(QSize(size, size))
    btn.setToolTip(tooltip)
    btn.setAccessibleName(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedSize(size + 14, size + 14)
    return btn


class Card(QFrame):
    def __init__(self, parent=None, padding=20, spacing=12):
        super().__init__(parent)
        self.setObjectName("Surface")
        self.body = vbox(self, (padding, padding, padding, padding), spacing)


class BusyButton(QPushButton):
    """Button that disables itself and shows progress text while busy
    (double-submit protection in the UI; the server guards as well)."""

    def __init__(self, text, variant="primary", icon_name=None, size=None):
        super().__init__(text)
        if variant:
            self.setProperty("variant", variant)
        if size:
            self.setProperty("size", size)
        if icon_name:
            self.setIcon(icons.icon(icon_name, "#FFFFFF" if variant == "primary" else C["text_2"], 16))
        self._idle_text = text
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def busy(self, on=True, text=None):
        self.setEnabled(not on)
        self.setText((text or "Working…") if on else self._idle_text)


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------
class Badge(QLabel):
    def __init__(self, text="", fg=C["text_2"], bg=C["neutral_bg"], parent=None):
        super().__init__(text, parent)
        self.set_colors(fg, bg)

    def set_colors(self, fg, bg):
        self.setStyleSheet(f"QLabel {{ color: {fg}; background: {bg}; border-radius: 10px; padding: 2px 8px; "
                           f"font-size: 12px; font-weight: 600; }}")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    @classmethod
    def status(cls, key):
        fg, bg, text = STATUS.get(key, (C["text_2"], C["neutral_bg"], (key or "").title()))
        return cls(text, fg, bg)

    @classmethod
    def severity(cls, key):
        fg, bg, text = SEVERITY.get((key or "").lower(), SEVERITY["info"])
        return cls(text, fg, bg)


def paint_pill(painter, rect, text, fg, bg, font=None):
    """Draw a status pill inside ``rect`` (used by table delegates)."""
    painter.save()
    f = QFont(font) if font else QFont(painter.font())
    f.setPixelSize(12)
    f.setWeight(QFont.Weight.DemiBold)
    painter.setFont(f)
    fm = QFontMetrics(f)
    w = fm.horizontalAdvance(text) + 16
    h = 20
    r = QRectF(rect.x(), rect.center().y() - h / 2 + 0.5, min(w, rect.width()), h)
    path = QPainterPath()
    path.addRoundedRect(r, 10, 10)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillPath(path, QColor(bg))
    painter.setPen(QColor(fg))
    painter.drawText(r, Qt.AlignmentFlag.AlignCenter, fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width()) - 8))
    painter.restore()


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------
_AVATAR_COLORS = ["#3F3F46", "#1D4ED8", "#047857", "#B45309", "#9F1239", "#0E7490", "#4D7C0F", "#6D28D9"]


class Avatar(QWidget):
    def __init__(self, name="", size=28, parent=None):
        super().__init__(parent)
        self._name = name
        self._pix = None
        self._online = None
        self.setFixedSize(size, size)

    def set_name(self, name):
        self._name = name or ""
        self.update()

    def set_pixmap(self, pix):
        self._pix = pix
        self.update()

    def set_online(self, online):
        self._online = online
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, s, s))
        if self._pix and not self._pix.isNull():
            p.setClipPath(path)
            p.drawPixmap(0, 0, s, s, self._pix)
            p.setClipping(False)
        else:
            initials = "".join(w[0] for w in self._name.split()[:2]).upper() or "?"
            color = _AVATAR_COLORS[sum(map(ord, self._name)) % len(_AVATAR_COLORS)] if self._name else "#A1A1AA"
            p.fillPath(path, QColor(color))
            f = QFont(self.font())
            f.setPixelSize(max(9, int(s * 0.38)))
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, initials)
        if self._online is not None:
            d = max(8, s // 3.5)
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(QColor("#12B76A" if self._online else "#A1A1AA"))
            p.drawEllipse(QRectF(s - d, s - d, d - 1, d - 1))
        p.end()


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------
class Spinner(QWidget):
    def __init__(self, size=20, color=C["text_2"], parent=None):
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e):
        self._timer.start(16)
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = 2
        r = QRectF(w, w, self.width() - 2 * w, self.height() - 2 * w)
        track = QColor(self._color)
        track.setAlpha(40)
        p.setPen(QPen(track, w))
        p.drawEllipse(r)
        p.setPen(QPen(self._color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, int(-self._angle * 16), int(100 * 16))
        p.end()


# ---------------------------------------------------------------------------
# Page header, metrics
# ---------------------------------------------------------------------------
class PageHeader(QWidget):
    def __init__(self, title, subtitle="", parent=None):
        super().__init__(parent)
        row = hbox(self, spacing=12)
        col = vbox(spacing=2)
        self.title = label(title, "PageTitle")
        self.subtitle = label(subtitle, "PageSubtitle", wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        if not subtitle:  # pages may fill it in after loading (never show() a widget before it has a parent)
            self.subtitle.hide()
        row.addLayout(col, 1)
        self.actions = hbox(spacing=8)
        row.addLayout(self.actions)

    def add_action(self, widget):
        self.actions.addWidget(widget)
        return widget


class Metric(QWidget):
    """A number with a label; optional hint line. Used in overview strips."""

    def __init__(self, label_text, value="—", hint="", parent=None):
        super().__init__(parent)
        lay = vbox(self, spacing=2)
        self.lbl = label(label_text, "MetricLabel")
        self.val = label(value, "MetricValue")
        self.hint = label(hint, "Meta")
        lay.addWidget(self.lbl)
        lay.addWidget(self.val)
        lay.addWidget(self.hint)
        self.hint.setVisible(bool(hint))

    def set(self, value, hint=None, color=None):
        self.val.setText(str(value))
        self.val.setStyleSheet(f"color: {color};" if color else "")
        if hint is not None:
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))


class MetricStrip(QFrame):
    """Horizontal row of metrics separated by hairlines, inside one surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Surface")
        self.row = hbox(self, (0, 0, 0, 0), 0)
        self.metrics = {}

    def add(self, key, label_text, hint=""):
        if self.metrics:
            sep = QFrame()
            sep.setFixedWidth(1)
            sep.setStyleSheet(f"background: {C['divider']};")
            self.row.addWidget(sep)
        holder = QWidget()
        lay = vbox(holder, (20, 16, 20, 16))
        m = Metric(label_text, "—", hint)
        lay.addWidget(m)
        self.row.addWidget(holder, 1)
        self.metrics[key] = m
        return m


# ---------------------------------------------------------------------------
# Loading / empty / error / offline / forbidden states
# ---------------------------------------------------------------------------
class StateView(QWidget):
    action = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = vbox(self)
        outer.addStretch(1)
        box = QWidget()
        box.setMaximumWidth(420)
        lay = vbox(box, spacing=10)
        self.spinner = Spinner(24)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label("", "SectionTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = label("", "Muted", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMinimumWidth(360)
        self.btn = button("Try again", icon_name="refresh")
        self.btn.clicked.connect(self.action.emit)
        for w in (self.spinner, self.icon):
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        lay.addSpacing(4)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignHCenter)
        row = hbox()
        row.addStretch(1)
        row.addWidget(box)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)

    def show_state(self, kind, title, text="", action_text=None, icon_name=None, action_icon=None):
        self.spinner.setVisible(kind == "loading")
        self.icon.setVisible(kind != "loading")
        name = icon_name or {"empty": "inbox", "error": "alert_circle", "offline": "wifi_off",
                             "forbidden": "lock"}.get(kind, "info")
        self.icon.setPixmap(icons.pixmap(name, C["text_3"], 28, 1.5))
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))
        self.btn.setVisible(bool(action_text))
        if action_text:
            self.btn.setText(action_text)
            # Retry-style states default to the refresh icon; other actions pass their own (or "").
            icon_key = action_icon if action_icon is not None else ("refresh" if kind in ("error", "offline") else "")
            self.btn.setIcon(icons.icon(icon_key, C["text_2"], 16) if icon_key else QIcon())


class StatefulArea(QStackedWidget):
    """Wraps content with loading / empty / error / offline / forbidden states."""
    retry = Signal()

    def __init__(self, content, parent=None, skeleton="table"):
        """``skeleton``: "table", "cards", "detail" or None (spinner)."""
        super().__init__(parent)
        self.content = content
        self.state = StateView()
        self._empty_action = None
        self.state.action.connect(lambda: (self._empty_action or self.retry.emit)())
        self.addWidget(content)
        self.addWidget(self.state)
        self.skeleton = None
        if skeleton:
            from seo_audit_pro.widgets.skeleton import Skeleton
            self.skeleton = Skeleton(skeleton)
            self.addWidget(self.skeleton)

    def loading(self, text="Loading…"):
        if self.skeleton is not None:
            self.skeleton.setAccessibleName(text)
            self.setCurrentWidget(self.skeleton)
            return
        self.state.show_state("loading", text)
        self.setCurrentWidget(self.state)

    def ready(self):
        self.setCurrentWidget(self.content)

    def empty(self, title, text="", action_text=None, icon_name=None, action_icon=None, on_action=None):
        """Empty state; ``on_action`` runs when its button is pressed."""
        self._empty_action = on_action
        self.state.show_state("empty", title, text, action_text, icon_name, action_icon)
        self.setCurrentWidget(self.state)

    def error(self, err):
        self._empty_action = None
        code = getattr(err, "code", "")
        status = getattr(err, "status", 0)
        if code in ("offline", "timeout"):
            self.state.show_state("offline", "Server unavailable", getattr(err, "message", ""), "Retry")
        elif status == 403:
            self.state.show_state("forbidden", "Permission denied", "Your role does not have access to this area.")
        elif status == 404:
            self.state.show_state("empty", "Not found", getattr(err, "message", ""), None)
        else:
            self.state.show_state("error", "Something went wrong",
                                  getattr(err, "message", "") or "We couldn't load this. Please try again.", "Try again")
        self.setCurrentWidget(self.state)


class Toggle(QWidget):
    """Small animated switch."""
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        self._checked = bool(value)
        self._anim.stop()
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()

    def mouseReleaseEvent(self, _e):
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)

    def _get_pos(self):
        return self._pos

    def _set_pos(self, v):
        self._pos = v
        self.update()

    pos = Property(float, _get_pos, _set_pos)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(C["ink"]) if self._checked else QColor("#D4D4D8")
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 34, 20), 10, 10)
        p.fillPath(path, track)
        x = 2 + self._pos * 14
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(x, 2, 16, 16))
        p.end()


def logo_label(size=28):
    from seo_audit_pro import brand
    lbl = QLabel()
    dpr = lbl.devicePixelRatioF() or 1.0
    lbl.setPixmap(brand.logo_pixmap(size, max(dpr, 2.0)))
    lbl.setFixedSize(size, size)
    return lbl


def scaled_pixmap(data_bytes, size):
    pix = QPixmap()
    pix.loadFromData(data_bytes)
    if pix.isNull():
        return None
    return pix.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                      Qt.TransformationMode.SmoothTransformation)


class Segmented(QWidget):
    """Compact segmented control (single choice)."""
    changed = Signal(object)

    def __init__(self, options, current=None):
        super().__init__()
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # paint the track from the stylesheet
        self.setStyleSheet(
            f"#Segmented {{ background: #EDEDF0; border-radius: 7px; }}"
            f"#Segmented QPushButton {{ border: none; background: transparent; min-height: 26px; padding: 0 12px;"
            f" border-radius: 5px; color: {C['text_2']}; font-weight: 600; }}"
            f"#Segmented QPushButton:hover {{ color: {C['text']}; }}"
            f"#Segmented QPushButton:checked {{ background: {C['surface']}; color: {C['text']}; }}")
        row = hbox(self, (3, 3, 3, 3), 2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for text, value in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, v=value: self.changed.emit(v))
            self.group.addButton(b)
            self.buttons[value] = b
            row.addWidget(b)
        self.set_value(current if current in self.buttons else options[0][1])

    def set_value(self, value):
        if value in self.buttons:
            self.buttons[value].setChecked(True)

    def value(self):
        return next((v for v, b in self.buttons.items() if b.isChecked()), None)

    def set_text(self, value, text):
        if value in self.buttons:
            self.buttons[value].setText(text)


GITHUB_URL = "https://github.com/farixdev"


def signature(compact=False):
    """Maker's mark: "by Faris" in a handwritten style, with the GitHub link.
    Quiet on purpose (small, grey); ``compact`` stacks it for narrow places."""
    box = QWidget()
    row = (vbox if compact else hbox)(box, spacing=0 if compact else 6)
    by = QLabel("crafted by")
    by.setStyleSheet(f"color: {C['text_3']}; font-size: 10px;")
    name = QLabel("Faris")
    font = QFont("Segoe Script")
    if not font.exactMatch():
        font = QFont("Brush Script MT")
    font.setPixelSize(17 if compact else 15)
    font.setItalic(True)
    name.setFont(font)
    name.setStyleSheet(f"color: {C['text_2']}; padding-bottom: 2px;")
    link = QLabel(f'<a href="{GITHUB_URL}" style="color: {C["text_3"]}; text-decoration: none;">github/farixdev</a>')
    link.setStyleSheet("font-size: 10px;")
    link.setOpenExternalLinks(True)
    link.setToolTip(GITHUB_URL)
    link.setCursor(Qt.CursorShape.PointingHandCursor)
    if compact:
        line = hbox(spacing=5)
        line.addWidget(by, 0, Qt.AlignmentFlag.AlignBottom)
        line.addWidget(name, 0, Qt.AlignmentFlag.AlignBottom)
        line.addStretch(1)
        row.addLayout(line)
        row.addWidget(link)
    else:
        for w in (by, name, link):
            row.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
    box.setToolTip(f"Made by Faris · {GITHUB_URL}")
    return box
