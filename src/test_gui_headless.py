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
        search_view.f_from.set_date(today)
        search_view.f_to.set_date(today)
        search_view._on_search()
        rows = search_view.result_table.rowCount()
        for r in range(rows):
            item = search_view.result_table.item(r, 11)
            val = item.text() if item else "-"
            # "-" でなく数値文字列であることを確認
            assert val != "-", f"行{r}: 期間実績が '-' のまま"
        ok(f"日付範囲指定時 期間実績(h)列 に数値表示（{rows}行）")
        # フィールドをリセット
        search_view.f_from.set_date("")
        search_view.f_to.set_date("")
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
    """当日ログ出力（Main タブ＝GanttView へ移設）のテスト"""
    print("\n[18] デイリーログ出力 UI テスト")
    try:
        mv = win.gantt_view
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
        # 既定では詳細ペインは閉じている（detail_pane_open=False）
        win._on_toggle_detail(False)
        win._switch_view(IDX_GANTT)
        assert not win.detail_pane.isVisible(), "既定（トグルOFF）では非表示であるべき"
        # トグル ON → main/plan で表示、team では非表示
        win._on_toggle_detail(True)
        win._switch_view(IDX_GANTT)
        assert win.detail_pane.isVisible(), "トグルONの main で表示されるべき"
        win._switch_view(IDX_ROADMAP)
        assert win.detail_pane.isVisible(), "トグルONの plan で表示されるべき"
        win._switch_view(IDX_TEAM)
        assert not win.detail_pane.isVisible(), "team では非表示であるべき"
        # main でトグル OFF → 非表示、ON → 再表示
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


def test_assignment_view_multi_rows(win, ticket_idx):
    """Request タブ（AssignmentView）に複数の依頼があるとき、
    ソート常時有効テーブルへの直接挿入で行がズレて空欄になる回帰を防ぐ"""
    print("\n[29] AssignmentView 複数依頼テスト")
    state = win.state
    orig_member = state.current_member
    try:
        to_user = "yamada@email.com"
        messages = ["message-0", "message-1", "message-2"]
        for i, msg in enumerate(messages):
            state.db.create_assignment(
                ticket_idx, f"sender{i}@email.com", to_user, msg)
        state.reload_daily()
        state.current_member = to_user
        win.assign_view.refresh()

        table = win.assign_view.recv_table
        assert table.rowCount() == len(messages), \
            f"rowCount={table.rowCount()}"
        msg_col = 7  # RECV_COLS: [...,"依頼者","メッセージ","状態","日時"]
        seen = set()
        for r in range(table.rowCount()):
            item = table.item(r, msg_col)
            text = item.text() if item else ""
            assert text, f"行{r}のメッセージ列が空白（行ズレのバグ再発の疑い）"
            seen.add(text)
        assert seen == set(messages), f"seen={seen}"
        ok(f"複数依頼（{len(messages)}件）が行ズレなく表示される OK")
    except Exception as e:
        ng("AssignmentView 複数依頼", e)
    finally:
        state.current_member = orig_member


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
def test_undo_redo(win, task_idx, ticket_idx):
    """B6: Ctrl+Z（元に戻す）/ Ctrl+Y（やり直し）"""
    print("\n[B6] 元に戻す / やり直しテスト")
    from PySide6.QtCore import Qt
    import db as DB
    state = win.state
    tp = win.main_pane.table_pane

    def row_of(idx):
        for r in range(tp.table.rowCount()):
            it = tp.table.item(r, 0)
            if it and it.data(Qt.ItemDataRole.UserRole) == idx:
                return r
        return -1

    try:
        # 起点: 保存直後（履歴なし・未保存なし）
        win._on_save()
        assert not win._undo.can_undo(), "保存直後に戻せる履歴がある"
        tp.update_for_parent(task_idx)
        orig = str(state.df_nodes.loc[ticket_idx, "title"])
        tp.table.item(row_of(ticket_idx), 0).setText("Undo確認用タイトル")
        assert state.df_nodes.loc[ticket_idx, "title"] == "Undo確認用タイトル"
        win._on_undo()
        assert state.df_nodes.loc[ticket_idx, "title"] == orig, "タイトルが戻らない"
        assert not state.nodes_modified, "起点まで戻ったのに未保存扱い"
        win._on_redo()
        assert state.df_nodes.loc[ticket_idx, "title"] == "Undo確認用タイトル", "やり直せない"
        assert state.nodes_modified
        win._on_undo()
        ok("表の編集 → Ctrl+Z で戻り、Ctrl+Y でやり直せる（起点では未保存フラグも戻る）")
    except Exception as e:
        ng("表の編集の Undo/Redo", e)

    try:
        # 1 操作内の複数変更（連番化 + 追加 + 自動チケット）は 1 回で戻る
        n_before = len(state.df_nodes)
        tp.update_for_parent(task_idx)
        tp._add_from_blank_row("Undo一括確認")
        assert len(state.df_nodes) == n_before + 1
        win._on_undo()
        assert len(state.df_nodes) == n_before, "1 回の Undo で追加が消えない"
        ok("1 操作内の複数変更は 1 回の Ctrl+Z で戻る")
    except Exception as e:
        ng("1 操作単位の Undo", e)

    try:
        # 日次スケジュールの割り当ても戻る（実績工数も連動）
        state.current_member = state.user
        panel = win.schedule_panel
        act_before = float(state.df_nodes.loc[ticket_idx, "actual_hours"] or 0)
        panel._update_schedule_slots([40, 41], ticket_idx)
        assert float(state.df_nodes.loc[ticket_idx, "actual_hours"]) == act_before + 0.5
        win._on_undo()
        sch_idx = DB.daily_sch_idx(state.current_date, state.user)
        slot_empty = (sch_idx not in state.df_daily.index
                      or not state.df_daily.loc[sch_idx, "C1000"])
        assert slot_empty, "スロット割り当てが戻らない"
        assert float(state.df_nodes.loc[ticket_idx, "actual_hours"] or 0) == act_before
        ok("スケジュール割り当ての Ctrl+Z で実績工数も元に戻る")
    except Exception as e:
        ng("スケジュールの Undo", e)

    try:
        # 保存すると起点が更新され、それより前には戻れない
        tp.update_for_parent(task_idx)
        tp.table.item(row_of(ticket_idx), 1).setText("7")
        win._on_save()
        win._on_undo()
        assert int(state.df_nodes.loc[ticket_idx, "priority"]) == 7, "保存前の状態に戻ってしまった"
        assert not state.nodes_modified
        ok("保存後は保存前の状態へ戻らない（保存時点が起点）")
    except Exception as e:
        ng("保存時の Undo 起点", e)


def test_quick_add_parse():
    """B2: クイック追加の解析ルール（06_B2 仕様書の表）"""
    print("\n[B2] クイック追加の解析テスト")
    import logic as LG
    T = datetime.date(2026, 9, 23)  # 水曜日
    D = lambda m, d, y=2026: datetime.date(y, m, d)
    cases = [
        ("水の入れ替え　1.５　明日", dict(title="水の入れ替え", hours=1.5, deadline=D(9, 24))),
        ("a ２ｈ", dict(hours=2.0)), ("a 2.5", dict(hours=2.5)), ("a 30分", dict(hours=0.5)),
        ("a 1時間半", dict(hours=1.5)), ("a 20分", dict(hours=0.5, hours_rounded=True)),
        ("Phase 3", dict(title="Phase 3", hours=None)),
        ("a 水", dict(deadline=D(9, 23))), ("a 来週水", dict(deadline=D(9, 30))),
        ("a 今週中", dict(deadline=D(9, 25))), ("a 月末", dict(deadline=D(9, 30))),
        ("a 5日", dict(deadline=D(10, 5))), ("a ９／３０", dict(deadline=D(9, 30))),
        ("a 1/10", dict(deadline=D(1, 10, 2027))), ("a 9/10", dict(deadline=D(9, 10))),
        ("a 9/28〜10/2", dict(start=D(9, 28), deadline=D(10, 2))),
        ("レビュー2h", dict(title="レビュー2h", hours=None)),
        ("「水」 交換", dict(title="水 交換", deadline=None)),
        ("見積 2h 金曜 @設計書", dict(title="見積", task_query="設計書", deadline=D(9, 25))),
        ("x 明日 #明日までに鍵", dict(title="x", memo="明日までに鍵", deadline=D(9, 24))),
        ("Issue#123 対応", dict(title="Issue#123 対応", memo="")),
    ]
    bad = []
    for text, exp in cases:
        r = LG.parse_quick_add(text, today=T)
        diff = {k: (r[k], v) for k, v in exp.items() if r[k] != v}
        if diff:
            bad.append((text, diff))
    if bad:
        ng(f"解析ルール {len(cases) - len(bad)}/{len(cases)}", Exception(str(bad)))
    else:
        ok(f"解析ルール {len(cases)} 例がすべて仕様どおり")
    try:
        r = LG.parse_quick_add("a 3", today=T)
        assert r["hints"] and r["title"] == "a 3"
        r = LG.parse_quick_add("a 9/10", today=T)
        assert any("過去日" in w for w in r["warnings"])
        ok("整数のみはヒント表示、過去日は警告")
    except Exception as e:
        ng("ヒント・警告", e)


def test_quick_add_inbox(win, task_idx, tmpdir):
    """B2: クイック追加・Inbox・振り分け・AI 取込"""
    print("\n[B2] クイック追加 / Inbox テスト")
    import logic as LG
    import db as DB
    import ui_main
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QDialog, QMessageBox
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmpdir)
    state = win.state
    cfg = state.config
    cfg.inbox_max_items, cfg.inbox_stale_days = 10, 3
    task_title = str(state.df_nodes.loc[task_idx, "title"])

    try:
        dlg = ui_main.QuickAddDialog(state)
        dlg.edit.setText("Inbox確認 1.5 明日 #給湯室の件")
        dlg._on_create()
        r = dlg.result_data
        assert r and r["parent"] == DB.INBOX_PARENT and r["hours"] == 1.5 \
            and r["memo"] == "給湯室の件", r
        ds = win._create_quick_ticket(r)
        assert state.df_nodes.loc[ds.name, "parent_id"] == DB.INBOX_PARENT
        inbox_idx = ds.name
        ok("@ なしは Inbox に作成（工数・メモも反映）")
    except Exception as e:
        ng("Inbox への作成", e)
        return

    try:
        dlg = ui_main.QuickAddDialog(state)
        dlg.edit.setText(f"Task指定確認 @{task_title}")
        dlg._on_create()
        assert dlg.result_data and dlg.result_data["parent"] == task_idx, dlg.result_data
        dlg = ui_main.QuickAddDialog(state)
        dlg.edit.setText("該当なし確認 @存在しないTask名xyz")
        dlg._on_create()
        assert dlg.result_data is None
        ok("@Task は候補から確定、該当なしは作成しない")
    except Exception as e:
        ng("@Task 指定", e)

    try:
        # 朝のスロット連携: 4 スロット選択 → 工数 1.0h・割り当て
        state.current_member = state.user
        orig_exec = ui_main.QuickAddDialog.exec

        def fake_exec(self):
            self.edit.setText(f"朝の割当確認 @{task_title}")
            self._on_create()
            return QDialog.DialogCode.Accepted if self.result_data else QDialog.DialogCode.Rejected
        ui_main.QuickAddDialog.exec = fake_exec
        try:
            win._on_quick_add(rows=[36, 37, 38, 39])
        finally:
            ui_main.QuickAddDialog.exec = orig_exec
        new = state.df_nodes[state.df_nodes["title"] == "朝の割当確認"]
        assert len(new) == 1
        n_idx = new.index[0]
        assert float(new.loc[n_idx, "estimated_hours"]) == 1.0
        sch_idx = DB.daily_sch_idx(state.current_date, state.user)
        assert state.df_daily.loc[sch_idx, "C0900"] == n_idx
        assert state.df_daily.loc[sch_idx, "C0945"] == n_idx
        ok("スロット選択中の作成: 工数=スロット時間、同時に割り当て")
    except Exception as e:
        ng("スロット連携", e)

    try:
        assert LG.status_change_error(state.df_nodes, inbox_idx, "done")
        assert LG.status_change_error(state.df_nodes, inbox_idx, "regularly")
        assert LG.status_change_error(state.df_nodes, inbox_idx, "cancel") is None
        ok("Inbox チケットは done / regularly 不可（cancel は可）")
    except Exception as e:
        ng("Inbox のステータス制約", e)

    try:
        cnt = LG.inbox_summary(state.df_nodes, state.user, 99, 3)["count"]
        cfg.inbox_max_items = cnt
        dlg = ui_main.QuickAddDialog(state)
        dlg.edit.setText("上限確認")
        dlg._on_create()
        assert dlg.result_data is None, "上限なのに Inbox へ作成できた"
        dlg.edit.setText(f"上限確認 @{task_title}")
        dlg._on_create()
        assert dlg.result_data is not None, "@ 指定でも作成できない"
        cfg.inbox_max_items = 10
        ok("Inbox 上限到達後は @Task 指定が必須")
    except Exception as e:
        cfg.inbox_max_items = 10
        ng("Inbox 上限", e)

    try:
        tp = win.main_pane.tree_pane
        tp.refresh()
        item = tp._find_item(tp.tree.invisibleRootItem(), DB.INBOX_PARENT)
        assert item is not None and item.childCount() >= 1 and "Inbox" in item.text(0)
        ok("Edit ツリーに 📥 Inbox とチケットが表示される")
    except Exception as e:
        ng("Inbox のツリー表示", e)

    try:
        # AI 取込: idx 指定 → 移動（新規作成しない）、同名 → 移動、不明 IDX → スキップ
        extra = win._create_quick_ticket({"title": "AI同名確認", "hours": 0, "deadline": None,
                                          "start": None, "memo": "", "parent": DB.INBOX_PARENT})
        view = win.ai_import_view
        prompt = view._build_task_list()
        assert "【Task未設定Ticket" in prompt and inbox_idx in prompt
        text = (f"idx: {inbox_idx}\nparent_idx: {task_idx}\n---\n"
                f"title: AI同名確認\nparent_idx: {task_idx}\n---\n"
                f"idx: 999999_99zzzzzz\nparent_idx: {task_idx}\n---\n"
                f"title: AI新規確認\nparent_idx: {task_idx}\n")
        items, errors, skipped = view._parse_llm_response(text)
        kinds = [(it["kind"], it.get("idx")) for it in items]
        assert not errors and len(skipped) == 1, (errors, skipped)
        assert kinds[0] == ("move", inbox_idx) and kinds[1] == ("move", extra.name) \
            and kinds[2][0] == "new", kinds
        n_before = len(state.df_nodes)
        view.text_edit.setPlainText(text)
        orig_q = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            view._on_import_tickets()
        finally:
            QMessageBox.question = orig_q
        df = state.df_nodes
        assert df.loc[inbox_idx, "parent_id"] == task_idx
        assert df.loc[extra.name, "parent_id"] == task_idx
        assert (df["title"] == "AI同名確認").sum() == 1, "同名チケットが二重作成された"
        assert len(df) == n_before + 1, "新規は 1 件のみのはず"
        ok("AI 取込: Inbox は移動（二重作成なし）、不明 IDX はスキップ、新規は作成")
    except Exception as e:
        ng("AI 取込の Inbox 振り分け", e)

    try:
        # 振り分け画面: 移動先を選んで移動
        t = win._create_quick_ticket({"title": "振り分け画面確認", "hours": 0, "deadline": None,
                                      "start": None, "memo": "", "parent": DB.INBOX_PARENT})
        dlg = ui_main.InboxTriageDialog(state)
        row = next(r for r in range(dlg.table.rowCount()) if dlg._row_idx(r) == t.name)
        combo = dlg.table.cellWidget(row, 5)
        combo.setCurrentIndex(combo.findData(task_idx))
        dlg._on_move()
        assert state.df_nodes.loc[t.name, "parent_id"] == task_idx
        ok("振り分け画面で Task へ移動できる")
    except Exception as e:
        ng("振り分け画面", e)

    try:
        # 起動時表示: 上限/滞留時のみ・1 日 1 回
        calls = []
        orig_open = win._open_inbox_triage
        win._open_inbox_triage = lambda: calls.append(1)
        try:
            win._create_quick_ticket({"title": "起動時確認", "hours": 0, "deadline": None,
                                      "start": None, "memo": "", "parent": DB.INBOX_PARENT})
            cfg.inbox_max_items = 99
            win.maybe_prompt_inbox()
            assert calls == [], "条件外なのに表示された"
            cfg.inbox_max_items = 1
            win.maybe_prompt_inbox()
            win.maybe_prompt_inbox()
            assert calls == [1], f"表示回数 {len(calls)}"
        finally:
            win._open_inbox_triage = orig_open
            cfg.inbox_max_items = 10
        ok("起動時の振り分け表示は上限/滞留時のみ・1 日 1 回")
    except Exception as e:
        ng("起動時の振り分け表示", e)
    state.nodes_modified = False
    state.schedule_modified = False


def test_ui_state(state, version, win, tmpdir):
    """D3: 画面状態（ウィンドウ・分割・フィルタ・ツリー開閉・前回タブ）の記憶"""
    print("\n[D3] 画面状態の記憶テスト")
    from PySide6.QtCore import QSettings, Qt
    from ui_main import MainWindow, IDX_ROADMAP
    # 実ユーザーの ui_state.ini を汚さないよう保存先を一時フォルダへ
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmpdir)
    try:
        tp = win.main_pane.tree_pane
        tp.refresh()
        root = tp._find_item(tp.tree.invisibleRootItem(), "0")  # P0（先頭は Inbox）
        first = root.child(0)                          # 先頭の P1
        first_idx = first.data(0, Qt.ItemDataRole.UserRole)
        first.setExpanded(False)
        tp.refresh()
        again = tp._find_item(tp.tree.invisibleRootItem(), first_idx)
        assert again is not None and not again.isExpanded(), "再描画で全展開に戻った"
        ok("Edit ツリーで閉じたノードは再描画後も閉じたまま")
    except Exception as e:
        ng("ツリー開閉の保持", e)
        return

    try:
        state.config.start_tab = "last"
        win.gantt_view._status_radios["all"].setChecked(True)
        win.road_view.apply_saved_view("Task", "月", True, 15)
        tp.filter_btn.setChecked(False)
        win.detail_toggle_btn.setChecked(True)
        win._switch_view(IDX_ROADMAP)
        win.main_pane.splitter.setSizes([400, 800])
        edit_sizes = win.main_pane.splitter.sizes()
        # ガントのタイトル列幅: 再構築しても保たれ、次回起動時にも戻る
        gv = win.gantt_view
        gv.table.setColumnWidth(1, 257)
        gv._rebuild_table()
        assert gv.table.columnWidth(1) == 257, f"再構築でタイトル列幅が戻った {gv.table.columnWidth(1)}"
        ok("ガントのタイトル列幅が再構築後も保たれる")
        rv0 = win.road_view
        rv0.table.setColumnWidth(0, 263)
        rv0._rebuild_table()
        assert rv0.table.columnWidth(0) == 263, f"再構築でタイトル列幅が戻った {rv0.table.columnWidth(0)}"
        ok("Plan のタイトル列幅が再構築後も保たれる")
        state.nodes_modified = False
        state.schedule_modified = False
        win.save_ui_state()

        win2 = MainWindow(state, version)
        win2.resize(1500, 900)
        win2.restore_ui_state()
        win2.show()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert win2.stack.currentIndex() == IDX_ROADMAP, "前回のタブで開かない"
        assert win2.detail_toggle_btn.isChecked(), "詳細ペインの開閉が戻らない"
        assert win2.gantt_view._get_status_filter() == "all"
        win2.gantt_view._rebuild_table()
        assert win2.gantt_view.table.columnWidth(1) == 257, "ガントの列幅が次回起動時に戻らない"
        win2.road_view._rebuild_table()
        assert win2.road_view.table.columnWidth(0) == 263, "Plan の列幅が次回起動時に戻らない"
        rv = win2.road_view
        assert (rv._current_level, rv._cell_unit, rv._filter_own, rv._date_col_extra) \
            == ("Task", "月", True, 15), "Plan の表示設定が戻らない"
        tp2 = win2.main_pane.tree_pane
        assert not tp2._filter_own, "Edit の『選択中メンバーのみ』が戻らない"
        assert first_idx in tp2._collapsed, "ツリーの開閉が戻らない"
        # 分割位置は Edit タブを表示してから比率で比較（ウィンドウ幅が異なるため）
        from ui_main import IDX_MAIN
        win2._switch_view(IDX_MAIN)
        QApplication.processEvents()
        s2 = win2.main_pane.splitter.sizes()
        r1, r2 = edit_sizes[0] / sum(edit_sizes), s2[0] / sum(s2)
        assert abs(r1 - r2) < 0.02, f"分割位置が戻らない {s2} vs {edit_sizes}"
        ok("終了時の画面状態が次回起動時に復元される（start_tab=last）")
        state.config.start_tab = "today"
        win3 = MainWindow(state, version)
        win3.restore_ui_state()
        from ui_main import IDX_TODAY
        assert win3.stack.currentIndex() == IDX_TODAY, "start_tab=today なのに前回タブで開いた"
        ok("start_tab が last 以外なら config のタブで起動する")
        for w in (win2, win3):
            w.hide()
            w.deleteLater()
    except Exception as e:
        ng("画面状態の保存・復元", e)


def test_requests_0925(win):
    """2026-09-25 要望: Config の start_tab 選択式・検索の日付カレンダー"""
    print("\n[要望0925] Config / 検索の日付テスト")
    from PySide6.QtWidgets import QComboBox
    try:
        cv = win.config_view
        w = cv._fields["gui_start_tab"]
        assert isinstance(w, QComboBox), type(w)
        assert [w.itemData(i) for i in range(w.count())] == ["today", "main", "plan", "edit", "last"]
        w.setCurrentIndex(w.findData("last"))
        assert cv._get("gui_start_tab") == "last"
        cv.refresh()   # config の値（today）に戻る
        assert cv._get("gui_start_tab") == win.state.config.start_tab
        ok("Config の start_tab が選択式（保存値は today/main/plan/edit/last）")
    except Exception as e:
        ng("start_tab 選択式", e)
    try:
        sv = win.search_view
        from ui_widgets import DateButton
        assert isinstance(sv.f_from, DateButton) and isinstance(sv.f_to, DateButton)
        sv.f_from.set_date("")
        sv._shift_date(sv.f_from, +1)
        assert sv.f_from.get_date() == (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        sv.f_from.clear_date()
        assert sv.f_from.get_date() == ""
        ok("検索の期間はカレンダーボタン（◀▶ で 1 日移動・✕ で未設定）")
    except Exception as e:
        ng("検索の日付カレンダー", e)


def test_analysis_0925(win, pj_idx, task_idx, ticket_idx):
    """2026-09-25 要望: Analyze（期間・人物・チェック式ツリー・自動再集計・ドリルダウン）"""
    print("\n[要望0925] Analyze テスト")
    import logic as LG
    anal = win.anal_view
    state = win.state
    try:
        T = datetime.date(2026, 9, 25)  # 金曜
        P = lambda n: LG.analysis_period(n, T)
        assert P("全期間") == ("", "")
        assert P("今週") == ("2026-09-21", "2026-09-27")
        assert P("先週") == ("2026-09-14", "2026-09-20")
        assert P("今月") == ("2026-09-01", "2026-09-30")
        assert P("先月") == ("2026-08-01", "2026-08-31")
        assert P("今年度") == ("2026-04-01", "2027-03-31")
        assert LG.analysis_period("今年度", datetime.date(2027, 2, 1)) == ("2026-04-01", "2027-03-31")
        ok("期間プリセット（週は月曜始まり・年度は 4 月始まり）")
    except Exception as e:
        ng("期間プリセット", e)
    try:
        anal.refresh()
        anal._select_me_only()
        assert anal._selected_users() == {state.user}
        assert anal._recalc_timer.isActive(), "条件変更で再集計が予約されない"
        anal._select_all_users()
        assert anal._selected_users() is None
        ok("人物: 自分だけ／全員 ボタン・変更で自動再集計")
    except Exception as e:
        ng("人物の選択", e)
    try:
        # 今日のスロットに自分のチケットを 1 時間割り当て → 期間「今日」の実績は 1.0h
        panel = win.schedule_panel
        state.current_date = datetime.date.today().isoformat()
        panel.refresh()
        panel._assign_to_rows([48, 49, 50, 51], ticket_idx)
        today = datetime.date.today().isoformat()
        anal._level_btns["ticket"].setChecked(True)
        anal._on_period_preset("全期間")
        anal.p_from.set_date(today); anal.p_to.set_date(today)
        agg = anal.aggregate()
        expect = LG.calc_period_hours(state.df_daily, [ticket_idx], today, today)[ticket_idx]
        assert ticket_idx in agg and abs(agg[ticket_idx]["actual"] - expect) < 1e-9, agg
        est = float(state.df_nodes.loc[ticket_idx, "estimated_hours"] or 0)
        assert agg[ticket_idx]["est"] == est, "期間指定時の見積はチケットの見積全体"
        assert all(v["actual"] > 0 for v in agg.values()), "期間内に作業の無いチケットが混ざる"
        ok(f"期間指定: 実績は期間内の分（{expect}h）・見積は全体")
        anal._on_period_custom()
        assert not any(b.isChecked() for b in anal._period_btns.values()), "任意期間なのにプリセットが選択中"
        anal._on_period_preset("全期間")
        assert anal._period() == ("", "")
        ok("カレンダーで任意期間にするとプリセットの選択が外れる")
    except Exception as e:
        ng("期間指定の集計", e)
    try:
        anal._level_btns["project1"].setChecked(True)
        anal._set_checked_ids({task_idx})
        assert task_idx in anal._checked_ids()
        agg = anal.aggregate()
        assert list(agg) == [pj_idx], agg
        anal._clear_checks()
        assert anal._checked_ids() == set()
        anal._tree_search.setText("存在しない名前")
        vis = [it for it in anal._iter_tree_items() if not it.isHidden()]
        assert not vis, "絞り込みで一致しない行が表示されている"
        anal._tree_search.setText("")
        ok("表示アイテム: チェックで対象を絞る・全解除・名前で絞り込み")
    except Exception as e:
        ng("表示アイテムの選択", e)
    try:
        anal._level_btns["project1"].setChecked(True)
        anal._calc()
        anal.drill_down(pj_idx)
        assert anal._current_level_type() == "task", anal._current_level_type()  # P2〜P4 を使わない PJ
        assert anal._checked_ids() >= {pj_idx} and anal._back_btn.isEnabled()
        anal._drill_back()
        assert anal._current_level_type() == "project1" and not anal._checked_ids()
        assert not anal._back_btn.isEnabled()
        ok("棒クリックのドリルダウン（実在する次の階層へ）と戻る")
    except Exception as e:
        ng("ドリルダウン", e)
    try:
        anal._show_est_cb.setChecked(False)
        anal._calc()
        assert len(anal._ax.containers) == 1, "見積を隠しても棒が 2 系列ある"
        anal._show_est_cb.setChecked(True)
        anal._calc()
        assert len(anal._ax.containers) == 2
        ok("見積の表示／非表示の切り替え")
    except Exception as e:
        ng("見積の表示切替", e)


def test_e1_estimate_assist(win, task_idx):
    """E1: 見積アシスト（自分の完了チケットのうち似たものの実績）"""
    print("\n[E1] 見積アシストテスト")
    import logic as LG
    import ui_main as M
    from db import create_initial_node
    state = win.state
    me = state.user
    added = []
    try:
        for title, est, act, owner in [("議事録作成 A社", 1.0, 1.5, me), ("議事録作成 B社", 1.0, 2.0, me),
                                       ("議事録作成 C社", 1.0, 9.0, "tanaka@email.com")]:
            n = create_initial_node(owner, "ticket", title, task_idx, 50)
            n["status"] = "done"; n["estimated_hours"] = est; n["actual_hours"] = act
            state.df_nodes.loc[n.name] = n
            added.append(n.name)
        a = LG.similar_ticket_hours(state.df_nodes, "議事録作成 D社", me)
        assert a and len(a["items"]) == 2, a            # 他人（tanaka）の分は含めない
        assert a["avg_actual"] == 1.75 and a["suggest"] == 1.75 and a["avg_est"] == 1.0, a
        assert LG.similar_ticket_hours(state.df_nodes, "まったく別の件", me) is None
        ok("自分の完了チケットだけから似た仕事の実績を平均（提案 1.75h）")
    except Exception as e:
        ng("similar_ticket_hours", e)
    try:
        d = M.QuickAddDialog(state, parent=win)
        d.edit.setText("議事録作成 D社 #メモ")
        assert d.assist_btn.isVisible() or not d.isVisible()  # 非表示ダイアログでも状態は持つ
        assert d._assist and d.assist_btn.isEnabled()
        d._apply_assist()
        assert d.edit.text() == "議事録作成 D社 1.75h #メモ", d.edit.text()
        assert d._parsed["hours"] == 1.75 and d._parsed["memo"] == "メモ"
        assert not d.assist_btn.isEnabled(), "工数を書いた後も上書きできる"
        d.close()
        ok("クイック追加: 提案の工数をメモの手前に書き足せる")
    except Exception as e:
        ng("クイック追加の見積アシスト", e)
    try:
        d = M._NodeEditDialog(task_idx, "ticket", state, parent=win)
        d.f_title.setText("議事録作成 E社")
        assert d._assist is not None and "1.75h を使う" in d.assist_use.text()
        d.assist_use.click()
        assert d.f_est.value() == 1.75
        d.f_title.setText("関係ない名前")
        assert d._assist is None
        d.close()
        d2 = M._NodeEditDialog("0", "project1", state, parent=win)
        d2.f_title.setText("議事録作成")
        assert d2._assist is None, "チケット以外にも表示された"
        d2.close()
        ok("新規作成ダイアログ: 似た仕事の実績を表示し「使う」で見積に反映（チケットのみ）")
    except Exception as e:
        ng("編集ダイアログの見積アシスト", e)
    finally:
        state.df_nodes.drop(index=[i for i in added if i in state.df_nodes.index], inplace=True)


def test_f1_work_log(win, ticket_idx, ticket2_idx):
    """F1: 作業ログ（メモ欄へ時刻つきで追記）"""
    print("\n[F1] 作業ログテスト")
    import logic as LG
    from PySide6.QtWidgets import QApplication
    import ui_main as M
    state = win.state
    try:
        now = datetime.datetime(2026, 9, 27, 10, 5)
        assert LG.append_work_log("", "先方回答待ち", now) == "[09/27 10:05] 先方回答待ち"
        assert LG.append_work_log("既存メモ\n", " 2 行\nにまたがる ", now) == "既存メモ\n[09/27 10:05] 2 行 にまたがる"
        assert LG.append_work_log("既存", "   ", now) == "既存"
        assert LG.WORK_LOG_RE.match("[09/27 10:05] 先方回答待ち")
        ok("append_work_log: 末尾に [MM/DD HH:MM] 本文 を 1 行追記")
    except Exception as e:
        ng("append_work_log", e)
    orig_memo = state.df_nodes.loc[ticket_idx, "memo"]
    try:
        win._switch_view(M.IDX_TODAY)
        state.nodes_modified = False
        win._on_worklog_requested(ticket_idx)
        dp = win.detail_pane
        assert win.stack.currentIndex() == M.IDX_GANTT and win.detail_toggle_btn.isChecked()
        assert dp._node_idx == ticket_idx and dp.log_edit is not None
        # Edit で親 Task を選び表に子チケットが並んだ状態でも、追記後に詳細ペインが切り替わらないこと
        tp = win.main_pane.tree_pane
        parent_task = str(state.df_nodes.loc[ticket_idx, "parent_id"])
        tp.tree.setCurrentItem(tp._find_item(tp.tree.invisibleRootItem(), parent_task))
        assert win.main_pane.table_pane.table.rowCount() > 1
        win._on_worklog_requested(ticket_idx)
        for text in ("資料の叩き台を作成", "先方回答待ち"):
            dp.log_edit.setText(text)
            dp._on_add_work_log()
            QApplication.processEvents()
            assert dp._node_idx == ticket_idx, f"追記後に詳細ペインが {dp._node_idx} へ切り替わった"
        lines = str(state.df_nodes.loc[ticket_idx, "memo"]).splitlines()[-2:]
        got = [LG.WORK_LOG_RE.match(l).group(5) for l in lines]
        assert got == ["資料の叩き台を作成", "先方回答待ち"], lines
        assert state.nodes_modified, "作業ログ追記で未保存にならない"
        assert dp.log_edit is not None and dp.log_edit.text() == "", "続けて入力できない"
        ok("右クリック→詳細ペインの入力欄で作業ログを追記（未保存扱い・続けて入力可）")
    except Exception as e:
        ng("詳細ペインの作業ログ", e)
    finally:
        state.df_nodes.loc[ticket_idx, "memo"] = orig_memo
    try:
        win.detail_pane.update_for_node(ticket2_idx)   # tanaka 担当
        assert win.detail_pane.log_edit is None, "他人のチケットにも入力欄が出た"
        ok("他人のチケットには作業ログの入力欄を出さない")
    except Exception as e:
        ng("他人のチケット", e)


def test_e2_deadline_risk(win, task_idx):
    """E2: 納期リスク予報（見積係数・Today・ガント・Config）"""
    print("\n[E2] 納期リスク予報テスト")
    import pandas as pd
    import logic as LG
    import ui_sub
    from db import create_initial_node
    state = win.state
    me = state.user

    def frame(rows):
        return pd.DataFrame([r for r in rows]).set_index(pd.Index([r.name for r in rows]))
    try:
        base = []
        for i, ratio in enumerate([1.2, 1.4, 1.3, 1.1, 1.5]):
            n = create_initial_node(me, "ticket", f"済{i}", "t", i)
            n["status"] = "done"; n["estimated_hours"] = 2.0; n["actual_hours"] = 2.0 * ratio
            base.append(n)
        df = frame(base)
        assert LG.estimate_factor(df, me) == (1.3, 5), LG.estimate_factor(df, me)
        assert LG.estimate_factor(frame(base[:4]), me) == (1.0, 4), "5 件未満は 1.0"
        base[0]["actual_hours"] = 0.0           # 実績未記録は除外 → 4 件で 1.0
        assert LG.estimate_factor(frame(base), me) == (1.0, 4)
        big = frame([create_initial_node(me, "ticket", f"遅{i}", "t", i) for i in range(5)])
        big["status"] = "done"; big["estimated_hours"] = 1.0; big["actual_hours"] = 5.0
        assert LG.estimate_factor(big, me)[0] == 2.0, "上限 2.0 に丸めない"
        ok("見積係数: 実績÷見積の平均（完了 5 件未満・実績 0 は除外、1.0〜2.0 に丸め）")
    except Exception as e:
        ng("estimate_factor", e)
    try:
        # 月曜 9/28 起点・1 日 5h。納期 9/29(火) の 8h → 係数 1.0 なら間に合う、1.5 なら 12h で 9/30 完了
        T = datetime.date(2026, 9, 28)
        task = create_initial_node(me, "task", "T", "p", 1)
        tk = create_initial_node(me, "ticket", "資料作成", task.name, 1)
        tk["estimated_hours"] = 8.0; tk["deadline"] = "2026-09-29"
        df = frame([task, tk])
        assert LG.deadline_risks(df, me, 5.0, ["SAT", "SUN"], T, 1.0) == []
        r = LG.deadline_risks(df, me, 5.0, ["SAT", "SUN"], T, 1.5)
        assert len(r) == 1 and r[0]["finish"] == datetime.date(2026, 9, 30) and r[0]["late_days"] == 1, r
        assert r[0]["task"] == "T"
        tk["status"] = "regularly"                # 定常は予報しない
        assert LG.deadline_risks(frame([task, tk]), me, 5.0, ["SAT", "SUN"], T, 1.5) == []
        tk["status"] = "todo"
        tk["deadline"] = "2026-09-25"             # 過去の納期は予報に含めない（超過アラートで扱う）
        assert LG.deadline_risks(frame([task, tk]), me, 5.0, ["SAT", "SUN"], T, 1.5) == []
        ok("予報: 見積×係数の残りで間に合わない今日以降の納期だけを返す（営業日で遅れ日数）")
    except Exception as e:
        ng("deadline_risks", e)
    added = None
    orig = state.config.risk_use_factor
    try:
        n = create_initial_node(me, "ticket", "巨大チケット", task_idx, 60)
        n["estimated_hours"] = 200.0
        n["deadline"] = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        state.df_nodes.loc[n.name] = n
        added = n.name
        dv = win.dashboard_view
        dv.refresh()
        lst = dv._cards["edit"]["list"]
        texts = [lst.item(i).text() for i in range(lst.count())]
        hits = [t for t in texts if t.startswith("🔮予報") and "巨大チケット" in t]
        assert len(hits) == 1, texts
        assert not any(t.startswith("接近") and "巨大チケット" in t for t in texts), "接近と予報が重複"
        ok("Today の納期アラートに 🔮予報 を表示（接近との重複なし）")
        gv = win.gantt_view
        state.current_member = me
        gv._status_radios["all"].setChecked(True)
        gv._rebuild_table()
        titles = [gv.table.item(r, 1).text() for r in range(gv.table.rowCount()) if gv.table.item(r, 1)]
        assert any("🔮" in t and "巨大チケット" in t for t in titles), titles
        ok("ガントのタイトルに 🔮 と予報のツールチップ")
        state.config.risk_use_factor = False
        assert ui_sub.deadline_risk_forecast(state, me)[1] == 1.0
        ok("Config risk_use_factor=false で係数 1.0（見積どおり）")
    except Exception as e:
        ng("Today・ガントの予報表示", e)
    finally:
        state.config.risk_use_factor = orig
        if added in state.df_nodes.index:
            state.df_nodes.drop(index=[added], inplace=True)


def test_i2_rewards(win):
    """I2: 小さなごほうび（連続記録・今週の完了・見積ぴったり）"""
    print("\n[I2] 小さなごほうびテスト")
    import pandas as pd
    import logic as LG
    from db import DAILY_TIME_COLS, create_initial_node
    me = win.state.user
    try:
        def daily(dates):
            rows = {}
            for d in dates:
                r = {c: "" for c in DAILY_TIME_COLS}
                r["Owner"] = me
                r["C0900"] = "tk"
                rows[f"{d}-{me}"] = r
            return pd.DataFrame.from_dict(rows, orient="index")
        T = datetime.date(2026, 9, 28)   # 月曜
        hol = ["SAT", "SUN"]
        empty = pd.DataFrame(columns=["node_type"])
        dd = daily(["2026-09-24", "2026-09-25"])          # 木・金（土日は休日）
        assert LG.motivation_stats(empty, dd, me, hol, T)["streak"] == 2, "今日未記録なら昨日から数える"
        dd = daily(["2026-09-24", "2026-09-25", "2026-09-28"])
        assert LG.motivation_stats(empty, dd, me, hol, T)["streak"] == 3
        dd = daily(["2026-09-23", "2026-09-25", "2026-09-28"])   # 木が抜け
        assert LG.motivation_stats(empty, dd, me, hol, T)["streak"] == 2
        dd = daily(["2026-09-25", "2026-09-27"])           # 日曜の休日出勤も数える
        assert LG.motivation_stats(empty, dd, me, hol, datetime.date(2026, 9, 27))["streak"] == 2
        ok("連続記録: 記録の無い休日は飛ばし休日出勤は数える・今日の記録前は昨日まで")
    except Exception as e:
        ng("連続記録", e)
    try:
        T = datetime.date(2026, 9, 30)   # 水曜
        rows = []
        for i, (end, est, act) in enumerate([("2026-09-28", 2.0, 2.2), ("2026-09-30", 2.0, 3.0),
                                             ("2026-09-25", 1.0, 1.0), ("2026-08-31", 1.0, 1.0)]):
            n = create_initial_node(me, "ticket", f"t{i}", "t", i)
            n["status"] = "done"; n["actual_end"] = end
            n["estimated_hours"] = est; n["actual_hours"] = act
            rows.append(n)
        df = pd.DataFrame(rows).set_index(pd.Index([r.name for r in rows]))
        st = LG.motivation_stats(df, pd.DataFrame(), me, ["SAT", "SUN"], T)
        assert st["week_done"] == 2, st                   # 9/28・9/30
        assert (st["bullseye"], st["bullseye_total"]) == (2, 3), st   # 今月 3 件中 ±20% は 2 件
        ok("今週の完了数・今月の見積ぴったり（±20%）")
    except Exception as e:
        ng("完了数・見積ぴったり", e)
    try:
        dv = win.dashboard_view
        dv.refresh()
        texts = [l.text() for l in dv._reward_lbls.values()]
        assert texts[0].startswith("🔥 連続記録") and texts[1].startswith("✅ 今週の完了") \
            and texts[2].startswith("🎯 見積ぴったり"), texts
        ok("Today のヘッダーに 3 つのごほうび表示")
    except Exception as e:
        ng("Today のごほうび表示", e)


def test_g4_forecast(win, task_idx):
    """G4: 完了予測（残りの推移・ペース・完了予想日）"""
    print("\n[G4] 完了予測テスト")
    from PySide6.QtWidgets import QLabel
    import pandas as pd
    import logic as LG
    from db import DAILY_TIME_COLS, create_initial_node
    me = win.state.user
    try:
        task = create_initial_node(me, "task", "T", "p", 1)
        task["deadline"] = "2026-10-09"
        a = create_initial_node(me, "ticket", "A", task.name, 1)
        a["estimated_hours"] = 10.0; a["actual_hours"] = 4.0; a["created_at"] = "2026-09-20"
        b = create_initial_node(me, "ticket", "B", task.name, 2)
        b["estimated_hours"] = 4.0; b["actual_hours"] = 4.0; b["status"] = "done"
        b["actual_end"] = "2026-09-29"; b["created_at"] = "2026-09-20"
        df = pd.DataFrame([task, a, b]).set_index(pd.Index([task.name, a.name, b.name]))
        rows = {}
        for d, tk, h in [("2026-09-25", b.name, 4.0), ("2026-09-28", a.name, 2.0), ("2026-09-29", a.name, 2.0)]:
            r = {c: "" for c in DAILY_TIME_COLS}
            r["Owner"] = me
            for c in DAILY_TIME_COLS[36:36 + int(h * 4)]:
                r[c] = tk
            rows[f"{d}-{me}"] = r
        daily = pd.DataFrame.from_dict(rows, orient="index")
        T = datetime.date(2026, 9, 30)
        fc = LG.completion_forecast(df, daily, {task.name}, None, ["SAT", "SUN"], T)
        assert fc["remaining_now"] == 6.0, fc
        assert fc["pace"] == 0.8, fc                         # 8h ÷ 10 営業日
        assert fc["forecast"] == datetime.date(2026, 10, 12), fc["forecast"]   # 8 営業日後
        assert fc["deadline"] == datetime.date(2026, 10, 9)
        h = dict(fc["history"])
        assert h[datetime.date(2026, 9, 24)] == 14.0 and h[datetime.date(2026, 9, 25)] == 10.0 \
            and h[datetime.date(2026, 9, 29)] == 6.0 and h[T] == 6.0, h
        assert fc["history"][0] == (datetime.date(2026, 9, 19), 0.0), fc["history"][:2]  # 作成前日から
        c = create_initial_node(me, "ticket", "C", task.name, 3)   # 完了日の記録が無い done
        c["estimated_hours"] = 3.0; c["status"] = "done"; c["created_at"] = "2026-09-20"
        df2 = pd.concat([df, pd.DataFrame([c]).set_index(pd.Index([c.name]))])
        fc2 = LG.completion_forecast(df2, daily, {task.name}, None, ["SAT", "SUN"], T)
        assert fc2["history"][-1][1] == fc2["remaining_now"] == 6.0, "推移の終点と現在の残りがずれる"
        txt = LG.completion_text(fc)
        assert txt.startswith("10/12 完了見込み（納期 10/9 に遅れ ⚠）"), txt
        empty = LG.completion_forecast(df, pd.DataFrame(), {task.name}, None, ["SAT", "SUN"], T)
        assert empty["forecast"] is None and "予測できません" in LG.completion_text(empty)
        ok("残り 6h・ペース 0.8h/日 → 10/12 完了見込み（納期超過を表示）、推移を日ごとに復元")
    except Exception as e:
        ng("completion_forecast", e)
    try:
        anal = win.anal_view
        anal._calc_burndown()
        assert anal._ax.get_title().startswith("完了予測"), anal._ax.get_title()
        anal._calc()
        ok("Analyze の「📉 完了予測」グラフ")
        dp = win.detail_pane
        dp.update_for_node(task_idx)
        heads = [w.text() for w in dp.findChildren(QLabel) if w.text() == "完了予測"]
        assert heads, "詳細ペインに完了予測カードが無い"
        ok("詳細ペイン（Task）に完了予測カード")
    except Exception as e:
        ng("完了予測の表示", e)


def test_g2_achievement(win):
    """G2: 成果のまとめ（期間プリセット・集計・Markdown・ダイアログ）"""
    print("\n[G2] 成果のまとめテスト")
    import pandas as pd
    import logic as LG
    import ui_sub
    from PySide6.QtWidgets import QApplication
    from db import DAILY_TIME_COLS, create_initial_node
    me = win.state.user
    try:
        R = lambda n, d: LG.review_period(n, d)
        oct5, jul1, feb1 = datetime.date(2026, 10, 5), datetime.date(2026, 7, 1), datetime.date(2027, 2, 1)
        assert R("上期", oct5) == ("2026-04-01", "2026-09-30")
        assert R("下期", oct5) == ("2026-10-01", "2027-03-31")
        assert R("年度", oct5) == ("2026-04-01", "2027-03-31")
        assert R("下期", jul1) == ("2025-10-01", "2026-03-31"), "上期中の「下期」は直近の前年度下期"
        assert R("上期", feb1) == ("2026-04-01", "2026-09-30") and R("下期", feb1) == ("2026-10-01", "2027-03-31")
        ok("期間プリセット: 上期／下期は今日を含む・直近の半期、年度は 4 月始まり")
    except Exception as e:
        ng("review_period", e)
    try:
        memo = "[12/20 10:00] 年末の調整\n[01/10 09:00] 年明けの対応\n[06/01 09:00] 期間外\n普通のメモ"
        assert LG._work_logs_in_period(memo, "2026-12-01", "2027-01-31") == \
            ["[12/20 10:00] 年末の調整", "[01/10 09:00] 年明けの対応"]
        ok("作業ログは年をまたぐ期間でも月日から期間内を判定")
    except Exception as e:
        ng("作業ログの期間抽出", e)
    try:
        p1a = create_initial_node(me, "project1", "案件A", "0", 1)
        p1b = create_initial_node(me, "project1", "案件B", "0", 2)
        ta = create_initial_node(me, "task", "設計", p1a.name, 1)
        tb = create_initial_node(me, "task", "運用", p1b.name, 1)
        k1 = create_initial_node(me, "ticket", "基本設計", ta.name, 1)
        k1.update({"status": "done", "actual_end": "2026-05-20", "estimated_hours": 4.0,
                   "actual_hours": 5.0, "memo": "[05/10 10:00] レビュー指摘を反映"})
        k2 = create_initial_node(me, "ticket", "月次運用", tb.name, 1)
        k3 = create_initial_node(me, "ticket", "昨年度の件", ta.name, 2)
        k3.update({"status": "done", "actual_end": "2026-03-30"})
        nodes = [p1a, p1b, ta, tb, k1, k2, k3]
        df = pd.DataFrame(nodes).set_index(pd.Index([n.name for n in nodes]))
        rows = {}
        for d, tk, h in [("2026-05-10", k1.name, 3.0), ("2026-05-11", k1.name, 2.0),
                         ("2026-06-01", k2.name, 1.0), ("2026-03-30", k3.name, 8.0)]:
            r = {c: "" for c in DAILY_TIME_COLS}
            r["Owner"] = me
            for c in DAILY_TIME_COLS[36:36 + int(h * 4)]:
                r[c] = tk
            rows[f"{d}-{me}"] = r
        daily = pd.DataFrame.from_dict(rows, orient="index")
        data = LG.achievement_summary(df, daily, me, "2026-04-01", "2026-09-30")
        assert data["total_hours"] == 6.0 and data["days_worked"] == 3, data   # 3/30 は期間外
        assert data["by_p1"] == [("案件A", 5.0), ("案件B", 1.0)], data["by_p1"]
        assert [t["hours"] for t in data["top"]] == [5.0, 1.0] and data["top"][0]["path"] == "案件A ＞ 設計 ＞ 基本設計"
        assert data["top"][0]["logs"] == ["[05/10 10:00] レビュー指摘を反映"]
        assert [c["title"] for c in data["completed"]] == ["基本設計"], data["completed"]
        assert data["accuracy"] == 1.25 and data["accuracy_by_month"] == {"2026-05": (1, 1.25)}
        md = LG.build_achievement_markdown(data, "山田")
        for key in ("# 成果のまとめ（山田）", "## プロジェクト別の投入時間", "| 案件A | 5h | 83% |",
                    "## 時間をかけた仕事 上位 5 件", "   - [05/10 10:00] レビュー指摘を反映",
                    "### 案件A", "- 2026-05-20 基本設計（見積 4h → 実績 5h）", "## 見積精度の推移（月別）"):
            assert key in md, key
        ok("投入時間・プロジェクト別・上位 5 件（作業ログ付き）・完了一覧・見積精度を Markdown に")
    except Exception as e:
        ng("achievement_summary", e)
    try:
        d = ui_sub.AchievementDialog(win.state, win)
        assert d._preset_btns["上期"].isChecked() and d.markdown.startswith("# 成果のまとめ")
        d.member.set_user("tanaka@email.com")
        assert win.state.display_name("tanaka@email.com") in d.markdown.splitlines()[0]
        d._copy_for_ai()
        clip = QApplication.clipboard().text()
        assert "自己評価の下書き" in clip and "# 成果のまとめ" in clip
        d.p_from.set_date("2026-01-01"); d._on_custom()
        assert not any(b.isChecked() for b in d._preset_btns.values())
        d.close()
        ok("ダイアログ: メンバー切替・任意期間・AI 用コピー（依頼文＋データ）")
    except Exception as e:
        ng("成果のまとめダイアログ", e)


def test_f3_now_window(win, state, version, tmpdir, ticket_idx):
    """F3: 「いま」の小窓（いま・次・残り分、ON/OFF、状態の記憶）"""
    print("\n[F3] いまの小窓テスト")
    import pandas as pd
    import logic as LG
    from PySide6.QtCore import QSettings, QPoint
    from PySide6.QtWidgets import QApplication
    from db import DAILY_TIME_COLS, create_initial_node
    me = state.user
    try:
        a = create_initial_node(me, "ticket", "仕様レビュー", "t", 1)
        b = create_initial_node(me, "ticket", "定例", "t", 2)
        nodes = pd.DataFrame([a, b]).set_index(pd.Index([a.name, b.name]))
        r = {c: "" for c in DAILY_TIME_COLS}
        r["Owner"] = me
        for c in DAILY_TIME_COLS[40:44]:   # 10:00〜11:00
            r[c] = a.name
        for c in DAILY_TIME_COLS[46:48]:   # 11:30〜12:00
            r[c] = b.name
        daily = pd.DataFrame.from_dict({f"2026-09-28-{me}": r}, orient="index")
        at = lambda h, m: LG.now_and_next(daily, nodes, me, datetime.datetime(2026, 9, 28, h, m))
        x = at(10, 40)
        assert x["now"]["title"] == "仕様レビュー" and x["left_min"] == 20 and x["next"]["title"] == "定例", x
        x = at(11, 10)
        assert x["now"] is None and x["next"]["from"] == "11:30", x
        x = at(12, 0)
        assert x["now"] is None and x["next"] is None, x
        ok("いま（残り分）と次の予定を今日の日次スケジュールから求める")
    except Exception as e:
        ng("now_and_next", e)
    try:
        nw = win.now_window
        win.now_btn.setChecked(True)
        assert nw.isVisible()
        nw.update_view()
        assert nw.now_lbl.text().startswith("いま: ") and nw.next_lbl.text().startswith("次: ")
        nw._on_close()
        assert not nw.isVisible() and not win.now_btn.isChecked(), "× で閉じてもボタンが ON のまま"
        ok("ツールバーの 📌 いま で表示・× で閉じるとボタンも OFF")
    except Exception as e:
        ng("小窓の表示切替", e)
    try:
        from ui_main import MainWindow
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmpdir)
        screen = QApplication.primaryScreen().availableGeometry()
        target = screen.topLeft() + QPoint(40, 50)
        win.now_btn.setChecked(True)
        win.now_window.move(target)
        state.nodes_modified = False
        state.schedule_modified = False
        win.save_ui_state()
        win.now_btn.setChecked(False)
        w2 = MainWindow(state, version)
        w2.restore_ui_state()
        assert w2.now_btn.isChecked() and w2.now_window.isVisible(), "表示状態が戻らない"
        assert w2.now_window.pos() == target, (w2.now_window.pos(), target)
        w2.now_btn.setChecked(False)
        w2.hide(); w2.deleteLater()
        ok("小窓の表示状態と位置を次回起動時に復元")
    except Exception as e:
        ng("小窓の状態の記憶", e)


def test_i3_dark_mode(win):
    """I3: ダークモード（配色の切替・Config の選択肢・起動時の判定）"""
    print("\n[I3] ダークモードテスト")
    import theme
    from schedule_app import AppConfig
    light = {k: getattr(theme.C, k) for k in vars(theme.C) if k.isupper()}
    btn, level = theme.STYLE_BUTTON, theme.LEVEL_BG
    try:
        theme.set_mode("dark")
        assert theme.MODE == "dark" and theme.C.SURFACE == theme.DARK["SURFACE"]
        assert theme.STYLE_BUTTON != btn and "#2A2640" in theme.STYLE_BUTTON, "共通スタイルが作り直されない"
        assert theme.LEVEL_BG is level and level["task"] == theme.DARK["TASK_BG"], "階層色の辞書が更新されない"
        assert theme.mpl_style()["axes.facecolor"] == theme.DARK["SURFACE"]
        assert theme.C.NODE_DEFAULT == light["NODE_DEFAULT"], "ダークに無いトークンはライトの値を使う"
        theme.set_mode("light")
        assert {k: getattr(theme.C, k) for k in light} == light and theme.STYLE_BUTTON == btn
        ok("set_mode: ダーク⇄ライトで色・共通スタイル・階層色・グラフ色が切り替わり、元に戻る")
    except Exception as e:
        ng("set_mode", e)
    finally:
        theme.set_mode("light")
    try:
        R = theme.resolve_mode
        assert R("dark", False) == "dark" and R("light", True) == "light"
        assert R("system", True) == "dark" and R("system", False) == "light" and R("", True) == "light"
        assert AppConfig().theme == "light"
        w = win.config_view._fields["gui_theme"]
        assert [w.itemData(i) for i in range(w.count())] == ["light", "dark", "system"]
        ok("Config の theme（ライト／ダーク／OS に合わせる、既定ライト）と起動時の判定")
    except Exception as e:
        ng("theme 設定", e)


def test_quick_add_same_task_name(win):
    """クイック追加: 別プロジェクトに同名 Task があっても選べる（@親/Task）"""
    print("\n[B2+] 同名 Task のクイック追加テスト")
    import logic as LG
    import ui_main as M
    from db import create_initial_node
    state = win.state
    me = state.user
    added = []
    try:
        ids = {}
        for pj in ("案件A", "案件B"):
            p = create_initial_node(me, "project1", pj, "0", 90)
            t = create_initial_node(me, "task", "設計 レビュー", p.name, 1)
            for n in (p, t):
                state.df_nodes.loc[n.name] = n
                added.append(n.name)
            ids[pj] = t.name
        df = state.df_nodes
        assert LG.resolve_task_query(df, "設計レビュー", me) == (None, 2), "同名なのに確定した"
        assert LG.resolve_task_query(df, "案件B/設計レビュー", me)[0] == ids["案件B"]
        assert LG.resolve_task_query(df, "ｂ＞設計", me)[0] == ids["案件B"], "親の部分一致・全角区切り"
        assert LG.resolve_task_query(df, "案件C/設計", me) == (None, 0)
        assert LG.task_query_label(df, ids["案件A"], me) == "案件A/設計レビュー"
        ok("同名 Task は @親の名前/Task で絞り込み・確定（親は部分一致、/ ／ > ＞ 区切り）")
    except Exception as e:
        ng("同名 Task の解決", e)
    try:
        d = M.QuickAddDialog(state, parent=win)
        d.edit.setText("資料作成 @設計レビュー #メモ")
        rows = [d.cands.item(i).data(0x0100) for i in range(d.cands.count())]
        d.cands.setCurrentRow(rows.index(ids["案件B"]))
        d._choose_candidate()
        assert d._task_idx == ids["案件B"], "候補を選んでも Task が確定しない"
        assert d.edit.text() == "資料作成 @案件B/設計レビュー #メモ", d.edit.text()
        assert d.btns.button(d.btns.StandardButton.Ok).isEnabled()
        d.edit.setText("資料作成 @案件a/設計 #メモ")
        assert d._task_idx == ids["案件A"]
        d.close()
        ok("候補から選ぶと @案件B/設計 に書き換えて確定・手入力の @案件a/設計 も確定")
    except Exception as e:
        ng("同名 Task の候補選択", e)
    finally:
        state.df_nodes.drop(index=[i for i in added if i in state.df_nodes.index], inplace=True)


def test_theme():
    """D1: 色は theme.py に集約（ui_*.py に色コードを直書きしない・@トークンは全て定義済み）"""
    print("\n[D1] テーマ集約テスト")
    import re
    import theme
    from PySide6.QtWidgets import QApplication
    src = Path(__file__).parent
    try:
        hits = [f"{f.name}:{n}" for f in sorted(src.glob("ui_*.py"))
                for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                if re.search(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b", line)]
        assert not hits, hits
        ok("ui_*.py に色コードの直書きが無い")
    except Exception as e:
        ng("色コードの直書き", e)
    try:
        names = {n for n in vars(theme.C) if n.isupper()}
        undefined = set()
        for f in list(src.glob("ui_*.py")) + [src / "theme.py"]:
            text = f.read_text(encoding="utf-8")
            undefined |= {m for m in re.findall(r"@([a-z][a-z0-9_]*)", text)
                          if m != "staticmethod" and m.upper() not in names}
            undefined |= {m for m in re.findall(r"\bC\.([A-Z][A-Z0-9_]*)", text) if m not in names}
        assert not undefined, undefined
        ok("使用中の色トークンが全て theme.C に定義されている")
    except Exception as e:
        ng("色トークンの定義", e)
    try:
        app = QApplication.instance()
        before = app.styleSheet()
        theme.apply_app_theme(app)
        assert app.styleSheet() == theme.APP_QSS and "@" not in theme.APP_QSS
        app.setStyleSheet(before)
        ok("apply_app_theme がアプリ全体のスタイルを適用できる")
    except Exception as e:
        ng("apply_app_theme", e)


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
            test_assignment_view_multi_rows(win, ticket_idx)
            test_undo_redo(win, task_idx, ticket_idx)
            test_quick_add_parse()
            test_quick_add_inbox(win, task_idx, tmpdir)
            test_quick_add_same_task_name(win)
            test_save_load(state)
            test_ui_state(state, version, win, tmpdir)
            test_theme()
            test_requests_0925(win)
            test_analysis_0925(win, pj_idx, task_idx, ticket_idx)
            test_e1_estimate_assist(win, task_idx)
            test_f1_work_log(win, ticket_idx, ticket2_idx)
            test_e2_deadline_risk(win, task_idx)
            test_i2_rewards(win)
            test_g4_forecast(win, task_idx)
            test_g2_achievement(win)
            test_f3_now_window(win, state, version, tmpdir, ticket_idx)
            test_i3_dark_mode(win)

    print("\n" + "=" * 55)
    print(f"  結果: OK={PASS}  NG={FAIL}  合計={PASS+FAIL}")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
