"""Portfolio tools — get_positions, get_cash, get_performance, get_weekly_report."""

import json
from datetime import date

from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP):

    @mcp.tool()
    def get_positions(profile: str = "default") -> str:
        """Get all open positions with current prices and unrealized P&L.

        Args:
            profile: Strategy profile name (default/conservative/aggressive)
        """
        from portfolio import get_open_positions, set_profile
        from data import fetch_stock_data
        from nikkei225 import NIKKEI_225

        set_profile(profile)
        positions = get_open_positions()

        result = []
        for pos in positions:
            ticker = pos["ticker"]
            entry = pos["entry_price"]
            try:
                df = fetch_stock_data(ticker, period="5d")
                current = float(df["Close"].iloc[-1])
            except Exception:
                current = entry

            pnl = (current - entry) * pos["shares"]
            pnl_pct = (current / entry - 1) * 100
            days = (date.today() - date.fromisoformat(pos["entry_date"])).days

            result.append({
                "ticker": ticker,
                "name": NIKKEI_225.get(ticker, ticker),
                "entry_price": entry,
                "current_price": round(current, 1),
                "shares": pos["shares"],
                "entry_date": pos["entry_date"],
                "days_held": days,
                "stop_price": pos.get("stop_price"),
                "high_price": pos.get("high_price"),
                "unrealized_pnl": round(pnl, 1),
                "unrealized_pnl_pct": round(pnl_pct, 1),
                "partial_exit_done": pos.get("partial_exit_done", False),
            })

        return json.dumps({"profile": profile, "positions": result}, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_cash(profile: str = "default") -> str:
        """Get current cash balance for a profile.

        Args:
            profile: Strategy profile name (default/conservative/aggressive)
        """
        from portfolio import get_cash_balance, get_open_positions, set_profile
        from main import load_config

        config = load_config()
        balance = config["account"]["balance"]
        set_profile(profile)

        cash = get_cash_balance(balance)
        positions = get_open_positions()
        stock_value = sum(p["entry_price"] * p["shares"] for p in positions)
        total_assets = cash + stock_value

        return json.dumps({
            "profile": profile,
            "initial_balance": balance,
            "cash": round(cash, 1),
            "stock_value": round(stock_value, 1),
            "total_assets": round(total_assets, 1),
            "position_count": len(positions),
        }, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_performance(period: str = "all") -> str:
        """Get trading performance summary (win rate, P&L, profit factor).

        Args:
            period: Time period — "1w", "1m", "3m", "6m", "1y", or "all"
        """
        from portfolio import get_performance_summary, set_profile, _load_trades
        from datetime import timedelta

        set_profile("default")
        trades = _load_trades()
        closed = [t for t in trades if t["status"] == "closed"]

        if period != "all":
            period_days = {"1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365}
            days = period_days.get(period, 9999)
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            closed = [t for t in closed if t.get("exit_date", "") >= cutoff]

        if not closed:
            return json.dumps({
                "period": period, "trade_count": 0,
                "total_pnl": 0, "win_rate": 0, "max_drawdown": 0,
            }, ensure_ascii=False)

        pnls = [t["pnl"] for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

        # Max drawdown
        peak = 0
        max_dd = 0
        running = 0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        avg_win = round(sum(wins) / len(wins), 1) if wins else 0
        avg_loss = round(sum(losses) / len(losses), 1) if losses else 0

        return json.dumps({
            "period": period,
            "trade_count": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "total_pnl": round(sum(pnls), 1),
            "gross_profit": round(gross_profit, 1),
            "gross_loss": round(gross_loss, 1),
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "max_drawdown": round(max_dd, 1),
        }, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_weekly_report() -> str:
        """Get weekly trading report with this week's trades, P&L, and cumulative stats."""
        from portfolio import get_weekly_report as _get_weekly_report, set_profile
        from main import load_config

        set_profile("default")
        config = load_config()
        report = _get_weekly_report()
        report["initial_balance"] = config["account"]["balance"]
        return json.dumps(report, ensure_ascii=False, default=str)
