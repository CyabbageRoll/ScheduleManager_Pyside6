"""
test_gui_headless.py - GUI をディスプレイなしでテストするスクリプト

実行方法:
    cd src/
    QT_QPA_PLATFORM=offscreen python test_gui_headless.py

offscreen プラットフォームを使うと、ウィンドウを画面に表示せずに
Qt ウィジェットを生成・操作でき、ボタンクリック等の動作を検証できる。
"""
import os
import sys
import datetime
import traceback
import tempfile
from pathlib import Path

# offscreen プラットフォームを強制設定（未設定なら設定）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent))

PASS = 0
FAIL = 0


def ok(label: str) -> None:
    global PASS
    PASS += 1
    print(f"  [OK]  {label}")


def ng(label: str, exc: Exception = None) -> None:
    global FAIL
    FAIL += 1
    msg = f"  [NG]  {label}"
    if exc:
        msg += f"  → {exc}"
    print(msg)
    if exc:
        traceback.print_exc()


# -------------------------------------------------------
# テスト用 AppState・DB の準備
# -------------------------------------------------------
def make_state(tmpdir: str):
    from schedule_app import load_config, AppState, APP_VERSION
    from db import Database

    cfg = load_config()
    cfg.members = ["yamada@email.com", "tanaka@email.com", "suzuki@email.com"]
    cfg.username = "yamada@email.com"
    state = AppState(config=cfg)
    state.db = Database(tmpdir)
    state.reload_nodes()
    state.reload_daily()
    state.reload_memo()
    return state, APP_VERSION


def make_test_data(state):
    """テスト用ノードを投入する"""
    from db import create_initial_node
    today = datetime.date.today().isoformat()

    pj = create_initial_node("yamada@email.com", "project1", "テストPJ", "0", 1)
    state.db.upsert_node(pj)
    task_ds = create_initial_node("yamada@email.com", "task", "テストTask", pj.name, 1)
    state.db.upsert_node(task_ds)
    state.db.create_auto_children(task_ds, "yamada@email.com")

    ticket1 = create_initial_node("yamada@email.com", "ticket", "チケットA", task_ds.name, 2)
    ticket1["estimated_hours"] = 2.0
    ticket1["deadline"] = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    state.db.upsert_node(ticket1)

    ticket2 = create_initial_node("tanaka@email.com", "ticket", "チケットB", task_ds.name, 3)
    ticket2["estimated_hours"] = 1.0
    ticket2["deadline"] = (datetime.date.today() + datetime.timedelta(days=10)).isoformat()
    ticket2["assigned_to"] = "tanaka@email.com"
    state.db.upsert_node(ticket2)

    state.reload_nodes()
    return pj.name, task_ds.name, ticket1.name, ticket2.name


# -------------------------------------------------------
# テスト群
# -------------------------------------------------------
def test_state_properties(state):
    """AppState の user / refresh / save / load プロパティ確認"""
    print("\n[1] AppState プロパティテスト")
    try:
        assert state.user == "yamada@email.com", f"user={state.user}"
        ok("state.user == 'yamada@email.com'")
    except Exception as e:
        ng("state.user", e)

    try:
        assert state.current_member == "yamada@email.com"
        ok("state.current_member == 'yamada@email.com'")
    except Exception as e:
        ng("state.current_member", e)

    try:
        state.current_member = "tanaka@email.com"
        # 仕様: user はログインユーザー固定（メンバーボタンで変わらない）
        assert state.user == "yamada@email.com", f"user={state.user}"
        assert state.current_member == "tanaka@email.com"
        state.current_member = "yamada@email.com"  # 元に戻す
        ok("current_member setter → login_user は変わらない（表示メンバーのみ変更）")
    except Exception as e:
        state.current_member = "yamada@email.com"  # 失敗時も必ず元に戻す
        ng("current_member setter", e)

    try:
        called = []
        state.refresh_func = lambda: called.append(1)
        state.refresh()
        assert called == [1]
        ok("state.refresh() が refresh_func を呼び出す")
    except Exception as e:
        ng("state.refresh()", e)

    try:
        state.save()
        ok("state.save() が例外なく完了する")
    except Exception as e:
        ng("state.save()", e)

    try:
        state.load()
        ok("state.load() が例外なく完了する")
    except Exception as e:
        ng("state.load()", e)


def test_main_window(state, version):
    """MainWindow の生成と基本動作"""
    print("\n[2] MainWindow テスト")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    try:
        from ui_main import MainWindow
        win = MainWindow(state, version)
        win.show()
        ok("MainWindow 生成 OK")
    except Exception as e:
        ng("MainWindow 生成", e)
        return None

    try:
        win.refresh()
        ok("MainWindow.refresh() OK")
    except Exception as e:
        ng("MainWindow.refresh()", e)

    return win


def test_tree_pane(win, pj_idx):
    """TreePane のフィルタートグル確認"""
    print("\n[3] TreePane テスト")
    tree_pane = win.main_pane.tree_pane

    try:
        assert hasattr(tree_pane, "filter_btn"), "filter_btn が存在しない"
        ok("filter_btn が存在する")
    except Exception as e:
        ng("filter_btn 存在確認", e)
        return

    try:
        # フィルターON
        tree_pane.filter_btn.setChecked(True)
        assert tree_pane._filter_own is True
        ok("フィルターON: _filter_own=True")
    except Exception as e:
        ng("フィルターON", e)

    try:
        tree_pane.refresh()
        ok("フィルターON 後の refresh() OK")
    except Exception as e:
        ng("フィルターON 後の refresh()", e)

    try:
        # フィルターOFF
        tree_pane.filter_btn.setChecked(False)
        tree_pane.refresh()
        ok("フィルターOFF 後の refresh() OK")
    except Exception as e:
        ng("フィルターOFF 後の refresh()", e)

    try:
        # ノード選択シグナルのシミュレーション
        tree_pane._selected_idx = pj_idx
        tree_pane.node_selected.emit(pj_idx)
        ok(f"node_selected シグナル送出 ({pj_idx[:12]}...)")
    except Exception as e:
        ng("node_selected シグナル", e)


def test_table_pane(win, pj_idx, task_idx):
    """TablePane のヘッダーラベルとノード表示確認"""
    print("\n[4] TablePane テスト")
    table_pane = win.main_pane.table_pane

    try:
        assert hasattr(table_pane, "header_label"), "header_label が存在しない"
        ok("header_label が存在する")
    except Exception as e:
        ng("header_label 存在確認", e)
        return

    try:
        table_pane.update_for_parent(pj_idx)
        label_text = table_pane.header_label.text()
        assert "Project1" in label_text or "テストPJ" in label_text, \
            f"label={label_text!r}"
        ok(f"Project1 選択時ヘッダー: {label_text!r}")
    except Exception as e:
        ng("Project1 ヘッダーラベル", e)

    try:
        table_pane.update_for_parent(task_idx)
        label_text = table_pane.header_label.text()
        assert "Task" in label_text, f"label={label_text!r}"
        ok(f"Task 選択時ヘッダー: {label_text!r}")
    except Exception as e:
        ng("Task ヘッダーラベル", e)

    try:
        rows = table_pane.table.rowCount()
        ok(f"テーブル行数: {rows} 件")
    except Exception as e:
        ng("テーブル行数", e)


def test_detail_pane(win, task_idx, ticket_idx):
    """DailyScheduleWidget（schedule_panel）の割り当て・Free 動作確認"""
    print("\n[5] DetailPane（割り当て・Free）テスト")
    # スケジュール操作は DailyScheduleWidget (win.schedule_panel) に移動した
    panel = win.schedule_panel
    state = win.state

    # チケット選択状態をセット（assign_ticket で _selected_ticket に保持）
    try:
        panel.assign_ticket(ticket_idx)
        assert panel._selected_ticket == ticket_idx
        ok(f"set_selected_ticket OK ({ticket_idx[:12]}...)")
    except Exception as e:
        ng("set_selected_ticket", e)

    # 日次スケジュールの新規行作成
    try:
        from db import daily_sch_idx
        sch_id = daily_sch_idx(state.current_date, state.user)
        # スロット 36 番（9:00）を選択した状態をシミュレーション
        panel.schedule_table.setCurrentCell(36, 1)
        panel.schedule_table.selectRow(36)
        # 割り当て実行
        panel._update_schedule_slots([36], ticket_idx)
        sch_row = state.df_daily.loc[sch_id] if sch_id in state.df_daily.index else None
        from db import DAILY_TIME_COLS
        col = DAILY_TIME_COLS[36]
        assigned = sch_row[col] if sch_row is not None and col in sch_row.index else None
        assert assigned == ticket_idx, f"assigned={assigned}"
        ok(f"スロット割り当て OK (col={col})")
    except Exception as e:
        ng("スロット割り当て", e)

    try:
        # Free（割り当て解除）
        panel._update_schedule_slots([36], "")
        from db import daily_sch_idx, DAILY_TIME_COLS
        sch_id = daily_sch_idx(state.current_date, state.user)
        col = DAILY_TIME_COLS[36]
        cleared = state.df_daily.loc[sch_id, col] if sch_id in state.df_daily.index else "x"
        assert cleared == "", f"cleared={cleared!r}"
        ok("スロット Free（解除）OK")
    except Exception as e:
        ng("スロット Free", e)


def test_edit_delete(win, task_idx, ticket_idx):
    """TablePane 編集・削除ボタンの前提条件確認"""
    print("\n[6] 編集・削除 前提条件テスト")
    table_pane = win.main_pane.table_pane
    state = win.state

    # 編集: state.user と assigned_to が一致するチケットなら編集ダイアログが開く
    try:
        assert hasattr(state, "user"), "state.user が未定義"
        ok(f"state.user = {state.user!r}")
    except Exception as e:
        ng("state.user 存在確認", e)

    try:
        df = state.df_nodes
        if ticket_idx in df.index:
            is_own = df.loc[ticket_idx, "assigned_to"] == state.user
            ok(f"チケットA は自分のもの: {is_own} (assigned_to={df.loc[ticket_idx, 'assigned_to']!r})")
        else:
            ng("チケットA が df_nodes に存在しない")
    except Exception as e:
        ng("assigned_to 確認", e)

    # _current_idx が正しく動作するか確認
    try:
        table_pane.update_for_parent(task_idx)
        # 0行目を選択
        if table_pane.table.rowCount() > 0:
            table_pane.table.selectRow(0)
            idx = table_pane._current_idx()
            assert idx is not None, "selected idx is None"
            short = repr(idx)[:20]
            ok(f"_current_idx() = {short}")
        else:
            ok("テーブルが空のためスキップ")
    except Exception as e:
        ng("_current_idx()", e)


def test_gantt_view(win):
    """GanttView の Task グループ表示確認"""
    print("\n[7] GanttView テスト")
    gantt = win.gantt_view

    try:
        gantt.refresh()
        ok("GanttView.refresh() OK")
    except Exception as e:
        ng("GanttView.refresh()", e)
        return

    try:
        rows = gantt.table.rowCount()
        ok(f"GanttView テーブル行数: {rows} 行")
    except Exception as e:
        ng("GanttView テーブル行数", e)

    try:
        # Task行（種別列が "── Task ──"）を探す
        task_rows = []
        for r in range(gantt.table.rowCount()):
            item = gantt.table.item(r, 0)
            if item and "Task" in item.text():
                task_rows.append(r)
        ok(f"Task ヘッダー行数: {len(task_rows)} 件")
    except Exception as e:
        ng("Task ヘッダー行確認", e)

    try:
        # Ticket行（種別列が "  Ticket"）を探す
        ticket_rows = []
        for r in range(gantt.table.rowCount()):
            item = gantt.table.item(r, 0)
            if item and "Ticket" in item.text():
                ticket_rows.append(r)
        ok(f"Ticket 行数: {len(ticket_rows)} 件")
    except Exception as e:
        ng("Ticket 行確認", e)

    try:
        # Project フィルターが効くか
        if gantt.pj_combo.count() > 1:
            gantt.pj_combo.setCurrentIndex(1)
            gantt._rebuild_table()
            filtered_rows = gantt.table.rowCount()
            gantt.pj_combo.setCurrentIndex(0)
            gantt._rebuild_table()
            all_rows = gantt.table.rowCount()
            ok(f"Projectフィルター: 全{all_rows}行 / フィルター後{filtered_rows}行")
        else:
            ok("Project フィルター: Project が1件以下のためスキップ")
    except Exception as e:
        ng("Projectフィルター", e)


def test_actual_hours_propagation(win, task_idx, ticket_idx):
    """
    スロット割り当て時に Ticket の actual_hours が更新され、
    親 Task にも伝播することを確認する。
    """
    print("\n[9] 工数伝播テスト（actual_hours）")
    panel = win.schedule_panel
    state = win.state
    from db import DAILY_TIME_COLS

    # 事前に全スロットをクリア（テスト独立性確保）
    try:
        panel._update_schedule_slots(list(range(len(DAILY_TIME_COLS))), "")
        ok("全スロット初期化 OK")
    except Exception as e:
        ng("全スロット初期化", e)
        return

    before_ticket = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
    before_task   = float(state.df_nodes.loc[task_idx,   "actual_hours"])

    # スロット 36（9:00）にチケットを割り当てる
    try:
        panel._update_schedule_slots([36], ticket_idx)
        after_ticket = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
        assert abs(after_ticket - (before_ticket + 0.25)) < 1e-9, \
            f"ticket actual_hours: {before_ticket} → {after_ticket} (期待: {before_ticket + 0.25})"
        ok(f"Ticket actual_hours +0.25: {before_ticket:.2f} → {after_ticket:.2f}")
    except Exception as e:
        ng("Ticket actual_hours 増加", e)
        return

    # 親 Task の actual_hours が伝播して増えているか確認
    try:
        after_task = float(state.df_nodes.loc[task_idx, "actual_hours"])
        assert abs(after_task - (before_task + 0.25)) < 1e-9, \
            f"task actual_hours: {before_task} → {after_task} (期待: {before_task + 0.25})"
        ok(f"Task actual_hours 伝播 +0.25: {before_task:.2f} → {after_task:.2f}")
    except Exception as e:
        ng("Task actual_hours 伝播", e)

    # スロット 37（9:15）にも割り当てて 2 スロット分確認
    try:
        panel._update_schedule_slots([37], ticket_idx)
        two_slot_ticket = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
        two_slot_task   = float(state.df_nodes.loc[task_idx,   "actual_hours"])
        assert abs(two_slot_ticket - (before_ticket + 0.50)) < 1e-9, \
            f"ticket 2スロット: {two_slot_ticket}"
        assert abs(two_slot_task - (before_task + 0.50)) < 1e-9, \
            f"task 2スロット: {two_slot_task}"
        ok(f"2スロット割り当て後 Ticket={two_slot_ticket:.2f} Task={two_slot_task:.2f}")
    except Exception as e:
        ng("2スロット割り当て", e)

    # Free（解除）で元に戻るか確認
    try:
        panel._update_schedule_slots([36, 37], "")
        freed_ticket = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
        freed_task   = float(state.df_nodes.loc[task_idx,   "actual_hours"])
        assert abs(freed_ticket - before_ticket) < 1e-9, \
            f"Free後 ticket: {freed_ticket} (期待: {before_ticket})"
        assert abs(freed_task - before_task) < 1e-9, \
            f"Free後 task: {freed_task} (期待: {before_task})"
        ok(f"Free後 Ticket={freed_ticket:.2f} Task={freed_task:.2f} (元に戻った)")
    except Exception as e:
        ng("Free後の actual_hours 復元", e)

    # recalc_actual_hours との一致確認
    try:
        df_recalc = state.db.recalc_actual_hours(state.df_nodes, state.df_daily)
        ticket_recalc = float(df_recalc.loc[ticket_idx, "actual_hours"])
        task_recalc   = float(df_recalc.loc[task_idx,   "actual_hours"])
        ticket_now    = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
        task_now      = float(state.df_nodes.loc[task_idx,   "actual_hours"])
        assert abs(ticket_recalc - ticket_now) < 1e-9, \
            f"Ticket recalc={ticket_recalc} vs memory={ticket_now}"
        assert abs(task_recalc - task_now) < 1e-9, \
            f"Task recalc={task_recalc} vs memory={task_now}"
        ok(f"recalc_actual_hours との一致確認 OK (ticket={ticket_recalc:.2f}, task={task_recalc:.2f})")
    except Exception as e:
        ng("recalc_actual_hours との一致確認", e)


def test_search_view(win, task_idx, ticket_idx):
    """SearchView の ticket 固定検索と親階層列の確認"""
    print("\n[10] SearchView テスト（ticket固定・親階層列）")
    search_view = win.search_view

    # type_checks が存在しないことを確認（削除済み）
    try:
        assert not hasattr(search_view, "type_checks"), "type_checks が残っている"
        ok("type_checks（種別チェックボックス）が削除されている")
    except Exception as e:
        ng("type_checks 削除確認", e)

    # 検索 → ticket のみ返る
    try:
        search_view._on_search()
        rows = search_view.result_table.rowCount()
        ok(f"検索実行 → {rows} 件")
        for r in range(rows):
            item = search_view.result_table.item(r, 0)
            val = item.text() if item else ""
            assert val == "ticket", f"行{r} 種類={val!r}"
        ok("全結果行が ticket 種別")
    except Exception as e:
        ng("ticket 固定検索確認", e)

    # 列数確認（12列）
    try:
        col_count = search_view.result_table.columnCount()
        assert col_count == 13, f"列数: {col_count} (期待: 13)"
        ok(f"結果テーブル列数: {col_count} 列")
    except Exception as e:
        ng("テーブル列数確認", e)

    # Project1 列（列1）に親タイトルが表示されているか
    try:
        rows = search_view.result_table.rowCount()
        has_pj1 = any(
            (search_view.result_table.item(r, 1) or type("", (), {"text": lambda: ""})()).text() != ""
            for r in range(rows)
        )
        ok(f"Project1 列にデータあり: {has_pj1}")
    except Exception as e:
        ng("Project1 列確認", e)

    # _get_ancestors() の動作確認
    try:
        anc = search_view._get_ancestors(ticket_idx)
        assert isinstance(anc, dict), "戻り値が dict でない"
        assert set(anc.keys()) == {"project1", "project2", "project3", "project4", "task"}, \
            f"keys: {set(anc.keys())}"
        assert anc["task"] != "", f"task タイトルが空: {anc}"
        ok(f"_get_ancestors() OK: task={anc['task']!r}, project1={anc['project1']!r}")
    except Exception as e:
        ng("_get_ancestors()", e)

    # 期間実績工数の計算確認
    try:
        today = datetime.date.today().isoformat()
        # 日付範囲なし → 空辞書
        result_empty = search_view._calc_period_hours_batch([ticket_idx], "", "")
        assert result_empty == {}, f"空辞書期待: {result_empty}"
        ok("日付範囲なし → 空辞書を返す")
    except Exception as e:
        ng("期間実績（日付範囲なし）", e)

    try:
        today = datetime.date.today().isoformat()
        # 日付範囲あり → 辞書に ticket_idx キーがある
        result_with_date = search_view._calc_period_hours_batch([ticket_idx], today, today)
        assert ticket_idx in result_with_date, f"ticket_idx がキーに存在しない: {result_with_date}"
        ok(f"日付範囲あり → ticket_idx={ticket_idx[:12]}... の期間実績={result_with_date[ticket_idx]:.2f}h")
    except Exception as e:
        ng("期間実績（日付範囲あり）", e)

    # 日付範囲指定時に 期間実績(h) 列（列11）が数値 or "0.0" を表示
    try:
        search_view.f_from.setText(today)
        search_view.f_to.setText(today)
        search_view._on_search()
        rows = search_view.result_table.rowCount()
        for r in range(rows):
            item = search_view.result_table.item(r, 11)
            val = item.text() if item else "-"
            # "-" でなく数値文字列であることを確認
            assert val != "-", f"行{r}: 期間実績が '-' のまま"
        ok(f"日付範囲指定時 期間実績(h)列 に数値表示（{rows}行）")
        # フィールドをリセット
        search_view.f_from.setText("")
        search_view.f_to.setText("")
    except Exception as e:
        ng("期間実績(h)列 表示確認", e)


def test_link_field(state, ticket_idx):
    """nodes.link 列（休眠カラム）の存在確認と extract_md_links のテスト"""
    print("\n[11] リンク列・md リンク抽出テスト")
    from db import NODE_COLUMNS
    import logic as LG

    try:
        assert "link" in NODE_COLUMNS, "NODE_COLUMNS に link がない"
        assert "link" in state.df_nodes.columns, "df_nodes に link 列がない"
        ok("NODE_COLUMNS / df_nodes に link 列が存在する（休眠）")
    except Exception as e:
        ng("link 列の存在確認", e)
        return

    try:
        ds = state.df_nodes.loc[ticket_idx].copy()
        ds.name = ticket_idx
        ds["link"] = "https://example.com/spec.md"
        state.db.upsert_node(ds)
        state.reload_nodes()
        saved = state.df_nodes.loc[ticket_idx, "link"]
        assert saved == "https://example.com/spec.md", f"link={saved!r}"
        ok("link DB ラウンドトリップ OK（休眠列として保持）")
    except Exception as e:
        ng("link ラウンドトリップ", e)

    try:
        from ui_main import _NodeEditDialog
        dlg = _NodeEditDialog(None, "ticket", state, edit_idx=ticket_idx)
        assert not hasattr(dlg, "f_link"), "f_link が削除されていない"
        ok("_NodeEditDialog に f_link 欄がない（廃止済み）")
    except Exception as e:
        ng("_NodeEditDialog f_link 廃止確認", e)

    try:
        text = "参考: [仕様書](https://example.com/spec.pdf) と [議事録](C:/tmp/memo.md)"
        links = LG.extract_md_links(text)
        assert len(links) == 2, f"links={links}"
        assert links[0] == ("仕様書", "https://example.com/spec.pdf")
        assert links[1] == ("議事録", "C:/tmp/memo.md")
        ok("extract_md_links: 複数リンク抽出 OK")
    except Exception as e:
        ng("extract_md_links", e)

    try:
        assert LG.extract_md_links("リンクなしのテキスト") == []
        assert LG.extract_md_links("") == []
        ok("extract_md_links: リンクなし → [] OK")
    except Exception as e:
        ng("extract_md_links 空", e)


def test_report_logic(state):
    """週報集計（calc_period_hours / collect_report_data）と Markdown 生成のテスト"""
    print("\n[12] レポート生成ロジックテスト")
    import pandas as pd
    import logic as LG
    from db import DAILY_SCH_COLS, DAILY_TIME_COLS, daily_sch_idx, create_initial_node

    user = state.user
    today = datetime.date.today().isoformat()

    # P4 → Task → Ticket(完了/進行中) の階層を作成
    try:
        p4 = create_initial_node(user, "project4", "テストP4", "0", 1)
        state.db.upsert_node(p4)
        task = create_initial_node(user, "task", "P4配下Task", p4.name, 1)
        state.db.upsert_node(task)
        t_done = create_initial_node(user, "ticket", "完了チケット", task.name, 1)
        t_done["status"] = "done"
        t_done["actual_end"] = today
        t_done["estimated_hours"] = 2.0
        state.db.upsert_node(t_done)
        t_wip = create_initial_node(user, "ticket", "進行中チケット", task.name, 2)
        t_wip["estimated_hours"] = 4.0
        t_wip["deadline"] = (datetime.date.today()
                             + datetime.timedelta(days=3)).isoformat()
        state.db.upsert_node(t_wip)
        state.reload_nodes()
        ok("P4/Task/Ticket テストデータ作成 OK")
    except Exception as e:
        ng("テストデータ作成", e)
        return

    # 期間工数集計（2スロット = 0.5h）
    try:
        sch_id = daily_sch_idx(today, user)
        row = {c: "" for c in DAILY_SCH_COLS[1:]}
        row["Owner"] = user
        row[DAILY_TIME_COLS[36]] = t_wip.name
        row[DAILY_TIME_COLS[37]] = t_wip.name
        df_daily = pd.DataFrame([row], index=[sch_id])
        hours = LG.calc_period_hours(df_daily, [t_wip.name], today, today)
        assert abs(hours[t_wip.name] - 0.5) < 1e-9, f"hours={hours}"
        ok("calc_period_hours: 2スロット = 0.5h")
    except Exception as e:
        ng("calc_period_hours", e)
        return

    # 配下チケット収集と期間集計・分類
    try:
        tickets = LG.collect_descendant_tickets(state.df_nodes, p4.name)
        assert t_done.name in tickets and t_wip.name in tickets, f"tickets={tickets}"
        ok(f"collect_descendant_tickets: {len(tickets)} 件")
    except Exception as e:
        ng("collect_descendant_tickets", e)

    try:
        data = LG.collect_report_data(
            state.df_nodes, df_daily, p4.name, today, today)
        comp_titles = [r["title"] for r in data["completed"]]
        wip_titles = [r["title"] for r in data["in_progress"]]
        appr_titles = [r["title"] for r in data["approaching"]]
        assert "完了チケット" in comp_titles, f"completed={comp_titles}"
        assert "進行中チケット" in wip_titles, f"in_progress={wip_titles}"
        assert "進行中チケット" in appr_titles, f"approaching={appr_titles}"
        assert abs(data["period_hours_total"] - 0.5) < 1e-9, \
            f"total={data['period_hours_total']}"
        ok("collect_report_data: 完了/進行中/納期接近/合計工数 OK")
    except Exception as e:
        ng("collect_report_data", e)
        return

    # Markdown 生成
    try:
        md = LG.build_report_markdown(data, "weekly")
        assert "# 週報" in md and "テストP4" in md, md[:80]
        assert "完了チケット" in md and "進行中チケット" in md
        assert "0.50h" in md, "投入工数合計が見つからない"
        ok("build_report_markdown(weekly) OK")
        md_m = LG.build_report_markdown(data, "monthly")
        assert "# 月報" in md_m, md_m[:80]
        ok("build_report_markdown(monthly) OK")
    except Exception as e:
        ng("build_report_markdown", e)

    # P2 単位月次ファイルのパス命名規約
    try:
        import tempfile
        import pandas as pd
        from db import NODE_COLUMNS, create_initial_node
        td = tempfile.mkdtemp()
        user = state.user
        p1 = create_initial_node(user, "project1", "確認P1", "0", 1)
        p2 = create_initial_node(user, "project2", "確認P2", p1.name, 1)
        tkt = create_initial_node(user, "ticket", "確認Ticket", p2.name, 1)
        df_tmp = pd.DataFrame([p1, p2, tkt], columns=NODE_COLUMNS)
        df_tmp.index = [p1.name, p2.name, tkt.name]
        p = LG.report_p2_path(td, df_tmp, tkt.name, "202601")
        assert p is not None, "パスが None"
        assert p.parent.name == "reports", f"parent={p.parent.name}"
        fname = p.name
        assert fname.startswith("202601_"), f"yyyymm prefix: {fname}"
        assert p2.name in fname, f"p2idx in fname: {fname}"
        assert "確認P1" in fname and "確認P2" in fname, f"titles in fname: {fname}"
        ok(f"report_p2_path 命名規約 OK: {fname}")
    except Exception as e:
        ng("report_p2_path 命名規約", e)



def test_template_parse(state):
    """プロジェクトテンプレートのパース（Task/自動チケット/Ticket 階層）テスト"""
    print("\n[16] テンプレート一括作成テスト")
    import logic as LG

    df = state.df_nodes
    p4_idx = df[(df["node_type"] == "project4") & (df["title"] == "テストP4")].index[0]

    text = (
        "# コメント行\n"
        "> 要件整理\n"
        "ヒアリング, 1, 2.0, , , ,\n"
        "要件まとめ, 2, 2.0, , , ,メモ付き\n"
        "> 執筆\n"
        "ドラフト執筆, 1, 6.0, , , ,\n"
    )
    try:
        nodes, errors = LG.parse_template_text(text, p4_idx, state.user, df)
        assert not errors, f"errors={errors}"
        # Task2件 + 自動チケット2件×2 + テンプレチケット3件 = 9件
        assert len(nodes) == 9, f"nodes={len(nodes)}"
        ok(f"パース OK: {len(nodes)} 件（エラーなし）")
    except Exception as e:
        ng("parse_template_text", e)
        return

    try:
        tasks = [n for n in nodes if n["node_type"] == "task"]
        tickets = [n for n in nodes if n["node_type"] == "ticket"]
        assert [t["title"] for t in tasks] == ["要件整理", "執筆"]
        assert all(t["parent_id"] == p4_idx for t in tasks)
        ok("Task 2 件が P4 直下に生成される")
        # 各 Task に「詳細作成」「完了」が付与されている
        for task in tasks:
            children = [t["title"] for t in tickets if t["parent_id"] == task.name]
            assert "詳細作成" in children and "完了" in children, \
                f"{task['title']} の子: {children}"
        ok("自動チケット（詳細作成・完了）付与 OK")
        hearing = [t for t in tickets if t["title"] == "ヒアリング"][0]
        assert hearing["parent_id"] == tasks[0].name
        assert abs(float(hearing["estimated_hours"]) - 2.0) < 1e-9
        memo_t = [t for t in tickets if t["title"] == "要件まとめ"][0]
        assert memo_t["memo"] == "メモ付き"
        ok("チケットの親・見積・メモが正しい")
    except Exception as e:
        ng("テンプレート階層検証", e)

    # エラー系: Task 行より前のチケット行・同名 Task
    try:
        bad = "迷子チケット, 1, 1.0, , , ,\n> 要件整理\n"
        df_with = state.df_nodes.copy()
        for n in nodes:
            df_with.loc[n.name] = n
        _, errors2 = LG.parse_template_text(bad, p4_idx, state.user, df_with)
        assert len(errors2) == 2, f"errors2={errors2}"
        ok(f"エラー検出 OK: {len(errors2)} 件（迷子チケット・同名 Task）")
    except Exception as e:
        ng("テンプレートエラー検出", e)


def test_daily_log_markdown(state):
    """デイリーワークログ Markdown 生成のテスト"""
    print("\n[17] デイリーログ md 出力テスト")
    import pandas as pd
    import logic as LG
    from db import DAILY_SCH_COLS, DAILY_TIME_COLS, DAILY_LOG_COLS, daily_sch_idx

    df = state.df_nodes
    ticket_idx = df[df["title"] == "進行中チケット"].index[0]
    user = state.user
    today = datetime.date.today().isoformat()
    sch_id = daily_sch_idx(today, user)

    # 9:00〜9:30 連続 + 10:00〜10:15 の 2 区間
    row = {c: "" for c in DAILY_SCH_COLS[1:]}
    row["Owner"] = user
    row[DAILY_TIME_COLS[36]] = ticket_idx  # 09:00
    row[DAILY_TIME_COLS[37]] = ticket_idx  # 09:15
    row[DAILY_TIME_COLS[40]] = ticket_idx  # 10:00
    df_daily = pd.DataFrame([row], index=[sch_id])

    log_row = {c: "" for c in DAILY_LOG_COLS[1:]}
    log_row["Owner"] = user
    log_row["health_status"] = "Good"
    log_row["work_place"] = "Home"
    log_row["notes"] = "定時退社します"
    df_log = pd.DataFrame([log_row], index=[sch_id])

    try:
        md = LG.build_daily_log_markdown(df_daily, df, df_log, today, user)
        assert f"# 作業ログ {today}" in md, md[:40]
        assert "09:00〜09:30" in md, "連続区間がまとまっていない"
        assert "10:00〜10:15" in md, "単独区間が出力されていない"
        assert "進行中チケット" in md and "0.50" in md
        assert "Good" in md and "Home" in md and "定時退社します" in md
        ok("作業内訳（区間集約）・体調・連絡事項 OK")
    except Exception as e:
        ng("build_daily_log_markdown", e)
        return

    try:
        # 記録がない日は「記録なし」表示
        md_empty = LG.build_daily_log_markdown(
            df_daily, df, df_log, "2000-01-01", user)
        assert "勤務: 記録なし" in md_empty and "（記録なし）" in md_empty
        ok("記録なしの日も正常に生成される")
    except Exception as e:
        ng("記録なし日の生成", e)


def test_daily_export(win):
    """MemoView デイリーログ出力のテスト"""
    print("\n[18] デイリーログ出力 UI テスト")
    try:
        mv = win.memo_view
        with tempfile.TemporaryDirectory() as td:
            win.state.config.report_output_dir = td
            mv._on_export_daily()
            files = list((Path(td) / "daily").glob("*.md"))
            assert len(files) == 1, f"出力ファイル数={len(files)}"
            content = files[0].read_text(encoding="utf-8")
            assert content.startswith("# 作業ログ"), content[:30]
            ok(f"デイリーログ出力 OK: daily/{files[0].name}")
        win.state.config.report_output_dir = ""
    except Exception as e:
        win.state.config.report_output_dir = ""
        ng("デイリーログ出力", e)


def test_dashboard(win, ticket_idx):
    """Today ダッシュボード（4カード・バッジ・タブ遷移シグナル）のテスト"""
    print("\n[19] ダッシュボードテスト")
    import logic as LG
    dv = win.dashboard_view
    state = win.state

    try:
        dv.refresh()
        assert set(dv._cards.keys()) == {"schedule", "edit", "request", "team"}
        ok("4カード構成 OK")
    except Exception as e:
        ng("ダッシュボード refresh", e)
        return

    try:
        # 納期アラート: チケットA は納期5日後 → 接近に入る
        alerts = LG.find_deadline_alerts(state.df_nodes, user=state.user,
                                         within_days=7)
        titles = [r["title"] for r in alerts["approaching"]]
        assert "チケットA" in titles, f"approaching={titles}"
        assert dv._cards["edit"]["list"].count() >= 1
        ok(f"納期アラート検出 OK（接近 {len(titles)} 件）")
    except Exception as e:
        ng("納期アラート", e)

    try:
        # 過去納期のチケットは超過に入る
        df = state.df_nodes
        over_t = df[(df["node_type"] == "ticket")].index[0]
        orig_deadline = df.loc[over_t, "deadline"]
        df.loc[over_t, "deadline"] = "2000-01-01"
        alerts2 = LG.find_deadline_alerts(df, user="", within_days=7)
        assert any(r["ticket_idx"] == over_t for r in alerts2["overdue"])
        df.loc[over_t, "deadline"] = orig_deadline
        ok("納期超過検出 OK")
    except Exception as e:
        ng("納期超過検出", e)

    try:
        # タブ遷移シグナル
        received = []
        dv.navigate_requested.connect(lambda k: received.append(k))
        dv.navigate_requested.emit("request")
        assert received == ["request"]
        ok("navigate_requested シグナル OK")
    except Exception as e:
        ng("navigate シグナル", e)

    try:
        # MainWindow 側のタブ遷移マッピング
        win._on_dashboard_navigate("team")
        from ui_main import IDX_TEAM
        assert win.stack.currentIndex() == IDX_TEAM, \
            f"currentIndex={win.stack.currentIndex()}"
        ok("ダッシュボード → Team タブ遷移 OK")
    except Exception as e:
        ng("タブ遷移", e)


def test_pomodoro(win, ticket_idx):
    """ポモドーロタイマー（状態遷移・スロット記録・占有スキップ）のテスト"""
    print("\n[20] ポモドーロテスト")
    state = win.state
    pomo = win.pomodoro

    try:
        assert pomo._mode == "idle"
        title = str(state.df_nodes.loc[ticket_idx, "title"])
        pomo.set_ticket(ticket_idx, title)
        assert pomo.ticket_lbl.text() == title
        ok("チケット設定 OK")
    except Exception as e:
        ng("チケット設定", e)
        return

    try:
        pomo._start_work()
        assert pomo._mode == "work" and pomo._timer.isActive()
        pomo._timer.stop()  # テストでは tick を進めない
        ok("作業開始 → work 状態 OK")
        pomo._start_break()
        assert pomo._mode == "break"
        pomo._to_idle()
        assert pomo._mode == "idle" and not pomo._timer.isActive()
        ok("休憩 → idle の状態遷移 OK")
    except Exception as e:
        ng("状態遷移", e)

    # スロット記録（確認ダイアログを通らない内部メソッドで検証）
    try:
        from db import daily_sch_idx, DAILY_TIME_COLS
        today = datetime.date.today().isoformat()
        sch_idx = daily_sch_idx(today, state.login_user)
        before = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
        win._write_pomodoro_slots(sch_idx, [60, 61], ticket_idx)  # 15:00-15:30
        col = DAILY_TIME_COLS[60]
        assert state.df_daily.loc[sch_idx, col] == ticket_idx
        after = float(state.df_nodes.loc[ticket_idx, "actual_hours"])
        assert abs(after - (before + 0.5)) < 1e-9, f"{before} → {after}"
        ok(f"スロット記録 + actual_hours 反映 OK ({before:.2f} → {after:.2f})")
    except Exception as e:
        ng("スロット記録", e)
        return

    try:
        # 経過時間→スロット数の丸め確認（_on_pomodoro_finished の前段ロジック）
        start = datetime.datetime(2026, 6, 11, 15, 0, 0)
        end = start + datetime.timedelta(minutes=25)
        n = int(round((end - start).total_seconds() / 60 / 15))
        assert n == 2, f"25分 → {n} スロット"
        end2 = start + datetime.timedelta(minutes=5)
        n2 = int(round((end2 - start).total_seconds() / 60 / 15))
        assert n2 == 0, f"5分 → {n2} スロット"
        ok("15分丸め（25分→2スロット / 5分→0）OK")
    except Exception as e:
        ng("15分丸め", e)

    try:
        # 後始末: 記録したスロットを解除して工数を戻す
        panel = win.schedule_panel
        win.state.current_date = datetime.date.today().isoformat()
        panel._update_schedule_slots([60, 61], "")
        ok("テストスロットの後始末 OK")
    except Exception as e:
        ng("後始末", e)


def test_personal_review(state, win):
    """個人振り返り（週別P1工数・見積精度）のテスト"""
    print("\n[21] 個人振り返りテスト")
    import pandas as pd
    import logic as LG
    from db import DAILY_SCH_COLS, DAILY_TIME_COLS, daily_sch_idx

    user = state.user
    df = state.df_nodes
    ticket_idx = df[df["title"] == "チケットA"].index[0]

    try:
        today = datetime.date.today().isoformat()
        row = {c: "" for c in DAILY_SCH_COLS[1:]}
        row["Owner"] = user
        row[DAILY_TIME_COLS[36]] = ticket_idx
        row[DAILY_TIME_COLS[37]] = ticket_idx
        df_daily = pd.DataFrame([row], index=[daily_sch_idx(today, user)])
        weekly = LG.calc_weekly_user_hours_by_p1(df, df_daily, user, weeks=4)
        assert len(weekly["weeks"]) == 4, weekly["weeks"]
        assert "テストPJ" in weekly["by_p1"], f"by_p1={weekly['by_p1']}"
        assert abs(weekly["by_p1"]["テストPJ"][-1] - 0.5) < 1e-9
        ok(f"週別P1工数: 今週 {weekly['by_p1']['テストPJ'][-1]}h（テストPJ）")
    except Exception as e:
        ng("calc_weekly_user_hours_by_p1", e)

    try:
        acc = LG.calc_estimate_accuracy(df, user)
        # 「完了チケット」(est=2.0, done) が含まれる
        titles = [a["title"] for a in acc]
        assert "完了チケット" in titles, f"acc={titles}"
        ok(f"見積精度ペア抽出 OK: {len(acc)} 件")
    except Exception as e:
        ng("calc_estimate_accuracy", e)

    try:
        anal = win.anal_view
        anal._calc_personal()
        assert len(anal._fig.axes) == 2, f"axes={len(anal._fig.axes)}"
        ok("個人振り返り描画（2グラフ）OK")
        # 通常の集計に戻しても描画できる（Figure 再生成の確認）
        anal._calc()
        assert len(anal._fig.axes) == 1
        ok("振り返り後の通常集計 OK（リグレッションなし）")
    except Exception as e:
        ng("個人振り返り描画", e)


def test_slot_context_menu(win):
    """スロット右クリック割り当てメニュー（最近使った + 2階層グループ）"""
    print("\n[22] スロット右クリックメニューテスト")
    from db import create_initial_node, DAILY_TIME_COLS, daily_sch_idx
    panel = win.schedule_panel
    state = win.state
    df = state.df_nodes
    user = state.user

    # ── MRU ロジック（重複除去・順序・上限8）──
    try:
        state.recent_tickets = []
        for i in range(10):
            state.push_recent_ticket(f"id{i}")
        state.push_recent_ticket("id0")  # 既存を先頭へ
        assert state.recent_tickets[0] == "id0", state.recent_tickets
        assert len(state.recent_tickets) == 8, state.recent_tickets
        assert state.recent_tickets.count("id0") == 1, state.recent_tickets
        ok("push_recent_ticket（重複除去・順序・上限8）OK")
    except Exception as e:
        ng("push_recent_ticket", e)
    finally:
        state.recent_tickets = []

    # regularly チケットを追加（yamada 担当）
    task_idx = df[df["title"] == "テストTask"].index[0]
    reg = create_initial_node(user, "ticket", "定常チケットR", task_idx, 9)
    reg["status"] = "regularly"
    state.db.upsert_node(reg)
    state.reload_nodes()
    df = state.df_nodes
    ticket_a = df[df["title"] == "チケットA"].index[0]

    # ── 2階層グループ: プロジェクトパス group_label / Task・Ticket リーフ ──
    assignable_idx = df[
        (df["node_type"] == "ticket")
        & (df["assigned_to"] == user)
        & (df["status"].isin(["todo", "regularly"]))
    ].index
    try:
        groups = panel._assignable_groups(df, assignable_idx)
        # group_label はプロジェクトパス（テストPJ 配下なので "テストPJ"）
        labels = [gl for gl, _ in groups]
        assert "テストPJ" in labels, labels
        # 全リーフラベルを集約
        all_leaves = [(lbl) for _, leaves in groups for (_, lbl) in leaves]
        # リーフは「Task名 / Ticket名」、regularly は ↻ 付き
        assert "テストTask / チケットA" in all_leaves, all_leaves
        assert "↻ テストTask / 定常チケットR" in all_leaves, all_leaves
        # 他人担当(チケットB)・done(完了チケット) は出ない
        assert all("チケットB" not in l for l in all_leaves), all_leaves
        assert all("完了チケット" not in l for l in all_leaves), all_leaves
        # テストPJ グループ内 テストTask の並びは priority 昇順（A=2 < R=9）
        pj_leaves = [lbl for gl, leaves in groups if gl == "テストPJ" for (_, lbl) in leaves]
        order = [l for l in pj_leaves if "チケットA" in l or "定常チケットR" in l]
        assert order == ["テストTask / チケットA", "↻ テストTask / 定常チケットR"], order
        ok(f"2階層グループ（パス/Task・Ticket・priority昇順）OK（{len(groups)}グループ）")
    except Exception as e:
        ng("2階層グループ", e)

    # ── 割り当て(_assign_to_rows) → スロット + 工数加算 + MRU更新 ──
    try:
        state.recent_tickets = []
        before_h = float(df.loc[ticket_a, "actual_hours"] or 0)
        assigned = panel._assign_to_rows([40], ticket_a)
        sch_id = daily_sch_idx(state.current_date, user)
        col = DAILY_TIME_COLS[40]
        assert assigned is True
        assert state.df_daily.loc[sch_id, col] == ticket_a, state.df_daily.loc[sch_id, col]
        assert float(state.df_nodes.loc[ticket_a, "actual_hours"] or 0) > before_h
        assert state.recent_tickets[0] == ticket_a, state.recent_tickets
        ok("_assign_to_rows 割り当て + 工数加算 + MRU更新 OK")
    except Exception as e:
        ng("_assign_to_rows 割り当て", e)

    # ── 他人担当チケットは _assign_to_rows で拒否される ──
    try:
        ticket_b = df[df["title"] == "チケットB"].index[0]
        rejected = panel._assign_to_rows([48], ticket_b)
        assert rejected is False
        ok("_assign_to_rows 他人チケット拒否 OK")
    except Exception as e:
        ng("_assign_to_rows 他人チケット拒否", e)

    # ── リグレッション: ガント行クリック(assign_ticket)も従来通り ──
    try:
        panel.schedule_table.clearSelection()
        panel.schedule_table.selectRow(44)
        panel.assign_ticket(ticket_a)
        sch_id = daily_sch_idx(state.current_date, user)
        col = DAILY_TIME_COLS[44]
        assert state.df_daily.loc[sch_id, col] == ticket_a
        ok("assign_ticket（ガント行クリック）リグレッションなし OK")
    except Exception as e:
        ng("assign_ticket リグレッション", e)


def test_detail_pane_link(win):
    """共通 DetailPane（右端・main/plan/edit 共有）配置・配線・トグル・md リンク表示"""
    print("\n[23] DetailPane（共有・md リンク表示）テスト")
    from PySide6.QtWidgets import QPushButton
    from ui_main import (DetailPane, IDX_MAIN, IDX_GANTT, IDX_ROADMAP,
                         IDX_TEAM)
    state = win.state
    df = state.df_nodes

    # 共通 DetailPane が MainWindow に存在すること
    try:
        dp = win.detail_pane
        assert isinstance(dp, DetailPane)
        ok("共通 DetailPane が MainWindow に配置されている")
    except Exception as e:
        ng("DetailPane 配置", e)
        return

    # report_edit に md リンクを書くと links_layout にボタンが生成される
    try:
        ticket_a = df[df["title"] == "チケットA"].index[0]
        dp.update_for_node(ticket_a)
        dp.report_edit.blockSignals(True)
        dp.report_edit.setPlainText(
            "[仕様書](https://example.com/spec.pdf) と [議事録](C:/tmp/memo.md)")
        dp.report_edit.blockSignals(False)
        dp._update_links()
        btns = [b for b in dp.links_area.findChildren(QPushButton)
                if b.text().startswith("[")]
        assert len(btns) == 2, f"リンクボタン数={len(btns)}"
        assert btns[0].text() == "[仕様書]"
        assert btns[1].text() == "[議事録]"
        ok("md リンク2件 → links_layout ボタン生成 OK")
    except Exception as e:
        ng("md リンク表示", e)

    # リンクなし本文 → links_layout が空
    try:
        dp.report_edit.blockSignals(True)
        dp.report_edit.clear()
        dp.report_edit.blockSignals(False)
        dp._update_links()
        btns = [b for b in dp.links_area.findChildren(QPushButton)
                if b.text().startswith("[")]
        assert len(btns) == 0, f"ボタン数={len(btns)}"
        ok("本文なし → links_layout 空 OK")
    except Exception as e:
        ng("リンクなし表示", e)

    # main(gantt)・plan(roadmap)・edit の各選択シグナルで DetailPane が更新される
    try:
        ticket_a = df[df["title"] == "チケットA"].index[0]
        win.main_pane.table_pane.node_selected.emit(ticket_a)
        assert win.detail_pane._node_idx == ticket_a, "table 配線"
        win.gantt_view.ticket_clicked.emit(ticket_a)
        assert win.detail_pane._node_idx == ticket_a, "gantt 配線"
        win.road_view.node_selected.emit(ticket_a)
        assert win.detail_pane._node_idx == ticket_a, "roadmap 配線"
        ok("main/plan/edit のノード選択 → DetailPane 更新 OK")
    except Exception as e:
        ng("3画面の選択配線", e)

    # トグルと、タブによる表示制御
    try:
        win._switch_view(IDX_GANTT)
        assert win.detail_pane.isVisible(), "main で表示されるべき"
        win._switch_view(IDX_ROADMAP)
        assert win.detail_pane.isVisible(), "plan で表示されるべき"
        win._switch_view(IDX_TEAM)
        assert not win.detail_pane.isVisible(), "team では非表示であるべき"
        # トグル OFF → main でも非表示
        win._switch_view(IDX_MAIN)
        assert win.detail_pane.isVisible()
        win._on_toggle_detail(False)
        assert not win.detail_pane.isVisible(), "トグルOFFで非表示"
        win._on_toggle_detail(True)
        assert win.detail_pane.isVisible(), "トグルONで再表示"
        ok("トグル・タブ別表示制御 OK")
    except Exception as e:
        ng("トグル・表示制御", e)


def test_detail_pane_report(win):
    """DetailPane の月次 P2 レポート（セクション保存・月ナビ・md リンク・P1 無効化）"""
    print("\n[28] DetailPane 月次 P2 レポートテスト")
    import tempfile
    import logic as LG
    from db import create_initial_node
    state = win.state
    df = state.df_nodes
    dp = win.detail_pane
    user = state.user

    # P1 > P2 > Ticket の階層を作成
    p1 = create_initial_node(user, "project1", "レポートP1", "0", 99)
    state.db.upsert_node(p1)
    p2 = create_initial_node(user, "project2", "レポートP2", p1.name, 99)
    state.db.upsert_node(p2)
    ticket_r = create_initial_node(user, "ticket", "レポートチケット", p2.name, 1)
    state.db.upsert_node(ticket_r)
    state.reload_nodes()
    df = state.df_nodes
    p1_idx = df[df["title"] == "レポートP1"].index[0]
    p2_idx = df[df["title"] == "レポートP2"].index[0]
    tr_idx = df[df["title"] == "レポートチケット"].index[0]

    d = tempfile.mkdtemp()
    orig_dir = state.config.report_output_dir
    state.config.report_output_dir = d
    try:
        # P1 選択 → レポート欄が無効化される
        dp.update_for_node(p1_idx)
        assert dp._node_idx == p1_idx
        assert not dp.report_edit.isEnabled(), "P1 選択でレポート欄が無効でない"
        ok("P1 選択でレポート欄無効化 OK")
    except Exception as e:
        ng("P1 選択レポート無効化", e)

    try:
        # P2 選択 → レポート欄が有効化・空本文
        dp.update_for_node(p2_idx)
        assert dp.report_edit.isEnabled(), "P2 選択でレポート欄が有効でない"
        assert dp.report_edit.toPlainText() == ""
        ok("P2 選択でレポート欄有効化・空本文 OK")
    except Exception as e:
        ng("P2 選択レポート有効化", e)

    try:
        # Ticket 選択 → レポート欄有効化
        dp.update_for_node(tr_idx)
        assert dp.report_edit.isEnabled(), "Ticket 選択でレポート欄が有効でない"
        ok("Ticket 選択でレポート欄有効化 OK")
    except Exception as e:
        ng("Ticket 選択レポート有効化", e)

    try:
        # 実績挿入 → report_edit に週報 md が入る
        dp.rep_mode.setCurrentIndex(0)  # 週報
        dp._on_insert_actuals()
        assert "# 週報" in dp.report_edit.toPlainText(), dp.report_edit.toPlainText()[:40]
        ok("実績挿入 OK（collect_report_data 経路）")
    except Exception as e:
        ng("実績挿入", e)

    try:
        # 保存 → P2 単位の月次ファイルに IDX セクションが書き込まれる
        # ※ H1〜H3 見出しはセクション区切りになるので #### 以下か地の文を使う
        body = "テスト所感: 月次レポートの検証本文です。"
        dp.report_edit.setPlainText(body)
        dp._on_save_report()
        yyyymm = dp._rep_month.strftime("%Y%m")
        p2_path = LG.report_p2_path(d, state.df_nodes, tr_idx, yyyymm)
        assert p2_path and p2_path.exists(), f"P2 ファイルが存在しない: {p2_path}"
        md_text = p2_path.read_text(encoding="utf-8")
        loaded = LG.read_section(md_text, tr_idx)
        assert loaded is not None and "テスト所感" in loaded, \
            f"セクションが見つからない: {md_text[:120]}"
        ok(f"月次 P2 保存・セクション読込 OK: {p2_path.name}")
    except Exception as e:
        ng("月次 P2 保存・セクション読込", e)

    try:
        # 別セクション（P2 自身）も同じファイルに共存できる
        dp.update_for_node(p2_idx)
        dp.report_edit.setPlainText("P2 自身の本文")
        dp._on_save_report()
        yyyymm = dp._rep_month.strftime("%Y%m")
        p2_path = LG.report_p2_path(d, state.df_nodes, p2_idx, yyyymm)
        md_text = p2_path.read_text(encoding="utf-8")
        loaded_p2 = LG.read_section(md_text, p2_idx)
        loaded_tr = LG.read_section(md_text, tr_idx)
        assert loaded_p2 is not None and "P2 自身" in loaded_p2, \
            f"P2 セクション: {loaded_p2!r}"
        assert loaded_tr is not None and "テスト所感" in loaded_tr, \
            f"Ticket セクション: {loaded_tr!r}"
        ok("P2・Ticket セクションが同一ファイルに共存 OK")
    except Exception as e:
        ng("マルチセクション共存", e)

    try:
        # 月ナビ ◀ で前月へ移動 → 前月データなしで空本文
        dp.update_for_node(tr_idx)
        dp.report_edit.setPlainText("今月の確認本文")
        # setPlainText が textChanged を発火するので dirty が立つ
        dp._rep_shift_month(-1)  # 前月へ（dirty なら自動保存後に移動）
        assert dp.report_edit.toPlainText() == "", \
            f"前月データは空のはず: {dp.report_edit.toPlainText()[:40]}"
        ok("月ナビ前月移動 → 空本文 OK")
    except Exception as e:
        ng("月ナビ前月移動", e)

    try:
        # Plan(road_view)選択で対象ノードが切り替わる
        dp.update_for_node(tr_idx)
        task = df[df["title"] == "テストTask"].index[0]
        win.road_view.node_selected.emit(task)
        assert dp._node_idx == task
        ok("Plan 選択でレポート対象切替 OK")
    except Exception as e:
        ng("Plan 選択切替", e)
    finally:
        state.config.report_output_dir = orig_dir


def test_edit_open_no_dirty(win):
    """Edit を開く/選択しただけでは未保存(dirty)にならない"""
    print("\n[24] Edit 表示時の未保存誤検知テスト")
    state = win.state
    df = state.df_nodes
    tp = win.main_pane.table_pane
    task_idx = df[df["title"] == "テストTask"].index[0]

    # 子の priority を非連番にする（以前はこれで update_for_parent が dirty にしていた）
    try:
        children = df[(df["parent_id"] == task_idx) & (df["status"] != "deleted")]
        first_child = children.sort_values("priority").index[0]
        state.df_nodes.loc[first_child, "priority"] = 99  # 連番を崩す
        state.nodes_modified = False
        tp.update_for_parent(task_idx)
        assert state.nodes_modified is False, "表示・選択で dirty になってはいけない"
        ok("update_for_parent では dirty にならない OK")
    except Exception as e:
        ng("update_for_parent 非dirty", e)

    # dirty 機構そのものは生きている（実編集ならフラグが立つ）
    try:
        state.nodes_modified = False
        tp._mark_dirty()
        assert state.nodes_modified is True
        state.nodes_modified = False
        ok("_mark_dirty で dirty になる（機構は健在）OK")
    except Exception as e:
        ng("_mark_dirty 機構", e)


def test_pomodoro_overwrite(win):
    """ポモドーロのスロット上書き（旧チケットの実績減算・新チケット加算）"""
    print("\n[25] ポモドーロ上書きテスト")
    from db import DAILY_TIME_COLS, daily_sch_idx
    state = win.state
    df = state.df_nodes
    user = state.login_user
    sch_idx = daily_sch_idx(datetime.date.today().isoformat(), user)
    # yamada 担当の 2 チケット
    old_t = df[df["title"] == "チケットA"].index[0]
    new_t = df[df["title"] == "進行中チケット"].index[0]
    slot = 70  # 17:30 付近の空きスロット

    try:
        # まず old_t を書く
        win._write_pomodoro_slots(sch_idx, [slot], old_t)
        assert state.df_daily.loc[sch_idx, DAILY_TIME_COLS[slot]] == old_t
        old_h_after_write = float(state.df_nodes.loc[old_t, "actual_hours"] or 0)
        new_h_before = float(state.df_nodes.loc[new_t, "actual_hours"] or 0)
        # new_t で上書き
        win._write_pomodoro_slots(sch_idx, [slot], new_t)
        assert state.df_daily.loc[sch_idx, DAILY_TIME_COLS[slot]] == new_t, "上書きされる"
        # old_t は -0.25、new_t は +0.25
        assert abs(float(state.df_nodes.loc[old_t, "actual_hours"] or 0)
                   - (old_h_after_write - 0.25)) < 1e-9, "旧チケット実績が減算される"
        assert abs(float(state.df_nodes.loc[new_t, "actual_hours"] or 0)
                   - (new_h_before + 0.25)) < 1e-9, "新チケット実績が加算される"
        ok("上書き: 旧-0.25 / 新+0.25 / スロット置換 OK")
    except Exception as e:
        ng("ポモドーロ上書き", e)

    # 後始末: スロットを解放
    try:
        win._write_pomodoro_slots(sch_idx, [slot], new_t)  # 冪等確認（同一なら変化なし）
        state.df_daily.loc[sch_idx, DAILY_TIME_COLS[slot]] = ""
        ok("後始末 OK")
    except Exception as e:
        ng("後始末", e)


def test_personal_review_member(win):
    """個人振り返りが選択中メンバーで集計される"""
    print("\n[26] 個人振り返り メンバー指定テスト")
    state = win.state
    anal = win.anal_view
    orig = state.current_member
    try:
        state.current_member = "tanaka@email.com"
        anal._calc_personal()
        title = anal._fig.axes[0].get_title()
        assert state.display_name("tanaka@email.com") in title, title
        ok(f"選択メンバーで集計 OK（{title}）")
    except Exception as e:
        ng("個人振り返り メンバー指定", e)
    finally:
        state.current_member = orig


def test_team_log_export(win):
    """チームログの期間Markdown出力（純ロジック + UI保存）"""
    print("\n[27] チームログ出力テスト")
    import tempfile
    import logic as LG
    from db import daily_sch_idx
    import pandas as pd
    state = win.state

    today = datetime.date.today().isoformat()
    members = ["yamada@email.com", "tanaka@email.com"]
    name_map = {m: state.display_name(m) for m in members}

    # 純ロジック: build_team_log_markdown
    try:
        log_idx = daily_sch_idx(today, "yamada@email.com")
        df_log = pd.DataFrame(
            [{"Owner": "yamada@email.com", "health_status": "良好",
              "work_place": "在宅", "safety": "宣言", "overwork": "なし",
              "notes": "作業中", "Last_Update": today}],
            index=[log_idx])
        all_perm = {"yamada@email.com": "常時メモX"}
        md = LG.build_team_log_markdown(
            state.df_daily, df_log, all_perm, members, name_map, today, today)
        assert "| 日付 | メンバー |" in md, md[:200]
        assert "良好" in md and "在宅" in md and "作業中" in md
        assert "## 常時メモ" in md and "常時メモX" in md
        ok("build_team_log_markdown 表組み OK")
    except Exception as e:
        ng("build_team_log_markdown", e)

    # 期間外は除外される
    try:
        past = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        md2 = LG.build_team_log_markdown(
            state.df_daily, df_log, {}, members, name_map, past, past)
        assert "良好" not in md2, "期間外データが含まれてはいけない"
        ok("期間外除外 OK")
    except Exception as e:
        ng("期間外除外", e)

    # UI 保存: output_dir に team_*.md が出力される
    try:
        d = tempfile.mkdtemp()
        orig_dir = state.config.report_output_dir
        state.config.report_output_dir = d
        win.team_view.f_from.set_date(today)
        win.team_view.f_to.set_date(today)
        win.team_view._on_export_team()
        team_dir = Path(d) / "team"
        files = list(team_dir.glob("team_*.md")) if team_dir.exists() else []
        assert len(files) == 1, f"files={files}"
        ok(f"output_dir へ出力 OK（{files[0].name}）")
    except Exception as e:
        ng("チームログ UI 出力", e)
    finally:
        state.config.report_output_dir = orig_dir


def test_save_load(state):
    """save() → load() のラウンドトリップ確認"""
    print("\n[8] save / load ラウンドトリップテスト")
    try:
        before = len(state.df_nodes)
        state.save()
        ok(f"save() 完了 (ノード数={before})")
    except Exception as e:
        ng("save()", e)

    try:
        state.load()
        after = len(state.df_nodes)
        ok(f"load() 完了 (ノード数={after})")
    except Exception as e:
        ng("load()", e)


# -------------------------------------------------------
# メイン
# -------------------------------------------------------
def main():
    print("=" * 55)
    print("  ヘッドレス GUI テスト (QT_QPA_PLATFORM=offscreen)")
    print("=" * 55)

    with tempfile.TemporaryDirectory() as tmpdir:
        state, version = make_state(tmpdir)
        pj_idx, task_idx, ticket_idx, ticket2_idx = make_test_data(state)

        test_state_properties(state)

        win = test_main_window(state, version)
        if win is None:
            print("\nMainWindow の生成に失敗したため残りのテストをスキップ")
        else:
            test_tree_pane(win, pj_idx)
            test_table_pane(win, pj_idx, task_idx)
            test_detail_pane(win, task_idx, ticket_idx)
            test_edit_delete(win, task_idx, ticket_idx)
            test_gantt_view(win)
            test_actual_hours_propagation(win, task_idx, ticket_idx)
            test_search_view(win, task_idx, ticket_idx)
            test_link_field(state, ticket_idx)
            test_report_logic(state)
            test_template_parse(state)
            test_daily_log_markdown(state)
            test_daily_export(win)
            test_dashboard(win, ticket_idx)
            test_pomodoro(win, ticket_idx)
            test_personal_review(state, win)
            test_slot_context_menu(win)
            test_detail_pane_link(win)
            test_detail_pane_report(win)
            test_edit_open_no_dirty(win)
            test_pomodoro_overwrite(win)
            test_personal_review_member(win)
            test_team_log_export(win)
            test_save_load(state)

    print("\n" + "=" * 55)
    print(f"  結果: OK={PASS}  NG={FAIL}  合計={PASS+FAIL}")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
