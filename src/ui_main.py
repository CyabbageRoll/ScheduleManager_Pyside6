"""
ui_main.py - メインウィンドウ・ツールバー・3ペイン・日次スケジュール
"""
import datetime
from typing import Optional

import pandas as pd
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QFormLayout, QScrollArea, QMessageBox, QHeaderView,
    QFrame, QStackedWidget, QSizePolicy, QToolBar, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QStyledItemDelegate, QDateEdit,
)
from pathlib import Path

from PySide6.QtCore import Qt, QDate, Signal, QEvent, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut, QAction, QPen, QDesktopServices

import db as DB
import logic as LG
from ui_widgets import (
    DateButton, UserCombo, ColorCombo, ButtonRow, InfoLabel,
    AutoCombo, ScrollableTable, Separator, COLOR_OPTIONS, STYLE_BUTTON,
)

# 画面インデックス（QStackedWidget）
IDX_MAIN    = 0
IDX_GANTT   = 1
IDX_ROADMAP = 2
IDX_ANALYSIS= 3
IDX_SEARCH  = 4
IDX_TEAM    = 5
IDX_ASSIGN  = 6
IDX_MEMO    = 7
IDX_VERSION  = 8
IDX_CONFIG   = 9
IDX_AIIMPORT = 10


# ---------- 日次スケジュール用カスタムデリゲート ----------

class _HourLineDelegate(QStyledItemDelegate):
    """毎時00分の行の上に区切り線を描画し、同一チケットを囲むデリゲート"""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        # 毎時区切り線
        if index.data(Qt.ItemDataRole.UserRole) == "hour":
            painter.save()
            pen = QPen(QColor("#5C6BC0"))
            pen.setWidth(2)
            painter.setPen(pen)
            r = option.rect
            painter.drawLine(r.topLeft(), r.topRight())
            painter.restore()
        # 項目6: 同一チケットの囲み線（task列のみ: col==1）
        if index.column() == 1:
            pos = index.data(Qt.ItemDataRole.UserRole + 1)
            if pos in ("first", "single", "last"):
                painter.save()
                pen = QPen(QColor("#5C6BC0"))
                pen.setWidth(1)
                painter.setPen(pen)
                r = option.rect
                # 左右の縦線（常時）
                painter.drawLine(r.topLeft(), r.bottomLeft())
                painter.drawLine(r.topRight(), r.bottomRight())
                if pos in ("first", "single"):
                    painter.drawLine(r.topLeft(), r.topRight())
                if pos in ("last", "single"):
                    painter.drawLine(r.bottomLeft(), r.bottomRight())
                painter.restore()
            elif pos == "middle":
                painter.save()
                pen = QPen(QColor("#5C6BC0"))
                pen.setWidth(1)
                painter.setPen(pen)
                r = option.rect
                painter.drawLine(r.topLeft(), r.bottomLeft())
                painter.drawLine(r.topRight(), r.bottomRight())
                painter.restore()


# ---------- 日次スケジュールウィジェット ----------

class DailyScheduleWidget(QWidget):
    """
    左端に常時表示される日次スケジュールパネル。
    00:00〜23:45 を 15 分刻みで表示し、各スロットにチケットを割り当てられる。
    健康状態・就業場所・常時メモなどの日次ログ入力フォームも内包する。
    """

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._selected_ticket: Optional[str] = None

        self.setMinimumWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── コンパクト入力フォーム（チームログから移動）──
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "QFrame { background:#F8F9FA; border-radius:4px; border:1px solid #CFD8DC; }"
            "QLabel { border:none; font-size:7pt; }"
        )
        in_layout = QVBoxLayout(input_frame)
        in_layout.setContentsMargins(4, 3, 4, 3)
        in_layout.setSpacing(2)

        cfg_combo = state.config.daily_combo
        self.f_health     = AutoCombo(cfg_combo.get("health_status", []))
        self.f_work_place = AutoCombo(cfg_combo.get("work_place", []))
        self.f_safety     = AutoCombo(cfg_combo.get("safety", []))
        self.f_overwork   = AutoCombo(cfg_combo.get("overwork", []))
        self.f_notes      = QLineEdit()
        self.f_notes.setPlaceholderText("今日の連絡事項")
        self.f_notes_ever = QLineEdit()
        self.f_notes_ever.setPlaceholderText("常時表示メモ")
        for w in [self.f_health, self.f_work_place, self.f_safety, self.f_overwork,
                  self.f_notes, self.f_notes_ever]:
            w.setFixedHeight(20)
            w.setStyleSheet("font-size:7pt;")

        row1 = QHBoxLayout(); row1.setSpacing(2)
        for lbl, wgt in [("健:", self.f_health), ("場:", self.f_work_place),
                          ("安:", self.f_safety),  ("残:", self.f_overwork)]:
            row1.addWidget(QLabel(lbl))
            row1.addWidget(wgt, stretch=1)
        in_layout.addLayout(row1)

        row2 = QHBoxLayout(); row2.setSpacing(2)
        row2.addWidget(QLabel("今日:"))
        row2.addWidget(self.f_notes, stretch=1)
        in_layout.addLayout(row2)

        row3 = QHBoxLayout(); row3.setSpacing(2)
        row3.addWidget(QLabel("常時:"))
        row3.addWidget(self.f_notes_ever, stretch=1)
        in_layout.addLayout(row3)
        # 常時メモは編集完了時（フォーカス移動・Enterキー）に自動保存
        self.f_notes_ever.editingFinished.connect(self._on_save_permanent)

        layout.addWidget(input_frame)

        layout.addWidget(QLabel("📅 日次スケジュール"))

        # ボタン行
        btn_row = QHBoxLayout()
        self.free_btn = QPushButton("Free")
        self.free_btn.setStyleSheet(STYLE_BUTTON)
        self.free_btn.clicked.connect(self._on_free)
        btn_row.addWidget(self.free_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # スケジュールテーブル（96行: 00:00〜23:45 の 15分刻み）
        self.schedule_table = QTableWidget(96, 2)
        self.schedule_table.setHorizontalHeaderLabels(["時刻", "タスク"])
        self.schedule_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.schedule_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.schedule_table.verticalHeader().setVisible(False)
        self.schedule_table.setColumnWidth(0, 90)
        self.schedule_table.horizontalHeader().setStretchLastSection(True)
        self.schedule_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.schedule_table.setAlternatingRowColors(False)
        self.schedule_table.verticalHeader().setDefaultSectionSize(14)
        self.schedule_table.setStyleSheet(
            "QTableWidget { gridline-color: #E8EAF6; border: 1px solid #9FA8DA;"
            " font-size: 7pt; background: #FAFAFA; }"
            "QTableWidget::item:selected { background: #7986CB; color: white; }"
        )
        # デリゲートを設定（毎時00分に区切り線、同一チケットに囲み線を描画）
        self.schedule_table.setItemDelegate(_HourLineDelegate(self.schedule_table))

        # 時刻ラベルを設定（全行フル表示、毎時にスタイルを付与）
        hour_font = QFont()
        hour_font.setBold(True)
        hour_font.setPointSize(7)
        sub_font = QFont()
        sub_font.setPointSize(6)
        for i in range(96):
            hh, mm = i // 4, (i % 4) * 15
            hh_next, mm_next = (i + 1) // 4, ((i + 1) % 4) * 15
            time_label = f"{hh:02d}:{mm:02d}〜{hh_next:02d}:{mm_next:02d}"
            item = QTableWidgetItem(time_label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            task_item = QTableWidgetItem("")
            task_item.setFlags(task_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if mm == 0:
                # 毎時00分: 太字・濃い青・薄い背景
                item.setData(Qt.ItemDataRole.UserRole, "hour")
                item.setFont(hour_font)
                item.setForeground(QColor("#283593"))
                item.setBackground(QColor("#EDE7F6"))
                task_item.setData(Qt.ItemDataRole.UserRole, "hour")
                task_item.setBackground(QColor("#EDE7F6"))
            else:
                # サブ行: 小さめ・グレー
                item.setFont(sub_font)
                item.setForeground(QColor("#78909C"))
            self.schedule_table.setItem(i, 0, item)
            self.schedule_table.setItem(i, 1, task_item)

        layout.addWidget(self.schedule_table, stretch=1)
        # 表示後に行高・スクロール位置を調整（レイアウト確定後に実行）
        QTimer.singleShot(0, self._adjust_row_heights)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def assign_ticket(self, ticket_idx: str) -> None:
        """チケット選択シグナルを受け取り、スロット割り当て or 選択保持を行う"""
        if ticket_idx not in self.state.df_nodes.index:
            return

        rows = sorted(set(
            idx.row() for idx in self.schedule_table.selectedIndexes()
        ))
        is_ticket = self.state.df_nodes.loc[ticket_idx, "node_type"] == "ticket"

        # 自分のチケットのみ割り当て可能
        assigned_to = str(self.state.df_nodes.loc[ticket_idx, "assigned_to"]) if is_ticket else ""
        is_own_ticket = is_ticket and assigned_to == self.state.user

        if rows and self.state.current_member == self.state.user and is_own_ticket:
            # 選択行に割り当て
            self._update_schedule_slots(rows, ticket_idx)
            self.schedule_table.clearSelection()  # 割り当て後は選択解除
            title = self.state.df_nodes.loc[ticket_idx, "title"]
            self.info.set_info(f"割り当て完了: {title}")
        elif rows and is_ticket and not is_own_ticket:
            # 他人のチケットは割り当て不可
            title = self.state.df_nodes.loc[ticket_idx, "title"]
            self.info.set_info(f"⚠ {title} は自分のチケットではありません")
        else:
            # 選択チケットとして保持
            self._selected_ticket = ticket_idx
            title = self.state.df_nodes.loc[ticket_idx, "title"] if is_ticket else ""
            if title:
                self.info.set_info(f"選択中: {title}")

    def _on_free(self) -> None:
        """選択スロットをクリアする"""
        if self.state.current_member != self.state.user:
            return
        rows = sorted(set(
            idx.row() for idx in self.schedule_table.selectedIndexes()
        ))
        self._update_schedule_slots(rows, "")
        self.schedule_table.clearSelection()  # 解除後は選択をクリア

    def refresh(self) -> None:
        """スケジュール表示を更新する"""
        self._rebuild_schedule()
        self._adjust_row_heights()
        self._load_log_values()

    def _load_log_values(self) -> None:
        """本日の入力欄に保存済み値をセット"""
        date = self.state.current_date
        my_idx = DB.daily_sch_idx(date, self.state.user)
        df_log = getattr(self.state, "df_daily_log", None)
        if df_log is not None and not df_log.empty and my_idx in df_log.index:
            row = df_log.loc[my_idx]
            self.f_health.set_value(str(row.get("health_status", "") or ""))
            self.f_work_place.set_value(str(row.get("work_place", "") or ""))
            self.f_safety.set_value(str(row.get("safety", "") or ""))
            self.f_overwork.set_value(str(row.get("overwork", "") or ""))
            self.f_notes.setText(str(row.get("notes", "") or ""))
        # 常時メモ（state に保持）
        self.f_notes_ever.setText(getattr(self.state, "permanent_notice", ""))

    def _on_save_log(self) -> None:
        """本日入力フォームと常時メモを保存する"""
        date = self.state.current_date
        idx = DB.daily_sch_idx(date, self.state.user)
        today = datetime.date.today().isoformat()
        row = {
            "Owner":         self.state.user,
            "health_status": self.f_health.get_value(),
            "work_place":    self.f_work_place.get_value(),
            "safety":        self.f_safety.get_value(),
            "overwork":      self.f_overwork.get_value(),
            "notes":         self.f_notes.text(),
            "Last_Update":   today,
        }
        self.state.df_daily_log.loc[idx] = row
        self.info.set_info("保存しました")

    def _on_save_permanent(self) -> None:
        """常時メモを自動保存する（editingFinished シグナルで呼ばれる）"""
        ever_text = self.f_notes_ever.text()
        if ever_text == getattr(self.state, "permanent_notice", ""):
            return  # 変化なしは保存しない
        self.state.permanent_notice = ever_text
        self.state.all_permanent_notices[self.state.user] = ever_text
        if self.state.db:
            self.state.db.save_permanent_notice(self.state.user, ever_text)

    def _adjust_row_heights(self) -> None:
        """begin_time〜end_timeがビューポートに収まるよう行高を調整し begin_time へスクロール"""
        begin = self.state.config.daily_begin_time
        end = self.state.config.daily_end_time
        work_rows = max(1, (end - begin) * 4)
        viewport_h = self.schedule_table.viewport().height()
        if viewport_h > 10:
            row_h = max(10, viewport_h // work_rows)
            self.schedule_table.verticalHeader().setDefaultSectionSize(row_h)
        # begin_time の行を最上部へスクロール
        begin_item = self.schedule_table.item(begin * 4, 0)
        if begin_item:
            self.schedule_table.scrollToItem(
                begin_item, QAbstractItemView.ScrollHint.PositionAtTop
            )

    def _rebuild_schedule(self) -> None:
        """
        current_date の daily_schedule をテーブルに描画する。

        処理フロー:
          1. 全スロットをクリア（白背景・空テキスト）
          2. df_daily から current_date・current_member の行を取得
          3. 連続チケットスロットの表示テキストを決定:
             - 1行目: P1〜Task のフルパスを「/」区切りで
             - 2行目: チケットタイトル
             - 3行目以降: 「↑」で継続を示す
          4. スロットグループ内の位置（first/middle/last/single）を計算
          5. 各スロットに背景色・テキスト・グループ位置を設定（デリゲートが囲み線を描画）
        """
        date = self.state.current_date
        member = self.state.current_member
        sch_idx = DB.daily_sch_idx(date, member)
        df = self.state.df_daily
        df_nodes = self.state.df_nodes

        # 全スロットをクリア（前回の表示を消す）
        for i in range(96):
            item = self.schedule_table.item(i, 1)
            if item:
                item.setText("")
                item.setBackground(QColor("white"))
                item.setData(Qt.ItemDataRole.UserRole, None)

        if df.empty or sch_idx not in df.index:
            return

        ds = df.loc[sch_idx]
        current_ticket = ""
        same_count = 0
        display = [""] * 96
        _ticket_title = ""  # 現在のチケットタイトル（キャッシュ）

        for i, col in enumerate(DB.DAILY_TIME_COLS):
            t_idx = ds[col] if col in ds.index else ""
            if not t_idx:
                # 空スロットで連続カウントをリセット
                current_ticket = ""
                same_count = 0
                _ticket_title = ""
                continue
            if t_idx != current_ticket:
                # 新しいチケットに切り替わった
                current_ticket = t_idx
                same_count = 0
                _ticket_title = df_nodes.loc[t_idx, "title"] if t_idx in df_nodes.index else t_idx

            if same_count == 0:
                # 項目5: 1行目: P1~Taskのフルパスを / 区切りで表示
                path_parts = []
                if t_idx in df_nodes.index:
                    pid = str(df_nodes.loc[t_idx, "parent_id"])
                    while pid and pid != "0" and pid in df_nodes.index:
                        path_parts.insert(0, str(df_nodes.loc[pid, "title"]))
                        pid = str(df_nodes.loc[pid, "parent_id"])
                display[i] = " / ".join(path_parts) if path_parts else f"[{_ticket_title}]"
            elif same_count == 1:
                # 2行目: チケットのタイトルを表示
                display[i] = _ticket_title
            else:
                # 3行目以降: 継続マークを表示
                display[i] = "↑"
            same_count += 1

        # 項目6: 各スロットのチケットグループ内位置を計算（デリゲートでの囲み線描画に使用）
        position_marks = [""] * 96
        for i, col in enumerate(DB.DAILY_TIME_COLS):
            t_idx_i = ds[col] if col in ds.index else ""
            if not t_idx_i:
                continue
            prev_idx = ds[DB.DAILY_TIME_COLS[i - 1]] if i > 0 and DB.DAILY_TIME_COLS[i - 1] in ds.index else ""
            next_idx = ds[DB.DAILY_TIME_COLS[i + 1]] if i < 95 and DB.DAILY_TIME_COLS[i + 1] in ds.index else ""
            is_first = (t_idx_i != prev_idx)
            is_last  = (t_idx_i != next_idx)
            if is_first and is_last:
                position_marks[i] = "single"
            elif is_first:
                position_marks[i] = "first"
            elif is_last:
                position_marks[i] = "last"
            else:
                position_marks[i] = "middle"

        for i in range(96):
            col = DB.DAILY_TIME_COLS[i]
            t_idx = ds[col] if col in ds.index else ""
            item = self.schedule_table.item(i, 1)
            if item:
                item.setText(display[i])
                item.setData(Qt.ItemDataRole.UserRole, t_idx)
                # 項目6: グループ内位置をアイテムに保存
                item.setData(Qt.ItemDataRole.UserRole + 1, position_marks[i])
                if t_idx and t_idx in df_nodes.index:
                    hex_c = COLOR_OPTIONS.get(
                        df_nodes.loc[t_idx, "color"], "#00BCD4"
                    )
                    bg = QColor(hex_c)
                    bg.setAlpha(80)
                    item.setBackground(bg)

    def _update_schedule_slots(self, rows: list, ticket_idx: str) -> None:
        """指定スロットにチケットを割り当て（または空欄に）する"""
        date = self.state.current_date
        member = self.state.current_member
        sch_idx = DB.daily_sch_idx(date, member)
        df = self.state.df_daily

        if df.empty or sch_idx not in df.index:
            # 新規行を作成
            new_row = {c: "" for c in DB.DAILY_SCH_COLS[1:]}
            new_row["Owner"] = member
            new_row["Last_Update"] = datetime.date.today().isoformat()
            df.loc[sch_idx] = new_row
            self.state.df_daily = df

        for r in rows:
            if r < len(DB.DAILY_TIME_COLS):
                col = DB.DAILY_TIME_COLS[r]
                # 変更前の値から actual_hours を更新
                old_t = self.state.df_daily.loc[sch_idx, col]
                if old_t and old_t in self.state.df_nodes.index:
                    self.state.df_nodes.loc[old_t, "actual_hours"] = max(
                        0.0,
                        float(self.state.df_nodes.loc[old_t, "actual_hours"] or 0) - 0.25,
                    )
                    self._propagate_actual_hours(old_t)
                # 新しい値を設定
                self.state.df_daily.loc[sch_idx, col] = ticket_idx
                if ticket_idx and ticket_idx in self.state.df_nodes.index:
                    self.state.df_nodes.loc[ticket_idx, "actual_hours"] = (
                        float(self.state.df_nodes.loc[ticket_idx, "actual_hours"] or 0) + 0.25
                    )
                    self.state.df_nodes.loc[ticket_idx, "updated_at"] = \
                        datetime.date.today().isoformat()
                    self._propagate_actual_hours(ticket_idx)

        self.state.df_daily.loc[sch_idx, "Last_Update"] = \
            datetime.date.today().isoformat()
        self._rebuild_schedule()

    def _propagate_actual_hours(self, node_idx: str) -> None:
        """ノードの actual_hours 変更を祖先ノード（Task〜Project1）に伝播する"""
        df = self.state.df_nodes
        current_idx = node_idx
        while True:
            parent_id = str(df.loc[current_idx, "parent_id"] or "")
            if not parent_id or parent_id == "0" or parent_id not in df.index:
                break
            # 親の actual_hours = 子（cancel/deleted 以外）の合計
            children = df[
                (df["parent_id"] == parent_id)
                & (~df["status"].isin(["cancel", "deleted"]))
            ]
            df.loc[parent_id, "actual_hours"] = float(children["actual_hours"].sum())
            current_idx = parent_id


class MainWindow(QMainWindow):
    """アプリケーションメインウィンドウ"""

    def __init__(self, state, version: str):
        super().__init__()
        self.state = state
        self.version = version
        self.setWindowTitle(f"スケジュール管理 {version}")

        # フォント設定
        font = QFont()
        font.setPointSize(state.config.font_size)
        self.setFont(font)

        self._build_toolbar()
        self._build_central()
        self._setup_shortcuts()

        # AppState にリフレッシュ関数を登録
        state.refresh_func = self.refresh

        self._switch_view(IDX_GANTT)

    # ---------- ツールバー ----------

    def _build_toolbar(self) -> None:
        tb = QToolBar("メインツールバー")
        tb.setMovable(False)
        tb.setStyleSheet(
            "QToolBar { background: #ECEFF1; border-bottom: 1px solid #CFD8DC; padding: 4px; }"
        )
        self.addToolBar(tb)

        # 日付ナビゲーション（前日・日付選択・翌日・今日）
        prev_btn = QPushButton("◀")
        prev_btn.setFixedWidth(28)
        prev_btn.setStyleSheet(STYLE_BUTTON)
        prev_btn.setToolTip("前日")
        prev_btn.clicked.connect(self._on_prev_day)
        tb.addWidget(prev_btn)

        self.date_btn = DateButton(initial_date=self.state.current_date)
        self.date_btn.date_changed.connect(self._on_date_changed)
        tb.addWidget(self.date_btn)

        next_btn = QPushButton("▶")
        next_btn.setFixedWidth(28)
        next_btn.setStyleSheet(STYLE_BUTTON)
        next_btn.setToolTip("翌日")
        next_btn.clicked.connect(self._on_next_day)
        tb.addWidget(next_btn)

        today_btn = QPushButton("⏎")
        today_btn.setFixedWidth(32)
        today_btn.setStyleSheet(STYLE_BUTTON)
        today_btn.setToolTip("今日")
        today_btn.clicked.connect(self._on_today)
        tb.addWidget(today_btn)

        tb.addSeparator()

        # Save / Load
        for label, slot in [("💾 保存 (Ctrl+S)", self._on_save),
                             ("🔄 読込 (Ctrl+R)", self._on_load)]:
            act = QAction(label, self)
            act.triggered.connect(slot)
            tb.addAction(act)

        tb.addSeparator()

        # 画面切替ボタン（等幅・縁付き・グラデーション）
        _TAB_STYLE = (
            "QPushButton {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "   stop:0 #FFFFFF, stop:1 #E8ECEF);"
            " color: #37474F;"
            " border: 1px solid #B0BEC5;"
            " border-radius: 5px;"
            " padding: 5px 0px;"
            " font-size: 8pt; font-weight: bold;"
            " min-width: 74px; max-width: 74px; }"
            "QPushButton:checked {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "   stop:0 #1E88E5, stop:1 #1565C0);"
            " color: white;"
            " border: 1px solid #0D47A1;"
            " border-bottom: 2px solid #083A82; }"
            "QPushButton:hover:!checked {"
            " background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "   stop:0 #E3F2FD, stop:1 #BBDEFB);"
            " border-color: #64B5F6; color:#1565C0; }"
        )
        views = [
            ("📊 Main",    IDX_GANTT),
            ("🗺 Plan",    IDX_ROADMAP),
            ("✏ Edit",    IDX_MAIN),
            ("👥 Team",    IDX_TEAM),
            ("🔍 Search",  IDX_SEARCH),
            ("📨 Request", IDX_ASSIGN),
            ("📝 Memo",    IDX_MEMO),
            ("📈 Analyze", IDX_ANALYSIS),
            ("ℹ Version", IDX_VERSION),
            ("⚙ Config",  IDX_CONFIG),
            ("🤖 AI取込",  IDX_AIIMPORT),
        ]
        self._tab_btns: dict = {}
        for label, view_idx in views:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(_TAB_STYLE)
            btn.clicked.connect(lambda checked, vi=view_idx: self._switch_view(vi))
            tb.addWidget(btn)
            self._tab_btns[view_idx] = btn

        tb.addSeparator()

        # マニュアルを開くボタン
        manual_btn = QPushButton("📖 Manual")
        manual_btn.setStyleSheet(STYLE_BUTTON)
        manual_btn.setToolTip("マニュアルをブラウザで開く")
        manual_btn.clicked.connect(self._on_open_manual)
        tb.addWidget(manual_btn)

        # 項目7: 2行目のツールバーにメンバーボタンを追加
        self.addToolBarBreak()
        tb2 = QToolBar("メンバー選択ツールバー")
        tb2.setMovable(False)
        tb2.setStyleSheet(
            "QToolBar { background: #F5F5F5; border-bottom: 1px solid #CFD8DC; padding: 2px; }"
        )
        self.addToolBar(tb2)
        tb2.addWidget(QLabel(" メンバー: "))

        # メンバーボタン（ボタン形式で素早く切替）
        _MEMBER_STYLE = (
            "QPushButton {"
            " background: #ECEFF1; color: #37474F;"
            " border: 1px solid #B0BEC5; border-radius: 4px;"
            " padding: 3px 10px; font-size: 8pt; }"
            "QPushButton:checked {"
            " background: #1565C0; color: white;"
            " border: 1px solid #0D47A1; }"
            "QPushButton:hover:!checked {"
            " background: #E3F2FD; border-color: #64B5F6; color: #1565C0; }"
        )
        self._member_btns: dict = {}
        for m in self.state.members:
            display = self.state.display_name(m)
            btn = QPushButton(display)
            btn.setCheckable(True)
            btn.setChecked(m == self.state.current_member)
            btn.setStyleSheet(_MEMBER_STYLE)
            btn.clicked.connect(lambda checked=False, member=m: self._on_member_changed(member))
            tb2.addWidget(btn)
            self._member_btns[m] = btn

    def _on_open_manual(self) -> None:
        """マニュアルHTMLをデフォルトブラウザで開く"""
        manual_path = Path(__file__).parent / "manual.html"
        if manual_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(manual_path)))
        else:
            QMessageBox.warning(self, "マニュアル", f"マニュアルファイルが見つかりません:\n{manual_path}")

    # ---------- 中央ウィジェット ----------

    def _build_central(self) -> None:
        """中央ウィジェットを構築する（左: 日次スケジュール / 右: スタックビュー）"""
        import ui_sub

        # 左端の日次スケジュールパネル（ガントビュー表示時のみ表示）
        self.schedule_panel = DailyScheduleWidget(self.state)

        # スタックウィジェット（各種ビューを切替）
        self.stack = QStackedWidget()

        # 外側スプリッター
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self.schedule_panel)
        outer.addWidget(self.stack)
        outer.setSizes([400, 1100])

        self.setCentralWidget(outer)

        # 各ビューを生成してスタックに追加
        self.main_pane   = _Main3Pane(self.state)
        self.gantt_view  = ui_sub.GanttView(self.state)
        self.road_view   = ui_sub.RoadmapView(self.state)
        self.anal_view   = ui_sub.AnalysisView(self.state)
        self.search_view = ui_sub.SearchView(self.state)
        self.team_view   = ui_sub.TeamLogView(self.state)
        self.assign_view = ui_sub.AssignmentView(self.state)
        self.memo_view   = ui_sub.MemoView(self.state)
        self.ver_view    = ui_sub.VersionView(self.state, self.version)
        self.config_view     = ui_sub.ConfigView(self.state)
        self.ai_import_view  = ui_sub.AIImportView(self.state)

        for w in [self.main_pane, self.gantt_view, self.road_view,
                  self.anal_view, self.search_view, self.team_view,
                  self.assign_view, self.memo_view, self.ver_view,
                  self.config_view, self.ai_import_view]:
            self.stack.addWidget(w)

        # シグナル接続：チケット選択 → スケジュールパネルへ
        self.main_pane.table_pane.node_selected.connect(self.schedule_panel.assign_ticket)
        self.gantt_view.ticket_clicked.connect(self.schedule_panel.assign_ticket)
        self.gantt_view.edit_requested.connect(self._on_gantt_edit_requested)
        self.road_view.edit_requested.connect(self._on_gantt_edit_requested)
        self.road_view.edit_popup_requested.connect(self._on_roadmap_edit_popup)
        # 項目3: Request シグナル接続
        self.gantt_view.request_requested.connect(self._on_request_requested)
        self.road_view.request_requested.connect(self._on_request_requested)
        # AI取込完了 → Edit タブへ
        self.ai_import_view.import_done.connect(self._on_ai_import_done)

    # ---------- ショートカット ----------

    def _setup_shortcuts(self) -> None:
        """キーボードショートカットを登録する（Ctrl+S: 保存、Ctrl+R: 読込、Ctrl+1〜9: ビュー切替）"""
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_save)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._on_load)
        for i, vi in enumerate(range(1, 10)):
            QShortcut(QKeySequence(f"Ctrl+{vi}"), self).activated.connect(
                lambda checked=False, idx=i: self._switch_view(idx)
            )

    # ---------- ビュー切替 ----------

    def _switch_view(self, view_idx: int) -> None:
        """指定インデックスのビューに切替え、タブボタンのチェック状態を更新する"""
        self.stack.setCurrentIndex(view_idx)
        # タブボタンのチェック状態を更新（選択中のビューを強調）
        for vi, btn in getattr(self, "_tab_btns", {}).items():
            btn.setChecked(vi == view_idx)
        # 日次スケジュールパネルはガントビューのみ表示
        show = view_idx == IDX_GANTT
        self.schedule_panel.setVisible(show)
        if show:
            self.schedule_panel.refresh()
        # 切替先ビューを更新
        cur = self.stack.currentWidget()
        if hasattr(cur, "refresh"):
            cur.refresh()

    # ---------- スロット ----------

    def _on_date_changed(self, date_str: str) -> None:
        self.state.current_date = date_str
        self.refresh()

    def _on_prev_day(self) -> None:
        d = datetime.date.fromisoformat(self.state.current_date)
        new_date = (d - datetime.timedelta(days=1)).isoformat()
        self.state.current_date = new_date
        self.date_btn.set_date(new_date)
        self.refresh()

    def _on_next_day(self) -> None:
        d = datetime.date.fromisoformat(self.state.current_date)
        new_date = (d + datetime.timedelta(days=1)).isoformat()
        self.state.current_date = new_date
        self.date_btn.set_date(new_date)
        self.refresh()

    def _on_today(self) -> None:
        new_date = datetime.date.today().isoformat()
        self.state.current_date = new_date
        self.date_btn.set_date(new_date)
        self.refresh()

    def _on_member_changed(self, member: str) -> None:
        """項目7: メンバー選択時にボタンのチェック状態を更新"""
        self.state.current_member = member
        # ボタンのチェック状態を更新
        for m, btn in getattr(self, "_member_btns", {}).items():
            btn.setChecked(m == member)
        self.refresh()

    def _on_save(self) -> None:
        try:
            self.state.save()
            self.statusBar().showMessage("保存しました", 3000)
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))

    def _on_load(self) -> None:
        try:
            self.state.load()
            self.refresh()
            self.statusBar().showMessage("読み込みました", 3000)
        except Exception as e:
            QMessageBox.critical(self, "読込エラー", str(e))

    def _on_gantt_edit_requested(self, idx: str) -> None:
        """ガントの右クリック Edit → Edit タブに切替してノードを選択"""
        self._switch_view(IDX_MAIN)
        # ツリーで対象ノードを選択
        self.main_pane.tree_pane._restore_selection(idx)
        # テーブルで親ノードを表示してその行を選択
        if idx in self.state.df_nodes.index:
            parent_id = str(self.state.df_nodes.loc[idx, "parent_id"])
            self.main_pane.table_pane.update_for_parent(parent_id)
            table = self.main_pane.table_pane.table
            for r in range(table.rowCount()):
                it = table.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) == idx:
                    table.selectRow(r)
                    break

    def _on_request_requested(self, idx: str) -> None:
        """項目3: 依頼タブに切替してチケットを選択状態にする"""
        self._switch_view(IDX_ASSIGN)
        self.assign_view.select_ticket(idx)

    def _on_roadmap_edit_popup(self, idx: str) -> None:
        """ロードマップのダブルクリック → ポップアップダイアログで編集"""
        if idx not in self.state.df_nodes.index:
            return
        if self.state.df_nodes.loc[idx, "assigned_to"] != self.state.user:
            QMessageBox.warning(self, "編集不可", "他ユーザーのデータは編集できません")
            return
        dlg = _NodeEditDialog(
            parent_idx=None, node_type=None,
            state=self.state, edit_idx=idx, parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ds = dlg.get_series()
            self.state.df_nodes.loc[ds.name] = ds
            self.state.db.upsert_node(ds)
            self.state.df_nodes = self.state.db.read_nodes()
            self.state.refresh()

    def _on_ai_import_done(self, idxs: list) -> None:
        """AI取込完了後に Edit タブへ切替し取り込みキューをセットする"""
        self._switch_view(IDX_MAIN)
        self.main_pane.tree_pane.start_import_queue(idxs)

    # ---------- リフレッシュ ----------

    def refresh(self) -> None:
        """全サブビューを更新する（Edit ペイン・スケジュールパネル・現在表示中のビュー）"""
        self.main_pane.refresh()
        self.schedule_panel.refresh()
        # 現在表示中のビューのみ追加でリフレッシュ（二重更新を防ぐ）
        cur = self.stack.currentWidget()
        if hasattr(cur, "refresh") and cur is not self.main_pane:
            cur.refresh()


# ---------- 3 ペインレイアウト ----------

class _Main3Pane(QWidget):
    """
    Edit 画面の 2 分割ビュー（左: 階層ツリー / 右: 子ノード一覧テーブル）。
    TreePane でノードを選択すると TablePane が対応する子ノードを表示する。
    """

    def __init__(self, state):
        super().__init__()
        self.state = state

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree_pane  = TreePane(state)
        self.table_pane = TablePane(state)

        splitter.addWidget(self.tree_pane)
        splitter.addWidget(self.table_pane)
        splitter.setSizes([280, 920])
        splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        # ペイン間の連携
        self.tree_pane.node_selected.connect(self._on_tree_select)

    def _on_tree_select(self, idx: str) -> None:
        self.table_pane.update_for_parent(idx)

    def refresh(self) -> None:
        self.tree_pane.refresh()
        self.table_pane.refresh()


# ---------- 左ペイン：階層ツリー ----------

class TreePane(QWidget):
    """
    階層ツリーペイン（Edit 画面の左側）。
    ノードの新規作成・削除・検索・自分のみフィルタ機能を持つ。
    ノード選択時に node_selected シグナルで IDX を TablePane へ通知する。
    """
    node_selected = Signal(str)  # 選択された IDX

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._selected_idx: Optional[str] = None
        self._filter_own: bool = True    # True = 自分に関係するノードのみ表示
        self._search_text: str = ""
        self._import_queue: list = []    # AI取込キュー
        self._import_pos: int = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ツールバー
        top_row = QHBoxLayout()
        self._btn_row = ButtonRow([
            ("＋ 新規",  self._on_new),
            ("🗑 削除",  self._on_delete),
        ])
        top_row.addWidget(self._btn_row)
        self.filter_btn = QPushButton("👤 自分のみ ✓")
        self.filter_btn.setStyleSheet(STYLE_BUTTON)
        self.filter_btn.setCheckable(True)
        self.filter_btn.setChecked(True)
        self.filter_btn.toggled.connect(self._on_filter_toggle)
        top_row.addWidget(self.filter_btn)
        # AI取込確認用「次へ」ボタン（取込時のみ有効）
        self._next_btn = QPushButton("次へ →")
        self._next_btn.setStyleSheet(STYLE_BUTTON)
        self._next_btn.setEnabled(False)
        self._next_btn.setToolTip("AI取込で追加されたアイテムを順番に表示")
        self._next_btn.clicked.connect(self._on_next_import)
        top_row.addWidget(self._next_btn)
        top_row.addStretch()
        layout.addLayout(top_row)

        # 検索バー
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 ノード検索...")
        self.search_input.textChanged.connect(self._on_search)
        layout.addWidget(self.search_input)

        # ツリーウィジェット（1列：タイトル列をインデントして階層を表現）
        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabels(["ノード階層"])
        self.tree.header().setStretchLastSection(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(self._on_item_changed)
        self.tree.setIndentation(18)
        self.tree.setRootIsDecorated(True)
        # 階層線と種別を視覚的に分かりやすくするスタイル
        self.tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid #CFD8DC;
            }
            QTreeWidget::item {
                padding: 3px 2px;
                border-bottom: 1px solid #EEEEEE;
            }
            QTreeWidget::item:selected {
                background: #B3E5FC;
                color: black;
            }
            QTreeWidget::branch:has-siblings:!adjoins-item {
                border-left: 1px solid #CCCCCC;
            }
            QTreeWidget::branch:has-siblings:adjoins-item {
                border-left: 1px solid #CCCCCC;
            }
            QTreeWidget::branch:!has-siblings:adjoins-item {
                border-left: 1px solid #CCCCCC;
            }
        """)
        layout.addWidget(self.tree)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def _on_filter_toggle(self, checked: bool) -> None:
        self._filter_own = checked
        self.filter_btn.setText("👤 自分のみ ✓" if checked else "👤 自分のみ")
        self.refresh()

    def _on_search(self, text: str) -> None:
        self._search_text = text.strip().lower()
        self.refresh()

    def _own_subtree_ids(self, df: pd.DataFrame) -> set:
        """自分のノード（および配下に自分のノードがある親）のIDXセットを返す"""
        user = self.state.current_user
        own = set(df[df["assigned_to"] == user].index)
        result = set(own)
        for idx in own:
            parent_id = df.loc[idx, "parent_id"] if idx in df.index else None
            while parent_id and parent_id != "0" and parent_id in df.index:
                result.add(parent_id)
                parent_id = df.loc[parent_id, "parent_id"]
        return result

    def _search_subtree_ids(self, df: pd.DataFrame, text: str) -> set:
        """検索テキストに一致するノード（および祖先）のIDXセットを返す"""
        mask = (
            df.get("title", pd.Series(dtype=str)).fillna("").str.lower().str.contains(text, regex=False)
            | df.get("memo", pd.Series(dtype=str)).fillna("").str.lower().str.contains(text, regex=False)
        )
        matched = set(df[mask].index)
        result = set(matched)
        for idx in matched:
            parent_id = df.loc[idx, "parent_id"] if idx in df.index else None
            while parent_id and parent_id != "0" and parent_id in df.index:
                result.add(parent_id)
                parent_id = df.loc[parent_id, "parent_id"]
        return result

    def refresh(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()

        # P0 仮想ルートアイテムを先頭に追加（P1 の親として選択可能）
        p0_item = QTreeWidgetItem(self.tree)
        p0_item.setData(0, Qt.ItemDataRole.UserRole, "0")
        p0_item.setText(0, "[P0] ────────────")
        p0_item.setBackground(0, QColor("#CFD8DC"))
        p0_item.setForeground(0, QColor("#37474F"))
        _f = QFont()
        _f.setBold(True)
        p0_item.setFont(0, _f)

        df = self.state.df_nodes
        if not df.empty:
            own_ids = self._own_subtree_ids(df) if self._filter_own else None
            search_ids = self._search_subtree_ids(df, self._search_text) if self._search_text else None
            if own_ids is not None and search_ids is not None:
                filter_ids = own_ids & search_ids
            elif own_ids is not None:
                filter_ids = own_ids
            elif search_ids is not None:
                filter_ids = search_ids
            else:
                filter_ids = None
            # P1 以下を P0 仮想ルートの子として表示
            self._build_tree(p0_item, df, "0", filter_ids)
        self.tree.expandAll()
        # 選択を復元（シグナルをブロックしたまま実行して update_for_parent の呼び出しを防ぐ）
        # blockSignals(False) を後に移動することで refresh 中に _normalize_priorities が
        # 呼ばれるのを防ぐ（日付編集後に順序が変わるバグの修正）
        if self._selected_idx:
            self._restore_selection(self._selected_idx)
        self.tree.blockSignals(False)

    # 種別ごとの短縮ラベル・背景色・文字色
    _TYPE_LABEL = {
        "project1": "P1", "project2": "P2",
        "project3": "P3", "project4": "P4",
        "task": "Task", "ticket": "Tkt",
    }
    _TYPE_BG = {
        "project1": "#E3F2FD", "project2": "#E8F5E9",
        "project3": "#FFF9C4", "project4": "#F3E5F5",
        "task":     "#ECEFF1", "ticket":   "#FFFFFF",
    }
    _TYPE_FG = {
        "project1": "#1565C0", "project2": "#2E7D32",
        "project3": "#F57F17", "project4": "#6A1B9A",
        "task":     "#37474F", "ticket":   "#546E7A",
    }

    def _build_tree(self, parent_item, df: pd.DataFrame, parent_id: str,
                    filter_ids=None) -> None:
        children = df[df["parent_id"] == parent_id].copy()
        if children.empty:
            return
        children = children.sort_values("priority")
        for idx, row in children.iterrows():
            if filter_ids is not None and idx not in filter_ids:
                continue
            item = QTreeWidgetItem(parent_item)
            node_type = str(row.get("node_type", ""))
            item.setData(0, Qt.ItemDataRole.UserRole, idx)

            # 種別バッジ＋ステータスアイコン＋タイトルを1列に表示
            type_short = self._TYPE_LABEL.get(node_type, node_type)
            status_icon = {"done": "✓", "cancel": "✗",
                           "regularly": "↻", "deleted": "🗑"}.get(row["status"], "")
            label = f"[{type_short}] {status_icon} {row['title']}".strip()
            item.setText(0, label)

            # 種別ごとの背景色・文字色
            type_bg = QColor(self._TYPE_BG.get(node_type, "#FFFFFF"))
            type_fg = QColor(self._TYPE_FG.get(node_type, "#000000"))
            item.setBackground(0, type_bg)
            if row["status"] in ("done", "deleted"):
                item.setForeground(0, QColor("#9E9E9E"))
            else:
                item.setForeground(0, type_fg)

            # P1/P2/Task は太字で強調
            if node_type in ("project1", "project2", "task"):
                f = QFont()
                f.setBold(True)
                item.setFont(0, f)

            self._build_tree(item, df, idx, filter_ids)

    def start_import_queue(self, idxs: list) -> None:
        """AI取込後の確認キューをセットし先頭アイテムへ移動する"""
        self._import_queue = list(idxs)
        self._import_pos = 0
        has_next = len(idxs) > 1
        self._next_btn.setEnabled(has_next)
        self._update_next_btn_label()
        if idxs:
            self._restore_selection(idxs[0])

    def _on_next_import(self) -> None:
        """AI取込キューの次のアイテムへ移動する"""
        self._import_pos += 1
        if self._import_pos >= len(self._import_queue):
            self._import_pos = len(self._import_queue) - 1
        self._restore_selection(self._import_queue[self._import_pos])
        self._update_next_btn_label()
        # 末尾に達したらボタンを無効化
        if self._import_pos >= len(self._import_queue) - 1:
            self._next_btn.setEnabled(False)

    def _update_next_btn_label(self) -> None:
        total = len(self._import_queue)
        pos   = self._import_pos + 1
        self._next_btn.setText(f"次へ → ({pos}/{total})")

    def _restore_selection(self, idx: str) -> None:
        it = self._find_item(self.tree.invisibleRootItem(), idx)
        if it:
            self.tree.setCurrentItem(it)

    def _find_item(self, parent, idx: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) == idx:
                return child
            found = self._find_item(child, idx)
            if found:
                return found
        return None

    def _on_item_changed(self, current, previous) -> None:
        if not current:
            return
        idx = current.data(0, Qt.ItemDataRole.UserRole)
        if idx:
            self._selected_idx = idx
            self.node_selected.emit(idx)
            # Ticketは子ノードを作れない
            node_type = ""
            if idx in self.state.df_nodes.index:
                node_type = str(self.state.df_nodes.loc[idx, "node_type"])
            is_ticket = (node_type == "ticket")
            self._btn_row.set_enabled("＋ 新規", not is_ticket)

    def _on_new(self) -> None:
        """新規ノード作成ダイアログ"""
        parent_idx = self._selected_idx or "0"
        if parent_idx != "0" and parent_idx not in self.state.df_nodes.index:
            parent_idx = "0"
        # 親のタイプから子のタイプを決定
        if parent_idx == "0":
            child_type = "project1"
        else:
            parent_type = self.state.df_nodes.loc[parent_idx, "node_type"]
            child_type = DB.CHILD_TYPE.get(parent_type)  # Ticketはキー無し→None
        if not child_type:
            QMessageBox.information(self, "情報", "これ以上子ノードは作成できません")
            return

        # 同じ親を持つ兄弟の最大 priority + 1 をデフォルトにする
        df = self.state.df_nodes
        siblings = df[df["parent_id"] == parent_idx]
        default_priority = int(siblings["priority"].max()) + 1 if not siblings.empty else 1

        dlg = _NodeEditDialog(
            parent_idx=parent_idx,
            node_type=child_type,
            state=self.state,
            default_priority=default_priority,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ds = dlg.get_series()
            self.state.df_nodes.loc[ds.name] = ds
            # 親ノード（task 以上）には自動チケット生成
            if child_type != "ticket":
                self.state.db.create_auto_children(ds, self.state.user)
                self.state.df_nodes = self.state.db.read_nodes()
            ds["updated_at"] = datetime.date.today().isoformat()
            self.state.db.upsert_node(ds)
            self.state.df_nodes = self.state.db.read_nodes()
            self.state.refresh()

    def _on_delete(self) -> None:
        if not self._selected_idx:
            return
        idx = self._selected_idx
        df = self.state.df_nodes
        if idx not in df.index:
            return
        # 実績工数がある場合は削除不可
        if float(df.loc[idx, "actual_hours"] or 0) > 0:
            QMessageBox.warning(self, "削除不可", "実績工数が記録されているため削除できません")
            return
        # 子ノードがある場合は削除不可
        if not df[df["parent_id"] == idx].empty:
            QMessageBox.warning(self, "削除不可", "子ノードが存在するため削除できません")
            return
        ans = QMessageBox.question(self, "削除確認",
                                   f"「{df.loc[idx, 'title']}」を論理削除しますか？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.state.df_nodes.loc[idx, "status"] = "deleted"
        self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        self.state.db.upsert_node(self.state.df_nodes.loc[idx])
        self.state.df_nodes = self.state.db.read_nodes()
        self.state.refresh()


# ---------- テーブル用デリゲート ----------

class _StatusDelegate(QStyledItemDelegate):
    """ステータス列用デリゲート：セルクリック時にコンボボックスを表示し、自由入力を禁止する"""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems([s for s in DB.STATUS_LIST if s != "deleted"])
        return combo

    def setEditorData(self, editor, index):
        val = index.data(Qt.ItemDataRole.EditRole) or ""
        i = editor.findText(val)
        if i >= 0:
            editor.setCurrentIndex(i)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _DateDelegate(QStyledItemDelegate):
    """日付列用デリゲート：セルクリック時にカレンダーポップアップを表示し、自由入力を禁止する"""

    def createEditor(self, parent, option, index):
        edit = QDateEdit(parent)
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        edit.lineEdit().setReadOnly(True)
        val = index.data(Qt.ItemDataRole.EditRole) or ""
        if val:
            d = QDate.fromString(str(val), "yyyy-MM-dd")
            if d.isValid():
                edit.setDate(d)
            else:
                edit.setDate(QDate.currentDate())
        else:
            edit.setDate(QDate.currentDate())
        return edit

    def setEditorData(self, editor, index):
        val = index.data(Qt.ItemDataRole.EditRole) or ""
        d = QDate.fromString(str(val), "yyyy-MM-dd")
        if d.isValid():
            editor.setDate(d)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.date().toString("yyyy-MM-dd"), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# ---------- 中央ペイン：子一覧テーブル ----------

class TablePane(QWidget):
    """
    子ノード一覧テーブルペイン（Edit 画面の右側）。
    TreePane で選択した親ノードの直下子を一覧表示し、直接編集できる。
    node_selected シグナルでクリックした IDX を DailyScheduleWidget へ通知する。
    """
    node_selected = Signal(str)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._parent_idx: Optional[str] = None
        self._rebuilding: bool = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 現在表示中の親ノードを示すヘッダー
        self.header_label = QLabel("（ノードをツリーから選択してください）")
        self.header_label.setStyleSheet(
            "QLabel { font-weight: bold; color: #37474F; "
            "background: #ECEFF1; padding: 4px 6px; border-radius: 3px; }"
        )
        layout.addWidget(self.header_label)

        # ボタン行
        btn_row = ButtonRow([
            ("＋ 追加",   self._on_add),
            ("✏ 編集",   self._on_edit),
            ("🗑 削除",   self._on_delete),
            ("▲ 上へ",   self._on_move_up),
            ("▼ 下へ",   self._on_move_down),
        ])
        layout.addWidget(btn_row)

        # テーブル（直接編集可）
        COLS = ["タイトル", "順序", "ステータス", "見積(h)", "実績(h)",
                "開始可能日", "納期", "担当者", "メモ"]
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # エクセルライク: 任意キー押下またはシングルクリックで編集開始
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.AnyKeyPressed |
            QAbstractItemView.EditTrigger.DoubleClicked |
            QAbstractItemView.EditTrigger.SelectedClicked
        )
        # ステータス・日付列: シングルクリックでエディタを開く
        self.table.clicked.connect(self._on_cell_clicked_for_edit)
        self.table.itemSelectionChanged.connect(
            lambda: self._on_row_changed(self.table.currentRow())
        )
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #E0E0E0; border: 1px solid #CFD8DC; }"
            "QTableWidget::item:selected { background: #B3E5FC; color: black; }"
        )
        self.table.itemChanged.connect(self._on_item_changed)
        widths = [180, 60, 80, 65, 65, 100, 100, 90, 120]
        for i, w in enumerate(widths):
            self.table.setColumnWidth(i, w)

        # デリゲート設定（Status列=2、日付列=5,6）
        self.table.setItemDelegateForColumn(2, _StatusDelegate(self.table))
        self.table.setItemDelegateForColumn(5, _DateDelegate(self.table))
        self.table.setItemDelegateForColumn(6, _DateDelegate(self.table))

        # Ctrl+Enter で末尾に即時行追加（ダイアログなし）
        QShortcut(QKeySequence("Ctrl+Return"), self.table).activated.connect(self._on_add_quick)

        # Alt+↑↓ でステータス列の値をサイクル
        self.table.installEventFilter(self)

        layout.addWidget(self.table)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def update_for_parent(self, parent_idx: str) -> None:
        self._parent_idx = parent_idx
        # ヘッダーラベルを更新
        df = self.state.df_nodes
        type_labels = {
            "project1": "Project1", "project2": "Project2",
            "project3": "Project3", "project4": "Project4",
            "task": "Task", "ticket": "Ticket",
        }
        if parent_idx == "0":
            # P0 仮想ルートを選択した場合
            self.header_label.setText("[P0] ルート  ▶  子 Project1 一覧")
        elif parent_idx and parent_idx in df.index:
            row = df.loc[parent_idx]
            node_type = str(row.get("node_type", ""))
            title = str(row.get("title", ""))
            type_label = type_labels.get(node_type, node_type)
            child_type = DB.CHILD_TYPE.get(node_type)
            if child_type:
                child_label = type_labels.get(child_type, child_type)
                self.header_label.setText(
                    f"[{type_label}] {title}  ▶  子 {child_label} 一覧"
                )
            else:
                self.header_label.setText(
                    f"[{type_label}] {title}  （子ノード作成不可）"
                )
        else:
            self.header_label.setText("（ノードをツリーから選択してください）")
        # 親が切り替わったときだけ連番化を実行（refresh() 経由では実行しない）
        self._normalize_priorities(parent_idx)
        self._rebuild_table()

    def _normalize_priorities(self, parent_idx: str) -> None:
        """子ノードの priority を 1 から連番に修正して DB に保存する。
        親ノードを選択したときにのみ呼び出す（状態再描画では呼ばない）。"""
        df = self.state.df_nodes
        children = df[df["parent_id"] == parent_idx].copy()
        if children.empty:
            return
        children = children.sort_values("priority")
        needs_save: list = []
        for i, idx in enumerate(children.index):
            expected = i + 1
            if int(children.at[idx, "priority"]) != expected:
                self.state.df_nodes.loc[idx, "priority"] = expected
                self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
                needs_save.append(idx)
        if needs_save and self.state.db:
            for save_idx in needs_save:
                self.state.db.upsert_node(self.state.df_nodes.loc[save_idx])

    def refresh(self) -> None:
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        """選択中の親ノードの子ノードをテーブルに再描画する。シグナルをブロックして再帰更新を防ぐ。"""
        self._rebuilding = True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            if not self._parent_idx:
                return
            df = self.state.df_nodes
            children = df[df["parent_id"] == self._parent_idx].copy()
            if children.empty:
                return
            children = children.sort_values("priority")
            for idx, row in children.iterrows():
                r = self.table.rowCount()
                self.table.insertRow(r)
                vals = [
                    row.get("title", ""),
                    row.get("priority", ""),
                    row.get("status", ""),
                    row.get("estimated_hours", ""),
                    row.get("actual_hours", ""),
                    row.get("start_available", ""),
                    row.get("deadline", ""),
                    self.state.display_name(str(row.get("assigned_to", ""))),
                    row.get("memo", ""),
                ]
                hex_c = COLOR_OPTIONS.get(row.get("color", "Cyan"), "#00BCD4")
                bg = QColor(hex_c)
                bg.setAlpha(60)
                is_own = str(row.get("assigned_to", "")) == self.state.user
                # 実績(h) col=4, 担当者 col=7 は常に読み取り専用
                _READONLY_COLS = {4, 7}
                for c, val in enumerate(vals):
                    item = QTableWidgetItem(str(val) if val is not None else "")
                    if not is_own or c in _READONLY_COLS:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setData(Qt.ItemDataRole.UserRole, idx)
                    if row.get("status") in ("done",):
                        item.setForeground(QColor("#9E9E9E"))
                    else:
                        item.setBackground(bg)
                    self.table.setItem(r, c, item)
        finally:
            self.table.blockSignals(False)
            self._rebuilding = False

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """セル編集時にデータを即時更新する"""
        if self._rebuilding:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not idx or idx not in self.state.df_nodes.index:
            return
        # 自分のノードのみ編集可
        if self.state.df_nodes.loc[idx, "assigned_to"] != self.state.user:
            return
        col_map = {
            0: "title",
            1: "priority",
            2: "status",
            3: "estimated_hours",
            5: "start_available",
            6: "deadline",
            8: "memo",
        }
        col = item.column()
        if col not in col_map:
            return
        field = col_map[col]
        value = item.text().strip()
        try:
            if field == "priority":
                value = int(value)
            elif field == "estimated_hours":
                value = float(value)
            elif field == "status":
                if value not in DB.STATUS_LIST:
                    return
        except (ValueError, TypeError):
            return
        self.state.df_nodes.loc[idx, field] = value
        self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        if self.state.db:
            self.state.db.upsert_node(self.state.df_nodes.loc[idx])
            # スケジュールに影響するフィールドが変更された場合は全体を再計算
            if field in ("status", "estimated_hours", "actual_hours",
                         "start_available", "deadline"):
                self.state.df_nodes = self.state.db.read_nodes()
                self.state.refresh()
        self.info.set_info(f"更新: {field} = {value}")

    def _current_idx(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_row_changed(self, row: int) -> None:
        item = self.table.item(row, 0)
        if item:
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx:
                self.node_selected.emit(idx)

    def _on_add(self) -> None:
        if not self._parent_idx:
            QMessageBox.information(self, "情報", "親ノードをツリーから選択してください")
            return
        if self._parent_idx not in self.state.df_nodes.index and self._parent_idx != "0":
            return
        # P0 仮想ルートの場合は child_type = project1、それ以外は CHILD_TYPE から取得
        if self._parent_idx == "0":
            child_type = "project1"
        else:
            parent_type = self.state.df_nodes.loc[self._parent_idx, "node_type"] \
                if self._parent_idx in self.state.df_nodes.index else None
            child_type = DB.CHILD_TYPE.get(parent_type) if parent_type else None
        if not child_type:
            QMessageBox.information(self, "情報", "Ticketには子ノードを作成できません")
            return
        # テーブルの現在の行数 + 1 をデフォルト priority にする（連番化済みのテーブルと一致）
        default_priority = self.table.rowCount() + 1
        dlg = _NodeEditDialog(
            parent_idx=self._parent_idx,
            node_type=child_type,
            state=self.state,
            default_priority=default_priority,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ds = dlg.get_series()
            self.state.db.upsert_node(ds)
            self.state.df_nodes = self.state.db.read_nodes()
            self.state.refresh()

    def _on_edit(self) -> None:
        idx = self._current_idx()
        if not idx or idx not in self.state.df_nodes.index:
            return
        if self.state.df_nodes.loc[idx, "assigned_to"] != self.state.user:
            QMessageBox.warning(self, "編集不可", "他ユーザーのデータは編集できません")
            return
        dlg = _NodeEditDialog(
            parent_idx=None,
            node_type=None,
            state=self.state,
            edit_idx=idx,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ds = dlg.get_series()
            self.state.df_nodes.loc[ds.name] = ds
            self.state.db.upsert_node(ds)
            self.state.df_nodes = self.state.db.read_nodes()
            self.state.refresh()

    def _on_delete(self) -> None:
        idx = self._current_idx()
        if not idx:
            return
        df = self.state.df_nodes
        if idx not in df.index:
            return
        if float(df.loc[idx, "actual_hours"] or 0) > 0:
            QMessageBox.warning(self, "削除不可", "実績工数が記録されているため削除できません")
            return
        if not df[df["parent_id"] == idx].empty:
            QMessageBox.warning(self, "削除不可", "子ノードが存在するため削除できません")
            return
        ans = QMessageBox.question(self, "削除確認",
                                   f"「{df.loc[idx, 'title']}」を論理削除しますか？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.state.df_nodes.loc[idx, "status"] = "deleted"
        self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        self.state.db.upsert_node(self.state.df_nodes.loc[idx])
        self.state.df_nodes = self.state.db.read_nodes()
        self.state.refresh()

    def _on_move_up(self)   -> None: self._swap_adjacent(-1)
    def _on_move_down(self) -> None: self._swap_adjacent(+1)

    def _swap_adjacent(self, direction: int) -> None:
        """現在行と隣接行の priority を入れ替えて配列の順序を変更し、DB に即時保存する。"""
        row = self.table.currentRow()
        if row < 0:
            return
        target_row = row + direction
        if target_row < 0 or target_row >= self.table.rowCount():
            return

        item_cur = self.table.item(row, 0)
        item_adj = self.table.item(target_row, 0)
        if not item_cur or not item_adj:
            return

        idx_cur = item_cur.data(Qt.ItemDataRole.UserRole)
        idx_adj = item_adj.data(Qt.ItemDataRole.UserRole)
        if not idx_cur or not idx_adj:
            return
        if idx_cur not in self.state.df_nodes.index or idx_adj not in self.state.df_nodes.index:
            return

        # priority を入れ替えて DB に即時保存（state.refresh で巻き戻らないようにする）
        p_cur = int(self.state.df_nodes.loc[idx_cur, "priority"] or 0)
        p_adj = int(self.state.df_nodes.loc[idx_adj, "priority"] or 0)
        today = datetime.date.today().isoformat()

        self.state.df_nodes.loc[idx_cur, "priority"] = p_adj
        self.state.df_nodes.loc[idx_adj, "priority"] = p_cur
        self.state.df_nodes.loc[idx_cur, "updated_at"] = today
        self.state.df_nodes.loc[idx_adj, "updated_at"] = today

        if self.state.db:
            self.state.db.upsert_node(self.state.df_nodes.loc[idx_cur])
            self.state.db.upsert_node(self.state.df_nodes.loc[idx_adj])

        self._rebuild_table()

        # 移動後に idx_cur の行を選択する
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == idx_cur:
                self.table.setCurrentCell(r, 0)
                break
        self.table.blockSignals(False)

    def _on_cell_clicked_for_edit(self, index) -> None:
        """ステータス(2)・開始可能日(5)・納期(6) 列はシングルクリックでエディタを開く"""
        if index.column() in {2, 5, 6}:
            item = self.table.item(index.row(), index.column())
            if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                self.table.edit(index)

    def _on_add_quick(self) -> None:
        """Ctrl+Enter: ダイアログなしで末尾に新しい行を即時追加する"""
        if not self._parent_idx:
            return
        if self._parent_idx not in self.state.df_nodes.index and self._parent_idx != "0":
            return
        parent_type = (
            self.state.df_nodes.loc[self._parent_idx, "node_type"]
            if self._parent_idx in self.state.df_nodes.index
            else "project1"
        )
        child_type = DB.CHILD_TYPE.get(parent_type)
        if not child_type:
            self.info.set_info("Ticket には子ノードを作成できません")
            return
        # 既存アイテムの priority を連番化してから末尾の優先度を求める
        # （priority=99 のまま放置されているアイテムがある場合に正しい末尾番号を得るため）
        self._normalize_priorities(self._parent_idx)
        df = self.state.df_nodes
        siblings = df[df["parent_id"] == self._parent_idx]
        max_priority = int(siblings["priority"].max()) + 1 if not siblings.empty else 1
        ds = DB.create_initial_node(
            owner=self.state.user,
            node_type=child_type,
            title="",
            parent_id=self._parent_idx,
            priority=max_priority,
        )
        ds["estimated_hours"] = 1.0
        ds["status"] = "todo"
        new_idx = ds.name
        self.state.db.upsert_node(ds)
        self.state.df_nodes = self.state.db.read_nodes()
        self._rebuild_table()
        # 追加した行のタイトル列に自動フォーカス
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == new_idx:
                self.table.setCurrentCell(r, 0)
                self.table.editItem(it)
                break

    def eventFilter(self, obj, event) -> bool:
        """Alt+↑↓ でステータス列の値をサイクルする"""
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mod = event.modifiers()
            if mod & Qt.KeyboardModifier.AltModifier and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self.table.currentRow()
                col = self.table.currentColumn()
                if col == 2:  # Status 列
                    item = self.table.item(row, col)
                    if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                        status_list = [s for s in DB.STATUS_LIST if s != "deleted"]
                        cur = item.text().strip()
                        i = status_list.index(cur) if cur in status_list else 0
                        if key == Qt.Key.Key_Up:
                            i = (i - 1) % len(status_list)
                        else:
                            i = (i + 1) % len(status_list)
                        item.setText(status_list[i])
                        return True
        return super().eventFilter(obj, event)


# ---------- 右ペイン：詳細フォームのみ ----------

class DetailPane(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self._node_idx: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ノード詳細フォーム
        self.form_area = QScrollArea()
        self.form_area.setWidgetResizable(True)
        self.form_area.setFrameShape(QFrame.Shape.NoFrame)
        form_widget = QWidget()
        self.form = QFormLayout(form_widget)
        self.form.setSpacing(4)
        self.form_area.setWidget(form_widget)
        layout.addWidget(self.form_area, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def update_for_node(self, idx: str) -> None:
        self._node_idx = idx
        self._rebuild_form(idx)

    def refresh(self) -> None:
        if self._node_idx:
            self._rebuild_form(self._node_idx)

    def _rebuild_form(self, idx: str) -> None:
        """選択ノードのフィールドをフォームに表示する"""
        # フォームをクリア
        while self.form.rowCount():
            self.form.removeRow(0)
        if not idx or idx not in self.state.df_nodes.index:
            return
        row = self.state.df_nodes.loc[idx]
        fields = [
            ("種類",       row.get("node_type", "")),
            ("タイトル",   row.get("title", "")),
            ("ステータス", row.get("status", "")),
            ("順序",       row.get("priority", "")),
            ("担当者",     self.state.display_name(str(row.get("assigned_to", "")))),
            ("見積工数(h)", row.get("estimated_hours", "")),
            ("実績工数(h)", row.get("actual_hours", "")),
            ("開始可能日", row.get("start_available", "")),
            ("納期",       row.get("deadline", "")),
            ("実績開始日", row.get("actual_start", "")),
            ("実績完了日", row.get("actual_end", "")),
            ("表示色",     row.get("color", "")),
            ("メモ",       row.get("memo", "")),
        ]
        for label, val in fields:
            lbl = QLabel(str(val) if val is not None else "")
            lbl.setWordWrap(True)
            self.form.addRow(f"{label}:", lbl)


# ---------- ノード編集ダイアログ ----------

class _NodeEditDialog(QDialog):
    def __init__(self, parent_idx: Optional[str], node_type: Optional[str],
                 state, edit_idx: Optional[str] = None,
                 default_priority: int = 99, parent=None):
        super().__init__(parent)
        self.state = state
        self._parent_idx = parent_idx
        self._node_type = node_type
        self._edit_idx = edit_idx

        is_edit = edit_idx is not None and edit_idx in state.df_nodes.index
        self.setWindowTitle("ノード編集" if is_edit else "新規作成")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.f_title = QLineEdit()
        self.f_priority = QSpinBox()
        self.f_priority.setRange(1, 999)
        # 新規作成時はデフォルト priority を使用（編集時は既存値で上書き）
        self.f_priority.setValue(default_priority)
        self.f_status = QComboBox()
        self.f_status.addItems(DB.STATUS_LIST[:-1])  # deleted 除外
        self.f_est = QDoubleSpinBox()
        self.f_est.setRange(0, 9999)
        self.f_est.setDecimals(2)
        self.f_est.setSingleStep(0.25)
        self.f_start = QLineEdit()
        self.f_start.setPlaceholderText("YYYY-MM-DD")
        self.f_deadline = QLineEdit()
        self.f_deadline.setPlaceholderText("YYYY-MM-DD")
        self.f_color = ColorCombo()
        self.f_memo = QTextEdit()
        self.f_memo.setMaximumHeight(80)

        form.addRow("タイトル *:",   self.f_title)
        form.addRow("順序:",         self.f_priority)
        form.addRow("ステータス:",   self.f_status)
        form.addRow("見積工数(h):",  self.f_est)
        form.addRow("開始可能日:",   self.f_start)
        form.addRow("納期:",         self.f_deadline)
        form.addRow("表示色:",       self.f_color)
        form.addRow("メモ:",         self.f_memo)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # 編集時は既存値を読み込む
        if is_edit:
            row = state.df_nodes.loc[edit_idx]
            self.f_title.setText(str(row.get("title", "")))
            self.f_priority.setValue(int(row.get("priority", 99)))
            si = self.f_status.findText(str(row.get("status", "todo")))
            if si >= 0:
                self.f_status.setCurrentIndex(si)
            self.f_est.setValue(float(row.get("estimated_hours", 0)))
            self.f_start.setText(str(row.get("start_available", "") or ""))
            self.f_deadline.setText(str(row.get("deadline", "") or ""))
            self.f_color.set_color(str(row.get("color", "Cyan")))
            self.f_memo.setPlainText(str(row.get("memo", "")))

    def _on_accept(self) -> None:
        if not self.f_title.text().strip():
            QMessageBox.warning(self, "入力エラー", "タイトルを入力してください")
            return
        self.accept()

    def get_series(self) -> pd.Series:
        """ダイアログの入力値から pd.Series を返す"""
        if self._edit_idx and self._edit_idx in self.state.df_nodes.index:
            ds = self.state.df_nodes.loc[self._edit_idx].copy()
            ds.name = self._edit_idx
        else:
            ds = DB.create_initial_node(
                owner=self.state.user,
                node_type=self._node_type or "ticket",
                title="",
                parent_id=self._parent_idx or "0",
            )
        ds["title"]           = self.f_title.text().strip()
        ds["priority"]        = self.f_priority.value()
        ds["status"]          = self.f_status.currentText()
        ds["estimated_hours"] = self.f_est.value()
        ds["start_available"] = self.f_start.text().strip() or None
        ds["deadline"]        = self.f_deadline.text().strip() or None
        ds["color"]           = self.f_color.current_color()
        ds["memo"]            = self.f_memo.toPlainText()
        ds["updated_at"]      = datetime.date.today().isoformat()
        return ds
