# -*- coding: utf-8 -*-
"""Top-level window: sign-in experience and workspace, with cross-fades."""

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QPropertyAnimation, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QMainWindow, QStackedWidget

from seo_audit_pro import APP_NAME, brand
from seo_audit_pro.core import api, settings, tasks
from seo_audit_pro.core.session import session
from seo_audit_pro.ui.auth import AuthView
from seo_audit_pro.ui.shell import Workspace


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(brand.app_icon())
        self.setMinimumSize(1100, 660)
        self._restore_geometry()
        self.stack = QStackedWidget()
        self.stack.setObjectName("Canvas")
        self.setCentralWidget(self.stack)
        self.auth = AuthView()
        self.workspace = Workspace()
        self.stack.addWidget(self.auth)
        self.stack.addWidget(self.workspace)
        self.auth.signed_in.connect(self.enter_workspace)
        self.auth.quick_requested.connect(self.enter_quick)
        self.quick = None   # Scrape & Audit portal, made on first use
        self.workspace.logout_requested.connect(self.sign_out)
        session.expired.connect(self._expired)
        session.password_change_required.connect(self._must_change)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=lambda: self.workspace.sidebar.toggle_collapsed()
                  if self.stack.currentWidget() is self.workspace else None)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.workspace.back)
        QApplication.instance().installEventFilter(self)  # mouse "back" side button
        self._fade = None

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.BackButton
                and self.stack.currentWidget() is self.workspace and self.isActiveWindow()):
            self.workspace.back()
            return True
        return False

    # -- geometry --------------------------------------------------------------
    def _restore_geometry(self):
        geo = settings.get("window_geometry")
        if isinstance(geo, QByteArray) and self.restoreGeometry(geo):
            return
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            w = min(1480, int(screen.width() * 0.88))
            h = min(940, int(screen.height() * 0.88))
            self.resize(w, h)
            self.move(screen.center().x() - w // 2, screen.center().y() - h // 2)

    def closeEvent(self, e):
        settings.put("window_geometry", self.saveGeometry())
        if self.quick is not None:
            self.quick.stop()
        self.workspace.stop()
        super().closeEvent(e)

    # -- transitions -----------------------------------------------------------
    def _switch(self, widget):
        if self.stack.currentWidget() is widget or not self.isVisible():
            self.stack.setCurrentWidget(widget)
            return
        snap = QLabel(self.stack)
        snap.setPixmap(self.stack.grab())
        snap.setGeometry(self.stack.rect())
        self.stack.setCurrentWidget(widget)
        snap.show()
        snap.raise_()
        fx = QGraphicsOpacityEffect(snap)
        snap.setGraphicsEffect(fx)
        anim = QPropertyAnimation(fx, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(snap.deleteLater)
        anim.start()
        self._fade = anim

    def show_auth(self, page="signin", notice=None, kind="error"):
        self.auth.refresh_server_label()
        self.auth.show_page(page, notice, kind)
        self._switch(self.auth)

    def enter_quick(self):
        if self.quick is None:
            from seo_audit_pro.ui.quick_portal import QuickPortal
            self.quick = QuickPortal()
            self.quick.leave.connect(self._leave_quick)
            self.stack.addWidget(self.quick)
        settings.set_start_in("quick")   # open here next time too
        self._switch(self.quick)
        self.quick.start()

    def _leave_quick(self):
        """"Sign in to a workspace": stop opening in Scrape & Audit and start the
        workspace connection the normal way (server on this computer, or the
        team server), then sign in — or go straight in with a saved session."""
        settings.set_start_in("")
        mode = settings.mode()
        if mode is None:
            self.show_auth("welcome")
            return
        self.show_auth("signin", "Connecting…", "info")
        if mode == settings.LOCAL:
            from seo_audit_pro.core import local

            def ready(status):
                if status.get("needs_setup"):
                    self.show_auth("setup")
                else:
                    self._restore_or_sign_in()

            local.start(ready, lambda err: self.show_auth("welcome", err.message), owner=self)
        else:
            self._restore_or_sign_in()

    def _restore_or_sign_in(self):
        """Use the saved "Keep me signed in" session when there is one."""
        stored = session.stored_refresh()
        if not (stored and stored.get("refresh_token")):
            self.show_auth("signin")
            return
        session.refresh_token = stored["refresh_token"]
        session.remember = True

        def done(ok):
            if not ok:
                session.end()
                self.show_auth("signin", "Your saved session has expired. Please sign in again.", "info")
            elif (session.user or {}).get("must_change_password"):
                self.show_auth("change")
            else:
                session.set_user(session.user)
                self.enter_workspace()

        tasks.run(api.refresh_session, on_done=done,
                  on_error=lambda e: self.show_auth("signin", f"Server unavailable — {e.message}"), owner=self)

    def enter_workspace(self):
        self._switch(self.workspace)
        self.workspace.start()

    # -- session events -----------------------------------------------------------
    def sign_out(self):
        token_snapshot = session.access_token
        if token_snapshot:
            tasks.run(api.post, "/api/auth/logout", owner=None)
        self.workspace.stop()
        session.end()
        self.show_auth("signin", "You have been signed out.", "info")

    def _expired(self, reason):
        self.workspace.stop()
        self.show_auth("signin", reason, "error")

    def _must_change(self):
        self.workspace.stop()
        self.show_auth("change", "For security, choose a new password before continuing.", "info")

    def _focus_search(self):
        if self.stack.currentWidget() is self.workspace:
            self.workspace.topbar.focus_search()

    def fade_in(self):
        fx = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(fx)
        anim = QPropertyAnimation(fx, b"opacity", self)
        anim.setDuration(240)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self.stack.setGraphicsEffect(None))
        anim.start()
        self._fade = anim
        self.show()
        self.raise_()
        self.activateWindow()
