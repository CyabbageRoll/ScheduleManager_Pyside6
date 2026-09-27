"""
logic.py - スケジューリング・テキストパーサー・検索・エクスポート
"""
import datetime
import calendar
import csv
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

import pandas as pd

from db import (
    NODE_TYPES, STATUS_LIST, DAILY_TIME_COLS,
    create_initial_node, generate_idx, build_auto_children, daily_sch_idx,
    INBOX_PARENT,
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


# ---------- プロジェクトテンプレート一括作成 ----------

def parse_template_text(text: str, p4_idx: str, owner: str,
                        df_nodes: pd.DataFrame) -> tuple:
    """
    テンプレートテキストから Task / Ticket の pd.Series リストを生成する。

    フォーマット（# はコメント行）:
        > Task名         … 行頭 > で新しい Task を開始
        タイトル, 優先度, 見積工数(h), 開始可能日, 納期, ステータス, メモ
                         … parse_ticket_text と同じチケット書式
    Task には「詳細作成」「完了」チケットが自動付与される（spec 2.2）。

    戻り値:
        (ノードリスト[pd.Series]（Task → 自動チケット → テンプレチケットの順）,
         エラーリスト[str])
    """
    results: List[pd.Series] = []
    errors: List[str] = []
    existing_task_titles = set(
        df_nodes[(df_nodes["parent_id"] == p4_idx)
                 & (df_nodes["node_type"] == "task")]["title"].tolist()
    ) if not df_nodes.empty else set()

    # 既存の兄弟ノードの最大 priority から連番を振る
    next_priority = 1
    if not df_nodes.empty:
        siblings = df_nodes[df_nodes["parent_id"] == p4_idx]
        if not siblings.empty:
            next_priority = int(siblings["priority"].max()) + 1

    current_task: Optional[pd.Series] = None
    buffer: List[str] = []

    def _flush() -> None:
        """貯めたチケット行を現在の Task 配下としてパースする"""
        nonlocal current_task, buffer
        if current_task is None:
            return
        tickets, errs = parse_ticket_text(
            "\n".join(buffer), current_task.name, owner, df_nodes)
        results.extend(tickets)
        errors.extend(errs)
        current_task = None
        buffer = []

    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(">"):
            _flush()
            title = line[1:].strip()
            if not title:
                errors.append(f"行 {line_no}: Task 名が空です → {raw}")
                continue
            if title in existing_task_titles:
                errors.append(f"行 {line_no}: 同名 Task が既に存在します → {title}")
                continue
            task_ds = create_initial_node(owner, "task", title, p4_idx,
                                          next_priority)
            next_priority += 1
            results.append(task_ds)
            # 「詳細作成」「完了」チケットを自動付与
            results.extend(build_auto_children(task_ds, owner))
            existing_task_titles.add(title)
            current_task = task_ds
            buffer = []
        else:
            if current_task is None:
                errors.append(
                    f"行 {line_no}: Task 行（> 名前）より前にチケット行があります → {raw}")
                continue
            buffer.append(raw)
    _flush()
    return results, errors


# ---------- デイリーワークログ Markdown 出力 ----------

def collect_daily_segments(df_daily: pd.DataFrame, df_nodes: pd.DataFrame,
                           date_str: str, user: str) -> List[dict]:
    """
    指定日の daily_schedule スロットを「同一チケットの連続区間」にまとめて返す。
    戻り値: [{"from": "09:00", "to": "09:30", "ticket_idx", "title", "task",
              "hours": 0.5}, ...]（時刻順）
    """
    idx = daily_sch_idx(date_str, user)
    raw: List[tuple] = []  # (開始スロットi, 終了スロットi(排他), ticket_idx)
    if not df_daily.empty and idx in df_daily.index:
        ds = df_daily.loc[idx]
        prev, start_i = "", 0
        for i in range(len(DAILY_TIME_COLS) + 1):
            col = DAILY_TIME_COLS[i] if i < len(DAILY_TIME_COLS) else None
            val = ""
            if col is not None and col in ds.index and ds[col]:
                val = str(ds[col])
            if val != prev:
                if prev:
                    raw.append((start_i, i, prev))
                start_i = i
                prev = val

    segments: List[dict] = []
    for s, e, t_idx in raw:
        end_s = ("24:00" if e >= len(DAILY_TIME_COLS)
                 else col_to_hhmm(DAILY_TIME_COLS[e]))
        title, task_title = t_idx, ""
        if df_nodes is not None and not df_nodes.empty \
                and t_idx in df_nodes.index:
            title = str(df_nodes.loc[t_idx, "title"])
            pid = str(df_nodes.loc[t_idx, "parent_id"])
            if pid in df_nodes.index:
                task_title = str(df_nodes.loc[pid, "title"])
        segments.append({
            "from": col_to_hhmm(DAILY_TIME_COLS[s]), "to": end_s,
            "ticket_idx": t_idx, "title": title, "task": task_title,
            "hours": (e - s) * 0.25,
        })
    return segments


def build_daily_log_markdown(df_daily: pd.DataFrame, df_nodes: pd.DataFrame,
                             df_log, date_str: str, user: str) -> str:
    """
    指定日の作業実績（daily_schedule のスロットを連続区間にまとめたもの）と
    日次ログ（体調・勤務地・連絡事項）を Markdown 化する。
    """
    idx = daily_sch_idx(date_str, user)
    lines = [f"# 作業ログ {date_str}", ""]

    # 勤務時間サマリー
    wh = calc_working_hours(df_daily, idx)
    if wh["total"] > 0:
        lines.append(
            f"- 勤務: {col_to_hhmm(wh['from'])}〜{col_to_hhmm(wh['to'])}"
            f"（休憩 {wh['break']:.2f}h、合計 {wh['total']:.2f}h）")
    else:
        lines.append("- 勤務: 記録なし")

    # daily_log（体調・勤務地・連絡事項）
    notes = ""
    if df_log is not None and not df_log.empty and idx in df_log.index:
        lg = df_log.loc[idx]
        health = str(lg.get("health_status", "") or "")
        place = str(lg.get("work_place", "") or "")
        if health or place:
            lines.append(f"- 体調: {health or '-'} / 勤務地: {place or '-'}")
        notes = str(lg.get("notes", "") or "")
    lines += ["", "## 作業内訳", ""]

    segments = collect_daily_segments(df_daily, df_nodes, date_str, user)
    if segments:
        lines += ["| 時間帯 | チケット | タスク | 時間(h) |", "|---|---|---|---|"]
        for seg in segments:
            lines.append(
                f"| {seg['from']}〜{seg['to']}"
                f" | {_md_escape(seg['title'])} | {_md_escape(seg['task'])}"
                f" | {seg['hours']:.2f} |")
    else:
        lines.append("（記録なし）")

    lines += ["", "## メモ・連絡事項", ""]
    lines.append(notes if notes.strip() else "（なし）")
    return "\n".join(lines) + "\n"


def build_team_log_markdown(df_daily, df_daily_log, all_permanent: dict,
                            members: list, name_map: dict,
                            date_from: str, date_to: str) -> str:
    """
    指定期間のチーム日次ログ（勤務状況・健康・連絡事項）を Markdown 表で出力する。

    日付 × メンバーで1行を作り、勤務時間・勤務場所・残業・健康状態・安全宣言・
    連絡事項を表にまとめる。データが全く無い行はスキップ。末尾に常時メモ表を付ける。
    """
    try:
        d_from = datetime.date.fromisoformat(date_from)
        d_to = datetime.date.fromisoformat(date_to)
    except (ValueError, TypeError):
        return "# チーム日次ログ\n\n期間の指定が不正です。\n"
    if d_to < d_from:
        d_from, d_to = d_to, d_from

    lines = [f"# チーム日次ログ {date_from} 〜 {date_to}", ""]
    lines += [
        "| 日付 | メンバー | 勤務時間 | 勤務場所 | 残業 | 健康状態 | 安全宣言 | 連絡事項 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    has_log = df_daily_log is not None and not df_daily_log.empty
    n_days = (d_to - d_from).days + 1
    for i in range(n_days):
        d = (d_from + datetime.timedelta(days=i)).isoformat()
        for member in members:
            idx = daily_sch_idx(d, member)
            wh = calc_working_hours(df_daily, idx)
            if wh["total"] > 0:
                wh_str = (f"{col_to_hhmm(wh['from'])}〜{col_to_hhmm(wh['to'])}"
                          f" [{wh['total']:.2f}h](休{wh['break']:.2f}h)")
            else:
                wh_str = ""
            place = overwork = health = safety = notes = ""
            if has_log and idx in df_daily_log.index:
                row = df_daily_log.loc[idx]
                place = str(row.get("work_place", "") or "")
                overwork = str(row.get("overwork", "") or "")
                health = str(row.get("health_status", "") or "")
                safety = str(row.get("safety", "") or "")
                notes = str(row.get("notes", "") or "")
            # 勤務時間もログも全く無い行はスキップ
            if not (wh_str or place or overwork or health or safety or notes):
                continue
            name = name_map.get(member, member)
            lines.append(
                f"| {d} | {_md_escape(name)} | {_md_escape(wh_str) or '-'}"
                f" | {_md_escape(place) or '-'} | {_md_escape(overwork) or '-'}"
                f" | {_md_escape(health) or '-'} | {_md_escape(safety) or '-'}"
                f" | {_md_escape(notes) or '-'} |")

    # 常時メモ
    lines += ["", "## 常時メモ", ""]
    perm_rows = [(m, str((all_permanent or {}).get(m, "") or "")) for m in members]
    perm_rows = [(m, v) for m, v in perm_rows if v.strip()]
    if perm_rows:
        lines += ["| メンバー | 常時メモ |", "|---|---|"]
        for m, v in perm_rows:
            lines.append(f"| {_md_escape(name_map.get(m, m))} | {_md_escape(v)} |")
    else:
        lines.append("（なし）")
    return "\n".join(lines) + "\n"


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


def schedule_tickets(df: pd.DataFrame, member: str, daily_h: float, holidays,
                     today: Optional[datetime.date] = None, factor: float = 1.0) -> dict:
    """
    担当者の全チケット（全Task・全Project横断）を対象に EDF＋整合どりスケジューリングを行う。

    アルゴリズム:
      ① 各Task配下のチケットごとに仮納期を逆算（fill_deadlines_backward）
         → 最後のチケットに納期がなければ親Taskの納期を使用
      ② 全チケットをまとめてEDFソート（仮納期昇順）
      ③ 1日の作業時間（Configで設定）を使ってグローバルに作業日を割り当て
         → 開始可能日制約で飛ばしたチケットは優先1で再チェック
      ④ 作業日が納期より遅い場合はアラート（呼び出し側でd > deadlineで判定）

    factor: 残り工数の見積係数（E2 納期リスク予報用。1.0 ならガントと同じ計算）
      残り工数 = 見積 × factor − 実績

    Returns:
        {ticket_idx: (work_days: set[date], start_avail: date|None, deadline: date|None)}
    """
    daily_h = max(0.25, float(daily_h))
    today = today or datetime.date.today()

    holidays_upper = {h.strip().upper() for h in holidays}
    day_abbrevs = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

    def is_holiday(d: datetime.date) -> bool:
        return day_abbrevs[d.weekday()] in holidays_upper

    def parse_date(val) -> Optional[datetime.date]:
        if val and str(val) not in ("", "nan", "None"):
            try:
                return datetime.date.fromisoformat(str(val))
            except Exception:
                pass
        return None

    # ① 全Taskを横断して担当者のチケットを収集し、Taskごとに仮納期を計算
    # done/cancel のTaskは完了済みのため除外（配下のチケットもスケジュール不要）
    all_tasks = df[
        (df["node_type"] == "task")
        & (~df["status"].isin(["done", "cancel", "deleted"]))
    ]

    collected: list[pd.DataFrame] = []
    for task_idx in all_tasks.index:
        # このタスク配下で担当者のチケット（done/cancel/deleted除外）
        # done は既に完了済みのため未来の作業スロットを消費しない
        tickets = df[
            (df["parent_id"] == task_idx)
            & (df["node_type"] == "ticket")
            & (~df["status"].isin(["done", "cancel", "deleted"]))
            & (df.get("assigned_to", pd.Series(dtype=str)) == member)
        ].copy()
        if tickets.empty:
            continue

        # 親タスクの納期（最後のチケットに納期がない場合のフォールバック）
        parent_dl = parse_date(df.loc[task_idx].get("deadline")) if task_idx in df.index else None

        # 仮納期を填入
        tickets = fill_deadlines_backward(tickets, daily_h, parent_deadline=parent_dl)

        # 同一Task内で開始可能日を後方チケットへ伝播
        # 例) A:3/3, B:-(なし), C:3/7, D:-(なし) → B:3/3, D:3/7
        tickets = tickets.sort_values("priority")
        running_start: Optional[datetime.date] = None
        for t_idx in tickets.index:
            raw = tickets.loc[t_idx, "start_available"]
            explicit: Optional[datetime.date] = None
            if raw and str(raw) not in ("", "nan", "None"):
                try:
                    explicit = datetime.date.fromisoformat(str(raw))
                except Exception:
                    pass
            if explicit is not None:
                effective = max(running_start, explicit) if running_start else explicit
                running_start = effective
                tickets.loc[t_idx, "start_available"] = effective.isoformat()
            elif running_start is not None:
                tickets.loc[t_idx, "start_available"] = running_start.isoformat()

        collected.append(tickets)

    if not collected:
        return {}

    # ② 全チケットをマージしてEDFソート
    all_tickets = pd.concat(collected)
    all_tickets["_dl_sort"] = pd.to_datetime(all_tickets["deadline"], errors="coerce")
    # 納期が過去（today以前）の場合、EDFソートキーをtodayに統一する
    # → 全ての期限超過チケットを同等の緊急度として扱い、priorityフィールドで順序を決定
    # ※ アラート表示（d > deadline）は元の納期で正しく評価されるため影響なし
    pd_today = pd.Timestamp(today)
    all_tickets["_dl_sort"] = all_tickets["_dl_sort"].clip(lower=pd_today)
    all_tickets = all_tickets.sort_values(["_dl_sort", "priority"], na_position="last")
    edf_order = list(all_tickets.index)

    # ③ 整合どりスケジューリング（全チケット横断・1日作業時間=daily_h）
    unscheduled = list(edf_order)
    skipped: list = []
    cursor = today
    cursor_h = 0.0
    result = {}

    while unscheduled or skipped:
        candidates = skipped + [t for t in unscheduled if t not in skipped]

        scheduled_any = False

        for t_idx in candidates:
            if t_idx not in all_tickets.index:
                if t_idx in skipped:     skipped.remove(t_idx)
                if t_idx in unscheduled: unscheduled.remove(t_idx)
                scheduled_any = True
                break

            tr = all_tickets.loc[t_idx]
            start_avail = parse_date(tr.get("start_available"))
            deadline    = parse_date(tr.get("deadline"))

            if start_avail and cursor < start_avail:
                if t_idx not in skipped:
                    skipped.append(t_idx)
                if t_idx in unscheduled:
                    unscheduled.remove(t_idx)
                continue

            if t_idx in skipped:     skipped.remove(t_idx)
            if t_idx in unscheduled: unscheduled.remove(t_idx)

            est_h      = float(tr.get("estimated_hours", 0) or 0)
            act_h      = float(tr.get("actual_hours", 0) or 0)
            remaining_h = max(0.0, est_h * factor - act_h)
            # todoで残り工数が0以下の場合は0.5hとして作業日を算出
            t_status = str(tr.get("status", ""))
            if remaining_h < 0.001 and t_status == "todo":
                remaining_h = 0.5

            work_days: set = set()
            if remaining_h > 0.001:
                h_left   = remaining_h
                d        = cursor
                h_in_day = cursor_h
                itr      = 0
                while h_left > 0.001 and itr < 1000:
                    if is_holiday(d):
                        d += datetime.timedelta(days=1); h_in_day = 0.0; itr += 1; continue
                    avail = daily_h - h_in_day
                    if avail <= 0.001:
                        d += datetime.timedelta(days=1); h_in_day = 0.0; itr += 1; continue
                    used = min(avail, h_left)
                    work_days.add(d)
                    h_left -= used; h_in_day += used
                    if h_in_day >= daily_h - 0.001:
                        d += datetime.timedelta(days=1); h_in_day = 0.0
                    itr += 1
                cursor   = d
                cursor_h = h_in_day

            result[t_idx] = (work_days, start_avail, deadline)
            scheduled_any = True
            break

        if not scheduled_any:
            all_pending = list(dict.fromkeys(skipped + unscheduled))
            next_dates  = [
                parse_date(all_tickets.loc[t, "start_available"])
                for t in all_pending if t in all_tickets.index
            ]
            next_dates = [nd for nd in next_dates if nd is not None]
            if next_dates:
                cursor   = min(next_dates)
                cursor_h = 0.0
            else:
                break

    return result


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


# 未完了とみなすステータス（仕様 2.3 の判定に使用）
_OPEN_STATUSES = ("todo", "regularly")


def status_change_error(df_nodes: pd.DataFrame, idx: str,
                        new_status: str) -> Optional[str]:
    """
    仕様 2.3 のステータス変更制約を判定する。変更不可なら理由、可なら None を返す。
      - 未完了（todo/regularly）の子ノードがある親は done にできない
      - 親ノードが done のとき、子ノードを todo/regularly に戻せない
      - Inbox（Task 未設定）のチケットは done / regularly にできない（振り分けが先）
    """
    if idx not in df_nodes.index:
        return None
    if (str(df_nodes.loc[idx, "parent_id"]) == INBOX_PARENT
            and new_status in ("done", "regularly")):
        return "Inbox のチケットは Task へ振り分けてから変更してください"
    if new_status == "done":
        open_children = df_nodes[(df_nodes["parent_id"] == idx)
                                 & (df_nodes["status"].isin(_OPEN_STATUSES))]
        if not open_children.empty:
            return "未完了の子ノードがあるため done にできません（子を先に完了させてください）"
    elif new_status in _OPEN_STATUSES:
        pid = str(df_nodes.loc[idx, "parent_id"] or "")
        if pid in df_nodes.index and str(df_nodes.loc[pid, "status"]) == "done":
            return "親ノードが done のため todo / regularly に戻せません"
    return None


def delete_block_reason(df_nodes: pd.DataFrame, idx: str, user: str) -> Optional[str]:
    """論理削除できない理由を返す（削除可能なら None）。仕様 2.3 の削除制約。"""
    if idx not in df_nodes.index:
        return "対象ノードが見つかりません"
    if str(df_nodes.loc[idx, "assigned_to"]) != user:
        return "他ユーザーのデータは削除できません"
    if float(df_nodes.loc[idx, "actual_hours"] or 0) > 0:
        return "実績工数が記録されているため削除できません"
    # 論理削除済みの子は「存在しない」ものとして扱う
    children = df_nodes[(df_nodes["parent_id"] == idx)
                        & (df_nodes["status"] != "deleted")]
    if not children.empty:
        return "子ノードが存在するため削除できません"
    return None


def apply_status(df_nodes: pd.DataFrame, idx: str, new_status: str) -> List[str]:
    """
    ステータスを変更し、付随処理をまとめて行う（df_nodes をその場で更新）。
      - done にしたら actual_end（実績完了日）に当日を設定、done 以外に戻したらクリア
      - 「完了」チケットが done になり兄弟が全て完了なら親も自動 done（仕様 2.2）
    戻り値: ステータスを変更した IDX のリスト（自動 done の親を含む）
    """
    today = datetime.date.today().isoformat()
    changed: List[str] = []

    def _set(i: str, s: str) -> None:
        df_nodes.loc[i, "status"] = s
        df_nodes.loc[i, "updated_at"] = today
        if s == "done":
            if not _date_str(df_nodes.loc[i, "actual_end"]):
                df_nodes.loc[i, "actual_end"] = today
        else:
            df_nodes.loc[i, "actual_end"] = None
        changed.append(i)

    if idx not in df_nodes.index:
        return changed
    _set(idx, new_status)
    for pid in check_auto_done(df_nodes, idx):
        if str(df_nodes.loc[pid, "status"]) != "done":
            _set(pid, "done")
    return changed


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
        # regex=False: 「(」「.」等を正規表現ではなく文字として扱う
        kw = keyword.lower()
        mask = (
            result.get("title", pd.Series(dtype=str)).fillna("").str.lower()
            .str.contains(kw, regex=False)
            | result.get("memo", pd.Series(dtype=str)).fillna("").str.lower()
            .str.contains(kw, regex=False)
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


ANALYSIS_PERIODS = ["全期間", "今週", "先週", "今月", "先月", "今年度"]


def analysis_period(name: str, today: Optional[datetime.date] = None) -> tuple:
    """
    工数分析の期間プリセットを (開始日, 終了日) の ISO 文字列で返す。
    全期間は ("", "")。週は月曜始まり、年度は 4 月始まり。
    """
    today = today or datetime.date.today()
    if name == "今週" or name == "先週":
        start = today - datetime.timedelta(days=today.weekday())
        if name == "先週":
            start -= datetime.timedelta(days=7)
        return start.isoformat(), (start + datetime.timedelta(days=6)).isoformat()
    if name == "今月" or name == "先月":
        first = today.replace(day=1)
        if name == "先月":
            first = (first - datetime.timedelta(days=1)).replace(day=1)
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        return first.isoformat(), last.isoformat()
    if name == "今年度":
        fy = today.year if today.month >= 4 else today.year - 1
        return datetime.date(fy, 4, 1).isoformat(), datetime.date(fy + 1, 3, 31).isoformat()
    return "", ""


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


def _safe_name(s: str) -> str:
    """ファイル名・見出し用にパス禁止文字と区切り記号を _ へ置換する"""
    return re.sub(r'[\\/:*?"<>|]', "_", str(s)).strip() or "_"


def ancestor_of_type(df_nodes: pd.DataFrame, idx: str, ntype: str) -> Optional[str]:
    """idx 自身または祖先で node_type==ntype の最初のノードIDXを返す（無ければ None）"""
    cur = idx
    while cur and cur != "0" and cur in df_nodes.index:
        if str(df_nodes.loc[cur, "node_type"]) == ntype:
            return cur
        cur = str(df_nodes.loc[cur, "parent_id"] or "")
    return None


def node_path_titles(df_nodes: pd.DataFrame, idx: str) -> List[str]:
    """ルート→idx のタイトル列（["P1名","P2名",…,"item名"]）を返す"""
    titles: List[str] = []
    cur = idx
    while cur and cur != "0" and cur in df_nodes.index:
        titles.append(str(df_nodes.loc[cur, "title"]))
        cur = str(df_nodes.loc[cur, "parent_id"] or "")
    titles.reverse()
    return titles


def report_target(df_nodes: pd.DataFrame, idx: str):
    """
    レポート保存先の (p1_idx, p1_title, p2_idx, p2_title) を返す。
    - P2配下（P3/P4/Task/Ticket）または P2自身 → P1・P2 を特定して返す
    - P1 / 対象外 / 不明 → None
    """
    if not idx or idx not in df_nodes.index:
        return None
    p2 = ancestor_of_type(df_nodes, idx, "project2")
    if not p2:
        return None
    p1 = ancestor_of_type(df_nodes, p2, "project1")
    if not p1:
        return None
    return (p1, str(df_nodes.loc[p1, "title"]),
            p2, str(df_nodes.loc[p2, "title"]))


def report_p2_path(out_dir: str, df_nodes: pd.DataFrame, idx: str,
                   yyyymm: str) -> Optional[Path]:
    """選択アイテムが属する P2 の、指定月の Markdown ファイル Path を返す（対象外は None）。
    形式: <out_dir>/reports/<yyyymm>_<p2idx>_<p1名>_<p2名>.md"""
    tgt = report_target(df_nodes, idx)
    if tgt is None or not out_dir:
        return None
    _p1, p1_title, p2_idx, p2_title = tgt
    fname = f"{yyyymm}_{_safe_name(p2_idx)}_{_safe_name(p1_title)}_{_safe_name(p2_title)}.md"
    return Path(out_dir) / "reports" / fname


def section_header(item_idx: str, item_title: str) -> str:
    """アイテムのセクション見出し（### IDX_<idx>_<名>）を返す"""
    return f"### IDX_{item_idx}_{item_title}"


def _section_bounds(lines: List[str], item_idx: str):
    """lines 内で item_idx のセクション [開始行, 本文開始, 終了)（終了は次のH1〜H3/EOF）を返す。
    見つからなければ None。"""
    head_prefix = f"### IDX_{item_idx}"
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(head_prefix) and (
                ln[len(head_prefix):len(head_prefix) + 1] in ("", "_", " ")):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,3}\s", lines[j]):
            end = j
            break
    return start, start + 1, end


def read_section(md_text: str, item_idx: str) -> Optional[str]:
    """md からアイテムのセクション本文を返す（見出し行は含まない）。無ければ None。"""
    lines = md_text.splitlines()
    b = _section_bounds(lines, item_idx)
    if b is None:
        return None
    _start, body_start, end = b
    return "\n".join(lines[body_start:end]).strip("\n")


def upsert_section(md_text: str, item_idx: str, item_title: str,
                   body: str) -> str:
    """md の該当 IDX セクションの本文を body で置換する。無ければ末尾に追記する。"""
    header = section_header(item_idx, item_title)
    body = body.rstrip("\n")
    lines = md_text.splitlines() if md_text else []
    b = _section_bounds(lines, item_idx)
    if b is None:
        # 新規追記
        out = list(lines)
        if out and out[-1].strip() != "":
            out.append("")
        out.append(header)
        if body:
            out.append(body)
        out.append("")
        return "\n".join(out).rstrip("\n") + "\n"
    start, _body_start, end = b
    new_block = [header]
    if body:
        new_block.append(body)
    new_block.append("")  # セクション間の空行
    out = lines[:start] + new_block + lines[end:]
    return "\n".join(out).rstrip("\n") + "\n"


def extract_md_links(text: str):
    """本文中の [label](target) を全て抽出して [(label, target), …] を返す（複数対応）。"""
    if not text:
        return []
    return [(m.group(1).strip(), m.group(2).strip())
            for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text)]


# ---------- ダッシュボード・個人振り返り用集計 ----------

def find_deadline_alerts(df_nodes: pd.DataFrame, user: str = "",
                         within_days: int = 7) -> dict:
    """
    納期超過・接近（within_days 日以内）の未完了チケットを抽出する。
    user 指定時はそのユーザー担当のチケットのみ。納期昇順でソートして返す。
    戻り値: {"overdue": [行], "approaching": [行]}
    """
    res = {"overdue": [], "approaching": []}
    if df_nodes.empty:
        return res
    today_s = datetime.date.today().isoformat()
    limit_s = (datetime.date.today()
               + datetime.timedelta(days=within_days)).isoformat()
    tickets = df_nodes[(df_nodes["node_type"] == "ticket")
                       & (~df_nodes["status"].isin(["done", "cancel", "deleted"]))]
    if user:
        tickets = tickets[tickets["assigned_to"] == user]
    for idx, r in tickets.iterrows():
        deadline = _date_str(r.get("deadline"))
        if not deadline:
            continue
        pid = str(r.get("parent_id", ""))
        row = {
            "ticket_idx": idx,
            "title":      str(r.get("title", "")),
            "task":       (str(df_nodes.loc[pid, "title"]) if pid in df_nodes.index
                           else "📥Inbox" if pid == INBOX_PARENT else ""),
            "deadline":   deadline,
        }
        if deadline < today_s:
            res["overdue"].append(row)
        elif deadline <= limit_s:
            res["approaching"].append(row)
    res["overdue"].sort(key=lambda r: r["deadline"])
    res["approaching"].sort(key=lambda r: r["deadline"])
    return res


def _ancestor_title(df_nodes: pd.DataFrame, idx: str, target_type: str) -> str:
    """指定ノードの祖先のうち target_type のタイトルを返す（無ければ空文字）"""
    cur = idx
    while cur in df_nodes.index:
        pid = str(df_nodes.loc[cur, "parent_id"] or "")
        if not pid or pid == "0" or pid not in df_nodes.index:
            return ""
        if str(df_nodes.loc[pid, "node_type"]) == target_type:
            return str(df_nodes.loc[pid, "title"])
        cur = pid
    return ""


def calc_weekly_user_hours_by_p1(df_nodes: pd.DataFrame, df_daily: pd.DataFrame,
                                 user: str, weeks: int = 4) -> dict:
    """
    直近 weeks 週（今週を含む、月曜起点）のユーザー投入工数を
    週 × Project1 別に集計する。
    戻り値: {"weeks": ["W23", ...], "by_p1": {P1タイトル: [h, ...]}}
    """
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    week_ranges = [
        (monday - datetime.timedelta(days=7 * w),
         monday - datetime.timedelta(days=7 * w) + datetime.timedelta(days=6))
        for w in range(weeks - 1, -1, -1)
    ]
    # 自分の daily_schedule 行のみ対象にする
    if not df_daily.empty and "Owner" in df_daily.columns:
        df_user = df_daily[df_daily["Owner"] == user]
    else:
        df_user = df_daily
    tickets = (list(df_nodes[df_nodes["node_type"] == "ticket"].index)
               if not df_nodes.empty else [])

    labels: List[str] = []
    by_p1: dict = {}
    for w_i, (start, end) in enumerate(week_ranges):
        iso = start.isocalendar()
        labels.append(f"W{iso[1]:02d}")
        hours = calc_period_hours(df_user, tickets,
                                  start.isoformat(), end.isoformat())
        for t_idx, h in hours.items():
            if h <= 0:
                continue
            p1 = _ancestor_title(df_nodes, t_idx, "project1") or "(P1なし)"
            by_p1.setdefault(p1, [0.0] * weeks)[w_i] += h
    for k in by_p1:
        by_p1[k] = [round(v, 2) for v in by_p1[k]]
    return {"weeks": labels, "by_p1": by_p1}


def calc_estimate_accuracy(df_nodes: pd.DataFrame, user: str) -> List[dict]:
    """
    自分が完了したチケットの見積 vs 実績ペアを返す（見積 0 は除外）。
    ratio = 実績 ÷ 見積（1 超 = 見積より時間がかかった）
    """
    res: List[dict] = []
    if df_nodes.empty:
        return res
    done = df_nodes[(df_nodes["node_type"] == "ticket")
                    & (df_nodes["status"] == "done")
                    & (df_nodes["assigned_to"] == user)]
    for idx, r in done.iterrows():
        est = float(r.get("estimated_hours", 0) or 0)
        act = float(r.get("actual_hours", 0) or 0)
        if est > 0:
            res.append({
                "title": str(r.get("title", "")),
                "estimated": est, "actual": act,
                "ratio": round(act / est, 2),
            })
    return res


# ---------- クイック追加（Ctrl+N）・Inbox ----------

_WEEKDAY_MAP = {
    "月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_DAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_RELATIVE_DAYS = {"今日": 0, "きょう": 0, "本日": 0, "明日": 1, "あした": 1, "あす": 1,
                  "明後日": 2, "あさって": 2}
# 月日指定（9/10 等）が過去日のとき、この日数以内なら今年、超えたら翌年とみなす
_PAST_KEEP_DAYS = 60


def _nfkc(s: str) -> str:
    """全角英数・記号を半角へ揃える（NFKC 正規化）"""
    return unicodedata.normalize("NFKC", str(s))


def norm_key(s: str) -> str:
    """照合用キー: NFKC・小文字化・空白除去"""
    return re.sub(r"\s+", "", _nfkc(s).lower())


def _parse_hours_token(t: str) -> Optional[float]:
    """正規化済みの語を工数(h)として解釈する。該当しなければ None。"""
    m = re.fullmatch(r"(\d+(?:\.\d+)?|\.\d+)(?:h|hr|hrs|時間)", t)
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"(\d+)(?:m|min|分)", t)
    if m:
        return int(m.group(1)) / 60
    m = re.fullmatch(r"(\d+)(?:h|時間)(\d+)(?:m|min|分)", t)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    m = re.fullmatch(r"(\d+)時間半", t)
    if m:
        return int(m.group(1)) + 0.5
    # 小数点付きの数字は単位なしでも工数とみなす（整数のみはタイトル扱い）
    if re.fullmatch(r"\d*\.\d+", t):
        return float(t)
    return None


def _last_business_day(d_from: datetime.date, d_to: datetime.date,
                       holidays) -> Optional[datetime.date]:
    """[d_from, d_to] の中で最後の営業日（休日設定を除く）を返す。無ければ None。"""
    d = d_to
    while d >= d_from:
        if _DAY_ABBR[d.weekday()] not in holidays:
            return d
        d -= datetime.timedelta(days=1)
    return None


def _month_day_date(month: int, day: int,
                    today: datetime.date) -> Optional[datetime.date]:
    """年を省いた月日を日付にする。過去 60 日より前なら翌年とみなす。"""
    try:
        d = datetime.date(today.year, month, day)
    except ValueError:
        return None
    if d < today - datetime.timedelta(days=_PAST_KEEP_DAYS):
        try:
            d = datetime.date(today.year + 1, month, day)
        except ValueError:
            return None
    return d


def _parse_date_token(t: str, today: datetime.date,
                      holidays) -> Optional[datetime.date]:
    """正規化済みの語を日付として解釈する。該当しなければ None。"""
    if t in _RELATIVE_DAYS:
        return today + datetime.timedelta(days=_RELATIVE_DAYS[t])
    m = re.fullmatch(r"(\d+)日後", t)
    if m:
        return today + datetime.timedelta(days=int(m.group(1)))
    # 曜日: 水 / 水曜 / 水曜日 / (水) / wed / 来週水 / 来週の水曜
    m = re.fullmatch(r"(来週の?)?\(?([月火水木金土日]|[a-z]+?)(?:曜日|曜)?\)?", t)
    if m and m.group(2) in _WEEKDAY_MAP:
        target = _WEEKDAY_MAP[m.group(2)]
        if m.group(1):
            next_monday = today + datetime.timedelta(days=7 - today.weekday())
            return next_monday + datetime.timedelta(days=target)
        return today + datetime.timedelta(days=(target - today.weekday()) % 7)
    if t in ("今週", "今週中", "今週末"):
        sunday = today + datetime.timedelta(days=6 - today.weekday())
        return _last_business_day(today, sunday, holidays) or today
    if t == "月末":
        last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return _last_business_day(today, last, holidays) or today
    m = re.fullmatch(r"(\d{1,2})日", t)
    if m:
        day = int(m.group(1))
        y, mo = today.year, today.month
        for _ in range(13):  # 今日以降で最も近い「その日」
            if day <= calendar.monthrange(y, mo)[1]:
                d = datetime.date(y, mo, day)
                if d >= today:
                    return d
            y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        return None
    m = (re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
         or re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", t))
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = (re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
         or re.fullmatch(r"(\d{1,2})月(\d{1,2})日", t))
    if m:
        return _month_day_date(int(m.group(1)), int(m.group(2)), today)
    return None


def _parse_date_range(t: str, today: datetime.date, holidays):
    """「9/28〜10/2」等の範囲を (開始, 終了) で返す。範囲でなければ None。"""
    for sep in ("〜", "~", "-"):
        if sep in t:
            a, _, b = t.partition(sep)
            da = _parse_date_token(a, today, holidays)
            db_ = _parse_date_token(b, today, holidays)
            if da and db_:
                return da, db_
    return None


def parse_quick_add(text: str, today: Optional[datetime.date] = None,
                    holidays=("SAT", "SUN")) -> dict:
    """
    クイック追加の 1 行入力を解析する（ルールは 06_B2 仕様書の表のとおり）。
      - 空白（半角/全角）区切りの語ごとに 工数 / 日付 / @Task を判定。順不同
      - 空白直後の # / ＃ から行末はメモ（解析しない）。「…」内は必ずタイトル
      - どれにも当たらない語はタイトル
    戻り値: {"title", "hours", "hours_rounded", "deadline", "start",
             "task_query"(None=指定なし), "memo", "warnings": [], "hints": []}
    """
    today = today or datetime.date.today()
    holidays = {h.upper() for h in holidays}
    res = {"title": "", "hours": None, "hours_rounded": False,
           "deadline": None, "start": None, "task_query": None, "memo": "",
           "warnings": [], "hints": []}

    # メモ: 空白直後（または先頭）の # から行末まで
    body = text
    m = re.search(r"(?:^|\s)[#＃]", text)
    if m:
        res["memo"] = text[m.end():].strip()
        body = text[:m.start()]

    # 「…」/ "…" で囲んだ部分はそのままタイトルの語にする
    items = []  # (quoted, 語)
    pos = 0
    for q in re.finditer(r"「([^」]*)」|\"([^\"]*)\"", body):
        items += [(False, w) for w in body[pos:q.start()].split()]
        items.append((True, q.group(1) if q.group(1) is not None else q.group(2)))
        pos = q.end()
    items += [(False, w) for w in body[pos:].split()]

    title_words = []
    for quoted, raw in items:
        if quoted:
            title_words.append(raw)
            continue
        t = _nfkc(raw).lower()
        if t.startswith("@"):
            if res["task_query"] is not None:
                res["warnings"].append("@Task が 2 つあります（後を採用）")
            res["task_query"] = _nfkc(raw)[1:]
            continue
        h = _parse_hours_token(t)
        if h is not None and h > 0:
            if res["hours"] is not None:
                res["warnings"].append("工数が 2 つあります（後を採用）")
            rounded = math.ceil(h * 4 - 1e-9) / 4  # 15 分単位に切上げ
            res["hours"] = rounded
            res["hours_rounded"] = abs(rounded - h) > 1e-9
            continue
        rng = _parse_date_range(t, today, holidays)
        d = None if rng else _parse_date_token(t, today, holidays)
        if rng or d:
            if res["deadline"] is not None:
                res["warnings"].append("日付が 2 つあります（後を採用）")
            if rng:
                res["start"], res["deadline"] = rng
                if rng[0] > rng[1]:
                    res["warnings"].append("開始可能日が納期より後です")
            else:
                res["deadline"] = d
            continue
        if re.fullmatch(r"\d+", t):
            res["hints"].append(f"工数なら「{t}h」と入力してください")
        title_words.append(raw)

    res["title"] = " ".join(title_words).strip()
    if res["deadline"] is not None and res["deadline"] < today:
        res["warnings"].append("納期が過去日です")
    return res


def _open_tasks(df_nodes: pd.DataFrame) -> pd.DataFrame:
    return df_nodes[(df_nodes["node_type"] == "task")
                    & (~df_nodes["status"].isin(["done", "cancel", "deleted"]))]


def quick_add_task_candidates(df_nodes: pd.DataFrame, query: str, user: str = "",
                              recent=(), limit: int = 10) -> List[tuple]:
    """
    @指定の Task 候補を [(task_idx, 階層パス), ...] で順位順に返す。
    順位: タイトル完全一致 → 前方一致 → 部分一致 → パス一致。
    同順位は最近使った Task・自分担当・パス順。完了/中止 Task は除外。
    """
    if df_nodes.empty:
        return []
    q = norm_key(query or "")
    recent = list(recent)
    scored = []
    for idx, r in _open_tasks(df_nodes).iterrows():
        title_k = norm_key(r.get("title", ""))
        path = " ＞ ".join(node_path_titles(df_nodes, idx))
        if not q:
            score = 3
        elif title_k == q:
            score = 0
        elif title_k.startswith(q):
            score = 1
        elif q in title_k:
            score = 2
        elif q in norm_key(path):
            score = 3
        else:
            continue
        rec = recent.index(idx) if idx in recent else len(recent)
        own = 0 if str(r.get("assigned_to", "")) == user else 1
        scored.append(((score, rec, own, path), idx, path))
    scored.sort(key=lambda x: x[0])
    return [(idx, path) for _, idx, path in scored[:limit]]


def resolve_task_query(df_nodes: pd.DataFrame, query: str, user: str = "",
                       recent=()) -> tuple:
    """@指定を 1 つの Task に確定する。(task_idx or None, 候補数) を返す。
    候補が 1 件、またはタイトル完全一致が 1 件なら確定する。"""
    cands = quick_add_task_candidates(df_nodes, query, user, recent, limit=1000)
    if len(cands) == 1:
        return cands[0][0], 1
    q = norm_key(query or "")
    exact = [i for i, _ in cands if norm_key(df_nodes.loc[i, "title"]) == q]
    if q and len(exact) == 1:
        return exact[0], len(cands)
    return None, len(cands)


def inbox_tickets(df_nodes: pd.DataFrame, user: str) -> pd.DataFrame:
    """ユーザーの Inbox（Task 未設定）チケットを作成日順で返す"""
    if df_nodes.empty:
        return df_nodes
    df = df_nodes[(df_nodes["parent_id"] == INBOX_PARENT)
                  & (df_nodes["assigned_to"] == user)
                  & (df_nodes["status"] != "deleted")]
    return df.sort_values("created_at")


def inbox_summary(df_nodes: pd.DataFrame, user: str, max_items: int,
                  stale_days: int, today: Optional[datetime.date] = None) -> dict:
    """Inbox の件数・最古の経過日数と、上限到達 / 滞留の判定を返す"""
    today = today or datetime.date.today()
    df = inbox_tickets(df_nodes, user)
    oldest = 0
    for v in df.get("created_at", []):
        try:
            oldest = max(oldest, (today - datetime.date.fromisoformat(str(v)[:10])).days)
        except ValueError:
            pass
    count = len(df)
    return {"count": count, "oldest_days": oldest,
            "over": count >= max_items, "stale": count > 0 and oldest > stale_days}


def _bigrams(s: str) -> set:
    k = norm_key(s)
    return {k[i:i + 2] for i in range(len(k) - 1)} or ({k} if k else set())


def suggest_task(df_nodes: pd.DataFrame, title: str, memo: str = "",
                 user: str = "", threshold: float = 0.3) -> Optional[str]:
    """
    Inbox チケットの移動先 Task を推定する。
    チケット名+メモと「Task 名 + 階層パス + 配下チケット名」の文字 2-gram 一致率が
    最も高い Task を返す（閾値未満なら None）。同点は自分担当を優先。
    """
    src = _bigrams(f"{title}{memo}")
    if not src or df_nodes.empty:
        return None
    tickets = df_nodes[df_nodes["node_type"] == "ticket"]
    kids = tickets.groupby("parent_id")["title"].apply(
        lambda s: " ".join(map(str, s))) if not tickets.empty else {}
    best, best_key = None, None
    for idx, r in _open_tasks(df_nodes).iterrows():
        text = " ".join(node_path_titles(df_nodes, idx)) + " " + str(kids.get(idx, ""))
        score = len(src & _bigrams(text)) / len(src)
        if score < threshold:
            continue
        key = (score, 1 if str(r.get("assigned_to", "")) == user else 0)
        if best_key is None or key > best_key:
            best, best_key = idx, key
    return best


# ============================================================
# E1: 見積アシスト（似た仕事の実績）
# ============================================================

def similar_ticket_hours(df_nodes: pd.DataFrame, title: str, user: str,
                         exclude_idx: str = "", threshold: float = 0.4,
                         limit: int = 3) -> Optional[dict]:
    """
    自分（user）の完了チケットから、タイトルが似ているものの実績を返す。
    類似度は文字 2-gram の Dice 係数。該当なしは None。
    戻り値: {"items": [{"idx","title","est","actual","score"}], "avg_actual", "avg_est", "suggest"}
      suggest = 実績平均を 15 分単位に切り上げた見積の提案値
    """
    src = _bigrams(title)
    if not src or df_nodes.empty:
        return None
    done = df_nodes[(df_nodes["node_type"] == "ticket") & (df_nodes["status"] == "done")
                    & (df_nodes["assigned_to"] == user)]
    hits = []
    for idx, r in done.iterrows():
        if idx == exclude_idx:
            continue
        actual = float(r.get("actual_hours", 0) or 0)
        if actual <= 0:
            continue
        other = _bigrams(str(r.get("title", "")))
        if not other:
            continue
        score = 2 * len(src & other) / (len(src) + len(other))
        if score >= threshold:
            hits.append({"idx": idx, "title": str(r.get("title", "")), "score": score,
                         "est": float(r.get("estimated_hours", 0) or 0), "actual": actual,
                         "end": str(r.get("actual_end", "") or "")})
    if not hits:
        return None
    # 似ている順、同点は新しい完了を優先
    hits.sort(key=lambda h: (h["score"], h["end"]), reverse=True)
    items = hits[:limit]
    avg_actual = sum(h["actual"] for h in items) / len(items)
    ests = [h["est"] for h in items if h["est"] > 0]
    return {
        "items": items,
        "avg_actual": round(avg_actual, 2),
        "avg_est": round(sum(ests) / len(ests), 2) if ests else None,
        "suggest": math.ceil(avg_actual * 4 - 1e-9) / 4,
    }


# ============================================================
# F1: 作業ログ（チケットのメモ欄へ時刻つきで追記）
# ============================================================

# 追記した 1 行の形式: [MM/DD HH:MM] 本文（成果のまとめ等で抽出に使う）
WORK_LOG_RE = re.compile(r"^\[(\d{2})/(\d{2}) (\d{2}):(\d{2})\] (.+)$")


def append_work_log(memo: str, text: str, now: Optional[datetime.datetime] = None) -> str:
    """メモの末尾に「[MM/DD HH:MM] 本文」を 1 行追記した文字列を返す（本文の改行は空白に）"""
    now = now or datetime.datetime.now()
    body = " ".join(str(text).split())
    if not body:
        return memo or ""
    line = f"[{now:%m/%d %H:%M}] {body}"
    memo = (memo or "").rstrip("\n")
    return f"{memo}\n{line}" if memo else line


# ============================================================
# E2: 納期リスク予報
# ============================================================

def estimate_factor(df_nodes: pd.DataFrame, user: str, min_count: int = 5,
                    lo: float = 1.0, hi: float = 2.0) -> tuple:
    """
    見積係数（自分の完了チケットの 実績÷見積 の平均）と件数を返す。
    実績 0（未記録）は除外。件数が min_count 未満なら 1.0。係数は lo〜hi に丸める。
    """
    ratios = [a["ratio"] for a in calc_estimate_accuracy(df_nodes, user) if a["actual"] > 0]
    if len(ratios) < min_count:
        return 1.0, len(ratios)
    avg = sum(ratios) / len(ratios)
    return round(min(hi, max(lo, avg)), 2), len(ratios)


def _business_days_between(d_from: datetime.date, d_to: datetime.date, holidays) -> int:
    """d_from の翌日〜d_to（両端含む側は d_to）の営業日数"""
    hol = {h.strip().upper() for h in holidays}
    n, d = 0, d_from
    while d < d_to:
        d += datetime.timedelta(days=1)
        if _DAY_ABBR[d.weekday()] not in hol:
            n += 1
    return n


def deadline_risks(df_nodes: pd.DataFrame, member: str, daily_h: float, holidays,
                   today: Optional[datetime.date] = None, factor: float = 1.0) -> List[dict]:
    """
    今のペース（1 日 daily_h 時間・残り＝見積×factor−実績）で作業すると、
    納期（今日以降に設定された実際の納期）に間に合わないチケットを返す。
    過去の納期は既存の「納期超過」アラートで扱うため含めない。
    定常（regularly）は繰り返しの仕事のため予報しない。
    戻り値: [{"idx","title","task","deadline","finish","late_days"}]（納期順）
    """
    today = today or datetime.date.today()
    if df_nodes.empty:
        return []
    sched = schedule_tickets(df_nodes, member, daily_h, holidays, today, factor)
    risks = []
    for idx, (days, _start, _dl) in sched.items():
        if idx not in df_nodes.index or str(df_nodes.loc[idx, "status"]) == "regularly":
            continue
        real = _parse_date(str(df_nodes.loc[idx, "deadline"] or ""))
        if not real or not days:
            continue
        dl = datetime.date.fromisoformat(real)
        finish = max(days)
        if dl < today or finish <= dl:
            continue
        risks.append({
            "idx": idx,
            "title": str(df_nodes.loc[idx, "title"]),
            "task": _ancestor_title(df_nodes, idx, "task"),
            "deadline": dl, "finish": finish,
            "late_days": _business_days_between(dl, finish, holidays),
        })
    risks.sort(key=lambda r: (r["deadline"], r["finish"]))
    return risks


# ============================================================
# I2: 小さなごほうび（続ける動機になる指標）
# ============================================================

def motivation_stats(df_nodes: pd.DataFrame, df_daily: pd.DataFrame, user: str,
                     holidays, today: Optional[datetime.date] = None) -> dict:
    """
    Today に出す 3 つの指標を返す。
      streak     : 日次スケジュールを記録した日の連続日数（記録の無い休日は飛ばす。今日が未記録なら昨日から）
      week_done  : 今週（月曜始まり）に完了したチケット数
      bullseye   : 今月完了したうち実績が見積の ±20% に収まった件数 / 見積のある完了件数
    """
    today = today or datetime.date.today()
    hol = {h.strip().upper() for h in holidays}

    # 記録のある日（スロットに 1 つでも入っている日）
    recorded: set = set()
    if not df_daily.empty and "Owner" in df_daily.columns:
        mine = df_daily[df_daily["Owner"] == user]
        cols = [c for c in DAILY_TIME_COLS if c in mine.columns]
        for idx, row in mine.iterrows():
            if any(str(v or "") not in ("", "nan", "None") for v in row[cols]):
                recorded.add(str(idx)[:10])
    streak, d = 0, today
    if d.isoformat() not in recorded:
        d -= datetime.timedelta(days=1)   # 今日はまだ記録前でも途切れない
    for _ in range(3660):
        if d.isoformat() not in recorded:
            if _DAY_ABBR[d.weekday()] in hol:   # 記録の無い休日は飛ばす（休日出勤の記録は数える）
                d -= datetime.timedelta(days=1)
                continue
            break
        streak += 1
        d -= datetime.timedelta(days=1)

    week_start = (today - datetime.timedelta(days=today.weekday())).isoformat()
    month_start = today.replace(day=1).isoformat()
    week_done, hit, total = 0, 0, 0
    if not df_nodes.empty:
        done = df_nodes[(df_nodes["node_type"] == "ticket") & (df_nodes["status"] == "done")
                        & (df_nodes["assigned_to"] == user)]
        for _, r in done.iterrows():
            end = _date_str(r.get("actual_end"))
            if not end:
                continue
            if week_start <= end <= today.isoformat():
                week_done += 1
            if month_start <= end <= today.isoformat():
                est = float(r.get("estimated_hours", 0) or 0)
                act = float(r.get("actual_hours", 0) or 0)
                if est > 0 and act > 0:
                    total += 1
                    hit += abs(act - est) <= est * 0.2
    return {"streak": streak, "week_done": week_done, "bullseye": hit, "bullseye_total": total}


# ============================================================
# G4: 完了予測（バーンダウン）
# ============================================================

def _scope_tickets(df_nodes: pd.DataFrame, scope: Optional[set], users: Optional[set]) -> pd.DataFrame:
    """scope（ノード IDX の集合。None=全体）配下で users 担当の有効チケット"""
    t = df_nodes[(df_nodes["node_type"] == "ticket")
                 & (~df_nodes["status"].isin(["cancel", "deleted"]))]
    if users is not None:
        t = t[t["assigned_to"].isin(users)]
    if scope:
        def inside(i) -> bool:
            cur, seen = str(i), set()
            while cur and cur not in seen:
                if cur in scope:
                    return True
                if cur not in df_nodes.index:
                    return False
                seen.add(cur)
                cur = str(df_nodes.loc[cur, "parent_id"] or "")
            return False
        t = t[t.index.map(inside)]
    return t


def _daily_hours_by_date(df_daily: pd.DataFrame, ticket_ids: set,
                         since: str = "") -> dict:
    """{日付: {ticket: 時間}}（日次スケジュールの 15 分スロットを集計）"""
    out: dict = {}
    if df_daily.empty or not ticket_ids:
        return out
    cols = [c for c in DAILY_TIME_COLS if c in df_daily.columns]
    for idx, row in df_daily.iterrows():
        d = str(idx)[:10]
        if since and d < since:
            continue
        for v in row[cols]:
            if v in ticket_ids:
                day = out.setdefault(d, {})
                day[v] = day.get(v, 0.0) + 0.25
    return out


def _add_business_days(start: datetime.date, days: int, holidays) -> datetime.date:
    """start の翌日から数えて days 営業日目の日付（days<=0 なら start）"""
    hol = {h.strip().upper() for h in holidays}
    d, n = start, 0
    while n < days:
        d += datetime.timedelta(days=1)
        if _DAY_ABBR[d.weekday()] not in hol:
            n += 1
    return d


def completion_forecast(df_nodes: pd.DataFrame, df_daily: pd.DataFrame,
                        scope: Optional[set], users: Optional[set], holidays,
                        today: Optional[datetime.date] = None,
                        pace_days: int = 14, history_days: int = 56) -> dict:
    """
    残り作業の推移と完了予想日を返す。
      remaining_now : 未完了チケットの 見積−実績 の合計(h)
      pace          : 直近 pace_days 日の実績(h) ÷ その期間の営業日数（h/営業日）
      forecast      : 今日から ceil(残り÷ペース) 営業日後（ペース 0 なら None、残り 0 なら今日）
      deadline      : scope ノード自身の納期（無ければ配下チケットの最も遅い納期）
      history       : [(日付, その日の終わり時点の残り h)]（最大 history_days 日前から今日まで。
                      残り 0 が続く先頭区間は省く）
    """
    today = today or datetime.date.today()
    tickets = _scope_tickets(df_nodes, scope, users)
    ids = set(tickets.index)
    est = {i: float(tickets.loc[i, "estimated_hours"] or 0) for i in ids}
    act_total = {i: float(tickets.loc[i, "actual_hours"] or 0) for i in ids}
    # 完了日（完了日の記録が無い done は「最初から完了」扱いにして現在の残りと揃える）
    done_on = {i: (_date_str(tickets.loc[i, "actual_end"]) or "0000-00-00")
               if str(tickets.loc[i, "status"]) == "done" else "" for i in ids}
    open_ids = [i for i in ids if str(tickets.loc[i, "status"]) != "done"]
    remaining_now = round(sum(max(0.0, est[i] - act_total[i]) for i in open_ids), 2)

    start = today - datetime.timedelta(days=history_days)
    by_date = _daily_hours_by_date(df_daily, ids, start.isoformat())
    hol = {h.strip().upper() for h in holidays}
    pace_from = today - datetime.timedelta(days=pace_days - 1)
    worked = sum(h for d, day in by_date.items() if d >= pace_from.isoformat()
                 for h in day.values())
    bdays = sum(1 for k in range(pace_days)
                if _DAY_ABBR[(pace_from + datetime.timedelta(days=k)).weekday()] not in hol)
    pace = round(worked / bdays, 2) if bdays else 0.0

    if remaining_now <= 0:
        forecast = today
    elif pace > 0:
        forecast = _add_business_days(today, math.ceil(remaining_now / pace - 1e-9), holidays)
    else:
        forecast = None

    # 推移: 今日の実績合計から日ごとに遡って「その日の終わりの残り」を復元する
    created = {i: _date_str(tickets.loc[i, "created_at"]) for i in ids}
    act_until = dict(act_total)
    history = []
    d = today
    while d >= start:
        ds = d.isoformat()
        rem = sum(max(0.0, est[i] - act_until[i]) for i in ids
                  if (not created[i] or created[i] <= ds) and not (done_on[i] and done_on[i] <= ds))
        history.append((d, round(rem, 2)))
        for i, h in by_date.get(ds, {}).items():
            act_until[i] -= h          # 前日の終わり時点へ戻す
        d -= datetime.timedelta(days=1)
    history.reverse()
    # 先頭の「まだチケットが無い（残り 0）」区間は省き、直前の 1 点だけ残す
    first = next((k for k, (_d, v) in enumerate(history) if v > 0), len(history))
    history = history[max(0, first - 1):] or history[-1:]

    deadline = None
    if scope and len(scope) == 1:
        (only,) = tuple(scope)
        if only in df_nodes.index:
            deadline = _parse_date(str(df_nodes.loc[only, "deadline"] or ""))
    if not deadline:
        dls = [_parse_date(str(v or "")) for v in tickets["deadline"]] if not tickets.empty else []
        dls = [x for x in dls if x]
        deadline = max(dls) if dls else None
    return {
        "remaining_now": remaining_now, "pace": pace, "forecast": forecast,
        "deadline": datetime.date.fromisoformat(deadline) if deadline else None,
        "history": history, "tickets": len(ids),
    }


def completion_text(fc: dict) -> str:
    """G4: 完了予測の 1 行表示（例: 10/15 完了見込み（納期 10/20）・残り 12h・ペース 3h/日）"""
    fmt = lambda d: f"{d.month}/{d.day}"
    if fc["remaining_now"] <= 0:
        head = "残り作業なし"
    elif fc["forecast"] is None:
        head = "完了日は予測できません（直近 2 週間の実績なし）"
    else:
        head = f"{fmt(fc['forecast'])} 完了見込み"
        if fc["deadline"]:
            late = fc["forecast"] > fc["deadline"]
            head += f"（納期 {fmt(fc['deadline'])}{' に遅れ ⚠' if late else ''}）"
    return f"{head}・残り {fc['remaining_now']:g}h・ペース {fc['pace']:g}h/日"


# ============================================================
# G2: 成果のまとめ（評価面談用）
# ============================================================

def review_period(name: str, today: Optional[datetime.date] = None) -> tuple:
    """
    成果のまとめの期間プリセット（年度は 4 月始まり）を (開始, 終了) の ISO 文字列で返す。
      上期: 今日を含む／直近の 4/1〜9/30、下期: 今日を含む／直近の 10/1〜3/31、年度: 今年度
    """
    today = today or datetime.date.today()
    fy = today.year if today.month >= 4 else today.year - 1
    if name == "上期":
        return datetime.date(fy, 4, 1).isoformat(), datetime.date(fy, 9, 30).isoformat()
    if name == "下期":
        y = fy if today.month >= 10 or today.month <= 3 else fy - 1
        return datetime.date(y, 10, 1).isoformat(), datetime.date(y + 1, 3, 31).isoformat()
    if name == "年度":
        return datetime.date(fy, 4, 1).isoformat(), datetime.date(fy + 1, 3, 31).isoformat()
    return "", ""


def _work_logs_in_period(memo: str, d_from: str, d_to: str) -> List[str]:
    """メモ中の作業ログ（[MM/DD HH:MM] 本文）のうち期間内のものを返す（年は期間から推定）"""
    out = []
    years = range(int(d_from[:4]), int(d_to[:4]) + 1)
    for line in str(memo or "").splitlines():
        m = WORK_LOG_RE.match(line.strip())
        if not m:
            continue
        for y in years:
            try:
                d = datetime.date(y, int(m.group(1)), int(m.group(2))).isoformat()
            except ValueError:
                continue
            if d_from <= d <= d_to:
                out.append(line.strip())
                break
    return out


def achievement_summary(df_nodes: pd.DataFrame, df_daily: pd.DataFrame, user: str,
                        d_from: str, d_to: str) -> dict:
    """
    user の期間 d_from〜d_to の成果を集計する。
    投入時間は日次スケジュール、完了は実績完了日（actual_end）で判定する。
    """
    hours_by_ticket: dict = {}
    days_worked: set = set()
    month_hours: dict = {}
    if not df_daily.empty and "Owner" in df_daily.columns:
        mine = df_daily[df_daily["Owner"] == user]
        cols = [c for c in DAILY_TIME_COLS if c in mine.columns]
        for idx, row in mine.iterrows():
            d = str(idx)[:10]
            if not (d_from <= d <= d_to):
                continue
            for v in row[cols]:
                if v and v in df_nodes.index:
                    hours_by_ticket[v] = hours_by_ticket.get(v, 0.0) + 0.25
                    days_worked.add(d)
                    month_hours[d[:7]] = month_hours.get(d[:7], 0.0) + 0.25
    total = sum(hours_by_ticket.values())

    def p1_title(i: str) -> str:
        if str(df_nodes.loc[i, "parent_id"]) == INBOX_PARENT:
            return "Inbox（Task 未設定）"
        p1 = ancestor_of_type(df_nodes, i, "project1")
        return str(df_nodes.loc[p1, "title"]) if p1 else "（プロジェクト外）"

    by_p1: dict = {}
    for i, h in hours_by_ticket.items():
        by_p1[p1_title(i)] = by_p1.get(p1_title(i), 0.0) + h

    top = sorted(hours_by_ticket.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_items = [{
        "idx": i, "path": " ＞ ".join(node_path_titles(df_nodes, i)), "hours": h,
        "status": str(df_nodes.loc[i, "status"]),
        "logs": _work_logs_in_period(str(df_nodes.loc[i, "memo"] or ""), d_from, d_to),
    } for i, h in top]

    done = df_nodes[(df_nodes["node_type"] == "ticket") & (df_nodes["status"] == "done")
                    & (df_nodes["assigned_to"] == user)] if not df_nodes.empty else df_nodes
    completed = []
    for i, r in done.iterrows():
        end = _date_str(r.get("actual_end"))
        if end and d_from <= end <= d_to:
            completed.append({"idx": i, "title": str(r.get("title", "")), "p1": p1_title(i),
                              "end": end, "est": float(r.get("estimated_hours", 0) or 0),
                              "actual": float(r.get("actual_hours", 0) or 0)})
    completed.sort(key=lambda c: (c["p1"], c["end"]))

    acc_by_month: dict = {}
    for c in completed:
        if c["est"] > 0 and c["actual"] > 0:
            acc_by_month.setdefault(c["end"][:7], []).append(c["actual"] / c["est"])
    ratios = [x for v in acc_by_month.values() for x in v]
    return {
        "user": user, "from": d_from, "to": d_to,
        "total_hours": round(total, 2), "days_worked": len(days_worked),
        "by_p1": sorted(((k, round(v, 2)) for k, v in by_p1.items()), key=lambda kv: kv[1], reverse=True),
        "top": top_items, "completed": completed,
        "accuracy": round(sum(ratios) / len(ratios), 2) if ratios else None,
        "accuracy_by_month": {m: (len(v), round(sum(v) / len(v), 2))
                              for m, v in sorted(acc_by_month.items())},
        "month_hours": {m: round(h, 2) for m, h in sorted(month_hours.items())},
    }


def build_achievement_markdown(data: dict, display_name: str = "") -> str:
    """成果のまとめを Markdown にする（評価面談の準備・AI への入力用）"""
    name = display_name or data["user"]
    lines = [f"# 成果のまとめ（{name}）", "",
             f"- 期間: {data['from']} 〜 {data['to']}",
             f"- 投入時間: {data['total_hours']:g}h（記録のある日 {data['days_worked']} 日）",
             f"- 完了したチケット: {len(data['completed'])} 件"]
    if data["accuracy"] is not None:
        lines.append(f"- 見積精度（実績÷見積の平均）: {data['accuracy']:g}"
                     "（1 に近いほど見積どおり、1 超は見積より時間がかかった）")
    lines += ["", "## プロジェクト別の投入時間", ""]
    if data["by_p1"]:
        lines += ["| プロジェクト | 時間 | 割合 |", "|---|---:|---:|"]
        for p, h in data["by_p1"]:
            pct = round(h / data["total_hours"] * 100) if data["total_hours"] else 0
            lines.append(f"| {_md_escape(p)} | {h:g}h | {pct}% |")
    else:
        lines.append("（期間内の記録なし）")
    if data["month_hours"]:
        lines += ["", "## 月別の投入時間", "", "| 月 | 時間 |", "|---|---:|"]
        lines += [f"| {m} | {h:g}h |" for m, h in data["month_hours"].items()]
    lines += ["", "## 時間をかけた仕事 上位 5 件", ""]
    for n, t in enumerate(data["top"], 1):
        lines.append(f"{n}. {_md_escape(t['path'])} — {t['hours']:g}h（{t['status']}）")
        for log in t["logs"][-5:]:
            lines.append(f"   - {_md_escape(log)}")
    if not data["top"]:
        lines.append("（期間内の記録なし）")
    lines += ["", "## 完了したチケット", ""]
    cur = None
    for c in data["completed"]:
        if c["p1"] != cur:
            cur = c["p1"]
            lines += ["", f"### {_md_escape(cur)}"]
        est = f"見積 {c['est']:g}h → " if c["est"] else ""
        lines.append(f"- {c['end']} {_md_escape(c['title'])}（{est}実績 {c['actual']:g}h）")
    if not data["completed"]:
        lines.append("（期間内に完了したチケットなし）")
    if data["accuracy_by_month"]:
        lines += ["", "## 見積精度の推移（月別）", "", "| 月 | 完了件数 | 実績÷見積 |", "|---|---:|---:|"]
        for m, (n, r) in data["accuracy_by_month"].items():
            lines.append(f"| {m} | {n} | {r:g} |")
    return "\n".join(lines) + "\n"
