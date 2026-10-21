# -*- coding: utf-8 -*-
"""Notifications: everything the server has told this user, newest first."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QWidget

from seo_audit_pro import icons
from seo_audit_pro.core import api, fmt
from seo_audit_pro.theme import C
from seo_audit_pro.ui.pages.base import Page
from seo_audit_pro.widgets.common import Segmented, StatefulArea, button, hbox, icon_button, label, vbox
from seo_audit_pro.widgets.feedback import show_error, toast

TYPE_ICON = {
    "lead_created": "leads", "lead_assigned": "leads", "lead_reassigned": "leads",
    "audit_completed": "audit", "audit_failed": "alert_triangle", "report_ready": "report",
    "new_message": "chat", "email_sent": "send", "email_failed": "alert_circle",
    "team_change": "teams", "password_changed": "lock",
}
TYPE_COLOR = {"audit_failed": C["danger"], "email_failed": C["danger"], "email_sent": C["success"],
              "audit_completed": C["success"]}


class NotificationRow(QFrame):
    clicked = Signal(dict)
    mark_read = Signal(dict)

    def __init__(self, n):
        super().__init__()
        self.n = n
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("NotifRow")
        unread = not n.get("is_read")
        self.setStyleSheet(
            f"#NotifRow {{ background: {'#FBFCFF' if unread else C['surface']}; border: none;"
            f" border-bottom: 1px solid {C['divider']}; }} #NotifRow:hover {{ background: #F6F7F9; }}")
        row = hbox(self, (20, 14, 16, 14), 14)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {C['accent'] if unread else 'transparent'}; border-radius: 4px;")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        tile = QLabel()
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(f"background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 8px;")
        tile.setPixmap(icons.pixmap(TYPE_ICON.get(n.get("type"), "bell"), TYPE_COLOR.get(n.get("type"), C["text_2"]), 16))
        row.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)
        col = vbox(spacing=3)
        title = label(n.get("title") or "")
        title.setStyleSheet(f"font-weight: {'600' if unread else '500'};")
        col.addWidget(title)
        body = label(n.get("body") or "", "Muted", wrap=True)
        col.addWidget(body)
        row.addLayout(col, 1)
        when = label(fmt.relative(n.get("created_at")), "Meta")
        when.setToolTip(fmt.datetime_(n.get("created_at")))
        row.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
        if unread:
            mark = icon_button("check", "Mark as read", 15)
            mark.clicked.connect(lambda: self.mark_read.emit(self.n))
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        else:
            spacer = QWidget()
            spacer.setFixedWidth(29)
            row.addWidget(spacer)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.n)
        super().mouseReleaseEvent(e)


class NotificationsPage(Page):
    title = "Notifications"
    nav_key = "notifications"

    def __init__(self, ws):
        super().__init__(ws, spacing=16)
        self.page_no, self.pages, self.filter = 1, 1, "all"
        self._dirty = False
        h = self.header("Notifications", "Lead assignments, audit results, reports, messages and email delivery.")
        self.mark_all = h.add_action(button("Mark all read", icon_name="check"))
        self.mark_all.clicked.connect(self._mark_all)
        bar = hbox(spacing=8)
        self.seg = Segmented([("All", "all"), ("Unread", "unread")])
        self.seg.changed.connect(self._set_filter)
        bar.addWidget(self.seg)
        bar.addStretch(1)
        self.count_label = label("", "Meta")
        bar.addWidget(self.count_label)
        self.body.addLayout(bar)

        surface = QFrame()
        surface.setObjectName("Surface")
        sl = vbox(surface)
        self.list_host = QWidget()
        self.list_layout = vbox(self.list_host)
        from PySide6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_host)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll = scroll
        sl.addWidget(scroll, 1)
        foot = QWidget()
        foot.setStyleSheet(f"background: {C['surface']}; border-top: 1px solid {C['border']};")
        fl = hbox(foot, (16, 8, 12, 8), 8)
        self.range_label = label("", "Meta")
        fl.addWidget(self.range_label, 1)
        self.prev_btn = button("", icon_name="chevron_left", tooltip="Newer")
        self.next_btn = button("", icon_name="chevron_right", tooltip="Older")
        for b in (self.prev_btn, self.next_btn):
            b.setFixedWidth(34)
        self.prev_btn.clicked.connect(lambda: self._go(self.page_no - 1))
        self.next_btn.clicked.connect(lambda: self._go(self.page_no + 1))
        fl.addWidget(self.prev_btn)
        fl.addWidget(self.next_btn)
        sl.addWidget(foot)
        self.area = StatefulArea(surface)
        self.area.retry.connect(self.load)
        self.body.addWidget(self.area, 1)

    def activate(self, **_):
        self.load()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self._dirty = False
            self.load()

    def _set_filter(self, value):
        self.filter = value
        self.page_no = 1
        self.load()

    def _go(self, page):
        self.page_no = max(1, min(self.pages, page))
        self.load()

    def load(self):
        if not self.list_layout.count():
            self.area.loading("Loading notifications…")
        params = {"page": self.page_no, "page_size": 30}
        if self.filter == "unread":
            params["unread"] = "1"
        self.fetch("list", api.get, "/api/notifications", params=params, on_done=self._render, on_error=self.area.error)

    def _render(self, data):
        while self.list_layout.count():
            it = self.list_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        items = data.get("items") or []
        unread = int(data.get("unread") or 0)
        self._publish_unread(unread)
        self.seg.set_text("unread", f"Unread · {unread}" if unread else "Unread")
        self.mark_all.setEnabled(unread > 0)
        self.page_no, self.pages = data.get("page", 1), data.get("pages", 1)
        total = data.get("total", 0)
        self.count_label.setText(fmt.plural(total, "notification") if total else "")
        size = data.get("page_size", 30)
        start = (self.page_no - 1) * size + 1 if total else 0
        self.range_label.setText(f"{start}–{min(total, self.page_no * size)} of {fmt.number(total)}" if total else "")
        self.prev_btn.setEnabled(self.page_no > 1)
        self.next_btn.setEnabled(self.page_no < self.pages)
        if not items:
            if self.filter == "unread":
                self.area.empty("You're all caught up.", "There are no unread notifications.", icon_name="check_circle")
            else:
                self.area.empty("You're all caught up.", "New activity on your leads, audits and messages will appear here.",
                                icon_name="bell")
            return
        for n in items:
            row = NotificationRow(n)
            row.clicked.connect(self._open)
            row.mark_read.connect(self._mark_one)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)
        self.area.ready()

    def _publish_unread(self, n):
        setter = getattr(self.ws, "set_unread", None)
        if setter:
            setter(n)

    def _open(self, n):
        self.ws.open_notification(n)

    def _mark_one(self, n):
        self.fetch("mark", api.post, "/api/notifications/read", {"ids": [n["id"]]},
                   on_done=lambda r: (self._publish_unread(r.get("unread", 0)), self.load()),
                   on_error=lambda e: show_error(self, e))

    def _mark_all(self):
        self.mark_all.setEnabled(False)

        def done(r):
            self._publish_unread(r.get("unread", 0))
            toast(self, "All notifications marked as read.", "success")
            self.load()

        def fail(e):
            self.mark_all.setEnabled(True)
            show_error(self, e)

        self.fetch("mark", api.post, "/api/notifications/read", {"all": True}, on_done=done, on_error=fail)

    def on_realtime(self, ev):
        if ev.get("type") != "notification":
            return
        if self.isVisible():
            self.load()
        else:
            self._dirty = True
