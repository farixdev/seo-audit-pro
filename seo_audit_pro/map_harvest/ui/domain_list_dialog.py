from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QPushButton,
)


class ListDialog(QDialog):
    """Generic popup for entering a list of items (one per line)."""

    def __init__(
        self,
        items: list | None = None,
        parent=None,
        title: str = "List",
        hint: str = "Enter one per line.",
        placeholder: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setFixedSize(400, 320)
        self._saved: list = list(items or [])
        self._hint = hint
        self._placeholder = placeholder
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        hint = QLabel(self._hint)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.text_edit = QTextEdit()
        if self._placeholder:
            self.text_edit.setPlaceholderText(self._placeholder)
        if self._saved:
            self.text_edit.setPlainText("\n".join(self._saved))
        layout.addWidget(self.text_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("outlined")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save")
        save_btn.setFixedHeight(36)
        save_btn.clicked.connect(self._save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _save(self):
        lines = self.text_edit.toPlainText().splitlines()
        self._saved = [line.strip() for line in lines if line.strip()]
        self.accept()

    def items(self) -> list:
        return list(self._saved)


class DomainListDialog(ListDialog):
    """Backward-compatible dialog for entering multiple search domains."""

    def __init__(self, domains: list | None = None, parent=None):
        super().__init__(
            items=domains,
            parent=parent,
            title="Domain List",
            hint="Enter one domain per line. These run in addition to the main input.",
            placeholder="restaurants\ncafes\ngyms\nhotels",
        )

    def domains(self) -> list:
        return self.items()
