import os

from PyQt5.QtCore import QPoint, QPropertyAnimation, QTimer, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QLabel, QLineEdit,
    QPushButton, QCheckBox, QGridLayout,
    QHBoxLayout, QVBoxLayout, QFrame,
    QScrollArea, QStackedWidget, QButtonGroup,
    QSlider, QSpinBox, QDoubleSpinBox, QComboBox,
    QListWidget, QListWidgetItem, QFileDialog,
)

from core.settings import load_settings, save_settings, add_saved_search
from ui.domain_list_dialog import DomainListDialog, ListDialog


class InputScreen(QWidget):
    # domains, areas, fields, headless, max_results, export_dir, filters
    start_signal = pyqtSignal(list, list, list, bool, int, str, dict)

    FIELD_KEYS = [
        "name", "category", "rating", "review_count", "hours", "claim_this_business",
        "address", "website", "phone", "maps_link",
        "latitude", "longitude", "place_id",
        "email", "facebook", "instagram", "linkedin", "twitter", "youtube",
        "review_1", "review_2", "review_3",
    ]
    FIELD_NAMES = [
        "Business Name", "Category", "Rating", "Review Count", "Hours", "Claim this business",
        "Address", "Website", "Phone Number", "Maps Link",
        "Latitude", "Longitude", "Place ID",
        "Email", "Facebook", "Instagram", "LinkedIn", "Twitter/X", "YouTube",
        "Review 1", "Review 2", "Review 3",
    ]

    # Fields that require opening each listing's page (Maps detail).
    DETAIL_FIELDS = {"hours", "claim_this_business", "review_1", "review_2", "review_3"}
    # Fields that require fetching each business website.
    ENRICH_FIELDS = {"email", "facebook", "instagram", "linkedin", "twitter", "youtube"}
    # Off by default because they're the slow paths.
    DEFAULT_OFF_FIELDS = DETAIL_FIELDS | ENRICH_FIELDS

    def __init__(self):
        super().__init__()
        self._extra_domains: list = []
        self._extra_areas: list = []
        self._anim = None
        self.settings = load_settings()
        self._build()
        self._apply_settings_to_ui()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)
        app_name = QLabel("MapHarvest")
        app_name.setObjectName("app_name")
        header.addWidget(app_name)
        header.addStretch()

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        tabs = [("Scrape", 0), ("Filters", 1), ("Settings", 2)]
        self._tab_btns = {}
        for label, idx in tabs:
            btn = QPushButton(label)
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.setChecked(idx == 0)
            self.tab_group.addButton(btn, idx)
            tab_row.addWidget(btn)
            self._tab_btns[idx] = btn
        self.scrape_tab_btn = self._tab_btns[0]
        header.addLayout(tab_row)
        root.addLayout(header)
        root.addSpacing(20)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_scrape_page())
        self.pages.addWidget(self._build_filters_page())
        self.pages.addWidget(self._build_settings_page())
        root.addWidget(self.pages, stretch=1)

        self.tab_group.idClicked.connect(self.pages.setCurrentIndex)

    # ── Scrape tab ───────────────────────────────────────────────────────────
    def _build_scrape_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        columns = QHBoxLayout()
        columns.setSpacing(40)

        left = QVBoxLayout()
        left.setSpacing(0)

        # Domain + list
        domain_header = QHBoxLayout()
        domain_header.addWidget(self._section_label("Domain"))
        domain_header.addStretch()
        self.domain_count_label = QLabel("")
        self.domain_count_label.setObjectName("muted")
        domain_header.addWidget(self.domain_count_label)
        left.addLayout(domain_header)
        left.addSpacing(8)

        domain_row = QHBoxLayout()
        domain_row.setSpacing(10)
        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText("e.g. car dealers")
        self.domain_input.setFixedHeight(40)
        self.list_btn = QPushButton("List")
        self.list_btn.setObjectName("outlined")
        self.list_btn.setFixedSize(72, 40)
        self.list_btn.setToolTip("Add multiple domains to scrape")
        self.list_btn.clicked.connect(self._open_domain_list)
        domain_row.addWidget(self.domain_input, stretch=1)
        domain_row.addWidget(self.list_btn)
        left.addLayout(domain_row)
        left.addSpacing(20)

        # Area + list (multi-city)
        area_header = QHBoxLayout()
        area_header.addWidget(self._section_label("Area"))
        area_header.addStretch()
        self.area_count_label = QLabel("")
        self.area_count_label.setObjectName("muted")
        area_header.addWidget(self.area_count_label)
        left.addLayout(area_header)
        left.addSpacing(8)

        area_row = QHBoxLayout()
        area_row.setSpacing(10)
        self.area_input = QLineEdit()
        self.area_input.setPlaceholderText("e.g. Lahore")
        self.area_input.setFixedHeight(40)
        self.area_list_btn = QPushButton("List")
        self.area_list_btn.setObjectName("outlined")
        self.area_list_btn.setFixedSize(72, 40)
        self.area_list_btn.setToolTip("Add multiple cities/areas to scrape")
        self.area_list_btn.clicked.connect(self._open_area_list)
        area_row.addWidget(self.area_input, stretch=1)
        area_row.addWidget(self.area_list_btn)
        left.addLayout(area_row)
        left.addSpacing(20)

        # Max results
        limit_header = QHBoxLayout()
        limit_header.addWidget(self._section_label("Max Results"))
        limit_header.addStretch()
        self.max_results_label = QLabel("50")
        self.max_results_label.setObjectName("muted")
        limit_header.addWidget(self.max_results_label)
        left.addLayout(limit_header)
        left.addSpacing(10)

        self.max_results_slider = QSlider(Qt.Horizontal)
        self.max_results_slider.setObjectName("limit_slider")
        self.max_results_slider.setMinimum(5)
        self.max_results_slider.setMaximum(100)
        self.max_results_slider.setValue(50)
        self.max_results_slider.setTickPosition(QSlider.TicksBelow)
        self.max_results_slider.setTickInterval(25)
        self.max_results_slider.valueChanged.connect(self._on_max_slider_changed)
        left.addWidget(self.max_results_slider)
        left.addSpacing(20)

        # Export folder
        left.addWidget(self._section_label("Export Folder"))
        left.addSpacing(8)
        export_row = QHBoxLayout()
        export_row.setSpacing(10)
        self.export_browse_btn = QPushButton("…")
        self.export_browse_btn.setObjectName("outlined")
        self.export_browse_btn.setFixedSize(40, 40)
        self.export_browse_btn.setToolTip("Choose folder for automatic CSV exports")
        self.export_browse_btn.clicked.connect(self._browse_export_dir)
        self.export_dir_input = QLineEdit()
        self.export_dir_input.setReadOnly(True)
        self.export_dir_input.setPlaceholderText("Select a folder on your computer")
        self.export_dir_input.setFixedHeight(40)
        export_row.addWidget(self.export_browse_btn)
        export_row.addWidget(self.export_dir_input, stretch=1)
        left.addLayout(export_row)
        left.addSpacing(20)

        # Recent searches
        left.addWidget(self._section_label("Recent Searches"))
        left.addSpacing(8)
        self.saved_list = QListWidget()
        self.saved_list.setObjectName("saved_list")
        self.saved_list.setMinimumHeight(100)
        self.saved_list.itemClicked.connect(self._load_saved_search)
        left.addWidget(self.saved_list, stretch=1)

        # Right column: fields
        right = QVBoxLayout()
        right.setSpacing(0)

        self.fields_section_label = self._section_label("Data to Scrape")
        right.addWidget(self.fields_section_label)
        right.addSpacing(12)

        self.checkboxes = {}
        fields_grid = QGridLayout()
        fields_grid.setHorizontalSpacing(24)
        fields_grid.setVerticalSpacing(9)
        fields_grid.setColumnStretch(0, 1)
        fields_grid.setColumnStretch(1, 1)
        for i, (key, name) in enumerate(zip(self.FIELD_KEYS, self.FIELD_NAMES)):
            cb = QCheckBox(name)
            cb.setChecked(key not in self.DEFAULT_OFF_FIELDS)
            if key in self.DETAIL_FIELDS:
                cb.setToolTip("Opens each listing's page — slower")
            elif key in self.ENRICH_FIELDS:
                cb.setToolTip("Fetches the business website — slower")
            self.checkboxes[key] = cb
            fields_grid.addWidget(cb, i // 2, i % 2)

        fields_holder = QWidget()
        fields_holder.setLayout(fields_grid)
        fields_scroll = QScrollArea()
        fields_scroll.setWidgetResizable(True)
        fields_scroll.setFrameShape(QFrame.NoFrame)
        fields_scroll.setWidget(fields_holder)
        right.addWidget(fields_scroll, stretch=1)

        right.addSpacing(8)
        # Quick select buttons
        quick_row = QHBoxLayout()
        quick_row.setSpacing(8)
        for text, slot in (("All", self._select_all_fields),
                           ("Fast only", self._select_fast_fields),
                           ("None", self._select_no_fields)):
            b = QPushButton(text)
            b.setObjectName("outlined")
            b.setFixedHeight(28)
            b.clicked.connect(slot)
            quick_row.addWidget(b)
        quick_row.addStretch()
        right.addLayout(quick_row)

        right.addSpacing(6)
        fields_hint = QLabel(
            "Hours/Reviews/Claim open each listing; Email & socials fetch each "
            "website. Both are slower and off by default."
        )
        fields_hint.setObjectName("hint")
        fields_hint.setWordWrap(True)
        right.addWidget(fields_hint)

        right.addSpacing(8)
        self.start_btn = QPushButton("Start Scraping")
        self.start_btn.setObjectName("start_btn")
        self.start_btn.setFixedHeight(44)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self._on_start)
        right.addWidget(self.start_btn)

        columns.addLayout(left, stretch=1)
        columns.addLayout(right, stretch=1)
        root.addLayout(columns, stretch=1)

        self._update_domain_count_label()
        self._update_area_count_label()
        return page

    # ── Filters tab ──────────────────────────────────────────────────────────
    def _build_filters_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        intro = QLabel(
            "Only collect businesses that match these rules. Leave everything "
            "blank/zero to keep every result."
        )
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(16)

        layout.addWidget(self._section_label("Rating & Reviews"))
        layout.addSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        grid.addWidget(QLabel("Minimum rating"), 0, 0)
        self.min_rating_spin = QDoubleSpinBox()
        self.min_rating_spin.setObjectName("spin")
        self.min_rating_spin.setRange(0.0, 5.0)
        self.min_rating_spin.setSingleStep(0.5)
        self.min_rating_spin.setDecimals(1)
        self.min_rating_spin.setSpecialValueText("Any")
        self.min_rating_spin.setFixedWidth(90)
        grid.addWidget(self.min_rating_spin, 0, 1)

        grid.addWidget(QLabel("Minimum reviews"), 1, 0)
        self.min_reviews_spin = QSpinBox()
        self.min_reviews_spin.setObjectName("spin")
        self.min_reviews_spin.setRange(0, 1000000)
        self.min_reviews_spin.setSingleStep(10)
        self.min_reviews_spin.setSpecialValueText("Any")
        self.min_reviews_spin.setFixedWidth(90)
        grid.addWidget(self.min_reviews_spin, 1, 1)

        grid.addWidget(QLabel("Maximum reviews"), 2, 0)
        self.max_reviews_spin = QSpinBox()
        self.max_reviews_spin.setObjectName("spin")
        self.max_reviews_spin.setRange(0, 1000000)
        self.max_reviews_spin.setSingleStep(10)
        self.max_reviews_spin.setSpecialValueText("No cap")
        self.max_reviews_spin.setFixedWidth(90)
        grid.addWidget(self.max_reviews_spin, 2, 1)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        rev_hint = QLabel("Review filters open each listing to read the exact count (slower).")
        rev_hint.setObjectName("hint")
        rev_hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(rev_hint)
        layout.addSpacing(20)

        layout.addWidget(self._section_label("Contact & Web presence"))
        layout.addSpacing(10)

        grid2 = QGridLayout()
        grid2.setHorizontalSpacing(16)
        grid2.setVerticalSpacing(12)
        grid2.addWidget(QLabel("Website"), 0, 0)
        self.website_combo = QComboBox()
        self.website_combo.addItems(["Any", "Has a website", "No website"])
        self.website_combo.setFixedWidth(160)
        grid2.addWidget(self.website_combo, 0, 1)

        grid2.addWidget(QLabel("Claim status"), 1, 0)
        self.claim_combo = QComboBox()
        self.claim_combo.addItems(["Any", "Has 'Claim this business' (Unclaimed)", "No 'Claim this business' (Claimed)"])
        self.claim_combo.setFixedWidth(260)
        grid2.addWidget(self.claim_combo, 1, 1)
        grid2.setColumnStretch(2, 1)
        layout.addLayout(grid2)
        layout.addSpacing(10)

        self.require_phone_cb = QCheckBox("Must have a phone number")
        layout.addWidget(self.require_phone_cb)
        layout.addSpacing(6)
        self.require_email_cb = QCheckBox("Must have a discoverable email (fetches website)")
        layout.addWidget(self.require_email_cb)
        layout.addSpacing(20)

        layout.addWidget(self._section_label("Name & Category text"))
        layout.addSpacing(10)
        text_grid = QGridLayout()
        text_grid.setHorizontalSpacing(16)
        text_grid.setVerticalSpacing(10)
        self.name_include_input = QLineEdit()
        self.name_include_input.setPlaceholderText("comma-separated, any match")
        self.name_exclude_input = QLineEdit()
        self.name_exclude_input.setPlaceholderText("comma-separated, exclude if any match")
        self.cat_include_input = QLineEdit()
        self.cat_include_input.setPlaceholderText("e.g. roofing, contractor")
        self.cat_exclude_input = QLineEdit()
        self.cat_exclude_input.setPlaceholderText("e.g. supplier")
        for r, (lbl, w) in enumerate((
            ("Name must include", self.name_include_input),
            ("Name must exclude", self.name_exclude_input),
            ("Category must include", self.cat_include_input),
            ("Category must exclude", self.cat_exclude_input),
        )):
            w.setFixedHeight(36)
            text_grid.addWidget(QLabel(lbl), r, 0)
            text_grid.addWidget(w, r, 1)
        text_grid.setColumnStretch(1, 1)
        layout.addLayout(text_grid)
        layout.addSpacing(16)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_btn = QPushButton("Reset filters")
        reset_btn.setObjectName("outlined")
        reset_btn.setFixedHeight(32)
        reset_btn.clicked.connect(self._reset_filters)
        reset_row.addWidget(reset_btn)
        layout.addLayout(reset_row)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    # ── Settings tab ─────────────────────────────────────────────────────────
    def _build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        layout.addWidget(self._section_label("Browser"))
        layout.addSpacing(10)
        self.headless_cb = QCheckBox("Run headless")
        self.headless_cb.setChecked(False)
        self.headless_cb.setToolTip("Hide the Chrome window while scraping")
        self.headless_cb.toggled.connect(self._persist_settings)
        layout.addWidget(self.headless_cb)
        hint = QLabel("When off, Chrome opens visibly so you can watch progress.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(hint)
        layout.addSpacing(22)

        layout.addWidget(self._section_label("Result Limit"))
        layout.addSpacing(10)
        cap_row = QHBoxLayout()
        cap_row.addWidget(QLabel("Slider maximum"))
        cap_row.addStretch()
        self.limit_cap_spin = QSpinBox()
        self.limit_cap_spin.setObjectName("spin")
        self.limit_cap_spin.setRange(25, 1000)
        self.limit_cap_spin.setSingleStep(25)
        self.limit_cap_spin.setValue(100)
        self.limit_cap_spin.setFixedWidth(80)
        self.limit_cap_spin.valueChanged.connect(self._on_limit_cap_changed)
        cap_row.addWidget(self.limit_cap_spin)
        layout.addLayout(cap_row)
        cap_hint = QLabel("Raise this to allow the scrape slider to go above 100.")
        cap_hint.setObjectName("hint")
        cap_hint.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(cap_hint)
        layout.addStretch()

        scroll.setWidget(page)
        return scroll

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())
        lbl.setObjectName("section_label")
        return lbl

    # ── Field quick-select ───────────────────────────────────────────────────
    def _select_all_fields(self):
        for cb in self.checkboxes.values():
            cb.setChecked(True)

    def _select_fast_fields(self):
        for key, cb in self.checkboxes.items():
            cb.setChecked(key not in self.DEFAULT_OFF_FIELDS)

    def _select_no_fields(self):
        for cb in self.checkboxes.values():
            cb.setChecked(False)

    def _reset_filters(self):
        self.min_rating_spin.setValue(0.0)
        self.min_reviews_spin.setValue(0)
        self.max_reviews_spin.setValue(0)
        self.website_combo.setCurrentIndex(0)
        self.claim_combo.setCurrentIndex(0)
        self.require_phone_cb.setChecked(False)
        self.require_email_cb.setChecked(False)
        for w in (self.name_include_input, self.name_exclude_input,
                  self.cat_include_input, self.cat_exclude_input):
            w.clear()

    # ── Settings glue ────────────────────────────────────────────────────────
    def _apply_settings_to_ui(self):
        cap = int(self.settings.get("max_limit_cap", 100))
        default_max = int(self.settings.get("default_max_results", 50))
        self.limit_cap_spin.blockSignals(True)
        self.limit_cap_spin.setValue(cap)
        self.limit_cap_spin.blockSignals(False)
        self._update_slider_cap(cap)
        self.max_results_slider.blockSignals(True)
        self.max_results_slider.setValue(min(default_max, cap))
        self.max_results_slider.blockSignals(False)
        self._on_max_slider_changed(self.max_results_slider.value())
        self.headless_cb.blockSignals(True)
        self.headless_cb.setChecked(bool(self.settings.get("headless", False)))
        self.headless_cb.blockSignals(False)
        export_dir = self.settings.get("export_dir") or ""
        self.export_dir_input.setText(export_dir)
        self._refresh_saved_list()

    def _update_slider_cap(self, cap: int):
        value = self.max_results_slider.value()
        self.max_results_slider.setMaximum(cap)
        if value > cap:
            self.max_results_slider.setValue(cap)

    def _on_limit_cap_changed(self, cap: int):
        self.settings["max_limit_cap"] = cap
        self._update_slider_cap(cap)
        self._persist_settings()

    def _on_max_slider_changed(self, value: int):
        self.max_results_label.setText(str(value))
        self.settings["default_max_results"] = value

    def _persist_settings(self):
        self.settings["headless"] = self.headless_cb.isChecked()
        self.settings["max_limit_cap"] = self.limit_cap_spin.value()
        self.settings["default_max_results"] = self.max_results_slider.value()
        self.settings["export_dir"] = self.export_dir_input.text().strip()
        save_settings(self.settings)

    def _browse_export_dir(self):
        start = self.export_dir_input.text().strip() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "Select export folder", start)
        if path:
            self.export_dir_input.setText(path)
            self.settings["export_dir"] = path
            save_settings(self.settings)

    def export_dir(self) -> str:
        return self.export_dir_input.text().strip()

    def _format_saved_search(self, entry: dict) -> str:
        domains = ", ".join(entry.get("domains") or [])
        area = entry.get("area", "")
        limit = entry.get("max_results", 50)
        return f"{domains} · {area} · max {limit}"

    def _refresh_saved_list(self):
        self.saved_list.clear()
        for entry in self.settings.get("saved_searches") or []:
            item = QListWidgetItem(self._format_saved_search(entry))
            item.setData(Qt.UserRole, entry)
            self.saved_list.addItem(item)
        if self.saved_list.count() == 0:
            item = QListWidgetItem("No recent searches yet")
            item.setFlags(Qt.NoItemFlags)
            self.saved_list.addItem(item)

    def _load_saved_search(self, item: QListWidgetItem):
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        domains = entry.get("domains") or []
        if domains:
            self.domain_input.setText(domains[0])
            self._extra_domains = domains[1:]
            self._update_domain_count_label()
        self.area_input.setText(entry.get("area", ""))
        self._extra_areas = list(entry.get("areas", []) or [])[1:] if entry.get("areas") else []
        self._update_area_count_label()
        limit = int(entry.get("max_results", 50))
        cap = self.max_results_slider.maximum()
        self.max_results_slider.setValue(min(limit, cap))

    # ── Domain / area lists ──────────────────────────────────────────────────
    def _open_domain_list(self):
        dialog = DomainListDialog(self._extra_domains, self)
        if dialog.exec_() == DomainListDialog.Accepted:
            self._extra_domains = dialog.domains()
            self._update_domain_count_label()

    def _open_area_list(self):
        dialog = ListDialog(
            self._extra_areas, self,
            title="Area List",
            hint="Enter one city/area per line. These run in addition to the main area.",
            placeholder="Toronto\nMississauga\nBrampton\nMarkham",
        )
        if dialog.exec_() == ListDialog.Accepted:
            self._extra_areas = dialog.items()
            self._update_area_count_label()

    def _update_domain_count_label(self):
        count = len(self._extra_domains)
        if count:
            self.domain_count_label.setText(f"+{count} in list")
            self.list_btn.setText(f"List ({count})")
        else:
            self.domain_count_label.setText("")
            self.list_btn.setText("List")

    def _update_area_count_label(self):
        count = len(self._extra_areas)
        if count:
            self.area_count_label.setText(f"+{count} in list")
            self.area_list_btn.setText(f"List ({count})")
        else:
            self.area_count_label.setText("")
            self.area_list_btn.setText("List")

    def _get_domains(self) -> list:
        seen, domains = set(), []
        for raw in [self.domain_input.text().strip(), *self._extra_domains]:
            d = raw.strip()
            if d and d.lower() not in seen:
                seen.add(d.lower())
                domains.append(d)
        return domains

    def _get_areas(self) -> list:
        seen, areas = set(), []
        for raw in [self.area_input.text().strip(), *self._extra_areas]:
            a = raw.strip()
            if a and a.lower() not in seen:
                seen.add(a.lower())
                areas.append(a)
        return areas

    def get_checked_fields(self) -> list:
        return [key for key, cb in self.checkboxes.items() if cb.isChecked()]

    def get_filters(self) -> dict:
        claim_idx = self.claim_combo.currentIndex()
        claim_status = "any"
        if claim_idx == 1:
            claim_status = "unclaimed"
        elif claim_idx == 2:
            claim_status = "claimed"
        return {
            "min_rating": self.min_rating_spin.value(),
            "min_reviews": self.min_reviews_spin.value(),
            "max_reviews": self.max_reviews_spin.value(),
            "require_phone": self.require_phone_cb.isChecked(),
            "require_website": self.website_combo.currentIndex() == 1,
            "require_no_website": self.website_combo.currentIndex() == 2,
            "require_email": self.require_email_cb.isChecked(),
            "claim_status": claim_status,
            "name_include": self.name_include_input.text(),
            "name_exclude": self.name_exclude_input.text(),
            "category_include": self.cat_include_input.text(),
            "category_exclude": self.cat_exclude_input.text(),
        }

    def is_headless(self) -> bool:
        return self.headless_cb.isChecked()

    def max_results(self) -> int:
        return self.max_results_slider.value()

    def validate(self):
        domains = self._get_domains()
        areas = self._get_areas()
        fields = self.get_checked_fields()
        export_dir = self.export_dir()

        if not domains:
            self._goto_scrape_tab()
            self.shake(self.domain_input)
            return None
        if not areas:
            self._goto_scrape_tab()
            self.shake(self.area_input)
            return None
        if not export_dir or not os.path.isdir(export_dir):
            self._goto_scrape_tab()
            self.shake(self.export_dir_input)
            return None
        if not fields:
            self._goto_scrape_tab()
            self._flash_fields_label()
            return None

        return domains, areas, fields, export_dir

    def _goto_scrape_tab(self):
        self.pages.setCurrentIndex(0)
        self.scrape_tab_btn.setChecked(True)

    def shake(self, widget):
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(200)
        pos = widget.pos()
        anim.setKeyValueAt(0, pos)
        anim.setKeyValueAt(0.2, pos + QPoint(-6, 0))
        anim.setKeyValueAt(0.4, pos + QPoint(6, 0))
        anim.setKeyValueAt(0.6, pos + QPoint(-4, 0))
        anim.setKeyValueAt(0.8, pos + QPoint(4, 0))
        anim.setKeyValueAt(1.0, pos)
        anim.start()
        self._anim = anim

    def _flash_fields_label(self):
        self.fields_section_label.setStyleSheet("color: #FF6B6B; font-size: 11px; font-weight: 500;")
        QTimer.singleShot(600, lambda: self.fields_section_label.setStyleSheet(""))

    def _on_start(self):
        validated = self.validate()
        if validated is None:
            return
        domains, areas, fields, export_dir = validated
        limit = self.max_results()
        primary_area = areas[0] if areas else ""
        self.settings = add_saved_search(self.settings, domains, primary_area, limit)
        self._refresh_saved_list()
        self._persist_settings()
        self.start_signal.emit(
            domains, areas, fields, self.is_headless(), limit, export_dir,
            self.get_filters(),
        )
