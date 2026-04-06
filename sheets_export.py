"""
Google Sheets export for stock-signal analysis data.

Exports daily analysis, IC memos, and portfolio data to Google Sheets
using gspread with Service Account authentication.
"""

import json
import logging
import os
from datetime import date

logger = logging.getLogger("signal")


def _get_client():
    """Get authenticated gspread client."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
    if not os.path.exists(creds_file):
        raise FileNotFoundError(f"Service account file not found: {creds_file}")

    creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
    return gspread.authorize(creds)


def _get_spreadsheet(config: dict):
    """Get or create the target spreadsheet."""
    sheets_cfg = config.get("sheets", {})
    spreadsheet_id = sheets_cfg.get("spreadsheet_id", "") or os.environ.get("SHEETS_SPREADSHEET_ID", "")

    if not spreadsheet_id:
        raise ValueError("spreadsheet_id not configured (config.yaml or SHEETS_SPREADSHEET_ID env var)")

    client = _get_client()
    return client.open_by_key(spreadsheet_id)


def _ensure_worksheet(spreadsheet, title: str, headers: list):
    """Get or create a worksheet with headers."""
    try:
        ws = spreadsheet.worksheet(title)
    except Exception:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        ws.update("A1", [headers])
        ws.format("A1:{}1".format(chr(64 + len(headers))), {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
        })
    return ws


def export_daily_analysis(buy_signals: list, config: dict):
    """
    Export daily analysis data to Sheet 1.

    Columns: Date | Ticker | Name | Price | RSI | Composite | Agent Signal |
             Score | Confidence | LLM | IC Memo判定 | Target
    """
    from nikkei225 import NIKKEI_225

    today = date.today().isoformat()
    headers = [
        "Date", "Ticker", "Name", "Price", "RSI", "Composite",
        "Agent Signal", "Score", "Confidence", "LLM", "IC Memo判定", "Target",
    ]

    spreadsheet = _get_spreadsheet(config)
    ws = _ensure_worksheet(spreadsheet, "日次分析", headers)

    rows = []
    for sig in buy_signals:
        ticker = sig.get("ticker", "")
        name = NIKKEI_225.get(ticker, ticker)
        agent = sig.get("agent_analysis", {})
        review = sig.get("llm_review", {})
        memo = sig.get("ic_memo", {})
        exec_summary = memo.get("executive_summary", {})

        llm_status = ""
        if not review.get("skipped", True):
            llm_status = "承認" if review.get("approved") else "却下"

        ic_status = exec_summary.get("recommendation", "")
        target = exec_summary.get("target_price", "")

        rows.append([
            today,
            ticker,
            name,
            sig.get("price", 0),
            round(sig.get("rsi", 0), 1),
            round(sig.get("composite_score", 0), 3),
            agent.get("signal", ""),
            round(agent.get("total_score", 0), 2) if agent else "",
            f'{agent.get("confidence", 0)}%' if agent else "",
            llm_status,
            ic_status,
            target,
        ])

    if rows:
        # Find next empty row
        existing = ws.get_all_values()
        next_row = len(existing) + 1

        # Remove existing rows for same date (overwrite)
        date_rows = []
        for i, row in enumerate(existing[1:], start=2):  # Skip header
            if row and row[0] == today:
                date_rows.append(i)

        if date_rows:
            # Clear old data for today
            for row_idx in reversed(date_rows):
                ws.delete_rows(row_idx)
            next_row = date_rows[0] if date_rows else len(ws.get_all_values()) + 1

        ws.update(f"A{next_row}", rows)
        logger.info(f"Sheets: Exported {len(rows)} rows to 日次分析")


def export_ic_memos(buy_signals: list, config: dict):
    """
    Export IC memos to Sheet 2.

    Columns: Date | Ticker | Name | Recommendation | Conviction |
             Target Price | Upside% | Summary | Full JSON
    """
    from nikkei225 import NIKKEI_225

    today = date.today().isoformat()
    headers = [
        "Date", "Ticker", "Name", "Recommendation", "Conviction",
        "Target Price", "Upside%", "Summary", "Full JSON",
    ]

    spreadsheet = _get_spreadsheet(config)
    ws = _ensure_worksheet(spreadsheet, "IC Memo", headers)

    rows = []
    for sig in buy_signals:
        memo = sig.get("ic_memo", {})
        if not memo or memo.get("skipped"):
            continue

        ticker = sig.get("ticker", "")
        name = NIKKEI_225.get(ticker, ticker)
        es = memo.get("executive_summary", {})

        # Truncate full JSON to avoid Sheets cell limit
        full_json = json.dumps(memo, ensure_ascii=False)
        if len(full_json) > 45000:
            full_json = full_json[:45000] + "..."

        rows.append([
            today,
            ticker,
            name,
            es.get("recommendation", ""),
            es.get("conviction", 0),
            es.get("target_price", 0),
            round(es.get("upside_pct", 0), 1),
            memo.get("valuation", {}).get("summary", ""),
            full_json,
        ])

    if rows:
        existing = ws.get_all_values()
        # Remove existing for today
        date_rows = [i for i, row in enumerate(existing[1:], start=2) if row and row[0] == today]
        for row_idx in reversed(date_rows):
            ws.delete_rows(row_idx)

        next_row = len(ws.get_all_values()) + 1
        ws.update(f"A{next_row}", rows)
        logger.info(f"Sheets: Exported {len(rows)} IC memos")


def export_portfolio(open_positions: list, config: dict):
    """
    Export portfolio snapshot to Sheet 3.

    Columns: Date | Ticker | Name | Entry | Current | Shares |
             Value | P&L | P&L% | Days | Status
    """
    from nikkei225 import NIKKEI_225

    today = date.today().isoformat()
    headers = [
        "Date", "Ticker", "Name", "Entry", "Current", "Shares",
        "Value", "P&L", "P&L%", "Days", "Status",
    ]

    spreadsheet = _get_spreadsheet(config)
    ws = _ensure_worksheet(spreadsheet, "ポートフォリオ", headers)

    rows = []
    for pos in open_positions:
        ticker = pos.get("ticker", "")
        name = NIKKEI_225.get(ticker, ticker)
        entry = pos.get("entry_price", 0)
        current = pos.get("current_price", entry)
        shares = pos.get("shares", 0)
        value = current * shares
        pnl = (current - entry) * shares
        pnl_pct = ((current / entry) - 1) * 100 if entry > 0 else 0
        days = 0
        if pos.get("entry_date"):
            try:
                d1 = date.fromisoformat(pos["entry_date"])
                days = (date.today() - d1).days
            except (ValueError, TypeError):
                pass

        rows.append([
            today,
            ticker,
            name,
            entry,
            current,
            shares,
            round(value),
            round(pnl),
            round(pnl_pct, 1),
            days,
            pos.get("status", "open"),
        ])

    if rows:
        existing = ws.get_all_values()
        date_rows = [i for i, row in enumerate(existing[1:], start=2) if row and row[0] == today]
        for row_idx in reversed(date_rows):
            ws.delete_rows(row_idx)

        next_row = len(ws.get_all_values()) + 1
        ws.update(f"A{next_row}", rows)
        logger.info(f"Sheets: Exported {len(rows)} portfolio positions")


def export_all(buy_signals: list, open_positions: list, config: dict):
    """
    Export all data to Google Sheets.
    Wraps all exports in try/except so failures don't affect main flow.
    """
    sheets_cfg = config.get("sheets", {})
    if not sheets_cfg.get("enabled", False):
        logger.info("Google Sheets export disabled")
        return

    try:
        export_daily_analysis(buy_signals, config)
    except Exception as e:
        logger.warning(f"Sheets export (daily analysis) failed: {e}")

    try:
        export_ic_memos(buy_signals, config)
    except Exception as e:
        logger.warning(f"Sheets export (IC memos) failed: {e}")

    try:
        export_portfolio(open_positions, config)
    except Exception as e:
        logger.warning(f"Sheets export (portfolio) failed: {e}")
