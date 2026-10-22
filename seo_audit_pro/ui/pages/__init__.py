# -*- coding: utf-8 -*-
"""Page registry. Pages are imported lazily the first time they are opened."""

import importlib

_REGISTRY = {
    "dashboard": ("dashboard", "DashboardPage"),
    "leads": ("leads", "LeadsPage"),
    "lead": ("lead_detail", "LeadDetailPage"),
    "audits": ("audits", "AuditsPage"),
    "audit": ("audit_detail", "AuditDetailPage"),
    "reports": ("reports", "ReportsPage"),
    "teams": ("teams", "TeamsPage"),
    "team": ("teams", "MyTeamPage"),
    "users": ("users", "UsersPage"),
    "outreach": ("outreach", "OutreachPage"),
    "outreach_activity": ("outreach", "OutreachActivityPage"),
    "chat": ("chat", "ChatPage"),
    "notifications": ("notifications", "NotificationsPage"),
    "audit_logs": ("audit_logs", "AuditLogsPage"),
    "settings": ("settings_page", "SettingsPage"),
    "profile": ("profile", "ProfilePage"),
    "map_harvest": ("map_harvest", "MapHarvestPage"),
}


def registry():
    def factory(module, cls):
        def make(workspace):
            mod = importlib.import_module(f"seo_audit_pro.ui.pages.{module}")
            return getattr(mod, cls)(workspace)
        return make
    return {key: factory(m, c) for key, (m, c) in _REGISTRY.items()}
