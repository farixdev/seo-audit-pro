# -*- coding: utf-8 -*-
"""
Desktop entry point.

  .venv\\Scripts\\pythonw.exe -m seo_audit_pro.app       (no console window)

Startup: splash with real initialization steps -> sign-in (or the restored
session) -> workspace. Heavy modules are imported only after the splash is
visible.
"""

import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler


def _setup_logging():
    from seo_audit_pro.core import settings
    handler = RotatingFileHandler(os.path.join(settings.log_dir(), "client.log"), maxBytes=2 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("seo_audit_pro")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return root


def _install_excepthook(log):
    def hook(exc_type, exc, tb):
        log.error("Unhandled exception: %s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            from PySide6.QtWidgets import QApplication
            from seo_audit_pro.widgets.feedback import toast
            win = QApplication.activeWindow()
            if win is not None:
                toast(win, "Something went wrong. We couldn't complete this operation.", "error")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = hook


class Startup:
    """Drives the splash through the real initialization sequence."""

    def __init__(self, app):
        self.app = app
        self.window = None
        self.started = time.monotonic()
        from seo_audit_pro.ui.splash import Splash
        self.splash = Splash()
        self.splash.show_centered()
        app.processEvents()

    def step(self, text, frac):
        self.splash.step(text, frac)
        self.app.processEvents()

    def run(self):
        from seo_audit_pro.core import settings
        self.step("Initializing application…", 0.08)
        self.step("Loading configuration…", 0.18)
        self.step("Loading interface…", 0.30)
        from seo_audit_pro.ui.main_window import MainWindow
        self.window = MainWindow()
        self.step("Checking local resources…", 0.40)
        missing = [f for f in ("app.ico", "check-white.svg") if not os.path.isfile(os.path.join(settings.asset_dir(), f))]
        if missing:
            logging.getLogger("seo_audit_pro").warning("Missing assets: %s", missing)
        mode = settings.mode()
        if mode is None:  # first run: choose "This computer" or a team server
            self.finish("welcome")
        elif mode == settings.LOCAL:
            self.start_local()
        else:
            self.connect()

    def start_local(self):
        from seo_audit_pro.core import local
        self.step("Starting the audit server on this computer…", 0.5)

        def ready(status):
            if status.get("needs_setup"):
                self.finish("setup")
            else:
                self.connect()

        local.start(ready, lambda err: self.finish("welcome", err.message))

    def connect(self):
        from seo_audit_pro.core import api, settings, tasks
        from seo_audit_pro.core.session import session
        server = settings.api_url()
        self.step("Initializing secure session…", 0.62)
        stored = session.stored_refresh()
        local = settings.mode() == settings.LOCAL
        where = "this computer" if local else server.split("://", 1)[-1]
        self.step(f"Connecting to {where}…", 0.7)
        answered = []

        def slow():  # a free cloud server sleeps when nobody uses it and needs up to a minute to wake
            if not answered:
                self.step("Waking the team server, this can take up to a minute…", 0.75)

        if not local:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(6000, slow)

        def connected(_health):
            answered.append(True)
            if stored and stored.get("refresh_token"):
                self.step("Restoring your session…", 0.8)
                session.refresh_token = stored["refresh_token"]
                session.remember = True
                tasks.run(api.refresh_session, on_done=restored, on_error=lambda _e: self.finish("signin"))
            else:
                self.finish("signin")

        def restored(ok):
            if not ok:
                session.end()
                self.finish("signin", "Your saved session has expired. Please sign in again.", "info")
                return
            self.step("Loading workspace…", 0.92)
            session.set_user(session.user)
            if session.user.get("must_change_password"):
                self.finish("change")
            else:
                self.finish("workspace")

        def offline(err):
            answered.append(True)
            self.finish("signin", f"Server unavailable — {err.message} You can retry signing in or change the server address.")

        tasks.run(api.health, timeout=10 if local else 90, on_done=connected, on_error=offline)

    def finish(self, target, notice=None, kind="error"):
        self.step("Ready", 1.0)
        # Let the progress line settle so the transition reads as intentional.
        remaining = max(0, 0.6 - (time.monotonic() - self.started))
        from PySide6.QtCore import QTimer

        def go():
            def reveal():
                if target == "workspace":
                    self.window.stack.setCurrentWidget(self.window.workspace)
                    self.window.fade_in()
                    self.window.workspace.start()
                else:
                    self.window.show_auth(target, notice, kind)
                    self.window.fade_in()
            self.splash.finish(reveal)

        QTimer.singleShot(int(remaining * 1000) + 120, go)


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QGuiApplication
    from PySide6.QtWidgets import QApplication

    from seo_audit_pro import APP_NAME, ORG_NAME, brand
    from seo_audit_pro.core import settings
    from seo_audit_pro.theme import FONT_FALLBACK, FONT_FAMILY, stylesheet

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{ORG_NAME}.{APP_NAME}.v3")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setWindowIcon(brand.app_icon())
    font = QFont(FONT_FAMILY)
    if not font.exactMatch():
        font = QFont(FONT_FALLBACK)
    font.setPixelSize(13)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)
    app.setStyleSheet(stylesheet(settings.asset_dir()))
    log = _setup_logging()
    _install_excepthook(log)
    instance = _SingleInstance()
    if instance.forward():
        return  # already running: the other window was brought to the front
    from seo_audit_pro import local_server
    app.aboutToQuit.connect(local_server.instance().on_app_quit)
    startup = Startup(app)
    instance.on_activate = lambda: _raise(startup.window)
    startup.run()
    _watch_local_server(app)
    sys.exit(app.exec())


def _raise(window):
    if window is None:
        return
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()


class _SingleInstance:
    """One window per Windows user: a second launch brings the first one forward."""

    def __init__(self):
        import getpass

        from PySide6.QtNetwork import QLocalServer
        from seo_audit_pro.core import settings
        self.name = f"SEOAuditPro-{getpass.getuser()}-{settings.PROFILE or 'default'}"
        self.server = QLocalServer()
        self.on_activate = None

    def forward(self):
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        sock = QLocalSocket()
        sock.connectToServer(self.name)
        if sock.waitForConnected(400):
            sock.write(b"show")
            sock.waitForBytesWritten(400)
            sock.disconnectFromServer()
            return True
        QLocalServer.removeServer(self.name)  # left behind by a crashed instance
        self.server.newConnection.connect(self._activated)
        self.server.listen(self.name)
        return False

    def _activated(self):
        conn = self.server.nextPendingConnection()
        if conn is not None:
            conn.disconnectFromServer()
        if self.on_activate:
            self.on_activate()


def _watch_local_server(app):
    """Restart the built-in server if it stops unexpectedly (This computer mode)."""
    from PySide6.QtCore import QTimer
    from seo_audit_pro import local_server
    from seo_audit_pro.core import local, settings
    srv = local_server.instance()
    state = {"busy": False}

    def check():
        if state["busy"] or settings.mode() != settings.LOCAL or srv.port is None or srv.running():
            return
        logging.getLogger("seo_audit_pro").warning("The local server stopped; restarting it")
        state["busy"] = True
        local.start(lambda _s: state.update(busy=False), lambda _e: state.update(busy=False))

    timer = QTimer(app)
    timer.setInterval(5000)
    timer.timeout.connect(check)
    timer.start()
    app._local_server_watch = timer


if __name__ == "__main__":
    main()


def selftest(argv):
    """``Rankly.exe --selftest <website> <folder>``: run one Scrape & Audit audit
    inside the real app window (hidden), the way a user does, and write
    selftest.log in <folder>. Exit code 0 = the audit finished."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    args = [a for a in argv if not a.startswith("--")]
    site, out = (args + ["https://example.com", os.getcwd()])[:2]
    os.makedirs(out, exist_ok=True)
    report = open(os.path.join(out, "selftest.log"), "w", encoding="utf-8", buffering=1)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from seo_audit_pro.core import settings
    from seo_audit_pro.theme import stylesheet
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(stylesheet(settings.asset_dir()))
    _setup_logging()
    settings.downloads_dir = lambda: out
    from seo_audit_pro.ui.main_window import MainWindow
    window = MainWindow()
    window.enter_quick()
    page = window.quick.audit
    page.pages.setValue(3)

    def progress(_id, pct, step):
        report.write(f"{pct}% {step}\n")

    def finished(_id, ok, info, path):
        report.write(f"FINISHED ok={ok} {info} {path}\n")
        # Keep the window running a little (as a user would), then leave without tearing it down.
        linger = int(os.environ.get("RANKLY_SELFTEST_LINGER", "0") or 0)
        QTimer.singleShot(linger * 1000, lambda: (report.write("STILL RUNNING OK\n"), report.close(),
                                                  os._exit(0 if ok else 1)))

    window.quick.runner.signals.progress.connect(progress)
    window.quick.runner.signals.finished.connect(finished)
    page.queue([{"site": site, "competitors": [], "business": ""}])
    QTimer.singleShot(600000, lambda: (report.write("TIMEOUT\n"), app.quit()))
    app.exec()
    report.close()
    os._exit(1)
