"""
main.py - エントリポイント・設定・ロギング・起動
"""
import configparser
import datetime
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# -------------------------------------------------------
# 定数
# -------------------------------------------------------
APP_NAME    = "Schedule Manager v3"
APP_VERSION = "3.0.0"
CONFIG_FILE = Path(__file__).parent / "config.ini"
LOG_DIR     = Path(__file__).parent / "logs"


# -------------------------------------------------------
# AppConfig - config.ini の内容を保持
# -------------------------------------------------------
@dataclass
class AppConfig:
    # [Database]
    server_dir: str = "./db"

    # [User]
    username: str = "User"
    members: List[str] = field(default_factory=lambda: ["User"])

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

    # [Commands]
    commands: List[Dict[str, str]] = field(default_factory=list)

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
    """config.ini を読み込んで AppConfig を返す。ファイルがなければデフォルト値を使用。"""
    cfg = AppConfig()
    if not path.exists():
        return cfg

    parser = configparser.ConfigParser()
    parser.read(str(path), encoding="utf-8")

    # [Database]
    if parser.has_section("Database"):
        cfg.server_dir = parser.get("Database", "server_dir", fallback=cfg.server_dir)

    # [User]
    if parser.has_section("User"):
        cfg.username = parser.get("User", "username", fallback=cfg.username)
        raw_members = parser.get("User", "members", fallback=cfg.username)
        cfg.members = [m.strip() for m in raw_members.split(",") if m.strip()]
        if cfg.username not in cfg.members:
            cfg.members.insert(0, cfg.username)

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

    # 現在の操作ユーザー・日付
    current_user: str = ""
    current_date: str = ""

    # DB インスタンス（起動後にセット）
    db: object = None  # type: db.Database

    # インメモリデータ
    df_nodes: pd.DataFrame       = field(default_factory=pd.DataFrame)
    df_daily: pd.DataFrame       = field(default_factory=pd.DataFrame)   # 日次スケジュール（全件）
    df_daily_log: pd.DataFrame   = field(default_factory=pd.DataFrame)   # 日次ログ（全件）
    df_assignments: pd.DataFrame = field(default_factory=pd.DataFrame)   # 依頼テーブル
    memo_text: str = ""
    permanent_notice: str = ""  # 常時表示メモ

    def __post_init__(self):
        if not self.current_user:
            self.current_user = self.config.username
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

    @property
    def members(self) -> List[str]:
        """config.members への委譲プロパティ"""
        return self.config.members

    @property
    def user(self) -> str:
        """current_user の簡易アクセス用エイリアス（UI コード内で state.user として参照）"""
        return self.current_user

    @property
    def current_member(self) -> str:
        """current_user の別名（UI との互換性用）"""
        return self.current_user

    @current_member.setter
    def current_member(self, value: str) -> None:
        self.current_user = value

    def refresh(self) -> None:
        """MainWindow から登録された UI リフレッシュ関数を呼び出す"""
        func = getattr(self, "refresh_func", None)
        if callable(func):
            func()

    def save(self) -> None:
        """変更データを DB に書き込む"""
        if not self.db:
            return
        self.db.save_nodes(self.df_nodes, self.current_user)
        self.db.save_daily_schedule(self.df_daily, self.current_user)
        self.db.save_daily_log(self.df_daily_log, self.current_user)
        self.db.save_memo(self.current_user, self.memo_text)
        self.db.save_permanent_notice(self.current_user, self.permanent_notice)

    def load(self) -> None:
        """DB から最新データを全件再読込する"""
        self.reload_nodes()
        self.reload_daily()
        self.reload_memo()

    def reload_memo(self) -> None:
        """DB からメモ・常時表示メモを再読込"""
        if self.db:
            self.memo_text         = self.db.read_memo(self.current_user)
            self.permanent_notice  = self.db.read_permanent_notice(self.current_user)


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
def main() -> None:
    logger = setup_logger()
    logger.info(f"{APP_NAME} {APP_VERSION} 起動中...")

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
    logger.info(f"設定読込完了: user={config.username}, db={config.server_dir}")

    # DB 初期化
    try:
        from db import Database
        db_dir = Path(config.server_dir)
        if not db_dir.is_absolute():
            db_dir = Path(__file__).parent / config.server_dir
        database = Database(str(db_dir), logger)
        logger.info(f"DB 接続成功: {db_dir}")
    except Exception as e:
        logger.exception("DB 初期化エラー")
        print(f"DB 初期化に失敗しました: {e}")
        sys.exit(1)

    # AppState 初期化
    state = AppState(config=config)
    state.db = database
    state.reload_nodes()
    state.reload_daily()
    state.reload_memo()

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

    # メインウィンドウ表示
    try:
        from ui_main import MainWindow
    except ImportError as e:
        logger.exception("UI モジュール読込エラー")
        print(f"UI モジュールの読込に失敗しました: {e}")
        sys.exit(1)

    window = MainWindow(state, APP_VERSION)
    window.resize(config.window_width, config.window_height)
    window.setWindowTitle(f"{APP_NAME}  [{config.username}]")
    window.show()

    logger.info("メインウィンドウ表示完了")
    ret = app.exec()
    logger.info(f"{APP_NAME} 終了 (code={ret})")
    sys.exit(ret)


if __name__ == "__main__":
    main()
