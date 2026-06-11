"""
ui_widgets.py - 共通ウィジェット（日付選択・ユーザー選択・テーブル等）
"""
import datetime
from typing import Callable, List, Optional

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QLineEdit, QFrame, QSizePolicy, QCalendarWidget,
    QDialog, QDialogButtonBox, QMessageBox, QApplication,
)
from PySide6.QtCore import Qt, QDate, Signal, QTimer
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
    カレンダーのダブルクリックでも即座に確定される。

    allow_empty=True の場合は「未設定」状態をサポートする。
    anchor_date_func を指定すると、日付未設定でカレンダーを開く際に
    その戻り値（YYYY-MM-DD）の月を初期表示月として使用する。
    """
    date_changed = Signal(str)

    def __init__(self, parent=None, initial_date: str = "",
                 allow_empty: bool = False,
                 anchor_date_func: Optional[Callable[[], str]] = None):
        super().__init__(parent)
        self._allow_empty = allow_empty
        self._anchor_date_func = anchor_date_func
        # allow_empty=False の場合は空文字を今日に補完（従来動作）
        if allow_empty:
            self._date = initial_date
        else:
            self._date = initial_date or datetime.date.today().isoformat()
        self._update_text()
        self.clicked.connect(self._open_calendar)
        self.setStyleSheet(STYLE_BUTTON)

    def get_date(self) -> str:
        """選択中の日付文字列（未設定の場合は空文字）を返す"""
        return self._date

    def set_date(self, date_str: str) -> None:
        self._date = date_str
        self._update_text()

    def clear_date(self) -> None:
        """日付をクリアして未設定状態にする（allow_empty=True の場合のみ有効）"""
        if self._allow_empty:
            self._date = ""
            self._update_text()
            self.date_changed.emit("")

    def _update_text(self) -> None:
        if self._date:
            self.setText(f"📅 {self._date}")
        else:
            self.setText("📅 未設定")

    def _open_calendar(self) -> None:
        """カレンダーダイアログを開き、選択された日付でボタン表示を更新してシグナルを送出する"""
        # 表示開始月: 現在の日付 > アンカー関数の戻り値 > 今日
        display_date = self._date
        if not display_date and self._anchor_date_func:
            display_date = self._anchor_date_func()
        dlg = _CalendarDialog(display_date or datetime.date.today().isoformat(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._date = dlg.selected_date()
            self._update_text()
            self.date_changed.emit(self._date)


class _CalendarDialog(QDialog):
    """DateButton が内部で使用するカレンダー選択ダイアログ（外部から直接使わない）"""
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
    """
    ユーザー一覧コンボボックス。表示は表示名、内部値はメールアドレス。
    members にメールアドレスのリストを渡し、display_names で表示名を指定する。
    """
    user_changed = Signal(str)  # メールアドレスを送出

    def __init__(self, members: List[str], display_names: dict = None, parent=None):
        super().__init__(parent)
        self._display_names = display_names or {}
        # 表示名を addItem し、UserRole にメールアドレスを保持（内部識別子）
        for email in members:
            display = self._display_names.get(email, email)
            self.addItem(display)
            self.setItemData(self.count() - 1, email, Qt.ItemDataRole.UserRole)
        self.currentIndexChanged.connect(self._on_index_changed)
        self.setStyleSheet(STYLE_COMBO)

    def _on_index_changed(self, index: int) -> None:
        if index >= 0:
            email = self.itemData(index, Qt.ItemDataRole.UserRole)
            if email:
                self.user_changed.emit(str(email))

    def current_user(self) -> str:
        """現在選択中のメールアドレスを返す"""
        idx = self.currentIndex()
        if idx >= 0:
            data = self.itemData(idx, Qt.ItemDataRole.UserRole)
            return str(data) if data else ""
        return ""

    def set_user(self, email: str) -> None:
        for i in range(self.count()):
            if self.itemData(i, Qt.ItemDataRole.UserRole) == email:
                self.setCurrentIndex(i)
                return


class ColorCombo(QComboBox):
    """
    カラー選択コンボボックス。
    COLOR_OPTIONS の色名を一覧表示し、各アイテムの左にカラーアイコンを表示する。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # DecorationRole に QColor を設定することでアイコン付き一覧を実現
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
    """
    横並びボタン群ウィジェット。
    ボタン名からボタンオブジェクトを参照・有効/無効化できる。
    """

    def __init__(self, buttons: List[tuple], parent=None):
        """
        buttons: [("ラベル", callback), ...]  callback が None のときはボタンのみ配置
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
    """
    ステータス表示用ラベル。
    通常メッセージは set_info()、エラーは set_error()（赤色）で表示する。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(STYLE_LABEL_INFO)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_info(self, msg: str) -> None:
        """通常の情報メッセージを表示する"""
        self.setText(msg)

    def set_error(self, msg: str) -> None:
        """エラーメッセージを赤色で表示する"""
        self.setText(f"⚠ {msg}")
        self.setStyleSheet("QLabel { color: #C62828; font-size: 10px; }")


class AutoCombo(QComboBox):
    """
    編集可能コンボボックス（候補リスト付き入力欄）。
    ドロップダウンで候補を選ぶことも、直接テキスト入力することも可能。
    """

    def __init__(self, items: List[str] = None, parent=None):
        super().__init__(parent)
        # setEditable(True) で自由入力を許可
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


class _NumericItem(QTableWidgetItem):
    """数値列を正しく数値ソートするための QTableWidgetItem サブクラス。"""
    def __lt__(self, other: "QTableWidgetItem") -> bool:
        try:
            return float(self.text()) < float(other.text())
        except ValueError:
            return super().__lt__(other)


class ScrollableTable(QTableWidget):
    """
    汎用スクロール可能テーブル。
    列名リストと表示幅リストを受け取って初期化する。
    行クリックで行全体が選択され、各行に IDX を UserRole として保持できる。
    """

    def __init__(self, columns: List[str], col_widths: List[int] = None,
                 parent=None, reset_sort_on_update: bool = False):
        super().__init__(0, len(columns), parent)
        self._reset_sort_on_update = reset_sort_on_update
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
        self.setSortingEnabled(True)  # ヘッダークリックでソート有効化

    def set_rows(self, rows: List[List], row_ids: List[str] = None,
                 colors: List[str] = None) -> None:
        """
        rows: [[val, val, ...], ...]
        row_ids: 各行に関連付ける IDX（UserRole で保持）
        colors: 各行の背景色 hex 文字列（None 行はデフォルト）
        """
        self.setSortingEnabled(False)  # 挿入中はソートを一時停止（行順の破損を防ぐ）
        self.setRowCount(0)
        for r_idx, row in enumerate(rows):
            self.insertRow(r_idx)
            for c_idx, val in enumerate(row):
                text = str(val) if val is not None else ""
                try:
                    float(text)  # 数値かどうか判定
                    item = _NumericItem(text)
                except ValueError:
                    item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if row_ids and r_idx < len(row_ids):
                    item.setData(Qt.ItemDataRole.UserRole, row_ids[r_idx])
                if colors and r_idx < len(colors) and colors[r_idx]:
                    bg = QColor(colors[r_idx])
                    bg.setAlpha(80)
                    item.setBackground(bg)
                self.setItem(r_idx, c_idx, item)
        if self._reset_sort_on_update:
            # setSortingEnabled(True) 前にリセットしないと Qt が自動ソートを実行してしまう
            self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.setSortingEnabled(True)  # 挿入完了後にソート再有効化

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
    """水平区切り線（セクション間の視覚的な分割に使用）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)


class PomodoroWidget(QWidget):
    """
    ポモドーロタイマー（ツールバー常駐のコンパクト表示）。

    状態遷移: idle → work（▶開始）→ break（作業終了で自動）→ idle
    作業終了時（タイマー満了 / ■停止での中断）に work_finished を送出し、
    実績記録（daily_schedule への書き込み）は受け手（MainWindow）が行う。
    """

    # (ticket_idx, 作業開始 datetime, 作業終了 datetime)
    work_finished = Signal(str, object, object)

    def __init__(self, work_minutes: int = 25, break_minutes: int = 5,
                 parent=None):
        super().__init__(parent)
        self._work_sec = max(1, int(work_minutes)) * 60
        self._break_sec = max(1, int(break_minutes)) * 60
        self._mode = "idle"  # idle / work / break
        self._remaining = 0
        self._ticket_idx = ""
        self._ticket_title = ""
        self._session_ticket = ""   # 作業開始時に確定する対象チケット
        self._work_start = None

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        self.time_lbl = QLabel("🍅 --:--")
        self.time_lbl.setStyleSheet("QLabel { font-weight: bold; }")
        self.ticket_lbl = QLabel("(チケット未選択)")
        self.ticket_lbl.setMaximumWidth(160)
        self.ticket_lbl.setStyleSheet(STYLE_LABEL_INFO)
        self.btn = QPushButton("▶ 集中開始")
        self.btn.setStyleSheet(STYLE_BUTTON)
        self.btn.setToolTip("選択中のチケットでポモドーロタイマーを開始します\n"
                            "（終了時に実績をスケジュールへ記録できます）")
        self.btn.clicked.connect(self._on_btn)
        lay.addWidget(self.time_lbl)
        lay.addWidget(self.ticket_lbl)
        lay.addWidget(self.btn)

    # ── 外部 API ──

    def set_ticket(self, idx: str, title: str) -> None:
        """Main/Edit タブで選択されたチケットを次セッションの対象にする"""
        self._ticket_idx = idx
        self._ticket_title = title
        if self._mode == "idle":
            self.ticket_lbl.setText(title or "(チケット未選択)")
            self.ticket_lbl.setToolTip(title)

    # ── 内部処理 ──

    @staticmethod
    def _fmt(sec: int) -> str:
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _on_btn(self) -> None:
        if self._mode == "idle":
            if not self._ticket_idx:
                QMessageBox.information(
                    self, "ポモドーロ",
                    "Main / Edit タブでチケットを選択してから開始してください")
                return
            self._start_work()
        elif self._mode == "work":
            # 中断時も経過分の記録を確認する
            self._finish_work()
        else:  # break
            self._to_idle()

    def _start_work(self) -> None:
        self._mode = "work"
        self._session_ticket = self._ticket_idx
        self._work_start = datetime.datetime.now()
        self._remaining = self._work_sec
        self.btn.setText("■ 停止")
        self.time_lbl.setText(f"🍅 {self._fmt(self._remaining)}")
        self.ticket_lbl.setText(self._ticket_title)
        self._timer.start()

    def _finish_work(self) -> None:
        self._timer.stop()
        QApplication.beep()
        self.work_finished.emit(
            self._session_ticket, self._work_start, datetime.datetime.now())
        self._start_break()

    def _start_break(self) -> None:
        self._mode = "break"
        self._remaining = self._break_sec
        self.btn.setText("■ 休憩中止")
        self.time_lbl.setText(f"☕ {self._fmt(self._remaining)}")
        self._timer.start()

    def _to_idle(self) -> None:
        self._timer.stop()
        self._mode = "idle"
        self.btn.setText("▶ 集中開始")
        self.time_lbl.setText("🍅 --:--")
        self.ticket_lbl.setText(self._ticket_title or "(チケット未選択)")

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            if self._mode == "work":
                self._finish_work()
            else:
                QApplication.beep()
                self._to_idle()
            return
        icon = "🍅" if self._mode == "work" else "☕"
        self.time_lbl.setText(f"{icon} {self._fmt(self._remaining)}")
