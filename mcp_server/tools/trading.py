"""Trading tools — execute_buy, execute_sell, update_stops."""

import json

from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP):

    @mcp.tool()
    def execute_buy(ticker: str, price: float, shares: int, confirm: bool = False) -> str:
        """Record a buy entry for a stock.

        When confirm=False (default), returns a preview of the trade without executing.
        When confirm=True, actually records the entry in trades.json.

        IMPORTANT: Always call with confirm=False first to preview, then confirm with the user
        before calling with confirm=True.

        Args:
            ticker: Stock ticker (e.g. "7203.T")
            price: Entry price
            shares: Number of shares to buy
            confirm: Set to True to actually execute the trade
        """
        from nikkei225 import NIKKEI_225
        from main import load_config
        from risk import calculate_stop_loss

        config = load_config()
        strat = config.get("strategy", {})
        stop_pct = strat.get("stop_loss_pct", 0.08)
        stop = calculate_stop_loss(price, stop_pct)
        cost = price * shares

        preview = {
            "action": "BUY",
            "ticker": ticker,
            "name": NIKKEI_225.get(ticker, ticker),
            "price": price,
            "shares": shares,
            "cost": round(cost, 1),
            "stop_loss": stop,
            "risk_amount": round((price - stop) * shares, 1),
            "confirmed": confirm,
        }

        if not confirm:
            preview["status"] = "PREVIEW — call again with confirm=True to execute"
            return json.dumps(preview, ensure_ascii=False, default=str)

        from portfolio import record_entry, set_profile
        set_profile("default")
        trade = record_entry(ticker, price, shares, stop_pct=stop_pct)
        preview["status"] = "EXECUTED"
        preview["trade"] = trade
        return json.dumps(preview, ensure_ascii=False, default=str)

    @mcp.tool()
    def execute_sell(ticker: str, price: float, confirm: bool = False) -> str:
        """Record a sell exit for a stock position.

        When confirm=False (default), returns a preview with P&L calculation.
        When confirm=True, actually records the exit in trades.json.

        IMPORTANT: Always call with confirm=False first to preview, then confirm with the user
        before calling with confirm=True.

        Args:
            ticker: Stock ticker (e.g. "7203.T")
            price: Exit price
            confirm: Set to True to actually execute the trade
        """
        from portfolio import get_open_positions, set_profile
        from nikkei225 import NIKKEI_225

        set_profile("default")
        positions = get_open_positions()
        pos = next((p for p in positions if p["ticker"] == ticker), None)

        if pos is None:
            return json.dumps({
                "error": f"No open position found for {ticker}",
                "ticker": ticker,
            }, ensure_ascii=False)

        entry = pos["entry_price"]
        shares = pos["shares"]
        pnl = round((price - entry) * shares, 1)
        pnl_pct = round((price / entry - 1) * 100, 1)

        preview = {
            "action": "SELL",
            "ticker": ticker,
            "name": NIKKEI_225.get(ticker, ticker),
            "entry_price": entry,
            "exit_price": price,
            "shares": shares,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "confirmed": confirm,
        }

        if not confirm:
            preview["status"] = "PREVIEW — call again with confirm=True to execute"
            return json.dumps(preview, ensure_ascii=False, default=str)

        from portfolio import record_exit
        trade = record_exit(ticker, price)
        preview["status"] = "EXECUTED"
        preview["trade"] = trade
        return json.dumps(preview, ensure_ascii=False, default=str)

    @mcp.tool()
    def update_stops() -> str:
        """Update trailing stops for all open positions based on current prices.

        Fetches latest prices and updates stop prices for positions where the
        current price exceeds the previous high watermark.
        """
        from portfolio import get_open_positions, update_trailing_stop, set_profile
        from data import fetch_stock_data
        from nikkei225 import NIKKEI_225
        from main import load_config

        config = load_config()
        stop_pct = config.get("strategy", {}).get("stop_loss_pct", 0.08)

        set_profile("default")
        positions = get_open_positions()

        results = []
        for pos in positions:
            ticker = pos["ticker"]
            try:
                df = fetch_stock_data(ticker, period="5d")
                current = float(df["Close"].iloc[-1])
                updated = update_trailing_stop(ticker, current, trail_pct=stop_pct)

                result = {
                    "ticker": ticker,
                    "name": NIKKEI_225.get(ticker, ticker),
                    "current_price": round(current, 1),
                    "stop_price": updated.get("stop_price") if updated else pos.get("stop_price"),
                    "high_price": updated.get("high_price") if updated else pos.get("high_price"),
                    "updated": bool(updated and current > pos.get("high_price", 0)),
                }
                results.append(result)
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "name": NIKKEI_225.get(ticker, ticker),
                    "error": str(e),
                })

        return json.dumps({"updates": results}, ensure_ascii=False, default=str)
