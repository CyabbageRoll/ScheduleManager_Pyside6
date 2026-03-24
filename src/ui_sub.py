"""
ui_sub.py - ガントチャート・ロードマップ・分析・検索・チームログ・依頼・メモ・バージョン・Config画面
"""
import calendar
import configparser
import datetime
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
    QTreeWidget, QTreeWidgetItem,
)
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QColor, QFont, QAction, QCursor
from PySide6.QtWidgets import QToolTip

import db as DB
import logic as LG
from ui_widgets import (
    DateButton, UserCombo, ButtonRow, InfoLabel, AutoCombo,
    ScrollableTable, Separator, COLOR_OPTIONS, STYLE_BUTTON,
)


# ---------- ガントチャート ----------

class GanttView(QWidget):
    """
    ガントチャート：Task グループ別・横日付バー表示。
    左端の DailyScheduleWidget と連動して、シングルクリックで割り当て可能。
    """
    ticket_clicked = Signal(str)   # チケット行クリック時に IDX を送出
    edit_requested = Signal(str)    # Edit メニュー選択時に IDX を送出

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
        for key, label in [("all", "全て"), ("todo", "todo"), ("done", "done"),
                            ("cancel", "cancel"), ("regularly", "regularly")]:
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
        df = self.state.df_nodes
        self._date_range = self._date_range_list()
        date_list = self._date_range

        # カラム設定
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
        global_schedule = self._compute_all_schedules(df, filter_member)

        tasks = df[
            (df["node_type"] == "task")
            & (~df["status"].isin(["deleted"]))
        ].sort_values("priority")

        # 各Taskの子チケット最早作業日を計算してTaskをソート
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

            # ── Task ヘッダー行 ──
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

                # ▶（開始可能日）/ 🚩（納期）/ 🔨（作業日）マーカー
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
                        # 納期 > 開始可能日 > 作業日 の優先順で表示
                        if deadline and d == deadline:
                            item.setText("🚩")
                            item.setBackground(QColor("#FFCDD2"))
                        elif start_avail and d == start_avail:
                            item.setText("▶")
                            item.setBackground(QColor("#C8E6C9"))
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
        """チケット行の右クリックメニュー（自分のチケットのみ操作可能）"""
        item = self.table.itemAt(pos)
        if not item:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if not idx or idx not in self.state.df_nodes.index:
            return
        if self.state.df_nodes.loc[idx, "node_type"] != "ticket":
            return
        # 自分のチケットのみ操作可能
        if self.state.df_nodes.loc[idx, "assigned_to"] != self.state.user:
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

        menu = QMenu(self)
        menu.addAction(QAction("✏ Edit", self, triggered=lambda: self.edit_requested.emit(idx)))
        menu.addSeparator()
        menu.addAction(QAction("📅 開始可能日変更", self,
                                triggered=lambda: _change_date("start_available")))
        menu.addAction(QAction("🚩 納期変更", self,
                                triggered=lambda: _change_date("deadline")))
        menu.addSeparator()
        for s_key, s_label in [("todo", "☐ ToDo"), ("done", "✓ Done"),
                                ("cancel", "✗ Cancel"), ("regularly", "↻ Regularly")]:
            act = QAction(s_label, self)
            act.triggered.connect(lambda checked=False, s=s_key: _set_status(s))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(QAction("🗑 Delete", self, triggered=_delete_ticket))
        menu.exec(self.table.viewport().mapToGlobal(pos))


# ---------- ロードマップ（スケジュール表） ----------

class RoadmapView(QWidget):
    """ロードマップ：開始日〜納期を日/週/月単位のスケジュールバーで表示"""

    edit_requested       = Signal(str)  # Edit タブにジャンプしてノードを表示
    edit_popup_requested = Signal(str)  # ポップアップダイアログで編集

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
        self._selected_parent_idx: Optional[str] = None
        self._current_level = "Task"
        self._cell_unit = "日"  # "日" / "週" / "月"

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
            btn.setChecked(label == "Task")
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
            btn.setChecked(unit == "日")
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
        self.tree.itemClicked.connect(self._on_tree_click)
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

    def _on_tree_click(self, item: QTreeWidgetItem, col: int) -> None:
        self._selected_parent_idx = item.data(0, Qt.ItemDataRole.UserRole)
        self._rebuild_table()

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
        chosen = menu.exec(QCursor.pos())
        if chosen == act_edit:
            self.edit_requested.emit(idx)

    # ── 実績データ ──

    def _build_actual_dates(self) -> dict:
        """df_daily → {ticket_idx: set[date]} のマッピングを構築"""
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
        """cell_unit に応じた (start, end, label) のリストを返す（最大 365 列）"""
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
        """idx の祖先チェーンを [(ntype, pid, title), ...] で返す（P1 が先頭）"""
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
        df = self.state.df_nodes
        try:
            d_from = datetime.date.fromisoformat(self.from_btn.get_date())
            d_to   = datetime.date.fromisoformat(self.to_btn.get_date())
        except ValueError:
            d_from = datetime.date.today()
            d_to   = d_from + datetime.timedelta(days=89)

        periods    = self._make_periods(d_from, d_to)
        total_cols = self._FIXED_COLS + len(periods)
        col_w      = 50 if self._cell_unit != "日" else self._COL_W_DATE

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
        filter_idx = self._selected_parent_idx

        # 実績データ（ticket_idx → set[date]）
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

        for idx, row in items.iterrows():
            if filter_idx is not None:
                if idx != filter_idx and not self._is_under(df, idx, filter_idx):
                    continue

            chain = self._get_parent_chain(df, idx)

            # 共通プレフィックスを計算し、差分部分にヘッダー行を挿入
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
                                cell.setText("🚩")
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
    def __init__(self, state):
        super().__init__()
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        layout.addWidget(QLabel("📈 工数分析"))
        layout.addWidget(Separator())

        # 期間選択
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("期間:"))
        self.from_edit = QLineEdit()
        self.from_edit.setPlaceholderText("YYYY-MM-DD")
        self.from_edit.setMaximumWidth(120)
        filter_row.addWidget(self.from_edit)
        filter_row.addWidget(QLabel("〜"))
        self.to_edit = QLineEdit()
        self.to_edit.setPlaceholderText("YYYY-MM-DD")
        self.to_edit.setMaximumWidth(120)
        filter_row.addWidget(self.to_edit)
        btn = QPushButton("集計")
        btn.setStyleSheet(STYLE_BUTTON)
        btn.clicked.connect(self._calc)
        filter_row.addWidget(btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        layout.addWidget(Separator())

        # 担当者別工数テーブル
        layout.addWidget(QLabel("担当者別 実績工数"))
        COLS = ["担当者", "実績工数(h)", "見積工数(h)", "チケット数"]
        self.member_table = ScrollableTable(COLS, [150, 100, 100, 80])
        self.member_table.setMaximumHeight(200)
        layout.addWidget(self.member_table)

        # PJ 別テーブル
        layout.addWidget(QLabel("Project1 別 実績工数"))
        PJ_COLS = ["Project1", "実績工数(h)", "見積工数(h)"]
        self.pj_table = ScrollableTable(PJ_COLS, [220, 100, 100])
        self.pj_table.setMaximumHeight(200)
        layout.addWidget(self.pj_table)

        # 見積 vs 実績アラート
        layout.addWidget(QLabel("見積 vs 実績（超過チケット）"))
        AL_COLS = ["タイトル", "担当者", "見積(h)", "実績(h)", "納期"]
        self.alert_table = ScrollableTable(AL_COLS, [200, 90, 65, 65, 100])
        layout.addWidget(self.alert_table, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

        # 初期日付
        today = datetime.date.today()
        month_start = today.replace(day=1).isoformat()
        self.from_edit.setText(month_start)
        self.to_edit.setText(today.isoformat())

    def refresh(self) -> None:
        self._calc()

    def _calc(self) -> None:
        df = self.state.df_nodes
        if df.empty:
            return

        # 担当者別集計
        tickets = df[df["node_type"] == "ticket"].copy()
        member_data = {}
        for _, row in tickets.iterrows():
            m = str(row.get("assigned_to", ""))
            if m not in member_data:
                member_data[m] = {"actual": 0.0, "est": 0.0, "cnt": 0}
            member_data[m]["actual"] += float(row.get("actual_hours", 0) or 0)
            member_data[m]["est"]    += float(row.get("estimated_hours", 0) or 0)
            member_data[m]["cnt"]    += 1
        m_rows = [[self.state.display_name(m), f"{v['actual']:.2f}", f"{v['est']:.2f}", v['cnt']]
                  for m, v in member_data.items()]
        self.member_table.set_rows(sorted(m_rows, key=lambda x: -float(x[1])))

        # PJ1 別集計
        pj1_list = df[df["node_type"] == "project1"]
        pj_rows = []
        for idx, row in pj1_list.iterrows():
            pj_rows.append([
                row.get("title", ""),
                f"{float(row.get('actual_hours', 0) or 0):.2f}",
                f"{float(row.get('estimated_hours', 0) or 0):.2f}",
            ])
        self.pj_table.set_rows(pj_rows)

        # 超過チケット
        over = tickets[
            tickets.apply(
                lambda r: float(r.get("actual_hours", 0) or 0)
                          > float(r.get("estimated_hours", 0) or 0) * 1.0
                          and float(r.get("estimated_hours", 0) or 0) > 0,
                axis=1,
            )
        ]
        al_rows = [[
            r.get("title", ""), self.state.display_name(str(r.get("assigned_to", ""))),
            f"{float(r.get('estimated_hours', 0)):.2f}",
            f"{float(r.get('actual_hours', 0)):.2f}",
            r.get("deadline", ""),
        ] for _, r in over.iterrows()]
        self.alert_table.set_rows(al_rows)
        self.info.set_info(
            f"チケット数: {len(tickets)} / 超過: {len(al_rows)}"
        )


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
            ("📤 CSV 出力",    self._on_export),
        ])
        layout.addWidget(btn_row)

        layout.addWidget(Separator())

        COLS = ["種類", "タイトル", "担当者", "ステータス",
                "見積(h)", "実績(h)", "更新日"]
        self.result_table = ScrollableTable(
            COLS, [70, 220, 90, 80, 65, 65, 100]
        )
        layout.addWidget(self.result_table, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

        self._last_result: Optional[pd.DataFrame] = None

    def refresh(self) -> None:
        self._on_search()

    def _on_search(self) -> None:
        statuses = [s for s, cb in self.status_checks.items() if cb.isChecked()]
        member = self.f_member.currentData() or ""
        result = LG.filter_nodes(
            self.state.df_nodes,
            keyword=self.f_kw.text(),
            statuses=statuses,
            member=member,
            date_from=self.f_from.text(),
            date_to=self.f_to.text(),
        )
        self._last_result = result
        rows = [[
            r.get("node_type", ""), r.get("title", ""),
            self.state.display_name(str(r.get("assigned_to", ""))), r.get("status", ""),
            r.get("estimated_hours", ""), r.get("actual_hours", ""),
            r.get("updated_at", ""),
        ] for _, r in result.iterrows()]
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
        self.req_to = QComboBox()
        for m in state.members:
            self.req_to.addItem(state.display_name(m), userData=m)
        self.req_msg = QLineEdit()
        self.req_msg.setPlaceholderText("メッセージ")
        req_form.addRow("チケット:", self.req_ticket)
        req_form.addRow("送り先:",   self.req_to)
        req_form.addRow("メッセージ:", self.req_msg)
        send_btn = QPushButton("依頼送信")
        send_btn.setStyleSheet(STYLE_BUTTON)
        send_btn.clicked.connect(self._on_send)
        req_form.addRow("", send_btn)
        layout.addWidget(req_box)

        layout.addWidget(Separator())

        # 受信一覧
        layout.addWidget(QLabel("受信した依頼"))
        COLS = ["チケット", "依頼者", "メッセージ", "状態", "日時"]
        self.recv_table = ScrollableTable(COLS, [180, 90, 200, 80, 100])
        layout.addWidget(self.recv_table, stretch=1)

        resp_row = ButtonRow([
            ("✅ 承諾", self._on_accept),
            ("❌ 拒否", self._on_reject),
        ])
        layout.addWidget(resp_row)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def refresh(self) -> None:
        # チケット一覧を更新
        df = self.state.df_nodes
        self.req_ticket.clear()
        tickets = df[
            (df["node_type"] == "ticket")
            & (df["assigned_to"] == self.state.user)
            & (~df["status"].isin(["deleted", "done"]))
        ]
        for idx, row in tickets.iterrows():
            self.req_ticket.addItem(row["title"], userData=idx)

        # 受信依頼一覧
        df_asgn = self.state.df_assignments
        if df_asgn.empty:
            self.recv_table.set_rows([])
            return
        mine = df_asgn[df_asgn["to_user"] == self.state.user]
        rows = []
        ids = []
        for idx, row in mine.iterrows():
            t_idx = row.get("ticket_id", "")
            t_title = df.loc[t_idx, "title"] if t_idx in df.index else t_idx
            rows.append([
                t_title,
                row.get("from_user", ""),
                row.get("message", ""),
                row.get("status", ""),
                row.get("created_at", ""),
            ])
            ids.append(idx)
        self.recv_table.set_rows(rows, ids)

    def _on_send(self) -> None:
        t_idx = self.req_ticket.currentData()
        if not t_idx:
            QMessageBox.information(self, "情報", "チケットを選択してください")
            return
        to_user = self.req_to.currentData() or self.req_to.currentText()
        if to_user == self.state.user:
            QMessageBox.warning(self, "エラー", "自分には依頼できません")
            return
        self.state.db.create_assignment(
            t_idx, self.state.user, to_user, self.req_msg.text()
        )
        self.state.df_assignments = self.state.db.read_assignments()
        self.req_msg.clear()
        self.refresh()
        QMessageBox.information(self, "完了", f"{self.state.display_name(to_user)} へ依頼しました")

    def _on_accept(self) -> None:
        asgn_idx = self.recv_table.selected_id()
        if not asgn_idx:
            return
        t_idx = self.state.df_assignments.loc[asgn_idx, "ticket_id"]
        if t_idx in self.state.df_nodes.index:
            self.state.df_nodes.loc[t_idx, "assigned_to"] = self.state.user
            self.state.df_nodes.loc[t_idx, "updated_at"] = \
                datetime.date.today().isoformat()
        self.state.db.respond_assignment(asgn_idx, "accepted")
        self.state.df_assignments = self.state.db.read_assignments()
        self.refresh()
        QMessageBox.information(self, "完了", "承諾しました")

    def _on_reject(self) -> None:
        asgn_idx = self.recv_table.selected_id()
        if not asgn_idx:
            return
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
        report = self.report_edit.toPlainText()
        self.state.db.save_memo(f"_report_{self.state.user}", report)
        QMessageBox.information(self, "完了", "レポートを保存しました")


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
        w = QSpinBox()
        w.setRange(mn, mx)
        w.setValue(int(value))
        form.addRow(label, w)
        self._fields[key] = w
        return w

    def _dspin(self, key: str, value: float, form: QFormLayout, label: str,
               mn: float = 0.0, mx: float = 24.0) -> QDoubleSpinBox:
        w = QDoubleSpinBox()
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
