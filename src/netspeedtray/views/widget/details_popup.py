from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class DetailRow:
    """One label/value row in the module detail popup."""

    label: str
    value: str
    accent: Optional[str] = None
    is_heading: bool = False


class ModuleDetailPopup(QWidget):
    """Small always-on-top popup used for widget module details."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._accent = "#18E8FF"

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._panel = QFrame(self)
        self._panel.setObjectName("detailPanel")
        root.addWidget(self._panel)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 8, 10, 9)
        panel_layout.setSpacing(6)

        self._title = QLabel(self._panel)
        self._title.setObjectName("detailTitle")
        self._title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        panel_layout.addWidget(self._title)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(3)
        panel_layout.addLayout(self._grid)

        self._apply_style()

    def set_content(self, title: str, rows: Iterable[DetailRow], accent: str) -> None:
        self._accent = accent if QColor(accent).isValid() else "#18E8FF"
        self._title.setText(title)

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        row_index = 0
        for row in rows:
            if row.is_heading:
                heading = QLabel(row.label, self._panel)
                heading.setObjectName("detailHeading")
                heading.setProperty("accentColor", row.accent or self._accent)
                self._grid.addWidget(heading, row_index, 0, 1, 2)
                row_index += 1
                continue

            label = QLabel(row.label, self._panel)
            label.setObjectName("detailLabel")
            value = QLabel(row.value, self._panel)
            value.setObjectName("detailValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            if row.accent:
                value.setStyleSheet(f"color: {row.accent};")

            self._grid.addWidget(label, row_index, 0)
            self._grid.addWidget(value, row_index, 1)
            row_index += 1

        self._apply_style()
        self.adjustSize()

    def show_at(self, anchor: QPoint) -> None:
        self.show_for_rect(QRect(anchor, anchor))

    def show_for_rect(self, anchor_rect: QRect) -> None:
        self.adjustSize()

        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        if screen is None:
            self.move(anchor_rect.topLeft())
            self.show()
            self.raise_()
            return

        geom = screen.availableGeometry()
        margin = 8
        width = self.width()
        height = self.height()

        x = anchor_rect.center().x() - width // 2
        x = max(geom.left() + margin, min(x, geom.right() - width - margin))

        y = anchor_rect.top() - height - 8
        if y < geom.top() + margin:
            y = anchor_rect.bottom() + 8
        y = max(geom.top() + margin, min(y, geom.bottom() - height - margin))

        self.move(x, y)
        self.show()
        self.raise_()

    def _apply_style(self) -> None:
        accent = self._accent
        self._panel.setStyleSheet(
            f"""
            QFrame#detailPanel {{
                background-color: rgba(6, 10, 16, 232);
                border: 1px solid {accent};
            }}
            QLabel#detailTitle {{
                color: #F7FBFF;
                font-weight: 700;
                font-size: 12px;
                padding-bottom: 2px;
                border-bottom: 1px solid rgba(255, 255, 255, 34);
            }}
            QLabel#detailHeading {{
                color: {accent};
                font-weight: 700;
                font-size: 10px;
                padding-top: 4px;
            }}
            QLabel#detailLabel {{
                color: rgba(210, 224, 238, 190);
                font-size: 10px;
            }}
            QLabel#detailValue {{
                color: #FFFFFF;
                font-weight: 650;
                font-size: 10px;
            }}
            """
        )
