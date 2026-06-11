"""
schedule_app.py - エントリポイント・設定・ロギング・起動

アプリケーションの起動フロー:
  1. setup_logger() でログ設定
  2. load_config() で config.ini を読み込み AppConfig 生成
  3. OSのログイン名を取得して username に設定
  4. Database 初期化
  5. AppState 初期化・データロード
  6. Qt アプリケーション・MainWindow を生成して表示

コマンドライン引数:
  --debug  デバッグモード: OSログイン名を yamada の担当者IDとして扱い、
           yamada 担当のアイテムを自分のアイテムとして表示する
"""
import argparse
import configparser
import datetime
import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# -------------------------------------------------------
# 定数
# -------------------------------------------------------
APP_NAME    = "Schedule Manager v3"
APP_VERSION = "3.0.0"
CONFIG_FILE      = Path(__file__).parent / "config.ini"
USER_CONFIG_FILE = Path(__file__).parent.parent / "user_config.ini"
LOG_DIR          = Path(__file__).parent / "logs"


# -------------------------------------------------------
# AppConfig - config.ini の内容を保持
# -------------------------------------------------------
@dataclass
class AppConfig:
    # [Database]
    server_dir: str = "./db"
    db_timeout: int = 60  # SQLite ロック待機秒数（timeout / busy_timeout 共通）

    # [User]
    # username は config.ini ではなく OS のログイン名から自動取得する
    username: str = ""
    members: List[str] = field(default_factory=lambda: [])

    # [GUI]
    window_width: int = 1500
    window_height: int = 900
    font_size: int = 9

    # [Schedule]
    daily_begin_time: int = 6
    daily_end_time: int = 21
    daily_task_hour: float = 5.0
    holidays: List[str] = field(default_factory=lambda: ["SUN", "SAT"])

    # [DailyInfoCombo]
    health_options: List[str] = field(default_factory=lambda: ["Good", "Bad"])
    work_place_options: List[str] = field(default_factory=lambda: ["Office", "Home"])
    safety_options: List[str] = field(default_factory=lambda: ["OK", "NG"])
    overwork_options: List[str] = field(default_factory=lambda: ["No", "Yes"])

    # [Report]
    # 週報・月報などの Markdown 出力先フォルダ（空の場合は保存時にダイアログで選択）
    report_output_dir: str = ""

    # [DisplayNames] - メールアドレス → 表示名 のマッピング
    display_names: Dict[str, str] = field(default_factory=dict)

    # [Commands]
    commands: List[Dict[str, str]] = field(default_factory=list)

    def get_display_name(self, email: str) -> str:
        """メールアドレスから表示名を返す。マッピングがなければメールアドレスをそのまま返す。"""
        return self.display_names.get(email, email)

    @property
    def daily_combo(self) -> Dict[str, List[str]]:
        """DailyInfoCombo 設定を辞書形式で返す"""
        return {
            "health_status": self.health_options,
            "work_place":    self.work_place_options,
            "safety":        self.safety_options,
            "overwork":      self.overwork_options,
        }


def load_config(path: Path = CONFIG_FILE) -> AppConfig:
    """
    config.ini と user_config.ini を読み込んで AppConfig を返す。
    user_config.ini が存在する場合は config.ini のデフォルト値を上書きする。
    """
    cfg = AppConfig()
    # 読み込み優先順: config.ini（デフォルト）→ user_config.ini（ユーザー設定で上書き）
    files = [str(path), str(USER_CONFIG_FILE)]
    parser = configparser.ConfigParser()
    parser.read(files, encoding="utf-8")
    if not parser.sections():
        return cfg

    # [Database]
    if parser.has_section("Database"):
        cfg.server_dir = parser.get("Database", "server_dir", fallback=cfg.server_dir)
        cfg.db_timeout = parser.getint("Database", "db_timeout", fallback=cfg.db_timeout)

    # [User]
    # username は OS ログイン名から取得するため config.ini には記載しない
    # members には OS ログイン名形式の ID を設定する
    if parser.has_section("User"):
        raw_members = parser.get("User", "members", fallback="")
        cfg.members = [m.strip() for m in raw_members.split(",") if m.strip()]

    # [GUI]
    if parser.has_section("GUI"):
        cfg.window_width  = parser.getint("GUI", "window_width",  fallback=cfg.window_width)
        cfg.window_height = parser.getint("GUI", "window_height", fallback=cfg.window_height)
        cfg.font_size     = parser.getint("GUI", "font_size",     fallback=cfg.font_size)

    # [Schedule]
    if parser.has_section("Schedule"):
        cfg.daily_begin_time = parser.getint("Schedule", "daily_begin_time", fallback=cfg.daily_begin_time)
        cfg.daily_end_time   = parser.getint("Schedule", "daily_end_time",   fallback=cfg.daily_end_time)
        cfg.daily_task_hour  = parser.getfloat("Schedule", "daily_task_hour", fallback=cfg.daily_task_hour)
        raw_hol = parser.get("Schedule", "holidays", fallback="SUN,SAT")
        cfg.holidays = [h.strip().upper() for h in raw_hol.split(",") if h.strip()]

    # [DailyInfoCombo]
    if parser.has_section("DailyInfoCombo"):
        def _split(key: str, fallback: List[str]) -> List[str]:
            raw = parser.get("DailyInfoCombo", key, fallback=None)
            if raw is None:
                return fallback
            return [v.strip() for v in raw.split(",") if v.strip()]

        cfg.health_options     = _split("health_status", cfg.health_options)
        cfg.work_place_options = _split("work_place",    cfg.work_place_options)
        cfg.safety_options     = _split("safety",        cfg.safety_options)
        cfg.overwork_options   = _split("overwork",      cfg.overwork_options)

    # [Report]
    if parser.has_section("Report"):
        cfg.report_output_dir = parser.get("Report", "output_dir",
                                           fallback=cfg.report_output_dir)

    # [DisplayNames]
    if parser.has_section("DisplayNames"):
        for email, name in parser.items("DisplayNames"):
            cfg.display_names[email] = name

    # [Commands]
    if parser.has_section("Commands"):
        i = 1
        while True:
            label_key  = f"command_{i:02d}_label"
            script_key = f"command_{i:02d}_script"
            if not parser.has_option("Commands", label_key):
                break
            label  = parser.get("Commands", label_key, fallback="")
            script = parser.get("Commands", script_key, fallback="")
            if label:
                cfg.commands.append({"label": label, "script": script})
            i += 1

    return cfg


# -------------------------------------------------------
# AppState - アプリ全体の共有状態
# -------------------------------------------------------
@dataclass
class AppState:
    config: AppConfig

    # ログインユーザー（起動時に固定、以降変更不可）
    login_user: str = ""
    # 表示対象メンバー（メンバーボタンで切り替え可能）
    current_user: str = ""
    current_date: str = ""

    # DB インスタンス・ロガー（起動後にセット）
    db: object = None      # type: db.Database
    logger: object = None  # type: logging.Logger

    # インメモリデータ
    df_nodes: pd.DataFrame       = field(default_factory=pd.DataFrame)
    df_daily: pd.DataFrame       = field(default_factory=pd.DataFrame)   # 日次スケジュール（全件）
    df_daily_log: pd.DataFrame   = field(default_factory=pd.DataFrame)   # 日次ログ（全件）
    df_assignments: pd.DataFrame = field(default_factory=pd.DataFrame)   # 依頼テーブル
    memo_text: str = ""
    permanent_notice: str = ""  # 自分の常時表示メモ
    all_permanent_notices: Dict[str, str] = field(default_factory=dict)  # 全員の常時メモ
    nodes_modified: bool = False     # ノード変更フラグ（Ctrl+S 保存前に True になる）
    schedule_modified: bool = False  # 日次スケジュール/ログ/メモ変更フラグ

    def __post_init__(self):
        if not self.login_user:
            self.login_user = self.config.username
        if not self.current_user:
            self.current_user = self.login_user
        if not self.current_date:
            self.current_date = datetime.date.today().isoformat()

    def reload_nodes(self) -> None:
        """DB からノード全件再読込"""
        if self.db:
            self.df_nodes = self.db.read_nodes()

    def reload_daily(self) -> None:
        """DB から日次スケジュール・ログ全件再読込"""
        if self.db:
            self.df_daily     = self.db.read_daily_schedule()
            self.df_daily_log = self.db.read_daily_log()
            self.df_assignments = self.db.read_assignments()

    def display_name(self, email: str) -> str:
        """メールアドレスから表示名を返す"""
        return self.config.get_display_name(email)

    @property
    def members(self) -> List[str]:
        """config.members への委譲プロパティ"""
        return self.config.members

    @property
    def user(self) -> str:
        """ログインユーザーを返す（メンバーボタン切り替えで変わらない）"""
        return self.login_user

    @property
    def current_member(self) -> str:
        """current_user の別名（UI との互換性用）"""
        return self.current_user

    @current_member.setter
    def current_member(self, value: str) -> None:
        self.current_user = value

    def notify_dirty(self) -> None:
        """保存ボタン色を更新するコールバックを呼ぶ（MainWindow が dirty_changed_func を登録）"""
        func = getattr(self, "dirty_changed_func", None)
        if callable(func):
            func()

    def refresh(self) -> None:
        """MainWindow から登録された UI リフレッシュ関数を呼び出す"""
        func = getattr(self, "refresh_func", None)
        if callable(func):
            func()

    def _log(self, level: str, msg: str) -> None:
        """ロガーがセットされていれば指定レベルで出力する"""
        if self.logger:
            getattr(self.logger, level)(msg)

    def save(self) -> None:
        """変更データを DB に書き込む。ロック取得失敗時は RuntimeError を送出する。"""
        if not self.db:
            return
        self._log("info", f"[保存] 開始 user={self.login_user} nodes_modified={self.nodes_modified}")
        if not self.db.acquire_lock():
            self._log("warning", "[保存] ロック取得失敗 - 他ユーザーが保存中の可能性")
            raise RuntimeError("dbが利用中です。しばらく時間をおいて実行してください")
        try:
            # nodes_modified が True のときのみ save_nodes を呼ぶ（DB アクセス最小化）
            if self.nodes_modified:
                self.db.save_nodes(self.df_nodes, self.login_user)
            self.db.save_daily_schedule(self.df_daily, self.login_user)
            self.db.save_daily_log(self.df_daily_log, self.login_user)
            self.db.save_memo(self.login_user, self.memo_text)
            self.db.save_permanent_notice(self.login_user, self.permanent_notice)
            self.nodes_modified = False     # 保存後にフラグをリセット
            self.schedule_modified = False  # 保存後にフラグをリセット
            self._log("info", "[保存] 完了")
        except Exception as e:
            self._log("error", f"[保存] DBエラー: {e}")
            raise
        finally:
            self.db.release_lock()

    def load(self) -> None:
        """DB から最新データを全件再読込する"""
        self._log("info", "[読込] 開始")
        self.reload_nodes()
        self.reload_daily()
        self.reload_memo()
        # daily_schedule の割り当てから actual_hours を再集計
        if self.db and not self.df_nodes.empty:
            self.df_nodes = self.db.recalc_actual_hours(self.df_nodes, self.df_daily)
        self._log("info", f"[読込] 完了 nodes={len(self.df_nodes)} daily={len(self.df_daily)}")

    def reload_memo(self) -> None:
        """DB からメモ・常時表示メモを再読込"""
        if self.db:
            self.memo_text              = self.db.read_memo(self.login_user)
            self.permanent_notice       = self.db.read_permanent_notice(self.login_user)
            self.all_permanent_notices  = self.db.read_all_permanent_notices()


# -------------------------------------------------------
# ロギング設定
# -------------------------------------------------------
def setup_logger(level: int = logging.INFO) -> logging.Logger:
    """アプリケーションロガーを設定して返す。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"app_{datetime.date.today().isoformat()}.log"

    logger = logging.getLogger(APP_NAME)
    logger.setLevel(level)

    if logger.handlers:
        return logger  # 二重登録防止

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ファイルハンドラ
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # コンソールハンドラ（WARNING 以上のみ）
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# -------------------------------------------------------
# メイン関数
# -------------------------------------------------------
def _get_server_log_dir() -> Optional[Path]:
    """
    __server_log_dir.json の PortablePy_Log キーからサーバーログディレクトリを返す。
    ファイルが存在しない・キーがない場合は None を返す。
    """
    json_path = Path(__file__).parent / "__server_log_dir.json"
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        raw = data.get("PortablePy_Log", "")
        if raw:
            return (Path(__file__).parent / raw).resolve()
    except Exception:
        pass
    return None


def _setup_excepthook(logger: logging.Logger) -> None:
    """
    未捕捉例外をサーバーログに保存するグローバルハンドラーを設定する。
    大きなエラー発生時に __server_log_dir.json で指定された場所へログを書き出す。
    """
    log_dir = _get_server_log_dir()

    def _excepthook(exc_type, exc_value, exc_tb):
        # まず通常のロガーに記録
        logger.critical("未捕捉例外が発生しました", exc_info=(exc_type, exc_value, exc_tb))
        # サーバーログディレクトリにも保存
        if log_dir:
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                err_file = log_dir / f"error_{ts}.txt"
                tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                err_file.write_text(
                    f"[{APP_NAME} {APP_VERSION}] 未捕捉例外\n"
                    f"日時: {datetime.datetime.now()}\n\n"
                    f"{tb_text}",
                    encoding="utf-8",
                )
            except Exception:
                pass  # ログ保存自体のエラーは無視
        # デフォルトの動作（標準エラーへの出力）も維持
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def _get_os_login() -> str:
    """
    OSのログイン名を取得して返す。
    環境変数 USERNAME (Windows) または USER (macOS/Linux) を参照する。
    どちらも取得できない場合は 'user' を返す。
    """
    return os.environ.get("USERNAME") or os.environ.get("USER") or "user"


# デバッグモードで「自分」として扱うテスト用 ID（id001 = 山田）
DEBUG_YAMADA_ID = "id001"


def main() -> None:
    # コマンドライン引数のパース
    parser_args = argparse.ArgumentParser(description=APP_NAME)
    parser_args.add_argument(
        "--debug",
        action="store_true",
        help=(
            "デバッグモード: OSログイン名に関係なく id001として起動する"
        ),
    )
    args = parser_args.parse_args()

    logger = setup_logger()
    logger.info(f"{APP_NAME} {APP_VERSION} 起動中...")
    if args.debug:
        logger.info(f"デバッグモード有効: ユーザーを {DEBUG_YAMADA_ID} として起動")

    # グローバル例外ハンドラーを設定（未捕捉エラーをサーバーログに保存）
    _setup_excepthook(logger)

    # PySide6 は import 前に QApplication が必要な場合があるため
    # ここでまとめて import する
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont
        from PySide6.QtCore import Qt
    except ImportError as e:
        print(f"PySide6 が見つかりません: {e}\npip install PySide6 を実行してください。")
        sys.exit(1)

    # 設定読込
    config = load_config()
    # username を決定:
    #   --debug 時は強制的に DEBUG_YAMADA_ID (id001) でログイン
    #   通常時は OS のログイン名を使用
    if args.debug:
        config.username = DEBUG_YAMADA_ID
    else:
        config.username = _get_os_login()
    # members に自分が含まれていない場合は先頭に追加
    if config.username not in config.members:
        config.members.insert(0, config.username)
    logger.info(f"設定読込完了: user={config.username}, db={config.server_dir}")

    # DB 初期化
    try:
        from db import Database
        db_dir = Path(config.server_dir)
        if not db_dir.is_absolute():
            db_dir = Path(__file__).parent / config.server_dir
        database = Database(str(db_dir), logger, timeout=config.db_timeout)
        logger.info(f"DB 接続成功: {db_dir}")
    except Exception as e:
        logger.exception("DB 初期化エラー")
        print(f"DB 初期化に失敗しました: {e}")
        sys.exit(1)

    # AppState 初期化
    state = AppState(config=config)
    state.db = database
    state.logger = logger
    state.reload_nodes()
    state.reload_daily()
    state.reload_memo()
    # 起動時に daily_schedule から actual_hours を再集計
    if not state.df_nodes.empty:
        state.df_nodes = state.db.recalc_actual_hours(state.df_nodes, state.df_daily)

    # Qt アプリケーション起動
    app = QApplication.instance() or QApplication(sys.argv)
    # ライトモード強制（ダークモード環境でも常にライトで表示）
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # フォントサイズ適用
    font = QFont()
    font.setPointSize(config.font_size)
    app.setFont(font)

    # Qt 内部メッセージをロガーへリダイレクト（コンソール出力を抑制）
    # "edit: editing failed" 等の Qt 警告をプロンプトに表示しないようにする
    from PySide6.QtCore import qInstallMessageHandler, QtMsgType as _QtMsg

    def _qt_msg_handler(mode, _context, message):
        """Qt 内部メッセージをファイルログのみに記録する（コンソール非表示）。"""
        if mode in (_QtMsg.QtFatalMsg, _QtMsg.QtCriticalMsg):
            logger.error(f"[Qt] {message}")
        else:
            # Debug / Info / Warning はすべてファイルのみ（level=DEBUG でコンソール非表示）
            logger.debug(f"[Qt] {message}")

    qInstallMessageHandler(_qt_msg_handler)

    # メインウィンドウ表示
    try:
        from ui_main import MainWindow
    except ImportError as e:
        logger.exception("UI モジュール読込エラー")
        print(f"UI モジュールの読込に失敗しました: {e}")
        sys.exit(1)

    window = MainWindow(state, APP_VERSION)
    window.resize(config.window_width, config.window_height)
    window.setWindowTitle(f"{APP_NAME}  [{config.get_display_name(config.username)}]")
    window.show()

    logger.info("メインウィンドウ表示完了")
    ret = app.exec()
    logger.info(f"{APP_NAME} 終了 (code={ret})")
    sys.exit(ret)


if __name__ == "__main__":
    main()
