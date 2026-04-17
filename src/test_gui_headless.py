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
            test_save_load(state)

    print("\n" + "=" * 55)
    print(f"  結果: OK={PASS}  NG={FAIL}  合計={PASS+FAIL}")
    print("=" * 55)
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
