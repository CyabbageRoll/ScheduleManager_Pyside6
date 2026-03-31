"""
create_test_db.py - 「新築一戸建て建設」テストDBデータ投入スクリプト

実行方法:
    cd src/
    python create_test_db.py

既存のDBを削除してから再作成します。
"""
import sys
import datetime
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import Database, create_initial_node, daily_sch_idx, DAILY_TIME_COLS

# -------------------------------------------------------
# 設定
# -------------------------------------------------------
DB_DIR   = Path(__file__).parent / "db"
# ユーザーIDはOSログイン名形式を使用（メールアドレスでなく単純なID）
MEMBERS  = ["id001", "id002", "id003", "id004"]
OWNER    = "id001"   # メインオーナー

# 今日基準の日付ヘルパー
TODAY = datetime.date.today()

def d(delta_days: int) -> str:
    return (TODAY + datetime.timedelta(days=delta_days)).isoformat()


# -------------------------------------------------------
# ノード作成ヘルパー
# -------------------------------------------------------
def make(db: Database, owner: str, node_type: str, title: str,
         parent_id: str, priority: int = 99, color: str = "",
         status: str = "todo", estimated_hours: float = 0.0,
         start_available: str = "", deadline: str = "",
         memo: str = "", assigned_to: str = "") -> str:
    """ノードを作成してIDXを返す。"""
    time.sleep(0.01)  # IDX衝突防止（MD5ランダム成分あるが念のため）
    ds = create_initial_node(owner, node_type, title, parent_id, priority, color)
    ds["status"]           = status
    ds["estimated_hours"]  = estimated_hours
    ds["start_available"]  = start_available or None
    ds["deadline"]         = deadline or None
    ds["memo"]             = memo
    ds["assigned_to"]      = assigned_to or owner
    db.upsert_node(ds)
    return ds.name


def make_task(db: Database, owner: str, title: str, parent_id: str,
              priority: int = 99, color: str = "Yellow",
              status: str = "todo", start_available: str = "",
              deadline: str = "", memo: str = "") -> str:
    """Taskノードを作成し、自動チケット（詳細作成・完了）も生成する。"""
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
    """Ticketノードを作成してIDXを返す。"""
    return make(db, owner, "ticket", title, parent_id, priority, color,
                status, estimated_hours, start_available, deadline, memo,
                assigned_to or owner)


# -------------------------------------------------------
# メイン処理
# -------------------------------------------------------
def main():
    # 既存DBを削除して再作成
    db_file = DB_DIR / "schedule.sqlite"
    if db_file.exists():
        db_file.unlink()
        print(f"既存DB削除: {db_file}")

    db = Database(str(DB_DIR))
    print(f"DB作成: {db_file}\n")

    # ================================================================
    # Project1: 新築一戸建て建設プロジェクト
    # ================================================================
    pj1 = make(db, OWNER, "project1", "新築一戸建て建設プロジェクト", "0",
               priority=1, color="Blue",
               memo="2026年10月完工予定。延床面積120m²、木造2階建て。")
    print(f"[PJ1] 新築一戸建て建設プロジェクト: {pj1}")

    # ================================================================
    # Project2-A: 計画・設計フェーズ
    # ================================================================
    pj2_plan = make(db, OWNER, "project2", "計画・設計フェーズ", pj1,
                    priority=1, color="Teal",
                    start_available=d(-60), deadline=d(-10))
    print(f"  [PJ2] 計画・設計フェーズ: {pj2_plan}")

    # --- Project3: 要件定義 ---
    pj3_req = make(db, OWNER, "project3", "要件定義", pj2_plan,
                   priority=1, color="Cyan",
                   start_available=d(-60), deadline=d(-40))

    # Project4: 建設条件整理
    pj4_cond = make(db, OWNER, "project4", "建設条件整理", pj3_req,
                    priority=1, color="Lime",
                    start_available=d(-60), deadline=d(-50))
    tk_cond = make_task(db, OWNER, "建設条件まとめ", pj4_cond,
                        priority=1, color="Yellow",
                        status="done",
                        start_available=d(-60), deadline=d(-50))
    make_ticket(db, OWNER, "敷地調査",     tk_cond, 2, 4.0,
                start_available=d(-60), deadline=d(-55), status="done",
                memo="測量士に依頼済み。境界確定済み。")
    make_ticket(db, OWNER, "法規制確認",   tk_cond, 3, 2.0,
                start_available=d(-58), deadline=d(-53), status="done",
                memo="第一種低層住居専用地域。建蔽率40%・容積率80%。")
    make_ticket(db, OWNER, "予算計画作成", tk_cond, 4, 3.0,
                start_available=d(-55), deadline=d(-50), status="done",
                memo="総予算3,500万円。土地代別。")

    # Project4: 設計事務所選定
    pj4_arch = make(db, OWNER, "project4", "設計事務所選定", pj3_req,
                    priority=2, color="Lime",
                    start_available=d(-50), deadline=d(-40))
    tk_arch = make_task(db, OWNER, "設計事務所選定・契約", pj4_arch,
                        priority=1, color="Yellow",
                        status="done",
                        start_available=d(-50), deadline=d(-40))
    make_ticket(db, OWNER, "候補事務所リストアップ", tk_arch, 2, 2.0,
                start_available=d(-50), deadline=d(-48), status="done")
    make_ticket(db, OWNER, "見積依頼・比較",         tk_arch, 3, 3.0,
                start_available=d(-48), deadline=d(-44), status="done",
                memo="3社から見積取得。A設計事務所に決定。")
    make_ticket(db, OWNER, "契約締結",               tk_arch, 4, 1.0,
                start_available=d(-44), deadline=d(-40), status="done")

    # --- Project3: 基本設計 ---
    pj3_design = make(db, OWNER, "project3", "基本設計", pj2_plan,
                      priority=2, color="Cyan",
                      start_available=d(-40), deadline=d(-15))

    # Project4: 間取り設計
    pj4_layout = make(db, OWNER, "project4", "間取り設計", pj3_design,
                      priority=1, color="Lime",
                      start_available=d(-40), deadline=d(-25))
    tk_layout = make_task(db, OWNER, "間取り確定", pj4_layout,
                          priority=1, color="Yellow",
                          status="done",
                          start_available=d(-40), deadline=d(-25))
    make_ticket(db, OWNER, "要望ヒアリング",       tk_layout, 2, 2.0,
                start_available=d(-40), deadline=d(-38), status="done",
                memo="家族全員の要望をまとめた。LDK20帖以上、書斎必須。")
    make_ticket(db, OWNER, "間取り案作成（1次）",   tk_layout, 3, 8.0,
                start_available=d(-38), deadline=d(-32), status="done")
    make_ticket(db, OWNER, "修正・フィードバック", tk_layout, 4, 4.0,
                start_available=d(-32), deadline=d(-28), status="done",
                memo="玄関収納と2階トイレを追加。3回修正で確定。")
    make_ticket(db, OWNER, "間取り最終確定",       tk_layout, 5, 1.0,
                start_available=d(-28), deadline=d(-25), status="done")

    # Project4: 設備設計
    pj4_equip = make(db, OWNER, "project4", "設備設計", pj3_design,
                     priority=2, color="Lime",
                     start_available=d(-30), deadline=d(-15))
    tk_equip = make_task(db, OWNER, "設備仕様決定", pj4_equip,
                         priority=1, color="Yellow",
                         status="done",
                         start_available=d(-30), deadline=d(-15))
    make_ticket(db, OWNER, "キッチン仕様決定",   tk_equip, 2, 3.0,
                start_available=d(-30), deadline=d(-25), status="done",
                memo="クリナップ ステディア。食洗機・IH標準装備。",
                assigned_to="id002")
    make_ticket(db, OWNER, "浴室仕様決定",       tk_equip, 3, 2.0,
                start_available=d(-28), deadline=d(-22), status="done",
                memo="TOTO サザナ 1616。自動洗浄機能付き。",
                assigned_to="id002")
    make_ticket(db, OWNER, "電気設備・照明計画", tk_equip, 4, 4.0,
                start_available=d(-25), deadline=d(-20), status="done",
                memo="全LED。スイッチ・コンセント位置図を確定。")
    make_ticket(db, OWNER, "外構・植栽計画",     tk_equip, 5, 3.0,
                start_available=d(-22), deadline=d(-15), status="done",
                memo="カーポート（2台分）、シンボルツリー（ヤマボウシ）。")

    # ================================================================
    # Project2-B: 申請・許可フェーズ
    # ================================================================
    pj2_permit = make(db, OWNER, "project2", "申請・許可フェーズ", pj1,
                      priority=2, color="Teal",
                      start_available=d(-15), deadline=d(10))
    print(f"  [PJ2] 申請・許可フェーズ: {pj2_permit}")

    pj3_permit = make(db, OWNER, "project3", "建築確認申請", pj2_permit,
                      priority=1, color="Cyan",
                      start_available=d(-15), deadline=d(10))
    pj4_permit = make(db, OWNER, "project4", "確認申請手続き", pj3_permit,
                      priority=1, color="Lime",
                      start_available=d(-15), deadline=d(10))
    tk_permit = make_task(db, OWNER, "建築確認申請", pj4_permit,
                          priority=1, color="Yellow",
                          start_available=d(-15), deadline=d(10))
    make_ticket(db, OWNER, "申請書類作成",         tk_permit, 2, 8.0,
                start_available=d(-15), deadline=d(-5), status="done",
                memo="設計事務所と連携して作成。")
    make_ticket(db, OWNER, "確認申請提出",         tk_permit, 3, 1.0,
                start_available=d(-5), deadline=d(0), status="done",
                memo="役所窓口へ提出済み。受付番号: R8-00123。")
    make_ticket(db, OWNER, "確認済証受領",         tk_permit, 4, 0.5,
                start_available=d(5), deadline=d(10), status="todo",
                memo="審査期間：約2週間。")

    # ================================================================
    # Project2-C: 工事フェーズ
    # ================================================================
    pj2_work = make(db, OWNER, "project2", "工事フェーズ", pj1,
                    priority=3, color="Teal",
                    start_available=d(15), deadline=d(150))
    print(f"  [PJ2] 工事フェーズ: {pj2_work}")

    # --- Project3: 基礎工事 ---
    pj3_found = make(db, OWNER, "project3", "基礎工事", pj2_work,
                     priority=1, color="Cyan",
                     start_available=d(15), deadline=d(45))

    pj4_ground = make(db, OWNER, "project4", "地盤工事", pj3_found,
                      priority=1, color="Lime",
                      start_available=d(15), deadline=d(25))
    tk_ground = make_task(db, OWNER, "地盤改良工事", pj4_ground,
                          priority=1, color="Yellow",
                          start_available=d(15), deadline=d(25))
    make_ticket(db, OWNER, "地盤調査実施",   tk_ground, 2, 2.0,
                start_available=d(15), deadline=d(17),
                memo="スウェーデン式サウンディング試験。")
    make_ticket(db, OWNER, "改良工法選定",   tk_ground, 3, 1.0,
                start_available=d(17), deadline=d(19),
                memo="柱状改良工法を採用。")
    make_ticket(db, OWNER, "地盤改良施工",   tk_ground, 4, 16.0,
                start_available=d(19), deadline=d(25),
                memo="施工会社：山田基礎工業。工期3日。",
                assigned_to="id003")

    pj4_found_work = make(db, OWNER, "project4", "基礎施工", pj3_found,
                          priority=2, color="Lime",
                          start_available=d(26), deadline=d(45))
    tk_found_work = make_task(db, OWNER, "基礎コンクリート工事", pj4_found_work,
                              priority=1, color="Yellow",
                              start_available=d(26), deadline=d(45))
    make_ticket(db, OWNER, "根切り・砕石地業",       tk_found_work, 2, 8.0,
                start_available=d(26), deadline=d(29),
                assigned_to="id003")
    make_ticket(db, OWNER, "捨てコンクリート打設",   tk_found_work, 3, 4.0,
                start_available=d(29), deadline=d(31),
                assigned_to="id003")
    make_ticket(db, OWNER, "配筋工事",               tk_found_work, 4, 16.0,
                start_available=d(31), deadline=d(36),
                assigned_to="id003")
    make_ticket(db, OWNER, "型枠設置・コンクリート打設", tk_found_work, 5, 8.0,
                start_available=d(36), deadline=d(40),
                assigned_to="id003")
    make_ticket(db, OWNER, "養生・脱型・仕上げ",     tk_found_work, 6, 8.0,
                start_available=d(40), deadline=d(45),
                assigned_to="id003")

    # --- Project3: 躯体工事 ---
    pj3_frame = make(db, OWNER, "project3", "躯体工事", pj2_work,
                     priority=2, color="Cyan",
                     start_available=d(46), deadline=d(90))

    pj4_timber = make(db, OWNER, "project4", "木工事（建方）", pj3_frame,
                      priority=1, color="Lime",
                      start_available=d(46), deadline=d(70))
    tk_timber = make_task(db, OWNER, "上棟工事", pj4_timber,
                          priority=1, color="Yellow",
                          start_available=d(46), deadline=d(70))
    make_ticket(db, OWNER, "プレカット材発注確認",   tk_timber, 2, 2.0,
                start_available=d(46), deadline=d(48))
    make_ticket(db, OWNER, "資材搬入・荷降ろし",     tk_timber, 3, 8.0,
                start_available=d(55), deadline=d(57),
                assigned_to="id003")
    make_ticket(db, OWNER, "建方（柱・梁・小屋）",   tk_timber, 4, 24.0,
                start_available=d(57), deadline=d(62),
                assigned_to="id003",
                memo="クレーン使用。2日間で上棟予定。")
    make_ticket(db, OWNER, "上棟式準備・実施",       tk_timber, 5, 4.0,
                start_available=d(62), deadline=d(63),
                memo="近隣への挨拶・お礼品手配。")
    make_ticket(db, OWNER, "屋根工事（ルーフィング）", tk_timber, 6, 16.0,
                start_available=d(63), deadline=d(70),
                assigned_to="id003")

    pj4_wall = make(db, OWNER, "project4", "外壁・防水工事", pj3_frame,
                    priority=2, color="Lime",
                    start_available=d(71), deadline=d(90))
    tk_wall = make_task(db, OWNER, "外壁・防水施工", pj4_wall,
                        priority=1, color="Yellow",
                        start_available=d(71), deadline=d(90))
    make_ticket(db, OWNER, "透湿防水シート施工", tk_wall, 2, 8.0,
                start_available=d(71), deadline=d(74), assigned_to="id003")
    make_ticket(db, OWNER, "外壁サイディング施工", tk_wall, 3, 24.0,
                start_available=d(74), deadline=d(84), assigned_to="id003",
                memo="ニチハ モエンエクセラード16。色：プレミアムストーン調。")
    make_ticket(db, OWNER, "バルコニー防水処理",  tk_wall, 4, 8.0,
                start_available=d(84), deadline=d(90), assigned_to="id003")

    # --- Project3: 内装工事 ---
    pj3_interior = make(db, OWNER, "project3", "内装・設備工事", pj2_work,
                        priority=3, color="Cyan",
                        start_available=d(91), deadline=d(140))

    pj4_interior = make(db, OWNER, "project4", "内装仕上げ", pj3_interior,
                        priority=1, color="Lime",
                        start_available=d(91), deadline=d(120))
    tk_interior = make_task(db, OWNER, "内装仕上げ工事", pj4_interior,
                            priority=1, color="Yellow",
                            start_available=d(91), deadline=d(120))
    make_ticket(db, OWNER, "断熱材施工",    tk_interior, 2, 16.0,
                start_available=d(91), deadline=d(96), assigned_to="id003")
    make_ticket(db, OWNER, "石膏ボード貼り", tk_interior, 3, 24.0,
                start_available=d(96), deadline=d(105), assigned_to="id003")
    make_ticket(db, OWNER, "クロス（壁紙）貼り", tk_interior, 4, 16.0,
                start_available=d(105), deadline=d(112), assigned_to="id003")
    make_ticket(db, OWNER, "床材（フローリング）施工", tk_interior, 5, 16.0,
                start_available=d(105), deadline=d(113), assigned_to="id003")
    make_ticket(db, OWNER, "建具（ドア・引き戸）取付", tk_interior, 6, 8.0,
                start_available=d(113), deadline=d(118), assigned_to="id003")
    make_ticket(db, OWNER, "造作家具・棚板施工", tk_interior, 7, 8.0,
                start_available=d(113), deadline=d(120),
                memo="書斎カウンター、リビング収納棚。")

    pj4_equipment = make(db, OWNER, "project4", "設備工事", pj3_interior,
                         priority=2, color="Lime",
                         start_available=d(95), deadline=d(135))
    tk_equipment = make_task(db, OWNER, "各種設備設置", pj4_equipment,
                             priority=1, color="Yellow",
                             start_available=d(95), deadline=d(135))
    make_ticket(db, OWNER, "給排水配管工事",   tk_equipment, 2, 24.0,
                start_available=d(95), deadline=d(108), assigned_to="id002")
    make_ticket(db, OWNER, "電気配線工事",     tk_equipment, 3, 24.0,
                start_available=d(95), deadline=d(108), assigned_to="id002")
    make_ticket(db, OWNER, "キッチン設置",     tk_equipment, 4, 8.0,
                start_available=d(108), deadline=d(114), assigned_to="id002")
    make_ticket(db, OWNER, "ユニットバス設置", tk_equipment, 5, 4.0,
                start_available=d(108), deadline=d(114), assigned_to="id002")
    make_ticket(db, OWNER, "洗面台・トイレ設置", tk_equipment, 6, 4.0,
                start_available=d(110), deadline=d(116), assigned_to="id002")
    make_ticket(db, OWNER, "エアコン・換気設備設置", tk_equipment, 7, 8.0,
                start_available=d(120), deadline=d(128), assigned_to="id002")
    make_ticket(db, OWNER, "太陽光パネル設置", tk_equipment, 8, 8.0,
                start_available=d(125), deadline=d(135),
                memo="4.5kW システム。売電契約は引渡後。",
                assigned_to="id002")

    # ================================================================
    # Project2-D: 完工・引渡しフェーズ
    # ================================================================
    pj2_finish = make(db, OWNER, "project2", "完工・引渡しフェーズ", pj1,
                      priority=4, color="Teal",
                      start_available=d(141), deadline=d(165))
    print(f"  [PJ2] 完工・引渡しフェーズ: {pj2_finish}")

    pj3_inspect = make(db, OWNER, "project3", "検査・確認", pj2_finish,
                       priority=1, color="Cyan",
                       start_available=d(141), deadline=d(155))
    pj4_inspect = make(db, OWNER, "project4", "完了検査", pj3_inspect,
                       priority=1, color="Lime",
                       start_available=d(141), deadline=d(155))
    tk_inspect = make_task(db, OWNER, "各種検査対応", pj4_inspect,
                           priority=1, color="Yellow",
                           start_available=d(141), deadline=d(155))
    make_ticket(db, OWNER, "中間検査申請・立会い",   tk_inspect, 2, 2.0,
                start_available=d(141), deadline=d(145),
                memo="基礎配筋検査・躯体検査が対象。")
    make_ticket(db, OWNER, "完了検査申請",           tk_inspect, 3, 1.0,
                start_available=d(148), deadline=d(150))
    make_ticket(db, OWNER, "完了検査立会い・合格",   tk_inspect, 4, 2.0,
                start_available=d(152), deadline=d(155))
    make_ticket(db, OWNER, "検査済証受領",           tk_inspect, 5, 0.5,
                start_available=d(155), deadline=d(157))

    pj3_delivery = make(db, OWNER, "project3", "引渡し", pj2_finish,
                        priority=2, color="Cyan",
                        start_available=d(156), deadline=d(165))
    pj4_delivery = make(db, OWNER, "project4", "引渡し手続き", pj3_delivery,
                        priority=1, color="Lime",
                        start_available=d(156), deadline=d(165))
    tk_delivery = make_task(db, OWNER, "引渡し準備・完了", pj4_delivery,
                            priority=1, color="Yellow",
                            start_available=d(156), deadline=d(165))
    make_ticket(db, OWNER, "施主検査（竣工確認）", tk_delivery, 2, 4.0,
                start_available=d(156), deadline=d(159),
                memo="不具合リストを作成して是正依頼する。")
    make_ticket(db, OWNER, "是正工事対応",          tk_delivery, 3, 8.0,
                start_available=d(159), deadline=d(163))
    make_ticket(db, OWNER, "鍵・書類の受領",        tk_delivery, 4, 2.0,
                start_available=d(163), deadline=d(164),
                memo="検査済証・設備保証書・図面一式。")
    make_ticket(db, OWNER, "引渡し式・入居",        tk_delivery, 5, 2.0,
                start_available=d(164), deadline=d(165),
                memo="引越し日程は別途調整。")

    # ================================================================
    # 定期作業（regularly）
    # ================================================================
    # 週次進捗確認ミーティング（工事フェーズ中）
    pj4_meeting_found = make(db, OWNER, "project4", "定期ミーティング（基礎）", pj3_found,
                             priority=99, color="Gray")
    tk_meeting_found = make_task(db, OWNER, "週次進捗確認", pj4_meeting_found,
                                 priority=99, color="Orange",
                                 start_available=d(15), deadline=d(45))
    make_ticket(db, OWNER, "週次進捗確認ミーティング", tk_meeting_found, 98, 1.0,
                status="regularly", memo="毎週月曜10:00〜。施工会社・設計士・施主。",
                color="Orange")

    # ================================================================
    # 日次スケジュール・ログのサンプル
    # ================================================================
    _add_sample_schedules(db)

    # ================================================================
    # メモ
    # ================================================================
    db.save_memo("id001",
                 "【工事会社連絡先】\n"
                 "・施工管理：鈴木 090-xxxx-xxxx\n"
                 "・設計担当：田中 080-xxxx-xxxx\n\n"
                 "【重要リンク】\n"
                 "・設計図面（社内サーバー）: \\\\server\\share\\house_plan\\\n"
                 "・見積書: \\\\server\\share\\house_plan\\estimate.xlsx\n\n"
                 "【メモ】\n"
                 "外構工事は引渡し後に別発注の予定。予算は200万円。")

    # ================================================================
    # 最終確認
    # ================================================================
    df = db.read_nodes()
    print(f"\n=== 作成完了 ===")
    print(f"総ノード数: {len(df)}")
    for nt in ["project1", "project2", "project3", "project4", "task", "ticket"]:
        cnt = len(df[df["node_type"] == nt])
        print(f"  {nt:12s}: {cnt:3d} 件")

    done_cnt = len(df[df["status"] == "done"])
    todo_cnt = len(df[df["status"] == "todo"])
    reg_cnt  = len(df[df["status"] == "regularly"])
    print(f"\nステータス別: done={done_cnt}, todo={todo_cnt}, regularly={reg_cnt}")
    print(f"\nDBファイル: {DB_DIR / 'schedule.sqlite'}")


def _add_sample_schedules(db: Database) -> None:
    """過去3日分のサンプル日次スケジュール・ログを追加する"""
    import pandas as pd
    from db import DAILY_SCH_COLS, DAILY_LOG_COLS, daily_sch_idx

    # 対象日付と担当者
    schedule_data = [
        # (日付オフセット, メンバー, 作業スロット開始インデックス, 終了インデックス)
        (d(-2), "id001", "C0900", "C1700"),
        (d(-2), "id002", "C0830", "C1730"),
        (d(-1), "id001", "C0900", "C1800"),
        (d(-1), "id003", "C0800", "C1700"),
        (d(0),  "id001", "C0900", "C1700"),
    ]

    # チケットIDX（日次スケジュールに割り当てる）
    df_nodes = db.read_nodes()
    tickets = df_nodes[df_nodes["node_type"] == "ticket"].copy()
    # done以外のチケットから適当に選ぶ
    active_tickets = tickets[~tickets["status"].isin(["done", "cancel", "deleted"])].head(5)
    ticket_idxs = list(active_tickets.index)
    if not ticket_idxs:
        # done含め全チケットから選ぶ
        ticket_idxs = list(tickets.head(5).index)
    if not ticket_idxs:
        return

    # スロット一覧
    begin_idx = DAILY_TIME_COLS.index("C0800")
    end_idx   = DAILY_TIME_COLS.index("C1800")
    work_slots = DAILY_TIME_COLS[begin_idx:end_idx]

    for date_str, member, slot_from, slot_to in schedule_data:
        idx = daily_sch_idx(date_str, member)
        row = {c: "" for c in DAILY_SCH_COLS[1:]}
        row["Owner"] = member
        row["Last_Update"] = date_str

        # スロットを割り当て（チケットをローテーション）
        try:
            s_idx = DAILY_TIME_COLS.index(slot_from)
            e_idx = DAILY_TIME_COLS.index(slot_to)
        except ValueError:
            continue

        total_slots = 0
        t_rot = 0
        first_slot = None
        last_slot  = None

        for i, col in enumerate(DAILY_TIME_COLS[s_idx:e_idx]):
            abs_i = s_idx + i
            # 12:00〜13:00 は昼休み（空スロット）
            noon_s = DAILY_TIME_COLS.index("C1200")
            noon_e = DAILY_TIME_COLS.index("C1300")
            if noon_s <= abs_i < noon_e:
                continue
            # チケットを割り当て
            t_idx_str = ticket_idxs[t_rot % len(ticket_idxs)]
            row[col] = t_idx_str
            total_slots += 1
            if first_slot is None:
                first_slot = col
            last_slot = col
            if (abs_i - s_idx) % 8 == 7:  # 2時間ごとにローテーション
                t_rot += 1

        # 集計列
        row["CTOTAL"] = round(total_slots * 0.25, 2)
        row["CFROM"]  = first_slot or ""
        try:
            to_i = DAILY_TIME_COLS.index(last_slot) + 1
            row["CTO"] = DAILY_TIME_COLS[to_i] if last_slot and to_i < len(DAILY_TIME_COLS) else (last_slot or "")
        except (ValueError, TypeError):
            row["CTO"] = last_slot or ""
        row["CBREAK"] = 1.0  # 昼休み1h

        ds = pd.Series(row, name=idx)
        df_sch = pd.DataFrame([ds])
        df_sch.index.name = "IDX"
        db.save_daily_schedule(df_sch, member)

    # 日次ログ
    log_data = [
        (d(-2), "id001", "Good",  "Office", "OK", "No",  "基礎工事の現場確認を実施しました。"),
        (d(-2), "id002", "Good",  "Home",   "OK", "No",  "給排水図面の最終確認中。"),
        (d(-2), "id003", "Good",  "Office", "OK", "Yes", "地盤改良工事の立会い。残業で報告書作成。"),
        (d(-1), "id001", "Good",  "Office", "OK", "No",  "確認申請の書類を提出しました。"),
        (d(-1), "id002", "Bad",   "Home",   "OK", "No",  "体調不良のため在宅勤務。"),
        (d(-1), "id003", "Good",  "Office", "OK", "No",  "捨てコン打設完了。"),
        (d(0),  "id001", "Good",  "Office", "OK", "No",  "確認済証待ち。申請番号: R8-00123。"),
        (d(0),  "id002", "Good",  "Office", "OK", "No",  ""),
    ]

    import pandas as pd
    for date_str, member, health, place, safety, ow, notes in log_data:
        idx = daily_sch_idx(date_str, member)
        row = {
            "Owner":         member,
            "health_status": health,
            "work_place":    place,
            "safety":        safety,
            "overwork":      ow,
            "notes":         notes,
            "Last_Update":   date_str,
        }
        ds = pd.Series(row, name=idx)
        df_log = pd.DataFrame([ds])
        df_log.index.name = "IDX"
        db.save_daily_log(df_log, member)

    print("日次スケジュール・ログ追加完了")


if __name__ == "__main__":
    main()
