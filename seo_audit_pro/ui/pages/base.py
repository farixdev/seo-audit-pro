# -*- coding: utf-8 -*-
"""Base class and helpers shared by every workspace page."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QScrollArea, QWidget

from seo_audit_pro.core import api, tasks
from seo_audit_pro.core import cache as client_cache
from seo_audit_pro.widgets.common import PageHeader, vbox


class Page(QWidget):
    """A workspace page.

    Subclasses set ``title`` and ``nav_key`` and implement ``activate(**params)``.
    ``self.ws`` is the Workspace (use ``self.ws.navigate(key, **params)``).
    Use ``self.fetch(fn, ...)`` for background loads: results from superseded
    loads are discarded automatically.
    """
    title = ""
    nav_key = None

    def __init__(self, workspace, scroll=False, margins=(32, 24, 32, 24), spacing=20):
        super().__init__()
        self.ws = workspace
        self._gen = {}
        outer = vbox(self)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            inner = QWidget()
            inner.setObjectName("Canvas")
            self.body = vbox(inner, margins, spacing)
            area.setWidget(inner)
            outer.addWidget(area)
        else:
            self.body = vbox(None, margins, spacing)
            outer.addLayout(self.body)

    def header(self, title, subtitle=""):
        h = PageHeader(title, subtitle)
        self.body.addWidget(h)
        return h

    def activate(self, **params):
        pass

    def fetch(self, channel, fn, *args, on_done=None, on_error=None, **kwargs):
        """Run ``fn`` in the background; only the latest call per channel delivers.

        GET requests (``api.get``) are served stale-while-revalidate: a cached
        response is shown immediately, then refreshed in the background and
        re-delivered only if it changed."""
        gen = self._gen.get(channel, 0) + 1
        self._gen[channel] = gen
        cached = None
        if fn is api.get and args:
            k = client_cache.key(args[0], kwargs.get("params"))
            cached, age = client_cache.get(k)
            if cached is not None:
                QTimer.singleShot(0, lambda: self._gen.get(channel) == gen and on_done and on_done(cached))
                if age < 8:
                    return  # fresh enough; skip the network round-trip
            fn = api.get_cached

        def done(res):
            if self._gen.get(channel) == gen and on_done and res != cached:
                on_done(res)

        def fail(err):
            if self._gen.get(channel) == gen and on_error and cached is None:
                on_error(err)

        tasks.run(fn, *args, on_done=done, on_error=fail, owner=self, **kwargs)
