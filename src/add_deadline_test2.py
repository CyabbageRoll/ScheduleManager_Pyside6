"""
add_deadline_test2.py - 仮納期計算テスト用データを既存DBに追加

追加内容:
  Task-E: 5チケット、全てに開始可能日・納期なし
           → 親タスクの納期(4/15)から全チケットの仮納期が逆算されるはず
  Task-F: 5チケット、最後(順番5)のみ納期=4/15、
           順番3のチケットに開始可能日を設定
           → 他チケットは最後から逆算で仮納期が付くはず

実行方法:
    cd src/
    python add_deadline_test2.py
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

    # ================================================================
    # テストProject: 仮納期計算テスト2
    # ================================================================
    pj1 = make(db, OWNER, "project1", "[テスト2] 仮納期逆算テストプロジェクト", "0",
               priority=3, color="Purple",
               deadline=DEADLINE_4_15,
               memo="全チケット納期なし・最後のみ納期ありの仮納期テスト用")
    print(f"[PJ1] 仮納期逆算テストプロジェクト: {pj1}")

    pj2 = make(db, OWNER, "project2", "仮納期テストグループ", pj1,
               priority=1, color="Teal",
               deadline=DEADLINE_4_15)

    pj3 = make(db, OWNER, "project3", "仮納期テストフェーズ", pj2,
               priority=1, color="Cyan",
               deadline=DEADLINE_4_15)

    pj4 = make(db, OWNER, "project4", "仮納期テスト作業", pj3,
               priority=1, color="Lime",
               deadline=DEADLINE_4_15)

    # ================================================================
    # Task-E: 全チケットに開始可能日・納期なし
    #   → 親タスクの納期(4/15)を基準に全チケットの仮納期を逆算するはず
    # ================================================================
    tk_e = make_task(db, OWNER, "Task-E: 全チケット納期なし（親納期=4/15）", pj4,
                     priority=1, color="Orange",
                     deadline=DEADLINE_4_15,
                     memo="全チケット納期・開始可能日なし。親の4/15から逆算")
    print(f"  [Task-E] 全チケット納期なし: {tk_e}")

    make_ticket(db, OWNER, "E-1: 作業ステップ1（納期なし）", tk_e, 2, 4.0,
                start_available="", deadline="",
                memo="納期なし・開始可能日なし")
    make_ticket(db, OWNER, "E-2: 作業ステップ2（納期なし）", tk_e, 3, 6.0,
                start_available="", deadline="",
                memo="納期なし・開始可能日なし")
    make_ticket(db, OWNER, "E-3: 作業ステップ3（納期なし）", tk_e, 4, 5.0,
                start_available="", deadline="",
                memo="納期なし・開始可能日なし")
    make_ticket(db, OWNER, "E-4: 作業ステップ4（納期なし）", tk_e, 5, 8.0,
                start_available="", deadline="",
                memo="納期なし・開始可能日なし")
    make_ticket(db, OWNER, "E-5: 作業ステップ5（納期なし）", tk_e, 6, 3.0,
                start_available="", deadline="",
                memo="納期なし・開始可能日なし（最後のTicket）")

    # ================================================================
    # Task-F: 最後(順番5)のみ納期=4/15、順番3に開始可能日あり
    #   → 順番1,2,4は逆算で仮納期が設定されるはず
    #   → 順番3のチケットは開始可能日制約でスキップされるケースのテスト
    # ================================================================
    tk_f = make_task(db, OWNER, "Task-F: 最後のみ納期あり・3番目に開始可能日", pj4,
                     priority=2, color="Red",
                     deadline=DEADLINE_4_15,
                     memo="最後(順番5)のみ納期=4/15、順番3に開始可能日あり")
    print(f"  [Task-F] 最後のみ納期あり: {tk_f}")

    make_ticket(db, OWNER, "F-1: 準備作業（納期なし）", tk_f, 2, 5.0,
                start_available="", deadline="",
                memo="納期なし → 逆算で仮納期が設定される")
    make_ticket(db, OWNER, "F-2: 設計（納期なし）", tk_f, 3, 8.0,
                start_available="", deadline="",
                memo="納期なし → 逆算で仮納期が設定される")
    make_ticket(db, OWNER, "F-3: 実装（開始可能日あり・納期なし）", tk_f, 4, 10.0,
                start_available=d(7), deadline="",
                memo="開始可能日=1週間後、納期なし → 逆算+開始制約のテスト")
    make_ticket(db, OWNER, "F-4: テスト（納期なし）", tk_f, 5, 4.0,
                start_available="", deadline="",
                memo="納期なし → 逆算で仮納期が設定される")
    make_ticket(db, OWNER, "F-5: リリース（納期=4/15）", tk_f, 6, 2.0,
                start_available="", deadline=DEADLINE_4_15,
                memo="最後のチケット。納期=4/15（唯一の明示的納期）")

    # ================================================================
    # 最終確認
    # ================================================================
    df = db.read_nodes()
    print(f"\n=== 追加完了 ===")
    print(f"総ノード数: {len(df)}")
    april15_tickets = df[
        (df["node_type"] == "ticket") & (df["deadline"] == DEADLINE_4_15)
    ]
    print(f"4/15納期チケット数（明示的）: {len(april15_tickets)}")
    print(f"\nTask-E, Task-F の全チケットはガントで仮納期が逆算表示されるはずです。")
    print(f"DBファイル: {db_file}")


if __name__ == "__main__":
    main()
