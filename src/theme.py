"""
theme.py - 配色・共通スタイルの一元管理

画面の色はすべてここの C（色トークン）で指定する。
- Python から使う: QColor(C.TEXT_MUTED)
- スタイルシートで使う: qss("QLabel { color:@text_muted; }")  … @トークン名（小文字）を色に置換
見た目を変えるときは、原則このファイルの値だけを書き換える。
（ユーザーが選ぶノード色 COLOR_OPTIONS はデータなので db.py に置いたまま）
"""
import re


class C:
    """色トークン（役割ごとの名前）"""
    # ── 面 ──
    SURFACE        = "#FFFFFF"   # 入力欄・表・カードの地
    SURFACE_ALT    = "#F4F6F8"   # 詳細ペインの地・補足ラベルの地
    CARD_ALT       = "#F8F9FA"   # 日次ログ入力フォームの地
    BG_SOFT        = "#F5F5F5"   # メンバーバー・淡いボタンの地
    TOOLBAR_BG     = "#ECEFF1"   # メインツールバーの地
    CONTROL        = "#ECEFF1"   # 標準ボタンの地
    CONTROL_HOVER  = "#CFD8DC"
    CONTROL_PRESSED = "#B0BEC5"
    SOFT_BTN_HOVER = "#E0E0E0"   # 淡いボタンの hover
    TAB_TOP        = "#FFFFFF"   # タブボタンのグラデーション（上）
    TAB_BOTTOM     = "#E8ECEF"   # タブボタンのグラデーション（下）
    HOUR_BG        = "#ECEFF1"   # 日次スケジュールの毎時行
    OFFDAY_BG      = "#F0F0F0"   # ガント・ロードマップの休日セル
    # ── 線 ──
    BORDER         = "#CFD8DC"   # 標準の枠線
    BORDER_STRONG  = "#B0BEC5"   # ボタン・入力欄の枠線
    BORDER_LIGHT   = "#E0E0E0"   # カードの枠線・表の格子
    ROW_LINE       = "#EEEEEE"   # ツリー行の区切り
    SLOT_LINE      = "#E8E8E8"   # 日次スケジュールの 15 分区切り
    HOUR_LINE      = "#90A4AE"   # 日次スケジュールの毎時区切り
    BRANCH_LINE    = "#CCCCCC"   # ツリーの枝線
    # ── 文字 ──
    TEXT           = "#37474F"   # 本文・見出し
    TEXT_STRONG    = "#263238"   # 詳細ペインのタイトル
    TEXT_SUB       = "#546E7A"   # 補足
    TEXT_MUTED     = "#90A4AE"   # 薄い補足
    TEXT_DONE      = "#9E9E9E"   # done 行のグレーアウト
    TEXT_DISABLED  = "#AAAAAA"   # Request ツリーの選択不可の行
    TEXT_DIM       = "#888888"   # 凡例・注記
    TEXT_NOTE      = "#555555"   # 斜体の説明文
    TEXT_ON_SELECT = "#000000"   # 選択行の文字
    TEXT_DEFAULT   = "#000000"   # 種別色が無いときの文字
    ON_ACCENT      = "#FFFFFF"   # 濃い色の上の文字
    # ── 強調（アクセント）──
    ACCENT         = "#1565C0"
    ACCENT_LIGHT   = "#1E88E5"
    ACCENT_DARK    = "#0D47A1"
    ACCENT_DARKER  = "#083A82"
    ACCENT_BG      = "#E3F2FD"   # アクセントの淡い地
    ACCENT_BG2     = "#BBDEFB"
    ACCENT_BORDER  = "#64B5F6"
    SELECT_BG      = "#B3E5FC"   # 表・ツリーの選択行
    SLOT_SELECT_BG = "#B2DFDB"   # 日次スケジュールの選択行
    SLOT_SELECT_TEXT = "#004D40"
    SLOT_CARD      = "#4DB6AC"   # 色未設定チケットの日次カード
    # ── 状態色 ──
    SUCCESS        = "#2E7D32"
    SUCCESS_BG     = "#E8F5E9"
    WARNING        = "#E65100"   # 保存待ち・納期接近・件数バッジ
    WARNING_DARK   = "#BF360C"
    WARNING_BG     = "#FFF3E0"
    DANGER         = "#C62828"
    DANGER_BG      = "#FFEBEE"
    DANGER_BTN     = "#E53935"   # ポモドーロ作業中ボタン
    DANGER_BTN_ON  = "#B71C1C"
    OVERDUE_CELL   = "#EF5350"   # ガントの納期超過セル
    DEADLINE_LINE  = "#D32F2F"   # ガントの納期線
    START_LINE     = "#2E7D32"   # ガントの開始可能日線
    PROGRESS_OK    = "#43A047"
    PROGRESS_OVER  = "#FB8C00"
    SUGGEST_BG     = "#FFF8E1"   # Inbox 振り分けの自動提案
    ROW_ERROR_BG   = "#FFCDD2"
    ROW_WARN_BG    = "#FFE0B2"
    CANCEL         = "#616161"
    REGULAR        = "#00695C"
    REGULAR_BG     = "#E0F2F1"
    # ── Inbox ──
    INBOX          = "#6A1B9A"
    INBOX_BG       = "#F3E5F5"
    INBOX_BORDER   = "#CE93D8"
    # ── 補助ボタン（LLM・Request・リンク）──
    LLM_BG         = "#EDE7F6"
    LLM_TEXT       = "#5E35B1"
    LLM_BORDER     = "#B39DDB"
    LLM_HOVER      = "#D1C4E9"
    REQUEST_TEXT   = "#4527A0"
    REQUEST_BORDER = "#7E57C2"
    LINK_BG        = "#E8EAF6"
    LINK_TEXT      = "#3949AB"
    LINK_BORDER    = "#C5CAE9"
    # ── ロードマップ・分析 ──
    PLAN_GRID      = "#E8EAF6"
    PLAN_SELECT_BG = "#C5CAE9"
    BAR_PLAN       = "#90CAF9"   # 計画
    BAR_ACTUAL     = "#A5D6A7"   # 実績
    BAR_BOTH       = "#80DEEA"   # 計画＋実績
    CHART_TEXT     = "#888888"
    CHART_GUIDE    = "#999999"
    UNSET_GRAY     = "#BDBDBD"   # 対象外・未設定のグレー
    # ── ノードの既定色（COLOR_OPTIONS に無いとき）──
    NODE_DEFAULT   = "#00BCD4"
    TASK_DEFAULT   = "#FFA726"
    # ── 階層（種別）ごとの色 ──
    P0_BG, P0_FG   = "#CFD8DC", "#37474F"   # ツリーの仮想ルート [P0]
    P1_BG, P1_FG   = "#E3F2FD", "#1565C0"
    P2_BG, P2_FG   = "#E8F5E9", "#2E7D32"
    P3_BG, P3_FG   = "#FFF9C4", "#F57F17"
    P4_BG, P4_FG   = "#F3E5F5", "#6A1B9A"
    TASK_BG, TASK_FG = "#ECEFF1", "#37474F"
    TICKET_BG, TICKET_FG = "#FFFFFF", "#546E7A"
    TICKET_BADGE_BG, TICKET_BADGE_FG = "#E0F7FA", "#00838F"   # 詳細ペインの種別バッジ


# 階層ごとの背景色・文字色（ツリー・表・ガントで共通）
LEVEL_BG = {
    "project1": C.P1_BG, "project2": C.P2_BG,
    "project3": C.P3_BG, "project4": C.P4_BG,
    "task":     C.TASK_BG, "ticket": C.TICKET_BG,
}
LEVEL_FG = {
    "project1": C.P1_FG, "project2": C.P2_FG,
    "project3": C.P3_FG, "project4": C.P4_FG,
    "task":     C.TASK_FG, "ticket": C.TICKET_FG,
}

_TOKEN_RE = re.compile(r"@([a-z][a-z0-9_]*)")


def qss(sheet: str) -> str:
    """スタイルシート中の @トークン名 を C の色に置き換える"""
    return _TOKEN_RE.sub(lambda m: getattr(C, m.group(1).upper()), sheet)


# ── 共通スタイル ──
STYLE_BUTTON = qss(
    "QPushButton { background: @control; border: 1px solid @border_strong;"
    " border-radius: 4px; padding: 4px 10px; }"
    "QPushButton:hover { background: @control_hover; }"
    "QPushButton:pressed { background: @control_pressed; }"
)
STYLE_COMBO = qss(
    "QComboBox { border: 1px solid @border_strong; border-radius: 4px;"
    " padding: 3px 6px; background: @surface; }"
)
STYLE_LABEL_INFO = qss("QLabel { color: @text_sub; font-size: 10px; }")
