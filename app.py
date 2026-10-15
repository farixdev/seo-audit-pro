# -*- coding: utf-8 -*-
"""
SEO Audit Pro — PySide6 desktop GUI.

Enter a website URL → bounded whole-site crawl → world-class technical / on-page
/ performance / structured-data / E-E-A-T audit → client-ready Word (.docx)
report with embedded charts, plus a live tabbed preview. Runs 100% free with no
paid API keys (PageSpeed Insights v5 is used keyless; an optional Groq key only
adds an AI-written executive summary).

Run:
  python app.py                         # the GUI
  python app.py --selftest              # open + auto-quit (smoke test)
  python app.py --cli https://site.com --out report.docx [--pages 25] [--single]
"""

import os
import sys
import time
import threading

import config
config.load_env()
import prefs
prefs.apply_saved_env()   # the tool's own saved .env wins for keys it manages

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, rel)


def _docx_to_pdf(docx_path, pdf_path):
    """Render a .docx to PDF via Microsoft Word (Windows), updating fields
    (TOC / page numbers) first. Returns (ok, error_message)."""
    import subprocess
    import tempfile
    docx_path, pdf_path = os.path.abspath(docx_path), os.path.abspath(pdf_path)
    ps = (
        "param($in,$out)\n"
        "try {\n"
        "  $w = New-Object -ComObject Word.Application\n"
        "  $w.Visible = $false\n"
        "  $d = $w.Documents.Open($in)\n"
        "  try { $d.Fields.Update() | Out-Null } catch {}\n"
        "  try { foreach($t in $d.TablesOfContents){ $t.Update() } } catch {}\n"
        "  $d.SaveAs([ref]$out,[ref]17)\n"
        "  $d.Close($false); $w.Quit()\n"
        "  Write-Output 'OK'\n"
        "} catch { Write-Output ('ERR:' + $_.Exception.Message) }\n")
    fh = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8")
    try:
        fh.write(ps)
        fh.close()
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             fh.name, docx_path, pdf_path],
            capture_output=True, text=True, timeout=180)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if os.path.exists(pdf_path) and "ERR:" not in out:
            return True, ""
        return False, (out[:200] or "Microsoft Word not available")
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.remove(fh.name)
        except OSError:
            pass


# ============================================================ CLI (headless) ==
def _run_cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="SEO Audit Pro — headless audit.")
    ap.add_argument("url")
    ap.add_argument("--out", default="", help="Output .docx path")
    ap.add_argument("--pages", type=int, default=25, help="Max pages to crawl")
    ap.add_argument("--single", action="store_true", help="Single-page audit")
    ap.add_argument("--json", default="", help="Also write the raw result JSON")
    ap.add_argument("--pdf", default="", help="Also render a PDF (needs Word)")
    ap.add_argument("--competitors", default="",
                    help="Comma-separated competitor URLs")
    args = ap.parse_args(argv)

    import audit
    import charts as charts_mod
    import report_html
    import report_docx

    params = {"url": args.url, "max_pages": args.pages,
              "single_page": args.single, "psi_strategy": "both",
              "api_key": os.environ.get("GROQ_API_KEY", ""),
              "psi_key": os.environ.get("PAGESPEED_API_KEY", ""),
              "competitors": [u.strip() for u in args.competitors.split(",")
                              if u.strip()]}
    result = audit.run_audit(params, progress=lambda m: print("... " + m,
                                                             file=sys.stderr))
    sc = result.get("score") or {}
    print("Overall: %d/100 (grade %s) — %s"
          % (sc.get("score", 0), sc.get("grade", "?"), sc.get("label", "")),
          file=sys.stderr)

    out = args.out or ("SEO-Audit-%s-%s.docx" % (result.get("domain", "site"),
                                                 result.get("date", "")))
    renderer = charts_mod.ChartRenderer()
    try:
        chart_imgs = report_html.build_charts(renderer, result)
        report_docx.generate_docx_report(result, out, chart_imgs)
    finally:
        renderer.cleanup()
    print("Wrote %s" % out, file=sys.stderr)
    if args.pdf:
        ok, err = _docx_to_pdf(out, args.pdf)
        print("Wrote %s" % args.pdf if ok else "PDF failed: %s" % err,
              file=sys.stderr)
    if args.json:
        import json
        slim = {k: v for k, v in result.items()
                if k not in ("all_facts", "home_facts")}
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(slim, fh, indent=2, ensure_ascii=False, default=str)
        print("Wrote %s" % args.json, file=sys.stderr)
    return 0


# ============================================================ GUI =============
def _run_gui():
    from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot
    from PySide6.QtGui import QFont, QIcon, QColor
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QLineEdit, QPushButton, QCheckBox, QComboBox, QLabel,
        QGroupBox, QTabWidget, QTextBrowser, QProgressBar, QFileDialog,
        QMessageBox, QPlainTextEdit, QSpinBox,
        QStackedWidget, QListWidget, QFrame, QGraphicsDropShadowEffect,
    )

    import audit
    import charts as charts_mod
    import report_html
    import report_docx
    import seo_checks
    import llm
    import prefs

    CATS = [(k, lbl) for k, lbl, _w in seo_checks.CATEGORIES]
    CAT_SHORT = {"technical": "Technical", "onpage": "On-Page",
                 "performance": "Performance", "content": "Content",
                 "mobile": "Mobile", "accessibility": "Accessibility",
                 "security": "Security", "ux": "UX"}

    class AuditWorker(QObject):
        finished = Signal(dict)
        error = Signal(str)
        progress = Signal(str)

        def __init__(self, params):
            super().__init__()
            self.params = params

        @Slot()
        def run(self):
            try:
                self.finished.emit(audit.run_audit(self.params,
                                                   progress=self.progress.emit))
            except Exception as e:
                self.error.emit(str(e))

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("SEO Audit Pro — Mindcob")
            self.resize(1140, 860)
            self.result = None
            self._threads = []
            self._cancel = None
            self._charts = None
            self._chart_imgs = {}

            self._spec = [("overview", "Overview"), ("dashboard", "Dashboard"),
                          ("action", "Action Plan")]
            self._spec += [(k, CAT_SHORT.get(k, lbl)) for k, lbl in CATS]
            self._spec += [("competitors", "Competitors"),
                           ("crawlmap", "Crawl Map"), ("rawjson", "Raw JSON")]

            central = QWidget()
            self.setCentralWidget(central)
            outer = QHBoxLayout(central)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            content_card = self._build_content()
            outer.addWidget(self._build_sidebar())

            main = QWidget()
            main.setObjectName("mainArea")
            m = QVBoxLayout(main)
            m.setContentsMargins(20, 18, 20, 14)
            m.setSpacing(12)
            m.addWidget(self._build_toolbar())
            m.addWidget(content_card, 1)
            m.addWidget(self._build_log())
            m.addLayout(self._build_footer())
            outer.addWidget(main, 1)

            self.nav.currentRowChanged.connect(self._on_nav)
            self.nav.setCurrentRow(0)
            self.set_status("Ready. Enter a website URL and press Audit.")

        # ---- header ----
        def _shadow(self, w, blur=20, y=4, alpha=22):
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(blur)
            eff.setOffset(0, y)
            eff.setColor(QColor(18, 28, 46, alpha))
            w.setGraphicsEffect(eff)

        # ---- sidebar ----
        def _build_sidebar(self):
            bar = QWidget()
            bar.setObjectName("sidebar")
            bar.setFixedWidth(230)
            v = QVBoxLayout(bar)
            v.setContentsMargins(14, 18, 14, 14)
            v.setSpacing(6)
            brand = QHBoxLayout()
            brand.setSpacing(11)
            logo = QLabel("SA")
            logo.setObjectName("logo")
            logo.setFixedSize(40, 40)
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            brand.addWidget(logo)
            bcol = QVBoxLayout()
            bcol.setSpacing(0)
            t1 = QLabel("SEO Audit")
            t1.setObjectName("brandTitle")
            t2 = QLabel("Pro")
            t2.setObjectName("brandSub")
            bcol.addWidget(t1)
            bcol.addWidget(t2)
            brand.addLayout(bcol)
            brand.addStretch(1)
            v.addLayout(brand)
            v.addSpacing(14)
            self.nav = QListWidget()
            self.nav.setObjectName("nav")
            self.nav.setFrameShape(QFrame.Shape.NoFrame)
            self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
            for _key, label in self._spec:
                self.nav.addItem(label)
            self.nav.addItem("Settings")
            v.addWidget(self.nav, 1)
            foot = QLabel("MINDCOB")
            foot.setObjectName("brand")
            foot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.addWidget(foot)
            return bar

        # ---- toolbar (input + audit) ----
        def _build_toolbar(self):
            card = QFrame()
            card.setObjectName("toolbar")
            g = QGridLayout(card)
            g.setContentsMargins(16, 14, 16, 14)
            g.setHorizontalSpacing(10)
            g.setVerticalSpacing(10)
            g.setColumnStretch(0, 1)
            self.url_edit = QLineEdit()
            self.url_edit.setPlaceholderText(
                "https://example.com  —  the whole site is crawled and audited")
            self.url_edit.returnPressed.connect(self._audit)
            g.addWidget(self.url_edit, 0, 0)
            self.audit_btn = QPushButton("Audit website")
            self.audit_btn.setObjectName("primary")
            self.audit_btn.setMinimumHeight(38)
            self.audit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.audit_btn.clicked.connect(self._audit)
            g.addWidget(self.audit_btn, 0, 1)
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setObjectName("danger")
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.cancel_btn.clicked.connect(self._cancel_run)
            g.addWidget(self.cancel_btn, 0, 2)
            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.setVisible(False)
            g.addWidget(self.progress, 1, 0, 1, 3)
            self._shadow(card)
            return card

        # ---- content (nav-driven stack) ----
        def _build_content(self):
            card = QFrame()
            card.setObjectName("contentCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(0, 0, 0, 0)
            self.content_stack = QStackedWidget()
            self.views = {}
            for key, _label in self._spec:
                tb = QTextBrowser()
                tb.setOpenExternalLinks(True)
                self.views[key] = tb
                self.content_stack.addWidget(tb)
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(12, 12, 12, 12)
            pv.addWidget(self._build_settings_group())
            pv.addStretch(1)
            self.content_stack.addWidget(page)
            cl.addWidget(self.content_stack)
            self._set_placeholder()
            return card

        def _on_nav(self, row):
            if 0 <= row < self.content_stack.count():
                self.content_stack.setCurrentIndex(row)

        # ---- input ----
        def _build_input_group(self):
            box = QGroupBox("Website  —  the tool crawls it and audits every page")
            lay = QGridLayout(box)
            lay.setHorizontalSpacing(12)
            lay.setVerticalSpacing(11)
            lay.setColumnStretch(1, 1)
            lay.addWidget(QLabel("Website URL:"), 0, 0)
            self.url_edit = QLineEdit()
            self.url_edit.setPlaceholderText("https://example.com")
            self.url_edit.returnPressed.connect(self._audit)
            lay.addWidget(self.url_edit, 0, 1)
            return box

        def _build_settings_group(self):
            box = QGroupBox("Options  —  saved automatically, entered once")
            lay = QGridLayout(box)
            lay.setHorizontalSpacing(12)
            lay.setVerticalSpacing(10)
            for c in (1, 3):
                lay.setColumnStretch(c, 1)

            # remember one key per provider (from the environment / saved .env)
            # so switching providers never loses another, and restore the last
            # active provider.
            self._llm_keys = {p: os.environ.get(llm.ENV_KEYS[p], "")
                              for p in llm.PROVIDERS}
            self._saved = prefs.load_settings()
            sp = self._saved.get("provider")
            if sp in llm.PROVIDERS and self._llm_keys.get(sp):
                self._active_provider = sp
            else:
                self._active_provider = next(
                    (p for p in llm.PROVIDERS if self._llm_keys[p]),
                    sp if sp in llm.PROVIDERS else "groq")

            lay.addWidget(QLabel("Max pages to crawl:"), 0, 0)
            self.pages_spin = QSpinBox()
            self.pages_spin.setRange(1, 100)
            self.pages_spin.setValue(25)
            lay.addWidget(self.pages_spin, 0, 1)
            self.single_cb = QCheckBox("Single page only (don't crawl)")
            self.single_cb.toggled.connect(
                lambda on: self.pages_spin.setDisabled(on))
            lay.addWidget(self.single_cb, 0, 2, 1, 2)

            self.autoopen_cb = QCheckBox(
                "Auto-generate the Word report and open it when the audit "
                "finishes")
            self.autoopen_cb.setChecked(True)
            lay.addWidget(self.autoopen_cb, 6, 0, 1, 4)

            lay.addWidget(QLabel("PageSpeed strategy:"), 1, 0)
            self.psi_combo = QComboBox()
            self.psi_combo.addItems(["Mobile + Desktop", "Mobile only"])
            lay.addWidget(self.psi_combo, 1, 1)
            lay.addWidget(QLabel("PageSpeed key (optional):"), 1, 2)
            self.psi_key = QLineEdit(os.environ.get("PAGESPEED_API_KEY", ""))
            self.psi_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.psi_key.setPlaceholderText("blank = keyless (works fine)")
            lay.addWidget(self.psi_key, 1, 3)

            # category toggles
            checks = QHBoxLayout()
            checks.addWidget(QLabel("Check groups:"))
            self.cat_cbs = {}
            for key, label in CATS:
                cb = QCheckBox(CAT_SHORT.get(key, label))
                cb.setChecked(True)
                cb.setToolTip(label)
                self.cat_cbs[key] = cb
                checks.addWidget(cb)
            checks.addStretch(1)
            lay.addLayout(checks, 2, 0, 1, 4)

            lay.addWidget(QLabel("Competitor URLs (optional):"), 7, 0)
            self.competitor_edit = QLineEdit()
            self.competitor_edit.setPlaceholderText(
                "comma-separated, e.g. https://rival1.com, https://rival2.com")
            self.competitor_edit.setToolTip(
                "Benchmark speed, content, metadata and technical scores "
                "side-by-side against up to 3 competitors.")
            lay.addWidget(self.competitor_edit, 7, 1, 1, 3)

            # optional LLM
            lay.addWidget(QLabel("AI summary provider:"), 3, 0)
            self.provider_combo = QComboBox()
            self.provider_combo.addItems([llm.PROVIDER_LABELS[p]
                                          for p in llm.PROVIDERS])
            self.provider_combo.currentIndexChanged.connect(
                self._on_provider_changed)
            lay.addWidget(self.provider_combo, 3, 1)
            lay.addWidget(QLabel("Model:"), 3, 2)
            self.model_combo = QComboBox()
            self.model_combo.setEditable(True)
            self.model_combo.addItems(llm.suggested_models("groq"))
            lay.addWidget(self.model_combo, 3, 3)

            lay.addWidget(QLabel("AI API key (optional):"), 4, 0)
            self.key_edit = QLineEdit(self._llm_keys.get(self._active_provider,
                                                         ""))
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.key_edit.setPlaceholderText(
                "blank = summary written from a template. Groq is free at "
                "console.groq.com")
            lay.addWidget(self.key_edit, 4, 1, 1, 2)
            lay.addWidget(QLabel("Proxy (optional):"), 4, 3)
            self.proxy_edit = QLineEdit()
            self.proxy_edit.setPlaceholderText("http://host:port")
            lay.addWidget(self.proxy_edit, 5, 3)

            hint = QLabel(
                "No key is required. PageSpeed Insights runs keyless (Core Web "
                "Vitals + Lighthouse scores). Paste any one AI key — Groq (free), "
                "OpenRouter, Anthropic or Gemini — only to get an AI-written "
                "executive summary; the audit itself is fully free and offline.")
            hint.setWordWrap(True)
            hint.setObjectName("hint")
            lay.addWidget(hint, 5, 0, 1, 3)

            self.save_settings_btn = QPushButton("Save keys & settings")
            self.save_settings_btn.setObjectName("ghost")
            self.save_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.save_settings_btn.clicked.connect(self._save_settings_clicked)
            lay.addWidget(self.save_settings_btn, 8, 0)
            note = QLabel("Keys and options are saved locally in this tool "
                          "(.env / settings.json) and load automatically next "
                          "time — and every audit saves them for you. Nothing is "
                          "sent anywhere.")
            note.setWordWrap(True)
            note.setObjectName("hint")
            lay.addWidget(note, 8, 1, 1, 3)

            self._restore_saved()
            return box

        def _on_provider_changed(self, _i):
            # keep the key that was showing under the previous provider, then
            # load the newly-selected provider's key.
            if getattr(self, "_active_provider", None) in self._llm_keys:
                self._llm_keys[self._active_provider] = \
                    self.key_edit.text().strip()
            prov = llm.PROVIDERS[self.provider_combo.currentIndex()]
            self._active_provider = prov
            self.model_combo.clear()
            self.model_combo.addItems(llm.suggested_models(prov))
            self.key_edit.setText(self._llm_keys.get(prov, ""))

        def _restore_saved(self):
            """Apply the saved provider/model/keys/options to the widgets."""
            s = getattr(self, "_saved", {}) or {}
            try:
                idx = llm.PROVIDERS.index(self._active_provider)
                self.provider_combo.blockSignals(True)
                self.provider_combo.setCurrentIndex(idx)
                self.provider_combo.blockSignals(False)
            except (ValueError, AttributeError):
                pass
            self.model_combo.clear()
            self.model_combo.addItems(llm.suggested_models(self._active_provider))
            # Only restore the saved model if it belongs to the active provider —
            # otherwise (we fell back to a different provider because the saved
            # one has no key) keep that provider's default model.
            if s.get("model") and s.get("provider") == self._active_provider:
                self.model_combo.setCurrentText(s["model"])
            self.key_edit.setText(self._llm_keys.get(self._active_provider, ""))
            if s.get("max_pages"):
                try:
                    self.pages_spin.setValue(int(s["max_pages"]))
                except (TypeError, ValueError):
                    pass
            self.single_cb.setChecked(bool(s.get("single_page", False)))
            self.autoopen_cb.setChecked(bool(s.get("autoopen", True)))
            if s.get("psi_strategy") == "mobile":
                self.psi_combo.setCurrentIndex(1)
            if s.get("proxy"):
                self.proxy_edit.setText(s["proxy"])

        def _persist(self):
            """Write keys to .env and options to settings.json. Never raises."""
            try:
                self._llm_keys[self._active_provider] = \
                    self.key_edit.text().strip()
                env = {llm.ENV_KEYS[p]: self._llm_keys.get(p, "")
                       for p in llm.PROVIDERS}
                env["PAGESPEED_API_KEY"] = self.psi_key.text().strip()
                prefs.save_env(env)
                prefs.save_settings({
                    "provider": self._active_provider,
                    "model": self.model_combo.currentText().strip(),
                    "max_pages": self.pages_spin.value(),
                    "psi_strategy": ("both" if self.psi_combo.currentIndex() == 0
                                     else "mobile"),
                    "single_page": self.single_cb.isChecked(),
                    "autoopen": self.autoopen_cb.isChecked(),
                    "proxy": self.proxy_edit.text().strip(),
                })
                return True
            except Exception as e:
                self._log("Could not save settings: %s" % e)
                return False

        def _save_settings_clicked(self):
            if self._persist():
                self.set_status("Saved — your keys and options will load "
                                "automatically next time.")

        def _build_action_row(self):
            row = QHBoxLayout()
            self.audit_btn = QPushButton("Audit website")
            self.audit_btn.setObjectName("primary")
            self.audit_btn.setMinimumHeight(40)
            self.audit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.audit_btn.clicked.connect(self._audit)
            row.addWidget(self.audit_btn)
            self.cancel_btn = QPushButton("Cancel")
            self.cancel_btn.setObjectName("danger")
            self.cancel_btn.setMinimumHeight(40)
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.clicked.connect(self._cancel_run)
            row.addWidget(self.cancel_btn)
            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.setVisible(False)
            self.progress.setMaximumWidth(220)
            row.addWidget(self.progress)
            row.addStretch(1)
            self.options_btn = QPushButton("Options")
            self.options_btn.setObjectName("ghost")
            self.options_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.options_btn.clicked.connect(self._toggle_settings)
            row.addWidget(self.options_btn)
            return row

        def _toggle_settings(self):
            show = not self._settings_box.isVisible()
            self._settings_box.setVisible(show)
            self.options_btn.setText("Hide options" if show else "Options")
            # force the central layout to recompute — otherwise collapsing the
            # panel leaves the log/footer clipped until the next window resize
            lay = self.centralWidget().layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()

        def _build_tabs(self):
            self.tabs = QTabWidget()
            self.tabs.setDocumentMode(True)
            self.tabs.setUsesScrollButtons(True)
            self.views = {}
            spec = [("overview", "Overview"), ("dashboard", "Dashboard"),
                    ("action", "Action Plan")]
            spec += [(k, CAT_SHORT.get(k, lbl)) for k, lbl in CATS]
            spec += [("competitors", "Competitors"), ("crawlmap", "Crawl Map"),
                     ("rawjson", "Raw JSON")]
            for key, label in spec:
                tb = QTextBrowser()
                tb.setOpenExternalLinks(True)
                self.views[key] = tb
                self.tabs.addTab(tb, label)
            self._set_placeholder()
            return self.tabs

        def _build_log(self):
            self.log_pane = QPlainTextEdit()
            self.log_pane.setObjectName("log")
            self.log_pane.setReadOnly(True)
            self.log_pane.setMaximumHeight(88)
            self.log_pane.setPlaceholderText("Activity log…")
            return self.log_pane

        def _build_footer(self):
            row = QHBoxLayout()
            self.status_lbl = QLabel("")
            self.status_lbl.setObjectName("status")
            row.addWidget(self.status_lbl, 1)
            self.save_docx_btn = QPushButton("Export report (.docx)")
            self.save_docx_btn.setObjectName("primary")
            self.save_docx_btn.setEnabled(False)
            self.save_docx_btn.clicked.connect(self._save_docx)
            row.addWidget(self.save_docx_btn)
            self.save_pdf_btn = QPushButton("Export (.pdf)")
            self.save_pdf_btn.setEnabled(False)
            self.save_pdf_btn.clicked.connect(self._save_pdf)
            row.addWidget(self.save_pdf_btn)
            self.save_html_btn = QPushButton("Export (.html)")
            self.save_html_btn.setEnabled(False)
            self.save_html_btn.clicked.connect(self._save_html)
            row.addWidget(self.save_html_btn)
            self.save_json_btn = QPushButton("Export (.json)")
            self.save_json_btn.setEnabled(False)
            self.save_json_btn.clicked.connect(self._save_json)
            row.addWidget(self.save_json_btn)
            return row

        # ---- helpers ----
        def set_status(self, text):
            self.status_lbl.setText(text)

        def _log(self, msg):
            self.set_status(msg)
            self.log_pane.appendPlainText("%s  %s"
                                          % (time.strftime("%H:%M:%S"), msg))

        def _set_placeholder(self):
            self.views["overview"].setHtml(
                "<div style='color:#888;font-family:Segoe UI;padding:24px'>"
                "<h2>SEO Audit Pro</h2>"
                "<p>Enter a <b>website URL</b> and press <b>Audit website</b>. "
                "The tool crawls the site, runs a full technical, on-page, "
                "performance (Core Web Vitals), security, structured-data and "
                "E-E-A-T audit, and builds a client-ready Word report with "
                "charts.</p>"
                "<p><b>100% free — no API keys needed.</b> PageSpeed Insights "
                "runs keyless. An optional free Groq key only adds an AI-written "
                "executive summary.</p></div>")

        # ---- run ----
        def _audit(self):
            url = self.url_edit.text().strip()
            if not url:
                QMessageBox.information(self, "No URL",
                                        "Enter a website URL first.")
                return
            enabled = {k for k, cb in self.cat_cbs.items() if cb.isChecked()}
            if not enabled:
                QMessageBox.information(self, "No checks",
                                        "Enable at least one check group.")
                return
            self._cancel = threading.Event()
            prov = llm.PROVIDERS[self.provider_combo.currentIndex()]
            params = {
                "url": url,
                "max_pages": self.pages_spin.value(),
                "single_page": self.single_cb.isChecked(),
                "psi_strategy": ("both" if self.psi_combo.currentIndex() == 0
                                 else "mobile"),
                "psi_key": self.psi_key.text().strip(),
                "proxy": self.proxy_edit.text().strip(),
                "enabled": enabled,
                "api_key": self.key_edit.text().strip(),
                "model": self.model_combo.currentText().strip()
                or llm.default_model(prov),
                "competitors": [u.strip() for u in
                                self.competitor_edit.text().replace(";", ",")
                                .split(",") if u.strip()],
                "cancel": self._cancel,
            }
            self._cleanup_charts()
            self._persist()   # remember keys & options for next launch
            self.audit_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.progress.setVisible(True)
            for b in (self.save_docx_btn, self.save_pdf_btn, self.save_html_btn,
                      self.save_json_btn):
                b.setEnabled(False)
            self.log_pane.clear()
            self._log("Starting audit…")

            thread = QThread()
            worker = AuditWorker(params)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progress.connect(self._log)
            worker.finished.connect(self._on_finished)
            worker.error.connect(self._on_error)
            worker.finished.connect(thread.quit)
            worker.error.connect(thread.quit)
            thread.finished.connect(thread.deleteLater)
            self._threads.append((thread, worker))
            thread.start()

        def _cancel_run(self):
            if self._cancel is not None:
                self._cancel.set()
                self.cancel_btn.setEnabled(False)
                self.set_status("Cancelling after the current step…")

        def _run_done(self):
            self.audit_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.progress.setVisible(False)

        def _on_error(self, msg):
            self._run_done()
            if "cancel" in msg.lower():
                self._log("Audit cancelled.")
                return
            self.set_status("Error.")
            self._log("Error: %s" % msg)
            QMessageBox.critical(self, "Could not audit", msg)

        def _on_finished(self, result):
            self.result = result
            self._run_done()
            try:
                self._charts = charts_mod.ChartRenderer()
                self._chart_imgs = report_html.build_charts(self._charts, result)
            except Exception as e:
                self._chart_imgs = {}
                self._log("Chart rendering failed: %s" % e)
            self._render_all(result)
            for b in (self.save_docx_btn, self.save_pdf_btn, self.save_html_btn,
                      self.save_json_btn):
                b.setEnabled(True)
            errs = result.get("errors") or []
            self.set_status("Audit complete.%s"
                            % ("  (%d warning(s))" % len(errs) if errs else ""))
            if self.autoopen_cb.isChecked():
                self._auto_export(result)

        def _auto_export(self, result):
            try:
                outdir = os.path.join(APP_DIR, "reports")
                os.makedirs(outdir, exist_ok=True)
                path = os.path.join(outdir, self._default_name() + ".docx")
                report_docx.generate_docx_report(result, path,
                                                 self._chart_imgs)
                self._log("Report saved → %s" % path)
                self.set_status("Report generated and opened: %s" % path)
                self._open_file(path)
            except Exception as e:
                self._log("Auto-generate failed: %s" % e)
                QMessageBox.warning(self, "Report not generated",
                                    "Could not auto-generate the report:\n%s\n\n"
                                    "Use the Export button to try again." % e)

        def _open_file(self, path):
            try:
                if sys.platform.startswith("win"):
                    os.startfile(path)  # noqa: E1101
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", path])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", path])
            except Exception:
                pass

        def _render_all(self, r):
            ci = self._chart_imgs

            def put(key, fn):
                try:
                    self.views[key].setHtml(fn())
                except Exception as e:
                    self.views[key].setHtml(
                        "<div style='padding:20px;color:#c0392b;"
                        "font-family:Segoe UI'>This section could not render: %s"
                        "</div>" % report_html.esc(str(e)))
                    self._log("Render '%s' failed: %s" % (key, e))

            put("overview", lambda: report_html.overview_html(r, ci))
            put("dashboard", lambda: report_html.dashboard_html(r, ci))
            put("action", lambda: report_html.action_html(r, ci))
            for key, _lbl in CATS:
                put(key, (lambda kk: lambda: report_html.category_html(
                    r, kk, ci))(key))
            put("competitors", lambda: report_html.competitor_html(r, ci))
            put("crawlmap", lambda: report_html.crawlmap_html(r))
            put("rawjson", lambda: report_html.raw_json_html(r))

        # ---- save ----
        def _default_name(self):
            base = "SEO-Audit-%s-%s" % (self.result.get("domain", "site"),
                                        self.result.get("date", ""))
            return "".join(c for c in base if c.isalnum() or c in "-_.") or \
                "SEO-Audit"

        def _save_docx(self):
            if not self.result:
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Word report", self._default_name() + ".docx",
                "Word report (*.docx)")
            if not path:
                return
            try:
                report_docx.generate_docx_report(self.result, path,
                                                 self._chart_imgs)
                self.set_status("Saved report to %s" % path)
                self._open_file(path)
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))

        def _save_pdf(self):
            if not self.result:
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Save PDF report", self._default_name() + ".pdf",
                "PDF report (*.pdf)")
            if not path:
                return
            self.set_status("Rendering PDF (via Word)…")
            try:
                import tempfile
                tmp = os.path.join(tempfile.gettempdir(),
                                   "seoauditpro_tmp.docx")
                report_docx.generate_docx_report(self.result, tmp,
                                                 self._chart_imgs)
                ok, err = _docx_to_pdf(tmp, path)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                if ok:
                    self.set_status("Saved PDF to %s" % path)
                    self._open_file(path)
                else:
                    QMessageBox.warning(
                        self, "PDF export needs Microsoft Word",
                        "Could not render a PDF (%s).\n\nThe .docx export works "
                        "everywhere; PDF uses Microsoft Word if installed. You "
                        "can also open the .docx and 'Save as PDF'." % err)
            except Exception as e:
                QMessageBox.critical(self, "PDF export failed", str(e))

        def _save_html(self):
            if not self.result:
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Save HTML report", self._default_name() + ".html",
                "HTML report (*.html)")
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(report_html.full_html(self.result, self._chart_imgs))
                self.set_status("Saved report to %s" % path)
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))

        def _save_json(self):
            if not self.result:
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Save JSON", self._default_name() + ".json",
                "JSON (*.json)")
            if not path:
                return
            try:
                import json
                slim = {k: v for k, v in self.result.items()
                        if k not in ("all_facts", "home_facts")}
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(slim, fh, indent=2, ensure_ascii=False,
                              default=str)
                self.set_status("Saved JSON to %s" % path)
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e))

        def _cleanup_charts(self):
            if self._charts is not None:
                try:
                    self._charts.cleanup()
                except Exception:
                    pass
                self._charts = None
            self._chart_imgs = {}

        def closeEvent(self, ev):
            self._cleanup_charts()
            super().closeEvent(ev)

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mindcob.seoauditpro.1.0")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("SEO Audit Pro")
    app.setStyle("Fusion")
    QApplication.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    _ico = resource_path(os.path.join("assets", "icon.ico"))
    _png = resource_path(os.path.join("assets", "icon.png"))
    _icon_path = _ico if os.path.exists(_ico) else _png
    _app_icon = None
    if os.path.exists(_icon_path):
        _app_icon = QIcon(_icon_path)
        app.setWindowIcon(_app_icon)
    win = MainWindow()
    if _app_icon:
        win.setWindowIcon(_app_icon)
    if "--selftest" in sys.argv:
        from PySide6.QtCore import QTimer
        win.show()
        QTimer.singleShot(400, app.quit)
        return app.exec()
    win.show()
    return app.exec()


STYLE = """
QWidget { color: #1F2733; font-size: 13px; }
QMainWindow, QMainWindow > QWidget { background: #F1F3F7; }
#mainArea { background: #F1F3F7; }
#sidebar  { background: #FFFFFF; border-right: 1px solid #E6EAF0; }
#logo { background: #2E7DF2; color: #FFFFFF; border-radius: 12px;
        font-size: 16px; font-weight: 700; }
#brandTitle { font-size: 16px; font-weight: 700; color: #1B2430; }
#brandSub   { font-size: 12px; font-weight: 600; color: #93A3C0;
              letter-spacing: 1px; }
#brand    { font-size: 11px; font-weight: 700; letter-spacing: 2px;
            color: #BAC2CE; }
QListWidget#nav { background: transparent; border: 0; outline: 0; }
QListWidget#nav::item {
    color: #55617A; padding: 9px 12px; border-radius: 9px; margin: 2px 0;
    font-weight: 600; font-size: 13px;
}
QListWidget#nav::item:hover    { background: #EEF3FB; color: #2E7DF2; }
QListWidget#nav::item:selected { background: #2E7DF2; color: #FFFFFF; }
#toolbar, #contentCard {
    background: #FFFFFF; border: 1px solid #E9EDF3; border-radius: 14px;
}
QGroupBox {
    background: #FFFFFF; border: 1px solid #E6EAF0; border-radius: 14px;
    margin-top: 8px; padding: 18px 18px 16px 18px; font-weight: 500;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 16px; top: -2px; padding: 0 6px; color: #96A0AE;
    font-size: 11px; font-weight: 600;
}
QLineEdit, QComboBox, QSpinBox {
    background: #FFFFFF; border: 1px solid #DCE2EA; border-radius: 9px;
    padding: 9px 12px; min-height: 18px;
    selection-background-color: #2E7DF2; selection-color: #FFFFFF;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #C3CCD8; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 2px solid #2E7DF2; padding: 8px 11px; }
QComboBox QAbstractItemView {
    background: #FFFFFF; border: 1px solid #E6EAF0; border-radius: 9px;
    padding: 4px; outline: 0;
    selection-background-color: #EAF2FE; selection-color: #1B2430;
}
QPushButton {
    background: #FFFFFF; color: #46515F; border: 1px solid #DCE2EA;
    border-radius: 9px; padding: 8px 15px; font-weight: 600;
}
QPushButton:hover   { background: #F5F8FC; border-color: #C3CCD8; }
QPushButton:pressed { background: #EBF1F8; }
QPushButton:disabled{ color: #B4BCC7; background: #F6F8FB; border-color: #EAEEF3; }
QPushButton#primary {
    background: #2E7DF2; color: #FFFFFF; border: 0; padding: 11px 26px;
    font-weight: 700; font-size: 14px;
}
QPushButton#primary:hover    { background: #2571E8; }
QPushButton#primary:pressed  { background: #1C66DC; }
QPushButton#primary:disabled { background: #B6CEF6; color: #F3F7FE; }
QPushButton#danger  { color: #D8483A; background: #FEF6F5; border-color: #F3CDC8; }
QPushButton#danger:hover    { background: #FCEBE8; }
QPushButton#danger:disabled { color: #DBB4AF; background: #FBF5F4;
                              border-color: #F1E1DE; }
QPushButton#ghost {
    background: transparent; color: #6C7788; border: 0; font-weight: 600;
    padding: 8px 12px;
}
QPushButton#ghost:hover { color: #2E7DF2; background: transparent; }
QCheckBox { spacing: 8px; color: #46515F; padding: 3px 0; }
QTabWidget::pane {
    border: 1px solid #E6EAF0; border-radius: 14px; background: #FFFFFF;
    top: -1px;
}
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent; color: #808A98; padding: 9px 13px;
    margin: 0 1px; border: 0; border-bottom: 2px solid transparent;
    font-weight: 600; font-size: 12px;
}
QTabBar::tab:hover    { color: #2E7DF2; }
QTabBar::tab:selected { color: #1C66DC; border-bottom: 2px solid #2E7DF2; }
QTextBrowser {
    background: transparent; border: 0; padding: 12px 18px;
}
QProgressBar { border: 0; border-radius: 6px; background: #DEE4EC; height: 6px;
               text-align: center; }
QProgressBar::chunk { background: #2E7DF2; border-radius: 6px; }
QPlainTextEdit#log {
    background: #FBFCFE; color: #6C7788; border: 1px solid #EAEEF3;
    border-radius: 11px; padding: 6px 8px;
    font-family: Consolas, monospace; font-size: 11px;
}
#hint   { color: #98A2AF; font-size: 12px; }
#status { color: #66707E; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 3px; }
QScrollBar::handle:vertical { background: #D2DAE4; border-radius: 5px;
                              min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #A9BAD2; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QToolTip { background: #1B2430; color: #F1F3F7; border: 0; padding: 6px 9px;
           border-radius: 7px; }
"""


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--cli":
        return _run_cli(argv[1:])
    if argv and argv[0] == "--pyside":
        return _run_gui()
    # Launch Web UI by default
    import web_app
    return web_app.start_server()


if __name__ == "__main__":
    sys.exit(main())
