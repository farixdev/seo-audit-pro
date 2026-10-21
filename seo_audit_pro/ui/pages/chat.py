# -*- coding: utf-8 -*-
"""
Internal collaboration: team channels and direct messages between people the
server allows to talk to each other. Realtime over the WebSocket hub, with a
slow poll only while the hub is disconnected.
"""

import mimetypes
import os
import time
from datetime import date, timedelta

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QFrame, QLabel, QMenu, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QWidget)

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.realtime import get_hub
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, ROLE_LABEL
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import (Avatar, StatefulArea, button, hbox, icon_button, label, scaled_pixmap, vbox)
from seo_audit_pro.widgets.feedback import Dialog, SearchBox, confirm, show_error, text_area, toast

REACTIONS = [("ack", "check", "Acknowledged"), ("like", "thumbs_up", "Like"), ("seen", "eye", "Seen"),
             ("question", "help", "Question")]
REACTION_ICON = {k: i for k, i, _t in REACTIONS}
REACTION_LABEL = {k: t for k, _i, t in REACTIONS}
BUBBLE_MAX = 520
MAX_UPLOAD = 10 * 1024 * 1024
_UPLOAD_TYPES = "Documents, images and data (*.pdf *.doc *.docx *.png *.jpg *.jpeg *.txt *.csv *.xlsx)"

_AVATAR_CACHE = {}


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_avatar(owner, user_id, avatar_widget, size):
    """Fetch a user's profile picture once and apply it (initials remain otherwise)."""
    if user_id in _AVATAR_CACHE:
        if _AVATAR_CACHE[user_id] is not None:
            avatar_widget.set_pixmap(_AVATAR_CACHE[user_id])
        return

    def done(data):
        pix = scaled_pixmap(data, size) if isinstance(data, (bytes, bytearray)) else None
        _AVATAR_CACHE[user_id] = pix
        if pix is not None:
            avatar_widget.set_pixmap(pix)

    _AVATAR_CACHE[user_id] = None
    tasks.run(api.get, f"/api/users/{user_id}/avatar", on_done=done, on_error=lambda _e: None, owner=avatar_widget)


def _day_label(iso):
    dt = fmt.parse(iso)
    if not dt:
        return ""
    d = dt.date()
    today = date.today()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return dt.strftime("%A, %d %B %Y")


def _team_tile(size=32):
    tile = QLabel()
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet(f"background: {C['ink']}; border-radius: {size // 4}px;")
    tile.setPixmap(icons.pixmap("teams", "#FFFFFF", int(size * 0.5)))
    return tile


# ---------------------------------------------------------------------------
# Conversation list
# ---------------------------------------------------------------------------
class ConversationRow(QFrame):
    clicked = Signal(dict)

    def __init__(self, conv, owner):
        super().__init__()
        self.conv = conv
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ConvRow")
        self.setFixedHeight(60)
        row = hbox(self, (10, 8, 10, 8), 10)
        if conv["type"] == "team":
            row.addWidget(_team_tile(34))
        else:
            other = conv.get("other_user") or {}
            self.avatar = Avatar(other.get("name", ""), 34)
            self.avatar.set_online(bool(other.get("online")))
            if other.get("has_avatar"):
                _load_avatar(owner, other["id"], self.avatar, 34)
            row.addWidget(self.avatar)
        col = vbox(spacing=2)
        top = hbox(spacing=6)
        title = label(conv.get("title") or "")
        unread = int(conv.get("unread") or 0)
        title.setStyleSheet(f"font-weight: {'700' if unread else '600'};")
        fm = QFontMetrics(title.font())
        title.setText(fm.elidedText(conv.get("title") or "", Qt.TextElideMode.ElideRight, 150))
        top.addWidget(title, 1)
        last = conv.get("last_message") or {}
        when = label(fmt.relative(last.get("created_at")) if last else "", "Meta")
        top.addWidget(when)
        col.addLayout(top)
        bottom = hbox(spacing=6)
        if last:
            who = last.get("sender_name") or ""
            mine = who == (session.user or {}).get("name")
            prev = f"{'You' if mine else who.split(' ')[0]}: {last.get('preview') or ''}" if conv["type"] == "team" or mine \
                else (last.get("preview") or "")
        else:
            prev = "Team channel" if conv["type"] == "team" else "No messages yet"
        preview = label("", "Muted")
        preview.setText(QFontMetrics(preview.font()).elidedText(prev.replace("\n", " "), Qt.TextElideMode.ElideRight, 190))
        if unread:
            preview.setStyleSheet(f"color: {C['text']};")
        bottom.addWidget(preview, 1)
        if unread:
            badge = QLabel("99+" if unread > 99 else str(unread))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setMinimumWidth(20)
            badge.setFixedHeight(18)
            badge.setStyleSheet(f"background: {C['ink']}; color: #FFFFFF; border-radius: 9px; font-size: 11px;"
                                " font-weight: 700; padding: 0 6px;")
            bottom.addWidget(badge)
        col.addLayout(bottom)
        row.addLayout(col, 1)
        self.set_selected(False)

    def set_selected(self, selected):
        self.setStyleSheet(
            f"#ConvRow {{ background: {C['selection'] if selected else 'transparent'}; border-radius: 8px; }}"
            f"#ConvRow:hover {{ background: {C['selection'] if selected else '#F4F4F6'}; }}"
            "#ConvRow QLabel { background: transparent; }")

    def set_online(self, online):
        if hasattr(self, "avatar"):
            self.avatar.set_online(online)
            if self.conv.get("other_user"):
                self.conv["other_user"]["online"] = online

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.conv)
        super().mouseReleaseEvent(e)


class SearchResultRow(QFrame):
    clicked = Signal(dict)

    def __init__(self, hit, title):
        super().__init__()
        self.hit = hit
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("HitRow")
        self.setStyleSheet("#HitRow { border-radius: 8px; } #HitRow:hover { background: #F4F4F6; }"
                           "#HitRow QLabel { background: transparent; }")
        col = vbox(self, (12, 8, 12, 8), 2)
        top = hbox(spacing=6)
        t = label(title)
        t.setStyleSheet("font-weight: 600;")
        top.addWidget(t, 1)
        top.addWidget(label(fmt.relative(hit.get("created_at")), "Meta"))
        col.addLayout(top)
        body = label(f"{hit.get('sender_name') or ''}: {(hit.get('body') or '').replace(chr(10), ' ')}", "Muted", wrap=True)
        body.setMaximumHeight(36)
        col.addWidget(body)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.hit)
        super().mouseReleaseEvent(e)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
class MessageWidget(QWidget):
    reply = Signal(dict)
    react = Signal(dict, str)
    edit = Signal(dict)
    remove = Signal(dict)
    open_attachment = Signal(dict)

    def __init__(self, msg, show_sender, compact, is_team):
        super().__init__()
        self.msg = msg
        mine = msg["sender_id"] == session.user_id
        self.mine = mine
        deleted = bool(msg.get("deleted"))
        outer = hbox(self, (0, 2 if compact else 10, 0, 0), 8)
        if not mine:
            if compact:
                spacer = QWidget()
                spacer.setFixedWidth(30)
                outer.addWidget(spacer)
            else:
                av = Avatar(msg.get("sender_name") or "", 30)
                outer.addWidget(av, 0, Qt.AlignmentFlag.AlignTop)
        else:
            outer.addStretch(1)

        column = vbox(spacing=4)
        if show_sender and not mine:
            name = label(msg.get("sender_name") or "")
            name.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {C['text_2']};")
            column.addWidget(name)
        bubble = QFrame()
        bubble.setObjectName("Bubble")
        if deleted:
            style = (f"#Bubble {{ background: transparent; border: 1px dashed {C['border_strong']}; border-radius: 12px; }}"
                     f"#Bubble QLabel {{ color: {C['text_3']}; background: transparent; }}")
        elif mine:
            style = (f"#Bubble {{ background: {C['ink']}; border-radius: 12px; }}"
                     "#Bubble QLabel { color: #FFFFFF; background: transparent; }")
        else:
            style = (f"#Bubble {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 12px; }}"
                     f"#Bubble QLabel {{ color: {C['text']}; background: transparent; }}")
        bubble.setStyleSheet(style)
        bl = vbox(bubble, (12, 8, 12, 7), 4)

        if msg.get("reply_to") and not deleted:
            q = msg["reply_to"]
            quote = QFrame()
            quote.setObjectName("Quote")
            qc = "#52525B" if mine else C["border_strong"]
            quote.setStyleSheet(f"#Quote {{ border: none; border-left: 2px solid {qc}; }}"
                                f"#Quote QLabel {{ color: {'#D4D4D8' if mine else C['text_3']}; background: transparent; }}")
            ql = vbox(quote, (8, 0, 0, 0), 0)
            qn = label(q.get("sender_name") or "")
            qn.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {'#E4E4E7' if mine else C['text_2']};")
            ql.addWidget(qn)
            qt = label(q.get("preview") or "", wrap=True)
            qt.setStyleSheet(f"font-size: 12px; color: {'#D4D4D8' if mine else C['text_3']};")
            qt.setTextFormat(Qt.TextFormat.PlainText)
            ql.addWidget(qt)
            bl.addWidget(quote)

        text = "This message was deleted." if deleted else (msg.get("body") or "")
        if text:
            body = QLabel(text)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if deleted:
                f = body.font()
                f.setItalic(True)
                body.setFont(f)
            # Measure with the stylesheet font applied: short messages stay on one
            # line; long ones wrap at BUBBLE_MAX with correct height-for-width.
            body.ensurePolished()
            fm = QFontMetrics(body.font())
            longest = max((fm.horizontalAdvance(line) for line in text.split("\n")), default=0)
            body.setMinimumWidth(max(40, min(BUBBLE_MAX, longest + 8)))
            body.setMaximumWidth(BUBBLE_MAX)
            body.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
            bl.addWidget(body)

        for att in ([] if deleted else (msg.get("attachments") or [])):
            chip = QPushButton(f"  {att.get('name')}  ·  {fmt.size(att.get('size_bytes'))}")
            chip.setIcon(icons.icon("paperclip", "#FFFFFF" if mine else C["text_2"], 14))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("Download and open")
            chip.setStyleSheet(
                f"QPushButton {{ text-align: left; min-height: 28px; padding: 0 10px; border-radius: 6px; font-weight: 500;"
                f" background: {'#2E2E33' if mine else C['surface_alt']}; color: {'#FFFFFF' if mine else C['text']};"
                f" border: 1px solid {'#3F3F46' if mine else C['border']}; }}"
                f"QPushButton:hover {{ background: {'#3F3F46' if mine else '#F0F0F2'}; }}")
            chip.clicked.connect(lambda _c=False, a=att: self.open_attachment.emit(a))
            bl.addWidget(chip)

        meta = hbox(spacing=6)
        meta.addStretch(1)
        stamp = fmt.time_(msg.get("created_at")) + ("  · edited" if msg.get("edited_at") and not deleted else "")
        ts = label(stamp)
        ts.setStyleSheet(f"font-size: 11px; color: {'#A1A1AA' if mine else C['text_3']};")
        ts.setToolTip(fmt.datetime_(msg.get("created_at")))
        meta.addWidget(ts)
        bl.addLayout(meta)

        bubble_row = hbox(spacing=6)
        self.actions = QWidget()
        al = hbox(self.actions, spacing=0)
        if not deleted:
            b_reply = icon_button("reply", "Reply", 14)
            b_reply.clicked.connect(lambda: self.reply.emit(self.msg))
            b_react = icon_button("thumbs_up", "React", 14)
            b_react.clicked.connect(lambda: self._react_menu(b_react))
            b_more = icon_button("more", "More actions", 14)
            b_more.clicked.connect(lambda: self._menu(b_more.mapToGlobal(b_more.rect().bottomLeft())))
            for b in (b_reply, b_react, b_more):
                al.addWidget(b)
        self.actions.setVisible(False)
        if mine:
            bubble_row.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignVCenter)
            bubble_row.addWidget(bubble)
        else:
            bubble_row.addWidget(bubble)
            bubble_row.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addLayout(bubble_row)

        rx = msg.get("reactions") or {}
        if rx and not deleted:
            rrow = hbox(spacing=4)
            if mine:
                rrow.addStretch(1)
            for key, _ic, title in REACTIONS:
                if key not in rx:
                    continue
                info = rx[key]
                chip = QToolButton()
                chip.setIcon(icons.icon(REACTION_ICON[key], C["accent"] if info.get("mine") else C["text_2"], 13))
                chip.setIconSize(QSize(13, 13))
                chip.setText(str(info.get("count", 0)))
                chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.setToolTip(f"{title}{' (you reacted)' if info.get('mine') else ''}")
                chip.setStyleSheet(
                    f"QToolButton {{ border-radius: 10px; padding: 1px 7px; font-size: 11px; font-weight: 600;"
                    f" background: {C['accent_soft'] if info.get('mine') else C['surface']};"
                    f" color: {C['accent'] if info.get('mine') else C['text_2']};"
                    f" border: 1px solid {'#C7D4FB' if info.get('mine') else C['border']}; }}"
                    "QToolButton:hover { border-color: #A1A1AA; }")
                chip.clicked.connect(lambda _c=False, k=key: self.react.emit(self.msg, k))
                rrow.addWidget(chip)
            if not mine:
                rrow.addStretch(1)
            column.addLayout(rrow)
        outer.addLayout(column)
        if not mine:
            outer.addStretch(1)
        bubble.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bubble.customContextMenuRequested.connect(lambda pos: self._menu(bubble.mapToGlobal(pos)))

    def enterEvent(self, e):
        if not self.msg.get("deleted"):
            self.actions.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.actions.setVisible(False)
        super().leaveEvent(e)

    def _react_menu(self, anchor):
        m = QMenu(self)
        for key, ic, title in REACTIONS:
            m.addAction(icons.icon(ic, C["text_2"]), title, lambda k=key: self.react.emit(self.msg, k))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _menu(self, global_pos):
        if self.msg.get("deleted"):
            return
        m = QMenu(self)
        m.addAction(icons.icon("reply", C["text_2"]), "Reply", lambda: self.reply.emit(self.msg))
        sub = m.addMenu(icons.icon("thumbs_up", C["text_2"]), "React")
        for key, ic, title in REACTIONS:
            sub.addAction(icons.icon(ic, C["text_2"]), title, lambda k=key: self.react.emit(self.msg, k))
        if self.msg.get("body"):
            m.addAction(icons.icon("copy", C["text_2"]), "Copy text",
                        lambda: QGuiApplication.clipboard().setText(self.msg.get("body") or ""))
        if self.mine:
            m.addSeparator()
            m.addAction(icons.icon("edit", C["text_2"]), "Edit", lambda: self.edit.emit(self.msg))
        if self.mine or session.is_manager():
            if not self.mine:
                m.addSeparator()
            m.addAction(icons.icon("trash", C["danger"]), "Delete", lambda: self.remove.emit(self.msg))
        m.exec(global_pos)


class DaySeparator(QWidget):
    def __init__(self, text):
        super().__init__()
        row = hbox(self, (0, 14, 0, 4), 10)
        left, right = QFrame(), QFrame()
        for line in (left, right):
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {C['border']};")
        row.addWidget(left, 1)
        row.addWidget(label(text, "Meta"))
        row.addWidget(right, 1)


class Composer(QPlainTextEdit):
    send = Signal()
    typing = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Write a message — Enter to send, Shift+Enter for a new line")
        self.setStyleSheet(f"QPlainTextEdit {{ border: 1px solid {C['border_strong']}; border-radius: 8px; padding: 8px 10px;"
                           f" background: {C['surface']}; }} QPlainTextEdit:focus {{ border-color: {C['focus']}; }}")
        self.textChanged.connect(self._resize)
        self.textChanged.connect(lambda: self.toPlainText() and self.typing.emit())
        self._resize()

    def _resize(self):
        raw = int(self.document().size().height())
        lines = max(1, min(6, raw))
        fm = QFontMetrics(self.font())
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if raw > 6
                                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(lines * fm.lineSpacing() + 28)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)


class ContactPickerDialog(Dialog):
    def __init__(self, parent):
        super().__init__(parent, "New message", "People you can message, based on your role and team.", 460)
        self.chosen = None
        self.search = SearchBox("Search people", 150)
        self.search.search.connect(self._filter)
        self.body.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(340)
        self.scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C['border']}; border-radius: 8px; background: {C['surface']}; }}")
        self.host = QWidget()
        self.list = vbox(self.host, (6, 6, 6, 6), 2)
        self.scroll.setWidget(self.host)
        self.body.addWidget(self.scroll, 1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        self.footer.addWidget(cancel)
        self.contacts = []
        self.list.addWidget(label("Loading people…", "Muted"))
        tasks.run(api.get, "/api/chat/contacts", on_done=self._loaded,
                  on_error=lambda e: self.show_error(e), owner=self)

    def _loaded(self, data):
        self.contacts = data.get("items") or []
        self._filter(self.search.text())
        self.search.setFocus()

    def _filter(self, text):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        q = (text or "").strip().lower()
        rows = [c for c in self.contacts if not q or q in c["name"].lower() or q in (c.get("email") or "").lower()
                or q in (c.get("team_name") or "").lower()]
        rows.sort(key=lambda c: (not c.get("online"), c["name"].lower()))
        if not rows:
            self.list.addWidget(label("No one matches." if self.contacts else "There is no one you can message yet.", "Muted"))
        for c in rows:
            self.list.addWidget(self._row(c))
        self.list.addStretch(1)

    def _row(self, c):
        w = QFrame()
        w.setObjectName("PickRow")
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        w.setStyleSheet("#PickRow { border-radius: 6px; } #PickRow:hover { background: #F4F4F6; }"
                        "#PickRow QLabel { background: transparent; }")
        row = hbox(w, (8, 6, 8, 6), 10)
        av = Avatar(c["name"], 30)
        av.set_online(bool(c.get("online")))
        if c.get("has_avatar"):
            _load_avatar(self, c["id"], av, 30)
        row.addWidget(av)
        col = vbox(spacing=1)
        n = label(c["name"])
        n.setStyleSheet("font-weight: 600;")
        col.addWidget(n)
        sub = ROLE_LABEL.get(c.get("role"), "") + (f" · {c['team_name']}" if c.get("team_name") else "")
        col.addWidget(label(sub, "Meta"))
        row.addLayout(col, 1)
        row.addWidget(label("Online" if c.get("online") else "Offline", "Meta"))
        w.mouseReleaseEvent = lambda _e, c=c: self._pick(c)
        return w

    def _pick(self, c):
        self.chosen = c
        self.accept()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
class ChatPage(Page):
    title = "Chat"
    nav_key = "chat"

    def __init__(self, ws):
        super().__init__(ws, margins=(0, 0, 0, 0), spacing=0)
        self.conversations = []
        self.rows = {}
        self.current = None
        self.messages = []
        self.has_more = False
        self.reply_to = None
        self.pending = []           # uploads waiting to be sent
        self._send_key = None
        self._last_typing = 0.0
        self._pending_open = None

        root = hbox(spacing=0)
        self.body.addLayout(root, 1)

        # Left pane -----------------------------------------------------------
        left = QFrame()
        left.setObjectName("ChatLeft")
        left.setFixedWidth(320)
        left.setStyleSheet(f"#ChatLeft {{ background: {C['surface']}; border-right: 1px solid {C['border']}; }}")
        ll = vbox(left, (14, 16, 14, 10), 10)
        lh = hbox(spacing=8)
        lh.addWidget(label("Conversations", "SectionTitle"), 1)
        new_btn = button("New", icon_name="plus", size="sm", tooltip="Start a direct message")
        new_btn.clicked.connect(self.new_message)
        lh.addWidget(new_btn)
        ll.addLayout(lh)
        self.search = SearchBox("Search messages")
        self.search.search.connect(self._search)
        ll.addWidget(self.search)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_layout = vbox(self.list_host, (0, 0, 0, 0), 2)
        self.list_scroll.setWidget(self.list_host)
        self.list_area = StatefulArea(self.list_scroll, skeleton=None)
        self._list_retry = self.load_conversations
        self.list_area.retry.connect(lambda: self._list_retry())
        ll.addWidget(self.list_area, 1)
        root.addWidget(left)

        # Right pane ----------------------------------------------------------
        conv = QWidget()
        cl = vbox(conv, (0, 0, 0, 0), 0)
        head = QFrame()
        head.setObjectName("ChatHead")
        head.setStyleSheet(f"#ChatHead {{ background: {C['surface']}; border-bottom: 1px solid {C['border']}; }}")
        hl = hbox(head, (20, 12, 20, 12), 12)
        self.head_icon_host = hbox()
        hl.addLayout(self.head_icon_host)
        hcol = vbox(spacing=1)
        self.head_title = label("")
        self.head_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.head_sub = label("", "Meta")
        hcol.addWidget(self.head_title)
        hcol.addWidget(self.head_sub)
        hl.addLayout(hcol, 1)
        self.refresh_btn = icon_button("refresh", "Reload conversation")
        self.refresh_btn.clicked.connect(lambda: self.open_conversation(self.current["id"]) if self.current else None)
        hl.addWidget(self.refresh_btn)
        cl.addWidget(head)

        self.msg_scroll = QScrollArea()
        self.msg_scroll.setWidgetResizable(True)
        self.msg_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.msg_scroll.setStyleSheet(f"QScrollArea {{ background: {C['canvas']}; border: none; }}")
        self.msg_host = QWidget()
        self.msg_host.setObjectName("Canvas")
        self.msg_layout = vbox(self.msg_host, (24, 12, 24, 16), 0)
        self.msg_scroll.setWidget(self.msg_host)
        self.msg_scroll.verticalScrollBar().rangeChanged.connect(self._range_changed)
        cl.addWidget(self.msg_scroll, 1)

        bottom = QFrame()
        bottom.setObjectName("ChatBottom")
        bottom.setStyleSheet(f"#ChatBottom {{ background: {C['surface']}; border-top: 1px solid {C['border']}; }}")
        bt = vbox(bottom, (20, 8, 20, 14), 6)
        self.typing_label = label("", "Meta")
        self.typing_label.setFixedHeight(16)
        bt.addWidget(self.typing_label)
        self.reply_bar = QFrame()
        self.reply_bar.setObjectName("ReplyBar")
        self.reply_bar.setStyleSheet(f"#ReplyBar {{ background: {C['surface_alt']}; border-left: 3px solid {C['ink']};"
                                     " border-radius: 4px; } #ReplyBar QLabel { background: transparent; }")
        rb = hbox(self.reply_bar, (10, 6, 4, 6), 8)
        ric = QLabel()
        ric.setPixmap(icons.pixmap("reply", C["text_2"], 14))
        rb.addWidget(ric)
        self.reply_text = label("", "Muted")
        rb.addWidget(self.reply_text, 1)
        cancel_reply = icon_button("x", "Cancel reply", 14)
        cancel_reply.clicked.connect(self.cancel_reply)
        rb.addWidget(cancel_reply)
        self.reply_bar.setVisible(False)
        bt.addWidget(self.reply_bar)
        self.pending_host = QWidget()
        self.pending_layout = hbox(self.pending_host, spacing=6)
        self.pending_host.setVisible(False)
        bt.addWidget(self.pending_host)
        crow = hbox(spacing=8)
        self.attach_btn = icon_button("paperclip", "Attach a file (max 10 MB)", 18)
        self.attach_btn.clicked.connect(self.attach_file)
        crow.addWidget(self.attach_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self.composer = Composer()
        self.composer.send.connect(self.send)
        self.composer.typing.connect(self._typing)
        crow.addWidget(self.composer, 1)
        self.send_btn = button("Send", "primary", icon_name="send")
        self.send_btn.clicked.connect(self.send)
        crow.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        bt.addLayout(crow)
        self.disabled_note = label("", "Meta")
        self.disabled_note.setVisible(False)
        bt.addWidget(self.disabled_note)
        cl.addWidget(bottom)

        self.conv_area = StatefulArea(conv, skeleton=None)
        self._conv_retry = None
        self.conv_area.retry.connect(lambda: self._conv_retry and self._conv_retry())
        root.addWidget(self.conv_area, 1)
        self._show_no_selection()

        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.timeout.connect(lambda: self.typing_label.setText(""))
        self._poll = QTimer(self)
        self._poll.setInterval(15000)
        self._poll.timeout.connect(self._poll_tick)
        self._poll.start()
        self._list_reload = QTimer(self)
        self._list_reload.setSingleShot(True)
        self._list_reload.setInterval(300)
        self._list_reload.timeout.connect(lambda: self.load_conversations(quiet=True))
        self._stick_bottom = True

    # -- lifecycle -----------------------------------------------------------
    def activate(self, conversation_id=None, **_):
        self._pending_open = conversation_id
        self.load_conversations(quiet=bool(self.conversations))
        if self.current and not conversation_id:
            self.open_conversation(self.current["id"], quiet=True)

    def _show_no_selection(self):
        self.conv_area.empty("Select a conversation", "Choose a team channel or a person on the left, or start a new "
                                                      "direct message.", "New message", icon_name="chat")
        self.conv_area.state.btn.setIcon(icons.icon("plus", C["text_2"], 16))
        self._conv_retry = self.new_message

    def _restore_retry(self):
        self.conv_area.state.btn.setIcon(icons.icon("refresh", C["text_2"], 16))
        self._conv_retry = lambda: self.open_conversation(self.current["id"]) if self.current else None

    # -- conversation list ------------------------------------------------------
    def load_conversations(self, quiet=False):
        if not quiet:
            self.list_area.loading("Loading conversations…")
        self.fetch("convs", api.get, "/api/chat/conversations", on_done=self._render_list, on_error=self._list_error)

    def _list_error(self, err):
        if not self.conversations:
            self.list_area.error(err)

    def _render_list(self, data):
        self.conversations = data.get("items") or []
        self.ws.sidebar.set_badge("chat", data.get("unread_total", 0))
        if self.search.text().strip():
            self.list_area.ready()
        else:
            self._draw_list()
        if self._pending_open:
            target, self._pending_open = self._pending_open, None
            self.open_conversation(target)

    def _draw_list(self):
        _clear(self.list_layout)
        self.rows = {}
        teams = [c for c in self.conversations if c["type"] == "team"]
        directs = [c for c in self.conversations if c["type"] == "direct"]
        if not self.conversations:
            self.list_area.empty("No conversations", "Start a direct message with a colleague.", "New message",
                                 icon_name="chat")
            self.list_area.state.btn.setIcon(icons.icon("plus", C["text_2"], 16))
            self._list_retry = self.new_message
            return
        self._list_retry = self.load_conversations
        self.list_area.state.btn.setIcon(icons.icon("refresh", C["text_2"], 16))
        for title, group in (("TEAM CHANNELS", teams), ("DIRECT MESSAGES", directs)):
            if not group:
                continue
            sec = label(title, "SidebarSection")
            sec.setContentsMargins(0, 8, 0, 4)
            self.list_layout.addWidget(sec)
            for c in group:
                row = ConversationRow(c, self)
                row.clicked.connect(lambda conv: self.open_conversation(conv["id"]))
                row.set_selected(bool(self.current) and self.current["id"] == c["id"])
                self.rows[c["id"]] = row
                self.list_layout.addWidget(row)
        if not directs:
            hint = label("No direct messages yet. Use New to start one.", "Meta", wrap=True)
            hint.setContentsMargins(10, 8, 10, 0)
            self.list_layout.addWidget(hint)
        self.list_layout.addStretch(1)
        self.list_area.ready()

    def _search(self, text):
        if len(text) < 2:
            self._draw_list()
            return
        titles = {c["id"]: c.get("title") or "" for c in self.conversations}

        def done(data):
            _clear(self.list_layout)
            hits = data.get("items") or []
            sec = label(f"{len(hits)} {'MATCH' if len(hits) == 1 else 'MATCHES'}", "SidebarSection")
            sec.setContentsMargins(0, 8, 0, 4)
            self.list_layout.addWidget(sec)
            if not hits:
                self.list_layout.addWidget(label("No messages contain that text.", "Muted"))
            for hit in hits:
                row = SearchResultRow(hit, titles.get(hit["conversation_id"], "Conversation"))
                row.clicked.connect(lambda h: (self.search.clear(), self.open_conversation(h["conversation_id"])))
                self.list_layout.addWidget(row)
            self.list_layout.addStretch(1)
            self.list_area.ready()

        self.fetch("search", api.get, "/api/chat/search", params={"q": text}, on_done=done,
                   on_error=lambda e: show_error(self, e))

    def new_message(self):
        dlg = ContactPickerDialog(self)
        if not dlg.exec() or not dlg.chosen:
            return

        def done(res):
            conv = res["conversation"]
            if not any(c["id"] == conv["id"] for c in self.conversations):
                self.conversations.append(conv)
                self._draw_list()
            self.open_conversation(conv["id"])
            self.load_conversations(quiet=True)

        tasks.run(api.post, "/api/chat/conversations/direct", {"user_id": dlg.chosen["id"]}, on_done=done,
                  on_error=lambda e: show_error(self, e), owner=self)

    # -- open conversation -------------------------------------------------------
    def open_conversation(self, conv_id, quiet=False):
        conv = next((c for c in self.conversations if c["id"] == conv_id), None)
        if conv is None:
            toast(self, "That conversation is not available.", "warning")
            return
        switching = not self.current or self.current["id"] != conv_id
        self.current = conv
        for cid, row in self.rows.items():
            row.set_selected(cid == conv_id)
        self._restore_retry()
        if switching:
            self.cancel_reply()
            self.pending = []
            self._render_pending()
            self.composer.clear()
            self._send_key = None
            self.typing_label.setText("")
            self._render_header()
            self.messages = []
            self.conv_area.loading("Loading messages…")
        self.fetch("msgs", api.get, f"/api/chat/conversations/{conv_id}/messages", params={"limit": 60},
                   on_done=lambda d: self._messages_loaded(conv_id, d, switching), on_error=self._msgs_error)

    def _msgs_error(self, err):
        if not self.messages:
            self.conv_area.error(err)
        else:
            show_error(self, err)

    def _render_header(self):
        conv = self.current
        _clear(self.head_icon_host)
        if conv["type"] == "team":
            self.head_icon_host.addWidget(_team_tile(36))
            self.head_title.setText(conv.get("title") or "Team")
            self.head_sub.setText("Team channel · visible to team members and managers")
            can_send, note = True, ""
        else:
            other = conv.get("other_user") or {}
            av = Avatar(other.get("name", ""), 36)
            av.set_online(bool(other.get("online")))
            if other.get("has_avatar"):
                _load_avatar(self, other["id"], av, 36)
            self.head_icon_host.addWidget(av)
            self.head_title.setText(other.get("name") or conv.get("title") or "")
            if not other.get("is_active", True):
                self.head_sub.setText(f"{ROLE_LABEL.get(other.get('role'), '')} · Account deactivated")
                can_send, note = False, "This account is deactivated. You can read the history but not send messages."
            else:
                self.head_sub.setText(f"{ROLE_LABEL.get(other.get('role'), '')} · {'Online' if other.get('online') else 'Offline'}")
                can_send, note = True, ""
        for w in (self.composer, self.send_btn, self.attach_btn):
            w.setEnabled(can_send)
        self.disabled_note.setText(note)
        self.disabled_note.setVisible(bool(note))

    def _messages_loaded(self, conv_id, data, switching):
        if not self.current or self.current["id"] != conv_id:
            return
        self.messages = data.get("items") or []
        self.has_more = bool(data.get("has_more"))
        self._stick_bottom = True if switching else self._near_bottom()
        self._render_messages()
        self.conv_area.ready()
        row_conv = next((c for c in self.conversations if c["id"] == conv_id), None)
        if row_conv and row_conv.get("unread"):
            row_conv["unread"] = 0
            self._list_reload.start()
        if switching:
            self.composer.setFocus()

    def load_earlier(self):
        if not self.current or not self.messages:
            return
        conv_id = self.current["id"]
        oldest = self.messages[0]["created_at"]
        bar = self.msg_scroll.verticalScrollBar()
        before_max = bar.maximum() - bar.value()

        def done(data):
            if not self.current or self.current["id"] != conv_id:
                return
            older = [m for m in (data.get("items") or []) if m["id"] not in {x["id"] for x in self.messages}]
            self.messages = older + self.messages
            self.has_more = bool(data.get("has_more"))
            self._stick_bottom = False
            self._render_messages()
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - before_max))

        self.fetch("earlier", api.get, f"/api/chat/conversations/{conv_id}/messages",
                   params={"limit": 60, "before": oldest}, on_done=done, on_error=lambda e: show_error(self, e))

    # -- rendering ---------------------------------------------------------------
    def _render_messages(self):
        self.msg_host.setUpdatesEnabled(False)
        _clear(self.msg_layout)
        if self.has_more:
            more = button("Load earlier messages", "ghost", size="sm")
            more.clicked.connect(self.load_earlier)
            self.msg_layout.addWidget(more, 0, Qt.AlignmentFlag.AlignHCenter)
        if not self.messages:
            empty = QWidget()
            el = vbox(empty, (0, 60, 0, 0), 6)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("chat", C["text_3"], 28, 1.5))
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(ic)
            t = label("No messages yet", "SectionTitle")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(t)
            s = label("Say hello — messages here are visible only to the people in this conversation.", "Muted")
            s.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(s)
            self.msg_layout.addWidget(empty)
        is_team = self.current["type"] == "team"
        prev = None
        for m in self.messages:
            day = _day_label(m["created_at"])
            new_day = not prev or _day_label(prev["created_at"]) != day
            if new_day:
                self.msg_layout.addWidget(DaySeparator(day))
            compact = bool(prev) and not new_day and prev["sender_id"] == m["sender_id"] and not m.get("reply_to") \
                and _seconds_between(prev["created_at"], m["created_at"]) < 300
            self.msg_layout.addWidget(self._message_widget(m, show_sender=is_team and not compact, compact=compact,
                                                           is_team=is_team))
            prev = m
        self.msg_layout.addStretch(1)
        self.msg_host.setUpdatesEnabled(True)

    def _message_widget(self, m, show_sender, compact, is_team):
        w = MessageWidget(m, show_sender, compact, is_team)
        w.reply.connect(self.start_reply)
        w.react.connect(self.toggle_reaction)
        w.edit.connect(self.edit_message)
        w.remove.connect(self.delete_message)
        w.open_attachment.connect(self.download_attachment)
        return w

    def _near_bottom(self):
        bar = self.msg_scroll.verticalScrollBar()
        return bar.maximum() - bar.value() < 80

    def _range_changed(self, _lo, hi):
        if self._stick_bottom:
            self.msg_scroll.verticalScrollBar().setValue(hi)

    def _merge_message(self, m):
        """Insert or replace one message; returns True if it was new."""
        for i, existing in enumerate(self.messages):
            if existing["id"] == m["id"]:
                self.messages[i] = m
                return False
        self.messages.append(m)
        self.messages.sort(key=lambda x: (x["created_at"], x["id"]))
        return True

    # -- composer ----------------------------------------------------------------
    def _typing(self):
        if not self.current:
            return
        now = time.monotonic()
        if now - self._last_typing > 3:
            self._last_typing = now
            get_hub().send({"type": "typing", "conversation_id": self.current["id"]})

    def start_reply(self, msg):
        self.reply_to = msg
        preview = (msg.get("body") or "Attachment").replace("\n", " ")
        fm = QFontMetrics(self.reply_text.font())
        self.reply_text.setText(fm.elidedText(f"Replying to {msg.get('sender_name')}: {preview}",
                                              Qt.TextElideMode.ElideRight, 520))
        self.reply_bar.setVisible(True)
        self.composer.setFocus()

    def cancel_reply(self):
        self.reply_to = None
        self.reply_bar.setVisible(False)

    def attach_file(self):
        if not self.current:
            return
        if len(self.pending) >= 5:
            toast(self, "Attach at most 5 files per message.", "warning")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Attach file", os.path.expanduser("~"), _UPLOAD_TYPES)
        if not path:
            return
        if os.path.getsize(path) > MAX_UPLOAD:
            toast(self, "The file is larger than 10 MB.", "error")
            return
        self.attach_btn.setEnabled(False)

        def upload():
            name = os.path.basename(path)
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            with open(path, "rb") as fh:
                return api.request("POST", "/api/chat/uploads", files={"file": (name, fh, mime)}, timeout=120)["upload"]

        def done(up):
            self.attach_btn.setEnabled(True)
            self.pending.append(up)
            self._render_pending()

        def fail(err):
            self.attach_btn.setEnabled(True)
            show_error(self, err, "Attachment not added")

        tasks.run(upload, on_done=done, on_error=fail, owner=self)

    def _render_pending(self):
        _clear(self.pending_layout)
        for up in self.pending:
            chip = QFrame()
            chip.setObjectName("PendChip")
            chip.setStyleSheet(f"#PendChip {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 6px; }}"
                               "#PendChip QLabel { background: transparent; }")
            row = hbox(chip, (8, 2, 2, 2), 6)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("paperclip", C["text_2"], 13))
            row.addWidget(ic)
            row.addWidget(label(f"{up['name']} · {fmt.size(up['size_bytes'])}", "Meta"))
            rm = icon_button("x", "Remove", 12)
            rm.clicked.connect(lambda _c=False, uid=up["id"]: self._remove_pending(uid))
            row.addWidget(rm)
            self.pending_layout.addWidget(chip)
        self.pending_layout.addStretch(1)
        self.pending_host.setVisible(bool(self.pending))

    def _remove_pending(self, upload_id):
        self.pending = [p for p in self.pending if p["id"] != upload_id]
        self._render_pending()

    def send(self):
        if not self.current or not self.send_btn.isEnabled():
            return
        text = self.composer.toPlainText().strip()
        if not text and not self.pending:
            return
        conv_id = self.current["id"]
        payload = {"body": text, "upload_ids": [p["id"] for p in self.pending]}
        if self.reply_to:
            payload["reply_to_id"] = self.reply_to["id"]
        self._send_key = self._send_key or api.new_idempotency_key()
        self.send_btn.setEnabled(False)
        self.composer.setReadOnly(True)

        def done(res):
            self._send_key = None
            self.send_btn.setEnabled(True)
            self.composer.setReadOnly(False)
            self.composer.clear()
            self.cancel_reply()
            self.pending = []
            self._render_pending()
            if self.current and self.current["id"] == conv_id:
                self._stick_bottom = True
                self._merge_message(res["message"])
                self._render_messages()
            self._list_reload.start()
            self.composer.setFocus()

        def fail(err):
            self.send_btn.setEnabled(True)
            self.composer.setReadOnly(False)
            if err.status and err.status < 500:
                self._send_key = None
            show_error(self, err, "Message not sent")

        tasks.run(api.post, f"/api/chat/conversations/{conv_id}/messages", payload, idempotency_key=self._send_key,
                  on_done=done, on_error=fail, owner=self)

    # -- message actions -----------------------------------------------------------
    def _after_mutation(self):
        # The server pushes 'message_updated'; reload only when the hub is down.
        if not get_hub().connected and self.current:
            self.open_conversation(self.current["id"], quiet=True)

    def toggle_reaction(self, msg, reaction):
        tasks.run(api.post, f"/api/chat/messages/{msg['id']}/reactions", {"reaction": reaction},
                  on_done=lambda _r: self._after_mutation(), on_error=lambda e: show_error(self, e), owner=self)

    def edit_message(self, msg):
        dlg = Dialog(self, "Edit message", "", 520)
        area = text_area("", msg.get("body") or "", 120)
        dlg.body.addWidget(area)
        dlg.add_buttons("Save")

        def save():
            new = area.toPlainText().strip()
            if not new:
                dlg.show_error(api.ApiError(422, "A message cannot be empty. Delete it instead."))
                return
            dlg.primary.busy(True, "Saving…")
            tasks.run(api.patch, f"/api/chat/messages/{msg['id']}", {"body": new},
                      on_done=lambda _r: (dlg.accept(), self._after_mutation()),
                      on_error=lambda e: (dlg.primary.busy(False), dlg.show_error(e)), owner=dlg)

        dlg.primary.clicked.connect(save)
        dlg.exec()

    def delete_message(self, msg):
        if not confirm(self, "Delete this message?", "Everyone in the conversation will see that a message was deleted.",
                       "Delete", danger=True):
            return
        tasks.run(api.delete, f"/api/chat/messages/{msg['id']}", on_done=lambda _r: self._after_mutation(),
                  on_error=lambda e: show_error(self, e), owner=self)

    def download_attachment(self, att):
        toast(self, f"Downloading {att.get('name')}…", "info", 1800)

        def done(path):
            toast(self, f"Saved to {os.path.dirname(path)}", "success")
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

        tasks.run(api.download, f"/api/chat/attachments/{att['id']}", filename=att.get("name"), on_done=done,
                  on_error=lambda e: show_error(self, e, "Download failed"), owner=self)

    # -- realtime --------------------------------------------------------------------
    def on_realtime(self, ev):
        kind = ev.get("type")
        if kind in ("message", "message_updated"):
            conv_id = ev.get("conversation_id")
            msg = ev.get("message") or {}
            if self.current and conv_id == self.current["id"] and self.isVisible():
                stick = self._near_bottom()
                self._merge_message(msg)
                self._stick_bottom = stick or msg.get("sender_id") == session.user_id
                self._render_messages()
                if kind == "message" and msg.get("sender_id") != session.user_id:
                    self.typing_label.setText("")
                    tasks.run(api.post, f"/api/chat/conversations/{conv_id}/read", owner=self)
            if kind == "message":
                self._list_reload.start()
        elif kind == "typing":
            if self.current and ev.get("conversation_id") == self.current["id"] and ev.get("user_id") != session.user_id:
                self.typing_label.setText(f"{ev.get('name') or 'Someone'} is typing…")
                self._typing_timer.start(4000)
        elif kind == "presence":
            uid, online = ev.get("user_id"), bool(ev.get("online"))
            for c in self.conversations:
                other = c.get("other_user") or {}
                if other.get("id") == uid:
                    other["online"] = online
                    row = self.rows.get(c["id"])
                    if row:
                        row.set_online(online)
                    if self.current and self.current["id"] == c["id"]:
                        self._render_header()

    def _poll_tick(self):
        if get_hub().connected or not self.isVisible():
            return
        self.load_conversations(quiet=True)
        if self.current:
            self.open_conversation(self.current["id"], quiet=True)


def _seconds_between(a, b):
    da, db = fmt.parse(a), fmt.parse(b)
    if not da or not db:
        return 1e9
    return abs((db - da).total_seconds())


def _clear(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            _clear(it.layout())
