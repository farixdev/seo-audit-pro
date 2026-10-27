# -*- coding: utf-8 -*-
"""
Design tokens and the application stylesheet.

Palette: Charcoal Grey (#2A2A2A) and Moonlit Silver (#C0C0C0) with the greys
between them. Colour is reserved for status, severity and scores.
"""

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI Variable Text"
FONT_FALLBACK = "Segoe UI"
MONO_FAMILY = "Cascadia Mono"

SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40, 12: 48}

C = {
    "canvas": "#F6F6F6",
    "surface": "#FFFFFF",
    "surface_alt": "#FAFAFA",
    "sidebar": "#EFEFEF",
    "sidebar_hover": "#E3E3E3",
    "sidebar_active": "#FFFFFF",
    "border": "#E0E0E0",
    "border_strong": "#C0C0C0",
    "divider": "#EBEBEB",
    "text": "#2A2A2A",
    "text_2": "#52525B",
    "text_3": "#71717A",
    "muted": "#A1A1AA",
    "ink": "#2A2A2A",
    "ink_hover": "#3D3D3D",
    "accent": "#2A2A2A",
    "accent_soft": "#ECECEC",
    "focus": "#7A7A7A",
    "selection": "#E6E6E6",
    "success": "#067647", "success_bg": "#ECFDF3",
    "warning": "#B54708", "warning_bg": "#FFFAEB",
    "danger": "#B42318", "danger_bg": "#FEF3F2",
    "info": "#175CD3", "info_bg": "#EFF8FF",
    "neutral": "#475467", "neutral_bg": "#F2F4F7",
}

SEVERITY = {
    "critical": ("#B42318", "#FEF3F2", "Critical"),
    "high": ("#C4320A", "#FFF4ED", "High"),
    "medium": ("#B54708", "#FFFAEB", "Medium"),
    "low": ("#175CD3", "#EFF8FF", "Low"),
    "info": ("#475467", "#F2F4F7", "Info"),
}

# status key -> (fg, bg, label)
STATUS = {
    # leads
    "new": ("#475467", "#F2F4F7", "New"),
    "assigned": ("#175CD3", "#EFF8FF", "Assigned"),
    "audited": ("#5925DC", "#F4F3FF", "Audited"),
    "contacted": ("#B54708", "#FFFAEB", "Contacted"),
    "qualified": ("#026AA2", "#F0F9FF", "Qualified"),
    "won": ("#067647", "#ECFDF3", "Won"),
    "lost": ("#B42318", "#FEF3F2", "Lost"),
    "archived": ("#667085", "#F2F4F7", "Archived"),
    # audits
    "queued": ("#475467", "#F2F4F7", "Queued"),
    "running": ("#175CD3", "#EFF8FF", "Running"),
    "completed": ("#067647", "#ECFDF3", "Completed"),
    "failed": ("#B42318", "#FEF3F2", "Failed"),
    "cancelled": ("#667085", "#F2F4F7", "Cancelled"),
    # emails
    "draft": ("#475467", "#F2F4F7", "Draft"),
    "sending": ("#175CD3", "#EFF8FF", "Sending"),
    "sent": ("#067647", "#ECFDF3", "Sent"),
    # issues
    "open": ("#B54708", "#FFFAEB", "Open"),
    "resolved": ("#067647", "#ECFDF3", "Resolved"),
    # check results
    "pass": ("#067647", "#ECFDF3", "Pass"),
    "warn": ("#B54708", "#FFFAEB", "Warning"),
    "fail": ("#B42318", "#FEF3F2", "Fail"),
    "na": ("#667085", "#F2F4F7", "Not applicable"),
    # people
    "active": ("#067647", "#ECFDF3", "Active"),
    "inactive": ("#667085", "#F2F4F7", "Inactive"),
}

ROLE_LABEL = {"MANAGER": "Manager", "TEAM_LEAD": "Team Lead", "AGENT": "Agent"}


def score_color(score):
    if score is None:
        return C["muted"]
    if score >= 80:
        return "#067647"
    if score >= 60:
        return "#B54708"
    return "#B42318"


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------
def stylesheet(asset_dir):
    c = C
    asset_dir = asset_dir.replace("\\", "/")
    return f"""
* {{ font-family: "{FONT_FAMILY}", "{FONT_FALLBACK}"; font-size: 13px; color: {c['text']}; }}
QMainWindow, QDialog, #Canvas {{ background: {c['canvas']}; }}
QWidget#Surface, QFrame#Surface {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QFrame#Divider {{ background: {c['divider']}; max-height: 1px; min-height: 1px; border: none; }}
QToolTip {{ background: {c['ink']}; color: #FFFFFF; border: none; padding: 5px 8px; border-radius: 4px; font-size: 12px; }}

/* Typography roles */
QLabel#PageTitle {{ font-size: 20px; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {c['text_3']}; font-size: 13px; }}
QLabel#SectionTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#Meta {{ color: {c['text_3']}; font-size: 12px; }}
QLabel#Muted {{ color: {c['text_3']}; }}
QLabel#FieldLabel {{ color: {c['text_2']}; font-size: 12px; font-weight: 600; }}
QLabel#FieldError {{ color: {c['danger']}; font-size: 12px; }}
QLabel#MetricValue {{ font-size: 26px; font-weight: 600; }}
QLabel#MetricLabel {{ color: {c['text_3']}; font-size: 12px; }}
QLabel#Mono {{ font-family: "{MONO_FAMILY}", Consolas; font-size: 12px; color: {c['text_2']}; }}
QLabel#Link {{ color: {c['accent']}; }}

/* Buttons */
QPushButton {{
    background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 6px;
    padding: 0 12px; min-height: 32px; font-weight: 600; color: {c['text']};
}}
QPushButton:hover {{ background: {c['surface_alt']}; border-color: #C4C4CA; }}
QPushButton:pressed {{ background: #F0F0F2; }}
QPushButton:focus {{ border-color: {c['focus']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['surface_alt']}; border-color: {c['border']}; }}
QPushButton[variant="primary"] {{ background: {c['ink']}; border-color: {c['ink']}; color: #FFFFFF; }}
QPushButton[variant="primary"]:hover {{ background: {c['ink_hover']}; border-color: {c['ink_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background: #A1A1AA; border-color: #A1A1AA; color: #F4F4F5; }}
QPushButton[variant="danger"] {{ background: {c['surface']}; border-color: #FDA29B; color: {c['danger']}; }}
QPushButton[variant="danger"]:hover {{ background: {c['danger_bg']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {c['text_2']}; }}
QPushButton[variant="ghost"]:hover {{ background: #EFEFF1; color: {c['text']}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; color: {c['accent']}; padding: 0; min-height: 20px; font-weight: 600; text-decoration: underline; }}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}
QPushButton[size="lg"] {{ min-height: 40px; font-size: 14px; }}
QPushButton[size="sm"] {{ min-height: 26px; padding: 0 8px; font-size: 12px; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: #EBEBEE; }}
QToolButton:pressed {{ background: #E2E2E6; }}

/* Inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDateEdit, QComboBox {{
    background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 6px;
    padding: 0 10px; min-height: 32px; selection-background-color: {c['accent_soft']}; selection-color: {c['text']};
}}
QPlainTextEdit, QTextEdit {{ padding: 8px 10px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {c['focus']}; }}
QLineEdit[invalid="true"], QPlainTextEdit[invalid="true"], QComboBox[invalid="true"] {{ border: 1px solid {c['danger']}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {c['surface_alt']}; color: {c['text_3']}; }}
QLineEdit#AuthInput {{ min-height: 40px; font-size: 14px; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url({asset_dir}/chevron-down.svg); width: 12px; height: 12px; }}
QSpinBox {{ padding: 0 22px 0 10px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 20px; border: none; background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url({asset_dir}/chevron-up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({asset_dir}/chevron-down.svg); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; padding: 4px; outline: none;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['border_strong']}; border-radius: 4px; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['ink']}; border-color: {c['ink']}; image: url({asset_dir}/check-white.svg); }}

/* Tables */
QTableView {{ background: {c['surface']}; border: none; gridline-color: transparent;
    selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; }}
QTableView::item {{ padding: 0 12px; border-bottom: 1px solid {c['divider']}; }}
QTableView::item:hover {{ background: #F7F8FA; }}
QTableView::item:selected {{ background: {c['selection']}; }}
QHeaderView {{ background: {c['surface']}; border: none; }}
QHeaderView::section {{ background: {c['surface_alt']}; color: {c['text_3']}; font-size: 12px; font-weight: 600;
    border: none; border-bottom: 1px solid {c['border']}; padding: 0 12px; min-height: 34px; }}
QHeaderView::section:hover {{ color: {c['text']}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 6px; padding: 0; }}
QListWidget::item:selected {{ background: {c['selection']}; }}
QListWidget::item:hover:!selected {{ background: #F4F4F6; }}

/* Tabs */
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; border: none; border-bottom: 2px solid transparent; padding: 8px 2px;
    margin-right: 20px; color: {c['text_3']}; font-weight: 600; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['ink']}; }}
QTabBar::tab:hover:!selected {{ color: {c['text']}; }}

/* Scrollbars */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D4D4D8; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: #A1A1AA; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #D4D4D8; border-radius: 3px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Progress */
QProgressBar {{ background: #EDEDF0; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {c['ink']}; border-radius: 3px; }}

/* Menus */
QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: #F2F2F4; }}
QMenu::item:disabled {{ color: {c['muted']}; }}
QMenu::separator {{ height: 1px; background: {c['divider']}; margin: 4px 6px; }}

/* Sidebar */
#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
#SidebarSection {{ color: {c['text_3']}; font-size: 11px; font-weight: 600; padding-left: 12px; }}
#TopBar {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
"""
