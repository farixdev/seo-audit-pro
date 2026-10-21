# -*- coding: utf-8 -*-
"""
Visual QA helper: renders client screens against a running local server and
saves PNG screenshots.

  .venv/Scripts/python.exe scripts/ui_snapshot.py OUT_DIR EMAIL PASSWORD page[:param=value] ...

Example:
  ... scripts/ui_snapshot.py shots manager@dev.local <pw> signin dashboard leads
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from seo_audit_pro.core import api, settings  # noqa: E402
from seo_audit_pro.core.session import session  # noqa: E402
from seo_audit_pro.theme import FONT_FALLBACK, FONT_FAMILY, stylesheet  # noqa: E402


def main():
    out, email, password, *pages = sys.argv[1:]
    os.makedirs(out, exist_ok=True)
    app = QApplication(sys.argv)
    font = QFont(FONT_FAMILY)
    if not font.exactMatch():
        font = QFont(FONT_FALLBACK)
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(stylesheet(settings.asset_dir()))
    from seo_audit_pro.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(int(os.environ.get("SNAP_W", 1440)), int(os.environ.get("SNAP_H", 900)))
    win.show()
    queue = list(pages)

    def shoot(name):
        path = os.path.join(out, f"{name.replace(':', '_').replace('=', '-')}.png")
        win.grab().save(path)
        print("saved", path)

    def step():
        if not queue:
            app.quit()
            return
        item = queue.pop(0)
        if item in ("signin", "forgot", "reset", "change", "server"):
            win.show_auth(item)
            QTimer.singleShot(600, lambda: (shoot(item), step()))
            return
        if not session.authenticated():
            payload = api.post("/api/auth/login", {"email": email, "password": password}, auth=False)
            session.start(payload, remember=False)
            win.enter_workspace()
            if os.environ.get("SNAP_OFFLINE"):
                # Simulate the server going away after sign-in (failure-state QA).
                settings.api_url = lambda: "http://127.0.0.1:9"
        key, _, rest = item.partition(":")
        params = dict(p.split("=", 1) for p in rest.split(",") if "=" in p)
        win.workspace.navigate(key, **params)
        QTimer.singleShot(int(os.environ.get("SNAP_WAIT", 1800)), lambda: (shoot(item), step()))

    QTimer.singleShot(300, step)
    app.exec()


if __name__ == "__main__":
    main()
