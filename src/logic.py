"""
logic.py - スケジューリング・テキストパーサー・検索・エクスポート
"""
import datetime
import csv
import os
from typing import List, Optional

import pandas as pd

from db import (
    NODE_TYPES, STATUS_LIST, DAILY_TIME_COLS,
    create_initial_node, generate_idx,
)


# ---------- テキスト一括チケット入力パーサー ----------

TICKET_TEXT_HEADER = (
    "# タイトル, 優先度, 見積工数(h), 開始可能日, 納期, ステータス, メモ\n"
    "# 空欄はそのままカンマで区切ってください。日付: YYYY-MM-DD\n"
)


def parse_ticket_text(text: str, parent_id: str, owner: str,
                      df_nodes: pd.DataFrame) -> tuple:
    """
    LLM 連携用テキストからチケットの pd.Series リストを生成する。

    フォーマット（1行1チケット、# はコメント行）:
        タイトル, 優先度, 見積工数(h), 開始可能日, 納期, ステータス, メモ

    戻り値:
        (成功リスト[pd.Series], エラーリスト[str])
    """
    results: List[pd.Series] = []
    errors: List[str] = []
    existing_titles = set(
        df_nodes[df_nodes["parent_id"] == parent_id]["title"].tolist()
    ) if not df_nodes.empty else set()

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",")]
        # フィールドを 7 個に揃える（足りない分は空文字）
        parts += [""] * max(0, 7 - len(parts))

        title       = parts[0]
        priority_s  = parts[1]
        hours_s     = parts[2]
        start_s     = parts[3]
        deadline_s  = parts[4]
        status_s    = parts[5].lower() if parts[5] else "todo"
        memo        = parts[6]

        if not title:
            errors.append(f"行 {line_no}: タイトルが空です → {raw}")
            continue

        # 優先度
        try:
            priority = int(priority_s) if priority_s else 99
        except ValueError:
            errors.append(f"行 {line_no}: 優先度が整数ではありません → {raw}")
            continue

        # 見積工数
        try:
            hours = float(hours_s) if hours_s else 0.25
            if hours <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"行 {line_no}: 見積工数が正の数ではありません → {raw}")
            continue

        # 日付パース
        start_date = _parse_date(start_s)
        deadline   = _parse_date(deadline_s)
        if start_s and start_date is None:
            errors.append(f"行 {line_no}: 開始可能日の形式が不正です → {raw}")
            continue
        if deadline_s and deadline is None:
            errors.append(f"行 {line_no}: 納期の形式が不正です → {raw}")
            continue

        # ステータス
        if status_s not in ("todo", "regularly", ""):
            errors.append(f"行 {line_no}: ステータスは todo / regularly のみ指定可 → {raw}")
            continue
        if not status_s:
            status_s = "todo"

        # 名前重複チェック
        if title in existing_titles:
            errors.append(f"行 {line_no}: 同名チケットが既に存在します → {title}")
            continue

        ds = create_initial_node(owner, "ticket", title, parent_id, priority)
        ds["estimated_hours"] = hours
        ds["start_available"] = start_date
        ds["deadline"]        = deadline
        ds["status"]          = status_s
        ds["memo"]            = memo
        results.append(ds)
        existing_titles.add(title)

    return results, errors


def _parse_date(s: str) -> Optional[str]:
    """YYYY-MM-DD または YYYY/MM/DD を受け付け ISO 形式文字列を返す。失敗時は None。"""
    if not s:
        return None
    s = s.replace("/", "-")
    try:
        datetime.date.fromisoformat(s)
        return s
    except ValueError:
        return None


# ---------- EDF + 優先度スケジューリング ----------

def edf_schedule(df_nodes: pd.DataFrame, task_idx: str,
                 daily_hours: float = 5.0) -> List[str]:
    """
    同一 Task 配下の Ticket を EDF + 優先度順に並べ替え、
    各チケットへの推奨開始日を計算して df_nodes を更新する。

    Returns:
        順序付き ticket IDX リスト
    """
    tickets = df_nodes[
        (df_nodes["parent_id"] == task_idx)
        & (df_nodes["node_type"] == "ticket")
        & (~df_nodes["status"].isin(["cancel", "deleted"]))
    ].copy()

    if tickets.empty:
        return []

    # 親タスクの納期を取得（最後のチケットに納期がない場合のフォールバック）
    parent_dl = None
    if task_idx in df_nodes.index:
        raw = df_nodes.loc[task_idx, "deadline"]
        if raw and str(raw) not in ("", "nan", "None"):
            try:
                parent_dl = datetime.date.fromisoformat(str(raw))
            except Exception:
                pass

    # 納期が未設定のチケットに逆算で設定（daily_hours を渡す）
    tickets = fill_deadlines_backward(tickets, daily_h=daily_hours, parent_deadline=parent_dl)

    # EDF: 納期昇順、同一納期内は優先度昇順
    tickets["_deadline_sort"] = pd.to_datetime(
        tickets["deadline"], errors="coerce"
    )
    tickets = tickets.sort_values(
        ["_deadline_sort", "priority"],
        na_position="last",
    )

    return list(tickets.index)


def fill_deadlines_backward(tickets: pd.DataFrame, daily_h: float = 5.0,
                             parent_deadline: Optional[datetime.date] = None) -> pd.DataFrame:
    """
    納期未設定チケットに対して、後ろ（優先度大=後で実行）のチケットの納期から
    逆算して仮の納期を設定する。

    アルゴリズム（優先度降順＝後ろから順に処理）:
      直後チケットの実行開始日 = 直後納期 - (ceil(直後残工数/daily_h) - 1) 日
      現チケットの仮納期       = 直後チケットの実行開始日 - 1日

    parent_deadline:
      最後のチケット（優先度最大）に納期が設定されていない場合、
      親タスクの納期を仮の基準として使用する。

    例) Ticket1(est=10h,実績=0,納期なし)、Ticket2(est=20h,実績=5h,納期=3/5)、daily_h=5
      Ticket2残工数=15h → 3日必要 → 3/3〜3/5に作業 → 開始日=3/3
      Ticket1仮納期=3/3-1=3/2 ✓
    """
    df = tickets.copy().sort_values("priority", ascending=False)
    prev_start: Optional[datetime.date] = None  # 直後チケットの実行開始日
    first_row = True  # 最初に処理される行 = 優先度最大 = 最後に実行されるチケット

    for idx in df.index:
        dl_raw = df.loc[idx, "deadline"]
        est_h = float(df.loc[idx, "estimated_hours"] or 0)
        act_h = float(df.loc[idx, "actual_hours"] or 0)
        remaining_h = max(0.0, est_h - act_h)

        # 必要日数（切り上げ）
        if remaining_h > 0.001:
            days_needed = max(1, -(-int(remaining_h * 100) // int(daily_h * 100)))
        else:
            days_needed = 0

        has_deadline = (dl_raw and str(dl_raw) not in ("", "nan", "None")
                        and dl_raw == dl_raw)

        # 最後のチケット（最初に処理）に納期がなく、親の納期があれば仮設定
        if first_row and not has_deadline and parent_deadline is not None:
            df.loc[idx, "deadline"] = parent_deadline.isoformat()
            has_deadline = True
            dl_raw = parent_deadline.isoformat()
        first_row = False

        if has_deadline:
            # 納期設定済み: 実行開始日を計算して prev_start に保持
            dl = datetime.date.fromisoformat(str(dl_raw))
            if days_needed > 0:
                prev_start = dl - datetime.timedelta(days=days_needed - 1)
            else:
                prev_start = dl + datetime.timedelta(days=1)
        elif prev_start is not None:
            # 納期未設定: 直後チケット開始日の前日を仮納期にする
            virtual_dl = prev_start - datetime.timedelta(days=1)
            df.loc[idx, "deadline"] = virtual_dl.isoformat()
            if days_needed > 0:
                prev_start = virtual_dl - datetime.timedelta(days=days_needed - 1)
            else:
                prev_start = virtual_dl + datetime.timedelta(days=1)
        # else: 後ろに納期設定済みチケットがない → そのまま（deadline なし）

    return df


# ---------- 完了チェック（spec 2.2 自動 done 伝播） ----------

def check_auto_done(df_nodes: pd.DataFrame, changed_idx: str) -> List[str]:
    """
    「完了」チケットが done になったとき、親を自動 done にする。
    返り値: done にすべき親 IDX のリスト
    """
    to_done: List[str] = []
    if changed_idx not in df_nodes.index:
        return to_done

    ds = df_nodes.loc[changed_idx]
    if ds["status"] != "done" or ds["title"] != "完了":
        return to_done

    parent_id = ds["parent_id"]
    if parent_id == "0" or parent_id not in df_nodes.index:
        return to_done

    # 親の子ノードに cancel/deleted 以外の未完了があるか
    siblings = df_nodes[
        (df_nodes["parent_id"] == parent_id)
        & (~df_nodes["status"].isin(["cancel", "deleted", "done"]))
    ]
    if siblings.empty:
        to_done.append(parent_id)

    return to_done


# ---------- 検索・フィルタ ----------

def filter_nodes(df: pd.DataFrame,
                 keyword: str = "",
                 statuses: Optional[List[str]] = None,
                 member: str = "",
                 date_from: str = "",
                 date_to: str = "",
                 node_types: Optional[List[str]] = None) -> pd.DataFrame:
    """
    条件に合うノードを返す。
    """
    if df.empty:
        return df

    result = df.copy()

    if keyword:
        kw = keyword.lower()
        mask = (
            result.get("title", pd.Series(dtype=str)).fillna("").str.lower().str.contains(kw)
            | result.get("memo", pd.Series(dtype=str)).fillna("").str.lower().str.contains(kw)
        )
        result = result[mask]

    if statuses:
        result = result[result["status"].isin(statuses)]

    if member:
        result = result[result.get("assigned_to", pd.Series(dtype=str)) == member]

    if date_from:
        result = result[
            result.get("updated_at", pd.Series(dtype=str)).fillna("") >= date_from
        ]
    if date_to:
        result = result[
            result.get("updated_at", pd.Series(dtype=str)).fillna("") <= date_to
        ]

    if node_types:
        result = result[result["node_type"].isin(node_types)]

    # deleted は常に除外
    result = result[result["status"] != "deleted"]
    return result


# ---------- CSV エクスポート ----------

def export_csv(df: pd.DataFrame, filepath: str) -> None:
    """DataFrame を CSV に出力する"""
    df.to_csv(filepath, encoding="utf-8-sig", index=True)


def export_excel(df: pd.DataFrame, filepath: str) -> None:
    """DataFrame を Excel に出力する"""
    df.to_excel(filepath, index=True)


# ---------- 勤務時間計算 ----------

def calc_working_hours(df_daily: pd.DataFrame, idx: str) -> dict:
    """
    daily_schedule の1行から勤務時間情報を計算して返す。
    戻り値: {"total": float, "from": str, "to": str, "break": float}
    """
    empty = {"total": 0.0, "from": "", "to": "", "break": 0.0}
    if df_daily.empty or idx not in df_daily.index:
        return empty

    ds = df_daily.loc[idx]
    total, break_h = 0.0, 0.0
    work_from, work_to_col = "", ""
    break_tmp = 0.0

    for col in DAILY_TIME_COLS:
        val = ds[col] if col in ds.index else ""
        if val:
            total += 0.25
            break_h += break_tmp
            break_tmp = 0.0
            work_to_col = col
            if not work_from:
                work_from = col
        elif work_from:
            break_tmp += 0.25

    if not work_from:
        return empty

    # work_to は最後の割り当てスロットの次
    try:
        to_idx = DAILY_TIME_COLS.index(work_to_col) + 1
        work_to = DAILY_TIME_COLS[to_idx] if to_idx < len(DAILY_TIME_COLS) else DAILY_TIME_COLS[-1]
    except ValueError:
        work_to = work_to_col

    return {"total": total, "from": work_from, "to": work_to, "break": break_h}


def col_to_hhmm(col: str) -> str:
    """'C0930' → '09:30' に変換"""
    if not col or len(col) < 5:
        return ""
    return f"{col[1:3]}:{col[3:5]}"
