# -*- coding: utf-8 -*-
"""Light / White QSS stylesheet for Map Harvest, matching SEO Audit Pro."""

QSS = """
QWidget {
    background-color: #FFFFFF;
    color: #18181B;
    font-family: 'DM Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #FFFFFF;
}

QLineEdit, QTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 8px;
    padding: 8px 12px;
    color: #18181B;
    font-size: 13px;
    selection-background-color: #EEF2FE;
    selection-color: #18181B;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid #2563EB;
    outline: none;
}

QLineEdit::placeholder, QTextEdit::placeholder {
    color: #A1A1AA;
}

QPushButton {
    background-color: #F4F4F5;
    color: #18181B;
    border: 1px solid #E4E4E7;
    border-radius: 8px;
    padding: 0 16px;
    height: 36px;
    font-size: 13px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #E4E4E7;
}

QPushButton:disabled {
    background-color: #F4F4F5;
    color: #A1A1AA;
    border-color: #E4E4E7;
}

QPushButton#outlined {
    background-color: #FFFFFF;
    color: #18181B;
    border: 1px solid #D4D4D8;
}

QPushButton#outlined:hover {
    background-color: #F4F4F5;
    border-color: #A1A1AA;
}

QPushButton#outlined:disabled {
    color: #A1A1AA;
    border-color: #E4E4E7;
}

QPushButton#danger {
    background-color: transparent;
    color: #DC2626;
    border: 1px solid rgba(220, 38, 38, 0.35);
}

QPushButton#danger:hover {
    background-color: rgba(220, 38, 38, 0.08);
}

QPushButton#lead_bank_btn {
    background-color: #2563EB;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 0 16px;
    height: 30px;
    font-size: 13px;
    font-weight: 600;
}

QPushButton#lead_bank_btn:hover {
    background-color: #1D4ED8;
}

QPushButton#lead_bank_btn:disabled {
    background-color: #E4E4E7;
    color: #A1A1AA;
}

QCheckBox {
    spacing: 8px;
    color: #18181B;
    font-size: 13px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #D4D4D8;
    border-radius: 4px;
    background: #FFFFFF;
}

QCheckBox::indicator:checked {
    background-color: #2563EB;
    border-color: #2563EB;
}

QCheckBox::indicator:hover {
    border-color: #2563EB;
}

QTableWidget {
    background-color: #FFFFFF;
    alternate-background-color: #F9FAFB;
    border: 1px solid #E4E4E7;
    border-radius: 10px;
    gridline-color: #F1F1F3;
    font-size: 12px;
    color: #18181B;
}

QTableWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid #F1F1F3;
}

QTableWidget::item:selected {
    background-color: #EEF2FE;
    color: #18181B;
}

QHeaderView::section {
    background-color: #F4F4F6;
    color: #52525B;
    font-size: 11px;
    font-weight: 600;
    padding: 8px 10px;
    height: 34px;
    border: none;
    border-bottom: 1px solid #E4E4E7;
    border-right: 1px solid #E4E4E7;
}

QScrollBar:vertical {
    background: #FFFFFF;
    width: 10px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #D4D4D8;
    border-radius: 5px;
    min-height: 24px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    height: 10px;
    background: #FFFFFF;
}

QScrollBar::handle:horizontal {
    background: #D4D4D8;
    border-radius: 5px;
    min-width: 24px;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QListWidget {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 8px;
    padding: 6px 4px;
    font-size: 12px;
    color: #27272A;
}

QListWidget::item {
    padding: 4px 8px;
    border: none;
    background: transparent;
}

QProgressBar {
    background-color: #E4E4E7;
    border: none;
    border-radius: 2px;
    height: 4px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: #2563EB;
    border-radius: 2px;
}

QLabel#section_label {
    color: #52525B;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QPushButton#tab {
    background-color: transparent;
    color: #71717A;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    height: 28px;
    font-size: 12px;
    font-weight: 500;
}

QPushButton#tab:hover {
    color: #18181B;
    background-color: #F4F4F5;
}

QPushButton#tab:checked {
    color: #18181B;
    background-color: #E4E4E7;
    font-weight: 600;
}

QLabel#hint {
    color: #71717A;
    font-size: 12px;
    line-height: 1.4;
}

QLabel#app_name {
    color: #18181B;
    font-size: 15px;
    font-weight: 700;
}

QLabel#count_label {
    color: #18181B;
    font-size: 15px;
    font-weight: 600;
}

QLabel#count_sub {
    color: #71717A;
    font-size: 13px;
    font-weight: 400;
    padding-top: 1px;
}

QLabel#muted {
    color: #71717A;
    font-size: 12px;
}

QLabel#status_text {
    color: #52525B;
    font-size: 12px;
    font-weight: 400;
}

QTableWidget#results_table {
    border-radius: 8px;
}

QWidget#results_header {
    background-color: transparent;
    border-bottom: 1px solid #E4E4E7;
}

QFrame#card {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 12px;
}

QScrollArea {
    border: none;
    background: #FFFFFF;
}

QScrollArea > QWidget > QWidget {
    background: #FFFFFF;
}

QDialog {
    background-color: #FFFFFF;
    color: #18181B;
}

QSlider::groove:horizontal {
    border: none;
    height: 4px;
    background: #E4E4E7;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #2563EB;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #1D4ED8;
}

QSlider::sub-page:horizontal {
    background: #2563EB;
    border-radius: 2px;
}

QSpinBox#spin {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 6px;
    padding: 4px 8px;
    color: #18181B;
}

QSpinBox#spin::up-button, QSpinBox#spin::down-button {
    width: 16px;
    border: none;
    background: transparent;
}

QListWidget#saved_list {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 8px;
    padding: 4px;
    font-size: 12px;
    color: #27272A;
}

QListWidget#saved_list::item {
    padding: 6px 8px;
    border-radius: 4px;
}

QListWidget#saved_list::item:selected {
    background-color: #EEF2FE;
    color: #18181B;
}

QListWidget#saved_list::item:hover {
    background-color: #F4F4F5;
}

QLabel#toast {
    background-color: #18181B;
    border: 1px solid #27272A;
    border-radius: 8px;
    color: #FFFFFF;
    font-size: 12px;
    padding: 12px 14px;
}

QPushButton#start_btn {
    background-color: #16A34A;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 600;
    padding: 0 20px;
}

QPushButton#start_btn:hover {
    background-color: #15803D;
}

QPushButton#start_btn:pressed {
    background-color: #166534;
}

QPushButton#start_btn:disabled {
    background-color: #E4E4E7;
    color: #A1A1AA;
}

QLineEdit#search_box {
    padding: 4px 10px;
    font-size: 12px;
    border-radius: 6px;
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    color: #18181B;
}

QComboBox {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 6px;
    padding: 5px 10px;
    color: #18181B;
}

QComboBox:hover { border-color: #2563EB; }
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #18181B;
    selection-background-color: #EEF2FE;
    selection-color: #18181B;
    border: 1px solid #E4E4E7;
    outline: none;
}

QDoubleSpinBox#spin {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 6px;
    padding: 4px 8px;
    color: #18181B;
}

QMenu {
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 8px;
    padding: 4px;
    color: #18181B;
}

QMenu::item {
    padding: 6px 18px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #F4F4F5;
}

QMenu::separator {
    height: 1px;
    background: #E4E4E7;
    margin: 4px 6px;
}
"""
