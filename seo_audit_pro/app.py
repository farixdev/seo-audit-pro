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
        server = settings.api_url()
        self.step("Loading interface…", 0.30)
        from seo_audit_pro.ui.main_window import MainWindow
        self.window = MainWindow()
        self.step("Checking local resources…", 0.45)
        missing = [f for f in ("app.ico", "check-white.svg") if not os.path.isfile(os.path.join(settings.asset_dir(), f))]
        if missing:
            logging.getLogger("seo_audit_pro").warning("Missing assets: %s", missing)
        self.step("Initializing secure session…", 0.55)
        from seo_audit_pro.core import api, tasks
        from seo_audit_pro.core.session import session
        stored = session.stored_refresh()
        self.step(f"Connecting to {server.split('://', 1)[-1]}…", 0.65)

        def connected(_health):
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
            self.finish("signin", f"Server unavailable — {err.message} You can retry signing in or change the server address.")

        tasks.run(api.health, on_done=connected, on_error=offline)

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
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mindcob.SEOAuditPro")
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
    startup = Startup(app)
    startup.run()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
