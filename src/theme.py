"""
theme.py - 配色・共通スタイルの一元管理

画面の色はすべてここの C（色トークン）で指定する。
- Python から使う: QColor(C.TEXT_MUTED)
- スタイルシートで使う: qss("QLabel { color:@text_muted; }")  … @トークン名（小文字）を色に置換
見た目を変えるときは、原則このファイルの値だけを書き換える。
（ユーザーが選ぶノード色 COLOR_OPTIONS はデータなので db.py に置いたまま）
"""
import re

from PySide6.QtGui import QColor, QPalette


class C:
    """色トークン（役割ごとの名前）"""
    # ── 面 ──
    SURFACE        = "#FFFFFF"   # 入力欄・表・カードの地
    SURFACE_ALT    = "#F6F5FC"   # 詳細ペインの地・補足ラベルの地
    CARD_ALT       = "#FAF9FE"   # 日次ログ入力フォームの地
    BG_SOFT        = "#F6F5FC"   # メンバーバー・淡いボタンの地
    TOOLBAR_BG     = "#FFFFFF"   # メインツールバーの地
    CONTROL        = "#FFFFFF"   # 標準ボタンの地
    CONTROL_HOVER  = "#F1EFFD"
    CONTROL_PRESSED = "#E4E1F5"
    SOFT_BTN_HOVER = "#EEECFD"   # 淡いボタンの hover
    WINDOW_BG      = "#F6F5FC"   # ウィンドウ全体の地
    HEADER_BG      = "#F6F5FC"   # 表の見出し行
    TAB_TEXT       = "#5D588A"   # 画面切替タブの文字
    SCROLL_HANDLE  = "#D5D1EC"
    SCROLL_HOVER   = "#B7AEF7"
    TOOLTIP_BG     = "#2E2A45"
    HOUR_BG        = "#F6F5FC"   # 日次スケジュールの毎時行
    OFFDAY_BG      = "#F4F3F9"   # ガント・ロードマップの休日セル
    # ── 線 ──
    BORDER         = "#E4E1F5"   # 標準の枠線
    BORDER_STRONG  = "#D5D1EC"   # ボタン・入力欄の枠線
    BORDER_LIGHT   = "#ECEAF6"   # カードの枠線・表の格子
    ROW_LINE       = "#F1F0F8"   # ツリー行の区切り
    SLOT_LINE      = "#F0EEF7"   # 日次スケジュールの 15 分区切り
    HOUR_LINE      = "#C9C4E4"   # 日次スケジュールの毎時区切り
    BRANCH_LINE    = "#DAD6EC"   # ツリーの枝線
    # ── 文字 ──
    TEXT           = "#2E2A45"   # 本文・見出し
    TEXT_STRONG    = "#1F1B36"   # 詳細ペインのタイトル
    TEXT_SUB       = "#5D588A"   # 補足
    TEXT_MUTED     = "#9A95B8"   # 薄い補足
    TEXT_DONE      = "#B1ADC8"   # done 行のグレーアウト
    TEXT_DISABLED  = "#BDB9D2"   # Request ツリーの選択不可の行
    TEXT_DIM       = "#8C88A8"   # 凡例・注記
    TEXT_NOTE      = "#6B6690"   # 斜体の説明文
    TEXT_ON_SELECT = "#2E2A45"   # 選択行の文字
    TEXT_DEFAULT   = "#2E2A45"   # 種別色が無いときの文字
    ON_ACCENT      = "#FFFFFF"   # 濃い色の上の文字
    # ── 強調（アクセント）──
    ACCENT         = "#7C6CF0"
    ACCENT_LIGHT   = "#8F81F4"
    ACCENT_DARK    = "#6554E0"
    ACCENT_DARKER  = "#5443C9"
    ACCENT_BG      = "#EEECFD"   # アクセントの淡い地
    ACCENT_BG2     = "#E0DCFB"
    ACCENT_BORDER  = "#B7AEF7"
    SELECT_BG      = "#E0DCFB"   # 表・ツリーの選択行
    SLOT_SELECT_BG = "#E0DCFB"   # 日次スケジュールの選択行
    SLOT_SELECT_TEXT = "#3B2FA8"
    SLOT_CARD      = "#A79CF5"   # 色未設定チケットの日次カード
    # ── 状態色 ──
    SUCCESS        = "#1F8A66"
    SUCCESS_BG     = "#E3F5EF"
    WARNING        = "#E8833A"   # 保存待ち・納期接近・件数バッジ
    WARNING_DARK   = "#CC6B24"
    WARNING_BG     = "#FFF3DC"
    DANGER         = "#C94444"
    DANGER_BG      = "#FDE7E7"
    DANGER_BTN     = "#E05A5A"   # ポモドーロ作業中ボタン
    DANGER_BTN_ON  = "#C04444"
    OVERDUE_CELL   = "#F28B8B"   # ガントの納期超過セル
    DEADLINE_LINE  = "#E05A5A"   # ガントの納期線
    START_LINE     = "#3FB68B"   # ガントの開始可能日線
    PROGRESS_OK    = "#3FB68B"
    PROGRESS_OVER  = "#F2A65A"
    SUGGEST_BG     = "#FFF8E6"   # Inbox 振り分けの自動提案
    ROW_ERROR_BG   = "#FDE0E0"
    ROW_WARN_BG    = "#FFEBD2"
    CANCEL         = "#8A86A3"
    REGULAR        = "#1E7F88"
    REGULAR_BG     = "#E2F4F6"
    # ── Inbox ──
    INBOX          = "#B0457E"
    INBOX_BG       = "#FBEAF3"
    INBOX_BORDER   = "#F0C3DA"
    # ── 補助ボタン（LLM・Request・リンク）──
    LLM_BG         = "#EEECFD"
    LLM_TEXT       = "#4B3FBF"
    LLM_BORDER     = "#CFC8F8"
    LLM_HOVER      = "#E0DCFB"
    REQUEST_TEXT   = "#4B3FBF"
    REQUEST_BORDER = "#B7AEF7"
    LINK_BG        = "#EAF3FB"
    LINK_TEXT      = "#2F6FA8"
    LINK_BORDER    = "#CBE1F3"
    # ── ロードマップ・分析 ──
    PLAN_GRID      = "#EEECF7"
    PLAN_SELECT_BG = "#E0DCFB"
    BAR_PLAN       = "#BDB4F8"   # 計画
    BAR_ACTUAL     = "#94DDC6"   # 実績
    BAR_BOTH       = "#A9D4F5"   # 計画＋実績
    CHART_TEXT     = "#8C88A8"
    CHART_GUIDE    = "#B1ADC8"
    UNSET_GRAY     = "#CFCBE0"   # 対象外・未設定のグレー
    # ── ノードの既定色（COLOR_OPTIONS に無いとき）──
    NODE_DEFAULT   = "#00BCD4"
    TASK_DEFAULT   = "#FFA726"
    # ── 階層（種別）ごとの色 ──
    P0_BG, P0_FG   = "#E4E1F5", "#2E2A45"   # ツリーの仮想ルート [P0]
    P1_BG, P1_FG   = "#EEECFD", "#4B3FBF"
    P2_BG, P2_FG   = "#E3F5EF", "#1E6E55"
    P3_BG, P3_FG   = "#FFF3DC", "#8A5A0B"
    P4_BG, P4_FG   = "#FBEAF3", "#A23B72"
    TASK_BG, TASK_FG = "#F3F2F8", "#3E3A5C"
    TICKET_BG, TICKET_FG = "#FFFFFF", "#5D588A"
    TICKET_BADGE_BG, TICKET_BADGE_FG = "#E2F4F6", "#1E7F88"   # 詳細ペインの種別バッジ


# 階層ごとの背景色・文字色（ツリー・表・ガントで共通）
LEVEL_BG: dict = {}
LEVEL_FG: dict = {}

_TOKEN_RE = re.compile(r"@([a-z][a-z0-9_]*)")


def qss(sheet: str) -> str:
    """スタイルシート中の @トークン名 を C の色に置き換える"""
    return _TOKEN_RE.sub(lambda m: getattr(C, m.group(1).upper()), sheet)


def _build() -> None:
    """現在の C の値から階層色・共通スタイルを作り直す（配色の切替後に呼ぶ）"""
    global STYLE_BUTTON, STYLE_COMBO, STYLE_LABEL_INFO, STYLE_CHIP, APP_QSS
    LEVEL_BG.clear()
    LEVEL_BG.update({
        "project1": C.P1_BG, "project2": C.P2_BG,
        "project3": C.P3_BG, "project4": C.P4_BG,
        "task":     C.TASK_BG, "ticket": C.TICKET_BG,
    })
    LEVEL_FG.clear()
    LEVEL_FG.update({
        "project1": C.P1_FG, "project2": C.P2_FG,
        "project3": C.P3_FG, "project4": C.P4_FG,
        "task":     C.TASK_FG, "ticket": C.TICKET_FG,
    })
    # ── 共通スタイル ──
    STYLE_BUTTON = qss(
        "QPushButton { background: @control; border: 1px solid @border_strong;"
        " border-radius: 6px; padding: 4px 10px; }"
        "QPushButton:hover { background: @control_hover; }"
        "QPushButton:pressed { background: @control_pressed; }"
    )
    STYLE_COMBO = qss(
        "QComboBox { border: 1px solid @border_strong; border-radius: 6px;"
        " padding: 3px 6px; background: @surface; }"
    )
    STYLE_LABEL_INFO = qss("QLabel { color: @text_sub; font-size: 10px; }")
    # 丸いトグルボタン（メンバー切替・分析の人物/期間など。選択中は淡い紫）
    STYLE_CHIP = qss(
        "QPushButton {"
        " background: transparent; color: @text_sub;"
        " border: 1px solid @border; border-radius: 8px;"
        " padding: 3px 12px; font-size: 8pt; }"
        "QPushButton:checked {"
        " background: @accent_bg; color: @accent_dark;"
        " border: 1px solid @accent_border; }"
        "QPushButton:hover:!checked {"
        " background: @control_hover; }"
    )


    # ── アプリ全体のスタイル（個別指定の無い部品に効く）──
    APP_QSS = qss("""
    QToolTip { background: @tooltip_bg; color: @on_accent; border: none; padding: 4px 8px; }
    QHeaderView::section {
        background: @header_bg; color: @text_sub; border: none;
        border-right: 1px solid @border_light; border-bottom: 1px solid @border;
        padding: 3px 6px;
    }
    QLineEdit, QTextEdit, QPlainTextEdit {
        border: 1px solid @border_strong; border-radius: 6px; background: @surface;
        selection-background-color: @accent_bg2; selection-color: @text;
    }
    QLineEdit { padding: 2px 6px; }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: @accent; }
    QMenu { background: @surface; border: 1px solid @border; padding: 4px; }
    QMenu::item { padding: 5px 20px 5px 12px; border-radius: 4px; }
    QMenu::item:selected { background: @accent_bg; color: @accent_dark; }
    QMenu::separator { height: 1px; background: @border_light; margin: 4px 6px; }
    QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: @scroll_handle; border-radius: 4px; min-height: 24px; margin: 1px 2px; }
    QScrollBar::handle:horizontal { background: @scroll_handle; border-radius: 4px; min-width: 24px; margin: 2px 1px; }
    QScrollBar::handle:hover { background: @scroll_hover; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: none; }
    QSplitter::handle { background: transparent; }
    QTableView, QTreeView, QListView { border: 1px solid @border; gridline-color: @border_light; }
    """)


def apply_app_theme(app) -> None:
    """アプリ全体の配色（パレット）と共通スタイルを適用する"""
    pal = app.palette()
    for role, color in [
        (QPalette.ColorRole.Window, C.WINDOW_BG), (QPalette.ColorRole.WindowText, C.TEXT),
        (QPalette.ColorRole.Base, C.SURFACE), (QPalette.ColorRole.AlternateBase, C.CARD_ALT),
        (QPalette.ColorRole.Text, C.TEXT), (QPalette.ColorRole.Button, C.CONTROL),
        (QPalette.ColorRole.ButtonText, C.TEXT), (QPalette.ColorRole.Highlight, C.SELECT_BG),
        (QPalette.ColorRole.HighlightedText, C.TEXT),
        (QPalette.ColorRole.ToolTipBase, C.TOOLTIP_BG), (QPalette.ColorRole.ToolTipText, C.ON_ACCENT),
        (QPalette.ColorRole.PlaceholderText, C.TEXT_MUTED), (QPalette.ColorRole.Link, C.ACCENT),
    ]:
        pal.setColor(role, QColor(color))
    if MODE == "dark":
        # 区切り線・枠の立体表現に使う色（既定は明るく、ダークでは白く浮くため枠線色に合わせる）
        for role, color in [
            (QPalette.ColorRole.Light, C.BORDER_STRONG), (QPalette.ColorRole.Midlight, C.BORDER),
            (QPalette.ColorRole.Mid, C.BORDER), (QPalette.ColorRole.Dark, C.BORDER_LIGHT),
            (QPalette.ColorRole.Shadow, C.WINDOW_BG),
        ]:
            pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(APP_QSS)


# ── 配色の切替（I3 ダークモード）──

MODE = "light"
_LIGHT = {k: v for k, v in vars(C).items() if k.isupper()}

# ダーク（夜用）の配色。ここに無いトークンはライトと同じ値を使う
DARK = {
    "SURFACE": "#221F33", "SURFACE_ALT": "#1B1928", "CARD_ALT": "#1E1B2C", "BG_SOFT": "#1B1928",
    "TOOLBAR_BG": "#1E1B2C", "CONTROL": "#2A2640", "CONTROL_HOVER": "#353052",
    "CONTROL_PRESSED": "#3F3960", "SOFT_BTN_HOVER": "#353052", "HOUR_BG": "#1E1B2C",
    "OFFDAY_BG": "#1A1826", "WINDOW_BG": "#17151F", "HEADER_BG": "#1E1B2C", "TAB_TEXT": "#B3AED6",
    "SCROLL_HANDLE": "#4A4468", "SCROLL_HOVER": "#6B5FC7", "TOOLTIP_BG": "#3A3552",
    "BORDER": "#3A3552", "BORDER_STRONG": "#4A4468", "BORDER_LIGHT": "#2E2A42", "ROW_LINE": "#2A2640",
    "SLOT_LINE": "#2E2A42", "HOUR_LINE": "#5A5480", "BRANCH_LINE": "#4A4468",
    "TEXT": "#E8E6F5", "TEXT_STRONG": "#F5F4FC", "TEXT_SUB": "#B3AED6", "TEXT_MUTED": "#8A85AD",
    "TEXT_DONE": "#6E6990", "TEXT_DISABLED": "#625D82", "TEXT_DIM": "#8A85AD", "TEXT_NOTE": "#A29DC4",
    "TEXT_ON_SELECT": "#F5F4FC", "TEXT_DEFAULT": "#E8E6F5",
    "ACCENT": "#8F80F5", "ACCENT_LIGHT": "#A396F7", "ACCENT_DARK": "#C3BAFB", "ACCENT_DARKER": "#D6D0FC",
    "ACCENT_BG": "#2E2850", "ACCENT_BG2": "#3A3266", "ACCENT_BORDER": "#6B5FC7",
    "SELECT_BG": "#3A3266", "SLOT_SELECT_BG": "#3A3266", "SLOT_SELECT_TEXT": "#E8E6F5",
    "SLOT_CARD": "#7C6CF0",
    "SUCCESS": "#5FD1A7", "SUCCESS_BG": "#1E3A31", "WARNING": "#E8833A", "WARNING_DARK": "#F2A65A",
    "WARNING_BG": "#3A2A1A", "DANGER": "#F07A7A", "DANGER_BG": "#3D2226", "OVERDUE_CELL": "#B84A4A",
    "DEADLINE_LINE": "#F07A7A", "START_LINE": "#5FD1A7", "PROGRESS_OK": "#5FD1A7",
    "SUGGEST_BG": "#3A3220", "ROW_ERROR_BG": "#4A2A2E", "ROW_WARN_BG": "#45351F",
    "CANCEL": "#9A96B5", "REGULAR": "#6CCFD8", "REGULAR_BG": "#1C3639",
    "INBOX": "#F09AC4", "INBOX_BG": "#3A2233", "INBOX_BORDER": "#6E3A58",
    "LLM_BG": "#2E2850", "LLM_TEXT": "#C3BAFB", "LLM_BORDER": "#4A4178", "LLM_HOVER": "#3A3266",
    "REQUEST_TEXT": "#C3BAFB", "REQUEST_BORDER": "#6B5FC7",
    "LINK_BG": "#1E2C3D", "LINK_TEXT": "#8EC3F0", "LINK_BORDER": "#34506E",
    "PLAN_GRID": "#2A2640", "PLAN_SELECT_BG": "#3A3266", "BAR_PLAN": "#8F80F5",
    "BAR_ACTUAL": "#3FB68B", "BAR_BOTH": "#4F9BD6", "CHART_TEXT": "#8A85AD", "CHART_GUIDE": "#6E6990",
    "UNSET_GRAY": "#5A5670",
    "P0_BG": "#2E2A42", "P0_FG": "#E8E6F5", "P1_BG": "#2E2850", "P1_FG": "#C3BAFB",
    "P2_BG": "#1E3A31", "P2_FG": "#7FDDB8", "P3_BG": "#3A3020", "P3_FG": "#F2C77A",
    "P4_BG": "#3A2233", "P4_FG": "#F09AC4", "TASK_BG": "#26233A", "TASK_FG": "#D6D3EA",
    "TICKET_BG": "#221F33", "TICKET_FG": "#B3AED6",
    "TICKET_BADGE_BG": "#1C3639", "TICKET_BADGE_FG": "#6CCFD8",
}


def set_mode(mode: str) -> None:
    """
    配色を "light" / "dark" に切り替える。
    各画面は作成時に色を決めるため、UI モジュール（ui_main 等）を import する前に呼ぶこと。
    """
    global MODE
    MODE = "dark" if mode == "dark" else "light"
    for k, v in _LIGHT.items():
        setattr(C, k, v)
    if MODE == "dark":
        for k, v in DARK.items():
            setattr(C, k, v)
    _build()


def resolve_mode(setting: str, system_is_dark: bool) -> str:
    """Config の theme（light / dark / system）から実際の配色を決める"""
    setting = (setting or "light").strip().lower()
    if setting == "system":
        return "dark" if system_is_dark else "light"
    return "dark" if setting == "dark" else "light"


def mpl_style() -> dict:
    """分析グラフ（matplotlib）の配色"""
    return {
        "figure.facecolor": C.SURFACE, "axes.facecolor": C.SURFACE,
        "savefig.facecolor": C.SURFACE, "axes.edgecolor": C.BORDER_STRONG,
        "axes.labelcolor": C.TEXT, "text.color": C.TEXT,
        "xtick.color": C.TEXT_SUB, "ytick.color": C.TEXT_SUB,
        "legend.facecolor": C.SURFACE, "legend.edgecolor": C.BORDER,
    }


_build()
