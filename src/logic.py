"""
logic.py - スケジューリング・テキストパーサー・検索・エクスポート
"""
import datetime
import csv
import os
import re
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
    # 既存チケット名の重複チェック用セット（同一親 ID 配下のタイトルを収集）
    existing_titles = set(
        df_nodes[df_nodes["parent_id"] == parent_id]["title"].tolist()
    ) if not df_nodes.empty else set()

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        # コメント行・空行はスキップ
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
    同一 Task 配下の Ticket を EDF（Earliest Deadline First）+ 優先度順に並べ替え、
    各チケットへの推奨開始日を計算して df_nodes を更新する。

    アルゴリズム概要:
      1. 対象 Task 配下の有効チケット（cancel/deleted 除く）を収集
      2. fill_deadlines_backward() で納期未設定チケットに仮納期を逆算設定
      3. 仮納期昇順 → 同一納期内は優先度昇順でソート（EDF）
      4. 順序付き IDX リストを返す（UI 側でこの順に表示）

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

    # EDF: 納期昇順、同一納期内は優先度昇順（納期なしは最後に配置）
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

    概要: 優先度降順（後から前）にチケットを走査し、
    直後チケットの作業開始日を起点に「前日」を仮納期として割り当てる。

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
    # 優先度降順（後ろのチケットから逆算するため）にソート
    df = tickets.copy().sort_values("priority", ascending=False)
    prev_start: Optional[datetime.date] = None  # 直後チケットの実行開始日
    first_row = True  # 最初に処理される行 = 優先度最大 = 最後に実行されるチケット

    for idx in df.index:
        dl_raw = df.loc[idx, "deadline"]
        est_h = float(df.loc[idx, "estimated_hours"] or 0)
        act_h = float(df.loc[idx, "actual_hours"] or 0)
        # 残工数（既に実績がある分を除く）
        remaining_h = max(0.0, est_h - act_h)

        # 必要日数（切り上げ整数演算: Python の -(-a//b) を利用）
        if remaining_h > 0.001:
            days_needed = max(1, -(-int(remaining_h * 100) // int(daily_h * 100)))
        else:
            days_needed = 0

        # NaN・空文字列・"None" 文字列を「納期なし」と扱う
        has_deadline = (dl_raw and str(dl_raw) not in ("", "nan", "None")
                        and dl_raw == dl_raw)

        # 最後のチケット（最初に処理）に納期がなく、親の納期があれば仮設定
        if first_row and not has_deadline and parent_deadline is not None:
            df.loc[idx, "deadline"] = parent_deadline.isoformat()
            has_deadline = True
            dl_raw = parent_deadline.isoformat()
        first_row = False

        if has_deadline:
            # 納期設定済み: 作業期間から実行開始日を逆算して prev_start に保持
            dl = datetime.date.fromisoformat(str(dl_raw))
            if days_needed > 0:
                # 例: 3 日必要で納期 3/5 → 3/3 から開始
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
    「完了」チケットが done になったとき、親ノードを自動 done にする（仕様 2.2）。
    全兄弟ノードが done/cancel/deleted なら親も done にする。
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

    # 親の子ノードに cancel/deleted/done 以外の未完了ノードがあるか確認
    siblings = df_nodes[
        (df_nodes["parent_id"] == parent_id)
        & (~df_nodes["status"].isin(["cancel", "deleted", "done"]))
    ]
    # 全て完了・取消・削除の場合のみ親を done にする
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
    複合条件でノードをフィルタリングして返す。
    deleted ステータスは常に除外する。各条件は AND で結合される。
    """
    if df.empty:
        return df

    result = df.copy()

    if keyword:
        # タイトルまたはメモにキーワードが含まれる行を抽出（大文字小文字を区別しない）
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
    連続するスロット間の空きを休憩として計算する。
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


# ---------- 週報・月報レポート生成 ----------

def _date_str(v) -> str:
    """日付値を YYYY-MM-DD 文字列に正規化する。欠損(None/NaN/空)は空文字を返す。"""
    if v is None or v != v:
        return ""
    s = str(v)
    if s in ("", "nan", "None", "NaT"):
        return ""
    return s[:10]


def _md_escape(s: str) -> str:
    """Markdown テーブルセル用にパイプ文字をエスケープする"""
    return str(s).replace("|", "\\|").replace("\n", " ")


def calc_period_hours(df_daily: pd.DataFrame, ticket_idxs: list,
                      date_from: str, date_to: str) -> dict:
    """
    指定期間における各チケットの実績工数(h)を daily_schedule から一括集計する。
    日付境界が空文字の場合、その側は無制限として扱う。
    戻り値: {ticket_idx: hours}
    """
    counts: dict = {idx: 0 for idx in ticket_idxs}
    ticket_set = set(ticket_idxs)
    if df_daily.empty or not ticket_set:
        return {idx: 0.0 for idx in ticket_idxs}
    for row_idx in df_daily.index:
        # IDX 先頭10文字が日付 (YYYY-MM-DD)
        date_part = str(row_idx)[:10]
        if date_from and date_part < date_from:
            continue
        if date_to and date_part > date_to:
            continue
        for col in DAILY_TIME_COLS:
            if col not in df_daily.columns:
                continue
            val = df_daily.loc[row_idx, col]
            if val in ticket_set:
                counts[val] = counts.get(val, 0) + 1
    return {idx: round(cnt * 0.25, 2) for idx, cnt in counts.items()}


def collect_descendant_tickets(df_nodes: pd.DataFrame, root_idx: str) -> List[str]:
    """root_idx 配下の全 Ticket IDX を再帰的に収集する（deleted 除外）"""
    result: List[str] = []
    if df_nodes.empty:
        return result
    stack = [root_idx]
    while stack:
        cur = stack.pop()
        children = df_nodes[df_nodes["parent_id"] == cur]
        for cidx in children.index:
            if str(children.loc[cidx, "status"]) == "deleted":
                continue
            if str(children.loc[cidx, "node_type"]) == "ticket":
                result.append(cidx)
            else:
                stack.append(cidx)
    return result


def collect_report_data(df_nodes: pd.DataFrame, df_daily: pd.DataFrame,
                        root_idx: str, date_from: str, date_to: str,
                        display_name_func=None) -> dict:
    """
    週報・月報用に root_idx（通常 Project4）配下のチケットを期間集計して分類する。

    戻り値 dict のキー:
        root_title, date_from, date_to,
        completed   : 期間内に完了したチケット行のリスト
        in_progress : 進行中（期間内に工数投入あり or 実績あり）の未完了チケット
        upcoming    : 来期間（同じ長さの次期間）に開始/納期が入る未着手チケット
        overdue     : 納期超過の未完了チケット
        approaching : 納期 7 日以内の未完了チケット
        period_hours_total : 期間内投入工数の合計(h)
        task_hours  : {タスク名: 期間内工数} の内訳
    """
    name = display_name_func or (lambda s: s)
    data = {
        "root_title": "", "date_from": date_from, "date_to": date_to,
        "completed": [], "in_progress": [], "upcoming": [],
        "overdue": [], "approaching": [],
        "period_hours_total": 0.0, "task_hours": {},
    }
    if df_nodes.empty or root_idx not in df_nodes.index:
        return data
    data["root_title"] = str(df_nodes.loc[root_idx, "title"])

    tickets = collect_descendant_tickets(df_nodes, root_idx)
    hours = calc_period_hours(df_daily, tickets, date_from, date_to)

    today = datetime.date.today()
    today_s = today.isoformat()
    approach_s = (today + datetime.timedelta(days=7)).isoformat()

    # 来期間 = 終了日翌日から同じ日数分
    next_from, next_to = "", ""
    try:
        d_from = datetime.date.fromisoformat(date_from)
        d_to = datetime.date.fromisoformat(date_to)
        span = (d_to - d_from).days + 1
        next_from = (d_to + datetime.timedelta(days=1)).isoformat()
        next_to = (d_to + datetime.timedelta(days=span)).isoformat()
    except (ValueError, TypeError):
        pass

    for idx in tickets:
        r = df_nodes.loc[idx]
        status = str(r.get("status", ""))
        if status == "cancel":
            continue
        pid = str(r.get("parent_id", ""))
        task_title = str(df_nodes.loc[pid, "title"]) if pid in df_nodes.index else ""
        row = {
            "title":           str(r.get("title", "")),
            "task":            task_title,
            "assigned_to":     name(str(r.get("assigned_to", ""))),
            "estimated":       float(r.get("estimated_hours", 0) or 0),
            "actual":          float(r.get("actual_hours", 0) or 0),
            "period_hours":    hours.get(idx, 0.0),
            "deadline":        _date_str(r.get("deadline")),
            "actual_end":      _date_str(r.get("actual_end")),
            "start_available": _date_str(r.get("start_available")),
        }
        data["period_hours_total"] += row["period_hours"]
        if row["period_hours"] > 0:
            data["task_hours"][task_title] = round(
                data["task_hours"].get(task_title, 0.0) + row["period_hours"], 2)

        if status == "done":
            # 期間内に完了したものだけ実績として報告
            if row["actual_end"] and date_from <= row["actual_end"] <= date_to:
                data["completed"].append(row)
            continue

        # 以降は未完了（todo / regularly）
        if row["period_hours"] > 0 or row["actual"] > 0:
            data["in_progress"].append(row)
        elif next_from and (
            (row["start_available"] and next_from <= row["start_available"] <= next_to)
            or (row["deadline"] and next_from <= row["deadline"] <= next_to)
        ):
            data["upcoming"].append(row)

        # 納期リスク（進行中・未着手を問わず判定）
        if row["deadline"]:
            if row["deadline"] < today_s:
                data["overdue"].append(row)
            elif row["deadline"] <= approach_s:
                data["approaching"].append(row)

    data["period_hours_total"] = round(data["period_hours_total"], 2)
    return data


def _ticket_table(rows: list, columns: list) -> List[str]:
    """
    チケット行リストから Markdown テーブル行を生成する。
    columns: (ヘッダー名, 行辞書から値を取り出す関数) のリスト
    """
    if not rows:
        return ["（なし）"]
    lines = [
        "| " + " | ".join(h for h, _ in columns) + " |",
        "|" + "---|" * len(columns),
    ]
    for r in rows:
        lines.append("| " + " | ".join(_md_escape(f(r)) for _, f in columns) + " |")
    return lines


def build_report_markdown(data: dict, mode: str = "weekly") -> str:
    """
    collect_report_data の結果から週報/月報の Markdown を組み立てる。
    mode: "weekly" または "monthly"
    """
    d_from, d_to = data["date_from"], data["date_to"]
    title = data["root_title"]
    if mode == "monthly":
        head = f"# 月報 {d_from[:7]}: {title}"
        period_label = "今月"
        next_label = "来月"
    else:
        try:
            iso = datetime.date.fromisoformat(d_from).isocalendar()
            week_s = f"{iso[0]}-W{iso[1]:02d}"
        except (ValueError, TypeError):
            week_s = d_from
        head = f"# 週報 {week_s}: {title}"
        period_label = "今週"
        next_label = "来週"

    lines = [
        head,
        "",
        f"- 期間: {d_from} 〜 {d_to}",
        f"- 生成: Schedule Manager ({datetime.date.today().isoformat()})",
        "",
        f"## {period_label}の実績（完了）",
        "",
    ]
    lines += _ticket_table(data["completed"], [
        ("チケット", lambda r: r["title"]),
        ("タスク",   lambda r: r["task"]),
        ("担当",     lambda r: r["assigned_to"]),
        ("工数(実績/見積)", lambda r: f"{r['actual']:.2f}h / {r['estimated']:.2f}h"),
        ("完了日",   lambda r: r["actual_end"]),
    ])

    lines += ["", "## 進行中", ""]
    lines += _ticket_table(data["in_progress"], [
        ("チケット", lambda r: r["title"]),
        ("タスク",   lambda r: r["task"]),
        ("担当",     lambda r: r["assigned_to"]),
        ("進捗工数", lambda r: f"{r['actual']:.2f}h / {r['estimated']:.2f}h"),
        ("納期",     lambda r: r["deadline"] or "-"),
    ])

    lines += ["", f"## {period_label}の投入工数", "",
              f"合計: **{data['period_hours_total']:.2f}h**", ""]
    if data["task_hours"]:
        lines += ["| タスク | 工数(h) |", "|---|---|"]
        for task, h in sorted(data["task_hours"].items(),
                              key=lambda kv: kv[1], reverse=True):
            lines.append(f"| {_md_escape(task)} | {h:.2f} |")

    lines += ["", f"## {next_label}の予定", ""]
    lines += _ticket_table(data["upcoming"], [
        ("チケット",   lambda r: r["title"]),
        ("タスク",     lambda r: r["task"]),
        ("見積(h)",    lambda r: f"{r['estimated']:.2f}"),
        ("開始可能日", lambda r: r["start_available"] or "-"),
        ("納期",       lambda r: r["deadline"] or "-"),
    ])

    lines += ["", "## 納期リスク", ""]
    risk_rows = ([dict(r, _risk="超過") for r in data["overdue"]]
                 + [dict(r, _risk="接近") for r in data["approaching"]])
    lines += _ticket_table(risk_rows, [
        ("区分",     lambda r: r["_risk"]),
        ("チケット", lambda r: r["title"]),
        ("タスク",   lambda r: r["task"]),
        ("担当",     lambda r: r["assigned_to"]),
        ("納期",     lambda r: r["deadline"]),
    ])

    return "\n".join(lines) + "\n"


def report_filename(mode: str, date_from: str, root_title: str) -> str:
    """レポートのファイル名を生成する（ファイル名禁止文字は _ に置換）"""
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", root_title).strip() or "report"
    if mode == "monthly":
        period = date_from[:7]  # YYYY-MM
    else:
        try:
            iso = datetime.date.fromisoformat(date_from).isocalendar()
            period = f"{iso[0]}-W{iso[1]:02d}"
        except (ValueError, TypeError):
            period = date_from
    prefix = "monthly" if mode == "monthly" else "weekly"
    return f"{prefix}_{period}_{safe_title}.md"
