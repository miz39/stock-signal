"""Analysis tools — get_signal, get_technical_summary."""

import json
import math

import numpy as np

from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP):

    @mcp.tool()
    def get_signal(ticker: str) -> str:
        """Generate BUY/SELL/HOLD signal for a specific stock with all indicator values.

        Uses the full strategy logic: SMA golden cross + RSI filter + ADX gate + volume check.
        Also returns composite score.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from data import fetch_stock_data
        from main import load_config
        from strategy import generate_signal, compute_composite_score, fetch_tv_recommendation
        from nikkei225 import NIKKEI_225
        from risk import calculate_stop_loss, calculate_position_size
        from portfolio import get_open_positions, get_cash_balance

        config = load_config()
        df = fetch_stock_data(ticker)
        sig = generate_signal(df, config)
        sig["ticker"] = ticker
        sig["name"] = NIKKEI_225.get(ticker, ticker)

        strat = config.get("strategy", {})
        tv_score = None
        if strat.get("tv_recommendation_enabled", False):
            tv_score = fetch_tv_recommendation(ticker)
            sig["tv_score"] = tv_score

        sig["composite_score"] = compute_composite_score(
            sig, df,
            strat.get("score_weights"),
            slope_days=strat.get("slope_days", 5),
            slope_blend=strat.get("slope_blend", 0.3),
            tv_score=tv_score,
        )

        if sig["signal"] == "BUY":
            account = config["account"]
            stop_pct = strat.get("stop_loss_pct", 0.08)
            stop = calculate_stop_loss(sig["price"], stop_pct)
            balance = account["balance"]
            available_cash = max(get_cash_balance(balance), 0)
            open_positions = get_open_positions()
            stock_value = sum(p["entry_price"] * p["shares"] for p in open_positions)
            total_assets = available_cash + stock_value

            shares = calculate_position_size(
                total_assets, account["risk_per_trade"],
                sig["price"], stop, account["unit"],
                account.get("max_allocation", 0.15),
            )
            max_affordable = math.floor(available_cash / sig["price"]) if sig["price"] > 0 else 0
            shares = min(shares, max(max_affordable, 0))

            sig["stop_loss"] = stop
            sig["recommended_shares"] = shares
            sig["risk_amount"] = round((sig["price"] - stop) * shares, 0)

        for key in list(sig.keys()):
            val = sig[key]
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                sig[key] = None

        return json.dumps(sig, ensure_ascii=False, default=str)

    @mcp.tool()
    def get_technical_summary(ticker: str) -> str:
        """Get a comprehensive technical analysis summary for a stock.

        Includes SMA (25/100/200), RSI, ADX, MACD proxy, Ichimoku cloud status,
        CoCh detection, volume analysis, and 52-week position.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from data import fetch_stock_data
        from main import load_config
        from strategy import (
            generate_signal, calculate_sma, calculate_rsi, calculate_adx,
            calculate_ichimoku, detect_coch, compute_composite_score,
            fetch_tv_recommendation,
        )
        from nikkei225 import NIKKEI_225

        config = load_config()
        df = fetch_stock_data(ticker)
        close = df["Close"]
        latest = float(close.iloc[-1])

        # SMA
        sma25 = calculate_sma(close, 25)
        sma100 = calculate_sma(close, 100)
        sma200 = calculate_sma(close, 200)

        # RSI
        rsi = calculate_rsi(close)

        # ADX
        adx = calculate_adx(df)

        # Ichimoku
        strat = config.get("strategy", {})
        ichimoku = calculate_ichimoku(
            df,
            tenkan_period=strat.get("ichimoku_tenkan", 9),
            kijun_period=strat.get("ichimoku_kijun", 26),
            senkou_b_period=strat.get("ichimoku_senkou_b", 52),
        )

        tenkan = float(ichimoku["tenkan"].iloc[-1])
        kijun = float(ichimoku["kijun"].iloc[-1])
        senkou_a = float(ichimoku["senkou_a"].iloc[-1])
        senkou_b = float(ichimoku["senkou_b"].iloc[-1])
        cloud_top = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)

        # CoCh
        coch = detect_coch(df, lookback=strat.get("coch_lookback", 3))

        # Volume
        avg_vol_20 = float(df["Volume"].rolling(20).mean().iloc[-1])
        latest_vol = float(df["Volume"].iloc[-1])
        vol_ratio = latest_vol / avg_vol_20 if avg_vol_20 > 0 else 0

        # 52-week stats
        high_52w = float(df["High"].rolling(min(252, len(df))).max().iloc[-1])
        low_52w = float(df["Low"].rolling(min(252, len(df))).min().iloc[-1])
        pct_from_high = round((latest / high_52w - 1) * 100, 1) if high_52w > 0 else 0
        pct_from_low = round((latest / low_52w - 1) * 100, 1) if low_52w > 0 else 0

        # Signal + composite score
        sig = generate_signal(df, config)
        tv_score = None
        if strat.get("tv_recommendation_enabled", False):
            tv_score = fetch_tv_recommendation(ticker)
        composite = compute_composite_score(
            sig, df, strat.get("score_weights"),
            slope_days=strat.get("slope_days", 5),
            slope_blend=strat.get("slope_blend", 0.3),
            tv_score=tv_score,
        )

        # SMA25 slope
        slope_days = strat.get("slope_days", 5)
        sma25_cur = float(sma25.iloc[-1])
        sma25_prev = float(sma25.iloc[-1 - slope_days]) if len(sma25) > slope_days else sma25_cur
        sma25_slope = (sma25_cur - sma25_prev) / sma25_prev * 100 if sma25_prev > 0 else 0

        def safe_round(val, digits=1):
            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                return None
            return round(val, digits)

        result = {
            "ticker": ticker,
            "name": NIKKEI_225.get(ticker, ticker),
            "price": round(latest, 1),
            "signal": sig["signal"],
            "reason": sig.get("reason", ""),
            "composite_score": composite,
            "sma": {
                "sma25": safe_round(float(sma25.iloc[-1])),
                "sma100": safe_round(float(sma100.iloc[-1])),
                "sma200": safe_round(float(sma200.iloc[-1])),
                "sma25_slope_pct": safe_round(sma25_slope, 2),
                "golden_cross": bool(float(sma25.iloc[-1]) > float(sma100.iloc[-1])),
                "above_sma200": bool(latest > float(sma200.iloc[-1])),
            },
            "rsi": {
                "value": safe_round(float(rsi.iloc[-1])),
                "zone": "overbought" if float(rsi.iloc[-1]) > 70 else "oversold" if float(rsi.iloc[-1]) < 30 else "neutral",
            },
            "adx": {
                "value": safe_round(float(adx.iloc[-1])),
                "trend": "strong" if float(adx.iloc[-1]) >= 25 else "weak",
            },
            "ichimoku": {
                "tenkan": safe_round(tenkan),
                "kijun": safe_round(kijun),
                "senkou_a": safe_round(senkou_a),
                "senkou_b": safe_round(senkou_b),
                "cloud_top": safe_round(cloud_top),
                "cloud_bottom": safe_round(cloud_bottom),
                "above_cloud": bool(latest > cloud_top),
                "tenkan_above_kijun": bool(tenkan > kijun),
                "bullish": bool(tenkan > kijun and latest > cloud_top),
            },
            "coch": coch,
            "volume": {
                "latest": int(latest_vol),
                "avg_20d": int(avg_vol_20),
                "ratio": round(vol_ratio, 2),
            },
            "range_52w": {
                "high": round(high_52w, 1),
                "low": round(low_52w, 1),
                "pct_from_high": pct_from_high,
                "pct_from_low": pct_from_low,
            },
            "tv_score": tv_score,
        }
        return json.dumps(result, ensure_ascii=False, default=str)
