"""Market data tools — scan_market, get_stock_data, get_market_regime, get_financial_data."""

import json
import math

import numpy as np
from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP):

    @mcp.tool()
    def scan_market(profile: str = "default") -> str:
        """Scan all Nikkei 225 stocks and return BUY/SELL candidates with market regime.

        This runs a full scan (225 stocks, may take 3-5 minutes).
        Returns top 10 BUY candidates sorted by composite score, SELL signals for open positions,
        current market regime, and portfolio snapshot.

        Args:
            profile: Strategy profile name (default/conservative/aggressive)
        """
        from main import scan_only
        result = scan_only(profile)
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_stock_data(ticker: str) -> str:
        """Get price summary and recent data for a specific stock.

        Returns latest price, 52-week high/low, moving averages, volume stats,
        and the last 5 trading days of OHLCV data.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from data import fetch_stock_data
        from strategy import calculate_sma, calculate_rsi
        from nikkei225 import NIKKEI_225

        df = fetch_stock_data(ticker)
        close = df["Close"]
        latest = float(close.iloc[-1])

        def _safe(val):
            v = float(val)
            return None if (math.isnan(v) or math.isinf(v)) else round(v, 1)

        sma25 = float(calculate_sma(close, 25).iloc[-1])
        sma100 = float(calculate_sma(close, 100).iloc[-1])
        sma200 = float(calculate_sma(close, 200).iloc[-1])
        rsi = float(calculate_rsi(close).iloc[-1])

        high_52w = float(df["High"].rolling(min(252, len(df))).max().iloc[-1])
        low_52w = float(df["Low"].rolling(min(252, len(df))).min().iloc[-1])

        avg_vol = float(df["Volume"].rolling(20).mean().iloc[-1])

        last_5 = []
        for i in range(-5, 0):
            row = df.iloc[i]
            last_5.append({
                "date": str(row.name.date()) if hasattr(row.name, "date") else str(row.name),
                "open": round(float(row["Open"]), 1),
                "high": round(float(row["High"]), 1),
                "low": round(float(row["Low"]), 1),
                "close": round(float(row["Close"]), 1),
                "volume": int(row["Volume"]),
            })

        result = {
            "ticker": ticker,
            "name": NIKKEI_225.get(ticker, ticker),
            "latest_price": round(latest, 1),
            "sma25": _safe(sma25),
            "sma100": _safe(sma100),
            "sma200": _safe(sma200),
            "rsi14": _safe(rsi),
            "high_52w": _safe(high_52w),
            "low_52w": _safe(low_52w),
            "avg_volume_20d": int(avg_vol) if not math.isnan(avg_vol) else 0,
            "last_5_days": last_5,
        }
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_market_regime() -> str:
        """Get current market regime based on Nikkei 225 SMA50/SMA200.

        Returns regime (bull/bear/neutral), SMA50, SMA200, and current price.
        """
        from data import fetch_stock_data
        from strategy import detect_market_regime

        nikkei_df = fetch_stock_data("^N225")
        regime = detect_market_regime(nikkei_df)
        return json.dumps(regime, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_financial_data(ticker: str) -> str:
        """Get fundamental financial data for a stock (PER, PBR, ROE, dividends, etc.).

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from data import fetch_financial_data
        from nikkei225 import NIKKEI_225

        result = fetch_financial_data(ticker)
        result["ticker"] = ticker
        result["name"] = NIKKEI_225.get(ticker, ticker)

        if "raw_info" in result:
            del result["raw_info"]
        if "raw_fin" in result:
            del result["raw_fin"]

        return json.dumps(result, ensure_ascii=False, default=str)
