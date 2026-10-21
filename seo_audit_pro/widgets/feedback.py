# -*- coding: utf-8 -*-
"""Toasts, dialogs, confirmations, side drawer, form fields."""

from PySide6.QtCore import QEasingCurve, QEvent, QParallelAnimationGroup, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QWidget)

from seo_audit_pro import icons
from seo_audit_pro.theme import C
from seo_audit_pro.widgets.common import BusyButton, button, hbox, icon_button, label, vbox


# ---------------------------------------------------------------------------
# Toast
# ---------------------------------------------------------------------------
class _Toast(QFrame):
    def __init__(self, host, text, kind):
        super().__init__(host)
        colors = {"success": C["success"], "error": C["danger"], "warning": C["warning"], "info": C["text_2"]}
        icon_name = {"success": "check_circle", "error": "alert_circle", "warning": "alert_triangle"}.get(kind, "info")
        self.setStyleSheet(f"QFrame {{ background: {C['ink']}; border-radius: 8px; }} QLabel {{ color: #FFFFFF; }}")
        row = hbox(self, (14, 10, 16, 10), 10)
        ic = QLabel()
        ic.setPixmap(icons.pixmap(icon_name, "#FFFFFF" if kind == "info" else _light(colors[kind]), 16))
        row.addWidget(ic)
        msg = QLabel(text)
        msg.setTextFormat(Qt.TextFormat.PlainText)
        msg.setWordWrap(True)
        msg.setMaximumWidth(420)
        row.addWidget(msg)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)
        self.adjustSize()


def _light(hex_color):
    return {C["success"]: "#6CE9A6", C["danger"]: "#FDA29B", C["warning"]: "#FEC84B"}.get(hex_color, "#FFFFFF")


_ACTIVE_TOASTS = []


def toast(parent, text, kind="info", ms=3600):
    host = parent.window() if parent else QApplication.activeWindow()
    if host is None:
        return
    t = _Toast(host, text, kind)
    _ACTIVE_TOASTS.append(t)
    y = host.height() - 24
    for other in _ACTIVE_TOASTS[:-1]:
        y -= other.height() + 8
    t.move(host.width() - t.width() - 24, y - t.height())
    t.show()
    t.raise_()

    def _close():
        if t in _ACTIVE_TOASTS:
            _ACTIVE_TOASTS.remove(t)
        t.deleteLater()

    QTimer.singleShot(ms, _close)


def show_error(parent, err, prefix=None):
    """Present an ApiError (or any exception) without technical details."""
    msg = getattr(err, "message", None) or "Something went wrong. We couldn't complete this operation."
    toast(parent, f"{prefix}: {msg}" if prefix else msg, "error", 5200)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class Dialog(QDialog):
    """Standard modal: title, optional description, body, footer buttons."""

    def __init__(self, parent, title, description="", width=480):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setStyleSheet(f"QDialog {{ background: {C['surface']}; }}")
        outer = vbox(self, (24, 22, 24, 20), 16)
        head = vbox(spacing=4)
        t = label(title, "SectionTitle")
        t.setStyleSheet("font-size: 16px;")
        head.addWidget(t)
        if description:
            head.addWidget(label(description, "Muted", wrap=True))
        outer.addLayout(head)
        self.body = vbox(spacing=12)
        outer.addLayout(self.body, 1)
        self.error = label("", "FieldError", wrap=True)
        self.error.setVisible(False)
        outer.addWidget(self.error)
        self.footer = hbox(spacing=8)
        self.footer.addStretch(1)
        outer.addLayout(self.footer)

    def add_buttons(self, primary_text, cancel_text="Cancel", variant="primary"):
        cancel = button(cancel_text)
        cancel.clicked.connect(self.reject)
        self.primary = BusyButton(primary_text, variant)
        self.primary.setDefault(True)
        self.footer.addWidget(cancel)
        self.footer.addWidget(self.primary)
        return self.primary

    def show_error(self, err):
        msg = getattr(err, "message", str(err))
        self.error.setText(msg)
        self.error.setVisible(True)

    def clear_error(self):
        self.error.setVisible(False)


class Field(QWidget):
    """Label + input + inline error."""

    def __init__(self, title, widget, hint=""):
        super().__init__()
        lay = vbox(self, spacing=6)
        self.title = label(title, "FieldLabel")
        self.title.setBuddy(widget)
        lay.addWidget(self.title)
        lay.addWidget(widget)
        self.widget = widget
        self.hint = label(hint, "Meta", wrap=True)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)
        self.err = label("", "FieldError", wrap=True)
        self.err.setVisible(False)
        lay.addWidget(self.err)

    def set_error(self, text):
        self.err.setText(text or "")
        self.err.setVisible(bool(text))
        self.widget.setProperty("invalid", bool(text))
        self.widget.style().unpolish(self.widget)
        self.widget.style().polish(self.widget)


def line_edit(placeholder="", text=""):
    w = QLineEdit(text)
    w.setPlaceholderText(placeholder)
    w.setClearButtonEnabled(False)
    return w


def text_area(placeholder="", text="", min_height=120):
    w = QPlainTextEdit(text)
    w.setPlaceholderText(placeholder)
    w.setMinimumHeight(min_height)
    return w


def combo(items, current=None):
    """items: list of (label, value)."""
    w = QComboBox()
    for text, value in items:
        w.addItem(text, value)
    if current is not None:
        idx = w.findData(current)
        if idx >= 0:
            w.setCurrentIndex(idx)
    return w


class PasswordInput(QWidget):
    returnPressed = Signal()

    def __init__(self, placeholder="", object_name=None):
        super().__init__()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)
        if object_name:
            self.edit.setObjectName(object_name)
        self.edit.returnPressed.connect(self.returnPressed.emit)
        self._toggle = self.edit.addAction(icons.icon("eye", C["text_3"], 16), QLineEdit.ActionPosition.TrailingPosition)
        self._toggle.setToolTip("Show password")
        self._toggle.triggered.connect(self._flip)
        lay = vbox(self)
        lay.addWidget(self.edit)
        self.setFocusProxy(self.edit)

    def _flip(self):
        hidden = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self._toggle.setIcon(icons.icon("eye_off" if hidden else "eye", C["text_3"], 16))
        self._toggle.setToolTip("Hide password" if hidden else "Show password")

    def text(self):
        return self.edit.text()

    def clear(self):
        self.edit.clear()

    def setProperty(self, name, value):  # noqa: N802 - forward invalid-state styling
        return self.edit.setProperty(name, value)

    def style(self):
        return self.edit.style()


def confirm(parent, title, text, confirm_text="Confirm", danger=False):
    dlg = Dialog(parent, title, text, 440)
    dlg.add_buttons(confirm_text, variant="danger" if danger else "primary")
    dlg.primary.clicked.connect(dlg.accept)
    return dlg.exec() == QDialog.DialogCode.Accepted


class SecretRevealDialog(Dialog):
    """Show a one-time secret (temporary password) with copy action."""

    def __init__(self, parent, title, description, secret):
        super().__init__(parent, title, description, 460)
        box = QFrame()
        box.setObjectName("SecretBox")
        box.setStyleSheet(f"QFrame#SecretBox {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; "
                          "border-radius: 6px; }")
        row = hbox(box, (12, 10, 8, 10), 8)
        val = label(secret, "Mono", selectable=True)
        val.setStyleSheet("font-size: 15px; color: #18181B;")
        row.addWidget(val, 1)
        copy = button("Copy", icon_name="copy", size="sm")
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(secret), copy.setText("Copied")))
        row.addWidget(copy)
        self.body.addWidget(box)
        self.body.addWidget(label("This password is shown only once. Share it through a secure channel. "
                                  "The user must choose a new password at first sign-in.", "Meta", wrap=True))
        done = button("Done", "primary")
        done.clicked.connect(self.accept)
        self.footer.addWidget(done)


# ---------------------------------------------------------------------------
# Drawer (right side panel)
# ---------------------------------------------------------------------------
class Drawer(QWidget):
    """Slide-in panel over the right edge of its host. Esc or the scrim closes it."""
    closed = Signal()

    def __init__(self, host, width=520):
        super().__init__(host)
        self.host = host
        self.panel_width = width
        self.setVisible(False)
        self.scrim = QWidget(self)
        self.scrim.setStyleSheet("background: rgba(24, 24, 27, 0.18);")
        self._scrim_fx = QGraphicsOpacityEffect(self.scrim)
        self.scrim.setGraphicsEffect(self._scrim_fx)
        self.scrim.mousePressEvent = lambda _e: self.close_drawer()
        self.panel = QFrame(self)
        self.panel.setStyleSheet(f"QFrame#DrawerPanel {{ background: {C['surface']}; border-left: 1px solid {C['border']}; }}")
        self.panel.setObjectName("DrawerPanel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(32)
        shadow.setOffset(-4, 0)
        shadow.setColor(QColor(0, 0, 0, 40))
        self.panel.setGraphicsEffect(shadow)
        pl = vbox(self.panel)
        head = hbox(spacing=8)
        head.setContentsMargins(24, 18, 16, 14)
        self.title = label("", "SectionTitle")
        self.title.setStyleSheet("font-size: 16px;")
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1)
        close = icon_button("x", "Close (Esc)")
        close.clicked.connect(self.close_drawer)
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        pl.addLayout(head)
        line = QFrame()
        line.setObjectName("Divider")
        pl.addWidget(line)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.content_layout = vbox(self.content, (24, 18, 24, 24), 16)
        self.scroll.setWidget(self.content)
        pl.addWidget(self.scroll, 1)
        self._anim = None
        host.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._layout(open_=True)
        return False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_drawer()
        else:
            super().keyPressEvent(e)

    def clear(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                _clear_layout(item.layout())

    def _layout(self, open_):
        self.setGeometry(0, 0, self.host.width(), self.host.height())
        self.scrim.setGeometry(0, 0, self.host.width(), self.host.height())
        w = min(self.panel_width, self.host.width() - 40)
        x = self.host.width() - w if open_ else self.host.width()
        self.panel.setGeometry(x, 0, w, self.host.height())

    def open_drawer(self, title):
        self.title.setText(title)
        self._layout(open_=False)
        self.show()
        self.raise_()
        self.setFocus()
        w = self.panel.width()
        start = QRect(self.host.width(), 0, w, self.host.height())
        end = QRect(self.host.width() - w, 0, w, self.host.height())
        group = QParallelAnimationGroup(self)
        a = QPropertyAnimation(self.panel, b"geometry")
        a.setDuration(180)
        a.setStartValue(start)
        a.setEndValue(end)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        b = QPropertyAnimation(self._scrim_fx, b"opacity")
        b.setDuration(180)
        b.setStartValue(0.0)
        b.setEndValue(1.0)
        group.addAnimation(a)
        group.addAnimation(b)
        self._anim = group
        group.start()

    def close_drawer(self):
        if not self.isVisible():
            return
        w = self.panel.width()
        a = QPropertyAnimation(self.panel, b"geometry", self)
        a.setDuration(140)
        a.setStartValue(self.panel.geometry())
        a.setEndValue(QRect(self.host.width(), 0, w, self.host.height()))
        a.setEasingCurve(QEasingCurve.Type.InCubic)
        a.finished.connect(self.hide)
        a.finished.connect(self.closed.emit)
        self._anim = a
        a.start()


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def clear_layout(layout):
    _clear_layout(layout)


def kv_row(key, value_widget_or_text):
    """Key / value line used in detail panels."""
    w = QWidget()
    row = hbox(w, spacing=12)
    k = label(key, "Meta")
    k.setFixedWidth(130)
    k.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    row.addWidget(k)
    if isinstance(value_widget_or_text, QWidget):
        row.addWidget(value_widget_or_text, 1)
    else:
        v = label(str(value_widget_or_text) if value_widget_or_text not in (None, "") else "—", wrap=True, selectable=True)
        row.addWidget(v, 1)
    return w


class SearchBox(QLineEdit):
    """Debounced search input."""
    search = Signal(str)

    def __init__(self, placeholder="Search", delay=300):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.addAction(icons.icon("search", C["text_3"], 16), QLineEdit.ActionPosition.LeadingPosition)
        self.setClearButtonEnabled(True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay)
        self._timer.timeout.connect(lambda: self.search.emit(self.text().strip()))
        self.textChanged.connect(lambda _t: self._timer.start())
        self.returnPressed.connect(lambda: (self._timer.stop(), self.search.emit(self.text().strip())))

