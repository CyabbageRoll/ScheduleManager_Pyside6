"""
ui_widgets.py - 共通ウィジェット（日付選択・ユーザー選択・テーブル等）
"""
import datetime
from typing import Callable, List, Optional

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QLineEdit, QFrame, QSizePolicy, QCalendarWidget,
    QDialog, QDialogButtonBox,
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QFont

from db import COLOR_OPTIONS

# --- 定数 ---
STYLE_BUTTON = (
    "QPushButton { background: #ECEFF1; border: 1px solid #B0BEC5;"
    " border-radius: 4px; padding: 4px 10px; }"
    "QPushButton:hover { background: #CFD8DC; }"
    "QPushButton:pressed { background: #B0BEC5; }"
)
STYLE_COMBO = (
    "QComboBox { border: 1px solid #B0BEC5; border-radius: 4px;"
    " padding: 3px 6px; background: white; }"
)
STYLE_LABEL_INFO = "QLabel { color: #546E7A; font-size: 10px; }"


class DateButton(QPushButton):
    """
    クリックするとカレンダーポップアップを表示する日付選択ボタン。
    date_changed シグナルで変更後の日付文字列（YYYY-MM-DD）を通知する。
    """
    date_changed = Signal(str)

    def __init__(self, parent=None, initial_date: str = ""):
        super().__init__(parent)
        self._date = initial_date or datetime.date.today().isoformat()
        self._update_text()
        self.clicked.connect(self._open_calendar)
        self.setStyleSheet(STYLE_BUTTON)

    def get_date(self) -> str:
        return self._date

    def set_date(self, date_str: str) -> None:
        self._date = date_str
        self._update_text()

    def _update_text(self) -> None:
        self.setText(f"📅 {self._date}")

    def _open_calendar(self) -> None:
        dlg = _CalendarDialog(self._date, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._date = dlg.selected_date()
            self._update_text()
            self.date_changed.emit(self._date)


class _CalendarDialog(QDialog):
    def __init__(self, date_str: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日付を選択")
        layout = QVBoxLayout(self)
        self._cal = QCalendarWidget()
        try:
            qd = QDate.fromString(date_str, "yyyy-MM-dd")
            if qd.isValid():
                self._cal.setSelectedDate(qd)
        except Exception:
            pass
        self._cal.activated.connect(lambda _: self.accept())
        layout.addWidget(self._cal)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def selected_date(self) -> str:
        return self._cal.selectedDate().toString("yyyy-MM-dd")


class UserCombo(QComboBox):
    """ユーザー一覧コンボボックス"""
    user_changed = Signal(str)

    def __init__(self, members: List[str], parent=None):
        super().__init__(parent)
        self.addItems(members)
        self.currentTextChanged.connect(self.user_changed)
        self.setStyleSheet(STYLE_COMBO)

    def current_user(self) -> str:
        return self.currentText()

    def set_user(self, username: str) -> None:
        idx = self.findText(username)
        if idx >= 0:
            self.setCurrentIndex(idx)


class ColorCombo(QComboBox):
    """カラー選択コンボボックス"""

    def __init__(self, parent=None):
        super().__init__(parent)
        for name, hex_val in COLOR_OPTIONS.items():
            self.addItem(name)
            self.setItemData(
                self.count() - 1,
                QColor(hex_val),
                Qt.ItemDataRole.DecorationRole,
            )
        self.setStyleSheet(STYLE_COMBO)

    def current_color(self) -> str:
        return self.currentText()

    def set_color(self, color_name: str) -> None:
        idx = self.findText(color_name)
        if idx >= 0:
            self.setCurrentIndex(idx)


class ButtonRow(QWidget):
    """横並びボタン群ウィジェット"""

    def __init__(self, buttons: List[tuple], parent=None):
        """
        buttons: [("ラベル", callback), ...]
        """
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._btns = {}
        for label, cb in buttons:
            btn = QPushButton(label)
            btn.setStyleSheet(STYLE_BUTTON)
            if cb:
                btn.clicked.connect(cb)
            layout.addWidget(btn)
            self._btns[label] = btn
        layout.addStretch()

    def btn(self, label: str) -> Optional[QPushButton]:
        return self._btns.get(label)

    def set_enabled(self, label: str, enabled: bool) -> None:
        b = self._btns.get(label)
        if b:
            b.setEnabled(enabled)


class InfoLabel(QLabel):
    """ステータス表示用ラベル"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE_LABEL_INFO)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_info(self, msg: str) -> None:
        self.setText(msg)

    def set_error(self, msg: str) -> None:
        self.setText(f"⚠ {msg}")
        self.setStyleSheet("QLabel { color: #C62828; font-size: 10px; }")


class AutoCombo(QComboBox):
    """編集可能コンボボックス（候補リスト付き入力欄）"""

    def __init__(self, items: List[str] = None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        if items:
            self.addItems(items)
        self.setStyleSheet(STYLE_COMBO)

    def get_value(self) -> str:
        return self.currentText()

    def set_value(self, v: str) -> None:
        idx = self.findText(v)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(v)


class ScrollableTable(QTableWidget):
    """
    汎用スクロール可能テーブル。
    列名リストと表示幅リストを受け取って初期化する。
    """

    def __init__(self, columns: List[str], col_widths: List[int] = None,
                 parent=None):
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setStyleSheet(
            "QTableWidget { gridline-color: #E0E0E0; }"
            "QTableWidget::item:selected { background: #B3E5FC; color: black; }"
        )
        if col_widths:
            for i, w in enumerate(col_widths):
                if i < len(columns):
                    self.setColumnWidth(i, w)

    def set_rows(self, rows: List[List], row_ids: List[str] = None,
                 colors: List[str] = None) -> None:
        """
        rows: [[val, val, ...], ...]
        row_ids: 各行に関連付ける IDX（UserRole で保持）
        colors: 各行の背景色 hex 文字列（None 行はデフォルト）
        """
        self.setRowCount(0)
        for r_idx, row in enumerate(rows):
            self.insertRow(r_idx)
            for c_idx, val in enumerate(row):
                item = QTableWidgetItem(str(val) if val is not None else "")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if row_ids and r_idx < len(row_ids):
                    item.setData(Qt.ItemDataRole.UserRole, row_ids[r_idx])
                if colors and r_idx < len(colors) and colors[r_idx]:
                    bg = QColor(colors[r_idx])
                    bg.setAlpha(80)
                    item.setBackground(bg)
                self.setItem(r_idx, c_idx, item)

    def selected_id(self) -> Optional[str]:
        """選択行の UserRole データ（IDX）を返す"""
        items = self.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.ItemDataRole.UserRole)

    def selected_ids(self) -> List[str]:
        """選択行の IDX リストを返す（重複なし）"""
        seen = set()
        result = []
        for item in self.selectedItems():
            uid = item.data(Qt.ItemDataRole.UserRole)
            if uid and uid not in seen:
                seen.add(uid)
                result.append(uid)
        return result


class Separator(QFrame):
    """水平区切り線"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
