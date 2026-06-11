"""
db.py - データベース接続・CRUD・工数集計・IDX生成
"""
import sqlite3
import hashlib
import random
import datetime
import time
from pathlib import Path

import pandas as pd
import math
import datetime

# --- 定数 ---
# ノード種別の一覧（Project1〜4の階層構造 + タスク + チケット）
NODE_TYPES = ["project1", "project2", "project3", "project4", "task", "ticket"]
# ステータスの選択肢（deleted は論理削除用）
STATUS_LIST = ["todo", "done", "cancel", "regularly", "deleted"]
# 各ノード種別の直下子種別マッピング（Ticketはキー無し → 子作成不可）
CHILD_TYPE = {
    "project1": "project2",
    "project2": "project3",
    "project3": "project4",
    "project4": "task",
    "task": "ticket",
}
NODE_COLUMNS = [
    "IDX", "node_type", "parent_id", "title", "status", "priority",
    "assigned_to", "estimated_hours", "actual_hours",
    "deadline", "start_available", "actual_start", "actual_end",
    "memo", "link", "color", "created_at", "updated_at",
]
# daily_schedule の時間スロット列名（C0000〜C2345 の 96 列、15分刻み）
DAILY_TIME_COLS = [f"C{i // 4:02d}{(i % 4) * 15:02d}" for i in range(24 * 4)]
DAILY_SCH_COLS = (["IDX", "Owner"] + DAILY_TIME_COLS
                  + ["CTOTAL", "CFROM", "CTO", "CBREAK", "Last_Update"])
DAILY_LOG_COLS = [
    "IDX", "Owner", "health_status", "work_place",
    "safety", "overwork", "notes", "Last_Update",
]
COLOR_OPTIONS = {
    "Cyan":   "#00BCD4",
    "Red":    "#EF5350",
    "Green":  "#66BB6A",
    "Blue":   "#42A5F5",
    "Yellow": "#FFA726",
    "Orange": "#FF7043",
    "Purple": "#AB47BC",
    "Pink":   "#EC407A",
    "Teal":   "#26A69A",
    "Lime":   "#9CCC65",
    "Brown":  "#8D6E63",
    "Gray":   "#BDBDBD",
}
DEFAULT_COLORS_BY_TYPE = {
    "project1": "Blue",
    "project2": "Teal",
    "project3": "Cyan",
    "project4": "Lime",
    "task":     "Yellow",
    "ticket":   "Cyan",
}


# --- IDX ユーティリティ ---
def generate_idx(owner: str) -> str:
    """YYMMDD_HH + MD5[:6] 形式の TEXT IDX を生成する。重複しにくいランダム文字列を付与。"""
    n = datetime.datetime.now().strftime("%y%m%d%H")
    rn = str(random.randint(0, 99999))
    suffix = hashlib.md5((rn + owner).encode()).hexdigest()[:6]
    return f"{n[:6]}_{n[6:]}{suffix}"


def daily_sch_idx(date_str: str, username: str) -> str:
    """daily_schedule / daily_log の IDX を生成する（例: '2026-03-16-User'）"""
    return f"{date_str}-{username}"


def build_auto_children(parent_ds: pd.Series, owner: str) -> list:
    """
    親ノード作成時に「詳細作成」「完了」チケットを自動生成して返す（DB 書き込みなし）。
    遅延保存（Ctrl+S 保存）に対応するため、DB への書き込みを行わない。
    create_auto_children の代替として TreePane._on_new で使用する。
    """
    children = []
    for i, title in enumerate(["詳細作成", "完了"]):
        child = create_initial_node(
            owner=owner,
            node_type="ticket",
            title=title,
            parent_id=parent_ds.name,
            priority=i + 1,
            color=parent_ds.get("color", "Cyan"),
        )
        children.append(child)
    return children


def create_initial_node(owner: str, node_type: str, title: str,
                        parent_id: str = "0", priority: int = 99,
                        color: str = "") -> pd.Series:
    """新規ノードのデフォルト pd.Series を生成する"""
    today = datetime.date.today().isoformat()
    idx = generate_idx(owner)
    if not color:
        color = DEFAULT_COLORS_BY_TYPE.get(node_type, "Cyan")
    data = {
        "node_type":        node_type,
        "parent_id":        parent_id,
        "title":            title,
        "status":           "todo",
        "priority":         priority,
        "assigned_to":      owner,
        "estimated_hours":  0.0,
        "actual_hours":     0.0,
        "deadline":         None,
        "start_available":  None,
        "actual_start":     None,
        "actual_end":       None,
        "memo":             "",
        "link":             "",
        "color":            color,
        "created_at":       today,
        "updated_at":       today,
    }
    return pd.Series(data, name=idx)


# --- スキーマ SQL ---
_SCHEMA_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    IDX             TEXT PRIMARY KEY,
    node_type       TEXT NOT NULL DEFAULT '',
    parent_id       TEXT NOT NULL DEFAULT '0',
    title           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'todo',
    priority        INTEGER NOT NULL DEFAULT 99,
    assigned_to     TEXT NOT NULL DEFAULT '',
    estimated_hours REAL DEFAULT 0.0,
    actual_hours    REAL DEFAULT 0.0,
    deadline        TEXT,
    start_available TEXT,
    actual_start    TEXT,
    actual_end      TEXT,
    memo            TEXT DEFAULT '',
    link            TEXT DEFAULT '',
    color           TEXT DEFAULT 'Cyan',
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT ''
);
"""

_SCHEMA_DAILY_LOG = """
CREATE TABLE IF NOT EXISTS daily_log (
    IDX           TEXT PRIMARY KEY,
    Owner         TEXT NOT NULL DEFAULT '',
    health_status TEXT DEFAULT '',
    work_place    TEXT DEFAULT '',
    safety        TEXT DEFAULT '',
    overwork      TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    Last_Update   TEXT DEFAULT ''
);
"""

_SCHEMA_ASSIGNMENTS = """
CREATE TABLE IF NOT EXISTS task_assignments (
    IDX          TEXT PRIMARY KEY,
    ticket_id    TEXT NOT NULL DEFAULT '',
    from_user    TEXT NOT NULL DEFAULT '',
    to_user      TEXT NOT NULL DEFAULT '',
    status       TEXT DEFAULT 'pending',
    message      TEXT DEFAULT '',
    created_at   TEXT DEFAULT '',
    responded_at TEXT DEFAULT ''
);
"""

_SCHEMA_MEMO = """
CREATE TABLE IF NOT EXISTS memo (
    username   TEXT PRIMARY KEY,
    content    TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
"""

_SCHEMA_PERMANENT_NOTICES = """
CREATE TABLE IF NOT EXISTS permanent_notices (
    username   TEXT PRIMARY KEY,
    text       TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
"""

# 進捗スナップショット（月次進捗レポートの推移グラフ用に日次で記録）
_SCHEMA_PROGRESS_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS progress_snapshots (
    snap_date       TEXT NOT NULL,
    node_idx        TEXT NOT NULL,
    done_count      INTEGER DEFAULT 0,
    total_count     INTEGER DEFAULT 0,
    actual_hours    REAL DEFAULT 0.0,
    estimated_hours REAL DEFAULT 0.0,
    PRIMARY KEY (snap_date, node_idx)
);
"""


def _daily_schedule_create_sql() -> str:
    """15分スロット96列 + 集計列を持つ daily_schedule テーブルの CREATE 文を返す"""
    cols = ["IDX TEXT PRIMARY KEY", "Owner TEXT NOT NULL DEFAULT ''"]
    cols += [f"{c} TEXT DEFAULT ''" for c in DAILY_TIME_COLS]
    cols += [
        "CTOTAL REAL DEFAULT 0",
        "CFROM  TEXT DEFAULT ''",
        "CTO    TEXT DEFAULT ''",
        "CBREAK REAL DEFAULT 0",
        "Last_Update TEXT DEFAULT ''",
    ]
    return f"CREATE TABLE IF NOT EXISTS daily_schedule ({', '.join(cols)});"


def _to_sql_value(v):
    """pandas / numpy の値をSQLiteに安全なPython標準型へ変換する"""
    # pandasの欠損
    if pd.isna(v):
        return None
    
    # numpy / pandas scalar -> Python scalar
    if hasattr(v, "item"):
        try:
            v = v.item()
        except Exception:
            pass
    
    # 日付系は文字列化 
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    
    # 許可する基本型
    if isinstance(v, (str, int, float, bytes, type(None))):
        # Nan対策
        if isinstance(v, float) and math.isnan(v):
            return None
        return v
    
    # それ以外は文字列化
    return str(v)


class Database:
    """SQLite 接続・CRUD・工数集計を担当するクラス"""

    def __init__(self, db_dir: str, logger=None, timeout: int = 60):
        self.db_dir = Path(db_dir)
        self.logger = logger
        self.timeout = timeout  # SQLite ロック待機秒数（config.ini の db_timeout）
        self.db_path = self.db_dir / "schedule.sqlite"
        self._ensure_db()

    # ---------- 内部ヘルパー ----------

    def _log(self, msg: str) -> None:
        if self.logger:
            self.logger.debug(msg)

    def _logi(self, msg: str) -> None:
        if self.logger:
            self.logger.info(msg)

    def _logw(self, msg: str) -> None:
        if self.logger:
            self.logger.warning(msg)

    # ロックファイルの有効期限（秒）
    _LOCK_EXPIRE_SEC = 300

    def _connect(self) -> sqlite3.Connection:
        """ファイルサーバー向け設定で SQLite 接続を開く"""
        conn = sqlite3.connect(str(self.db_path), timeout=float(self.timeout))
        # WAL はファイルサーバー非推奨のため DELETE モードで運用
        conn.execute("PRAGMA journal_mode=DELETE")
        # ビジー待機（ミリ秒）: Python の timeout と二重で保護
        conn.execute(f"PRAGMA busy_timeout={self.timeout * 1000}")
        # ファイルサーバーではOSキャッシュが信頼できないため FULL に設定
        conn.execute("PRAGMA synchronous=FULL")
        conn.row_factory = sqlite3.Row
        return conn

    def acquire_lock(self) -> bool:
        """保存用ロックファイルを取得する。5分以上古いロックは無効とみなす。"""
        lock_path = self.db_dir / "schedule.lock"
        now = time.time()
        if lock_path.exists():
            try:
                lock_time = float(lock_path.read_text().strip())
                elapsed = now - lock_time
                if elapsed < self._LOCK_EXPIRE_SEC:
                    self._logw(f"[DB] ロック取得失敗 - 他ユーザーが保存中 (ロック作成から{elapsed:.0f}秒)")
                    return False
                # 有効期限切れは強制解除して取得
                self._logw(f"[DB] 期限切れロックを強制解除して取得 (経過={elapsed:.0f}秒)")
            except Exception:
                self._logw("[DB] ロックファイル読み取り失敗 - 強制上書き")
        try:
            lock_path.write_text(str(now))
        except Exception as e:
            self._log(f"ロックファイル作成失敗: {e}")
            return False
        return True

    def release_lock(self) -> None:
        """保存用ロックファイルを解放する"""
        lock_path = self.db_dir / "schedule.lock"
        try:
            lock_path.unlink(missing_ok=True)
        except Exception as e:
            self._log(f"ロックファイル削除失敗: {e}")

    def _ensure_db(self) -> None:
        """DB ファイルとテーブルが存在しない場合に初期化する"""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(_SCHEMA_NODES)
            conn.execute(_daily_schedule_create_sql())
            conn.execute(_SCHEMA_DAILY_LOG)
            conn.execute(_SCHEMA_ASSIGNMENTS)
            conn.execute(_SCHEMA_MEMO)
            conn.execute(_SCHEMA_PERMANENT_NOTICES)
            conn.execute(_SCHEMA_PROGRESS_SNAPSHOTS)
            conn.commit()
            # 既存 DB へのマイグレーション: link 列が無ければ追加
            cols = {r[1] for r in conn.execute("PRAGMA table_info(nodes)").fetchall()}
            if "link" not in cols:
                conn.execute("ALTER TABLE nodes ADD COLUMN link TEXT DEFAULT ''")
                conn.commit()
                self._logi("[DB] マイグレーション: nodes.link 列を追加")
            self._log(f"DB 初期化完了: {self.db_path}")
        except Exception as e:
            self._log(f"DB 初期化エラー: {e}")
        finally:
            conn.close()

    def _read_df(self, sql: str, index_col: str = None,
                 params: list = None) -> pd.DataFrame:
        """SQL を実行して DataFrame を返す汎用ヘルパー。エラー時は空 DataFrame。"""
        conn = self._connect()
        try:
            df = pd.read_sql_query(sql, conn, params=params or [])
            if index_col and not df.empty and index_col in df.columns:
                df.set_index(index_col, inplace=True)
            return df
        except Exception as e:
            self._log(f"read_df エラー ({sql[:50]}): {e}")
            return pd.DataFrame()
        finally:
            conn.close()

    def _empty_nodes_df(self) -> pd.DataFrame:
        df = pd.DataFrame(columns=[c for c in NODE_COLUMNS if c != "IDX"])
        df.index.name = "IDX"
        return df

    def _empty_daily_df(self) -> pd.DataFrame:
        df = pd.DataFrame(columns=DAILY_SCH_COLS[1:])
        df.index.name = "IDX"
        return df

    # ---------- nodes ----------

    def read_nodes(self, include_deleted: bool = False) -> pd.DataFrame:
        """全ノードを DataFrame で返す。IDX がインデックス"""
        sql = "SELECT * FROM nodes"
        if not include_deleted:
            sql += " WHERE status != 'deleted'"
        df = self._read_df(sql, index_col="IDX")
        if df.empty:
            return self._empty_nodes_df()
        # 数値列の型を正規化（SQLite から bytes/object で返る場合に対応）
        for col in ("priority",):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(99).astype(int)
        for col in ("estimated_hours", "actual_hours"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        return df

    def upsert_node(self, ds: pd.Series) -> None:
        """1 件のノードを upsert する（DELETE + INSERT で最新データに上書き）"""
        ds = ds.copy()
        ds["updated_at"] = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            # 既存行を一度削除してから再挿入（SQLite の UPSERT の代替）
            conn.execute("DELETE FROM nodes WHERE IDX=?", [ds.name])
            row = {c: _to_sql_value(ds.get(c, None)) for c in NODE_COLUMNS[1:]}
            row_full = {"IDX": _to_sql_value(ds.name), **row}
            cols = list(row_full.keys())
            vals = list(row_full.values())
            conn.execute(
                f"INSERT INTO nodes ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
                vals,
            )
            conn.commit()
            self._log(f"upsert_node: {ds.name} ({ds.get('title', '')})")
        except Exception as e:
            self._log(f"upsert_node エラー: {e}")
        finally:
            conn.close()

    def upsert_nodes_bulk(self, series_list: list) -> None:
        """複数ノードを 1 トランザクションで一括 upsert する（_normalize_priorities 等に使用）"""
        if not series_list:
            return
        today = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            for ds in series_list:
                ds = ds.copy()
                ds["updated_at"] = today
                conn.execute("DELETE FROM nodes WHERE IDX=?", [ds.name])
                row = {c: _to_sql_value(ds.get(c, None)) for c in NODE_COLUMNS[1:]}
                row_full = {"IDX": _to_sql_value(ds.name), **row}
                cols = list(row_full.keys())
                vals = list(row_full.values())
                conn.execute(
                    f"INSERT INTO nodes ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
                    vals,
                )
            conn.commit()
            self._log(f"upsert_nodes_bulk: {len(series_list)} 件")
        except Exception as e:
            self._log(f"upsert_nodes_bulk エラー: {e}")
        finally:
            conn.close()

    def save_nodes(self, df: pd.DataFrame, user: str) -> None:
        """ユーザー自身が担当するノードを DB に保存する"""
        if df.empty:
            return
        # 自分が担当するノードのみ保存対象とする（2日フィルター廃止）
        mask = df.get("assigned_to", pd.Series(dtype=str)) == user
        target = df[mask]
        if target.empty:
            return
        conn = self._connect()
        try:
            for idx in target.index:
                conn.execute("DELETE FROM nodes WHERE IDX=?", [idx])
                row = {c: _to_sql_value(target.loc[idx, c] if c in target.columns else None)
                       for c in NODE_COLUMNS[1:]}
                row_full = {"IDX": _to_sql_value(idx), **row}
                cols = list(row_full.keys())
                vals = list(row_full.values())
                conn.execute(
                    f"INSERT INTO nodes ({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
                    vals,
                )
            conn.commit()
            self._logi(f"[DB] save_nodes: {len(target)} 件保存 user={user}")
        except Exception as e:
            self._log(f"save_nodes エラー: {e}")
        finally:
            conn.close()

    # ---------- daily_schedule ----------

    def read_daily_schedule(self) -> pd.DataFrame:
        """全ユーザー・全日付の daily_schedule を DataFrame で返す。IDX がインデックス。"""
        df = self._read_df("SELECT * FROM daily_schedule", index_col="IDX")
        if df.empty:
            return self._empty_daily_df()
        return df

    def save_daily_schedule(self, df: pd.DataFrame, user: str) -> None:
        """指定ユーザーの直近 2 日以内のスケジュール行を DB に保存する"""
        if df.empty:
            return
        threshold = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
        if "Owner" not in df.columns:
            return
        # 自分のデータかつ直近 2 日以内のレコードのみ保存
        target = df[df["Owner"] == user].copy()
        if "Last_Update" in target.columns:
            target = target[target["Last_Update"] >= threshold]
        if target.empty:
            return
        conn = self._connect()
        try:
            for idx in target.index:
                conn.execute("DELETE FROM daily_schedule WHERE IDX=?", [idx])
                vals = [idx] + [
                    target.loc[idx, c] if c in target.columns else ""
                    for c in DAILY_SCH_COLS[1:]
                ]
                conn.execute(
                    f"INSERT INTO daily_schedule ({','.join(DAILY_SCH_COLS)})"
                    f" VALUES ({','.join(['?'] * len(DAILY_SCH_COLS))})",
                    vals,
                )
            conn.commit()
            self._logi(f"[DB] save_daily_schedule: {len(target)} 件保存 user={user}")
        except Exception as e:
            self._log(f"save_daily_schedule エラー: {e}")
        finally:
            conn.close()

    # ---------- daily_log ----------

    def read_daily_log(self) -> pd.DataFrame:
        df = self._read_df("SELECT * FROM daily_log", index_col="IDX")
        if df.empty:
            cols = [c for c in DAILY_LOG_COLS if c != "IDX"]
            empty = pd.DataFrame(columns=cols)
            empty.index.name = "IDX"
            return empty
        return df

    def save_daily_log(self, df: pd.DataFrame, user: str) -> None:
        if df.empty or "Owner" not in df.columns:
            return
        target = df[df["Owner"] == user].copy()
        if target.empty:
            return
        conn = self._connect()
        try:
            for idx in target.index:
                conn.execute("DELETE FROM daily_log WHERE IDX=?", [idx])
                vals = [idx] + [
                    target.loc[idx, c] if c in target.columns else ""
                    for c in DAILY_LOG_COLS[1:]
                ]
                conn.execute(
                    f"INSERT INTO daily_log ({','.join(DAILY_LOG_COLS)})"
                    f" VALUES ({','.join(['?'] * len(DAILY_LOG_COLS))})",
                    vals,
                )
            conn.commit()
            self._logi(f"[DB] save_daily_log: {len(target)} 件保存 user={user}")
        except Exception as e:
            self._log(f"save_daily_log エラー: {e}")
        finally:
            conn.close()

    # ---------- task_assignments ----------

    def read_assignments(self) -> pd.DataFrame:
        df = self._read_df("SELECT * FROM task_assignments", index_col="IDX")
        if df.empty:
            cols = ["ticket_id", "from_user", "to_user", "status",
                    "message", "created_at", "responded_at"]
            empty = pd.DataFrame(columns=cols)
            empty.index.name = "IDX"
            return empty
        return df

    def create_assignment(self, ticket_id: str, from_user: str,
                          to_user: str, message: str) -> None:
        """チケット依頼レコードを新規作成する（初期ステータス: pending）"""
        idx = generate_idx(from_user)
        today = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO task_assignments"
                " (IDX,ticket_id,from_user,to_user,status,message,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                [idx, ticket_id, from_user, to_user, "pending", message, today],
            )
            conn.commit()
        finally:
            conn.close()

    def respond_assignment(self, assignment_idx: str, response: str) -> None:
        """依頼に対する応答（accept/reject 等）を記録し responded_at を更新する"""
        today = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            # ステータスと応答日時を一括更新
            conn.execute(
                "UPDATE task_assignments SET status=?, responded_at=? WHERE IDX=?",
                [response, today, assignment_idx],
            )
            conn.commit()
        finally:
            conn.close()

    def respond_assignments_bulk(self, assignment_idxs: list, response: str) -> None:
        """複数の依頼に対する応答を 1 トランザクションで一括記録する"""
        if not assignment_idxs:
            return
        today = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            for asgn_idx in assignment_idxs:
                conn.execute(
                    "UPDATE task_assignments SET status=?, responded_at=? WHERE IDX=?",
                    [response, today, asgn_idx],
                )
            conn.commit()
            self._log(f"respond_assignments_bulk: {len(assignment_idxs)} 件 → {response}")
        except Exception as e:
            self._log(f"respond_assignments_bulk エラー: {e}")
        finally:
            conn.close()

    # ---------- memo ----------

    def read_memo(self, username: str) -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT content FROM memo WHERE username=?", [username]
            ).fetchone()
            return row[0] if row else ""
        finally:
            conn.close()

    def save_memo(self, username: str, content: str) -> None:
        """ユーザーのメモを保存する（INSERT OR REPLACE で既存行を上書き）"""
        today = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO memo (username, content, updated_at) VALUES (?,?,?)",
                [username, content, today],
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- permanent_notices ----------

    def read_permanent_notice(self, username: str) -> str:
        """常時表示メモを取得する"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT text FROM permanent_notices WHERE username=?", [username]
            ).fetchone()
            return row[0] if row else ""
        finally:
            conn.close()

    def read_all_permanent_notices(self) -> dict:
        """全ユーザーの常時表示メモを {username: text} の辞書で返す"""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT username, text FROM permanent_notices").fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            conn.close()

    def save_permanent_notice(self, username: str, text: str) -> None:
        """常時表示メモを保存する"""
        today = datetime.date.today().isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO permanent_notices (username, text, updated_at)"
                " VALUES (?,?,?)",
                [username, text, today],
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- progress_snapshots ----------

    def save_progress_snapshots(self, rows: list, snap_date: str = None) -> int:
        """
        進捗スナップショットを記録する（1日1回想定）。
        同一 (snap_date, node_idx) は INSERT OR IGNORE でスキップし、新規記録件数を返す。
        rows: {"node_idx", "done_count", "total_count", "actual_hours",
               "estimated_hours"} の辞書リスト（logic.build_progress_snapshot_rows で生成）
        """
        if not rows:
            return 0
        snap_date = snap_date or datetime.date.today().isoformat()
        conn = self._connect()
        saved = 0
        try:
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO progress_snapshots"
                    " (snap_date, node_idx, done_count, total_count,"
                    "  actual_hours, estimated_hours) VALUES (?,?,?,?,?,?)",
                    [snap_date, r["node_idx"], int(r["done_count"]),
                     int(r["total_count"]), float(r["actual_hours"]),
                     float(r["estimated_hours"])],
                )
                saved += cur.rowcount
            conn.commit()
            if saved:
                self._log(f"save_progress_snapshots: {saved} 件 ({snap_date})")
        except Exception as e:
            self._log(f"save_progress_snapshots エラー: {e}")
        finally:
            conn.close()
        return saved

    def read_progress_snapshots(self, node_idx: str, date_from: str = "",
                                date_to: str = "") -> pd.DataFrame:
        """指定ノードのスナップショットを日付昇順の DataFrame で返す"""
        sql = "SELECT * FROM progress_snapshots WHERE node_idx=?"
        params = [node_idx]
        if date_from:
            sql += " AND snap_date>=?"
            params.append(date_from)
        if date_to:
            sql += " AND snap_date<=?"
            params.append(date_to)
        sql += " ORDER BY snap_date"
        return self._read_df(sql, params=params)

    # ---------- actual_hours 再集計 ----------

    def recalc_actual_hours(self, df_nodes: pd.DataFrame,
                            df_daily: pd.DataFrame) -> pd.DataFrame:
        """
        daily_schedule の割り当てから Ticket の actual_hours を集計し、
        Task → Project4 → ... → Project1 へ伝播する。
        """
        if df_nodes.empty:
            return df_nodes
        df = df_nodes.copy()

        # Ticket の実績工数をいったんゼロリセット（全スロットから再集計するため）
        ticket_mask = df["node_type"] == "ticket"
        df.loc[ticket_mask, "actual_hours"] = 0.0

        # 各スロット（15分=0.25h）からチケットの actual_hours を積算
        if not df_daily.empty:
            for row_idx in df_daily.index:
                for col in DAILY_TIME_COLS:
                    if col not in df_daily.columns:
                        continue
                    t_idx = df_daily.loc[row_idx, col]
                    if t_idx and t_idx in df.index:
                        if df.loc[t_idx, "status"] not in ("cancel", "deleted"):
                            df.loc[t_idx, "actual_hours"] = (
                                df.loc[t_idx, "actual_hours"] + 0.25
                            )

        # Task 以上に集計（下位から上位へバブルアップ）
        for nt in ["task", "project4", "project3", "project2", "project1"]:
            for idx in df[df["node_type"] == nt].index:
                children = df[
                    (df["parent_id"] == idx)
                    & (~df["status"].isin(["cancel", "deleted"]))
                ]
                df.loc[idx, "actual_hours"] = float(children["actual_hours"].sum())
        return df

    # ---------- 自動子チケット生成 ----------

    def create_auto_children(self, parent_ds: pd.Series, owner: str) -> None:
        """
        親ノード作成時に「詳細作成」「完了」チケットを自動生成する（spec 2.2）。
        """
        for i, title in enumerate(["詳細作成", "完了"]):
            child = create_initial_node(
                owner=owner,
                node_type="ticket",
                title=title,
                parent_id=parent_ds.name,
                priority=i + 1,
                color=parent_ds.get("color", "Cyan"),
            )
            self.upsert_node(child)
        self._log(f"自動チケット生成: {parent_ds.name} ({parent_ds.get('title', '')})")
