# -*- coding: utf-8 -*-
"""Bulk audit: review what will be audited, then queue it."""

from PySide6.QtWidgets import QCheckBox, QFrame, QSpinBox

from seo_audit_pro.core import api, fmt, tasks
from seo_audit_pro.theme import C
from seo_audit_pro.widgets.common import hbox, label, vbox
from seo_audit_pro.widgets.feedback import Dialog


class BulkAuditDialog(Dialog):
    """``selection`` is ``{"lead_ids": [...]}`` or ``{"filter": {...}}`` plus a
    ``label`` describing it. The server decides what is audited; this dialog
    shows its preview and confirms exactly that number."""

    def __init__(self, parent, selection):
        super().__init__(parent, "Audit leads", "Each audit is a real crawl of the website. Audits wait in a shared queue "
                                                "and run a few at a time on the server.", 520)
        self.selection = {k: v for k, v in selection.items() if k in ("lead_ids", "filter")}
        self.label_text = selection.get("label") or ""
        self.preview = None
        self.result = None
        self._idem = api.new_idempotency_key()

        self.headline = label("Checking the selection…", "SectionTitle", wrap=True)
        self.body.addWidget(self.headline)
        self.scope = label(self.label_text, "Meta", wrap=True)
        self.scope.setVisible(bool(self.label_text))
        self.body.addWidget(self.scope)

        box = QFrame()
        box.setObjectName("Surface")
        self.rows = vbox(box, (14, 12, 14, 12), 8)
        self.body.addWidget(box)

        # Pages to crawl / scrape per site
        prow = hbox(spacing=10)
        prow.addWidget(label("Pages to crawl / scrape per site:", "FieldLabel"))
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(1, 500)
        self.pages_spin.setValue(50)
        self.pages_spin.setToolTip("Maximum pages crawled per website (1-500)")
        prow.addWidget(self.pages_spin)
        prow.addStretch(1)
        self.body.addLayout(prow)

        self.recent = QCheckBox("")
        self.recent.setVisible(False)
        self.recent.toggled.connect(lambda _c: self.load())
        self.body.addWidget(self.recent)
        self.queue_note = label("", "Meta", wrap=True)
        self.body.addWidget(self.queue_note)
        self.add_buttons("Audit")
        self.primary.setEnabled(False)
        self.primary.clicked.connect(self.submit)
        self.load()

    def _payload(self):
        return dict(self.selection, include_recent=self.recent.isChecked(), max_pages=self.pages_spin.value())

    def load(self):
        self.primary.setEnabled(False)
        self.clear_error()
        tasks.run(api.post, "/api/audits/bulk/preview", self._payload(), timeout=60, on_done=self._render,
                  on_error=self.show_error, owner=self)

    def _row(self, text, value, color=None):
        r = hbox(spacing=8)
        r.addWidget(label(text, "Muted"), 1)
        v = label(value)
        v.setStyleSheet(f"font-weight: 600; color: {color or C['text']};")
        r.addWidget(v)
        self.rows.addLayout(r)

    def _render(self, p):
        self.preview = p
        while self.rows.count():
            item = self.rows.takeAt(0)
            lay = item.layout()
            if lay is not None:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w is not None:
                        w.deleteLater()
        n = p["will_queue"]
        if n:
            self.headline.setText(f"{n:,} {'lead' if n == 1 else 'leads'} will be audited")
        else:
            self.headline.setText("Nothing to audit in this selection")
        self._row("Selected", f"{p['selected']:,}")
        sk = p["skipped"]
        if sk["recently_audited"]:
            self._row(f"Audited in the last {p['recent_days']} days (skipped)", f"{sk['recently_audited']:,}")
        if sk["in_progress"]:
            self._row("Already waiting or running (skipped)", f"{sk['in_progress']:,}")
        if sk["archived"]:
            self._row("Archived (skipped)", f"{sk['archived']:,}")
        if p["not_now"]:
            why = (f"over the limit of {p['cap']:,} per run" if p["eligible"] > p["cap"]
                   else "over your waiting-audit limit")
            self._row(f"Not included now — {why}", f"{p['not_now']:,}", C["warning"])
        self._row("Will be audited", f"{n:,}", C["success"] if n else C["text_3"])
        recent_total = p.get("recent_available") or 0
        self.recent.blockSignals(True)
        self.recent.setText(f"Also re-audit the {recent_total:,} lead(s) audited in the last {p['recent_days']} days")
        self.recent.setVisible(bool(recent_total))
        self.recent.blockSignals(False)
        q = p["queue"]
        waiting = f"{q['queued']:,} audits are waiting · " if q["queued"] else ""
        self.queue_note.setText(f"{waiting}{q['workers']} run at a time · these should be done in "
                                f"{fmt.eta(p['eta_seconds'])}. You can keep working; you'll get a notification "
                                "when the batch finishes.")
        self.queue_note.setVisible(bool(n))
        self.primary.setText(f"Audit {n:,} {'lead' if n == 1 else 'leads'}" if n else "Audit")
        self.primary.setEnabled(bool(n))

    def submit(self):
        if not self.preview or not self.preview["will_queue"]:
            return
        payload = dict(self._payload(), confirm_count=self.preview["will_queue"], label=self.label_text)
        self.primary.busy(True, "Queueing…")

        def done(res):
            self.primary.busy(False)
            self.result = res["batch"]
            self.accept()

        def fail(err):
            self.primary.busy(False)
            self._idem = api.new_idempotency_key()
            if err.code == "count_changed" and err.data.get("preview"):
                self._render(err.data["preview"])
            self.show_error(err)

        tasks.run(api.post, "/api/audits/bulk", payload, idempotency_key=self._idem, timeout=120, on_done=done,
                  on_error=fail, owner=self)
