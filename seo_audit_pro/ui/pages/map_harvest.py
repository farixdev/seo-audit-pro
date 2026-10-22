# -*- coding: utf-8 -*-
"""Map Harvest page integrated into SEO Audit Pro workspace."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from seo_audit_pro.map_harvest.ui.screen_input import InputScreen
from seo_audit_pro.map_harvest.ui.screen_results import ResultsScreen
from seo_audit_pro.map_harvest.ui.theme import QSS
from seo_audit_pro.ui.pages.base import Page


class MapHarvestPage(Page):
    title = "Map Harvest"
    nav_key = "map_harvest"

    def __init__(self, workspace):
        super().__init__(workspace, scroll=False, margins=(0, 0, 0, 0), spacing=0)

        # Scoped container with Map Harvest's dark theme
        self.container = QWidget()
        self.container.setObjectName("MapHarvestContainer")
        self.container.setStyleSheet(QSS)
        lay = QVBoxLayout(self.container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.stack = QStackedWidget()
        self.input_screen = InputScreen()
        self.results_screen = ResultsScreen()

        self.stack.addWidget(self.input_screen)
        self.stack.addWidget(self.results_screen)
        lay.addWidget(self.stack)

        self.body.addWidget(self.container)

        # Wire transitions
        self.input_screen.start_signal.connect(self.on_start)
        self.results_screen.stop_signal.connect(self.on_stop)
        self.results_screen.home_signal.connect(self.on_home)

    def on_start(self, domains, areas, fields, headless=False, max_results=50, export_dir="", filters=None):
        self.results_screen.setup(
            domains, areas, fields, headless, max_results, export_dir, filters or {},
        )
        self.stack.setCurrentIndex(1)
        self.results_screen.start_worker()

    def on_stop(self):
        self.results_screen.stop_worker()

    def on_home(self):
        self.stack.setCurrentIndex(0)

    def activate(self, **params):
        # Refresh permission visibility on buttons when switching tabs
        if hasattr(self.results_screen, "lead_bank_btn"):
            try:
                from seo_audit_pro.core.session import session
                can_import = session.is_manager() or session.is_team_lead()
                self.results_screen.lead_bank_btn.setVisible(can_import)
            except Exception:
                pass

    def stop(self):
        self.shutdown_worker()

    def shutdown_worker(self):
        """Stop running scrape and tear browser down cleanly."""
        worker = getattr(self.results_screen, "worker", None)
        if worker is None or not worker.isRunning():
            return
        self.results_screen._stopped_by_user = True
        worker.stop()
        if worker.wait(3000):
            return
        worker.abort()
        worker.wait(3000)
