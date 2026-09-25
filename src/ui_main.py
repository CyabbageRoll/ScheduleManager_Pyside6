"""
ui_main.py - メインウィンドウ・ツールバー・3ペイン・日次スケジュール
"""
import datetime
import re
from typing import Optional

import pandas as pd
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QFormLayout, QScrollArea, QMessageBox, QHeaderView,
    QFrame, QStackedWidget, QSizePolicy, QToolBar, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QStyledItemDelegate, QDateEdit, QAbstractItemDelegate, QMenu,
    QApplication, QStyle, QProgressBar, QGridLayout, QListWidget, QListWidgetItem,
)
from pathlib import Path

from PySide6.QtCore import Qt, QDate, Signal, QEvent, QTimer, QUrl, QRectF, QSettings
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QAction, QPen, QDesktopServices,
    QBrush, QPainter, QPainterPath,
)

import db as DB
import logic as LG
from schedule_app import UndoHistory
from ui_widgets import (
    DateButton, UserCombo, ColorCombo, ButtonRow, InfoLabel,
    AutoCombo, ScrollableTable, Separator, PomodoroWidget,
    COLOR_OPTIONS, STYLE_BUTTON,
)
from theme import C, qss, LEVEL_BG, LEVEL_FG, STYLE_CHIP

# 画面インデックス（QStackedWidget）
IDX_MAIN    = 0
IDX_GANTT   = 1
IDX_ROADMAP = 2
IDX_ANALYSIS= 3
IDX_SEARCH  = 4
IDX_TEAM    = 5
IDX_ASSIGN  = 6
IDX_AIIMPORT = 7
IDX_VERSION  = 8
IDX_CONFIG   = 9
IDX_TODAY    = 10


# ---------- 日次スケジュール用カスタムデリゲート ----------

class _HourLineDelegate(QStyledItemDelegate):
    """日次スケジュールの描画: 毎時の実線・15 分の点線・角丸のチケットカード"""

    _CARD_RADIUS = 5   # カードの角丸
    _CARD_INSET  = 3   # カードの左右の余白
    _STRIPE_W    = 4   # カード左端のカラーストライプ幅

    # チケット行はテキストと背景を手動描画するため、initStyleOption でクリア
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 1 and index.data(Qt.ItemDataRole.UserRole + 1):
            option.text = ""                  # super().paint() での描画を抑制
            option.backgroundBrush = QBrush()  # 四角い背景の塗りを抑制（角丸カードで描く）

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        r = option.rect
        col = index.column()
        pos = index.data(Qt.ItemDataRole.UserRole + 1) if col == 1 else None

        # ── 15 分の区切り: 下端に淡い点線（カードの中は描かない）──
        if not pos:
            painter.save()
            pen = QPen(QColor(C.SLOT_LINE))
            pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.drawLine(r.bottomLeft(), r.bottomRight())
            painter.restore()

        # ── 毎時区切り線（row % 4 == 0 = 00分の行）──
        if index.row() % 4 == 0:
            # 同一チケットが時間境界をまたいでいる場合は区切り線を非表示
            skip_hour_line = False
            if col == 1 and index.row() > 0:
                cur_t = index.data(Qt.ItemDataRole.UserRole)
                prev_t = index.sibling(index.row() - 1, 1).data(Qt.ItemDataRole.UserRole)
                if cur_t and cur_t == prev_t:
                    skip_hour_line = True
            if not skip_hour_line:
                painter.save()
                painter.setPen(QPen(QColor(C.HOUR_LINE)))
                painter.drawLine(r.topLeft(), r.topRight())
                painter.restore()

        if not pos:
            return

        # ── チケットカード描画（連続スロットを 1 枚の角丸カードに見せる）──
        bg_data = index.data(Qt.ItemDataRole.BackgroundRole)
        if bg_data is not None:
            fill = bg_data.color() if hasattr(bg_data, "color") else QColor(bg_data)
        else:
            fill = QColor(C.SLOT_CARD)
            fill.setAlpha(110)
        solid = QColor(fill.red(), fill.green(), fill.blue())
        border = QColor(fill.red(), fill.green(), fill.blue(), 150)

        # このセルに見えるカード範囲（先頭・末尾だけ上下に 1px の隙間）
        rad = self._CARD_RADIUS
        card = QRectF(r).adjusted(self._CARD_INSET, 0, -self._CARD_INSET, 0)
        if pos in ("first", "single"):
            card.setTop(card.top() + 1)
        if pos in ("last", "single"):
            card.setBottom(card.bottom() - 1)
        # 角丸にしない側はセル外へ伸ばしてからクリップし、隣のセルと継ぎ目なくつなぐ
        shape = QRectF(card)
        if pos in ("middle", "last"):
            shape.setTop(shape.top() - rad * 2)
        if pos in ("first", "middle"):
            shape.setBottom(shape.bottom() + rad * 2)
        path = QPainterPath()
        path.addRoundedRect(shape.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(card)
        painter.fillPath(path, fill)
        # 左端のカラーストライプ（カードの角丸に沿って切り抜く）
        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        painter.fillRect(QRectF(card.left(), card.top(), self._STRIPE_W, card.height()), solid)
        painter.restore()
        painter.setPen(QPen(border, 1))
        painter.drawPath(path)
        painter.restore()

        # ── テキストをストライプの右に描画 ──
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            is_sel = bool(option.state & QStyle.StateFlag.State_Selected)
            text_color = (option.palette.highlightedText().color() if is_sel
                          else option.palette.text().color())
            # 親パス行はフォントを1pt小さくする
            font = QFont(option.font)
            if index.data(Qt.ItemDataRole.UserRole + 2) == "path":
                font.setPointSize(max(font.pointSize() - 1, 6))
            painter.save()
            painter.setPen(text_color)
            painter.setFont(font)
            text_rect = card.toRect().adjusted(self._STRIPE_W + 4, 0, -3, 0)
            fm = painter.fontMetrics()
            elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect,
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             elided)
            painter.restore()


# ---------- 日次スケジュールウィジェット ----------

class DailyScheduleWidget(QWidget):
    """
    左端に常時表示される日次スケジュールパネル。
    00:00〜23:45 を 15 分刻みで表示し、各スロットにチケットを割り当てられる。
    健康状態・就業場所・常時メモなどの日次ログ入力フォームも内包する。
    """
    # 選択スロットで「新しいチケットを作って割り当て」（行番号リスト）
    quick_add_requested = Signal(list)

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
        input_frame.setStyleSheet(qss(
            "QFrame { background:@card_alt; border-radius:4px; border:1px solid @border; }"
            "QLabel { border:none; font-size:7pt; }"
        ))
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

        # 日次ログは選択変更・入力確定のたびに自動保存
        for combo in (self.f_health, self.f_work_place, self.f_safety, self.f_overwork):
            combo.activated.connect(lambda _: self._on_save_log())
            combo.lineEdit().editingFinished.connect(self._on_save_log)
        self.f_notes.editingFinished.connect(self._on_save_log)

        layout.addWidget(input_frame)

        layout.addWidget(QLabel("📅 日次スケジュール"))

        # ボタン行
        btn_row = QHBoxLayout()
        self.free_btn = QPushButton("Free")
        self.free_btn.setStyleSheet(STYLE_BUTTON)
        self.free_btn.clicked.connect(self._on_free)
        btn_row.addWidget(self.free_btn)
        self.wh_label = QLabel("─")
        self.wh_label.setStyleSheet(qss("font-size:7pt; color:@text_sub; padding-left:4px;"))
        btn_row.addWidget(self.wh_label, stretch=1)
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
        self.schedule_table.setShowGrid(False)  # グリッド線を非表示（カードのシームレス描画のため）
        self.schedule_table.verticalHeader().setDefaultSectionSize(16)
        self.schedule_table.setStyleSheet(qss(
            "QTableWidget { border: 1px solid @border;"
            " font-size: 8pt; background: @surface; }"
            "QTableWidget::item:selected { background: @slot_select_bg; color: @slot_select_text; }"
        ))
        # デリゲートを設定（毎時00分に区切り線、同一チケットに囲み線を描画）
        self.schedule_table.setItemDelegate(_HourLineDelegate(self.schedule_table))

        # 右クリックメニュー（最近使った + 階層カスケードで割り当て）
        self.schedule_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.schedule_table.customContextMenuRequested.connect(self._on_slot_context_menu)

        # 時刻ラベルを設定（全行フル表示、毎時にスタイルを付与）
        hour_font = QFont()
        hour_font.setBold(True)
        hour_font.setPointSize(8)
        sub_font = QFont()
        sub_font.setPointSize(7)
        _HOUR_BG = QColor(C.HOUR_BG)   # 毎時00分行の背景色（青灰系）
        for i in range(96):
            hh, mm = i // 4, (i % 4) * 15
            hh_next, mm_next = (i + 1) // 4, ((i + 1) % 4) * 15
            time_label = f"{hh:02d}:{mm:02d}〜{hh_next:02d}:{mm_next:02d}"
            item = QTableWidgetItem(time_label)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            task_item = QTableWidgetItem("")
            task_item.setFlags(task_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if mm == 0:
                # 毎時00分: 太字・スレートブルー・薄い青灰背景
                item.setData(Qt.ItemDataRole.UserRole, "hour")
                item.setFont(hour_font)
                item.setForeground(QColor(C.TEXT))
                item.setBackground(_HOUR_BG)
                task_item.setData(Qt.ItemDataRole.UserRole, "hour")
                task_item.setBackground(_HOUR_BG)
            else:
                # サブ行: 少し小さめ・グレー
                item.setFont(sub_font)
                item.setForeground(QColor(C.TEXT_MUTED))
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
            self._assign_to_rows(rows, ticket_idx)
            self.schedule_table.clearSelection()  # 割り当て後は選択解除
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

    def _assign_to_rows(self, rows: list, ticket_idx: str) -> bool:
        """指定スロット行に自分担当チケットを割り当てる。

        ガント行クリック・右クリックメニューの双方から呼ばれる共通処理。
        割り当てたら最近使ったリストを更新し True を、不可なら False を返す。
        """
        if not rows or ticket_idx not in self.state.df_nodes.index:
            return False
        if self.state.current_member != self.state.user:
            return False
        node = self.state.df_nodes.loc[ticket_idx]
        is_own_ticket = (node["node_type"] == "ticket"
                         and str(node["assigned_to"]) == self.state.user)
        title = str(node["title"])
        if not is_own_ticket:
            self.info.set_info(f"⚠ {title} は自分のチケットではありません")
            return False
        self._update_schedule_slots(rows, ticket_idx)
        self.state.push_recent_ticket(ticket_idx)
        self.info.set_info(f"割り当て完了: {title}")
        return True

    # ── 右クリックメニューによる割り当て ──

    def _on_slot_context_menu(self, pos) -> None:
        """スロット表の右クリックメニュー（最近使った + 階層カスケード + クリア）"""
        if self.state.current_member != self.state.user:
            return  # 他人のスケジュール表示中は割り当て不可
        # 対象行: 選択があれば選択行、無ければ右クリックした行
        rows = sorted(set(idx.row() for idx in self.schedule_table.selectedIndexes()))
        if not rows:
            r = self.schedule_table.rowAt(pos.y())
            if r < 0:
                return
            rows = [r]

        df = self.state.df_nodes
        menu = QMenu(self)
        # チケットが無いことに気づいたらその場で作って割り当てる（Ctrl+N と同じ）
        new_act = menu.addAction("＋ 新しいチケットを作成して割り当て…  (Ctrl+N)")
        new_act.triggered.connect(
            lambda checked=False: self.quick_add_requested.emit(list(rows)))
        menu.addSeparator()

        # 自分担当・割り当て可能(todo/regularly)のチケット
        if df.empty:
            assignable_idx = pd.Index([])
        else:
            assignable_idx = df[
                (df["node_type"] == "ticket")
                & (df["assigned_to"] == self.state.user)
                & (df["status"].isin(["todo", "regularly"]))
            ].index

        # ── 最近使った ──
        recent_idxs = [i for i in self.state.recent_tickets if i in assignable_idx]
        if recent_idxs:
            menu.addSection("最近使った")
            for t_idx in recent_idxs:
                act = menu.addAction(self._ticket_menu_label(df, t_idx))
                act.triggered.connect(
                    lambda checked=False, ti=t_idx: self._assign_to_rows(rows, ti)
                )
            menu.addSeparator()

        # ── 2階層メニュー（プロジェクトパス ▸ Task/Ticket、Edit と同じ priority 昇順）──
        for group_label, leaves in self._assignable_groups(df, assignable_idx):
            sub = menu.addMenu(group_label)
            for t_idx, leaf_label in leaves:
                act = sub.addAction(leaf_label)
                act.triggered.connect(
                    lambda checked=False, ti=t_idx: self._assign_to_rows(rows, ti)
                )

        # ── クリア ──
        menu.addSeparator()
        clear_act = menu.addAction("クリア（割り当て解除）")
        clear_act.triggered.connect(
            lambda checked=False: self._update_schedule_slots(rows, "")
        )

        menu.exec(self.schedule_table.viewport().mapToGlobal(pos))

    def _ticket_menu_label(self, df: pd.DataFrame, t_idx: str) -> str:
        """最近使った節用ラベル: 親(Task)名 / チケット名"""
        title = str(df.loc[t_idx, "title"]) if t_idx in df.index else ""
        pid = str(df.loc[t_idx, "parent_id"] or "") if t_idx in df.index else ""
        parent_title = str(df.loc[pid, "title"]) if pid in df.index else ""
        return f"{parent_title} / {title}" if parent_title else title

    def _priority_path(self, df: pd.DataFrame, idx: str) -> tuple:
        """ルートから idx までの priority タプルを返す（Edit と同じ DFS 順のソートキー）"""
        path = []
        cur = idx
        while cur and cur != "0" and cur in df.index:
            try:
                path.append(int(df.loc[cur, "priority"]))
            except (ValueError, TypeError):
                path.append(0)
            cur = str(df.loc[cur, "parent_id"] or "")
        path.reverse()
        return tuple(path)

    def _assignable_groups(self, df: pd.DataFrame, assignable_idx) -> list:
        """割り当て可能チケットを「プロジェクトパス」グループにまとめて返す。

        2階層メニュー用。各チケットの最も近い project 系祖先を group_node とし、
        group_label = ルート〜group_node の project タイトルを " / " 連結、
        leaf_label  = group_node〜チケット間の Task タイトル + チケットタイトルを " / " 連結。
        並びはグループ・リーフとも Edit と同じ priority パス順。

        Returns: [(group_label, [(ticket_idx, leaf_label), ...]), ...]
        """
        groups: dict = {}  # group_node_id -> {"label", "key", "leaves": [(key, idx, leaf_label)]}
        for t_idx in assignable_idx:
            # 祖先チェーンを収集（project 系と非 project 系を分ける）
            proj_titles, mid_titles = [], []
            group_node = None
            cur = str(df.loc[t_idx, "parent_id"] or "") if t_idx in df.index else ""
            while cur and cur != "0" and cur in df.index:
                ntype = str(df.loc[cur, "node_type"])
                title = str(df.loc[cur, "title"])
                if ntype.startswith("project"):
                    if group_node is None:
                        group_node = cur
                    proj_titles.append(title)
                else:
                    mid_titles.append(title)
                cur = str(df.loc[cur, "parent_id"] or "")
            if group_node is None:
                # Task 未設定（Inbox）のチケットは専用グループにまとめる
                in_inbox = str(df.loc[t_idx, "parent_id"]) == DB.INBOX_PARENT
                group_node = DB.INBOX_PARENT if in_inbox else "0"
            proj_titles.reverse()
            mid_titles.reverse()
            group_label = (" / ".join(proj_titles) if proj_titles
                           else "📥 Inbox（Task 未設定）" if group_node == DB.INBOX_PARENT
                           else "（プロジェクト未設定）")

            status_icon = "↻ " if str(df.loc[t_idx, "status"]) == "regularly" else ""
            leaf_parts = mid_titles + [str(df.loc[t_idx, "title"])]
            leaf_label = status_icon + " / ".join(leaf_parts)

            g = groups.setdefault(group_node, {
                "label": group_label,
                "key": (self._priority_path(df, group_node)
                        if group_node not in ("0", DB.INBOX_PARENT) else ()),
                "leaves": [],
            })
            g["leaves"].append((self._priority_path(df, t_idx), t_idx, leaf_label))

        # グループを priority パス順、リーフも priority パス順でソート
        ordered = sorted(groups.values(), key=lambda g: g["key"])
        result = []
        for g in ordered:
            leaves = [(t_idx, label) for _, t_idx, label in sorted(g["leaves"], key=lambda x: x[0])]
            result.append((g["label"], leaves))
        return result

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
        """選択中のメンバー・日付の入力欄に保存済み値をセット"""
        date = self.state.current_date
        member = self.state.current_member or self.state.user
        idx = DB.daily_sch_idx(date, member)
        df_log = getattr(self.state, "df_daily_log", None)
        if df_log is not None and not df_log.empty and idx in df_log.index:
            row = df_log.loc[idx]
            self.f_health.set_value(str(row.get("health_status", "") or ""))
            self.f_work_place.set_value(str(row.get("work_place", "") or ""))
            self.f_safety.set_value(str(row.get("safety", "") or ""))
            self.f_overwork.set_value(str(row.get("overwork", "") or ""))
            self.f_notes.setText(str(row.get("notes", "") or ""))
        else:
            self.f_health.set_value("")
            self.f_work_place.set_value("")
            self.f_safety.set_value("")
            self.f_overwork.set_value("")
            self.f_notes.setText("")
        # 常時メモ（メンバーごと）
        notices = getattr(self.state, "all_permanent_notices", {})
        self.f_notes_ever.setText(notices.get(member, ""))
        # 他人のデータは閲覧のみ
        is_own = (member == self.state.user)
        for w in [self.f_health, self.f_work_place, self.f_safety,
                  self.f_overwork, self.f_notes, self.f_notes_ever]:
            w.setEnabled(is_own)

    def _on_save_log(self) -> None:
        """選択中メンバーの入力フォームを保存する（自分のデータのみ）"""
        member = self.state.current_member or self.state.user
        if member != self.state.user:
            return
        date = self.state.current_date
        idx = DB.daily_sch_idx(date, member)
        today = datetime.date.today().isoformat()
        row = {
            "Owner":         member,
            "health_status": self.f_health.get_value(),
            "work_place":    self.f_work_place.get_value(),
            "safety":        self.f_safety.get_value(),
            "overwork":      self.f_overwork.get_value(),
            "notes":         self.f_notes.text(),
            "Last_Update":   today,
        }
        # 値が変わっていなければ何もしない（フォーカス移動だけで未保存扱いにしない）
        fields = ["health_status", "work_place", "safety", "overwork", "notes"]
        df_log = self.state.df_daily_log
        if idx in df_log.index:
            old = df_log.loc[idx]
            if all(str(old.get(k, "") or "") == row[k] for k in fields):
                return
        elif not any(row[k] for k in fields):
            return
        self.state.df_daily_log.loc[idx] = row
        self.state.schedule_modified = True
        self.state.notify_dirty()
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
        _HOUR_BG = QColor(C.HOUR_BG)
        for i in range(96):
            item = self.schedule_table.item(i, 1)
            if item:
                item.setText("")
                # 毎時00分行（i % 4 == 0）の背景は保持し、それ以外は白に戻す
                item.setBackground(_HOUR_BG if i % 4 == 0 else QColor(C.SURFACE))
                item.setData(Qt.ItemDataRole.UserRole, None)
                # 囲み線の位置マークもクリア（枠線が残らないようにする）
                item.setData(Qt.ItemDataRole.UserRole + 1, "")
                # パス行フラグもクリア
                item.setData(Qt.ItemDataRole.UserRole + 2, "")

        if df.empty or sch_idx not in df.index:
            self.wh_label.setText("─")
            return

        ds = df.loc[sch_idx]

        # ── Step1: position_marks を先に計算 ──
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

        # ── Step2: display を position_marks を使って計算 ──
        # single   : チケット名のみ（親パス不要）
        # first (row=0): チケット名（最重要情報を先頭へ）
        # second (row=1): 親パス（コンテキスト情報）
        # row 3+   : 空白（カードで視覚的に判断）
        display = [""] * 96
        is_path = [False] * 96   # 親パス行フラグ（フォントサイズを1pt小さくする）
        current_ticket = ""
        group_row = 0
        for i, col in enumerate(DB.DAILY_TIME_COLS):
            t_idx = ds[col] if col in ds.index else ""
            if not t_idx:
                current_ticket = ""
                group_row = 0
                continue
            if t_idx != current_ticket:
                current_ticket = t_idx
                group_row = 0
            title = df_nodes.loc[t_idx, "title"] if t_idx in df_nodes.index else t_idx
            pos = position_marks[i]
            if pos == "single":
                display[i] = title
            elif group_row == 0:
                display[i] = title
            elif group_row == 1:
                path_parts: list = []
                if t_idx in df_nodes.index:
                    pid = str(df_nodes.loc[t_idx, "parent_id"])
                    while pid and pid != "0" and pid in df_nodes.index:
                        path_parts.insert(0, str(df_nodes.loc[pid, "title"]))
                        pid = str(df_nodes.loc[pid, "parent_id"])
                display[i] = " / ".join(path_parts) if path_parts else ""
                is_path[i] = True   # 親パス行としてマーク
            # row 3+ は "" のまま
            group_row += 1

        for i in range(96):
            col = DB.DAILY_TIME_COLS[i]
            t_idx = ds[col] if col in ds.index else ""
            item = self.schedule_table.item(i, 1)
            if item:
                item.setText(display[i])
                item.setData(Qt.ItemDataRole.UserRole, t_idx)
                # 項目6: グループ内位置をアイテムに保存
                item.setData(Qt.ItemDataRole.UserRole + 1, position_marks[i])
                # 項目7: 親パス行フラグ（デリゲートでフォントサイズを縮小する）
                item.setData(Qt.ItemDataRole.UserRole + 2, "path" if is_path[i] else "")
                if t_idx and t_idx in df_nodes.index:
                    hex_c = COLOR_OPTIONS.get(
                        df_nodes.loc[t_idx, "color"], C.NODE_DEFAULT
                    )
                    bg = QColor(hex_c)
                    bg.setAlpha(110)   # カードの視認性向上
                    item.setBackground(bg)

        # 勤務時間ラベルを更新
        wh = LG.calc_working_hours(df, sch_idx)
        if wh["total"] > 0:
            wh_str = (f"{LG.col_to_hhmm(wh['from'])} 〜 "
                      f"{LG.col_to_hhmm(wh['to'])} "
                      f"[{wh['total']:.2f}h]")
        else:
            wh_str = "─"
        self.wh_label.setText(wh_str)

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
        # df_daily と df_nodes(actual_hours) が変更されたため両フラグをセット
        self.state.schedule_modified = True
        self.state.nodes_modified = True
        self.state.notify_dirty()
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

        # AppState にリフレッシュ関数・dirty 通知コールバックを登録
        state.refresh_func = self.refresh
        state.dirty_changed_func = self._update_save_btn_style

        # 元に戻す / やり直し（編集のたびに状態を記録。1 操作内の複数変更は 1 回にまとめる）
        self._qa_recent_tasks: list = []  # クイック追加で最近使った Task（候補の上位表示用）
        self._undo = UndoHistory()
        self._undo_pending = False
        self._undo.reset(state)
        state.modified_func = self._schedule_undo_snapshot
        state.undo_reset_func = self._reset_undo

        # 初期表示タブ（config の start_tab で変更可）
        start_map = {"today": IDX_TODAY, "main": IDX_GANTT,
                     "edit": IDX_MAIN, "plan": IDX_ROADMAP}
        self._switch_view(start_map.get(state.config.start_tab, IDX_TODAY))

    # ---------- ツールバー ----------

    def _build_toolbar(self) -> None:
        tb = QToolBar("メインツールバー")
        tb.setMovable(False)
        tb.setStyleSheet(qss(
            "QToolBar { background: @toolbar_bg; border-bottom: 1px solid @border; padding: 4px; }"
        ))
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

        # Save / Load（保存ボタンは dirty 時に橙色で強調するため参照を保持）
        self._save_btn = None
        for i, (label, slot) in enumerate([("💾 保存 (Ctrl+S)", self._on_save),
                                            ("🔄 読込 (Ctrl+R)", self._on_load)]):
            act = QAction(label, self)
            act.triggered.connect(slot)
            tb.addAction(act)
            if i == 0:  # 保存ボタン（i==0）の QToolButton 参照を保持
                self._save_btn = tb.widgetForAction(act)

        # 📥 Inbox（件数表示・上限/滞留で強調。クリックで振り分け画面）
        self.inbox_btn = QPushButton("📥 Inbox")
        self.inbox_btn.setToolTip("Task 未設定チケットを振り分けます（Ctrl+N で追加）")
        self.inbox_btn.clicked.connect(self._open_inbox_triage)
        tb.addWidget(self.inbox_btn)

        tb.addSeparator()

        # 画面切替ボタン（等幅・角丸のピル型。選択中はアクセント色で塗る）
        _TAB_STYLE = qss(
            "QPushButton {"
            " background: transparent;"
            " color: @tab_text;"
            " border: 1px solid transparent;"
            " border-radius: 8px;"
            " padding: 5px 0px;"
            " font-size: 8pt; font-weight: bold;"
            " min-width: 74px; max-width: 74px; }"
            "QPushButton:checked {"
            " background: @accent;"
            " color: @on_accent;"
            " border-color: @accent; }"
            "QPushButton:hover:!checked {"
            " background: @accent_bg; color: @accent_dark; }"
        )
        views = [
            ("🏠 Today",   IDX_TODAY),
            ("📊 Main",    IDX_GANTT),
            ("🗺 Plan",    IDX_ROADMAP),
            ("✏ Edit",    IDX_MAIN),
            ("👥 Team",    IDX_TEAM),
            ("🔍 Search",  IDX_SEARCH),
            ("📨 Request", IDX_ASSIGN),
            ("🤖 AI取込",  IDX_AIIMPORT),
            ("📈 Analyze", IDX_ANALYSIS),
            ("ℹ Version", IDX_VERSION),
            ("⚙ Config",  IDX_CONFIG),
        ]
        self._tab_style = _TAB_STYLE  # バッジリセット時に使用
        self._tab_btns: dict = {}
        self._tab_labels: dict = {}  # 番号付きの元ラベル（バッジ表示後の復元用）
        # タブの並び順（一番左を1番として Ctrl+番号 と対応させる）
        self._view_order = [view_idx for _, view_idx in views]
        for n, (label, view_idx) in enumerate(views, start=1):
            # 並び順に合わせて番号を付与（Ctrl+番号 のショートカットと一致）
            num_label = f"{n} {label}" if n <= 9 else label
            btn = QPushButton(num_label)
            btn.setCheckable(True)
            btn.setStyleSheet(_TAB_STYLE)
            btn.clicked.connect(lambda checked, vi=view_idx: self._switch_view(vi))
            if n <= 9:
                btn.setToolTip(f"Ctrl+{n} で切替")
            tb.addWidget(btn)
            self._tab_btns[view_idx] = btn
            self._tab_labels[view_idx] = num_label

        tb.addSeparator()

        # 詳細ペイン表示トグル（main/plan/edit で右の詳細ペインを開閉）
        self.detail_toggle_btn = QPushButton("🔎 詳細")
        self.detail_toggle_btn.setCheckable(True)
        self.detail_toggle_btn.setChecked(self.state.config.detail_pane_open)
        self.detail_toggle_btn.setStyleSheet(STYLE_BUTTON)
        self.detail_toggle_btn.setToolTip("右の詳細ペインの表示/非表示（main/plan/edit）")
        self.detail_toggle_btn.toggled.connect(self._on_toggle_detail)
        tb.addWidget(self.detail_toggle_btn)

        # マニュアルを開くボタン
        manual_btn = QPushButton("📖 Manual")
        manual_btn.setStyleSheet(STYLE_BUTTON)
        manual_btn.setToolTip("マニュアルをブラウザで開く")
        manual_btn.clicked.connect(self._on_open_manual)
        tb.addWidget(manual_btn)

        tb.addSeparator()

        # ポモドーロタイマー（終了時に実績をスケジュールへ記録）
        self.pomodoro = PomodoroWidget(
            work_minutes=self.state.config.pomodoro_work_minutes,
            break_minutes=self.state.config.pomodoro_break_minutes,
        )
        self.pomodoro.work_finished.connect(self._on_pomodoro_finished)
        tb.addWidget(self.pomodoro)

        # 項目7: 2行目のツールバーにメンバーボタンを追加
        self.addToolBarBreak()
        tb2 = QToolBar("メンバー選択ツールバー")
        tb2.setMovable(False)
        tb2.setStyleSheet(qss(
            "QToolBar { background: @bg_soft; border-bottom: 1px solid @border; padding: 2px; }"
        ))
        self.addToolBar(tb2)
        tb2.addWidget(QLabel(" メンバー: "))

        # メンバーボタン（ボタン形式で素早く切替）
        _MEMBER_STYLE = STYLE_CHIP
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
        manual_path = Path(__file__).parent / "documents" / "manual.html"
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

        # 右端の共通詳細ペイン（main/plan/edit で表示、トグルで開閉）
        self.detail_pane = DetailPane(self.state)
        self._detail_visible = self.state.config.detail_pane_open  # トグルボタンの状態

        # 外側スプリッター（左: 日次 / 中: スタック / 右: 詳細）
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self.schedule_panel)
        outer.addWidget(self.stack)
        outer.addWidget(self.detail_pane)
        outer.setSizes([400, 900, 320])
        self._outer_splitter = outer  # 画面状態の記憶用

        self.setCentralWidget(outer)

        # 各ビューを生成してスタックに追加
        self.main_pane   = _Main3Pane(self.state)
        self.gantt_view  = ui_sub.GanttView(self.state)
        self.road_view   = ui_sub.RoadmapView(self.state)
        self.anal_view   = ui_sub.AnalysisView(self.state)
        self.search_view = ui_sub.SearchView(self.state)
        self.team_view   = ui_sub.TeamLogView(self.state)
        self.assign_view = ui_sub.AssignmentView(self.state)
        self.ver_view    = ui_sub.VersionView(self.state, self.version)
        self.config_view     = ui_sub.ConfigView(self.state)
        self.ai_import_view  = ui_sub.AIImportView(self.state)
        self.dashboard_view  = ui_sub.DashboardView(self.state)

        # 追加順は IDX_* 定数と一致させること（QStackedWidget のインデックス）
        for w in [self.main_pane, self.gantt_view, self.road_view,
                  self.anal_view, self.search_view, self.team_view,
                  self.assign_view, self.ai_import_view,
                  self.ver_view, self.config_view,
                  self.dashboard_view]:
            self.stack.addWidget(w)

        # ダッシュボードの「開く」 → 対応タブへ遷移
        self.dashboard_view.navigate_requested.connect(self._on_dashboard_navigate)
        # チケット選択をポモドーロタイマーの対象に反映
        self.gantt_view.ticket_clicked.connect(self._on_pomodoro_ticket)
        self.main_pane.tree_pane.node_selected.connect(self._on_pomodoro_ticket)

        # 共通 DetailPane へのノード選択配線（main/plan/edit の各画面）
        self.main_pane.tree_pane.node_selected.connect(self.detail_pane.update_for_node)
        self.main_pane.table_pane.node_selected.connect(self.detail_pane.update_for_node)
        self.gantt_view.ticket_clicked.connect(self.detail_pane.update_for_node)
        self.road_view.node_selected.connect(self.detail_pane.update_for_node)

        # シグナル接続：チケット選択 → スケジュールパネルへ（ガントチャートからのみ）
        # インライン編集後の軽量リフレッシュ（ツリー再構築なし）
        self.main_pane.table_pane.schedule_refresh.connect(self.schedule_panel.refresh)
        self.gantt_view.ticket_clicked.connect(self.schedule_panel.assign_ticket)
        self.schedule_panel.quick_add_requested.connect(self._on_quick_add)
        self.gantt_view.edit_requested.connect(self._on_gantt_edit_requested)
        self.road_view.edit_requested.connect(self._on_gantt_edit_requested)
        self.road_view.edit_popup_requested.connect(self._on_roadmap_edit_popup)
        # 項目3: Request シグナル接続
        self.gantt_view.request_requested.connect(self._on_request_requested)
        self.road_view.request_requested.connect(self._on_request_requested)
        self.main_pane.tree_pane.request_requested.connect(self._on_request_requested)
        # AI取込完了 → Edit タブへ
        self.ai_import_view.import_done.connect(self._on_ai_import_done)
        # Edit タブの dirty 変化 → 保存ボタン色更新
        self.main_pane.table_pane.dirty_changed.connect(self._update_save_btn_style)
        # Edit タブのインメモリ変更 → ツリーと現在表示中ビューを更新
        self.main_pane.table_pane.nodes_changed.connect(self._on_nodes_changed)

    # ---------- ショートカット ----------

    def _setup_shortcuts(self) -> None:
        """キーボードショートカットを登録する（Ctrl+S: 保存、Ctrl+R: 読込、Ctrl+1〜9: ビュー切替）

        Ctrl+番号 はタブの並び順（一番左が1）に対応させる。
        """
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._on_save)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._on_load)
        # 入力欄の編集中は各欄の文字単位の Undo が優先される（Qt の標準動作）
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(lambda: self._on_quick_add())
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self._on_undo)
        for seq in ("Ctrl+Shift+Z", "Ctrl+Y"):
            QShortcut(QKeySequence(seq), self).activated.connect(self._on_redo)
        for n, view_idx in enumerate(self._view_order[:9], start=1):
            QShortcut(QKeySequence(f"Ctrl+{n}"), self).activated.connect(
                lambda checked=False, vi=view_idx: self._switch_view(vi)
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
        # 詳細ペインの表示制御（main/plan/edit のみ）
        self._update_detail_visibility()
        # 未処理 Request バッジを常に最新化（起動時含む）
        if hasattr(self, "_tab_btns"):
            self._update_request_tab_badge()

    def _on_toggle_detail(self, checked: bool) -> None:
        """🔎 詳細トグル: 詳細ペインの表示/非表示を切り替える"""
        self._detail_visible = checked
        self._update_detail_visibility()

    def _update_detail_visibility(self) -> None:
        """詳細ペインは main(ガント)/plan(ロードマップ)/edit でのみ、かつトグルON時に表示"""
        if not hasattr(self, "detail_pane"):
            return
        visible = (getattr(self, "_detail_visible", True)
                   and self.stack.currentIndex() in (IDX_MAIN, IDX_GANTT, IDX_ROADMAP))
        self.detail_pane.setVisible(visible)
        if visible:
            self.detail_pane.refresh()

    # ---------- スロット ----------

    def _on_date_changed(self, date_str: str) -> None:
        self._commit_pending_edits()  # 切替前に入力中の日次ログを確定
        self.state.current_date = date_str
        self.refresh()

    def _on_prev_day(self) -> None:
        self._commit_pending_edits()  # 切替前に入力中の日次ログを確定
        d = datetime.date.fromisoformat(self.state.current_date)
        new_date = (d - datetime.timedelta(days=1)).isoformat()
        self.state.current_date = new_date
        self.date_btn.set_date(new_date)
        self.refresh()

    def _on_next_day(self) -> None:
        self._commit_pending_edits()  # 切替前に入力中の日次ログを確定
        d = datetime.date.fromisoformat(self.state.current_date)
        new_date = (d + datetime.timedelta(days=1)).isoformat()
        self.state.current_date = new_date
        self.date_btn.set_date(new_date)
        self.refresh()

    def _on_today(self) -> None:
        self._commit_pending_edits()  # 切替前に入力中の日次ログを確定
        new_date = datetime.date.today().isoformat()
        self.state.current_date = new_date
        self.date_btn.set_date(new_date)
        self.refresh()

    # ---------- ポモドーロ ----------

    def _on_pomodoro_ticket(self, idx: str) -> None:
        """選択ノードが自分のチケットならポモドーロタイマーの対象にする
        （他人のチケットはスケジュールへ割り当てられないため対象外）"""
        df = self.state.df_nodes
        if (idx and idx in df.index and str(df.loc[idx, "node_type"]) == "ticket"
                and str(df.loc[idx, "assigned_to"]) == self.state.login_user):
            self.pomodoro.set_ticket(idx, str(df.loc[idx, "title"]))

    def _on_pomodoro_finished(self, ticket_idx: str, start_dt, end_dt) -> None:
        """ポモドーロ終了時: 経過時間を15分スロットに丸めて開始日の実績に記録する"""
        df = self.state.df_nodes
        if not ticket_idx or ticket_idx not in df.index:
            return
        if str(df.loc[ticket_idx, "assigned_to"]) != self.state.login_user:
            QMessageBox.information(self, "ポモドーロ",
                                    "自分のチケットではないため実績は記録しません")
            return
        elapsed_min = (end_dt - start_dt).total_seconds() / 60
        n_slots = int(round(elapsed_min / 15))
        if n_slots <= 0:
            self.statusBar().showMessage("15分未満のため実績は記録しません", 5000)
            return
        start_slot = start_dt.hour * 4 + start_dt.minute // 15
        slots = list(range(start_slot, min(start_slot + n_slots,
                                           len(DB.DAILY_TIME_COLS))))
        # スロットは開始時刻基準のため、日付も開始日を使う（日付またぎ対策）
        sch_idx = DB.daily_sch_idx(start_dt.date().isoformat(), self.state.login_user)
        df_daily = self.state.df_daily
        # 既入力(occupied)と空き(free)に分割
        free, occupied = [], []
        for s in slots:
            col = DB.DAILY_TIME_COLS[s]
            is_occ = (not df_daily.empty and sch_idx in df_daily.index
                      and bool(df_daily.loc[sch_idx, col]))
            (occupied if is_occ else free).append(s)
        title = str(df.loc[ticket_idx, "title"])

        # 既入力スロットがある場合は上書き可否をユーザーに確認
        targets = list(free)
        if occupied:
            ans = QMessageBox.question(
                self, "ポモドーロ実績記録",
                f"対象時間帯のうち {len(occupied)} スロットには既に実績が入力されています。\n"
                f"上書きしますか？\n（はい: 上書きして記録 / いいえ: 空き {len(free)} スロットのみ記録）")
            if ans == QMessageBox.StandardButton.Yes:
                targets = sorted(free + occupied)

        if not targets:
            QMessageBox.information(
                self, "ポモドーロ",
                "記録対象のスロットがありません")
            return
        hours = len(targets) * 0.25
        ans = QMessageBox.question(
            self, "ポモドーロ実績記録",
            f"「{title}」に {hours:.2f}h（{len(targets)} スロット）の実績を記録しますか？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._write_pomodoro_slots(sch_idx, targets, ticket_idx)
        self.statusBar().showMessage(f"ポモドーロ実績を記録しました: {title} +{hours:.2f}h", 5000)

    def _write_pomodoro_slots(self, sch_idx: str, slots: list,
                              ticket_idx: str) -> None:
        """スロットへチケットを書き込み、actual_hours を伝播する（遅延保存）。
        既存チケットがある枠を上書きする場合は旧チケットの実績を減算する。"""
        df_daily = self.state.df_daily
        if df_daily.empty or sch_idx not in df_daily.index:
            new_row = {c: "" for c in DB.DAILY_SCH_COLS[1:]}
            new_row["Owner"] = self.state.login_user
            df_daily.loc[sch_idx] = new_row
            self.state.df_daily = df_daily
        nodes = self.state.df_nodes
        today = datetime.date.today().isoformat()
        for s in slots:
            col = DB.DAILY_TIME_COLS[s]
            old_t = self.state.df_daily.loc[sch_idx, col]
            if old_t == ticket_idx:
                continue  # 既に同じチケット: 変更なし
            # 上書き対象の旧チケットから実績を減算
            if old_t and old_t in nodes.index:
                nodes.loc[old_t, "actual_hours"] = max(
                    0.0, float(nodes.loc[old_t, "actual_hours"] or 0) - 0.25)
                self.schedule_panel._propagate_actual_hours(old_t)
            self.state.df_daily.loc[sch_idx, col] = ticket_idx
            nodes.loc[ticket_idx, "actual_hours"] = (
                float(nodes.loc[ticket_idx, "actual_hours"] or 0) + 0.25)
        nodes.loc[ticket_idx, "updated_at"] = today
        self.state.df_daily.loc[sch_idx, "Last_Update"] = today
        self.schedule_panel._propagate_actual_hours(ticket_idx)
        self.state.schedule_modified = True
        self.state.nodes_modified = True
        self.state.notify_dirty()
        self.refresh()

    def _on_dashboard_navigate(self, key: str) -> None:
        """ダッシュボードのカードから対応タブへ遷移する"""
        if key == "inbox":
            self._open_inbox_triage()
            return
        mapping = {"schedule": IDX_GANTT, "edit": IDX_MAIN,
                   "request": IDX_ASSIGN, "team": IDX_TEAM}
        self._switch_view(mapping.get(key, IDX_GANTT))

    def _on_member_changed(self, member: str) -> None:
        """項目7: メンバー選択時にボタンのチェック状態を更新"""
        self._commit_pending_edits()  # 切替前に入力中の日次ログを確定
        self.state.current_member = member
        # ボタンのチェック状態を更新
        for m, btn in getattr(self, "_member_btns", {}).items():
            btn.setChecked(m == member)
        self.refresh()

    def _update_save_btn_style(self) -> None:
        """dirty 状態に応じて保存ボタンの色を変える（橙色: 未保存あり / 通常: 保存済み）
        nodes_modified（ノード変更）または schedule_modified（日次/メモ変更）のどちらかが
        True であれば橙色にする。"""
        if not self._save_btn:
            return
        dirty = (getattr(self.state, "nodes_modified", False) or
                 getattr(self.state, "schedule_modified", False))
        if dirty:
            self._save_btn.setStyleSheet(qss(
                "QToolButton { background: @warning; color: @on_accent; font-weight: bold; "
                "border-radius: 3px; padding: 4px 8px; }"
                "QToolButton:hover { background: @warning_dark; }"
            ))
        else:
            self._save_btn.setStyleSheet("")  # デフォルトに戻す

    # ---------- Inbox ----------

    def _inbox_summary(self) -> dict:
        cfg = self.state.config
        return LG.inbox_summary(self.state.df_nodes, self.state.user,
                                cfg.inbox_max_items, cfg.inbox_stale_days)

    def _update_inbox_btn(self) -> None:
        s = self._inbox_summary()
        self.inbox_btn.setText(f"📥 Inbox {s['count']}")
        if s["over"] or s["stale"]:
            self.inbox_btn.setStyleSheet(qss(
                "QPushButton { background:@warning; color:@on_accent; font-weight:bold;"
                " border-radius:4px; padding:4px 10px; }"))
        else:
            self.inbox_btn.setStyleSheet(STYLE_BUTTON)

    def _open_inbox_triage(self) -> None:
        dlg = InboxTriageDialog(self.state, self.ai_import_view._on_get_ticket_prompt, self)
        dlg.exec()
        self.refresh()

    def maybe_prompt_inbox(self) -> None:
        """起動時: 上限到達または滞留があるときだけ振り分け画面を出す（1 日 1 回まで）"""
        s = self._inbox_summary()
        if not (s["over"] or s["stale"]):
            return
        settings = self._ui_settings()
        today = datetime.date.today().isoformat()
        if settings.value("inbox/last_prompt", "", type=str) == today:
            return
        settings.setValue("inbox/last_prompt", today)
        settings.sync()
        self._open_inbox_triage()

    # ---------- クイック追加（Ctrl+N） ----------

    def _on_quick_add(self, rows=None) -> None:
        """クイック追加ダイアログを開く。日次スケジュールでスロット選択中なら
        作成したチケットをそのスロットへ割り当てる（工数省略時はスロット時間を見積に）。"""
        self._commit_pending_edits()
        if rows is None:
            rows = []
            if (self.schedule_panel.isVisible()
                    and self.state.current_member == self.state.user):
                rows = sorted({i.row() for i in
                               self.schedule_panel.schedule_table.selectedIndexes()})
        default_h = len(rows) * 0.25 if rows else None
        dlg = QuickAddDialog(self.state, default_h, self._qa_recent_tasks, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.result_data:
            return
        ds = self._create_quick_ticket(dlg.result_data)
        where = ("📥 Inbox" if ds["parent_id"] == DB.INBOX_PARENT
                 else self.state.df_nodes.loc[ds["parent_id"], "title"])
        if rows:
            self.schedule_panel._assign_to_rows(rows, ds.name)
            self.schedule_panel.schedule_table.clearSelection()
        self.refresh()
        self.statusBar().showMessage(
            f"作成しました: {ds['title']} → {where}"
            + ("（選択スロットに割り当て）" if rows else ""), 5000)

    def _create_quick_ticket(self, r: dict) -> pd.Series:
        """クイック追加の結果からチケットを作成する（インメモリ。保存は Ctrl+S）"""
        df = self.state.df_nodes
        parent = r["parent"]
        siblings = df[df["parent_id"] == parent]
        priority = int(siblings["priority"].max()) + 1 if not siblings.empty else 1
        color = str(df.loc[parent, "color"] or "Cyan") if parent in df.index else "Cyan"
        ds = DB.create_initial_node(self.state.user, "ticket", r["title"],
                                    parent_id=parent, priority=priority, color=color)
        ds["estimated_hours"] = float(r["hours"] or 0.0)
        ds["deadline"] = r["deadline"].isoformat() if r["deadline"] else None
        ds["start_available"] = r["start"].isoformat() if r["start"] else None
        ds["memo"] = r["memo"]
        self.state.df_nodes.loc[ds.name] = ds
        if parent != DB.INBOX_PARENT:
            if parent in self._qa_recent_tasks:
                self._qa_recent_tasks.remove(parent)
            self._qa_recent_tasks.insert(0, parent)
            del self._qa_recent_tasks[10:]
        self.state.nodes_modified = True
        self.state.notify_dirty()
        return ds

    # ---------- 元に戻す / やり直し ----------

    def _schedule_undo_snapshot(self) -> None:
        """変更フラグが立ったら、その操作の処理が終わった時点の状態を記録する"""
        if not self._undo_pending:
            self._undo_pending = True
            QTimer.singleShot(0, self._take_undo_snapshot)

    def _reset_undo(self) -> None:
        """現在の状態を起点に履歴を初期化する（保存・再読込・DB 直接書き込みの後）"""
        self._undo_pending = False
        self._undo.reset(self.state)

    def _take_undo_snapshot(self) -> None:
        if self._undo_pending:
            self._undo_pending = False
            self._undo.push(self.state)

    def _on_undo(self) -> None:
        self._apply_history(self._undo.undo, "元に戻しました", "元に戻せる操作はありません")

    def _on_redo(self) -> None:
        self._apply_history(self._undo.redo, "やり直しました", "やり直せる操作はありません")

    def _apply_history(self, step, done_msg: str, none_msg: str) -> None:
        self._commit_pending_edits()
        self._take_undo_snapshot()  # 未記録の直前の編集を先に記録する
        if not step(self.state):
            self.statusBar().showMessage(none_msg, 3000)
            return
        self.refresh()
        if self.detail_pane.isVisible():
            self.detail_pane.refresh()
        self.statusBar().showMessage(f"{done_msg}（保存前の操作のみ対象）", 3000)

    def _commit_pending_edits(self) -> None:
        """入力中（フォーカス中）の欄の編集を確定させる。
        日次ログ等は editingFinished で反映されるため、Ctrl+S・日付切替・終了の前に
        フォーカスを外して取りこぼしを防ぐ。"""
        fw = QApplication.focusWidget()
        if fw is not None and self.isAncestorOf(fw):
            fw.clearFocus()

    def _is_dirty(self) -> bool:
        return bool(self.state.nodes_modified or self.state.schedule_modified)

    def _confirm_unsaved(self, action: str) -> bool:
        """未保存の変更があれば 保存/破棄/キャンセル を確認する。続行してよければ True。"""
        self._commit_pending_edits()
        if not self._is_dirty():
            return True
        ans = QMessageBox.question(
            self, "未保存の変更",
            f"未保存の変更があります。{action}前に保存しますか？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if ans == QMessageBox.StandardButton.Save:
            self._on_save()
            return not self._is_dirty()  # 保存失敗時は中止
        return ans == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:
        """終了時に未保存の変更・未保存のレポートを確認する"""
        if self.detail_pane._rep_dirty:
            self.detail_pane._on_save_report()
        if self._confirm_unsaved("終了する"):
            self.save_ui_state()
            event.accept()
        else:
            event.ignore()

    # ---------- 画面状態の記憶（ウィンドウ・分割位置・フィルタ等） ----------

    @staticmethod
    def _ui_settings() -> QSettings:
        """OS ユーザーごとの画面状態ファイル（ui_state.ini）"""
        return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                         "ScheduleManagerV3", "ui_state")

    def _state_splitters(self) -> dict:
        return {
            "outer":   self._outer_splitter,
            "edit":    self.main_pane.splitter,
            "detail":  self.detail_pane._vsplit,
            "plan":    self.road_view._splitter,
            "request": self.assign_view._splitter,
        }

    def save_ui_state(self) -> None:
        """終了時の画面状態を保存する"""
        s = self._ui_settings()
        s.setValue("window/geometry", self.saveGeometry())
        for key, sp in self._state_splitters().items():
            s.setValue(f"splitter/{key}", sp.saveState())
        s.setValue("view/last_tab", self.stack.currentIndex())
        s.setValue("view/detail_open", self.detail_toggle_btn.isChecked())
        tp = self.main_pane.tree_pane
        s.setValue("edit/filter_own", tp._filter_own)
        # 存在しなくなったノードは記録しない
        alive = set(self.state.df_nodes.index) | {"0"}
        s.setValue("edit/collapsed", sorted(tp._collapsed & alive))
        gv = self.gantt_view
        s.setValue("main/status", gv._get_status_filter())
        s.setValue("main/project", gv.pj_combo.currentData() or "")
        s.setValue("main/col_widths", list(gv.fixed_col_widths))
        rv = self.road_view
        s.setValue("plan/level", rv._current_level)
        s.setValue("plan/unit", rv._cell_unit)
        s.setValue("plan/filter_own", rv._filter_own)
        s.setValue("plan/col_extra", rv._date_col_extra)
        s.setValue("plan/col_widths", list(rv.fixed_col_widths))
        s.sync()

    def restore_ui_state(self) -> None:
        """前回終了時の画面状態を復元する（起動時に config のサイズ適用後に呼ぶ）"""
        s = self._ui_settings()
        geo = s.value("window/geometry")
        if geo:
            self.restoreGeometry(geo)
        for key, sp in self._state_splitters().items():
            st = s.value(f"splitter/{key}")
            if st:
                sp.restoreState(st)
        tp = self.main_pane.tree_pane
        tp._collapsed = set(s.value("edit/collapsed", [], type=list) or [])
        if s.contains("edit/filter_own"):
            tp.filter_btn.setChecked(s.value("edit/filter_own", True, type=bool))
        gv = self.gantt_view
        status = s.value("main/status", "", type=str)
        if status in gv._status_radios:
            gv._status_radios[status].setChecked(True)
        gv._initial_pj = s.value("main/project", "", type=str)
        # ガント・Plan の固定列の幅
        for view, key in ((gv, "main/col_widths"), (self.road_view, "plan/col_widths")):
            widths = s.value(key, [], type=list) or []
            if len(widths) == len(view.fixed_col_widths):
                try:
                    view.fixed_col_widths = [max(int(w), 20) for w in widths]
                except (TypeError, ValueError):
                    pass
        rv = self.road_view
        rv.apply_saved_view(
            s.value("plan/level", rv._current_level, type=str),
            s.value("plan/unit", rv._cell_unit, type=str),
            s.value("plan/filter_own", rv._filter_own, type=bool),
            s.value("plan/col_extra", rv._date_col_extra, type=int),
        )
        # start_tab = last の場合のみ、前回のタブと詳細ペインの開閉を再現する
        if self.state.config.start_tab == "last":
            if s.contains("view/detail_open"):
                self.detail_toggle_btn.setChecked(
                    s.value("view/detail_open", False, type=bool))
            tab = s.value("view/last_tab", -1, type=int)
            if 0 <= tab < self.stack.count():
                self._switch_view(tab)
                return
        self.refresh()

    def _on_save(self) -> None:
        self._commit_pending_edits()
        self.statusBar().showMessage("保存中...")
        QApplication.processEvents()
        try:
            self.state.save()
            self._reset_undo()  # 保存した状態を Undo の起点にする
            self.main_pane.table_pane._update_dirty_indicator()
            self._update_save_btn_style()
            self.main_pane.tree_pane.refresh()
            skipped = getattr(self.state, "save_skipped", {})
            if skipped:
                # 他ユーザーへ移管済みのノードは巻き戻さず保存対象外にした旨を通知
                self.statusBar().showMessage(
                    f"保存しました（{len(skipped)} 件は他ユーザーへ移管済みのため保存対象外）", 8000)
                self.refresh()
                return
            self.statusBar().showMessage("保存しました")
            QTimer.singleShot(500, lambda: self.statusBar().clearMessage())
        except Exception as e:
            self.statusBar().clearMessage()
            err_msg = str(e)
            if "dbが利用中" in err_msg or "database is locked" in err_msg:
                QMessageBox.warning(
                    self, "保存できません",
                    "dbが利用中です。しばらく時間をおいて実行してください",
                )
            else:
                QMessageBox.critical(self, "保存エラー", err_msg)
            # 保存失敗時は保存ボタンの赤色を維持するため nodes_modified を True に戻す
            self.state.nodes_modified = True
            self._update_save_btn_style()

    def _on_load(self) -> None:
        if not self._confirm_unsaved("再読込する"):
            return
        try:
            self.state.load()
            self.state.nodes_modified = False     # 再読込後は未保存フラグをリセット
            self.state.schedule_modified = False  # 再読込後は未保存フラグをリセット
            self._reset_undo()  # 読み込んだ状態を Undo の起点にする
            self.main_pane.table_pane._update_dirty_indicator()
            self._update_save_btn_style()
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
            dlg.apply_to_state()  # インメモリ更新のみ（DB書き込みはCtrl+S）
            self.state.nodes_modified = True
            self._update_save_btn_style()
            self.state.refresh()

    def _on_ai_import_done(self, idxs: list) -> None:
        """AI取込完了後に Edit タブへ切替し取り込みキューをセットする"""
        self._switch_view(IDX_MAIN)
        self.main_pane.tree_pane.start_import_queue(idxs)

    def _on_nodes_changed(self) -> None:
        """Edit のインメモリ変更をツリーと現在表示中ビューへ伝播する。
        singleShot(0) で遅延することで、テーブルの itemChanged 処理中に
        同期的なツリー再構築が発生してレイアウトイベントが積まれるのを防ぐ。"""
        QTimer.singleShot(0, self._do_nodes_refresh)

    def _do_nodes_refresh(self) -> None:
        """ツリーと現在表示中ビューの実際の更新処理"""
        self.main_pane.tree_pane.refresh()
        cur = self.stack.currentWidget()
        if hasattr(cur, "refresh") and cur is not self.main_pane:
            cur.refresh()

    # ---------- リフレッシュ ----------

    def refresh(self) -> None:
        """全サブビューを更新する（Edit ペイン・スケジュールパネル・現在表示中のビュー）"""
        self.main_pane.refresh()
        self.schedule_panel.refresh()
        # 現在表示中のビューのみ追加でリフレッシュ（二重更新を防ぐ）
        cur = self.stack.currentWidget()
        if hasattr(cur, "refresh") and cur is not self.main_pane:
            cur.refresh()
        # 未処理 Request の件数に応じてタブボタンを強調表示
        self._update_request_tab_badge()
        self._update_inbox_btn()
        # Plan タブ等の変更後も保存ボタン色を最新状態に同期
        self._update_save_btn_style()

    def _update_request_tab_badge(self) -> None:
        """自分宛の未処理 Request 件数をタブボタンに表示する"""
        df_asgn = self.state.df_assignments
        pending = 0
        if not df_asgn.empty:
            pending = int(
                ((df_asgn["to_user"] == self.state.user) & (df_asgn["status"] == "pending")).sum()
            )
        btn = self._tab_btns.get(IDX_ASSIGN)
        if btn is None:
            return
        # 番号付きの元ラベルを基準にする（Ctrl+番号の表示を消さない）
        base_label = self._tab_labels.get(IDX_ASSIGN, "📨 Request")
        if pending > 0:
            btn.setText(f"{base_label} ({pending})")
            # 基本スタイル + 強調（現在のスタイルに足すと refresh ごとに文字列が伸び続ける）
            btn.setStyleSheet(
                self._tab_style + qss(
                    "QPushButton { background: @danger_btn; color: @on_accent; font-weight: bold; }"
                    "QPushButton:checked { background: @danger_btn_on; color: @on_accent; }")
            )
        else:
            btn.setText(base_label)
            btn.setStyleSheet(self._tab_style)


# ---------- 3 ペインレイアウト ----------

class _Main3Pane(QWidget):
    """
    Edit 画面の 2 分割ビュー（左: 階層ツリー / 右: 子ノード一覧テーブル）。
    TreePane でノードを選択すると TablePane が対応する子ノードを表示する。
    ノード詳細（リンク含む）は MainWindow 共通の DetailPane（右端）に表示する。
    """

    def __init__(self, state):
        super().__init__()
        self.state = state

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree_pane  = TreePane(state)
        self.table_pane = TablePane(state)

        splitter.addWidget(self.tree_pane)
        splitter.addWidget(self.table_pane)
        splitter.setSizes([260, 940])
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter  # 画面状態の記憶用

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


# ---------- ドラッグ&ドロップ対応ツリーウィジェット ----------

class DndTreeWidget(QTreeWidget):
    """親ノード移動のドラッグ&ドロップをサポートするツリーウィジェット。
    ドロップ時に node_reparented シグナルを emit し、
    デフォルトの Qt 移動処理は行わない（データモデルは TreePane が更新する）。
    """
    # (dragged_idx: str, new_parent_idx: str)
    node_reparented = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def dropEvent(self, event) -> None:
        """ドロップ時にシグナルを emit する。Qt のデフォルト移動処理は行わない。"""
        dragged_item = self.currentItem()
        if not dragged_item:
            event.ignore()
            return

        dragged_idx = dragged_item.data(0, Qt.ItemDataRole.UserRole)
        if not dragged_idx:
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint())
        if target_item is None:
            event.ignore()
            return

        new_parent_idx = target_item.data(0, Qt.ItemDataRole.UserRole)
        if new_parent_idx is None or new_parent_idx == dragged_idx:
            event.ignore()
            return

        # デフォルトの Qt 移動処理を防ぎ、シグナルで通知する
        event.ignore()
        self.node_reparented.emit(dragged_idx, new_parent_idx)


# ---------- 左ペイン：階層ツリー ----------

class TreePane(QWidget):
    """
    階層ツリーペイン（Edit 画面の左側）。
    ノードの新規作成・削除・検索・自分のみフィルタ機能を持つ。
    ノード選択時に node_selected シグナルで IDX を TablePane へ通知する。
    """
    node_selected     = Signal(str)  # 選択された IDX
    request_requested = Signal(str)  # 右クリック Request 選択時に IDX を送出

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._selected_idx: Optional[str] = None
        self._filter_own: bool = True    # True = 自分に関係するノードのみ表示
        self._search_text: str = ""
        self._import_queue: list = []    # AI取込キュー
        self._collapsed: set = set()     # 閉じているノードの IDX（再描画・再起動後も維持）
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
        self.filter_btn = QPushButton("👤 選択中メンバーのみ ✓")
        self.filter_btn.setStyleSheet(STYLE_BUTTON)
        self.filter_btn.setCheckable(True)
        self.filter_btn.setChecked(True)
        self.filter_btn.toggled.connect(self._on_filter_toggle)
        top_row.addWidget(self.filter_btn)
        # ツリー全展開 / 全閉じボタン
        _expand_btn = QPushButton("⊞ 全展開")
        _expand_btn.setStyleSheet(STYLE_BUTTON)
        _expand_btn.setToolTip("ツリーを全て展開")
        _expand_btn.clicked.connect(self._expand_all)
        top_row.addWidget(_expand_btn)
        _collapse_btn = QPushButton("⊟ 全閉じ")
        _collapse_btn.setStyleSheet(STYLE_BUTTON)
        _collapse_btn.setToolTip("ツリーを全て閉じる")
        _collapse_btn.clicked.connect(self._collapse_all)
        top_row.addWidget(_collapse_btn)
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
        # DndTreeWidget でドラッグ&ドロップによる親変更をサポート
        self.tree = DndTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderLabels(["ノード階層"])
        self.tree.header().setStretchLastSection(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(self._on_item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.node_reparented.connect(self._on_node_reparented)
        # ユーザーの開閉操作を記憶する（refresh 中はシグナルをブロックするため記録されない）
        self.tree.itemCollapsed.connect(
            lambda it: self._collapsed.add(it.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.itemExpanded.connect(
            lambda it: self._collapsed.discard(it.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.setIndentation(18)
        self.tree.setRootIsDecorated(True)
        # 階層線と種別を視覚的に分かりやすくするスタイル
        self.tree.setStyleSheet(qss("""
            QTreeWidget {
                border: 1px solid @border;
            }
            QTreeWidget::item {
                padding: 3px 2px;
                border-bottom: 1px solid @row_line;
            }
            QTreeWidget::item:selected {
                background: @select_bg;
                color: @text_on_select;
            }
            QTreeWidget::branch:has-siblings:!adjoins-item {
                border-left: 1px solid @branch_line;
            }
            QTreeWidget::branch:has-siblings:adjoins-item {
                border-left: 1px solid @branch_line;
            }
            QTreeWidget::branch:!has-siblings:adjoins-item {
                border-left: 1px solid @branch_line;
            }
        """))
        layout.addWidget(self.tree)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def _on_filter_toggle(self, checked: bool) -> None:
        self._filter_own = checked
        self.filter_btn.setText("👤 選択中メンバーのみ ✓" if checked else "👤 選択中メンバーのみ")
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

        # 📥 Inbox（Task 未設定チケット。表示中メンバーの分のみ）
        inbox_item = QTreeWidgetItem(self.tree)
        inbox_item.setData(0, Qt.ItemDataRole.UserRole, DB.INBOX_PARENT)
        inbox_item.setBackground(0, QColor(C.INBOX_BG))
        inbox_item.setForeground(0, QColor(C.INBOX))
        _fi = QFont()
        _fi.setBold(True)
        inbox_item.setFont(0, _fi)

        # P0 仮想ルートアイテムを先頭に追加（P1 の親として選択可能）
        p0_item = QTreeWidgetItem(self.tree)
        p0_item.setData(0, Qt.ItemDataRole.UserRole, "0")
        p0_item.setText(0, "[P0] ────────────")
        p0_item.setBackground(0, QColor(C.P0_BG))
        p0_item.setForeground(0, QColor(C.P0_FG))
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
            inbox_df = LG.inbox_tickets(df, self.state.current_user)
            self._build_tree(inbox_item, inbox_df, DB.INBOX_PARENT, filter_ids)
        inbox_item.setText(0, f"📥 Inbox ({inbox_item.childCount()})  Task 未設定")
        self.tree.expandAll()
        # 選択を復元（シグナルをブロックしたまま実行して update_for_parent の呼び出しを防ぐ）
        # blockSignals(False) を後に移動することで refresh 中に _normalize_priorities が
        # 呼ばれるのを防ぐ（日付編集後に順序が変わるバグの修正）
        if self._selected_idx:
            self._restore_selection(self._selected_idx)
        # ユーザーが閉じていたノードは閉じたまま再現する（選択復元のスクロールで
        # 親が自動展開されるため、復元の後に適用する）
        if self._collapsed:
            self._apply_collapsed(self.tree.invisibleRootItem())
        self.tree.blockSignals(False)

    # 種別ごとの短縮ラベル・背景色・文字色
    _TYPE_LABEL = {
        "project1": "P1", "project2": "P2",
        "project3": "P3", "project4": "P4",
        "task": "Task", "ticket": "Tkt",
    }
    _TYPE_BG = LEVEL_BG
    _TYPE_FG = LEVEL_FG

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
            type_bg = QColor(self._TYPE_BG.get(node_type, C.SURFACE))
            type_fg = QColor(self._TYPE_FG.get(node_type, C.TEXT_DEFAULT))
            item.setBackground(0, type_bg)
            if row["status"] in ("done", "deleted"):
                item.setForeground(0, QColor(C.TEXT_DONE))
            else:
                item.setForeground(0, type_fg)

            # P1/P2/Task は太字で強調
            if node_type in ("project1", "project2", "task"):
                f = QFont()
                f.setBold(True)
                item.setFont(0, f)

            self._build_tree(item, df, idx, filter_ids)

    def _apply_collapsed(self, parent) -> None:
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(0, Qt.ItemDataRole.UserRole) in self._collapsed:
                child.setExpanded(False)
            self._apply_collapsed(child)

    def _expand_all(self) -> None:
        self._collapsed.clear()
        self.tree.expandAll()

    def _collapse_all(self) -> None:
        # collapseAll は項目ごとの itemCollapsed を出さないため、ここで全 IDX を記録する
        self._collapsed = set(self.state.df_nodes.index) | {"0"}
        self.tree.collapseAll()

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

    def _on_context_menu(self, pos) -> None:
        """ツリーの右クリックメニューを表示する"""
        item = self.tree.itemAt(pos)
        if not item:
            return
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if not idx or idx == "0":
            return
        menu = QMenu(self)
        act_req = menu.addAction("📨 Request")
        # Project4 選択時のみテンプレート一括作成を表示
        act_tmpl = None
        if idx in self.state.df_nodes.index and \
                str(self.state.df_nodes.loc[idx, "node_type"]) == "project4":
            act_tmpl = menu.addAction("📋 テンプレートから作成")
        act = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if act == act_req:
            self.request_requested.emit(idx)
        elif act_tmpl is not None and act == act_tmpl:
            self._on_create_from_template(idx)

    def _on_create_from_template(self, p4_idx: str) -> None:
        """テンプレートから P4 配下に Task/Ticket を一括作成する（インメモリのみ）"""
        tmpl_dir = Path(__file__).parent / "documents" / "templates"
        templates = {}
        if tmpl_dir.exists():
            for f in sorted(tmpl_dir.glob("*.txt")):
                try:
                    templates[f.stem] = f.read_text(encoding="utf-8")
                except Exception:
                    pass
        if not templates:
            QMessageBox.information(
                self, "情報",
                f"テンプレートがありません。\n{tmpl_dir} に .txt を配置してください")
            return
        dlg = _TemplateSelectDialog(templates, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        nodes, errors = LG.parse_template_text(
            dlg.selected_text(), p4_idx, self.state.user, self.state.df_nodes)
        if errors:
            QMessageBox.warning(self, "テンプレートエラー", "\n".join(errors[:15]))
            if not nodes:
                return
        if not nodes:
            QMessageBox.information(self, "情報", "作成対象がありません")
            return
        for ds in nodes:
            self.state.df_nodes.loc[ds.name] = ds
        self.state.nodes_modified = True
        self.state.notify_dirty()
        self.state.refresh()
        QMessageBox.information(
            self, "完了",
            f"{len(nodes)} 件のノードを作成しました（Ctrl+S で DB に保存されます）")

    def _on_node_reparented(self, dragged_idx: str, new_parent_idx: str) -> None:
        """ドラッグ&ドロップによる親変更を処理する。
        バリデーション→確認ダイアログ→インメモリ更新→dirty フラグ→ツリー再構築の順で実行する。
        """
        df = self.state.df_nodes
        if dragged_idx not in df.index:
            return

        dragged_type = str(df.loc[dragged_idx, "node_type"])

        # 他ユーザーのノードは移動不可（保存対象外のため変更が失われる）
        if str(df.loc[dragged_idx, "assigned_to"]) != self.state.user:
            QMessageBox.warning(self, "移動不可", "他ユーザーのデータは移動できません")
            return

        # 新しい親の種別バリデーション
        if new_parent_idx == "0":
            if dragged_type != "project1":
                type_lbl = self._TYPE_LABEL.get(dragged_type, dragged_type)
                QMessageBox.warning(self, "移動不可",
                    f"P0直下には Project1 のみ配置できます。\n（{type_lbl} はドロップできません）")
                return
        else:
            if new_parent_idx not in df.index:
                return
            new_parent_type = str(df.loc[new_parent_idx, "node_type"])
            expected = DB.CHILD_TYPE.get(new_parent_type)
            if expected != dragged_type:
                dragged_lbl     = self._TYPE_LABEL.get(dragged_type, dragged_type)
                parent_lbl      = self._TYPE_LABEL.get(new_parent_type, new_parent_type)
                expected_lbl    = self._TYPE_LABEL.get(expected, expected) if expected else "？"
                QMessageBox.warning(self, "移動不可（階層変更禁止）",
                    f"[{dragged_lbl}] は [{parent_lbl}] の子にはなれません。\n"
                    f"[{parent_lbl}] の子は [{expected_lbl}] のみです。")
                return

        # 現在の親と同じなら何もしない
        current_parent = str(df.loc[dragged_idx, "parent_id"])
        if current_parent == new_parent_idx:
            return

        # 循環参照チェック（dragged_idx の子孫を新しい親にはできない）
        cur = new_parent_idx
        while cur and cur != "0" and cur in df.index:
            if cur == dragged_idx:
                QMessageBox.warning(self, "移動不可", "子孫ノードを祖先に移動することはできません。")
                return
            cur = str(df.loc[cur, "parent_id"])

        # ユーザー確認ダイアログ
        dragged_title = str(df.loc[dragged_idx, "title"])
        if new_parent_idx == "0":
            parent_title = "[P0] トップレベル"
        else:
            parent_title = str(df.loc[new_parent_idx, "title"])
        ret = QMessageBox.question(
            self, "親の変更確認",
            f"「{dragged_title}」を\n「{parent_title}」の下に移動しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        # インメモリ更新・dirty フラグ・ツリー再構築
        df.loc[dragged_idx, "parent_id"] = new_parent_idx
        df.loc[dragged_idx, "updated_at"] = datetime.date.today().isoformat()
        self._selected_idx = dragged_idx
        self.state.nodes_modified = True
        self.state.notify_dirty()
        self.refresh()
        # テーブルも更新する
        self.node_selected.emit(dragged_idx)

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
            # Inbox へは Ctrl+N / 表の空白行で追加する
            self._btn_row.set_enabled("＋ 新規", not is_ticket and idx != DB.INBOX_PARENT)

    def _on_new(self) -> None:
        """新規ノード作成ダイアログ"""
        if self._selected_idx == DB.INBOX_PARENT:
            return  # Inbox へは Ctrl+N / 表の空白行で追加する
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
            ds = dlg.apply_to_state()
            # 親ノード（task 以上）には「詳細作成」「完了」チケットを自動生成（インメモリのみ）
            if child_type != "ticket":
                for _child in DB.build_auto_children(ds, self.state.user):
                    self.state.df_nodes.loc[_child.name] = _child
            self.state.nodes_modified = True
            self.state.refresh()

    def _on_delete(self) -> None:
        if not self._selected_idx:
            return
        idx = self._selected_idx
        df = self.state.df_nodes
        if idx not in df.index:
            return
        # 他ユーザー・実績工数あり・子ノードありは削除不可
        reason = LG.delete_block_reason(df, idx, self.state.user)
        if reason:
            QMessageBox.warning(self, "削除不可", reason)
            return
        ans = QMessageBox.question(self, "削除確認",
                                   f"「{df.loc[idx, 'title']}」を論理削除しますか？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.state.df_nodes.loc[idx, "status"] = "deleted"
        self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        self.state.nodes_modified = True
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


class _ColorDelegate(QStyledItemDelegate):
    """色列用デリゲート：セルクリック時にカラーコンボボックスを表示する"""

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        for name, hex_val in COLOR_OPTIONS.items():
            combo.addItem(name)
            combo.setItemData(combo.count() - 1, QColor(hex_val), Qt.ItemDataRole.DecorationRole)
        return combo

    def setEditorData(self, editor, index):
        val = index.data(Qt.ItemDataRole.EditRole) or "Cyan"
        i = editor.findText(val)
        if i >= 0:
            editor.setCurrentIndex(i)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _DateClearWidget(QWidget):
    """QDateEdit（カレンダーポップアップ付き）と × クリアボタンを一体化したデリゲート用ウィジェット"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cleared = False  # True のとき setModelData で空文字を書き込む

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.lineEdit().setReadOnly(True)
        layout.addWidget(self.date_edit, 1)

        self.clear_btn = QPushButton("×")
        self.clear_btn.setFixedWidth(22)
        self.clear_btn.setToolTip("日付をクリア")
        layout.addWidget(self.clear_btn)


class _DateDelegate(QStyledItemDelegate):
    """日付列用デリゲート：カレンダーポップアップ + × クリアボタン付き"""

    def createEditor(self, parent, option, index):
        widget = _DateClearWidget(parent)
        val = index.data(Qt.ItemDataRole.EditRole) or ""
        if val:
            d = QDate.fromString(str(val), "yyyy-MM-dd")
            widget.date_edit.setDate(d if d.isValid() else QDate.currentDate())
        else:
            widget.date_edit.setDate(QDate.currentDate())
        # × ボタン押下 → cleared フラグを立てて即コミット・クローズ
        widget.clear_btn.clicked.connect(lambda: self._on_clear(widget))
        return widget

    def _on_clear(self, widget: _DateClearWidget) -> None:
        widget.cleared = True
        self.commitData.emit(widget)
        self.closeEditor.emit(widget, QAbstractItemDelegate.EndEditHint.NoHint)

    def setEditorData(self, editor: _DateClearWidget, index) -> None:
        val = index.data(Qt.ItemDataRole.EditRole) or ""
        d = QDate.fromString(str(val), "yyyy-MM-dd")
        if d.isValid():
            editor.date_edit.setDate(d)

    def setModelData(self, editor: _DateClearWidget, model, index) -> None:
        if editor.cleared:
            model.setData(index, "", Qt.ItemDataRole.EditRole)
        else:
            model.setData(index, editor.date_edit.date().toString("yyyy-MM-dd"),
                          Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


# ---------- 中央ペイン：子一覧テーブル ----------

class TablePane(QWidget):
    """
    子ノード一覧テーブルペイン（Edit 画面の右側）。
    TreePane で選択した親ノードの直下子を一覧表示し、直接編集できる。
    node_selected シグナルでクリックした IDX を DailyScheduleWidget へ通知する。
    """
    node_selected    = Signal(str)
    schedule_refresh = Signal()  # スケジュールパネルのみ軽量リフレッシュ
    dirty_changed    = Signal()  # 未保存状態が変化したとき（MainWindow の保存ボタン色変更用）
    nodes_changed    = Signal()  # ノードのインメモリ変更（ツリー・他タブへの伝播用）

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._parent_idx: Optional[str] = None
        self._rebuilding: bool = False
        self._pending_digit: Optional[str] = None  # 数字キー上書き用バッファ

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 現在表示中の親ノードを示すヘッダー
        self.header_label = QLabel("（ノードをツリーから選択してください）")
        self.header_label.setStyleSheet(qss(
            "QLabel { font-weight: bold; color: @text; "
            "background: @control; padding: 4px 6px; border-radius: 3px; }"
        ))
        layout.addWidget(self.header_label)

        # ボタン行（未保存インジケーター付き）
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)
        for _lbl, _slot in [
            ("＋ 追加",   self._on_add),
            ("✏ 編集",   self._on_edit),
            ("🗑 削除",   self._on_delete),
            ("▲ 上へ",   self._on_move_up),
            ("▼ 下へ",   self._on_move_down),
            ("色伝播",    self._on_propagate_color),
        ]:
            _btn = QPushButton(_lbl)
            _btn.setStyleSheet(STYLE_BUTTON)
            _btn.clicked.connect(_slot)
            btn_layout.addWidget(_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # テーブル（直接編集可）
        COLS = ["タイトル", "順序", "ステータス", "見積(h)", "実績(h)",
                "開始可能日", "納期", "担当者", "色", "メモ"]
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # エクセルライク: 任意キー押下またはダブルクリックで編集開始
        # SelectedClicked は削除: Qt が clicked シグナル emit 前に edit() を実行するため
        # _on_cell_clicked_for_edit と二重衝突して "edit: editing failed" が出る
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.AnyKeyPressed |
            QAbstractItemView.EditTrigger.DoubleClicked
        )
        # ステータス・日付列: シングルクリックでエディタを開く
        self.table.clicked.connect(self._on_cell_clicked_for_edit)
        self.table.itemSelectionChanged.connect(
            lambda: self._on_row_changed(self.table.currentRow())
        )
        self.table.setStyleSheet(qss(
            "QTableWidget { gridline-color: @border_light; border: 1px solid @border; }"
            "QTableWidget::item:selected { background: @select_bg; color: @text_on_select; }"
        ))
        self.table.itemChanged.connect(self._on_item_changed)
        widths = [180, 60, 80, 65, 65, 100, 100, 90, 70, 120]
        for i, w in enumerate(widths):
            self.table.setColumnWidth(i, w)

        # デリゲート設定（Status列=2、日付列=5,6、色列=8）
        self.table.setItemDelegateForColumn(2, _StatusDelegate(self.table))
        self.table.setItemDelegateForColumn(5, _DateDelegate(self.table))
        self.table.setItemDelegateForColumn(6, _DateDelegate(self.table))
        self.table.setItemDelegateForColumn(8, _ColorDelegate(self.table))

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
        elif parent_idx == DB.INBOX_PARENT:
            self.header_label.setText(
                "📥 Inbox（Task 未設定）  ▶  ツリーで Task へドラッグして振り分け")
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
        # 表示・選択は編集ではないため正規化しない（dirty 誤検知の防止）。
        # priority 連番化は削除・貼り付け等の実変更時にのみ行う。
        self._rebuild_table()

    def _normalize_priorities(self, parent_idx: str) -> None:
        """子ノード（削除済みを除く）の priority を 1 から連番に修正してインメモリを更新する。
        DB への書き込みはユーザーが明示的に保存（Ctrl+S）したときのみ行う。"""
        df = self.state.df_nodes
        children = df[(df["parent_id"] == parent_idx) & (df["status"] != "deleted")].copy()
        if children.empty:
            return
        children = children.sort_values("priority")
        changed = False
        for i, idx in enumerate(children.index):
            expected = i + 1
            # 他ユーザーのノードは保存されないため変更しない
            if str(children.at[idx, "assigned_to"]) != self.state.user:
                continue
            if int(children.at[idx, "priority"]) != expected:
                self.state.df_nodes.loc[idx, "priority"] = expected
                self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
                changed = True
        if changed:
            self._mark_dirty()

    def refresh(self) -> None:
        self._rebuild_table()

    def _mark_dirty(self) -> None:
        """編集が行われたことをマークし、保存ボタン色変更・ノード変更シグナルを送る"""
        self.state.nodes_modified = True
        self.dirty_changed.emit()   # MainWindow の保存ボタン色を更新
        self.nodes_changed.emit()   # ツリー・他タブへの伝播

    def _update_dirty_indicator(self) -> None:
        """state.nodes_modified の変化を MainWindow の保存ボタン色に反映する"""
        self.dirty_changed.emit()

    def _rebuild_table(self) -> None:
        """選択中の親ノードの子ノードをテーブルに再描画する。シグナルをブロックして再帰更新を防ぐ。"""
        self._rebuilding = True
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            if not self._parent_idx:
                return
            df = self.state.df_nodes
            # 論理削除済みノードは表示しない
            children = df[
                (df["parent_id"] == self._parent_idx) & (df["status"] != "deleted")
            ].copy()
            if self._parent_idx == DB.INBOX_PARENT:
                children = children[children["assigned_to"] == self.state.current_user]
            if not children.empty:
                children = children.sort_values("priority")
                for idx, row in children.iterrows():
                    r = self.table.rowCount()
                    self.table.insertRow(r)
                    color_name = str(row.get("color", "Cyan") or "Cyan")
                    vals = [
                        row.get("title", ""),
                        row.get("priority", ""),
                        row.get("status", ""),
                        row.get("estimated_hours", ""),
                        row.get("actual_hours", ""),
                        row.get("start_available", ""),
                        row.get("deadline", ""),
                        self.state.display_name(str(row.get("assigned_to", ""))),
                        color_name,
                        row.get("memo", ""),
                    ]
                    hex_c = COLOR_OPTIONS.get(color_name, C.NODE_DEFAULT)
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
                            item.setForeground(QColor(C.TEXT_DONE))
                        else:
                            item.setBackground(bg)
                        self.table.setItem(r, c, item)
            # 新規入力用の空白行を常に末尾に追加（子ノードを持てる親の場合のみ）
            can_add_child = self._get_child_type() is not None
            if can_add_child:
                r = self.table.rowCount()
                self.table.insertRow(r)
                for c in range(self.table.columnCount()):
                    item = QTableWidgetItem("")
                    # タイトル列(0)のみ編集可、その他は読み取り専用
                    if c != 0:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setData(Qt.ItemDataRole.UserRole, "__new__")
                    self.table.setItem(r, c, item)
        finally:
            self.table.blockSignals(False)
            self._rebuilding = False
            self._update_dirty_indicator()
            # 行が何も選択されていない場合は先頭の実データ行を選択（矢印キー操作のため）
            if self.table.rowCount() > 0 and self.table.currentRow() < 0:
                self.table.setCurrentCell(0, 0)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """セル編集時にデータを即時更新する"""
        if self._rebuilding:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        col = item.column()
        value = item.text().strip()

        # 空白行へのタイトル入力 → 新ノード作成
        if idx == "__new__":
            if col == 0 and value:
                self._add_from_blank_row(value)
            return

        if not idx or idx not in self.state.df_nodes.index:
            return
        # 自分のノードのみ編集可
        if self.state.df_nodes.loc[idx, "assigned_to"] != self.state.user:
            return

        # タイトルを空白に変更した場合は無視（元の値に戻す）
        if col == 0 and not value:
            orig = str(self.state.df_nodes.loc[idx, "title"] or "")
            self.table.blockSignals(True)
            item.setText(orig)
            self.table.blockSignals(False)
            return

        col_map = {
            0: "title",
            1: "priority",
            2: "status",
            3: "estimated_hours",
            5: "start_available",
            6: "deadline",
            8: "color",
            9: "memo",
        }
        if col not in col_map:
            return
        field = col_map[col]
        try:
            if field == "priority":
                value = int(value)
            elif field == "estimated_hours":
                value = float(value)
                if value < 0:
                    raise ValueError("負の値不可")
            elif field == "status":
                if value not in DB.STATUS_LIST:
                    return
            elif field == "color":
                if value not in COLOR_OPTIONS:
                    return
        except (ValueError, TypeError):
            # 数値変換不可の場合はセルを元の値に戻す
            if field in ("estimated_hours", "priority"):
                orig = self.state.df_nodes.loc[idx, field] \
                    if idx in self.state.df_nodes.index else ""
                self.table.blockSignals(True)
                item.setText(str(orig) if orig != "" else "")
                self.table.blockSignals(False)
            return
        if field == "status":
            cur_status = str(self.state.df_nodes.loc[idx, "status"])
            if value == cur_status:
                return
            err = LG.status_change_error(self.state.df_nodes, idx, value)
            if err:
                # 仕様 2.3 違反: セルを元に戻して警告（編集確定処理の後に表示する）
                self.table.blockSignals(True)
                item.setText(cur_status)
                self.table.blockSignals(False)
                QTimer.singleShot(0, lambda: QMessageBox.warning(
                    self, "ステータス変更不可", err))
                return
            # 実績完了日の設定・「完了」チケットによる親の自動 done を含めて反映
            LG.apply_status(self.state.df_nodes, idx, value)
        else:
            self.state.df_nodes.loc[idx, field] = value
            self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        self._mark_dirty()
        # status/color 変更はテーブル外観（背景色等）を再描画
        if field in ("status", "color"):
            current_row = self.table.currentRow()
            self._rebuild_table()
            if 0 <= current_row < self.table.rowCount():
                self.table.setCurrentCell(current_row, col)
            if field == "status":
                self.schedule_refresh.emit()
        elif field in ("actual_hours", "start_available", "deadline"):
            self.schedule_refresh.emit()
        self.info.set_info(f"更新: {field} = {value}")
        # タイトル列の編集後、次の行のタイトルセルに移動して編集開始する
        if field == "title":
            QTimer.singleShot(0, self._move_to_next_title_cell)
        # 見積もり工数の編集後、一つ下の行（同列）を選択する
        elif field == "estimated_hours":
            QTimer.singleShot(0, self._move_to_next_est_cell)

    def _move_to_next_title_cell(self) -> None:
        """タイトル編集後に次の行のタイトルセルへ移動する。

        editItem は廃止: AnyKeyPressed トリガーで編集開始するため不要。
        """
        row = self.table.currentRow()
        next_row = row + 1
        if 0 <= next_row < self.table.rowCount():
            next_item = self.table.item(next_row, 0)
            if next_item and (next_item.flags() & Qt.ItemFlag.ItemIsEditable):
                self.table.setCurrentCell(next_row, 0)

    def _move_to_next_est_cell(self) -> None:
        """見積もり工数編集後に一つ下の行の同列を選択状態にする"""
        row = self.table.currentRow()
        next_row = row + 1
        if 0 <= next_row < self.table.rowCount():
            self.table.setCurrentCell(next_row, 3)

    def _current_idx(self) -> Optional[str]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if not item:
            return None
        idx = item.data(Qt.ItemDataRole.UserRole)
        # 空白行（新規入力行）は実ノードではないので None を返す
        return None if idx == "__new__" else idx

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
        # 親の色をデフォルト色として取得
        df = self.state.df_nodes
        default_color = "Cyan"
        if self._parent_idx and self._parent_idx in df.index:
            default_color = str(df.loc[self._parent_idx, "color"] or "Cyan")
        dlg = _NodeEditDialog(
            parent_idx=self._parent_idx,
            node_type=child_type,
            state=self.state,
            default_priority=default_priority,
            default_color=default_color,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ds = dlg.apply_to_state()  # インメモリ更新
            # 親ノード（task 以上）には「詳細作成」「完了」チケットを自動生成（仕様 2.2）
            if child_type != "ticket":
                for _child in DB.build_auto_children(ds, self.state.user):
                    self.state.df_nodes.loc[_child.name] = _child
            self._mark_dirty()
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
            dlg.apply_to_state()  # インメモリ更新
            self._mark_dirty()
            self.state.refresh()

    def _on_delete(self) -> None:
        idx = self._current_idx()
        if not idx:
            return
        df = self.state.df_nodes
        if idx not in df.index:
            return
        # 他ユーザー・実績工数あり・子ノードありは削除不可
        reason = LG.delete_block_reason(df, idx, self.state.user)
        if reason:
            QMessageBox.warning(self, "削除不可", reason)
            return
        ans = QMessageBox.question(self, "削除確認",
                                   f"「{df.loc[idx, 'title']}」を論理削除しますか？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        self.state.df_nodes.loc[idx, "status"] = "deleted"
        self.state.df_nodes.loc[idx, "updated_at"] = datetime.date.today().isoformat()
        # 削除で生じた priority の欠番を連番化（実変更なので dirty 誤検知にならない）
        self._normalize_priorities(self._parent_idx)
        self._mark_dirty()
        self.state.refresh()

    def _on_move_up(self)   -> None: self._swap_adjacent(-1)
    def _on_move_down(self) -> None: self._swap_adjacent(+1)

    def _on_propagate_color(self) -> None:
        """選択アイテムの色を子孫に伝播する（Task以上のノードのみ）"""
        idx = self._current_idx()
        if not idx or idx not in self.state.df_nodes.index:
            QMessageBox.information(self, "情報", "ノードをテーブルから選択してください")
            return
        row = self.state.df_nodes.loc[idx]
        node_type = str(row.get("node_type", ""))
        if node_type == "ticket":
            QMessageBox.information(self, "情報", "Ticketには子ノードがないため伝播できません")
            return
        color = str(row.get("color", "Cyan") or "Cyan")
        title = str(row.get("title", ""))

        dlg = QMessageBox(self)
        dlg.setWindowTitle("色伝播")
        dlg.setText(f"「{title}」の色（{color}）を子孫に伝播します")
        btn_children = dlg.addButton("子のみ", QMessageBox.ButtonRole.AcceptRole)
        btn_all = dlg.addButton("全子孫（再帰）", QMessageBox.ButtonRole.ActionRole)
        dlg.addButton(QMessageBox.StandardButton.Cancel)
        dlg.exec()

        clicked = dlg.clickedButton()
        if clicked not in (btn_children, btn_all):
            return
        recursive = (clicked == btn_all)
        self._apply_color_propagation(idx, color, recursive)
        self._mark_dirty()
        self._rebuild_table()

    def _apply_color_propagation(self, parent_id: str, color: str, recursive: bool) -> None:
        """parent_id の直下子（自分担当）の色を color に変更する。recursive なら再帰的に子孫全員に適用。"""
        df = self.state.df_nodes
        today = datetime.date.today().isoformat()
        children = df[
            (df["parent_id"] == parent_id) &
            (df["status"] != "deleted") &
            (df["assigned_to"] == self.state.user)
        ]
        for child_idx in children.index:
            df.loc[child_idx, "color"] = color
            df.loc[child_idx, "updated_at"] = today
            if recursive:
                self._apply_color_propagation(child_idx, color, True)

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
        # 入れ替え相手が他ユーザーのノードだと片方の priority しか保存されないため不可
        user = self.state.user
        if (str(self.state.df_nodes.loc[idx_cur, "assigned_to"]) != user
                or str(self.state.df_nodes.loc[idx_adj, "assigned_to"]) != user):
            self.info.set_info("⚠ 他ユーザーのノードとは順序を入れ替えできません")
            return

        # priority を入れ替えて DB に即時保存（state.refresh で巻き戻らないようにする）
        p_cur = int(self.state.df_nodes.loc[idx_cur, "priority"] or 0)
        p_adj = int(self.state.df_nodes.loc[idx_adj, "priority"] or 0)
        today = datetime.date.today().isoformat()

        self.state.df_nodes.loc[idx_cur, "priority"] = p_adj
        self.state.df_nodes.loc[idx_adj, "priority"] = p_cur
        self.state.df_nodes.loc[idx_cur, "updated_at"] = today
        self.state.df_nodes.loc[idx_adj, "updated_at"] = today
        self._mark_dirty()
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
        """ステータス(2)・開始可能日(5)・納期(6)・色(8) 列はシングルクリックでエディタを開く"""
        if index.column() in {2, 5, 6, 8}:
            item = self.table.item(index.row(), index.column())
            if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                self.table.edit(index)

    def _focus_blank_row(self) -> None:
        """末尾の空白行（新規入力行）にフォーカスしてスクロールする。

        editItem は廃止: AnyKeyPressed トリガーによりユーザーが任意のキーを
        押すことで自動的に編集モードに入る。editItem を呼ぶと編集状態のタイミング
        問題で "edit: editing failed" 警告が発生するため使用しない。
        """
        last_row = self.table.rowCount() - 1
        if last_row < 0:
            return
        item = self.table.item(last_row, 0)
        if not (item and item.data(Qt.ItemDataRole.UserRole) == "__new__"):
            return
        self.table.setCurrentCell(last_row, 0)

        def _scroll_to_row() -> None:
            r = self.table.rowCount() - 1
            it = self.table.item(r, 0) if r >= 0 else None
            if it and it.data(Qt.ItemDataRole.UserRole) == "__new__":
                self.table.scrollToItem(
                    it, QAbstractItemView.ScrollHint.EnsureVisible
                )

        QTimer.singleShot(0, _scroll_to_row)

    def _get_child_type(self) -> Optional[str]:
        """現在選択中の親ノードに対する子ノードタイプを返す。作成不可なら None。"""
        if not self._parent_idx:
            return None
        if self._parent_idx == "0":
            return "project1"
        if self._parent_idx == DB.INBOX_PARENT:
            return "ticket"
        if self._parent_idx not in self.state.df_nodes.index:
            return None
        parent_type = self.state.df_nodes.loc[self._parent_idx, "node_type"]
        return DB.CHILD_TYPE.get(parent_type)

    def _add_from_blank_row(self, title: str) -> None:
        """空白行にタイトルを入力した際、新ノードを作成してテーブルを再描画する。"""
        child_type = self._get_child_type()
        if not child_type:
            return
        if self._parent_idx == DB.INBOX_PARENT:
            cfg = self.state.config
            if LG.inbox_summary(self.state.df_nodes, self.state.user,
                                cfg.inbox_max_items, cfg.inbox_stale_days)["over"]:
                self.info.set_info(f"⚠ Inbox が上限（{cfg.inbox_max_items} 件）です。"
                                   "先に振り分けてください")
                QTimer.singleShot(0, self._rebuild_table)
                return
        # 既存アイテムの priority を連番化してから末尾の優先度を求める
        self._normalize_priorities(self._parent_idx)
        df = self.state.df_nodes
        siblings = df[
            (df["parent_id"] == self._parent_idx) & (df["status"] != "deleted")
        ]
        max_priority = int(siblings["priority"].max()) + 1 if not siblings.empty else 1
        # 親の色を引き継ぐ
        parent_color = "Cyan"
        if self._parent_idx and self._parent_idx in df.index:
            parent_color = str(df.loc[self._parent_idx, "color"] or "Cyan")
        ds = DB.create_initial_node(
            owner=self.state.user,
            node_type=child_type,
            title=title,
            parent_id=self._parent_idx,
            priority=max_priority,
        )
        ds["estimated_hours"] = 1.0
        ds["status"] = "todo"
        ds["color"] = parent_color
        self.state.df_nodes.loc[ds.name] = ds  # インメモリ更新
        # 親ノード（task 以上）には「詳細作成」「完了」チケットを自動生成（仕様 2.2）
        if child_type != "ticket":
            for _child in DB.build_auto_children(ds, self.state.user):
                self.state.df_nodes.loc[_child.name] = _child
        self._mark_dirty()
        self.info.set_info(f"追加: {title}")
        # itemChanged シグナル処理中に setRowCount(0) を呼ぶと
        # "commitData called with an editor that does not belong to this view"
        # エラーが出るため、テーブル再描画をイベントループの次のターンに遅延する
        def _deferred_rebuild() -> None:
            self._rebuild_table()
            self._focus_blank_row()
        QTimer.singleShot(0, _deferred_rebuild)

    def eventFilter(self, obj, event) -> bool:
        """キーボード操作のハンドリング"""
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mod = event.modifiers()
            row = self.table.currentRow()
            col = self.table.currentColumn()

            # Alt+↑↓ でステータス列の値をサイクル
            if mod & Qt.KeyboardModifier.AltModifier and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
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

            # Tab: 次の編集可能セルへ移動
            elif key == Qt.Key.Key_Tab and not mod:
                self._navigate_editable_col(row, col, 1)
                return True

            # Shift+Tab (Backtab): 前の編集可能セルへ移動
            elif key == Qt.Key.Key_Backtab:
                self._navigate_editable_col(row, col, -1)
                return True

            elif not mod:
                # Escape: 編集キャンセル後にテーブルのフォーカス・選択を維持
                if key == Qt.Key.Key_Escape:
                    self.table.setFocus()
                    return True

                # Delete/Backspace: 日付列をクリア
                elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                    if col in {5, 6}:  # 開始可能日(5)・納期(6)列
                        item = self.table.item(row, col)
                        if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                            item.setText("")
                            self._mark_dirty()
                            return True

                # Enter/Return: 選択セルの編集開始
                elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    item = self.table.item(row, col)
                    if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                        self.table.editItem(item)
                        return True

                # タイトル列(0): 印刷可能文字キー → 編集開始してキーをエディタに転送
                elif col == 0 and Qt.Key.Key_Space <= key <= Qt.Key.Key_AsciiTilde:
                    item = self.table.item(row, 0)
                    if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                        self.table.editItem(item)
                        focused = QApplication.focusWidget()
                        if focused and focused is not self.table:
                            QApplication.sendEvent(focused, event)
                        return True

                # 見積もり工数列(3): 数字キー → 上書き編集
                elif col == 3 and Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
                    item = self.table.item(row, 3)
                    if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                        self._pending_digit = chr(key)
                        self.table.editItem(item)
                        QTimer.singleShot(0, self._apply_digit_overwrite)
                        return True

        return super().eventFilter(obj, event)

    def _navigate_editable_col(self, row: int, col: int, direction: int) -> None:
        """Tab / Shift+Tab で編集可能な次／前のセルに移動する。
        行末（行頭）を超えた場合は次（前）の行の先頭（末尾）編集可能セルへ移動する。"""
        total_rows = self.table.rowCount()
        total_cols = self.table.columnCount()
        r, c = row, col
        for _ in range(total_rows * total_cols):
            c += direction
            if c >= total_cols:
                c = 0
                r += 1
            elif c < 0:
                c = total_cols - 1
                r -= 1
            if r < 0 or r >= total_rows:
                return
            item = self.table.item(r, c)
            if item and (item.flags() & Qt.ItemFlag.ItemIsEditable):
                self.table.setCurrentCell(r, c)
                return

    def _apply_digit_overwrite(self) -> None:
        """見積もり工数列の数字キー入力を上書きでエディタに反映する"""
        if not self._pending_digit:
            return
        focused = QApplication.focusWidget()
        if focused and focused is not self.table and hasattr(focused, "setText"):
            focused.setText(self._pending_digit)
        self._pending_digit = None


# ---------- 右ペイン：詳細フォームのみ ----------

class DetailPane(QWidget):
    """ノード詳細 + レポート編集（上: 詳細/本日レポート編集、下: 過去レポート閲覧）。
    Plan/Gantt/Edit のノード選択に連動し、選択ノードの日付付きレポートを管理する。"""

    # LLM 文章化プロンプトのテンプレート（ユーザーがカスタマイズ可能）
    _LLM_TEMPLATE_PATH = Path(__file__).parent / "documents" / "llm_report.md"
    _LLM_FALLBACK = (
        "以下の実績データから、上司向けの業務報告を簡潔な敬体で作成してください。\n"
        "構成: 1) 成果サマリー 2) 進行中と見通し 3) 課題・リスク 4) 所感（叩き台）\n"
        "数値はデータのまま正確に使い、データにない事実は創作しないでください。")

    # ── カード/バッジ表示用パレット ──
    # 種別バッジ（RoadmapView と同系統の配色）
    _TYPE_LABEL = {
        "project1": "P1", "project2": "P2",
        "project3": "P3", "project4": "P4",
        "task": "Task",   "ticket":   "Ticket",
    }
    _TYPE_BG = {**LEVEL_BG, "ticket": C.TICKET_BADGE_BG}
    _TYPE_FG = {**LEVEL_FG, "ticket": C.TICKET_BADGE_FG}
    # ステータスバッジ（ラベル, 背景色, 文字色）
    _STATUS_INFO = {
        "todo":      ("未着手", C.WARNING_BG, C.WARNING),
        "done":      ("完了",   C.SUCCESS_BG, C.SUCCESS),
        "cancel":    ("中止",   C.CONTROL,    C.CANCEL),
        "regularly": ("定常",   C.REGULAR_BG, C.REGULAR),
        "deleted":   ("削除",   C.DANGER_BG,  C.DANGER),
    }
    # レポート操作ボタン（保存=青系 / LLM=紫系）
    _STYLE_BTN_PRIMARY = qss(
        "QPushButton { background:@accent_light; color:@on_accent; border:1px solid @accent;"
        " border-radius:4px; padding:4px 10px; font-weight:bold; }"
        "QPushButton:hover { background:@accent; }"
        "QPushButton:pressed { background:@accent_dark; }"
        "QPushButton:disabled { background:@control_hover; color:@text_muted;"
        " border-color:@border_strong; }"
    )
    _STYLE_BTN_ACCENT = qss(
        "QPushButton { background:@llm_bg; color:@llm_text; border:1px solid @llm_border;"
        " border-radius:4px; padding:4px 10px; font-weight:bold; }"
        "QPushButton:hover { background:@llm_hover; }"
        "QPushButton:disabled { background:@bg_soft; color:@border_strong;"
        " border-color:@border_light; }"
    )

    def __init__(self, state):
        super().__init__()
        self.state = state
        self._node_idx: Optional[str] = None

        # 詳細ペイン全体のトーン（淡い背景＋白カード）
        self.setStyleSheet(qss(
            "QFrame#detailCard { background:@surface; border:1px solid @border_light;"
            " border-radius:8px; }"
            "QWidget#detailFormHost { background:@surface_alt; }"
        ))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        vsplit = QSplitter(Qt.Orientation.Vertical)

        # ── 上: ノード詳細（カード表示）──
        self.form_area = QScrollArea()
        self.form_area.setWidgetResizable(True)
        self.form_area.setFrameShape(QFrame.Shape.NoFrame)
        form_widget = QWidget()
        form_widget.setObjectName("detailFormHost")
        self.form_box = QVBoxLayout(form_widget)
        self.form_box.setContentsMargins(4, 4, 4, 4)
        self.form_box.setSpacing(8)
        self.form_box.addStretch()  # カードを上詰めにする
        self.form_area.setWidget(form_widget)
        vsplit.addWidget(self.form_area)

        # ── 下: レポートセクション（月次・P2単位ファイル / IDX セクション）──
        report_widget = QWidget()
        rlay = QVBoxLayout(report_widget)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(3)

        self._rep_month = datetime.date.today().replace(day=1)  # 表示中の月（1日）
        self._rep_dirty = False  # エディタ未保存フラグ

        self.report_title_lbl = QLabel("📋 レポート")
        self.report_title_lbl.setWordWrap(True)
        self.report_title_lbl.setStyleSheet(qss(
            "QLabel { background:@accent_bg; color:@accent; border-radius:6px;"
            " padding:6px 8px; font-weight:bold; font-size:9pt; }"))
        rlay.addWidget(self.report_title_lbl)

        # 月ナビ ◀ [yyyy/mm] ▶
        mrow = QHBoxLayout()
        mrow.setSpacing(3)
        self.rep_prev_btn = QPushButton("◀")
        self.rep_prev_btn.setStyleSheet(STYLE_BUTTON)
        self.rep_prev_btn.setFixedWidth(28)
        self.rep_prev_btn.clicked.connect(lambda: self._rep_shift_month(-1))
        self.rep_month_lbl = QLabel("")
        self.rep_month_lbl.setStyleSheet(qss(
            "QLabel { font-weight:bold; color:@text; padding:0 4px; }"))
        self.rep_next_btn = QPushButton("▶")
        self.rep_next_btn.setStyleSheet(STYLE_BUTTON)
        self.rep_next_btn.setFixedWidth(28)
        self.rep_next_btn.clicked.connect(lambda: self._rep_shift_month(+1))
        mrow.addWidget(self.rep_prev_btn)
        mrow.addWidget(self.rep_month_lbl)
        mrow.addWidget(self.rep_next_btn)
        mrow.addStretch()
        rlay.addLayout(mrow)

        # ボタン行
        tbar = QHBoxLayout()
        tbar.setSpacing(3)
        self.rep_mode = QComboBox()
        self.rep_mode.addItem("週報", "weekly")
        self.rep_mode.addItem("月報", "monthly")
        tbar.addWidget(self.rep_mode)
        self.rep_insert_btn = QPushButton("📊 実績を挿入")
        self.rep_insert_btn.setStyleSheet(STYLE_BUTTON)
        self.rep_insert_btn.setToolTip("選択アイテム配下の実績を集約して本文に差し込む")
        self.rep_insert_btn.clicked.connect(self._on_insert_actuals)
        self.rep_save_btn = QPushButton("💾 保存")
        self.rep_save_btn.setStyleSheet(self._STYLE_BTN_PRIMARY)
        self.rep_save_btn.setToolTip("この月の P2 ファイルの該当セクションへ保存")
        self.rep_save_btn.clicked.connect(self._on_save_report)
        self.rep_llm_btn = QPushButton("🤖 LLM")
        self.rep_llm_btn.setStyleSheet(self._STYLE_BTN_ACCENT)
        self.rep_llm_btn.setToolTip("LLM 用プロンプト + 本文をクリップボードへコピー")
        self.rep_llm_btn.clicked.connect(self._on_copy_llm)
        tbar.addWidget(self.rep_insert_btn)
        tbar.addWidget(self.rep_save_btn)
        tbar.addWidget(self.rep_llm_btn)
        tbar.addStretch()
        rlay.addLayout(tbar)

        self.report_edit = QTextEdit()
        self.report_edit.setAcceptRichText(False)
        self.report_edit.setStyleSheet(qss(
            "QTextEdit { border:1px solid @border; border-radius:6px;"
            " padding:6px; background:@surface; }"))
        self.report_edit.setPlaceholderText(
            "このアイテムの当月レポート（自由記述。[名前](パス/URL) でリンクを貼れます）")
        self.report_edit.textChanged.connect(self._on_report_text_changed)
        rlay.addWidget(self.report_edit, stretch=1)

        # 🔗 抽出リンク一覧
        _links_lbl = QLabel("🔗 リンク")
        _links_lbl.setStyleSheet(qss(
            "QLabel { color:@text_sub; font-size:8pt; font-weight:bold; }"))
        rlay.addWidget(_links_lbl)
        self.links_area = QScrollArea()
        self.links_area.setWidgetResizable(True)
        self.links_area.setFrameShape(QFrame.Shape.NoFrame)
        self.links_area.setMaximumHeight(110)
        self._links_host = QWidget()
        self.links_layout = QVBoxLayout(self._links_host)
        self.links_layout.setContentsMargins(0, 0, 0, 0)
        self.links_layout.setSpacing(2)
        self.links_area.setWidget(self._links_host)
        rlay.addWidget(self.links_area)

        vsplit.addWidget(report_widget)
        vsplit.setSizes([260, 460])
        self._vsplit = vsplit  # 画面状態の記憶用
        layout.addWidget(vsplit, stretch=1)

        self.info = InfoLabel()
        layout.addWidget(self.info)

    def update_for_node(self, idx: str) -> None:
        if self._rep_dirty and self._node_idx:
            self._on_save_report()
        self._node_idx = idx
        self._rep_month = datetime.date.today().replace(day=1)
        self._rebuild_form(idx)
        self._load_report()

    def refresh(self) -> None:
        if self._node_idx:
            self._rebuild_form(self._node_idx)
            # 編集中（未保存）のレポート本文はファイル内容で上書きしない
            if not self._rep_dirty:
                self._load_report()

    # ── 詳細カード生成ヘルパー ──

    @staticmethod
    def _make_badge(text: str, bg: str, fg: str) -> QLabel:
        """角丸ピル風のバッジラベルを生成する"""
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        lbl.setStyleSheet(
            f"QLabel {{ background:{bg}; color:{fg}; border-radius:9px;"
            f" padding:2px 10px; font-size:9pt; font-weight:bold; }}")
        return lbl

    def _make_card(self, title: str = ""):
        """白カード QFrame と、本文を積む QVBoxLayout を返す"""
        card = QFrame()
        card.setObjectName("detailCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        if title:
            head = QLabel(title)
            head.setStyleSheet(qss(
                "QLabel { color:@text_muted; font-size:8pt; font-weight:bold; }"))
            v.addWidget(head)
        return card, v

    @staticmethod
    def _field_widget(caption: str, value: str) -> QWidget:
        """小見出し(灰)＋値(太字) を縦に並べたミニ項目ウィジェット"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        cap = QLabel(caption)
        cap.setStyleSheet(qss("QLabel { color:@text_muted; font-size:8pt; }"))
        val = QLabel(value if value else "—")
        val.setWordWrap(True)
        val.setStyleSheet(qss(
            "QLabel { color:@text; font-size:9pt; font-weight:bold; }"))
        v.addWidget(cap)
        v.addWidget(val)
        return w

    def _fields_card(self, title: str, fields: list):
        """(見出し, 値) のリストを2列グリッドで並べたカードを返す。
        値が空の項目は省略し、表示項目が無ければ None を返す。"""
        shown = [(c, v) for c, v in fields if v]
        if not shown:
            return None
        card, v = self._make_card(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for i, (cap, val) in enumerate(shown):
            grid.addWidget(self._field_widget(cap, val), i // 2, i % 2)
        v.addLayout(grid)
        return card

    def _progress_widget(self, actual: float, est: float) -> QWidget:
        """実績/見積の進捗バー＋数値ラベル。見積が無い場合はバーを省く"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        if est and est > 0:
            pct = int(round(min(actual / est, 1.0) * 100))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(False)
            bar.setFixedHeight(10)
            # 見積超過は橙、範囲内は緑
            chunk = C.PROGRESS_OVER if actual > est else C.PROGRESS_OK
            bar.setStyleSheet(
                f"QProgressBar {{ background:{C.CONTROL}; border:none;"
                " border-radius:5px; }"
                f"QProgressBar::chunk {{ background:{chunk};"
                " border-radius:5px; }")
            cap = QLabel(f"実績 {actual:.1f} / 見積 {est:.1f} h　（{pct}%）")
            v.addWidget(bar)
            v.addWidget(cap)
        else:
            cap = QLabel(f"実績 {actual:.1f} h　（見積 未設定）")
            v.addWidget(cap)
        cap.setStyleSheet(qss("QLabel { color:@text_sub; font-size:9pt; }"))
        return w

    def _swatch(self, color_name: str) -> QWidget:
        """表示色の丸チップ＋色名"""
        hex_v = DB.COLOR_OPTIONS.get(color_name, C.UNSET_GRAY)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        dot = QLabel()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(
            f"QLabel {{ background:{hex_v}; border-radius:7px;"
            f" border:1px solid {C.BORDER_STRONG}; }}")
        name = QLabel(color_name or "—")
        name.setStyleSheet(qss("QLabel { color:@text_muted; font-size:8pt; }"))
        h.addWidget(dot)
        h.addWidget(name)
        h.addStretch()
        return w

    def _rebuild_form(self, idx: str) -> None:
        """選択ノードの詳細をカード形式で再構築する"""
        # 既存カードを全削除
        while self.form_box.count():
            item = self.form_box.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        if not idx or idx not in self.state.df_nodes.index:
            self.form_box.addStretch()
            return
        row = self.state.df_nodes.loc[idx]

        def _s(key: str) -> str:
            v = row.get(key, "")
            return "" if v is None else str(v)

        # 1) ヘッダーカード（種別バッジ＋ステータス＋タイトル＋表示色）
        ntype = _s("node_type")
        header, hv = self._make_card("")
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self._make_badge(
            self._TYPE_LABEL.get(ntype, ntype or "—"),
            self._TYPE_BG.get(ntype, C.CONTROL),
            self._TYPE_FG.get(ntype, C.TEXT)))
        st = _s("status")
        if st:
            s_label, s_bg, s_fg = self._STATUS_INFO.get(
                st, (st, C.CONTROL, C.CANCEL))
            top.addWidget(self._make_badge(s_label, s_bg, s_fg))
        top.addStretch()
        hv.addLayout(top)
        title = QLabel(_s("title") or "（無題）")
        title.setWordWrap(True)
        title.setStyleSheet(qss(
            "QLabel { color:@text_strong; font-size:12pt; font-weight:bold; }"))
        hv.addWidget(title)
        if _s("color"):
            hv.addWidget(self._swatch(_s("color")))
        self.form_box.addWidget(header)

        # 2) 工数カード（進捗バー）
        try:
            est = float(row.get("estimated_hours") or 0)
        except (TypeError, ValueError):
            est = 0.0
        try:
            act = float(row.get("actual_hours") or 0)
        except (TypeError, ValueError):
            act = 0.0
        prog_card, pv = self._make_card("工数")
        pv.addWidget(self._progress_widget(act, est))
        self.form_box.addWidget(prog_card)

        # 3) 日程カード
        sched = self._fields_card("日程", [
            ("開始可能日", _s("start_available")),
            ("納期",       _s("deadline")),
            ("実績開始日", _s("actual_start")),
            ("実績完了日", _s("actual_end")),
        ])
        if sched:
            self.form_box.addWidget(sched)

        # 4) 基本情報カード
        assignee = (self.state.display_name(_s("assigned_to"))
                    if _s("assigned_to") else "")
        basic = self._fields_card("基本情報", [
            ("担当者", assignee),
            ("順序",   _s("priority")),
        ])
        if basic:
            self.form_box.addWidget(basic)

        # 5) メモカード
        memo = _s("memo")
        if memo:
            memo_card, mv = self._make_card("メモ")
            mlbl = QLabel(memo)
            mlbl.setWordWrap(True)
            mlbl.setStyleSheet(qss("QLabel { color:@text; font-size:9pt; }"))
            mv.addWidget(mlbl)
            self.form_box.addWidget(memo_card)

        self.form_box.addStretch()

    def _open_link(self, link: str) -> None:
        """リンク先を OS の既定アプリで開く。URL とローカルパスの両方に対応。"""
        link = link.strip()
        if not link:
            return
        if "://" in link:
            url = QUrl(link)
        else:
            path = Path(link)
            if not path.exists():
                QMessageBox.warning(
                    self, "リンクエラー", f"リンク先が見つかりません:\n{link}")
                return
            url = QUrl.fromLocalFile(str(path))
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(
                self, "リンクエラー", f"リンクを開けませんでした:\n{link}")

    # ── レポート（月次・P2 単位ファイル / IDX セクション）──

    def _on_report_text_changed(self) -> None:
        self._rep_dirty = True

    def _load_report(self) -> None:
        """選択アイテム・選択月のセクションを読み込む"""
        idx = self._node_idx
        self.rep_month_lbl.setText(self._rep_month.strftime("%Y/%m"))

        target = (
            LG.report_target(self.state.df_nodes, idx)
            if (idx and idx in self.state.df_nodes.index)
            else None
        )

        if target is None:
            self.report_title_lbl.setText(
                "📋 レポート（レポート対象外：Project2 以下を選択）")
            self.report_edit.blockSignals(True)
            self.report_edit.clear()
            self.report_edit.blockSignals(False)
            self.report_edit.setEnabled(False)
            self.rep_insert_btn.setEnabled(False)
            self.rep_save_btn.setEnabled(False)
            self.rep_llm_btn.setEnabled(False)
            self._update_links()
            return

        path_titles = LG.node_path_titles(self.state.df_nodes, idx)
        # 階層ごとに改行＆インデントして表示（横長で見切れるのを防ぐ）
        breadcrumb = "\n".join(
            f"{'　' * i}{t}" for i, t in enumerate(path_titles))
        self.report_title_lbl.setText(
            f"📋 今月のレポート ({self._rep_month.strftime('%Y/%m')})\n{breadcrumb}")

        self.report_edit.setEnabled(True)
        self.rep_insert_btn.setEnabled(True)
        self.rep_save_btn.setEnabled(True)
        self.rep_llm_btn.setEnabled(True)

        out_dir = (self.state.config.report_output_dir or "").strip()
        body = ""
        if out_dir:
            yyyymm = self._rep_month.strftime("%Y%m")
            p2_path = LG.report_p2_path(out_dir, self.state.df_nodes, idx, yyyymm)
            if p2_path and p2_path.exists():
                try:
                    md_text = p2_path.read_text(encoding="utf-8")
                    body = LG.read_section(md_text, idx) or ""
                except Exception:
                    pass

        self.report_edit.blockSignals(True)
        self.report_edit.setPlainText(body)
        self.report_edit.blockSignals(False)
        self._rep_dirty = False
        self._update_links()

    def _rep_shift_month(self, delta: int) -> None:
        """月ナビ ◀▶ で前月/翌月へ移動（dirty なら自動保存）"""
        if self._rep_dirty:
            self._on_save_report()
        m = self._rep_month
        new_month = m.month + delta
        new_year = m.year
        if new_month < 1:
            new_month = 12
            new_year -= 1
        elif new_month > 12:
            new_month = 1
            new_year += 1
        self._rep_month = m.replace(year=new_year, month=new_month, day=1)
        self._load_report()

    def _update_links(self) -> None:
        """report_edit の本文から []() リンクを抽出してボタン一覧を再構築する"""
        while self.links_layout.count():
            item = self.links_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)  # 即時削除（deleteLater は非同期）
        links = LG.extract_md_links(self.report_edit.toPlainText())
        for label, target in links:
            btn = QPushButton(f"[{label}]")
            btn.setToolTip(target)
            btn.setStyleSheet(qss(
                "QPushButton { background:@link_bg; color:@link_text;"
                " border:1px solid @link_border; border-radius:10px;"
                " padding:3px 10px; text-align:left; }"
                "QPushButton:hover { background:@link_border; }"))
            btn.clicked.connect(lambda _=False, t=target: self._open_link(t))
            self.links_layout.addWidget(btn)

    def _report_period(self, mode: str):
        """モードに応じた集計期間(date_from, date_to: ISO文字列)を返す"""
        today = datetime.date.today()
        if mode == "monthly":
            # 月ナビで表示中の月を対象にする（過去月のレポートに当月実績が入らないように）
            first = self._rep_month
            nxt = (first.replace(year=first.year + 1, month=1)
                   if first.month == 12
                   else first.replace(month=first.month + 1))
            return first.isoformat(), (nxt - datetime.timedelta(days=1)).isoformat()
        # weekly: 月曜〜日曜
        monday = today - datetime.timedelta(days=today.weekday())
        return monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat()

    def _on_insert_actuals(self) -> None:
        """選択ノード配下の実績を集約した md を本文へ挿入する"""
        idx = self._node_idx
        if not idx or idx not in self.state.df_nodes.index:
            QMessageBox.information(self, "情報", "Plan などでノードを選択してください")
            return
        mode = self.rep_mode.currentData()
        d_from, d_to = self._report_period(mode)
        data = LG.collect_report_data(
            self.state.df_nodes, self.state.df_daily, idx, d_from, d_to,
            display_name_func=self.state.display_name)
        md = LG.build_report_markdown(data, mode)
        cur = self.report_edit.toPlainText()
        self.report_edit.setPlainText((cur + "\n\n" if cur.strip() else "") + md)
        self.info.set_info("実績を挿入しました")

    def _on_save_report(self) -> None:
        """当月 P2 ファイルの IDX セクションへ保存する"""
        idx = self._node_idx
        if not idx or idx not in self.state.df_nodes.index:
            return
        out_dir = (self.state.config.report_output_dir or "").strip()
        if not out_dir:
            QMessageBox.warning(
                self, "出力先未設定",
                "Config で [Report] output_dir を設定してください")
            return
        yyyymm = self._rep_month.strftime("%Y%m")
        p2_path = LG.report_p2_path(out_dir, self.state.df_nodes, idx, yyyymm)
        if p2_path is None:
            return
        item_title = str(self.state.df_nodes.loc[idx, "title"])
        body = self.report_edit.toPlainText()
        try:
            p2_path.parent.mkdir(parents=True, exist_ok=True)
            md_text = p2_path.read_text(encoding="utf-8") if p2_path.exists() else ""
            updated = LG.upsert_section(md_text, idx, item_title, body)
            p2_path.write_text(updated, encoding="utf-8")
            self._rep_dirty = False
            self.info.set_info(f"保存しました: {p2_path.name}")
            self._update_links()
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", str(e))

    def _on_copy_llm(self) -> None:
        """LLM 用プロンプト + 本文をクリップボードへコピーする"""
        text = self.report_edit.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "情報", "本文がありません")
            return
        try:
            template = self._LLM_TEMPLATE_PATH.read_text(encoding="utf-8")
        except Exception:
            template = self._LLM_FALLBACK
        QApplication.clipboard().setText(
            template.rstrip() + "\n\n# 実績データ\n\n" + text)
        self.info.set_info("LLM 用プロンプトをコピーしました")


# ---------- テンプレート選択ダイアログ ----------

class _TemplateSelectDialog(QDialog):
    """プロジェクトテンプレート選択ダイアログ（一覧 + プレビュー）"""

    def __init__(self, templates: dict, parent=None):
        # templates: {テンプレート名: ファイル内容}
        super().__init__(parent)
        self.setWindowTitle("テンプレートから作成")
        self.setMinimumSize(480, 380)
        self._templates = templates

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("テンプレート:"))
        self.combo = QComboBox()
        self.combo.addItems(list(templates.keys()))
        self.combo.currentTextChanged.connect(self._on_changed)
        layout.addWidget(self.combo)

        layout.addWidget(QLabel("内容プレビュー:"))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, stretch=1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        if templates:
            self._on_changed(self.combo.currentText())

    def _on_changed(self, name: str) -> None:
        self.preview.setPlainText(self._templates.get(name, ""))

    def selected_text(self) -> str:
        return self._templates.get(self.combo.currentText(), "")


# ---------- ノード編集ダイアログ ----------

class _NodeEditDialog(QDialog):
    def __init__(self, parent_idx: Optional[str], node_type: Optional[str],
                 state, edit_idx: Optional[str] = None,
                 default_priority: int = 99, default_color: str = "Cyan", parent=None):
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
        # 開始可能日: カレンダー選択ボタン（空=未設定）
        self.f_start = DateButton(allow_empty=True)
        start_row = QWidget()
        start_layout = QHBoxLayout(start_row)
        start_layout.setContentsMargins(0, 0, 0, 0)
        start_layout.addWidget(self.f_start)
        start_clear = QPushButton("×")
        start_clear.setFixedWidth(28)
        start_clear.setToolTip("開始可能日をクリア")
        start_clear.clicked.connect(self.f_start.clear_date)
        start_layout.addWidget(start_clear)

        # 納期: カレンダー選択ボタン（開始可能日の月を初期表示月として使用）
        self.f_deadline = DateButton(
            allow_empty=True,
            anchor_date_func=lambda: self.f_start.get_date(),
        )
        deadline_row = QWidget()
        deadline_layout = QHBoxLayout(deadline_row)
        deadline_layout.setContentsMargins(0, 0, 0, 0)
        deadline_layout.addWidget(self.f_deadline)
        deadline_clear = QPushButton("×")
        deadline_clear.setFixedWidth(28)
        deadline_clear.setToolTip("納期をクリア")
        deadline_clear.clicked.connect(self.f_deadline.clear_date)
        deadline_layout.addWidget(deadline_clear)

        self.f_color = ColorCombo()
        self.f_memo = QTextEdit()
        self.f_memo.setMaximumHeight(80)

        form.addRow("タイトル *:",   self.f_title)
        form.addRow("順序:",         self.f_priority)
        form.addRow("ステータス:",   self.f_status)
        form.addRow("見積工数(h):",  self.f_est)
        form.addRow("開始可能日:",   start_row)
        form.addRow("納期:",         deadline_row)
        form.addRow("表示色:",       self.f_color)
        form.addRow("メモ:",         self.f_memo)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # 編集時は既存値、新規作成時は default_color を適用
        if is_edit:
            row = state.df_nodes.loc[edit_idx]
            self.f_title.setText(str(row.get("title", "")))
            self.f_priority.setValue(int(row.get("priority", 99)))
            si = self.f_status.findText(str(row.get("status", "todo")))
            if si >= 0:
                self.f_status.setCurrentIndex(si)
            self.f_est.setValue(float(row.get("estimated_hours", 0)))
            self.f_start.set_date(str(row.get("start_available", "") or ""))
            self.f_deadline.set_date(str(row.get("deadline", "") or ""))
            self.f_color.set_color(str(row.get("color", "Cyan")))
            self.f_memo.setPlainText(str(row.get("memo", "")))
        else:
            self.f_color.set_color(default_color)

    def _on_accept(self) -> None:
        if not self.f_title.text().strip():
            QMessageBox.warning(self, "入力エラー", "タイトルを入力してください")
            return
        # 編集時のステータス変更は仕様 2.3 の制約を確認する
        df = self.state.df_nodes
        new_status = self.f_status.currentText()
        if (self._edit_idx and self._edit_idx in df.index
                and new_status != str(df.loc[self._edit_idx, "status"])):
            err = LG.status_change_error(df, self._edit_idx, new_status)
            if err:
                QMessageBox.warning(self, "ステータス変更不可", err)
                return
        self.accept()

    def apply_to_state(self) -> pd.Series:
        """入力値をインメモリの df_nodes に反映して返す（DB 書き込みは Ctrl+S）。
        ステータスが変わった場合は実績完了日の設定・親の自動 done も行う。"""
        df = self.state.df_nodes
        old_status = (str(df.loc[self._edit_idx, "status"])
                      if self._edit_idx and self._edit_idx in df.index else "todo")
        ds = self.get_series()
        df.loc[ds.name] = ds
        if str(ds["status"]) != old_status:
            LG.apply_status(df, ds.name, str(ds["status"]))
        return ds

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
        ds["start_available"] = self.f_start.get_date() or None
        ds["deadline"]        = self.f_deadline.get_date() or None
        ds["color"]           = self.f_color.current_color()
        ds["memo"]            = self.f_memo.toPlainText()
        ds["updated_at"]      = datetime.date.today().isoformat()
        return ds


# ---------- クイック追加ダイアログ（Ctrl+N） ----------

class QuickAddDialog(QDialog):
    """
    1 行入力でチケットを作るダイアログ（B2）。
    入力例: 「見積書レビュー 2h 金曜 @設計書 #先方確認済み」
    解析は logic.parse_quick_add。@Task は候補リストから選べる。@ なしは Inbox 行き。
    確定後は result_data に {title, hours, deadline, start, memo, parent} を持つ。
    """

    _HELP = ("工数: 2h 30m 1.5 1時間半 ／ 納期: 明日 金 来週水 9/30 月末 5日 ／ "
             "範囲: 9/28〜10/2 ／ @Task（なしは Inbox） ／ #以降はメモ ／ 「」で囲むとタイトル")

    def __init__(self, state, default_hours: Optional[float] = None,
                 recent_tasks=(), parent=None):
        super().__init__(parent)
        self.state = state
        self._default_hours = default_hours
        self._recent = list(recent_tasks)
        self._chosen: dict = {}   # 候補から選んだ @語（正規化）→ Task IDX
        self._parsed: dict = {}
        self._task_idx: Optional[str] = None
        self.result_data: Optional[dict] = None

        self.setWindowTitle("＋ クイック追加")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("例: 見積書レビュー 2h 金曜 @設計書 #先方確認済み")
        self.edit.setStyleSheet("QLineEdit { font-size: 11pt; padding: 5px; }")
        lay.addWidget(self.edit)

        self.cands = QListWidget()
        self.cands.setMaximumHeight(160)
        self.cands.setVisible(False)
        self.cands.itemDoubleClicked.connect(lambda _: self._choose_candidate())
        lay.addWidget(self.cands)

        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setStyleSheet(qss(
            "QLabel { background:@surface_alt; border:1px solid @border_light;"
            " border-radius:6px; padding:6px; }"))
        lay.addWidget(self.preview)

        help_lbl = QLabel(self._HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet(qss("QLabel { color:@text_muted; font-size:8pt; }"))
        lay.addWidget(help_lbl)

        self.btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                     | QDialogButtonBox.StandardButton.Cancel)
        self.btns.button(QDialogButtonBox.StandardButton.Ok).setText("作成 (Enter)")
        self.btns.accepted.connect(self._on_create)
        self.btns.rejected.connect(self.reject)
        lay.addWidget(self.btns)

        self.edit.textChanged.connect(self._update)
        self.edit.installEventFilter(self)
        self._update()

    # ── 入力の解析とプレビュー ──

    def _inbox_full(self) -> bool:
        cfg = self.state.config
        s = LG.inbox_summary(self.state.df_nodes, self.state.user,
                             cfg.inbox_max_items, cfg.inbox_stale_days)
        return s["over"]

    def _update(self) -> None:
        p = LG.parse_quick_add(self.edit.text(), holidays=self.state.config.holidays)
        self._parsed = p
        df = self.state.df_nodes
        errors, notes = [], []
        self._task_idx = None
        q = p["task_query"]

        # @Task の解決と候補リスト
        show_cands = False
        if q is not None:
            key = LG.norm_key(q)
            if key in self._chosen and self._chosen[key] in df.index:
                self._task_idx = self._chosen[key]
            else:
                self._task_idx, n = LG.resolve_task_query(
                    df, q, self.state.user, self._recent)
                show_cands = True
                if self._task_idx is None:
                    errors.append("該当する Task がありません" if n == 0
                                  else f"Task 候補 {n} 件 — ↑↓ で選んで Enter")
        self._fill_candidates(q if show_cands else None)

        if not p["title"]:
            errors.append("チケット名を入力してください")
        if q is None and self._inbox_full():
            errors.append(f"Inbox が上限（{self.state.config.inbox_max_items} 件）です。"
                          "@ で Task を指定してください")

        # プレビュー（チップ風に 1 行で）
        def chip(text: str, color: str = C.TEXT) -> str:
            return f"<span style='color:{color};'>{text}</span>"
        parts = [chip(f"📄 <b>{self._esc(p['title']) or '（未入力）'}</b>")]
        hours = p["hours"]
        if hours is not None:
            parts.append(chip(f"⏱ {hours:g}h" + ("（15分単位に切上げ）" if p["hours_rounded"] else "")))
        elif self._default_hours:
            parts.append(chip(f"⏱ {self._default_hours:g}h（選択スロット）"))
        if p["start"]:
            parts.append(chip(f"▶ {self._fmt_date(p['start'])}"))
        if p["deadline"]:
            parts.append(chip(f"📅 {self._fmt_date(p['deadline'])}"))
        if self._task_idx:
            path = " ＞ ".join(LG.node_path_titles(df, self._task_idx))
            parts.append(chip(f"📁 {self._esc(path)}", C.ACCENT))
        elif q is None:
            parts.append(chip("📥 Inbox", C.INBOX))
        if p["memo"]:
            memo = p["memo"] if len(p["memo"]) <= 30 else p["memo"][:30] + "…"
            parts.append(chip(f"📝 {self._esc(memo)}"))
        html = " │ ".join(parts)
        for w in p["warnings"]:
            html += f"<br>{chip('⚠ ' + self._esc(w), C.WARNING)}"
        for h in p["hints"]:
            html += f"<br>{chip('💡 ' + self._esc(h), C.TEXT_SUB)}"
        for e in errors:
            html += f"<br>{chip('✖ ' + self._esc(e), C.DANGER)}"
        self.preview.setText(html)
        self.btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not errors)

    @staticmethod
    def _esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def _fmt_date(d) -> str:
        return f"{d.month}/{d.day}({'月火水木金土日'[d.weekday()]})"

    # ── @Task 候補 ──

    def _fill_candidates(self, query: Optional[str]) -> None:
        self.cands.clear()
        if query is None:
            self.cands.setVisible(False)
            return
        for idx, path in LG.quick_add_task_candidates(
                self.state.df_nodes, query, self.state.user, self._recent):
            item = QListWidgetItem(path)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.cands.addItem(item)
        self.cands.setVisible(self.cands.count() > 0)
        if self.cands.count():
            self.cands.setCurrentRow(0)

    def _choose_candidate(self) -> None:
        """選択中の候補を @語 として確定する（入力欄の @語 をタイトルで置き換え）"""
        item = self.cands.currentItem()
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        title = re.sub(r"\s+", "", str(self.state.df_nodes.loc[idx, "title"]))
        text = self.edit.text()
        memo_m = re.search(r"(?:^|\s)[#＃]", text)
        body_end = memo_m.start() if memo_m else len(text)
        hits = [m for m in re.finditer(r"(?:(?<=\s)|^)[@＠]\S*", text)
                if m.start() < body_end]
        if not hits:
            return
        m = hits[-1]
        new_text = text[:m.start()] + "@" + title + text[m.end():]
        self._chosen[LG.norm_key(title)] = idx
        self.edit.setText(new_text)
        self.edit.setCursorPosition(m.start() + 1 + len(title))

    def eventFilter(self, obj, event) -> bool:
        if obj is self.edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if self.cands.isVisible() and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = -1 if key == Qt.Key.Key_Up else 1
                row = max(0, min(self.cands.count() - 1, self.cands.currentRow() + step))
                self.cands.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # 候補表示中の Enter は候補の確定、それ以外は作成
                if self.cands.isVisible() and self.cands.currentItem() is not None:
                    self._choose_candidate()
                else:
                    self._on_create()
                return True
        return super().eventFilter(obj, event)

    # ── 確定 ──

    def _on_create(self) -> None:
        self._update()
        if not self.btns.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            return
        p = self._parsed
        hours = p["hours"] if p["hours"] is not None else (self._default_hours or 0.0)
        self.result_data = {
            "title":    p["title"],
            "hours":    hours,
            "deadline": p["deadline"],
            "start":    p["start"],
            "memo":     p["memo"],
            "parent":   self._task_idx or DB.INBOX_PARENT,
        }
        self.accept()


# ---------- Inbox 振り分けダイアログ ----------

class InboxTriageDialog(QDialog):
    """
    Inbox（Task 未設定）チケットを Task へ振り分けるダイアログ（B2）。
    各行の移動先は、過去チケット名との類似度（logic.suggest_task）で自動提案する。
    変更はインメモリ（Ctrl+Z 可・Ctrl+S で保存）。
    """
    _COLS = ["経過", "チケット", "見積", "納期", "メモ", "移動先 Task（入力で絞り込み）"]
    _SUGGEST_BG = C.SUGGEST_BG  # 自動提案した移動先の背景色

    def __init__(self, state, copy_prompt_func=None, parent=None):
        super().__init__(parent)
        self.state = state
        self._copy_prompt_func = copy_prompt_func
        self.setWindowTitle("📥 Inbox の振り分け")
        self.resize(980, 480)
        lay = QVBoxLayout(self)

        self.head = QLabel()
        self.head.setStyleSheet(qss("QLabel { font-weight:bold; color:@inbox; }"))
        lay.addWidget(self.head)

        self.table = QTableWidget(0, len(self._COLS))
        self.table.setHorizontalHeaderLabels(self._COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for c, w in enumerate([55, 220, 55, 90, 220]):
            self.table.setColumnWidth(c, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, stretch=1)

        self.info = InfoLabel()
        lay.addWidget(self.info)

        row = QHBoxLayout()
        self.move_btn = QPushButton("✔ 移動先を選んだ行を移動")
        self.move_btn.clicked.connect(self._on_move)
        self.del_btn = QPushButton("🗑 選択行を削除")
        self.del_btn.clicked.connect(self._on_delete)
        self.ai_btn = QPushButton("🤖 AI 振り分け用プロンプトをコピー")
        self.ai_btn.setToolTip("Task 一覧 + Inbox 一覧をコピーします。\n"
                               "LLM の返答は AI取込タブの「取り込み」で反映できます")
        self.ai_btn.clicked.connect(self._on_copy_prompt)
        later_btn = QPushButton("後で")
        later_btn.clicked.connect(self.reject)
        for b in (self.move_btn, self.del_btn, self.ai_btn):
            b.setStyleSheet(STYLE_BUTTON)
            row.addWidget(b)
        row.addStretch()
        later_btn.setStyleSheet(STYLE_BUTTON)
        row.addWidget(later_btn)
        lay.addLayout(row)

        self._task_items = self._task_choices()
        self._rebuild()

    def _task_choices(self) -> list:
        """移動先候補 [(階層パス, task_idx), ...]（完了・中止 Task 除く、パス順）"""
        df = self.state.df_nodes
        tasks = LG.quick_add_task_candidates(df, "", self.state.user, limit=100000)
        return sorted(((path, idx) for idx, path in tasks), key=lambda x: x[0])

    def _rebuild(self) -> None:
        cfg = self.state.config
        df = self.state.df_nodes
        inbox = LG.inbox_tickets(df, self.state.user)
        s = LG.inbox_summary(df, self.state.user, cfg.inbox_max_items, cfg.inbox_stale_days)
        self.head.setText(f"📥 Inbox {s['count']} 件（上限 {cfg.inbox_max_items} 件"
                          f"・最古 {s['oldest_days']} 日前）")
        self.table.setRowCount(0)
        today = datetime.date.today()
        from PySide6.QtWidgets import QCompleter
        for idx, r in inbox.iterrows():
            row = self.table.rowCount()
            self.table.insertRow(row)
            try:
                age = (today - datetime.date.fromisoformat(str(r.get("created_at", ""))[:10])).days
            except ValueError:
                age = 0
            est = float(r.get("estimated_hours") or 0)
            vals = [f"{age}日", str(r.get("title", "")),
                    f"{est:g}h" if est else "-",
                    str(r.get("deadline") or "") or "-",
                    str(r.get("memo") or "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, idx)
                if c == 0:
                    # 滞留の色分け: stale_days 超=橙、7 日超=赤
                    if age > 7:
                        it.setBackground(QColor(C.ROW_ERROR_BG))
                    elif age > cfg.inbox_stale_days:
                        it.setBackground(QColor(C.ROW_WARN_BG))
                self.table.setItem(row, c, it)
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItem("（未選択）", None)
            for path, t_idx in self._task_items:
                combo.addItem(path, t_idx)
            comp = combo.completer()
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            sug = LG.suggest_task(df, str(r.get("title", "")), str(r.get("memo") or ""),
                                  self.state.user)
            if sug:
                i = combo.findData(sug)
                if i >= 0:
                    combo.setCurrentIndex(i)
                    combo.setStyleSheet(f"QComboBox {{ background:{self._SUGGEST_BG}; }}")
                    combo.setToolTip("💡 過去のチケット名から自動提案した移動先です")
            self.table.setCellWidget(row, 5, combo)
        has = self.table.rowCount() > 0
        self.move_btn.setEnabled(has)
        self.del_btn.setEnabled(has)

    def _row_idx(self, row: int) -> Optional[str]:
        it = self.table.item(row, 0)
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _on_move(self) -> None:
        df = self.state.df_nodes
        today = datetime.date.today().isoformat()
        moved = 0
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 5)
            i = combo.findText(combo.currentText())  # 手入力の場合も一致する候補で確定
            task_idx = combo.itemData(i) if i > 0 else None
            idx = self._row_idx(row)
            if not task_idx or idx not in df.index:
                continue
            siblings = df[df["parent_id"] == task_idx]
            df.loc[idx, "parent_id"] = task_idx
            df.loc[idx, "priority"] = int(siblings["priority"].max()) + 1 if not siblings.empty else 1
            df.loc[idx, "updated_at"] = today
            moved += 1
        if not moved:
            self.info.set_info("⚠ 移動先を選んだ行がありません")
            return
        self.state.nodes_modified = True
        self.state.notify_dirty()
        self._rebuild()
        self.info.set_info(f"{moved} 件を振り分けました（Ctrl+S で保存 / Ctrl+Z で取り消し）")

    def _on_delete(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            self.info.set_info("⚠ 削除する行を選択してください")
            return
        df = self.state.df_nodes
        ans = QMessageBox.question(self, "削除確認", f"選択した {len(rows)} 件を論理削除しますか？")
        if ans != QMessageBox.StandardButton.Yes:
            return
        today = datetime.date.today().isoformat()
        skipped = []
        for row in rows:
            idx = self._row_idx(row)
            reason = LG.delete_block_reason(df, idx, self.state.user)
            if reason:
                skipped.append(f"{df.loc[idx, 'title']}: {reason}")
                continue
            df.loc[idx, "status"] = "deleted"
            df.loc[idx, "updated_at"] = today
        self.state.nodes_modified = True
        self.state.notify_dirty()
        self._rebuild()
        if skipped:
            QMessageBox.warning(self, "削除できなかったチケット", "\n".join(skipped))

    def _on_copy_prompt(self) -> None:
        if self._copy_prompt_func:
            self._copy_prompt_func()
            self.info.set_info("コピーしました。LLM の返答は AI取込タブで取り込めます")
