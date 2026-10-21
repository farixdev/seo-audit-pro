# -*- coding: utf-8 -*-
"""
Data table: QTableView + QAbstractTableModel with server-side sorting and
pagination, painted status badges, score cells, two-line cells, and a
selection checkbox column for bulk actions.
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHeaderView, QSpinBox, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableView, QWidget)

from seo_audit_pro import icons
from seo_audit_pro.core import fmt
from seo_audit_pro.theme import C, SEVERITY, STATUS, score_color
from seo_audit_pro.widgets.common import button, hbox, label, paint_pill, vbox

ROW_ROLE = Qt.ItemDataRole.UserRole + 1


class Column:
    def __init__(self, key, title, kind="text", width=None, sort=None, stretch=False, render=None, secondary=None,
                 align="left", empty="Not audited", optional=False):
        self.key = key
        self.optional = optional  # hidden first when the table is too narrow
        self.empty = empty        # text shown for an empty score cell
        self.title = title
        self.kind = kind          # text | strong | status | severity | score | date | datetime | relative | number | check
        self.width = width
        self.sort = sort          # server sort key, or None if not sortable
        self.stretch = stretch
        self.render = render      # fn(row) -> str
        self.secondary = secondary  # fn(row) -> str (second line, muted)
        self.align = align


class RowModel(QAbstractTableModel):
    def __init__(self, columns, id_key="id"):
        super().__init__()
        self.columns = columns
        self.rows = []
        self.id_key = id_key
        self.checked = set()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        col = self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if col.kind in ("check", "actions") else col.title
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int((Qt.AlignmentFlag.AlignRight if col.align == "right" else Qt.AlignmentFlag.AlignLeft)
                       | Qt.AlignmentFlag.AlignVCenter)
        return None

    def text_for(self, row, col):
        if col.render:
            return col.render(row)
        val = row.get(col.key)
        if col.kind == "date":
            return fmt.date(val)
        if col.kind == "datetime":
            return fmt.datetime_(val)
        if col.kind == "relative":
            return fmt.relative(val)
        if col.kind == "number":
            return fmt.number(val)
        return "—" if val in (None, "") else str(val)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = self.columns[index.column()]
        if role == ROW_ROLE:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if col.kind in ("check", "actions") else self.text_for(row, col)
        if role == Qt.ItemDataRole.ToolTipRole and col.kind in ("text", "strong"):
            t = self.text_for(row, col)
            return t if len(t) > 28 else None
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return self.text_for(row, col)
        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        ids = {r.get(self.id_key) for r in self.rows}
        self.checked &= ids
        self.endResetModel()

    def toggle(self, row_index):
        rid = self.rows[row_index].get(self.id_key)
        if rid in self.checked:
            self.checked.discard(rid)
        else:
            self.checked.add(rid)
        top = self.index(row_index, 0)
        self.dataChanged.emit(top, top)

    def toggle_all(self):
        ids = {r.get(self.id_key) for r in self.rows}
        if ids and ids <= self.checked:
            self.checked -= ids
        else:
            self.checked |= ids
        self.layoutChanged.emit()


class CellDelegate(QStyledItemDelegate):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), 44)

    def paint(self, painter, option, index):
        col = self.model.columns[index.column()]
        row = index.data(ROW_ROLE)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # Background (hover / selection) via style, without the default text.
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        r = option.rect.adjusted(12, 0, -12, 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if col.kind == "check":
            checked = row.get(self.model.id_key) in self.model.checked
            box = QRectF(r.x(), r.center().y() - 8, 16, 16)
            path = QPainterPath()
            path.addRoundedRect(box, 4, 4)
            if checked:
                painter.fillPath(path, QColor(C["ink"]))
                painter.drawPixmap(box.toRect().adjusted(2, 2, -2, -2), icons.pixmap("check", "#FFFFFF", 12, 3))
            else:
                painter.setPen(QPen(QColor(C["border_strong"]), 1))
                painter.drawPath(path)
        elif col.kind == "actions":
            size = 16
            painter.drawPixmap(int(r.center().x() - size / 2), int(r.center().y() - size / 2),
                               icons.pixmap("more", C["text_2"], size))
        elif col.kind in ("status", "severity"):
            key = (row.get(col.key) or "").lower() if not col.render else col.render(row)
            table = STATUS if col.kind == "status" else SEVERITY
            fg, bg, text = table.get(key, (C["text_2"], C["neutral_bg"], (key or "—").title()))
            if key:
                paint_pill(painter, r, text, fg, bg, option.font)
            else:
                self._text(painter, r, "—", C["muted"], option.font)
        elif col.kind == "score":
            val = row.get(col.key)
            if val is None:
                self._text(painter, r, col.empty, C["muted"], option.font)
            else:
                f = QFont(option.font)
                f.setWeight(QFont.Weight.DemiBold)
                painter.setFont(f)
                painter.setPen(QColor(score_color(val)))
                painter.drawText(r, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), str(val))
                fm = QFontMetrics(f)
                bar_x = r.x() + fm.horizontalAdvance("100") + 10
                bar = QRectF(bar_x, r.center().y() - 2, max(0, min(56, r.right() - bar_x)), 4)
                if bar.width() > 12:
                    track = QPainterPath()
                    track.addRoundedRect(bar, 2, 2)
                    painter.fillPath(track, QColor("#EDEDF0"))
                    fill = QPainterPath()
                    fill.addRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * max(0, min(100, val)) / 100, 4), 2, 2)
                    painter.fillPath(fill, QColor(score_color(val)))
        else:
            text = self.model.text_for(row, col)
            secondary = col.secondary(row) if col.secondary else None
            strong = col.kind == "strong"
            if secondary:
                top = QRect(r.x(), r.y() + 5, r.width(), r.height() // 2 - 3)
                bottom = QRect(r.x(), r.center().y() + 1, r.width(), r.height() // 2 - 5)
                self._text(painter, top, text, C["text"], option.font, strong, Qt.AlignmentFlag.AlignBottom)
                self._text(painter, bottom, secondary, C["text_3"], option.font, False, Qt.AlignmentFlag.AlignTop, 12)
            else:
                color = C["muted"] if text == "—" else C["text"]
                self._text(painter, r, text, color, option.font, strong,
                           align_h=Qt.AlignmentFlag.AlignRight if col.align == "right" else Qt.AlignmentFlag.AlignLeft)
        painter.restore()

    @staticmethod
    def _text(painter, rect, text, color, font, bold=False, align_v=Qt.AlignmentFlag.AlignVCenter, px=None,
              align_h=Qt.AlignmentFlag.AlignLeft):
        f = QFont(font)
        if bold:
            f.setWeight(QFont.Weight.DemiBold)
        if px:
            f.setPixelSize(px)
        painter.setFont(f)
        painter.setPen(QColor(color))
        fm = QFontMetrics(f)
        painter.drawText(rect, int(align_v | align_h), fm.elidedText(str(text), Qt.TextElideMode.ElideRight, rect.width()))


class DataTable(QWidget):
    sort_changed = Signal(str, str)          # key, "asc"|"desc"
    page_changed = Signal(int)
    page_size_changed = Signal(int)
    activated = Signal(dict)
    selection_changed = Signal(list)
    context_requested = Signal(dict, object)  # row, global QPoint

    def __init__(self, columns, id_key="id", paginated=True, page_sizes=(25, 50, 100), parent=None):
        super().__init__(parent)
        self.columns = columns
        self.model = RowModel(columns, id_key)
        self.sort_key, self.sort_order = None, "desc"
        self.page, self.pages = 1, 1

        lay = vbox(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(CellDelegate(self.model, self.view))
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(False)
        self.view.setWordWrap(False)
        self.view.setMouseTracking(True)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(44)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context)
        self.view.clicked.connect(self._clicked)
        self.view.doubleClicked.connect(self._double)
        self.view.activated.connect(self._double)
        hdr = self.view.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setSectionsClickable(True)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hdr.sectionClicked.connect(self._header_clicked)
        hdr.setMinimumSectionSize(40)
        for i, col in enumerate(columns):
            if col.stretch:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            else:
                hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.view.setColumnWidth(i, self._base_width(col))
        lay.addWidget(self.view, 1)

        self.footer = QWidget()
        foot = hbox(self.footer, (12, 8, 12, 8), 8)
        self.range_label = label("", "Meta")
        foot.addWidget(self.range_label)
        foot.addStretch(1)
        foot.addWidget(label("Rows per page", "Meta"))
        self.size_combo = QComboBox()
        for s in page_sizes:
            self.size_combo.addItem(str(s), s)
        self.size_combo.setFixedWidth(72)
        self.size_combo.currentIndexChanged.connect(lambda _i: self.page_size_changed.emit(self.size_combo.currentData()))
        foot.addWidget(self.size_combo)
        self.prev_btn = button("", icon_name="chevron_left", tooltip="Previous page")
        self.next_btn = button("", icon_name="chevron_right", tooltip="Next page")
        for b in (self.prev_btn, self.next_btn):
            b.setFixedWidth(34)
        self.page_label = label("", "Meta")
        self.page_box = QSpinBox()
        self.page_box.setMinimum(1)
        self.page_box.setFixedWidth(84)
        self.page_box.setToolTip("Go to page")
        self.page_box.setKeyboardTracking(False)
        self.page_box.valueChanged.connect(lambda v: v != self.page and self.page_changed.emit(v))
        self.prev_btn.clicked.connect(lambda: self.page_changed.emit(max(1, self.page - 1)))
        self.next_btn.clicked.connect(lambda: self.page_changed.emit(min(self.pages, self.page + 1)))
        foot.addWidget(self.prev_btn)
        foot.addWidget(label("Page", "Meta"))
        foot.addWidget(self.page_box)
        foot.addWidget(self.page_label)
        foot.addWidget(self.next_btn)
        self.footer.setObjectName("TableFooter")
        self.footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.footer.setStyleSheet(f"#TableFooter {{ background: {C['surface']}; border-top: 1px solid {C['border']}; }}")
        self.footer.setVisible(paginated)
        lay.addWidget(self.footer)

    @staticmethod
    def _base_width(col):
        return col.width or (40 if col.kind in ("check", "actions") else 140)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_columns()
        QTimer.singleShot(0, self._fit_columns)  # child geometry settles after hidden-page layouts

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._fit_columns)

    def _fit_columns(self):
        """Hide optional columns (right to left) until the rest fit without
        horizontal scrolling; show them again when there is room."""
        # The table's own width; the viewport may not be resized yet while hidden.
        available = self.width() - 16
        stretch_min = 180
        needed = sum(self._base_width(c) if not c.stretch else stretch_min for c in self.columns)
        for i in reversed(range(len(self.columns))):
            col = self.columns[i]
            if not col.optional:
                continue
            hide = needed > available
            self.view.setColumnHidden(i, hide)
            if hide:
                needed -= self._base_width(col)

    # -- data --------------------------------------------------------------
    def set_page(self, data):
        """Accepts the server's paged payload {items,total,page,page_size,pages}."""
        self.model.set_rows(data.get("items") or [])
        self.page, self.pages = data.get("page", 1), data.get("pages", 1)
        total = data.get("total", 0)
        size = data.get("page_size", 25)
        start = 0 if not total else (self.page - 1) * size + 1
        end = min(total, self.page * size)
        self.range_label.setText(f"{start}–{end} of {fmt.number(total)}" if total else "No results")
        self.page_box.blockSignals(True)
        self.page_box.setMaximum(max(1, self.pages))
        self.page_box.setValue(self.page)
        self.page_box.blockSignals(False)
        self.page_label.setText(f"of {fmt.number(self.pages)}")
        self.prev_btn.setEnabled(self.page > 1)
        self.next_btn.setEnabled(self.page < self.pages)
        self.selection_changed.emit(self.selected_ids())

    def set_rows(self, rows):
        self.model.set_rows(rows)
        self.selection_changed.emit(self.selected_ids())

    def selected_ids(self):
        return [r.get(self.model.id_key) for r in self.model.rows if r.get(self.model.id_key) in self.model.checked]

    def selected_rows(self):
        return [r for r in self.model.rows if r.get(self.model.id_key) in self.model.checked]

    def clear_selection(self):
        self.model.checked.clear()
        self.model.layoutChanged.emit()
        self.selection_changed.emit([])

    def current_row(self):
        idx = self.view.currentIndex()
        return idx.data(ROW_ROLE) if idx.isValid() else None

    def set_sort_indicator(self, key, order):
        self.sort_key, self.sort_order = key, order
        for i, c in enumerate(self.columns):
            if c.sort == key:
                self.view.horizontalHeader().setSortIndicatorShown(True)
                self.view.horizontalHeader().setSortIndicator(
                    i, Qt.SortOrder.AscendingOrder if order == "asc" else Qt.SortOrder.DescendingOrder)
                return

    # -- events ------------------------------------------------------------
    def _header_clicked(self, section):
        col = self.columns[section]
        if col.kind == "check":
            self.model.toggle_all()
            self.selection_changed.emit(self.selected_ids())
            return
        if not col.sort:
            self.set_sort_indicator(self.sort_key, self.sort_order)
            return
        order = "asc" if (self.sort_key == col.sort and self.sort_order == "desc") else "desc"
        self.set_sort_indicator(col.sort, order)
        self.sort_changed.emit(col.sort, order)

    def _clicked(self, index):
        kind = self.columns[index.column()].kind
        if kind == "check":
            self.model.toggle(index.row())
            self.selection_changed.emit(self.selected_ids())
        elif kind == "actions":
            rect = self.view.visualRect(index)
            self.context_requested.emit(index.data(ROW_ROLE), self.view.viewport().mapToGlobal(rect.bottomLeft()))

    def _double(self, index):
        if index.isValid() and self.columns[index.column()].kind not in ("check", "actions"):
            self.activated.emit(index.data(ROW_ROLE))

    def _context(self, pos):
        idx = self.view.indexAt(pos)
        if idx.isValid():
            self.view.setCurrentIndex(idx)
            self.context_requested.emit(idx.data(ROW_ROLE), self.view.viewport().mapToGlobal(pos))
