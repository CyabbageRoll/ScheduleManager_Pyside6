"""
add_april15_test.py - 4/15が納期のテストデータを既存DBに追加するスクリプト

既存DBは削除しません。データを追加するだけです。

実行方法:
    cd src/
    python add_april15_test.py
"""
import sys
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import Database, create_initial_node

DB_DIR = Path(__file__).parent / "db"
OWNER = "Yamada"
DEADLINE_4_15 = "2026-04-15"

TODAY = datetime.date.today()


def d(delta_days: int) -> str:
    return (TODAY + datetime.timedelta(days=delta_days)).isoformat()


def delta_to_april15() -> int:
    """今日から4/15までの日数差"""
    target = datetime.date(2026, 4, 15)
    return (target - TODAY).days


def make(db: Database, owner: str, node_type: str, title: str,
         parent_id: str, priority: int = 99, color: str = "",
         status: str = "todo", estimated_hours: float = 0.0,
         start_available: str = "", deadline: str = "",
         memo: str = "", assigned_to: str = "") -> str:
    time.sleep(0.01)
    ds = create_initial_node(owner, node_type, title, parent_id, priority, color)
    ds["status"]          = status
    ds["estimated_hours"] = estimated_hours
    ds["start_available"] = start_available or None
    ds["deadline"]        = deadline or None
    ds["memo"]            = memo
    ds["assigned_to"]     = assigned_to or owner
    db.upsert_node(ds)
    return ds.name


def make_task(db: Database, owner: str, title: str, parent_id: str,
              priority: int = 99, color: str = "Yellow",
              status: str = "todo", start_available: str = "",
              deadline: str = "", memo: str = "") -> str:
    idx = make(db, owner, "task", title, parent_id, priority, color,
               status, 0.0, start_available, deadline, memo)
    df = db.read_nodes()
    task_ds = df.loc[idx]
    db.create_auto_children(task_ds, owner)
    return idx


def make_ticket(db: Database, owner: str, title: str, parent_id: str,
                priority: int, estimated_hours: float,
                start_available: str = "", deadline: str = "",
                status: str = "todo", memo: str = "",
                color: str = "Cyan", assigned_to: str = "") -> str:
    return make(db, owner, "ticket", title, parent_id, priority, color,
                status, estimated_hours, start_available, deadline, memo,
                assigned_to or owner)


def main():
    db_file = DB_DIR / "schedule.sqlite"
    if not db_file.exists():
        print(f"DBファイルが見つかりません: {db_file}")
        print("先に create_test_db.py を実行してください。")
        return

    db = Database(str(DB_DIR))
    print(f"DB接続: {db_file}\n")

    # 4/15までの残り日数
    days_to = delta_to_april15()
    print(f"今日から4/15まで: {days_to}日\n")

    # ================================================================
    # テストProject: 4/15締め切りプロジェクト
    # ================================================================
    pj1 = make(db, OWNER, "project1", "[テスト] 4月15日締め切りプロジェクト", "0",
               priority=2, color="Red",
               memo="ガントチャート表示テスト用。4/15が納期のチケットを多数含む。")
    print(f"[PJ1] 4月15日締め切りプロジェクト: {pj1}")

    # ================================================================
    # PJ2-A: 4/15が納期のタスク群（納期あり・開始可能日あり）
    # ================================================================
    pj2_a = make(db, OWNER, "project2", "タスクグループA（納期あり）", pj1,
                 priority=1, color="Orange",
                 start_available=d(0), deadline=DEADLINE_4_15)
    print(f"  [PJ2-A] タスクグループA: {pj2_a}")

    pj3_a = make(db, OWNER, "project3", "設計・調査フェーズ", pj2_a,
                 priority=1, color="Cyan",
                 start_available=d(0), deadline=DEADLINE_4_15)

    pj4_a = make(db, OWNER, "project4", "設計作業", pj3_a,
                 priority=1, color="Lime",
                 start_available=d(0), deadline=DEADLINE_4_15)
    tk_a1 = make_task(db, OWNER, "要件整理タスク", pj4_a,
                      priority=1, color="Yellow",
                      start_available=d(0), deadline=DEADLINE_4_15)
    # 全チケットが4/15納期（直接指定）
    make_ticket(db, OWNER, "要件ヒアリング", tk_a1, 2, 3.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="関係者全員からヒアリング実施")
    make_ticket(db, OWNER, "現状分析レポート作成", tk_a1, 3, 5.0,
                start_available=d(1), deadline=DEADLINE_4_15,
                memo="As-Is/To-Be分析")
    make_ticket(db, OWNER, "課題リスト整備", tk_a1, 4, 2.0,
                start_available=d(2), deadline=DEADLINE_4_15,
                memo="優先度付きで整理")
    make_ticket(db, OWNER, "スケジュール策定", tk_a1, 5, 4.0,
                start_available=d(3), deadline=DEADLINE_4_15,
                memo="WBSを作成して工程を確認")
    make_ticket(db, OWNER, "ステークホルダー合意", tk_a1, 6, 2.0,
                start_available=d(4), deadline=DEADLINE_4_15,
                memo="全関係者のサインオフ取得")

    # ================================================================
    # PJ2-B: 納期なしチケット（仮納期計算テスト用）
    # ================================================================
    pj2_b = make(db, OWNER, "project2", "タスクグループB（納期なし混在）", pj1,
                 priority=2, color="Purple",
                 start_available=d(0), deadline=DEADLINE_4_15)
    print(f"  [PJ2-B] タスクグループB: {pj2_b}")

    pj3_b = make(db, OWNER, "project3", "開発フェーズ", pj2_b,
                 priority=1, color="Cyan",
                 start_available=d(0), deadline=DEADLINE_4_15)

    pj4_b = make(db, OWNER, "project4", "開発作業", pj3_b,
                 priority=1, color="Lime",
                 start_available=d(0), deadline=DEADLINE_4_15)
    tk_b1 = make_task(db, OWNER, "開発タスク1", pj4_b,
                      priority=1, color="Yellow",
                      start_available=d(0), deadline=DEADLINE_4_15)
    # 後ろのチケットは4/15、前のチケットは納期なし（逆算で仮納期が設定されるはず）
    make_ticket(db, OWNER, "基本設計（納期なし）", tk_b1, 2, 8.0,
                start_available=d(0), deadline="",
                memo="納期なし → 仮納期が逆算で計算されるはず")
    make_ticket(db, OWNER, "詳細設計（納期なし）", tk_b1, 3, 6.0,
                start_available=d(0), deadline="",
                memo="納期なし → 仮納期が逆算で計算されるはず")
    make_ticket(db, OWNER, "実装", tk_b1, 4, 16.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="メイン実装作業")
    make_ticket(db, OWNER, "単体テスト（納期なし）", tk_b1, 5, 4.0,
                start_available=d(0), deadline="",
                memo="納期なし → 実装から逆算")
    make_ticket(db, OWNER, "結合テスト", tk_b1, 6, 4.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="4/15が最終納期")

    # ================================================================
    # PJ2-C: 開始可能日制約テスト
    # ================================================================
    pj2_c = make(db, OWNER, "project2", "タスクグループC（開始可能日制約）", pj1,
                 priority=3, color="Teal",
                 start_available=d(0), deadline=DEADLINE_4_15)
    print(f"  [PJ2-C] タスクグループC: {pj2_c}")

    pj3_c = make(db, OWNER, "project3", "テストフェーズ", pj2_c,
                 priority=1, color="Cyan",
                 start_available=d(0), deadline=DEADLINE_4_15)

    pj4_c = make(db, OWNER, "project4", "テスト作業", pj3_c,
                 priority=1, color="Lime",
                 start_available=d(0), deadline=DEADLINE_4_15)
    tk_c1 = make_task(db, OWNER, "テストタスク（開始制約あり）", pj4_c,
                      priority=1, color="Yellow",
                      start_available=d(0), deadline=DEADLINE_4_15)
    # 一部のチケットに将来の開始可能日を設定（スケジューリングスキップのテスト）
    make_ticket(db, OWNER, "テスト計画作成", tk_c1, 2, 4.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="すぐ開始可能")
    make_ticket(db, OWNER, "環境構築（遅延開始）", tk_c1, 3, 6.0,
                start_available=d(7), deadline=DEADLINE_4_15,
                memo="開始可能日: 1週間後 → スキップして他に先行")
    make_ticket(db, OWNER, "テストデータ準備", tk_c1, 4, 3.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="すぐ開始可能")
    make_ticket(db, OWNER, "性能テスト（遅延開始）", tk_c1, 5, 8.0,
                start_available=d(14), deadline=DEADLINE_4_15,
                memo="開始可能日: 2週間後")
    make_ticket(db, OWNER, "バグ修正対応", tk_c1, 6, 5.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="テスト後に発生するバグを想定")

    # ================================================================
    # PJ2-D: 過負荷・納期超過テスト（多数の大工数チケット）
    # ================================================================
    pj2_d = make(db, OWNER, "project2", "タスクグループD（過負荷テスト）", pj1,
                 priority=4, color="Gray",
                 start_available=d(0), deadline=DEADLINE_4_15)
    print(f"  [PJ2-D] タスクグループD: {pj2_d}")

    pj3_d = make(db, OWNER, "project3", "大量作業フェーズ", pj2_d,
                 priority=1, color="Cyan",
                 start_available=d(0), deadline=DEADLINE_4_15)

    pj4_d = make(db, OWNER, "project4", "大量作業", pj3_d,
                 priority=1, color="Lime",
                 start_available=d(0), deadline=DEADLINE_4_15)
    tk_d1 = make_task(db, OWNER, "過負荷タスク", pj4_d,
                      priority=1, color="Yellow",
                      start_available=d(0), deadline=DEADLINE_4_15)
    # 合計工数が多く、4/15までに完了が難しいチケット群（赤アラートのテスト）
    make_ticket(db, OWNER, "大量作業1（工数大）", tk_d1, 2, 20.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="工数が大きい: 20h")
    make_ticket(db, OWNER, "大量作業2（工数大）", tk_d1, 3, 25.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="工数が大きい: 25h")
    make_ticket(db, OWNER, "大量作業3（工数大）", tk_d1, 4, 30.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="工数が大きい: 30h → 納期超過の可能性あり")
    make_ticket(db, OWNER, "大量作業4（工数大）", tk_d1, 5, 20.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="工数が大きい: 20h")
    make_ticket(db, OWNER, "大量作業5（工数大）", tk_d1, 6, 15.0,
                start_available=d(0), deadline=DEADLINE_4_15,
                memo="工数が大きい: 15h")

    # ================================================================
    # 最終確認
    # ================================================================
    df = db.read_nodes()
    print(f"\n=== 追加完了 ===")
    print(f"総ノード数: {len(df)}")
    april15_tickets = df[
        (df["node_type"] == "ticket") & (df["deadline"] == DEADLINE_4_15)
    ]
    print(f"4/15納期チケット数: {len(april15_tickets)}")
    print(f"\nDBファイル: {db_file}")


if __name__ == "__main__":
    main()
