"""
ui_sub.py - ガントチャート・ロードマップ・分析・検索・チームログ・依頼・メモ・バージョン・Config画面
"""
import calendar
import configparser
import datetime
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QHeaderView, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QScrollArea, QFrame,
    QMessageBox, QFormLayout, QSplitter, QFileDialog, QGroupBox,
    QSizePolicy, QRadioButton, QButtonGroup, QCalendarWidget, QMenu,
    QDialog, QDialogButtonBox, QSpinBox, QDoubleSpinBox,
    QTreeWidget, QTreeWidgetItem, QStyledItemDelegate, QPlainTextEdit,
    QApplication,
)
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QColor, QFont, QAction, QCursor, QPen
from PySide6.QtWidgets import QToolTip

import db as DB
import logic as LG
from ui_widgets import (
    DateButton, UserCombo, ButtonRow, InfoLabel, AutoCombo,
    ScrollableTable, Separator, COLOR_OPTIONS, STYLE_BUTTON,
)


# ---------- 項目4: ガントチャートセル用デリゲート ----------

class _GanttCellDelegate(QStyledItemDelegate):
    """開始可能日セルの左側に縦線を描画するデリゲート"""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.data(Qt.ItemDataRole.UserRole + 10) == "start_avail":
            painter.save()
            pen = QPen(QColor("#2E7D32"))
            pen.setWidth(3)
            painter.setPen(pen)
            r = option.rect
            painter.drawLine(r.topLeft(), r.bottomLeft())
            painter.restore()


# ---------- ガントチャート ----------

class GanttView(QWidget):
    """
    ガントチャート：Task グループ別・横日付バー表示。
    左端の DailyScheduleWidget と連動して、シングルクリックで割り当て可能。
    EDF スケジューリングで算出した作業日を 🔨 マーカーで表示し、
    開始可能日は左縦線、納期は 🏁 で示す。
    """
    ticket_clicked    = Signal(str)  # チケット行クリック時に IDX を送出
    edit_requested    = Signal(str)  # Edit メニュー選択時に IDX を送出
    request_requested = Signal(str)  # 項目3: Request メニュー選択時に IDX を送出

    _FIXED_COLS = 5   # 種別/タイトル/ステータス/担当者/見積h
    _COL_WIDTH_DATE = 28  # 日付列の幅(px)

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._date_range: list = []   # 表示日付リスト (datetime.date)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("📊 ガントチャート（Task グループ別・日付バー表示）"))
        layout.addWidget(Separator())

        # ステータス選択ラジオボタン行（"doing" は除外）
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("表示ステータス:"))
        self._status_group = QButtonGroup(self)
        self._status_radios = {}
        for key, label in [("all", "全て"), ("todo", "todo"), ("regularly", "regularly"),
                            ("done", "done"), ("cancel", "cancel")]:
            rb = QRadioButton(label)
            self._status_group.addButton(rb)
            self._status_radios[key] = rb
            status_row.addWidget(rb)
        # デフォルト: todo のみ表示
        self._status_radios["todo"].setChecked(True)
        self._status_group.buttonClicked.connect(lambda _: self._rebuild_table())
        status_row.addStretch()
        layout.addLayout(status_row)

        # フィルター行（Project・担当者・期間）
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Project:"))
        self.pj_combo = QComboBox()
        self.pj_combo.setMinimumWidth(150)
        self.pj_combo.addItem("（全て）", userData="")
        self.pj_combo.currentIndexChanged.connect(self._rebuild_table)
        filter_row.addWidget(self.pj_combo)

        filter_row.addWidget(QLabel(" 期間:"))
        self.from_btn = DateButton(initial_date=datetime.date.today().isoformat())
        self.to_btn = DateButton(
            initial_date=(datetime.date.today() + datetime.timedelta(days=59)).isoformat()
        )
        filter_row.addWidget(self.from_btn)
        filter_row.addWidget(QLabel("〜"))
        filter_row.addWidget(self.to_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # シグナル接続
        self.from_btn.date_changed.connect(self._rebuild_table)
        self.to_btn.date_changed.connect(self._rebuild_table)

        # テーブル
        self.table = QTableWidget(0, self._FIXED_COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #E0E0E0; border: 1px solid #CFD8DC; }"
            "QTableWidget::item:selected { background: #B3E5FC; color: black; }"
        )
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.cellEntered.connect(self._on_cell_entered)
        # 項目4: 開始可能日セルの左側縦線を描画するデリゲートを設定
        self.table.setItemDelegate(_GanttCellDelegate(self.table))
        layout.addWidget(self.table, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def _get_status_filter(self) -> str:
        """選択中のステータスフィルターを返す（"all" or ステータス名）"""
        for key, rb in self._status_radios.items():
            if rb.isChecked():
                return key
        return "all"

    def _compute_all_schedules(self, df: pd.DataFrame, member: str) -> dict:
        """
        担当者の全チケット（全Task・全Project横断）を対象に EDF＋整合どりスケジューリングを行う。

        アルゴリズム:
          ① 各Task配下のチケットごとに仮納期を逆算（fill_deadlines_backward）
             → 最後のチケットに納期がなければ親Taskの納期を使用
          ② 全チケットをまとめてEDFソート（仮納期昇順）
          ③ 1日の作業時間（Configで設定）を使ってグローバルに作業日を割り当て
             → 開始可能日制約で飛ばしたチケットは優先1で再チェック
          ④ 作業日が納期より遅い場合はアラート（呼び出し側でd > deadlineで判定）

        Returns:
            {ticket_idx: (work_days: set[date], start_avail: date|None, deadline: date|None)}
        """
        daily_h = max(0.25, float(self.state.config.daily_task_hour))
        today = datetime.date.today()

        holidays_upper = {h.strip().upper() for h in self.state.config.holidays}
        day_abbrevs = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

        def is_holiday(d: datetime.date) -> bool:
            return day_abbrevs[d.weekday()] in holidays_upper

        def parse_date(val) -> Optional[datetime.date]:
            if val and str(val) not in ("", "nan", "None"):
                try:
                    return datetime.date.fromisoformat(str(val))
                except Exception:
                    pass
            return None

        import logic as LG

        # ① 全Taskを横断して担当者のチケットを収集し、Taskごとに仮納期を計算
        # done/cancel のTaskは完了済みのため除外（配下のチケットもスケジュール不要）
        all_tasks = df[
            (df["node_type"] == "task")
            & (~df["status"].isin(["done", "cancel", "deleted"]))
        ]

        collected: list[pd.DataFrame] = []
        for task_idx in all_tasks.index:
            # このタスク配下で担当者のチケット（done/cancel/deleted除外）
            # done は既に完了済みのため未来の作業スロットを消費しない
            tickets = df[
                (df["parent_id"] == task_idx)
                & (df["node_type"] == "ticket")
                & (~df["status"].isin(["done", "cancel", "deleted"]))
                & (df.get("assigned_to", pd.Series(dtype=str)) == member)
            ].copy()
            if tickets.empty:
                continue

            # 親タスクの納期（最後のチケットに納期がない場合のフォールバック）
            parent_dl = parse_date(df.loc[task_idx].get("deadline")) if task_idx in df.index else None

            # 仮納期を填入
            tickets = LG.fill_deadlines_backward(tickets, daily_h, parent_deadline=parent_dl)

            # 同一Task内で開始可能日を後方チケットへ伝播
            # 例) A:3/3, B:-(なし), C:3/7, D:-(なし) → B:3/3, D:3/7
            tickets = tickets.sort_values("priority")
            running_start: Optional[datetime.date] = None
            for t_idx in tickets.index:
                raw = tickets.loc[t_idx, "start_available"]
                explicit: Optional[datetime.date] = None
                if raw and str(raw) not in ("", "nan", "None"):
                    try:
                        explicit = datetime.date.fromisoformat(str(raw))
                    except Exception:
                        pass
                if explicit is not None:
                    effective = max(running_start, explicit) if running_start else explicit
                    running_start = effective
                    tickets.loc[t_idx, "start_available"] = effective.isoformat()
                elif running_start is not None:
                    tickets.loc[t_idx, "start_available"] = running_start.isoformat()

            collected.append(tickets)

        if not collected:
            return {}

        # ② 全チケットをマージしてEDFソート
        all_tickets = pd.concat(collected)
        all_tickets["_dl_sort"] = pd.to_datetime(all_tickets["deadline"], errors="coerce")
        all_tickets = all_tickets.sort_values(["_dl_sort", "priority"], na_position="last")
        edf_order = list(all_tickets.index)

        # ③ 整合どりスケジューリング（全チケット横断・1日作業時間=daily_h）
        unscheduled = list(edf_order)
        skipped: list = []
        cursor = today
        cursor_h = 0.0
        result = {}

        while unscheduled or skipped:
            candidates = skipped + [t for t in unscheduled if t not in skipped]

            scheduled_any = False

            for t_idx in candidates:
                if t_idx not in all_tickets.index:
                    if t_idx in skipped:     skipped.remove(t_idx)
                    if t_idx in unscheduled: unscheduled.remove(t_idx)
                    scheduled_any = True
                    break

                tr = all_tickets.loc[t_idx]
                start_avail = parse_date(tr.get("start_available"))
                deadline    = parse_date(tr.get("deadline"))

                if start_avail and cursor < start_avail:
                    if t_idx not in skipped:
                        skipped.append(t_idx)
                    if t_idx in unscheduled:
                        unscheduled.remove(t_idx)
                    continue

                if t_idx in skipped:     skipped.remove(t_idx)
                if t_idx in unscheduled: unscheduled.remove(t_idx)

                est_h      = float(tr.get("estimated_hours", 0) or 0)
                act_h      = float(tr.get("actual_hours", 0) or 0)
                remaining_h = max(0.0, est_h - act_h)

                work_days: set = set()
                if remaining_h > 0.001:
                    h_left   = remaining_h
                    d        = cursor
                    h_in_day = cursor_h
                    itr      = 0
                    while h_left > 0.001 and itr < 1000:
                        if is_holiday(d):
                            d += datetime.timedelta(days=1); h_in_day = 0.0; itr += 1; continue
                        avail = daily_h - h_in_day
                        if avail <= 0.001:
                            d += datetime.timedelta(days=1); h_in_day = 0.0; itr += 1; continue
                        used = min(avail, h_left)
                        work_days.add(d)
                        h_left -= used; h_in_day += used
                        if h_in_day >= daily_h - 0.001:
                            d += datetime.timedelta(days=1); h_in_day = 0.0
                        itr += 1
                    cursor   = d
                    cursor_h = h_in_day

                result[t_idx] = (work_days, start_avail, deadline)
                scheduled_any = True
                break

            if not scheduled_any:
                all_pending = list(dict.fromkeys(skipped + unscheduled))
                next_dates  = [
                    parse_date(all_tickets.loc[t, "start_available"])
                    for t in all_pending if t in all_tickets.index
                ]
                next_dates = [nd for nd in next_dates if nd is not None]
                if next_dates:
                    cursor   = min(next_dates)
                    cursor_h = 0.0
                else:
                    break

        return result

    def refresh(self) -> None:
        df = self.state.df_nodes
        # Project1 コンボ更新
        self.pj_combo.blockSignals(True)
        cur = self.pj_combo.currentData()
        self.pj_combo.clear()
        self.pj_combo.addItem("（全て）", userData="")
        for idx, row in df[df["node_type"] == "project1"].iterrows():
            self.pj_combo.addItem(str(row["title"]), userData=idx)
        for i in range(self.pj_combo.count()):
            if self.pj_combo.itemData(i) == cur:
                self.pj_combo.setCurrentIndex(i)
                break
        self.pj_combo.blockSignals(False)
        self._rebuild_table()

    def _date_range_list(self) -> list:
        """from_btn から to_btn までの日付リストを返す"""
        try:
            d_from = datetime.date.fromisoformat(self.from_btn.get_date())
            d_to   = datetime.date.fromisoformat(self.to_btn.get_date())
        except ValueError:
            d_from = datetime.date.today()
            d_to   = d_from + datetime.timedelta(days=59)
        if d_to < d_from:
            d_to = d_from + datetime.timedelta(days=59)
        days = min((d_to - d_from).days + 1, 120)  # 最大120日
        return [d_from + datetime.timedelta(days=i) for i in range(days)]

    def _get_ancestor_pj1(self, df, idx):
        parent_id = df.loc[idx, "parent_id"] if idx in df.index else None
        while parent_id and parent_id != "0" and parent_id in df.index:
            if df.loc[parent_id, "node_type"] == "project1":
                return parent_id
            parent_id = df.loc[parent_id, "parent_id"]
        return None

    def _get_parent_path(self, df, task_idx):
        parts = []
        parent_id = df.loc[task_idx, "parent_id"] if task_idx in df.index else None
        while parent_id and parent_id != "0" and parent_id in df.index:
            parts.append(str(df.loc[parent_id, "title"]))
            parent_id = df.loc[parent_id, "parent_id"]
        parts.reverse()
        return " > ".join(parts) if parts else ""

    def _rebuild_table(self) -> None:
        """
        ガントチャートテーブルを再構築する。

        処理フロー:
          1. 表示期間の日付列を生成（固定 5 列 + 日付列）
          2. 担当者の全チケットを EDF スケジューリング（_compute_all_schedules）
          3. Task ヘッダー行を挿入（背景色・ツールチップ付き）
          4. 各チケット行の日付セルに 🚩/🔨/開始可能日マーカーを設定
          5. 納期超過チケットは赤色でアラート表示
        """
        df = self.state.df_nodes
        self._date_range = self._date_range_list()
        date_list = self._date_range

        # カラム設定（固定情報列 + 日付列）
        total_cols = self._FIXED_COLS + len(date_list)
        self.table.setColumnCount(total_cols)
        headers = ["種別", "タイトル", "ステータス", "担当者", "見積h"]
        for d in date_list:
            headers.append(f"{d.month}/{d.day}\n{['月','火','水','木','金','土','日'][d.weekday()]}")
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(4, 45)
        for c in range(self._FIXED_COLS, total_cols):
            self.table.setColumnWidth(c, self._COL_WIDTH_DATE)
        # 列幅はマウス操作で変更可能（Interactive）
        for c in range(total_cols):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)

        self.table.setRowCount(0)
        if df.empty:
            return

        filter_pj = self.pj_combo.currentData() or ""
        filter_member = self.state.current_member or ""
        filter_status = self._get_status_filter()

        # 担当者の全チケットを横断してグローバルにスケジューリング（一括計算）
        # 戻り値: {ticket_idx: (work_days: set, start_avail, deadline)}
        global_schedule = self._compute_all_schedules(df, filter_member)

        tasks = df[
            (df["node_type"] == "task")
            & (~df["status"].isin(["deleted"]))
        ].sort_values("priority")

        # 各 Task の配下チケットの最早作業日を求め、作業開始が早い Task 順に表示
        def _earliest_work_day(task_idx):
            child_idxs = df[
                (df["parent_id"] == task_idx)
                & (df["node_type"] == "ticket")
            ].index.tolist()
            earliest = None
            for ci in child_idxs:
                wd_set, _, _ = global_schedule.get(ci, (set(), None, None))
                if wd_set:
                    m = min(wd_set)
                    if earliest is None or m < earliest:
                        earliest = m
            return earliest if earliest is not None else datetime.date.max

        sorted_task_idxs = sorted(tasks.index, key=_earliest_work_day)
        tasks = tasks.loc[sorted_task_idxs]

        task_count = 0
        ticket_count = 0

        for task_idx, task_row in tasks.iterrows():
            if filter_pj and self._get_ancestor_pj1(df, task_idx) != filter_pj:
                continue

            # このタスク配下でスケジュール済みチケットを取得（EDF順を維持）
            task_ticket_idxs = df[
                (df["parent_id"] == task_idx)
                & (df["node_type"] == "ticket")
                & (~df["status"].isin(["cancel", "deleted"]))
            ].sort_values("priority").index.tolist()

            # フィルター後の表示チケットを収集（global_scheduleに含まれるもの優先）
            schedule_dict = global_schedule  # 参照を共有

            visible_tickets = []
            for t_idx in task_ticket_idxs:
                if t_idx not in df.index:
                    continue
                tr = df.loc[t_idx]
                t_status = str(tr.get("status", ""))
                if filter_member and str(tr.get("assigned_to", "")) != filter_member:
                    continue
                if filter_status != "all" and t_status != filter_status:
                    continue
                visible_tickets.append(t_idx)

            # 表示対象チケットがない場合はタスク行もスキップ
            if not visible_tickets:
                continue

            # ── Task ヘッダー行を挿入（Project 階層パスをタイトルに付記）──
            parent_path = self._get_parent_path(df, task_idx)
            title = str(task_row.get("title", ""))
            if parent_path:
                title = f"{title}  [{parent_path}]"
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setRowHeight(r, 22)

            task_hex = COLOR_OPTIONS.get(str(task_row.get("color", "Yellow")), "#FFA726")
            task_bg = QColor(task_hex)
            task_bg.setAlpha(140)
            bold = QFont(); bold.setBold(True)

            # ── Task ツールチップ生成 ──
            task_dl_raw = task_row.get("deadline")
            task_dl_str = str(task_dl_raw) if task_dl_raw and str(task_dl_raw) not in ("", "nan", "None") else ""
            # 子チケットの工数を積算
            child_est = sum(
                float(df.loc[ci_, "estimated_hours"] or 0)
                for ci_ in task_ticket_idxs if ci_ in df.index
            )
            child_act = sum(
                float(df.loc[ci_, "actual_hours"] or 0)
                for ci_ in task_ticket_idxs if ci_ in df.index
            )
            task_tip_lines = [
                f"納期      : {task_dl_str}" if task_dl_str else "納期      : —",
                f"親        : {parent_path}" if parent_path else "親        : —",
                f"見積工数  : {child_est:.1f}h  / 実績: {child_act:.1f}h",
            ]
            task_tooltip = "\n".join(task_tip_lines)

            for c, val in enumerate(["── Task ──", title,
                                      str(task_row.get("status", "")),
                                      self.state.display_name(str(task_row.get("assigned_to", ""))),
                                      f"{float(task_row.get('estimated_hours', 0) or 0):.1f}"]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, task_idx)
                item.setData(Qt.ItemDataRole.ToolTipRole, task_tooltip)
                item.setBackground(task_bg)
                item.setFont(bold)
                self.table.setItem(r, c, item)

            # Task 行の日付列: 薄く塗る
            task_start = None
            task_end   = None
            if task_row.get("start_available"):
                try: task_start = datetime.date.fromisoformat(str(task_row["start_available"]))
                except: pass
            if task_row.get("deadline"):
                try: task_end = datetime.date.fromisoformat(str(task_row["deadline"]))
                except: pass
            for ci, d in enumerate(date_list):
                item = QTableWidgetItem("")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, task_idx)
                item.setData(Qt.ItemDataRole.ToolTipRole, task_tooltip)
                if task_start and task_end and task_start <= d <= task_end:
                    bg2 = QColor(task_hex); bg2.setAlpha(40)
                    item.setBackground(bg2)
                elif d.weekday() >= 5:
                    item.setBackground(QColor("#F0F0F0"))
                self.table.setItem(r, self._FIXED_COLS + ci, item)

            task_count += 1

            # ── Ticket 行 ──
            for t_idx in visible_tickets:
                tr = df.loc[t_idx]
                t_status = str(tr.get("status", ""))
                r2 = self.table.rowCount()
                self.table.insertRow(r2)
                self.table.setRowHeight(r2, 20)

                t_hex = COLOR_OPTIONS.get(str(tr.get("color", "Cyan")), "#00BCD4")
                t_bg = QColor(t_hex); t_bg.setAlpha(50)
                bar_color = QColor(t_hex); bar_color.setAlpha(180)

                status_icon = {"done": "✓", "cancel": "✗", "regularly": "↻"}.get(t_status, "")
                est_h = float(tr.get("estimated_hours", 0) or 0)
                act_h = float(tr.get("actual_hours", 0) or 0)

                # ── ツールチップ生成（固定列ループの前に計算）──
                work_days, start_avail, deadline = schedule_dict.get(t_idx, (set(), None, None))

                orig_dl_raw = tr.get("deadline")
                orig_dl = str(orig_dl_raw) if orig_dl_raw and str(orig_dl_raw) not in ("", "nan", "None") else ""

                sa_str = str(tr.get("start_available", "") or "")
                if sa_str in ("nan", "None"):
                    sa_str = ""

                memo_str = str(tr.get("memo", "") or "")
                if memo_str in ("nan", "None"):
                    memo_str = ""

                tip_lines = [
                    f"担当      : {self.state.display_name(str(tr.get('assigned_to', '')))}",
                    f"開始可能日: {sa_str}" if sa_str else "開始可能日: —",
                    f"納期      : {orig_dl}" if orig_dl else "納期      : —",
                    f"見積工数  : {est_h:.1f}h  / 実績: {act_h:.1f}h",
                ]
                if memo_str:
                    tip_lines.append(f"Memo      : {memo_str}")
                tooltip = "\n".join(tip_lines)

                # ── 固定列を作成（tooltip をアイテム生成時に直接設定）──
                for c, val in enumerate([
                    f"  {status_icon} Ticket",
                    f"  {tr.get('title', '')}",
                    t_status,
                    self.state.display_name(str(tr.get("assigned_to", ""))),
                    f"{est_h:.1f}",
                ]):
                    item = QTableWidgetItem(val)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setData(Qt.ItemDataRole.UserRole, t_idx)
                    item.setData(Qt.ItemDataRole.ToolTipRole, tooltip)
                    if t_status == "done":
                        item.setForeground(QColor("#9E9E9E"))
                    else:
                        item.setBackground(t_bg)
                    self.table.setItem(r2, c, item)

                # ▶（開始可能日）/ 🏁（納期）/ 🔨（作業日）マーカー
                for ci, d in enumerate(date_list):
                    item = QTableWidgetItem("")
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setData(Qt.ItemDataRole.UserRole, t_idx)

                    if t_status == "regularly":
                        # regularly: スケジュール表示は空白（マーカーなし）
                        if d.weekday() >= 5:
                            item.setBackground(QColor("#F0F0F0"))
                        else:
                            item.setBackground(t_bg)
                    elif t_status not in ("done", "cancel"):
                        # セルのマーカー優先順: 納期🏁 > 開始可能日（左縦線）> 作業日🔨
                        # 背景色は通常のチケット色と統一し、色の多用を避ける
                        if deadline and d == deadline:
                            item.setText("🏁")
                            item.setBackground(t_bg)
                        elif start_avail and d == start_avail:
                            # 開始可能日: テキストなし、左縦線デリゲートで表示
                            item.setData(Qt.ItemDataRole.UserRole + 10, "start_avail")
                            item.setBackground(t_bg)
                        elif d in work_days:
                            item.setText("🔨")
                            if deadline and d > deadline:
                                # 作業日が納期を超過 → 赤アラート
                                item.setBackground(QColor("#EF5350"))
                                item.setForeground(QColor("white"))
                            else:
                                item.setBackground(bar_color)
                        elif d.weekday() >= 5:
                            item.setBackground(QColor("#F0F0F0"))
                        else:
                            item.setBackground(t_bg)
                    else:
                        if d.weekday() >= 5:
                            item.setBackground(QColor("#F0F0F0"))
                        if t_status == "done":
                            item.setForeground(QColor("#9E9E9E"))

                    item.setData(Qt.ItemDataRole.ToolTipRole, tooltip)
                    self.table.setItem(r2, self._FIXED_COLS + ci, item)

                ticket_count += 1

        self.info.set_info(
            f"Task {task_count} 件 / Ticket {ticket_count} 件  "
            f"({date_list[0]} 〜 {date_list[-1]})"
        )

    def _on_cell_entered(self, row: int, col: int) -> None:
        """セルにマウスが入ったときツールチップを表示する"""
        item = self.table.item(row, col)
        if item:
            tip = item.data(Qt.ItemDataRole.ToolTipRole)
            if tip:
                QToolTip.showText(QCursor.pos(), tip, self.table)
                return
        QToolTip.hideText()

    def _on_cell_clicked(self, row: int, col: int) -> None:
        """セルクリック → Ticket IDX を ticket_clicked として送出"""
        item = self.table.item(row, 0)
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx and idx in self.state.df_nodes.index:
            if self.state.df_nodes.loc[idx, "node_type"] == "ticket":
                self.ticket_clicked.emit(idx)

    def _on_context_menu(self, pos) -> None:
        """チケット行の右クリックメニュー"""
        item = self.table.itemAt(pos)
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not idx or idx not in self.state.df_nodes.index:
            return
        if self.state.df_nodes.loc[idx, "node_type"] != "ticket":
            return

        # 項目3: 担当者チェック前にIDXを取得し、チケットであれば全てメニューを表示
        assigned_to = str(self.state.df_nodes.loc[idx, "assigned_to"])
        is_own = (assigned_to == self.state.user)

        # 自分以外のチケットの場合はRequestのみ表示
        if not is_own:
            menu = QMenu(self)
            menu.addSeparator()
            menu.addAction(QAction("📨 Request", self,
                                    triggered=lambda: self.request_requested.emit(idx)))
            menu.exec(self.table.viewport().mapToGlobal(pos))
            return

        def _change_date(field: str) -> None:
            """日付選択ダイアログを表示して値を更新する"""
            dlg = QDialog(self)
            dlg.setWindowTitle("日付選択")
            layout = QVBoxLayout(dlg)
            cal = QCalendarWidget()
            cur_val = self.state.df_nodes.loc[idx, field]
            if cur_val:
                try:
                    d = datetime.date.fromisoformat(str(cur_val))
                    cal.setSelectedDate(QDate(d.year, d.month, d.day))
                except Exception:
                    pass
            layout.addWidget(cal)
            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            layout.addWidget(btns)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                qd = cal.selectedDate()
                new_date = datetime.date(qd.year(), qd.month(), qd.day()).isoformat()
                self.state.df_nodes.loc[idx, field] = new_date
                self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
                if self.state.db:
                    self.state.db.upsert_node(self.state.df_nodes.loc[idx])
                    self.state.df_nodes = self.state.db.read_nodes()
                self.state.refresh()

        def _set_status(s: str) -> None:
            self.state.df_nodes.loc[idx, "status"] = s
            self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
            if self.state.db:
                self.state.db.upsert_node(self.state.df_nodes.loc[idx])
                self.state.df_nodes = self.state.db.read_nodes()
            self.state.refresh()

        def _delete_ticket() -> None:
            title = self.state.df_nodes.loc[idx, "title"]
            ans = QMessageBox.question(self, "削除確認",
                                       f"「{title}」を論理削除しますか？")
            if ans == QMessageBox.StandardButton.Yes:
                _set_status("deleted")

        # 自分のチケット: 全メニューを表示
        menu = QMenu(self)
        menu.addAction(QAction("✏ Edit", self, triggered=lambda: self.edit_requested.emit(idx)))
        menu.addSeparator()
        menu.addAction(QAction("📅 開始可能日変更", self,
                                triggered=lambda: _change_date("start_available")))
        menu.addAction(QAction("🏁 納期変更", self,
                                triggered=lambda: _change_date("deadline")))
        menu.addSeparator()
        for s_key, s_label in [("todo", "☐ ToDo"), ("done", "✓ Done"),
                                ("cancel", "✗ Cancel"), ("regularly", "↻ Regularly")]:
            act = QAction(s_label, self)
            act.triggered.connect(lambda checked=False, s=s_key: _set_status(s))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(QAction("🗑 Delete", self, triggered=_delete_ticket))
        # 項目3: Requestメニューを追加
        menu.addSeparator()
        menu.addAction(QAction("📨 Request", self,
                                triggered=lambda: self.request_requested.emit(idx)))
        menu.exec(self.table.viewport().mapToGlobal(pos))


# ---------- ロードマップ（スケジュール表） ----------

class RoadmapView(QWidget):
    """
    ロードマップ：開始日〜納期を日/週/月単位のスケジュールバーで表示。
    左ツリーで親を絞り込み、右テーブルで各ノードの計画期間・実績日を確認できる。
    ダブルクリックでポップアップ編集ダイアログが開く。
    """

    edit_requested       = Signal(str)  # Edit タブにジャンプしてノードを表示
    edit_popup_requested = Signal(str)  # ポップアップダイアログで編集
    request_requested    = Signal(str)  # 項目3: Request メニュー選択時に IDX を送出

    _LEVEL_TYPES = [
        ("Project2", "project2"),
        ("Project3", "project3"),
        ("Project4", "project4"),
        ("Task",     "task"),
    ]
    _FIXED_COLS = 4  # タイトル, 担当者, ステータス, 期間
    _COL_W_DATE = 28

    # Edit タブと統一した種別スタイル
    _TYPE_LABEL = {
        "project1": "P1", "project2": "P2",
        "project3": "P3", "project4": "P4",
        "task": "Task",   "ticket":   "Tkt",
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
    # 表示レベルボタンの色
    _LVL_BG = {"Project2": "#E8F5E9", "Project3": "#FFF9C4",
               "Project4": "#F3E5F5", "Task":     "#ECEFF1"}
    _LVL_FG = {"Project2": "#2E7D32", "Project3": "#F57F17",
               "Project4": "#6A1B9A", "Task":     "#37474F"}

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._selected_parent_idxs: set = set()
        self._current_level = "Project4"
        self._cell_unit = "週"  # "日" / "週" / "月"
        self._date_col_extra: int = 0  # 日付列幅の追加ピクセル数

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── 上部コントロール ──
        ctrl = QHBoxLayout()

        # 表示レベル（可愛いトグルボタン）
        ctrl.addWidget(QLabel("表示レベル:"))
        self._lvl_btns: dict = {}
        for label, _ in self._LEVEL_TYPES:
            bg = self._LVL_BG[label]; fg = self._LVL_FG[label]
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(label == "Project4")
            btn.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:{fg}; border:2px solid {fg};"
                f" border-radius:10px; padding:3px 12px; font-weight:bold; font-size:8pt; }}"
                f"QPushButton:checked {{ background:{fg}; color:white; }}"
            )
            btn.clicked.connect(lambda _, lbl=label: self._on_level_btn(lbl))
            ctrl.addWidget(btn)
            self._lvl_btns[label] = btn

        ctrl.addSpacing(10)

        # セル単位ボタン群
        ctrl.addWidget(QLabel("単位:"))
        self._unit_btns: dict = {}
        for unit in ["日", "週", "月"]:
            btn = QPushButton(unit)
            btn.setCheckable(True)
            btn.setChecked(unit == "週")
            btn.setStyleSheet(
                "QPushButton { background:#E3F2FD; color:#1565C0; border:2px solid #1565C0;"
                " border-radius:10px; padding:3px 10px; font-size:8pt; }"
                "QPushButton:checked { background:#1565C0; color:white; }"
            )
            btn.clicked.connect(lambda _, u=unit: self._on_unit_btn(u))
            ctrl.addWidget(btn)
            self._unit_btns[unit] = btn

        ctrl.addSpacing(10)
        ctrl.addWidget(QLabel("期間:"))
        # デフォルトは会計年度（4/1〜翌3/31）
        today = datetime.date.today()
        if today.month >= 4:
            fy_start = datetime.date(today.year, 4, 1)
            fy_end   = datetime.date(today.year + 1, 3, 31)
        else:
            fy_start = datetime.date(today.year - 1, 4, 1)
            fy_end   = datetime.date(today.year, 3, 31)
        self.from_btn = DateButton(initial_date=fy_start.isoformat())
        self.from_btn.date_changed.connect(self._rebuild_table)
        ctrl.addWidget(self.from_btn)
        ctrl.addWidget(QLabel("〜"))
        self.to_btn = DateButton(initial_date=fy_end.isoformat())
        self.to_btn.date_changed.connect(self._rebuild_table)
        ctrl.addWidget(self.to_btn)
        ctrl.addSpacing(8)
        # クイック期間ボタン群
        _quick_style = (
            "QPushButton { background:#EDE7F6; color:#4527A0; border:1px solid #7E57C2;"
            " border-radius:8px; padding:2px 5px; font-size:7pt; font-weight:bold; }"
            "QPushButton:hover { background:#D1C4E9; }"
        )
        for qname in ["今期", "1Q", "2Q", "3Q", "4Q", "Next30d", "Next06m", "Next01y"]:
            qbtn = QPushButton(qname)
            qbtn.setToolTip(f"{qname} の期間を設定")
            qbtn.setStyleSheet(_quick_style)
            qbtn.clicked.connect(lambda _, n=qname: self._on_quick_period(n))
            ctrl.addWidget(qbtn)
        ctrl.addSpacing(8)
        # 日付列幅の縮小・拡大ボタン
        ctrl.addWidget(QLabel("列幅:"))
        for label, delta in [("-", -5), ("+", 5)]:
            btn = QPushButton(label)
            btn.setFixedWidth(26)
            btn.setToolTip("日付列幅を縮小" if delta < 0 else "日付列幅を拡大")
            btn.setStyleSheet(
                "QPushButton { background:#F5F5F5; border:1px solid #BDBDBD;"
                " border-radius:4px; font-size:10pt; font-weight:bold; }"
                "QPushButton:hover { background:#E0E0E0; }"
            )
            btn.clicked.connect(lambda _, d=delta: self._on_date_col_resize(d))
            ctrl.addWidget(btn)
        ctrl.addStretch()
        layout.addLayout(ctrl)

        # ── スプリッター：左ツリー + 右テーブル ──
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左: 親絞り込みツリー（Edit スタイル）
        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabel("親の絞り込み")
        self.tree.setMinimumWidth(160)
        self.tree.setMaximumWidth(280)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setStyleSheet("""
            QTreeWidget { border: 1px solid #CFD8DC; font-size: 8pt; }
            QTreeWidget::item { padding: 3px 2px; border-bottom: 1px solid #EEEEEE; }
            QTreeWidget::item:selected { background: #B3E5FC; color: black; }
            QTreeWidget::branch:has-siblings:!adjoins-item { border-left: 1px solid #CCCCCC; }
            QTreeWidget::branch:has-siblings:adjoins-item  { border-left: 1px solid #CCCCCC; }
            QTreeWidget::branch:!has-siblings:adjoins-item { border-left: 1px solid #CCCCCC; }
        """)
        # Ctrl+クリックで複数の親を選択できるよう拡張選択モードに変更
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self._splitter.addWidget(self.tree)

        # 右: スケジュールテーブル
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setDefaultSectionSize(self._COL_W_DATE)
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #E8EAF6; font-size: 8pt; }"
            "QTableWidget::item:selected { background: #C5CAE9; color: black; }"
        )
        # ダブルクリック・右クリックメニューの設定
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)

        self._splitter.addWidget(self.table)
        self._splitter.setSizes([200, 700])
        layout.addWidget(self._splitter, stretch=1)

        # 凡例
        legend = QHBoxLayout()
        for color, lbl in [("#90CAF9", "計画（開始日〜納期）"),
                            ("#A5D6A7", "実績（作業済み）"),
                            ("#80DEEA", "計画＋実績")]:
            dot = QLabel("■")
            dot.setStyleSheet(f"color:{color}; font-size:14pt;")
            legend.addWidget(dot)
            legend.addWidget(QLabel(lbl))
            legend.addSpacing(12)
        legend.addStretch()
        layout.addLayout(legend)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    # ── レベル / 単位ボタン ──

    def _on_level_btn(self, label: str) -> None:
        self._current_level = label
        for lbl, btn in self._lvl_btns.items():
            btn.setChecked(lbl == label)
        self._rebuild_table()

    def _on_unit_btn(self, unit: str) -> None:
        self._cell_unit = unit
        for u, btn in self._unit_btns.items():
            btn.setChecked(u == unit)
        self._rebuild_table()

    # ── ツリー構築（Edit スタイル） ──

    def _rebuild_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        df = self.state.df_nodes
        if not df.empty:
            all_item = QTreeWidgetItem(["（全て）"])
            all_item.setData(0, Qt.ItemDataRole.UserRole, None)
            self.tree.addTopLevelItem(all_item)
            self._fill_tree(df, "0", None)
            self._expand_to_p4(self.tree.invisibleRootItem())
        self.tree.blockSignals(False)

    def _fill_tree(self, df, parent_id, parent_item) -> None:
        node_types = ["project1", "project2", "project3", "project4", "task"]
        children = df[
            (df["parent_id"] == parent_id)
            & (df["node_type"].isin(node_types))
            & (~df["status"].isin(["deleted"]))
        ].sort_values("priority")
        for idx, row in children.iterrows():
            ntype = str(row.get("node_type", ""))
            type_short  = self._TYPE_LABEL.get(ntype, ntype)
            status_icon = {"done": "✓", "cancel": "✗", "regularly": "↻"}.get(
                str(row.get("status", "")), "")
            label = f"[{type_short}] {status_icon} {row.get('title', '')}".strip()
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, idx)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, ntype)
            item.setBackground(0, QColor(self._TYPE_BG.get(ntype, "#FFFFFF")))
            item.setForeground(0, QColor(self._TYPE_FG.get(ntype, "#000000")))
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            self._fill_tree(df, idx, item)

    def _expand_to_p4(self, parent_item) -> None:
        """P1〜P3 を展開、P4 は表示するが閉じたまま（Task 以下は見えない）"""
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            ntype = child.data(0, Qt.ItemDataRole.UserRole + 1)
            if ntype in ("project4", "task"):
                child.setExpanded(False)  # P4 とTask は閉じたまま
            else:
                child.setExpanded(True)
                self._expand_to_p4(child)

    def _on_tree_selection_changed(self) -> None:
        """ツリー選択変更時（Ctrl+クリックで複数選択対応）"""
        selected = self.tree.selectedItems()
        # （全て）が含まれる場合はフィルタ解除
        for item in selected:
            if item.data(0, Qt.ItemDataRole.UserRole) is None:
                self._selected_parent_idxs = set()
                self._rebuild_table()
                return
        self._selected_parent_idxs = {
            item.data(0, Qt.ItemDataRole.UserRole)
            for item in selected
            if item.data(0, Qt.ItemDataRole.UserRole) is not None
        }
        self._rebuild_table()

    def _on_quick_period(self, name: str) -> None:
        """クイック期間ボタン押下時に from/to を設定してテーブルを再描画する"""
        today = datetime.date.today()
        # 会計年度の開始年を算出（4月始まり）
        fy_year = today.year if today.month >= 4 else today.year - 1

        if name == "今期":
            d_from = datetime.date(fy_year, 4, 1)
            d_to   = datetime.date(fy_year + 1, 3, 31)
        elif name == "1Q":
            d_from = datetime.date(fy_year, 4, 1)
            d_to   = datetime.date(fy_year, 6, 30)
        elif name == "2Q":
            d_from = datetime.date(fy_year, 7, 1)
            d_to   = datetime.date(fy_year, 9, 30)
        elif name == "3Q":
            d_from = datetime.date(fy_year, 10, 1)
            d_to   = datetime.date(fy_year, 12, 31)
        elif name == "4Q":
            d_from = datetime.date(fy_year + 1, 1, 1)
            d_to   = datetime.date(fy_year + 1, 3, 31)
        elif name == "Next30d":
            d_from = today
            d_to   = today + datetime.timedelta(days=30)
        elif name == "Next06m":
            d_from = today
            m6 = today.month + 6
            y6 = today.year + (m6 - 1) // 12
            m6 = (m6 - 1) % 12 + 1
            d6_max = calendar.monthrange(y6, m6)[1]
            d_to = datetime.date(y6, m6, min(today.day, d6_max))
        elif name == "Next01y":
            d_from = today
            d_to   = datetime.date(today.year + 1, today.month, today.day)
        else:
            return

        # シグナルを発さずに日付をセットし、_rebuild_table を1回だけ呼ぶ
        self.from_btn.set_date(d_from.isoformat())
        self.to_btn.set_date(d_to.isoformat())
        self._rebuild_table()

    def _on_date_col_resize(self, delta: int) -> None:
        """日付列幅を delta px 分増減し、テーブルに即時反映する"""
        self._date_col_extra += delta
        # 最小幅 10px、最大幅 +100px の範囲に収める
        base = 50 if self._cell_unit != "日" else self._COL_W_DATE
        self._date_col_extra = max(10 - base, self._date_col_extra)  # 合計最小 10px
        self._date_col_extra = min(100, self._date_col_extra)
        col_w = max(10, base + self._date_col_extra)
        total_cols = self.table.columnCount()
        for c in range(self._FIXED_COLS, total_cols):
            self.table.setColumnWidth(c, col_w)

    def _get_idx_at(self, row: int) -> Optional[str]:
        """テーブルの行から IDX を取得する（親ヘッダー行は None を返す）"""
        it = self.table.item(row, 0)
        if it is None:
            return None
        idx = it.data(Qt.ItemDataRole.UserRole)
        # 親ヘッダー行（非選択）は UserRole が None または flags に ItemIsSelectable がない
        if not (it.flags() & Qt.ItemFlag.ItemIsSelectable):
            return None
        return str(idx) if idx else None

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """ダブルクリックでポップアップ編集ダイアログを表示"""
        idx = self._get_idx_at(item.row())
        if idx:
            self.edit_popup_requested.emit(idx)

    def _on_context_menu(self, pos) -> None:
        """右クリックコンテキストメニューを表示"""
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        idx = self._get_idx_at(row)
        if not idx:
            return
        menu = QMenu(self)
        act_edit = menu.addAction("Edit（Editタブで開く）")
        # 項目3: Requestメニューを追加
        act_req = menu.addAction("📨 Request")
        chosen = menu.exec(QCursor.pos())
        if chosen == act_edit:
            self.edit_requested.emit(idx)
        elif chosen == act_req:
            self.request_requested.emit(idx)

    # ── 実績データ ──

    def _build_actual_dates(self) -> dict:
        """df_daily の全スロットを走査して {ticket_idx: set[date]} のマッピングを構築する"""
        result: dict = {}
        df_daily = getattr(self.state, "df_daily", None)
        if df_daily is None or df_daily.empty:
            return result
        slot_cols = [c for c in df_daily.columns
                     if c.startswith("C") and len(c) == 5 and c[1:].isdigit()]
        for _, row in df_daily.iterrows():
            idx_str = str(row.get("IDX", ""))
            # IDX 形式: "2026-03-16-UserName"
            parts = idx_str.split("-")
            if len(parts) < 3:
                continue
            try:
                d = datetime.date.fromisoformat("-".join(parts[:3]))
            except ValueError:
                continue
            for col in slot_cols:
                t_idx = str(row.get(col, "") or "")
                if t_idx and t_idx not in ("nan", "None", ""):
                    result.setdefault(t_idx, set()).add(d)
        return result

    def _collect_descendants(self, df, idx: str) -> set:
        """idx 配下の全 ticket IDX を再帰収集"""
        result: set = set()
        children = df[df["parent_id"] == idx]
        for cid, crow in children.iterrows():
            if crow["node_type"] == "ticket":
                result.add(cid)
            else:
                result |= self._collect_descendants(df, cid)
        return result

    # ── 列（ピリオド）生成 ──

    def _make_periods(self, d_from: datetime.date, d_to: datetime.date) -> list:
        """
        cell_unit（日/週/月）に応じた (start, end, label) タプルのリストを返す。
        日: 最大 180 列、週: 最大 52 列、月: 最大 24 列。
        """
        periods = []
        if self._cell_unit == "日":
            d = d_from
            while d <= d_to and len(periods) < 180:
                wd = ["月", "火", "水", "木", "金", "土", "日"][d.weekday()]
                periods.append((d, d, f"{d.month}/{d.day}\n{wd}"))
                d += datetime.timedelta(days=1)
        elif self._cell_unit == "週":
            d = d_from - datetime.timedelta(days=d_from.weekday())  # 月曜起点
            while d <= d_to and len(periods) < 52:
                end = d + datetime.timedelta(days=6)
                periods.append((d, min(end, d_to),
                                 f"{d.month}/{d.day}〜\n{end.month}/{end.day}"))
                d += datetime.timedelta(days=7)
        else:  # 月
            y, m = d_from.year, d_from.month
            while len(periods) < 24:
                last = calendar.monthrange(y, m)[1]
                start = datetime.date(y, m, 1)
                end   = datetime.date(y, m, last)
                if start > d_to:
                    break
                periods.append((start, min(end, d_to), f"{y}/{m}\n({m}月)"))
                if m == 12:
                    y, m = y + 1, 1
                else:
                    m += 1
        return periods

    # ── テーブル構築 ──

    def _is_under(self, df, idx: str, ancestor_idx: str) -> bool:
        pid = df.loc[idx, "parent_id"] if idx in df.index else None
        while pid and pid != "0" and pid in df.index:
            if pid == ancestor_idx:
                return True
            pid = df.loc[pid, "parent_id"]
        return False

    def _get_parent_chain(self, df, idx: str) -> list:
        """idx の祖先チェーンを [(ntype, pid, title), ...] で返す（P1 が先頭）。テーブルのグループヘッダー生成に使用。"""
        chain = []
        pid = df.loc[idx, "parent_id"] if idx in df.index else None
        while pid and pid != "0" and pid in df.index:
            chain.insert(0, (
                str(df.loc[pid, "node_type"]),
                pid,
                str(df.loc[pid, "title"]),
            ))
            pid = df.loc[pid, "parent_id"]
        return chain

    def _rebuild_table(self) -> None:
        """
        ロードマップテーブルを再構築する。

        処理フロー:
          1. 期間ボタンから表示期間を取得し、cell_unit に応じた列（ピリオド）を生成
          2. 表示レベル（Project2/3/4/Task）でノードを絞り込み
          3. 祖先チェーンごとに親ヘッダー行を挿入してグループ化
          4. 各ノード行に計画バー（青）・実績バー（緑）・両方（シアン）を描画
          5. ツリー絞り込みで選択した親の配下ノードのみ表示
        """
        df = self.state.df_nodes
        try:
            d_from = datetime.date.fromisoformat(self.from_btn.get_date())
            d_to   = datetime.date.fromisoformat(self.to_btn.get_date())
        except ValueError:
            d_from = datetime.date.today()
            d_to   = d_from + datetime.timedelta(days=89)

        # cell_unit（日/週/月）に応じたピリオドリストを生成
        periods    = self._make_periods(d_from, d_to)
        total_cols = self._FIXED_COLS + len(periods)
        col_w      = max(10, (50 if self._cell_unit != "日" else self._COL_W_DATE) + self._date_col_extra)

        self.table.setColumnCount(total_cols)
        headers = ["タイトル", "担当者", "ステータス", "期間"]
        for ps, pe, pl in periods:
            headers.append(pl)
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 75)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 110)
        # 列幅はマウス操作で変更可能（Interactive）
        for c in range(total_cols):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        for c in range(self._FIXED_COLS, total_cols):
            self.table.setColumnWidth(c, col_w)
        self.table.setRowCount(0)

        if df.empty:
            self.info.set_info("データなし")
            return

        node_type  = dict(self._LEVEL_TYPES).get(self._current_level, "task")
        items      = df[
            (df["node_type"] == node_type)
            & (~df["status"].isin(["deleted"]))
        ].sort_values("priority")
        filter_idxs = self._selected_parent_idxs  # set[str]（空の場合は全件表示）

        # daily_schedule から ticket の実績作業日セットを収集（{ticket_idx: set[date]}）
        actual_dates = self._build_actual_dates()

        def _parse(val):
            if val and str(val) not in ("", "nan", "None"):
                try:
                    return datetime.date.fromisoformat(str(val))
                except Exception:
                    pass
            return None

        def _make_row_non_selectable(bg_color: QColor):
            """親ヘッダー行の非選択セルを生成するヘルパー"""
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 選択・編集不可
            it.setBackground(bg_color)
            return it

        def _insert_parent_row(depth: int, ntype: str, pid: str, ptitle: str) -> None:
            """親グループのヘッダー行をテーブルに挿入する"""
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setRowHeight(r, 18)

            indent = "  " * depth
            type_short = self._TYPE_LABEL.get(ntype, ntype)
            bg_hex = self._TYPE_BG.get(ntype, "#FFFFFF")
            fg_hex = self._TYPE_FG.get(ntype, "#000000")
            bg_color = QColor(bg_hex)
            bg_color.setAlpha(50)  # うっすら見える程度に薄く

            it = QTableWidgetItem(f"{indent}[{type_short}] {ptitle}")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setBackground(bg_color)
            it.setForeground(QColor(fg_hex))
            f = QFont(); f.setBold(True); f.setPointSize(8)
            it.setFont(f)
            self.table.setItem(r, 0, it)

            # 担当者/ステータス/期間: 親ノードの情報を表示
            p_row = df.loc[pid] if pid in df.index else None
            p_sa  = _parse(p_row.get("start_available")) if p_row is not None else None
            p_dl  = _parse(p_row.get("deadline"))        if p_row is not None else None
            p_sta = str(p_row.get("status", ""))         if p_row is not None else ""
            p_per = ""
            if p_sa and p_dl:
                p_per = f"{p_sa} 〜 {p_dl}"
            elif p_sa:
                p_per = f"{p_sa} 〜"
            elif p_dl:
                p_per = f"〜 {p_dl}"

            for c, val in enumerate(["", "", p_sta, p_per], start=1):
                cel = QTableWidgetItem(val)
                cel.setFlags(Qt.ItemFlag.ItemIsEnabled)
                cel.setBackground(bg_color)
                cel.setForeground(QColor(fg_hex))
                self.table.setItem(r, c, cel)

            # 日付バー（親の計画期間を薄く表示）
            for ci, (ps, pe, _pl) in enumerate(periods):
                cell = _make_row_non_selectable(bg_color)
                if ps.weekday() >= 5 and self._cell_unit == "日":
                    cell.setBackground(QColor("#F0F0F0"))
                else:
                    plan_ov = False
                    if p_sa and p_dl:
                        plan_ov = p_sa <= pe and p_dl >= ps
                    elif p_sa:
                        plan_ov = p_sa <= pe
                    elif p_dl:
                        plan_ov = p_dl >= ps
                    if plan_ov:
                        c_bg = QColor(bg_hex)
                        c_bg.setAlpha(40)  # 薄く
                        cell.setBackground(c_bg)
                self.table.setItem(r, self._FIXED_COLS + ci, cell)

        # 表示済み親チェーンの管理（前の行との差分でヘッダー行を挿入）
        prev_chain: list = []
        count = 0

        # 親チェーン順でソートして同じ親のノードが連続するようにグループ化
        # 項目1: 親チェーン順でソートして親が同じものが連続するようにする
        # ポイント: (priority, id) のタプルを各階層に使うことで、
        # 同じpriorityを持つ異なる親が混在しても確実にグルーピングできる
        def _sort_key(idx):
            chain = self._get_parent_chain(df, idx)
            key = []
            for _, pid, _ in chain:
                try:
                    prio = float(df.loc[pid, "priority"] or 9999) if pid in df.index else 9999.0
                except (ValueError, TypeError):
                    prio = 9999.0
                # (priority, id) のタプルで祖先を一意に特定してグループ化
                key.append((prio, str(pid)))
            try:
                self_prio = float(df.loc[idx, "priority"] or 9999) if idx in df.index else 9999.0
            except (ValueError, TypeError):
                self_prio = 9999.0
            key.append((self_prio, str(idx)))
            return key

        sorted_idxs = sorted(items.index, key=_sort_key)
        items = items.loc[sorted_idxs]

        for idx, row in items.iterrows():
            # フィルタが設定されている場合、いずれかの選択親に属するノードのみ表示
            if filter_idxs:
                if not any(idx == fi or self._is_under(df, idx, fi) for fi in filter_idxs):
                    continue

            chain = self._get_parent_chain(df, idx)

            # 前の行と共通する祖先チェーンのプレフィックスを計算
            # 差分部分（新しい親グループ）にのみヘッダー行を挿入する
            common = 0
            for i in range(min(len(chain), len(prev_chain))):
                if chain[i][1] == prev_chain[i][1]:  # 同じ親 ID なら共通
                    common = i + 1
                else:
                    break
            for depth, (ntype, pid, ptitle) in enumerate(chain[common:], start=common):
                _insert_parent_row(depth, ntype, pid, ptitle)
            prev_chain = chain

            # ── アイテム本体行 ──
            depth = len(chain)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setRowHeight(r, 22)

            start_avail = _parse(row.get("start_available"))
            deadline    = _parse(row.get("deadline"))
            status      = str(row.get("status", ""))
            hex_color   = COLOR_OPTIONS.get(str(row.get("color", "Cyan")), "#00BCD4")
            bg          = QColor(hex_color); bg.setAlpha(35)

            indent = "  " * depth
            period_str = ""
            if start_avail and deadline:
                period_str = f"{start_avail} 〜 {deadline}"
            elif start_avail:
                period_str = f"{start_avail} 〜"
            elif deadline:
                period_str = f"〜 {deadline}"

            for c, val in enumerate([
                f"{indent}{row.get('title', '')}",
                self.state.display_name(str(row.get("assigned_to", ""))),
                status,
                period_str,
            ]):
                it = QTableWidgetItem(val)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setData(Qt.ItemDataRole.UserRole, idx)
                if status == "done":
                    it.setForeground(QColor("#9E9E9E"))
                else:
                    it.setBackground(bg)
                self.table.setItem(r, c, it)

            # 配下 ticket の実績日付セット
            desc_tickets      = self._collect_descendants(df, idx)
            item_actual_dates: set = set()
            for t_idx in desc_tickets:
                item_actual_dates |= actual_dates.get(t_idx, set())

            # 日付バー（計画 + 実績）
            for ci, (ps, pe, _pl) in enumerate(periods):
                cell = QTableWidgetItem()
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                cell.setData(Qt.ItemDataRole.UserRole, idx)

                if ps.weekday() >= 5 and self._cell_unit == "日":
                    cell.setBackground(QColor("#F0F0F0"))
                elif status not in ("done", "cancel"):
                    plan_ov = False
                    if start_avail and deadline:
                        plan_ov = start_avail <= pe and deadline >= ps
                    elif start_avail:
                        plan_ov = start_avail <= pe
                    elif deadline:
                        plan_ov = deadline >= ps

                    act_ov = any(ps <= ad <= pe for ad in item_actual_dates)

                    if plan_ov and act_ov:
                        cell.setBackground(QColor("#80DEEA"))  # 計画＋実績: シアン
                    elif act_ov:
                        cell.setBackground(QColor("#A5D6A7"))  # 実績のみ: 緑
                    elif plan_ov:
                        cell.setBackground(QColor("#90CAF9"))  # 計画のみ: 青
                        if self._cell_unit == "日":
                            if ps == start_avail:
                                cell.setText("▶")
                            elif ps == deadline:
                                # 納期セル: 背景は計画色と統一し🏁で示す
                                cell.setText("🏁")
                    else:
                        cell.setBackground(bg)
                else:
                    cell.setForeground(QColor("#BDBDBD"))

                self.table.setItem(r, self._FIXED_COLS + ci, cell)

            count += 1

        self.info.set_info(
            f"{count} 件表示  ({d_from} 〜 {d_to})  "
            "  ■青=計画  ■緑=実績  ■シアン=計画+実績"
        )

    # ── 公開 ──

    def refresh(self) -> None:
        self._rebuild_tree()
        self._rebuild_table()


# ---------- 工数分析 ----------

class AnalysisView(QWidget):
    """
    工数分析タブ。
    集計レベル（P1〜Task）・親ノード・ユーザーを選択して棒グラフで工数を可視化し、
    超過チケット一覧を表示する。
    """

    # ノード階層の順序
    _LEVEL_ORDER = ["project1", "project2", "project3", "project4", "task"]

    def __init__(self, state):
        super().__init__()
        self.state = state

        # matplotlib を遅延インポート（起動時のオーバーヘッドを避ける）
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        import matplotlib
        import matplotlib.font_manager as _fm
        # システムにインストール済みのフォントのみ指定（未インストールは警告が出るため除外）
        _installed = {f.name for f in _fm.fontManager.ttflist}
        _candidates = ["Hiragino Sans", "Yu Gothic", "Noto Sans CJK JP", "sans-serif"]
        matplotlib.rcParams["font.family"] = [f for f in _candidates
                                               if f in _installed or f == "sans-serif"]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("📈 工数分析"))
        layout.addWidget(Separator())

        # ── コントロールパネル ──────────────────────────────
        ctrl_box = QGroupBox("集計設定")
        ctrl_vlay = QVBoxLayout(ctrl_box)

        # 集計レベル
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("集計レベル:"))
        self._level_btns: dict[str, QRadioButton] = {}
        self._level_grp = QButtonGroup(self)
        for lbl, typ in [("P1", "project1"), ("P2", "project2"),
                          ("P3", "project3"), ("P4", "project4"), ("Task", "task")]:
            rb = QRadioButton(lbl)
            self._level_btns[typ] = rb
            self._level_grp.addButton(rb)
            level_row.addWidget(rb)
        self._level_btns["project1"].setChecked(True)
        level_row.addStretch()
        ctrl_vlay.addLayout(level_row)

        # 親ノード選択（P2 以下のとき表示）
        self._parent_box = QGroupBox("対象ノード（親選択）")
        parent_inner = QVBoxLayout(self._parent_box)
        self._parent_scroll = QScrollArea()
        self._parent_scroll.setWidgetResizable(True)
        self._parent_scroll.setMaximumHeight(80)
        self._parent_widget = QWidget()
        self._parent_layout = QHBoxLayout(self._parent_widget)
        self._parent_layout.setContentsMargins(4, 4, 4, 4)
        self._parent_scroll.setWidget(self._parent_widget)
        parent_inner.addWidget(self._parent_scroll)
        self._parent_checks: dict[str, QCheckBox] = {}
        ctrl_vlay.addWidget(self._parent_box)
        self._parent_box.setVisible(False)

        # ユーザー選択
        user_row = QHBoxLayout()
        user_row.addWidget(QLabel("ユーザー:"))
        self._all_user_cb = QCheckBox("全員")
        self._all_user_cb.setChecked(True)
        self._all_user_cb.stateChanged.connect(self._on_all_user_toggled)
        user_row.addWidget(self._all_user_cb)
        self._user_checks: dict[str, QCheckBox] = {}
        for m in state.members:
            cb = QCheckBox(state.display_name(m))
            cb.setChecked(True)
            cb.stateChanged.connect(self._on_user_check_changed)
            user_row.addWidget(cb)
            self._user_checks[m] = cb
        user_row.addStretch()
        ctrl_vlay.addLayout(user_row)

        # 集計ボタン
        calc_btn = QPushButton("集計")
        calc_btn.setStyleSheet(STYLE_BUTTON)
        calc_btn.clicked.connect(self._calc)
        ctrl_vlay.addWidget(calc_btn)

        layout.addWidget(ctrl_box)

        # レベル変更時に親ノード選択を更新
        for rb in self._level_btns.values():
            rb.toggled.connect(self._on_level_changed)

        # ── 棒グラフ ───────────────────────────────────────
        self._fig = Figure(tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setMinimumHeight(220)
        layout.addWidget(self._canvas, stretch=2)

        layout.addWidget(Separator())

        # ── 超過チケット一覧 ───────────────────────────────
        layout.addWidget(QLabel("見積 vs 実績（超過チケット）"))
        AL_COLS = ["親", "チケット名", "担当者", "見積(h)", "実績(h)", "納期", "メモ"]
        AL_WIDTHS = [280, 160, 90, 65, 65, 100, 200]
        self._AL_COL_MEMO = 6  # メモ列のインデックス
        self.alert_table = ScrollableTable(AL_COLS, AL_WIDTHS)
        # メモ列のみダブルクリックで編集可能にする
        self.alert_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.alert_table.itemChanged.connect(self._on_alert_memo_changed)
        layout.addWidget(self.alert_table, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    # ── ユーザー選択ヘルパー ─────────────────────────────

    def _on_all_user_toggled(self, state: int) -> None:
        """全員チェック変化 → 個別チェックに同期"""
        checked = bool(state)
        for cb in self._user_checks.values():
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)

    def _on_user_check_changed(self) -> None:
        """個別ユーザーチェック変化 → 全員チェックを更新"""
        all_checked = all(cb.isChecked() for cb in self._user_checks.values())
        self._all_user_cb.blockSignals(True)
        self._all_user_cb.setChecked(all_checked)
        self._all_user_cb.blockSignals(False)

    # ── レベル選択ヘルパー ───────────────────────────────

    def _current_level_type(self) -> str:
        for typ, rb in self._level_btns.items():
            if rb.isChecked():
                return typ
        return "project1"

    def _parent_type_of(self, node_type: str) -> Optional[str]:
        """node_type の一つ上の親タイプを返す（project1 は None）"""
        i = self._LEVEL_ORDER.index(node_type) if node_type in self._LEVEL_ORDER else -1
        return self._LEVEL_ORDER[i - 1] if i > 0 else None

    def _on_level_changed(self) -> None:
        """集計レベル変更 → 親ノード選択パネルを更新"""
        level = self._current_level_type()
        parent_type = self._parent_type_of(level)
        if parent_type is None:
            self._parent_box.setVisible(False)
            return
        # 既存チェックボックスを全削除
        for i in reversed(range(self._parent_layout.count())):
            item = self._parent_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        self._parent_checks.clear()
        # 親レベルのノードをチェックボックスとして列挙
        df = self.state.df_nodes
        parents = df[df["node_type"] == parent_type]
        for idx, row in parents.iterrows():
            cb = QCheckBox(str(row.get("title", idx)))
            cb.setChecked(True)
            self._parent_layout.addWidget(cb)
            self._parent_checks[str(idx)] = cb
        self._parent_layout.addStretch()
        self._parent_box.setVisible(True)

    # ── 集計・描画 ───────────────────────────────────────

    def _find_ancestor_at_type(self, idx: str, target_type: str) -> Optional[str]:
        """idx の祖先を遡り node_type == target_type の IDX を返す（なければ None）"""
        df = self.state.df_nodes
        current: Optional[str] = idx
        visited: set[str] = set()
        while current and current not in visited:
            if current not in df.index:
                return None
            row = df.loc[current]
            if str(row.get("node_type", "")) == target_type:
                return current
            visited.add(current)
            parent = str(row.get("parent_id", ""))
            current = parent if parent else None
        return None

    def refresh(self) -> None:
        """タブ切替・データ更新時に呼ばれる"""
        self._on_level_changed()
        self._calc()

    def _calc(self) -> None:
        """集計してグラフと超過チケット一覧を更新する"""
        df = self.state.df_nodes
        if df.empty:
            return

        level = self._current_level_type()
        parent_type = self._parent_type_of(level)

        # 選択ユーザー（None = 全員）
        if self._all_user_cb.isChecked():
            user_set: Optional[set[str]] = None
        else:
            user_set = {u for u, cb in self._user_checks.items() if cb.isChecked()}

        # 選択親ノード（None = 全て）
        parent_idxs: Optional[set[str]] = None
        if parent_type is not None:
            parent_idxs = {idx for idx, cb in self._parent_checks.items()
                           if cb.isChecked()}

        # 集計レベルのノードを取得
        groups = df[df["node_type"] == level]
        if parent_idxs is not None:
            groups = groups[groups["parent_id"].isin(parent_idxs)]

        # 各グループの初期レコード
        agg: dict[str, dict] = {
            str(idx): {
                "title": str(row.get("title", "")),
                "actual": 0.0,
                "est": 0.0,
            }
            for idx, row in groups.iterrows()
        }

        if user_set is None:
            # 全員: ノード自身の集計値をそのまま使用
            for idx in list(agg.keys()):
                row = df.loc[idx]
                agg[idx]["actual"] = float(row.get("actual_hours", 0) or 0)
                agg[idx]["est"]    = float(row.get("estimated_hours", 0) or 0)
        else:
            # 特定ユーザーのみ: チケットを走査して積み上げ
            tickets = df[(df["node_type"] == "ticket") &
                         (df["assigned_to"].isin(user_set))]
            for t_idx, t_row in tickets.iterrows():
                anc = self._find_ancestor_at_type(str(t_idx), level)
                if anc and anc in agg:
                    agg[anc]["actual"] += float(t_row.get("actual_hours", 0) or 0)
                    agg[anc]["est"]    += float(t_row.get("estimated_hours", 0) or 0)

        # ── 棒グラフ描画 ──────────────────────────────────
        self._ax.clear()
        if agg:
            labels  = [v["title"] for v in agg.values()]
            ests    = [v["est"]    for v in agg.values()]
            actuals = [v["actual"] for v in agg.values()]
            x = list(range(len(labels)))
            w = 0.35
            self._ax.bar([i - w / 2 for i in x], ests,    w, label="見積(h)", color="#90CAF9")
            self._ax.bar([i + w / 2 for i in x], actuals, w, label="実績(h)", color="#A5D6A7")
            self._ax.set_xticks(x)
            self._ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
            self._ax.set_ylabel("工数 (h)", fontsize=9)
            self._ax.legend(fontsize=8)
            self._ax.set_title(f"{level} レベル 工数分析", fontsize=10)
        self._canvas.draw()

        # ── 超過チケット一覧 ──────────────────────────────
        tickets_all = df[df["node_type"] == "ticket"]
        if user_set is not None:
            tickets_all = tickets_all[tickets_all["assigned_to"].isin(user_set)]
        over = tickets_all[
            tickets_all.apply(
                lambda r: (float(r.get("actual_hours", 0) or 0)
                           > float(r.get("estimated_hours", 0) or 0)
                           and float(r.get("estimated_hours", 0) or 0) > 0),
                axis=1,
            )
        ]
        al_rows = []
        al_ids = []
        for t_idx, r in over.iterrows():
            # Project1 から直接の親まで全祖先タイトルを " > " 区切りで表示
            ancestor_titles: list[str] = []
            cur_id = str(r.get("parent_id", ""))
            visited: set[str] = set()
            while cur_id and cur_id not in visited and cur_id in df.index:
                visited.add(cur_id)
                ancestor_titles.insert(0, str(df.loc[cur_id, "title"] or ""))
                cur_id = str(df.loc[cur_id, "parent_id"] or "")
            parent_chain = " > ".join(ancestor_titles) if ancestor_titles else ""
            al_rows.append([
                parent_chain,
                r.get("title", ""),
                self.state.display_name(str(r.get("assigned_to", ""))),
                f"{float(r.get('estimated_hours', 0)):.2f}",
                f"{float(r.get('actual_hours', 0)):.2f}",
                r.get("deadline", ""),
                r.get("memo", ""),
            ])
            al_ids.append(str(t_idx))
        self.alert_table.blockSignals(True)
        self.alert_table.set_rows(al_rows, row_ids=al_ids)
        # メモ列のみ編集可能フラグを追加
        for row_i in range(self.alert_table.rowCount()):
            memo_item = self.alert_table.item(row_i, self._AL_COL_MEMO)
            if memo_item:
                memo_item.setFlags(memo_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.alert_table.blockSignals(False)
        self.info.set_info(
            f"集計: {len(agg)} ノード / 超過チケット: {len(al_rows)}"
        )

    def _on_alert_memo_changed(self, item: QTableWidgetItem) -> None:
        """超過チケット一覧のメモ列が編集されたとき、df_nodes を更新する"""
        if item.column() != self._AL_COL_MEMO:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not idx or idx not in self.state.df_nodes.index:
            return
        new_memo = item.text()
        self.state.df_nodes.loc[idx, "memo"] = new_memo
        self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        self.state.nodes_modified = True


# ---------- 検索 ----------

class SearchView(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("🔍 検索"))
        layout.addWidget(Separator())

        # フィルター群
        filter_box = QGroupBox("検索条件")
        fl = QFormLayout(filter_box)

        self.f_kw = QLineEdit()
        self.f_kw.setPlaceholderText("キーワード（タイトル・メモ）")
        self.f_kw.returnPressed.connect(self._on_search)
        fl.addRow("キーワード:", self.f_kw)

        self.f_member = QComboBox()
        self.f_member.addItem("（全員）", userData="")
        for m in state.members:
            self.f_member.addItem(state.display_name(m), userData=m)
        fl.addRow("担当者:", self.f_member)

        status_row = QHBoxLayout()
        self.status_checks = {}
        for s in DB.STATUS_LIST[:-1]:  # deleted 除外
            cb = QCheckBox(s)
            cb.setChecked(True)
            self.status_checks[s] = cb
            status_row.addWidget(cb)
        status_widget = QWidget()
        status_widget.setLayout(status_row)
        fl.addRow("ステータス:", status_widget)

        date_row = QHBoxLayout()
        self.f_from = QLineEdit()
        self.f_from.setPlaceholderText("YYYY-MM-DD")
        self.f_from.setMaximumWidth(120)
        self.f_to = QLineEdit()
        self.f_to.setPlaceholderText("YYYY-MM-DD")
        self.f_to.setMaximumWidth(120)
        date_row.addWidget(self.f_from)
        date_row.addWidget(QLabel("〜"))
        date_row.addWidget(self.f_to)
        date_widget = QWidget()
        date_widget.setLayout(date_row)
        fl.addRow("更新日付:", date_widget)

        layout.addWidget(filter_box)

        btn_row = ButtonRow([
            ("🔍 検索",        self._on_search),
            ("📋 今週の仕事",   self._on_preset_week),
            ("📅 今日の仕事",   self._on_preset_today),
            ("📤 CSV 出力",    self._on_export),
        ])
        layout.addWidget(btn_row)

        layout.addWidget(Separator())

        COLS = ["種類", "Project1", "Project2", "Project3", "Project4", "Task",
                "タイトル", "担当者", "ステータス", "見積(h)", "実績(h)", "期間実績(h)", "更新日"]
        self.result_table = ScrollableTable(
            COLS, [65, 110, 110, 110, 110, 110, 160, 90, 70, 55, 55, 65, 90]
        )
        layout.addWidget(self.result_table, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

        self._last_result: Optional[pd.DataFrame] = None

    def refresh(self) -> None:
        self._on_search()

    def _calc_period_hours_batch(self, ticket_idxs: list,
                                  date_from: str, date_to: str) -> dict:
        """
        指定期間における各チケットの実績工数を daily_schedule から一括集計する。
        日付範囲未指定（date_from・date_to ともに空）の場合は空辞書を返す。
        """
        if not date_from and not date_to:
            return {}
        df_daily = self.state.df_daily
        counts: dict = {idx: 0 for idx in ticket_idxs}
        ticket_set = set(ticket_idxs)
        if df_daily.empty or not ticket_set:
            return {idx: 0.0 for idx in ticket_idxs}
        for row_idx in df_daily.index:
            # IDX 先頭10文字が日付 (YYYY-MM-DD)
            date_part = str(row_idx)[:10]
            if date_from and date_part < date_from:
                continue
            if date_to and date_part > date_to:
                continue
            for col in DB.DAILY_TIME_COLS:
                if col not in df_daily.columns:
                    continue
                val = df_daily.loc[row_idx, col]
                if val in ticket_set:
                    counts[val] = counts.get(val, 0) + 1
        return {idx: round(cnt * 0.25, 2) for idx, cnt in counts.items()}

    def _get_ancestors(self, idx: str) -> dict:
        """指定ノードの祖先タイトルを種別ごとに返す"""
        df = self.state.df_nodes
        result = {"project1": "", "project2": "", "project3": "", "project4": "", "task": ""}
        if idx not in df.index:
            return result
        current = df.loc[idx]
        while True:
            parent_id = str(current.get("parent_id") or "")
            if not parent_id or parent_id == "0" or parent_id not in df.index:
                break
            parent = df.loc[parent_id]
            nt = str(parent.get("node_type", ""))
            if nt in result:
                result[nt] = str(parent.get("title", ""))
            current = parent
        return result

    def _on_search(self) -> None:
        statuses = [s for s, cb in self.status_checks.items() if cb.isChecked()]
        member = self.f_member.currentData() or ""
        date_from = self.f_from.text().strip()
        date_to   = self.f_to.text().strip()
        result = LG.filter_nodes(
            self.state.df_nodes,
            keyword=self.f_kw.text(),
            statuses=statuses,
            member=member,
            date_from=date_from,
            date_to=date_to,
            node_types=["ticket"],
        )
        self._last_result = result
        # 期間内実績工数を一括計算（日付範囲指定時のみ）
        period_hours = self._calc_period_hours_batch(
            list(result.index), date_from, date_to
        )
        rows = []
        for idx, r in result.iterrows():
            anc = self._get_ancestors(idx)
            ph = period_hours.get(idx)
            period_str = str(ph) if ph is not None else "-"
            rows.append([
                r.get("node_type", ""),
                anc["project1"],
                anc["project2"],
                anc["project3"],
                anc["project4"],
                anc["task"],
                r.get("title", ""),
                self.state.display_name(str(r.get("assigned_to", ""))),
                r.get("status", ""),
                r.get("estimated_hours", ""),
                r.get("actual_hours", ""),
                period_str,
                r.get("updated_at", ""),
            ])
        ids = list(result.index)
        colors = [COLOR_OPTIONS.get(r.get("color", "Cyan"))
                  for _, r in result.iterrows()]
        self.result_table.set_rows(rows, ids, colors)
        self.info.set_info(f"{len(rows)} 件")

    def _on_preset_week(self) -> None:
        today = datetime.date.today()
        week_ago = (today - datetime.timedelta(days=7)).isoformat()
        self.f_from.setText(week_ago)
        self.f_to.setText(today.isoformat())
        self._on_search()

    def _on_preset_today(self) -> None:
        """今日更新されたノードを検索するプリセット"""
        today = datetime.date.today().isoformat()
        self.f_from.setText(today)
        self.f_to.setText(today)
        self._on_search()

    def _on_export(self) -> None:
        if self._last_result is None or self._last_result.empty:
            QMessageBox.information(self, "情報", "検索結果がありません")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV保存", "search_result.csv",
            "CSV ファイル (*.csv);;Excel ファイル (*.xlsx)"
        )
        if not path:
            return
        try:
            if path.endswith(".xlsx"):
                LG.export_excel(self._last_result, path)
            else:
                LG.export_csv(self._last_result, path)
            QMessageBox.information(self, "完了", f"出力しました: {path}")
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))


# ---------- チーム日次ログ ----------

class TeamLogView(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QHBoxLayout()
        header.addWidget(QLabel("👥 チーム日次ログ"))
        header.addStretch()
        layout.addLayout(header)
        layout.addWidget(Separator())

        # 勤務時間表
        layout.addWidget(QLabel("本日の勤務状況"))
        WORK_COLS = ["メンバー", "勤務時間", "勤務場所", "残業", "健康状態"]
        self.work_table = ScrollableTable(WORK_COLS, [120, 200, 90, 60, 80])
        layout.addWidget(self.work_table, stretch=1)

        layout.addWidget(QLabel("連絡事項 / 備考"))
        INFO_COLS = ["メンバー", "安全確認", "今日", "常時"]
        self.info_table = ScrollableTable(INFO_COLS, [120, 80, 200, 200])
        layout.addWidget(self.info_table, stretch=1)

        self.info_lbl = InfoLabel()
        layout.addWidget(self.info_lbl)

    def refresh(self) -> None:
        date = self.state.current_date
        df_log = self.state.df_daily_log
        df_daily = self.state.df_daily
        all_permanent = getattr(self.state, "all_permanent_notices", {})

        work_rows = []
        info_rows = []
        for member in self.state.members:
            idx = DB.daily_sch_idx(date, member)
            wh = LG.calc_working_hours(df_daily, idx)
            if wh["total"] > 0:
                wh_str = (f"{LG.col_to_hhmm(wh['from'])} 〜 "
                          f"{LG.col_to_hhmm(wh['to'])} "
                          f"[{wh['total']:.1f}h] (休{wh['break']:.1f}h)")
            else:
                wh_str = "-"
            place = overwork = health = safety = notes = ""
            if not df_log.empty and idx in df_log.index:
                row = df_log.loc[idx]
                place   = str(row.get("work_place", "") or "")
                overwork= str(row.get("overwork", "") or "")
                health  = str(row.get("health_status", "") or "")
                safety  = str(row.get("safety", "") or "")
                notes   = str(row.get("notes", "") or "")
            # 常時メモ（キャッシュになければDBから取得）
            ever = all_permanent.get(member, "")
            if not ever and self.state.db:
                ever = self.state.db.read_permanent_notice(member)
            work_rows.append([self.state.display_name(member), wh_str, place, overwork, health])
            info_rows.append([self.state.display_name(member), safety, notes, ever])

        self.work_table.set_rows(work_rows)
        self.info_table.set_rows(info_rows)


# ---------- タスク依頼 ----------

class AssignmentView(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("📨 タスク依頼"))
        layout.addWidget(Separator())

        # 依頼作成
        req_box = QGroupBox("依頼を送る")
        req_form = QFormLayout(req_box)
        self.req_ticket = QComboBox()
        self.req_ticket.setMinimumWidth(250)
        self.req_ticket.currentIndexChanged.connect(self._on_target_changed)

        # 動作説明ラベル（ノードタイプに応じて切替）
        self._req_info_lbl = QLabel("")
        self._req_info_lbl.setStyleSheet(
            "QLabel { color: #555; font-style: italic; padding: 2px 0; }"
        )

        # 送り先: ticket は複数選択可、task以上は1名のみ
        self._req_to_checks: dict[str, QCheckBox] = {}
        checks_widget = QWidget()
        checks_layout = QHBoxLayout(checks_widget)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        checks_layout.setSpacing(6)
        for m in state.members:
            cb = QCheckBox(state.display_name(m))
            if m == state.user:
                # 自分自身はチェック不可
                cb.setEnabled(False)
            else:
                cb.toggled.connect(self._on_check_toggled)
            checks_layout.addWidget(cb)
            self._req_to_checks[m] = cb
        checks_layout.addStretch()

        self.req_msg = QLineEdit()
        self.req_msg.setPlaceholderText("メッセージ")
        req_form.addRow("対象:", self.req_ticket)
        req_form.addRow("", self._req_info_lbl)
        req_form.addRow("送り先:", checks_widget)
        req_form.addRow("メッセージ:", self.req_msg)
        send_btn = QPushButton("依頼送信")
        send_btn.setStyleSheet(STYLE_BUTTON)
        send_btn.clicked.connect(self._on_send)
        req_form.addRow("", send_btn)
        layout.addWidget(req_box)

        layout.addWidget(Separator())

        # 受信一覧（親階層付き）
        layout.addWidget(QLabel("受信した依頼"))
        RECV_COLS = ["P1", "P2", "P3", "P4", "Task", "Ticket", "依頼者", "メッセージ", "状態", "日時"]
        self.recv_table = ScrollableTable(RECV_COLS, [80, 80, 80, 80, 80, 120, 70, 150, 60, 90])
        self.recv_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        layout.addWidget(self.recv_table, stretch=1)

        resp_row = ButtonRow([
            ("✅ 承諾", self._on_accept),
            ("❌ 拒否", self._on_reject),
        ])
        layout.addWidget(resp_row)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    # Requestできるノードタイプ（ticket含む全階層）
    _REQUESTABLE_TYPES = ("project1", "project2", "project3", "project4", "task", "ticket")
    # 種別の短縮ラベル（表示用）
    _TYPE_SHORT = {
        "project1": "P1", "project2": "P2", "project3": "P3", "project4": "P4",
        "task": "Task", "ticket": "Tkt",
    }

    # 階層順（P1〜Ticket の 6 段階）
    _HIERARCHY = ["project1", "project2", "project3", "project4", "task", "ticket"]

    def _node_hierarchy_row(self, t_idx: str) -> tuple:
        """ノード IDX から [P1, P2, P3, P4, Task, Ticket] のタイトルと
        グレーアウトフラグ（bool リスト）を返す。
        対象ノードより下位の列は空欄でグレーアウト。"""
        df = self.state.df_nodes
        if t_idx not in df.index:
            return [""] * 6, [True] * 6
        target_type = str(df.loc[t_idx, "node_type"])
        target_pos = self._HIERARCHY.index(target_type) if target_type in self._HIERARCHY else 5
        # 祖先をたどってタイプ→タイトルの辞書を作成
        path: dict = {}
        cur = t_idx
        for _ in range(10):
            if cur not in df.index:
                break
            ntype = str(df.loc[cur, "node_type"])
            path[ntype] = str(df.loc[cur, "title"])
            parent_id = str(df.loc[cur, "parent_id"])
            if not parent_id or parent_id == "0":
                break
            cur = parent_id
        titles = []
        grayed = []
        for i, t in enumerate(self._HIERARCHY):
            if i > target_pos:
                titles.append("")
                grayed.append(True)
            else:
                titles.append(path.get(t, ""))
                grayed.append(False)
        return titles, grayed

    def refresh(self) -> None:
        # 依頼対象一覧を更新（project1~4, task, ticket すべて対象）
        df = self.state.df_nodes
        self.req_ticket.clear()
        targets = df[
            (df["node_type"].isin(self._REQUESTABLE_TYPES))
            & (df["assigned_to"] == self.state.user)
            & (~df["status"].isin(["deleted", "done"]))
        ]
        for idx, row in targets.iterrows():
            type_short = self._TYPE_SHORT.get(str(row.get("node_type", "")), "")
            label = f"[{type_short}] {row['title']}" if type_short else row["title"]
            self.req_ticket.addItem(label, userData=idx)

        # 受信依頼一覧（current_member ベースで閲覧、承諾/拒否は login_user のみ）
        df_asgn = self.state.df_assignments
        self.recv_table.setRowCount(0)
        if df_asgn.empty:
            return
        view_member = self.state.current_member
        # 承諾/拒否後 10 日経過したアイテムは非表示（pending は常に表示）
        cutoff = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        responded = df_asgn["responded_at"].fillna("") if "responded_at" in df_asgn.columns \
            else pd.Series("", index=df_asgn.index)
        visible = df_asgn[
            (df_asgn["to_user"] == view_member)
            & (
                (df_asgn["status"] == "pending")
                | (responded >= cutoff)
            )
        ]
        _GRAY = QColor("#BDBDBD")
        _MINE_BG = QColor("#E3F2FD")  # 自分宛の行は薄青で強調
        for asgn_idx, asgn_row in visible.iterrows():
            t_idx = asgn_row.get("ticket_id", "")
            hier_titles, hier_grayed = self._node_hierarchy_row(t_idx)
            extra = [
                self.state.display_name(str(asgn_row.get("from_user", ""))),
                str(asgn_row.get("message", "")),
                str(asgn_row.get("status", "")),
                str(asgn_row.get("created_at", "")),
            ]
            all_vals = hier_titles + extra
            is_mine = (str(asgn_row.get("to_user", "")) == self.state.user)
            r = self.recv_table.rowCount()
            self.recv_table.insertRow(r)
            for c, val in enumerate(all_vals):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, asgn_idx)
                if c < 6 and hier_grayed[c]:
                    # 下位列はグレーアウト
                    item.setBackground(_GRAY)
                elif is_mine:
                    item.setBackground(_MINE_BG)
                self.recv_table.setItem(r, c, item)

    def _on_send(self) -> None:
        """依頼を送信する"""
        t_idx = self.req_ticket.currentData()
        if not t_idx:
            QMessageBox.information(self, "情報", "対象を選択してください")
            return

        # チェックされたメンバーを収集（自分自身を除外）
        selected_members = [
            m for m, cb in self._req_to_checks.items()
            if cb.isChecked() and m != self.state.user
        ]
        if not selected_members:
            QMessageBox.warning(self, "エラー", "送り先を1人以上選択してください")
            return

        # 選択された全メンバーに送信
        for to_user in selected_members:
            self.state.db.create_assignment(
                t_idx, self.state.user, to_user, self.req_msg.text()
            )
        self.state.df_assignments = self.state.db.read_assignments()
        self.req_msg.clear()
        # チェックボックスをリセット
        for cb in self._req_to_checks.values():
            cb.setChecked(False)
        self.refresh()
        names = ", ".join(self.state.display_name(m) for m in selected_members)
        QMessageBox.information(self, "完了", f"{names} へ依頼しました")

    def _current_node_type(self) -> str:
        """現在コンボで選択中のノードタイプを返す"""
        t_idx = self.req_ticket.currentData()
        if t_idx and t_idx in self.state.df_nodes.index:
            return str(self.state.df_nodes.loc[t_idx, "node_type"])
        return ""

    def _on_target_changed(self, _index: int) -> None:
        """対象コンボ変更時に送付先の制限と説明文を切り替える"""
        ntype = self._current_node_type()
        is_ticket = (ntype == "ticket")
        if is_ticket:
            self._req_info_lbl.setText("※ ticket: 対象のコピーを送付先に作成します（自分のアイテムは削除されません）")
        elif ntype:
            self._req_info_lbl.setText("※ task/project: 担当者の変更のみ（送付先は1名のみ選択してください）")
        else:
            self._req_info_lbl.setText("")
        # チェックボックスをすべてリセット
        for cb in self._req_to_checks.values():
            cb.setChecked(False)

    def _on_check_toggled(self, checked: bool) -> None:
        """task以上のとき、チェックを1名のみに制限する"""
        ntype = self._current_node_type()
        if checked and ntype and ntype != "ticket":
            # チェックされた送信者: 他のチェックを外す
            sender_cb = self.sender()
            for m, cb in self._req_to_checks.items():
                if cb is not sender_cb:
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)

    def select_ticket(self, ticket_idx: str) -> None:
        """外部から呼ばれた時にチケットを選択状態にする"""
        for i in range(self.req_ticket.count()):
            if self.req_ticket.itemData(i) == ticket_idx:
                self.req_ticket.setCurrentIndex(i)
                break

    def _own_selected_ids(self) -> list:
        """選択行のうち、ログインユーザー宛（to_user == user）の IDX のみ返す"""
        result = []
        for asgn_idx in self.recv_table.selected_ids():
            if asgn_idx not in self.state.df_assignments.index:
                continue
            if self.state.df_assignments.loc[asgn_idx, "to_user"] == self.state.user:
                result.append(asgn_idx)
        return result

    def _on_accept(self) -> None:
        asgn_ids = self._own_selected_ids()
        if not asgn_ids:
            self.info.set_info("⚠ 承諾できる依頼が選択されていません（自分宛のみ承諾可）")
            return
        today = datetime.date.today().isoformat()
        for asgn_idx in asgn_ids:
            t_idx = self.state.df_assignments.loc[asgn_idx, "ticket_id"]
            if t_idx in self.state.df_nodes.index:
                # 担当者をログインユーザーに変更（インメモリ＋DB即時保存）
                self.state.df_nodes.loc[t_idx, "assigned_to"] = self.state.user
                self.state.df_nodes.loc[t_idx, "updated_at"] = today
                self.state.db.upsert_node(self.state.df_nodes.loc[t_idx])
            self.state.db.respond_assignment(asgn_idx, "accepted")
        self.state.df_assignments = self.state.db.read_assignments()
        self.refresh()
        count = len(asgn_ids)
        msg = "承諾しました" if count == 1 else f"{count}件をまとめて承諾しました"
        QMessageBox.information(self, "完了", msg)

    def _on_reject(self) -> None:
        candidates = self._own_selected_ids()
        # 承諾済みは拒否不可
        asgn_ids = [
            i for i in candidates
            if i in self.state.df_assignments.index
            and self.state.df_assignments.loc[i, "status"] != "accepted"
        ]
        if not asgn_ids:
            self.info.set_info("⚠ 拒否できる依頼がありません（承諾済みまたは対象外）")
            return
        for asgn_idx in asgn_ids:
            self.state.db.respond_assignment(asgn_idx, "rejected")
        self.state.df_assignments = self.state.db.read_assignments()
        self.refresh()


# ---------- メモ ----------

class MemoView(QWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("📝 メモ（個人用）"))
        layout.addWidget(Separator())

        self.editor = QTextEdit()
        self.editor.setFont(QFont("Courier New", 10))
        self.editor.textChanged.connect(self._on_changed)
        layout.addWidget(self.editor, stretch=1)

        btn_row = ButtonRow([("保存", self._on_save)])
        layout.addWidget(btn_row)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def refresh(self) -> None:
        self.editor.blockSignals(True)
        self.editor.setPlainText(self.state.memo_text)
        self.editor.blockSignals(False)

    def _on_changed(self) -> None:
        self.state.memo_text = self.editor.toPlainText()

    def _on_save(self) -> None:
        self.state.db.save_memo(self.state.user, self.state.memo_text)
        self.info.set_info("メモを保存しました")


# ---------- バージョン・レポート ----------

class VersionView(QWidget):
    def __init__(self, state, version: str):
        super().__init__()
        self.state = state
        self.version = version
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel(f"ℹ バージョン情報"))
        layout.addWidget(Separator())

        info_text = QLabel(
            f"<b>スケジュール管理 {version}</b><br>"
            f"PySide6 ベース チームスケジュール管理ツール<br><br>"
            f"spec: a04_Schedule_Manager_v3/docs/spec.md"
        )
        info_text.setWordWrap(True)
        layout.addWidget(info_text)

        # 項目9: UPDATE_APP.txt の内容を表示
        layout.addWidget(Separator())
        layout.addWidget(QLabel("📋 アップデート内容 (UPDATE_APP.txt)"))

        self.update_text = QTextEdit()
        self.update_text.setReadOnly(True)  # 編集不可
        self.update_text.setFont(QFont("Courier New", 9))
        self.update_text.setPlaceholderText("UPDATE_APP.txt が見つかりません")

        # UPDATE_APP.txt を読み込んで表示
        _update_txt_path = Path(__file__).parent / "UPDATE_APP.txt"
        if _update_txt_path.exists():
            try:
                self.update_text.setPlainText(_update_txt_path.read_text(encoding="utf-8"))
            except Exception:
                self.update_text.setPlainText("(読み込みエラー)")

        layout.addWidget(self.update_text, stretch=1)

        layout.addWidget(Separator())
        layout.addWidget(QLabel("バグ・改善レポート"))
        self.report_edit = QTextEdit()
        self.report_edit.setPlaceholderText(
            "バグや改善提案を記入してください。\n"
            "Save ボタンで保存されます。"
        )
        layout.addWidget(self.report_edit, stretch=1)

        btn_row = ButtonRow([("保存", self._on_save)])
        layout.addWidget(btn_row)
        layout.addStretch()

    def refresh(self) -> None:
        pass

    def _on_save(self) -> None:
        """バグ・改善レポートを保存する"""
        import json
        report = self.report_edit.toPlainText()
        if not report.strip():
            QMessageBox.information(self, "情報", "内容が空です")
            return

        # 項目10: __server_log_dir.json から保存先ディレクトリを取得
        log_dir = None
        json_path = Path(__file__).parent / "__server_log_dir.json"
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                raw_path = data.get("PortablePy_Log", "")
                if raw_path:
                    # 相対パスは __server_log_dir.json の場所を基準に解決
                    log_dir = (Path(__file__).parent / raw_path).resolve()
            except Exception:
                pass

        if log_dir:
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = log_dir / f"report_{self.state.user}_{timestamp}.txt"
                filename.write_text(report, encoding="utf-8")
                self.report_edit.clear()
                QMessageBox.information(self, "完了", f"レポートを保存しました\n{filename}")
            except Exception as e:
                QMessageBox.critical(self, "保存エラー", str(e))
        else:
            # フォールバック: DB に保存
            self.state.db.save_memo(f"_report_{self.state.user}", report)
            QMessageBox.information(self, "完了", "レポートを保存しました（DB）")


# ---------- 項目8: スクロールホイール禁止スピンボックス ----------

class _NoWheelSpinBox(QSpinBox):
    """マウスホイールでの値変更を禁止したスピンボックス"""

    def wheelEvent(self, e):
        e.ignore()


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    """マウスホイールでの値変更を禁止したダブルスピンボックス"""

    def wheelEvent(self, e):
        e.ignore()


# ---------- Config 設定画面 ----------

class ConfigView(QWidget):
    """config.ini の閲覧・編集ビュー"""

    _CONFIG_PATH = Path(__file__).parent / "config.ini"

    def __init__(self, state):
        super().__init__()
        self.state = state

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        outer.addWidget(QLabel("⚙ Config 設定"))
        outer.addWidget(Separator())

        # スクロール可能エリア
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        self._form_layout = QVBoxLayout(container)
        self._form_layout.setSpacing(12)
        scroll.setWidget(container)
        outer.addWidget(scroll, stretch=1)

        # フィールド参照辞書
        self._fields: dict = {}

        self._build_section_database()
        self._build_section_user()
        self._build_section_display_names()
        self._build_section_gui()
        self._build_section_schedule()
        self._build_section_daily_info()
        self._build_section_commands()

        self._form_layout.addStretch()

        # ボタン行
        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 config.ini に保存")
        save_btn.setStyleSheet(STYLE_BUTTON)
        save_btn.clicked.connect(self._on_save)
        reload_btn = QPushButton("🔄 再読込")
        reload_btn.setStyleSheet(STYLE_BUTTON)
        reload_btn.clicked.connect(self.refresh)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(reload_btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        note = QLabel("※ 一部の設定はアプリ再起動後に反映されます。")
        note.setStyleSheet("QLabel { color: #888; font-size: 10px; }")
        outer.addWidget(note)

        self.info = InfoLabel()
        outer.addWidget(self.info)

    # ── セクション構築ヘルパー ──

    def _group(self, title: str) -> QFormLayout:
        """グループボックスを追加してその FormLayout を返す"""
        gb = QGroupBox(title)
        gb.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #CFD8DC;"
            " border-radius: 4px; margin-top: 8px; padding-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )
        fl = QFormLayout(gb)
        fl.setSpacing(6)
        self._form_layout.addWidget(gb)
        return fl

    def _text(self, key: str, value: str, form: QFormLayout, label: str) -> QLineEdit:
        w = QLineEdit(str(value))
        form.addRow(label, w)
        self._fields[key] = w
        return w

    def _spin(self, key: str, value: int, form: QFormLayout, label: str,
              mn: int = 0, mx: int = 9999) -> QSpinBox:
        # 項目8: ホイール禁止スピンボックスを使用
        w = _NoWheelSpinBox()
        w.setRange(mn, mx)
        w.setValue(int(value))
        form.addRow(label, w)
        self._fields[key] = w
        return w

    def _dspin(self, key: str, value: float, form: QFormLayout, label: str,
               mn: float = 0.0, mx: float = 24.0) -> QDoubleSpinBox:
        # 項目8: ホイール禁止ダブルスピンボックスを使用
        w = _NoWheelDoubleSpinBox()
        w.setRange(mn, mx)
        w.setDecimals(1)
        w.setSingleStep(0.5)
        w.setValue(float(value))
        form.addRow(label, w)
        self._fields[key] = w
        return w

    # ── セクション別フォーム構築 ──

    def _build_section_database(self) -> None:
        cfg = self.state.config
        fl = self._group("[Database]")
        self._text("db_server_dir", cfg.server_dir, fl, "server_dir:")

    def _build_section_user(self) -> None:
        cfg = self.state.config
        fl = self._group("[User]")
        self._text("user_username", cfg.username, fl, "username:")
        self._text("user_members", ", ".join(cfg.members), fl,
                   "members (カンマ区切り):")

    def _build_section_display_names(self) -> None:
        cfg = self.state.config
        fl = self._group("[DisplayNames]")
        for email, display in cfg.display_names.items():
            key = f"dn_{email}"
            self._text(key, display, fl, f"{email} =")

    def _build_section_gui(self) -> None:
        cfg = self.state.config
        fl = self._group("[GUI]")
        self._spin("gui_window_width",  cfg.window_width,  fl, "window_width:",  800, 3840)
        self._spin("gui_window_height", cfg.window_height, fl, "window_height:", 400, 2160)
        self._spin("gui_font_size",     cfg.font_size,     fl, "font_size:",     6, 24)

    def _build_section_schedule(self) -> None:
        cfg = self.state.config
        fl = self._group("[Schedule]")
        self._spin("sch_begin",      cfg.daily_begin_time, fl, "daily_begin_time (時):", 0, 23)
        self._spin("sch_end",        cfg.daily_end_time,   fl, "daily_end_time (時):",   0, 23)
        self._dspin("sch_task_hour", cfg.daily_task_hour,  fl, "daily_task_hour (h/日):", 0.5, 24.0)
        self._text("sch_holidays", ", ".join(cfg.holidays), fl,
                   "holidays (SUN/MON/TUE/WED/THU/FRI/SAT カンマ区切り):")

    def _build_section_daily_info(self) -> None:
        cfg = self.state.config
        fl = self._group("[DailyInfoCombo]")
        self._text("di_health",    ", ".join(cfg.health_options),     fl, "health_status:")
        self._text("di_workplace", ", ".join(cfg.work_place_options), fl, "work_place:")
        self._text("di_safety",    ", ".join(cfg.safety_options),     fl, "safety:")
        self._text("di_overwork",  ", ".join(cfg.overwork_options),   fl, "overwork:")

    def _build_section_commands(self) -> None:
        cfg = self.state.config
        fl = self._group("[Commands]")
        if not cfg.commands:
            fl.addRow(QLabel("（コマンドなし）"))
        for i, cmd in enumerate(cfg.commands, 1):
            self._text(f"cmd_{i}_label",  cmd.get("label", ""),  fl, f"command_{i:02d}_label:")
            self._text(f"cmd_{i}_script", cmd.get("script", ""), fl, f"command_{i:02d}_script:")

    # ── リフレッシュ（フォームを現在のconfig値で再構築）──

    def refresh(self) -> None:
        def _set(key: str, val):
            w = self._fields.get(key)
            if w is None:
                return
            if isinstance(w, QSpinBox):
                w.setValue(int(val))
            elif isinstance(w, QDoubleSpinBox):
                w.setValue(float(val))
            elif isinstance(w, QLineEdit):
                w.setText(str(val))

        cfg = self.state.config
        _set("db_server_dir",    cfg.server_dir)
        _set("user_username",    cfg.username)
        _set("user_members",     ", ".join(cfg.members))
        _set("gui_window_width", cfg.window_width)
        _set("gui_window_height",cfg.window_height)
        _set("gui_font_size",    cfg.font_size)
        _set("sch_begin",        cfg.daily_begin_time)
        _set("sch_end",          cfg.daily_end_time)
        _set("sch_task_hour",    cfg.daily_task_hour)
        _set("sch_holidays",     ", ".join(cfg.holidays))
        _set("di_health",        ", ".join(cfg.health_options))
        _set("di_workplace",     ", ".join(cfg.work_place_options))
        _set("di_safety",        ", ".join(cfg.safety_options))
        _set("di_overwork",      ", ".join(cfg.overwork_options))

    # ── 保存 ──

    def _get(self, key: str, default="") -> str:
        w = self._fields.get(key)
        if w is None:
            return str(default)
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            return str(w.value())
        return w.text().strip()

    def _on_save(self) -> None:
        """フォーム値を config.ini に書き込む"""
        parser = configparser.ConfigParser()
        # 既存ファイルを読んで保持
        if self._CONFIG_PATH.exists():
            parser.read(str(self._CONFIG_PATH), encoding="utf-8")

        def _ensure(section: str):
            if not parser.has_section(section):
                parser.add_section(section)

        _ensure("Database")
        parser.set("Database", "server_dir", self._get("db_server_dir"))

        _ensure("User")
        parser.set("User", "username", self._get("user_username"))
        parser.set("User", "members",  self._get("user_members"))

        _ensure("DisplayNames")
        for email in self.state.config.display_names:
            new_display = self._get(f"dn_{email}", email)
            parser.set("DisplayNames", email, new_display)

        _ensure("GUI")
        parser.set("GUI", "window_width",  self._get("gui_window_width"))
        parser.set("GUI", "window_height", self._get("gui_window_height"))
        parser.set("GUI", "font_size",     self._get("gui_font_size"))

        _ensure("Schedule")
        parser.set("Schedule", "daily_begin_time", self._get("sch_begin"))
        parser.set("Schedule", "daily_end_time",   self._get("sch_end"))
        parser.set("Schedule", "daily_task_hour",  self._get("sch_task_hour"))
        parser.set("Schedule", "holidays",         self._get("sch_holidays"))

        _ensure("DailyInfoCombo")
        parser.set("DailyInfoCombo", "health_status", self._get("di_health"))
        parser.set("DailyInfoCombo", "work_place",    self._get("di_workplace"))
        parser.set("DailyInfoCombo", "safety",        self._get("di_safety"))
        parser.set("DailyInfoCombo", "overwork",      self._get("di_overwork"))

        # Commands は既存エントリをそのまま保持（フォームに表示した分のみ更新）
        _ensure("Commands")
        cfg = self.state.config
        for i, cmd in enumerate(cfg.commands, 1):
            new_label  = self._get(f"cmd_{i}_label",  cmd.get("label", ""))
            new_script = self._get(f"cmd_{i}_script", cmd.get("script", ""))
            parser.set("Commands", f"command_{i:02d}_label",  new_label)
            parser.set("Commands", f"command_{i:02d}_script", new_script)

        try:
            with open(str(self._CONFIG_PATH), "w", encoding="utf-8") as f:
                parser.write(f)
            self.info.set_info("config.ini を保存しました（一部設定は再起動後に反映）")
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))


# ---------- AI取込ビュー ----------

class AIImportView(QWidget):
    """
    AI取込タブ：LLMにクリップボード経由で指示を渡し、
    返答を取り込んでノードを自動作成する。

    ① 指示メッセージ取得: テンプレート + 既存プロジェクト一覧をクリップボードにコピー
    ② 取り込み: LLM返答をパースして確認ダイアログ → DB登録 → Edit タブへ移動
    """

    import_done = Signal(list)  # 取り込んだ IDX のリスト

    # llm_msg.md のパス（docs/ 以下）
    _TEMPLATE_PATH = Path(__file__).parent / "documents" / "llm_msg.md"

    def __init__(self, state):
        """AI取込ビューを初期化する"""
        super().__init__()
        self.state = state

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── ボタン行 ──
        btn_row = QHBoxLayout()
        btn_get = QPushButton("📋 指示メッセージ取得")
        btn_get.setStyleSheet(STYLE_BUTTON)
        btn_get.setToolTip("テンプレート + 既存プロジェクト一覧をクリップボードにコピー")
        btn_get.clicked.connect(self._on_get_prompt)
        btn_row.addWidget(btn_get)

        btn_import = QPushButton("📥 取り込み")
        btn_import.setStyleSheet(STYLE_BUTTON)
        btn_import.setToolTip("テキストエリアの内容を解析してアイテムを登録")
        btn_import.clicked.connect(self._on_import)
        btn_row.addWidget(btn_import)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── テキストエリア ──
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "LLM の返答をここに貼り付けてください。\n"
            "```items ブロックを含む返答を認識します。"
        )
        self.text_edit.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 9pt; }"
        )
        layout.addWidget(self.text_edit, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    # ── 指示メッセージ生成 ──

    def _on_get_prompt(self) -> None:
        """テンプレートに既存プロジェクト一覧を付加してクリップボードにコピーする"""
        try:
            template = self._TEMPLATE_PATH.read_text(encoding="utf-8")
        except Exception as e:
            self.info.set_error(f"テンプレート読み込みエラー: {e}")
            return

        project_list = self._build_project_list()
        msg = template + "\n\n" + project_list
        QApplication.clipboard().setText(msg)
        self.info.set_info("指示メッセージをクリップボードにコピーしました")

    def _build_project_list(self) -> str:
        """既存アイテム一覧を IDX・種別・タイトル・階層パスの形式で文字列化する"""
        df = self.state.df_nodes
        if df.empty:
            return "【既存アイテム一覧】\n（アイテムなし）"

        lines = ["【既存アイテム一覧（IDX | 種別 | タイトル | 階層パス）】"]
        # deleted を除外し priority 順に表示
        visible = df[~df["status"].isin(["deleted"])].copy()

        def _path(idx: str) -> str:
            parts = []
            pid = str(df.loc[idx, "parent_id"]) if idx in df.index else ""
            while pid and pid != "0" and pid in df.index:
                parts.insert(0, str(df.loc[pid, "title"]))
                pid = str(df.loc[pid, "parent_id"])
            return " > ".join(parts) if parts else "(ルート)"

        for idx, row in visible.iterrows():
            ntype = str(row.get("node_type", ""))
            title = str(row.get("title", ""))
            path  = _path(str(idx))
            lines.append(f"  {idx} | {ntype} | {title} | {path}")

        return "\n".join(lines)

    # ── 取り込み ──

    def _on_import(self) -> None:
        """テキストエリアの LLM 返答をパースし、確認後に DB へ登録する"""
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.info.set_error("テキストが空です")
            return

        items, errors = self._parse_llm_response(text)
        if errors:
            self.info.set_error("パースエラー: " + " / ".join(errors))
            return
        if not items:
            self.info.set_error("取り込める内容が見つかりませんでした")
            return

        # 確認ダイアログ（プレビュー）
        preview = "\n".join(
            f"{i}. [{it['node_type']}] {it['title']}  (parent: {it['parent_idx']})"
            for i, it in enumerate(items, 1)
        )
        reply = QMessageBox.question(
            self, "取り込み確認",
            f"{len(items)} 件を取り込みます。よろしいですか？\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # DB 登録
        imported_idxs = []
        for it in items:
            parent_idx = it["parent_idx"]
            node_type  = it["node_type"]
            # 親種別に合わせて子種別を自動補正
            df = self.state.df_nodes
            if parent_idx != "0" and parent_idx in df.index:
                parent_type    = str(df.loc[parent_idx, "node_type"])
                expected_child = DB.CHILD_TYPE.get(parent_type)
                if expected_child and expected_child != node_type:
                    node_type = expected_child

            ds = DB.create_initial_node(
                owner=self.state.user,
                node_type=node_type,
                title=it["title"],
                parent_id=parent_idx,
            )
            if it.get("assigned_to"):
                ds["assigned_to"] = it["assigned_to"]
            if it.get("deadline"):
                ds["deadline"] = it["deadline"]
            if it.get("memo"):
                ds["memo"] = it["memo"]

            self.state.db.upsert_node(ds)
            imported_idxs.append(str(ds.name))

        self.state.df_nodes = self.state.db.read_nodes()
        self.state.refresh()

        self.info.set_info(f"{len(imported_idxs)} 件を取り込みました")
        self.import_done.emit(imported_idxs)

    def _parse_llm_response(self, text: str) -> tuple:
        """
        LLM 返答から ```items ブロックを抽出し、アイテムリストとエラーリストを返す。

        フォーマット:
            ```items
            title: タイトル
            parent_idx: <IDX>
            node_type: task|ticket|...
            assigned_to: ユーザー名（省略可）
            deadline: YYYY-MM-DD（省略可）
            memo: メモ（省略可）
            ---
            （複数アイテムは --- で区切る）
            ```
        """
        items: list = []
        errors: list = []
        df = self.state.df_nodes

        # ```items ... ``` ブロックを抽出
        match = re.search(r"```items\s*(.*?)```", text, re.DOTALL)
        if not match:
            errors.append("```items ... ``` ブロックが見つかりません")
            return items, errors

        block   = match.group(1).strip()
        entries = [e.strip() for e in block.split("---") if e.strip()]

        for entry in entries:
            item: dict = {}
            for line in entry.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    item[key.strip()] = val.strip()

            # 必須フィールド検証
            if not item.get("title"):
                errors.append("title が空のエントリがあります")
                continue
            if not item.get("parent_idx"):
                errors.append(f"parent_idx が空: {item.get('title', '?')}")
                continue
            if not item.get("node_type"):
                errors.append(f"node_type が空: {item.get('title', '?')}")
                continue

            # parent_idx の存在確認
            parent_idx = item["parent_idx"]
            if parent_idx != "0" and parent_idx not in df.index:
                errors.append(f"parent_idx が存在しません: {parent_idx} (title: {item.get('title')})")
                continue

            # node_type の確認
            if item["node_type"] not in DB.NODE_TYPES:
                errors.append(f"不正な node_type: {item['node_type']} (title: {item.get('title')})")
                continue

            items.append(item)

        return items, errors

    def refresh(self) -> None:
        """リフレッシュ（AI取込タブは状態保持のため何もしない）"""
