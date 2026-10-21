# -*- coding: utf-8 -*-
"""
Application shell: collapsible sidebar, top bar (title, global search,
connection status, notifications, profile) and the lazily-built page stack.
"""

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractButton, QFrame, QLabel, QListWidget, QListWidgetItem, QMenu, QScrollArea,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from seo_audit_pro import APP_NAME, icons
from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.core.realtime import get_hub
from seo_audit_pro.core.session import session
from seo_audit_pro.theme import C, ROLE_LABEL
from seo_audit_pro.widgets.common import Avatar, Spinner, button, hbox, icon_button, label, logo_label, vbox
from seo_audit_pro.widgets.feedback import SearchBox, show_error


def _esc(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Navigation model per role (UI convenience only — the API enforces access)
# ---------------------------------------------------------------------------
def navigation_for(role):
    if role == "MANAGER":
        return [
            (None, [("dashboard", "Dashboard", "dashboard")]),
            ("Workspace", [("leads", "Lead Bank", "leads"), ("audits", "Audits", "audit"), ("reports", "Reports", "report")]),
            ("Organization", [("teams", "Teams", "teams"), ("users", "Users", "user")]),
            ("Outreach", [("outreach", "Emails", "mail"), ("outreach_activity", "Activity", "activity")]),
            ("Communication", [("chat", "Chat", "chat")]),
            ("System", [("notifications", "Notifications", "bell"), ("audit_logs", "Audit Logs", "logs"),
                        ("settings", "Settings", "settings")]),
        ]
    if role == "TEAM_LEAD":
        return [(None, [("dashboard", "Dashboard", "dashboard"), ("leads", "Lead Bank", "leads"), ("audits", "Audits", "audit"),
                        ("reports", "Reports", "report"), ("team", "Team", "teams"), ("outreach", "Outreach", "mail"),
                        ("chat", "Chat", "chat"), ("notifications", "Notifications", "bell"), ("profile", "Profile", "user")])]
    return [(None, [("dashboard", "Dashboard", "dashboard"), ("leads", "My Leads", "leads"), ("audits", "Audits", "audit"),
                    ("reports", "Reports", "report"), ("outreach", "Outreach", "mail"), ("chat", "Chat", "chat"),
                    ("notifications", "Notifications", "bell"), ("profile", "Profile", "user")])]


class NavItem(QAbstractButton):
    def __init__(self, key, text, icon_name):
        super().__init__()
        self.key, self.text_value, self.icon_name = key, text, icon_name
        self.badge = 0
        self.collapsed = False
        self.active = False  # painted state; set only after navigation succeeds
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setToolTip("")
        self.setAccessibleName(text)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._hover = False

    def set_badge(self, n):
        self.badge = int(n or 0)
        self.update()

    def set_active(self, value):
        self.active = value
        self.setAccessibleDescription("current page" if value else "")
        self.update()

    def set_collapsed(self, value):
        self.collapsed = value
        self.setToolTip(self.text_value if value else "")
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def sizeHint(self):
        return QSize(200, 34)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        if self.active or self._hover or self.hasFocus():
            path = QPainterPath()
            path.addRoundedRect(r, 6, 6)
            p.fillPath(path, QColor(C["sidebar_active"] if self.active else C["sidebar_hover"]))
            if self.active:
                p.setPen(QColor(C["border"]))
                p.drawPath(path)
        color = C["text"] if self.active else C["text_2"]
        ix = 10 if not self.collapsed else (self.width() - 16) / 2
        p.drawPixmap(int(ix), int((self.height() - 16) / 2), icons.pixmap(self.icon_name, color, 16))
        if not self.collapsed:
            f = QFont(self.font())
            f.setPixelSize(13)
            f.setWeight(QFont.Weight.DemiBold if self.active else QFont.Weight.Medium)
            p.setFont(f)
            p.setPen(QColor(color))
            p.drawText(QRectF(36, 0, self.width() - 70, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text_value)
        if self.badge:
            txt = "99+" if self.badge > 99 else str(self.badge)
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setWeight(QFont.Weight.DemiBold)
            p.setFont(f)
            w = QFontMetrics(f).horizontalAdvance(txt) + 10
            if self.collapsed:
                br = QRectF(self.width() / 2 + 2, 3, max(16, w), 16)
            else:
                br = QRectF(self.width() - max(18, w) - 8, (self.height() - 18) / 2, max(18, w), 18)
            bp = QPainterPath()
            bp.addRoundedRect(br, br.height() / 2, br.height() / 2)
            p.fillPath(bp, QColor(C["ink"]))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


class Sidebar(QFrame):
    navigate = Signal(str)
    EXPANDED, COLLAPSED = 232, 60

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(self.EXPANDED)
        self.collapsed = False
        self.items = {}
        lay = vbox(self, (10, 14, 10, 12), 2)
        brand = QWidget()
        brow = hbox(brand, (4, 0, 0, 0), 10)
        brow.addWidget(logo_label(26))
        self.brand_text = label(APP_NAME)
        self.brand_text.setStyleSheet("font-size: 14px; font-weight: 600;")
        brow.addWidget(self.brand_text, 1)
        lay.addWidget(brand)
        lay.addSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_host = QWidget()
        self.nav_layout = vbox(self.nav_host, spacing=2)
        self.scroll.setWidget(self.nav_host)
        lay.addWidget(self.scroll, 1)
        self.toggle = QToolButton()
        self.toggle.setIcon(icons.icon("panel_left", C["text_2"], 16))
        self.toggle.setToolTip("Collapse sidebar")
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.clicked.connect(self.toggle_collapsed)
        lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._anim = None

    def build(self, role):
        while self.nav_layout.count():
            it = self.nav_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.items = {}
        self.section_labels = []
        for section, entries in navigation_for(role):
            if section:
                self.nav_layout.addSpacing(10)
                sl = label(section.upper(), "SidebarSection")
                sl.setFixedHeight(22)
                self.section_labels.append(sl)
                self.nav_layout.addWidget(sl)
            for key, text, icon_name in entries:
                item = NavItem(key, text, icon_name)
                item.clicked.connect(lambda _c=False, k=key: self.navigate.emit(k))
                item.set_collapsed(self.collapsed)
                self.items[key] = item
                self.nav_layout.addWidget(item)
        self.nav_layout.addStretch(1)

    def set_active(self, key):
        for k, item in self.items.items():
            item.set_active(k == key)

    def set_badge(self, key, n):
        if key in self.items:
            self.items[key].set_badge(n)

    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        end = self.COLLAPSED if self.collapsed else self.EXPANDED
        self.brand_text.setVisible(not self.collapsed)
        for sl in getattr(self, "section_labels", []):
            sl.setVisible(not self.collapsed)
        for item in self.items.values():
            item.set_collapsed(self.collapsed)
        self.toggle.setToolTip("Expand sidebar" if self.collapsed else "Collapse sidebar")
        for prop in (b"minimumWidth", b"maximumWidth"):
            a = QPropertyAnimation(self, prop, self)
            a.setDuration(160)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.setStartValue(self.width())
            a.setEndValue(end)
            a.start()
            self._anim = a
        from seo_audit_pro.core import settings
        settings.put("sidebar_collapsed", self.collapsed)


# ---------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------
class ConnectionIndicator(QWidget):
    def __init__(self):
        super().__init__()
        row = hbox(self, spacing=6)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.text = label("", "Meta")
        row.addWidget(self.dot)
        row.addWidget(self.text)
        self.set_state("connecting")

    def set_state(self, state):
        color, text = {"online": ("#12B76A", "Connected"), "connecting": ("#F79009", "Connecting"),
                       "offline": ("#F04438", "Offline")}.get(state, ("#A1A1AA", state))
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.text.setText(text)
        self.setToolTip({"online": "Connected to the server", "connecting": "Connecting to the server…",
                         "offline": "The server is unreachable. Data shown may be out of date."}.get(state, ""))


class SearchPopup(QFrame):
    chosen = Signal(str, dict)

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setStyleSheet(f"QFrame {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 8px; }}")
        lay = vbox(self, (6, 6, 6, 6), 0)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._pick)
        self.list.itemClicked.connect(self._pick)
        lay.addWidget(self.list)
        self.setFixedWidth(440)

    def show_results(self, results, anchor):
        self.list.clear()
        groups = [("leads", "Leads", "leads", lambda r: (r["company"], r["normalized_domain"])),
                  ("audits", "Audits", "audit", lambda r: (r["company"], f"{r['status'].title()} · {fmt.date(r['created_at'])}")),
                  ("users", "People", "user", lambda r: (r["name"], r["email"])),
                  ("teams", "Teams", "teams", lambda r: (r["name"], ""))]
        count = 0
        for key, title, icon_name, render in groups:
            rows = results.get(key) or []
            if not rows:
                continue
            head = QListWidgetItem(title.upper())
            head.setFlags(Qt.ItemFlag.NoItemFlags)
            head.setForeground(QColor(C["text_3"]))
            f = head.font()
            f.setPixelSize(11)
            f.setWeight(QFont.Weight.DemiBold)
            head.setFont(f)
            self.list.addItem(head)
            for r in rows:
                primary, secondary = render(r)
                it = QListWidgetItem(icons.icon(icon_name, C["text_2"], 16), f"{primary}    {secondary}".rstrip())
                it.setData(Qt.ItemDataRole.UserRole, (key, r))
                it.setSizeHint(QSize(0, 32))
                self.list.addItem(it)
                count += 1
        if not count:
            it = QListWidgetItem("No matches")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(it)
        self.list.setFixedHeight(min(420, self.list.count() * 32 + 8))
        self.adjustSize()
        self.move(anchor)
        self.show()

    def _pick(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.hide()
            self.chosen.emit(data[0], data[1])


class NotificationPopover(QFrame):
    """Fixed-width popover anchored under the bell. The list scrolls inside a
    height capped to the screen space below the anchor, so the popup never
    resizes itself off-screen (which made Windows push it upward)."""
    open_entity = Signal(dict)
    see_all = Signal()
    WIDTH = 380
    MAX_HEIGHT = 520

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Pop")
        self.setStyleSheet(f"QFrame#Pop {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px; }}")
        self.setFixedWidth(self.WIDTH)
        self._anchor = QPoint()
        lay = vbox(self, (0, 0, 0, 0), 0)
        head = hbox(spacing=8)
        head.setContentsMargins(16, 12, 12, 10)
        head.addWidget(label("Notifications", "SectionTitle"), 1)
        self.mark_all = button("Mark all read", "ghost", size="sm")
        head.addWidget(self.mark_all)
        lay.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.holder = QWidget()
        self.list = QVBoxLayout(self.holder)
        self.list.setContentsMargins(6, 0, 6, 6)
        self.list.setSpacing(0)
        self.scroll.setWidget(self.holder)
        lay.addWidget(self.scroll, 1)
        foot = button("View all notifications", "ghost")
        foot.clicked.connect(lambda: (self.hide(), self.see_all.emit()))
        lay.addWidget(foot)
        self.mark_all.clicked.connect(self._mark_all)
        self.spinner = Spinner(18)

    def load(self, anchor):
        self._anchor = anchor
        self._clear()
        self.list.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        self._place(120)
        self.show()
        tasks.run(api.get, "/api/notifications", params={"page_size": 10}, on_done=self._render,
                  on_error=lambda e: (self._clear(), self.list.addWidget(label(e.message, "Muted", wrap=True)),
                                      self._fit()), owner=self)

    def _place(self, list_height):
        """Anchor under the bell, clamped horizontally to the screen, with the
        height capped to the space available below the anchor."""
        screen = (QGuiApplication.screenAt(self._anchor) or self.screen()).availableGeometry()
        chrome = 46 + 40  # header + footer
        room = max(160, screen.bottom() - self._anchor.y() - 12)
        height = min(self.MAX_HEIGHT, room, chrome + list_height)
        x = min(max(screen.left() + 8, self._anchor.x()), screen.right() - self.WIDTH - 8)
        self.setGeometry(x, self._anchor.y(), self.WIDTH, height)

    def _fit(self):
        # Measure each row at the width it will actually get (wrapped text).
        inner = self.WIDTH - 2 - 12
        content = 6
        for i in range(self.list.count()):
            w = self.list.itemAt(i).widget()
            if w is None:
                continue
            content += w.heightForWidth(inner) if w.hasHeightForWidth() else w.sizeHint().height()
        self._place(content)

    def _clear(self):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().setParent(None)

    def _render(self, data):
        self._clear()
        items = data.get("items") or []
        if not items:
            empty = label("You're all caught up.", "Muted")
            empty.setContentsMargins(12, 16, 12, 16)
            self.list.addWidget(empty)
        for n in items:
            self.list.addWidget(_notification_row(n, lambda n=n: (self.hide(), self.open_entity.emit(n))))
        self._fit()

    def _mark_all(self):
        tasks.run(api.post, "/api/notifications/read", {"all": True},
                  on_done=lambda _r: (self.hide(), session.user and session.user.update(unread_notifications=0),
                                      session.user_changed.emit()),
                  on_error=lambda e: show_error(self, e), owner=self)


def _notification_row(n, on_click):
    w = QFrame()
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.setStyleSheet("QFrame { border-radius: 6px; } QFrame:hover { background: #F4F4F6; }")
    row = hbox(w, (10, 8, 10, 8), 10)
    dot = QLabel()
    dot.setFixedSize(7, 7)
    dot.setStyleSheet(f"background: {C['accent'] if not n.get('is_read') else 'transparent'}; border-radius: 3px;")
    row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
    col = vbox(spacing=2)
    t = label(n["title"])
    t.setStyleSheet("font-weight: 600;")
    col.addWidget(t)
    b = label(n["body"], "Muted", wrap=True)
    col.addWidget(b)
    col.addWidget(label(fmt.relative(n["created_at"]), "Meta"))
    row.addLayout(col, 1)
    w.mousePressEvent = lambda _e: on_click()
    return w


class TopBar(QFrame):
    back_requested = Signal()
    search_chosen = Signal(str, dict)
    profile_requested = Signal()
    logout_requested = Signal()
    notifications_page = Signal()
    open_notification = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setObjectName("TopBar")
        self.setFixedHeight(56)
        row = hbox(self, (16, 0, 16, 0), 8)
        self.back_btn = icon_button("arrow_left", "Back (Alt+Left)", 16)
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self.back_requested.emit)
        row.addWidget(self.back_btn)
        self.title = label("")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        row.addWidget(self.title)
        row.addStretch(1)
        self.search = SearchBox("Search leads, audits, people…  (Ctrl+K)")
        self.search.setFixedWidth(340)
        self.search.search.connect(self._search)
        row.addWidget(self.search)
        row.addSpacing(8)
        self.conn = ConnectionIndicator()
        row.addWidget(self.conn)
        self.bell = icon_button("bell", "Notifications", 18)
        self.bell.clicked.connect(self._open_notifications)
        self.badge = QLabel(self.bell)
        self.badge.setStyleSheet(f"background: {C['danger']}; color: #FFFFFF; border-radius: 7px; font-size: 10px; "
                                 "font-weight: 700; padding: 0 4px;")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.move(17, 1)
        self.badge.hide()
        row.addWidget(self.bell)
        self.avatar_btn = QToolButton()
        self.avatar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar_btn.setToolTip("Account")
        self.avatar_btn.setFixedSize(36, 36)
        av_lay = vbox(self.avatar_btn, (4, 4, 4, 4))
        self.avatar = Avatar("", 28)
        self.avatar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        av_lay.addWidget(self.avatar)
        self.avatar_btn.clicked.connect(self._menu)
        row.addWidget(self.avatar_btn)
        self.popup = SearchPopup(self)
        self.popup.chosen.connect(self.search_chosen.emit)
        self.notif_pop = NotificationPopover(self)
        self.notif_pop.see_all.connect(self.notifications_page.emit)
        self.notif_pop.open_entity.connect(self.open_notification.emit)
        self._search_gen = 0

    def set_title(self, text, section=None):
        """Breadcrumb: muted section, then the current page."""
        if section:
            self.title.setText(f'<span style="color:{C["text_3"]}; font-weight:500;">{_esc(section)}'
                               f'&nbsp;&nbsp;/&nbsp;&nbsp;</span>{_esc(text)}')
        else:
            self.title.setText(_esc(text))

    def set_user(self, user):
        self.avatar.set_name(user.get("name", ""))
        self.avatar.set_pixmap(None)
        self.set_unread(user.get("unread_notifications", 0))
        if user.get("has_avatar") and user.get("id"):
            from seo_audit_pro.widgets.common import scaled_pixmap

            def show(data):
                if isinstance(data, (bytes, bytearray)):
                    self.avatar.set_pixmap(scaled_pixmap(bytes(data), 28))

            tasks.run(api.get, f"/api/users/{user['id']}/avatar", on_done=show, on_error=lambda _e: None, owner=self)

    def set_can_go_back(self, value):
        self.back_btn.setEnabled(value)

    def set_unread(self, n):
        n = int(n or 0)
        self.badge.setVisible(n > 0)
        self.badge.setText("9+" if n > 9 else str(n))
        self.badge.adjustSize()
        self.badge.setFixedHeight(14)
        self.bell.setToolTip(f"Notifications ({n} unread)" if n else "Notifications")

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def _search(self, text):
        if len(text) < 2:
            self.popup.hide()
            return
        self._search_gen += 1
        gen = self._search_gen
        anchor = self.search.mapToGlobal(QPoint(self.search.width() - 440, self.search.height() + 6))

        def done(res):
            if gen == self._search_gen and self.search.text().strip():
                self.popup.show_results(res, anchor)
                self.search.setFocus()

        tasks.run(api.get, "/api/search", params={"q": text}, on_done=done, on_error=lambda e: show_error(self, e),
                  owner=self)

    def _open_notifications(self):
        self.notif_pop.load(self.bell.mapToGlobal(QPoint(self.bell.width() - NotificationPopover.WIDTH,
                                                         self.bell.height() + 6)))

    def _menu(self):
        u = session.user or {}
        m = QMenu(self)
        head = m.addAction(f"{u.get('name', '')}\n{ROLE_LABEL.get(u.get('role'), '')}"
                           + (f" · {u['team_name']}" if u.get("team_name") else ""))
        head.setEnabled(False)
        m.addSeparator()
        m.addAction(icons.icon("user", C["text_2"]), "Profile", self.profile_requested.emit)
        m.addAction(icons.icon("logout", C["text_2"]), "Sign out", self.logout_requested.emit)
        m.exec(self.avatar_btn.mapToGlobal(QPoint(self.avatar_btn.width() - 200, self.avatar_btn.height() + 4)))


# ---------------------------------------------------------------------------
# Workspace (sidebar + top bar + pages)
# ---------------------------------------------------------------------------
class Workspace(QWidget):
    logout_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Canvas")
        row = hbox(self)
        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self.navigate)
        row.addWidget(self.sidebar)
        right = QWidget()
        col = vbox(right)
        self.topbar = TopBar()
        self.topbar.search_chosen.connect(self._open_search_result)
        self.topbar.back_requested.connect(self.back)
        self.topbar.profile_requested.connect(lambda: self.navigate("profile"))
        self.topbar.logout_requested.connect(self.logout_requested.emit)
        self.topbar.notifications_page.connect(lambda: self.navigate("notifications"))
        self.topbar.open_notification.connect(self.open_notification)
        col.addWidget(self.topbar)
        self.stack = QStackedWidget()
        col.addWidget(self.stack, 1)
        row.addWidget(right, 1)
        self.pages = {}
        self.current = None
        self.history = []          # [(page key, params)]
        self.current_params = {}
        hub = get_hub()
        hub.event.connect(self._on_event)
        session.connection_changed.connect(self.topbar.conn.set_state)
        hub.state_changed.connect(lambda ok: self.topbar.conn.set_state("online" if ok else
                                                                        ("offline" if session.connection == "offline" else "connecting")))
        session.user_changed.connect(self._user_changed)
        self._unread_timer = QTimer(self)
        self._unread_timer.setInterval(60000)
        self._unread_timer.timeout.connect(self._refresh_counts)

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        from seo_audit_pro.core import settings
        user = session.user or {}
        self.sidebar.build(user.get("role"))
        if settings.get("sidebar_collapsed", False) in (True, "true") and not self.sidebar.collapsed:
            self.sidebar.toggle_collapsed()
        self.topbar.set_user(user)
        self.topbar.conn.set_state(session.connection)
        for key in list(self.pages):
            page = self.pages.pop(key)
            self.stack.removeWidget(page)
            page.deleteLater()
        self.history.clear()
        self.topbar.set_can_go_back(False)
        self.navigate("dashboard")
        get_hub().start()
        self._unread_timer.start()
        self._refresh_counts()

    def stop(self):
        get_hub().stop()
        self._unread_timer.stop()

    def _user_changed(self):
        if session.user:
            self.topbar.set_user(session.user)

    # -- navigation ----------------------------------------------------------
    def page(self, key):
        if key not in self.pages:
            from seo_audit_pro.ui import pages
            factory = pages.registry().get(key)
            if factory is None:
                return None
            widget = factory(self)
            self.pages[key] = widget
            self.stack.addWidget(widget)
        return self.pages[key]

    def navigate(self, key, _from_history=False, **params):
        widget = self.page(key)
        if widget is None:
            return
        if self.current and (self.current, self.current_params) != (key, params) and not _from_history:
            self.history.append((self.current, self.current_params))
            self.history = self.history[-30:]
        self.current, self.current_params = key, dict(params)
        self.topbar.set_can_go_back(bool(self.history))
        self.stack.setCurrentWidget(widget)
        nav_key = getattr(widget, "nav_key", key)
        self.sidebar.set_active(nav_key)
        self.topbar.set_title(getattr(widget, "title", ""), self._section_of(nav_key, widget))
        if hasattr(widget, "activate"):
            widget.activate(**params)

    def _section_of(self, nav_key, widget):
        crumb = getattr(widget, "breadcrumb", None)
        if crumb:
            return crumb
        for section, entries in navigation_for(session.role):
            if any(key == nav_key for key, _t, _i in entries):
                return section
        return None

    def set_page_title(self, widget, title):
        """Pages call this after loading (e.g. lead name in the breadcrumb)."""
        widget.title = title
        if self.stack.currentWidget() is widget:
            self.topbar.set_title(title, self._section_of(getattr(widget, "nav_key", None), widget))

    def back(self):
        """Return to the previous screen with the parameters it was opened
        with; pages keep their own filters, page and scroll state."""
        if self.history:
            key, params = self.history.pop()
            self.navigate(key, _from_history=True, **params)

    def _open_search_result(self, kind, row):
        self.topbar.search.clear()
        if kind == "leads":
            self.navigate("lead", lead_id=row["id"])
        elif kind == "audits":
            self.navigate("audit", audit_id=row["id"])
        elif kind == "users":
            self.navigate("users" if session.is_manager() else "team", user_id=row["id"])
        elif kind == "teams":
            self.navigate("teams" if session.is_manager() else "team", team_id=row["id"])

    def open_notification(self, n):
        if not n.get("is_read"):
            tasks.run(api.post, "/api/notifications/read", {"ids": [n["id"]]},
                      on_done=lambda r: self.set_unread(r.get("unread", 0)), owner=self)
        et, eid = n.get("entity_type"), n.get("entity_id")
        if et == "lead" and eid:
            self.navigate("lead", lead_id=eid)
        elif et == "audit" and eid:
            self.navigate("audit", audit_id=eid)
        elif et == "audit_batch" and eid:
            self.navigate("audits", batch_id=eid)
        elif et == "conversation" and eid:
            self.navigate("chat", conversation_id=eid)
        elif et == "email" and eid:
            self.navigate("outreach_activity" if session.is_manager() else "outreach", email_id=eid)
        else:
            self.navigate("notifications")

    # -- live counters -------------------------------------------------------
    def set_unread(self, n):
        """Update the notification badge (top bar and sidebar)."""
        if session.user is not None:
            session.user["unread_notifications"] = n
        self.topbar.set_unread(n)
        self.sidebar.set_badge("notifications", n)

    def _refresh_counts(self):
        tasks.run(api.get, "/api/notifications", params={"page_size": 1},
                  on_done=lambda d: self.set_unread(d.get("unread", 0)), owner=self)
        tasks.run(api.get, "/api/chat/conversations",
                  on_done=lambda d: self.sidebar.set_badge("chat", d.get("unread_total", 0)), owner=self)

    def _on_event(self, ev):
        kind = ev.get("type")
        if kind in ("notification", "audit_progress", "audit_batch", "import_progress", "message", "message_updated"):
            from seo_audit_pro.core import cache as client_cache
            if kind != "audit_progress" or ev.get("status") in ("completed", "failed", "cancelled"):
                client_cache.clear()  # server data changed; next visits refetch
        if kind == "notification":
            current = int((session.user or {}).get("unread_notifications", 0)) + 1
            self.set_unread(current)
        elif kind == "message":
            if self.current != "chat":
                msg = ev.get("message") or {}
                if msg.get("sender_id") != session.user_id:
                    QTimer.singleShot(200, self._refresh_counts)
        for page in self.pages.values():
            handler = getattr(page, "on_realtime", None)
            if handler:
                handler(ev)
