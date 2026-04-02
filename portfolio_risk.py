"""Portfolio-level risk analysis and anomaly detection."""

import json
import math
import os
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from nikkei225 import NIKKEI_225, get_sector

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))


# ── 3-1: Portfolio-level risk checks ──


def check_correlation(positions: list, threshold: float = 0.7, lookback: int = 60) -> list:
    """Check pairwise correlation of open positions using recent closing prices.

    Returns list of highly correlated pairs: [{"pair": (t1, t2), "corr": 0.85}, ...]
    """
    if len(positions) < 2:
        return []

    tickers = [p["ticker"] for p in positions]
    closes = {}
    for ticker in tickers:
        try:
            df = yf.download(ticker, period=f"{lookback + 10}d", progress=False)
            if df is not None and len(df) >= lookback:
                close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
                closes[ticker] = close.iloc[-lookback:].values
        except Exception:
            pass

    valid_tickers = [t for t in tickers if t in closes]
    if len(valid_tickers) < 2:
        return []

    # Build returns matrix
    returns = {}
    for t in valid_tickers:
        prices = closes[t]
        ret = np.diff(prices) / prices[:-1]
        returns[t] = ret

    # Pairwise correlation
    alerts = []
    for i in range(len(valid_tickers)):
        for j in range(i + 1, len(valid_tickers)):
            t1, t2 = valid_tickers[i], valid_tickers[j]
            min_len = min(len(returns[t1]), len(returns[t2]))
            if min_len < 20:
                continue
            corr = np.corrcoef(returns[t1][-min_len:], returns[t2][-min_len:])[0, 1]
            if abs(corr) >= threshold:
                alerts.append({
                    "pair": (t1, t2),
                    "names": (NIKKEI_225.get(t1, t1), NIKKEI_225.get(t2, t2)),
                    "corr": round(corr, 3),
                })

    alerts.sort(key=lambda x: -abs(x["corr"]))
    return alerts


def check_sector_concentration(positions: list, total_assets: float,
                               max_pct: float = 0.30) -> list:
    """Check if any sector exceeds max_pct of total assets by market value.

    Returns list of over-concentrated sectors.
    """
    if not positions or total_assets <= 0:
        return []

    sector_values = defaultdict(float)
    for pos in positions:
        value = pos.get("current_price", pos["entry_price"]) * pos["shares"]
        sector = get_sector(pos["ticker"])
        sector_values[sector] += value

    alerts = []
    for sector, value in sector_values.items():
        pct = value / total_assets
        if pct >= max_pct:
            tickers_in_sector = [
                NIKKEI_225.get(p["ticker"], p["ticker"])
                for p in positions if get_sector(p["ticker"]) == sector
            ]
            alerts.append({
                "sector": sector,
                "value": round(value),
                "pct": round(pct * 100, 1),
                "tickers": tickers_in_sector,
            })

    alerts.sort(key=lambda x: -x["pct"])
    return alerts


def check_portfolio_drawdown(total_assets: float, initial_balance: float,
                             max_dd_pct: float = 0.10) -> Optional[dict]:
    """Check if portfolio drawdown exceeds threshold.

    Returns alert dict if DD exceeds limit, None otherwise.
    """
    if initial_balance <= 0:
        return None

    # Track peak from execution history
    peak = initial_balance
    history = _load_execution_history()
    for entry in history:
        snap = entry.get("portfolio_snapshot", {})
        assets = snap.get("total_assets", 0)
        if assets > peak:
            peak = assets

    if total_assets > peak:
        peak = total_assets

    dd_pct = (total_assets / peak - 1) if peak > 0 else 0
    if dd_pct <= -max_dd_pct:
        return {
            "drawdown_pct": round(dd_pct * 100, 2),
            "peak": round(peak),
            "current": round(total_assets),
            "threshold": round(-max_dd_pct * 100, 1),
        }
    return None


# ── 3-2: Risk metrics ──


def calculate_portfolio_var(positions: list, total_assets: float,
                            confidence: float = 0.95, lookback: int = 60) -> dict:
    """Calculate portfolio VaR and CVaR using historical simulation.

    Returns: {"var_pct": float, "var_amount": float, "cvar_pct": float, "cvar_amount": float}
    """
    if not positions:
        return {"var_pct": 0, "var_amount": 0, "cvar_pct": 0, "cvar_amount": 0}

    # Download returns for each position
    weights = []
    all_returns = []
    for pos in positions:
        value = pos.get("current_price", pos["entry_price"]) * pos["shares"]
        try:
            df = yf.download(pos["ticker"], period=f"{lookback + 10}d", progress=False)
            if df is None or len(df) < lookback:
                continue
            close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
            prices = close.iloc[-lookback:].values
            ret = np.diff(prices) / prices[:-1]
            all_returns.append(ret)
            weights.append(value)
        except Exception:
            continue

    if not all_returns:
        return {"var_pct": 0, "var_amount": 0, "cvar_pct": 0, "cvar_amount": 0}

    # Align lengths
    min_len = min(len(r) for r in all_returns)
    returns_matrix = np.array([r[-min_len:] for r in all_returns])
    weight_arr = np.array(weights) / sum(weights)

    # Portfolio daily returns (weighted)
    portfolio_returns = returns_matrix.T @ weight_arr

    # VaR at confidence level
    var_pct = float(np.percentile(portfolio_returns, (1 - confidence) * 100))
    var_amount = round(var_pct * total_assets, 0)

    # CVaR (expected shortfall beyond VaR)
    tail = portfolio_returns[portfolio_returns <= var_pct]
    cvar_pct = float(np.mean(tail)) if len(tail) > 0 else var_pct
    cvar_amount = round(cvar_pct * total_assets, 0)

    return {
        "var_pct": round(var_pct * 100, 2),
        "var_amount": var_amount,
        "cvar_pct": round(cvar_pct * 100, 2),
        "cvar_amount": cvar_amount,
        "confidence": confidence,
        "lookback": lookback,
    }


def calculate_portfolio_volatility(positions: list, lookback: int = 20) -> dict:
    """Calculate annualized portfolio volatility.

    Returns: {"daily_vol": float, "annual_vol": float}
    """
    if not positions:
        return {"daily_vol": 0, "annual_vol": 0}

    weights = []
    all_returns = []
    for pos in positions:
        value = pos.get("current_price", pos["entry_price"]) * pos["shares"]
        try:
            df = yf.download(pos["ticker"], period=f"{lookback + 10}d", progress=False)
            if df is None or len(df) < lookback:
                continue
            close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
            prices = close.iloc[-lookback:].values
            ret = np.diff(prices) / prices[:-1]
            all_returns.append(ret)
            weights.append(value)
        except Exception:
            continue

    if not all_returns:
        return {"daily_vol": 0, "annual_vol": 0}

    min_len = min(len(r) for r in all_returns)
    returns_matrix = np.array([r[-min_len:] for r in all_returns])
    weight_arr = np.array(weights) / sum(weights)

    portfolio_returns = returns_matrix.T @ weight_arr
    daily_vol = float(np.std(portfolio_returns, ddof=1))
    annual_vol = daily_vol * math.sqrt(252)

    return {
        "daily_vol": round(daily_vol * 100, 3),
        "annual_vol": round(annual_vol * 100, 2),
    }


def calculate_atr(ticker: str, period: int = 14) -> Optional[float]:
    """Calculate ATR (Average True Range) for a ticker.

    Returns ATR value or None if data insufficient.
    """
    try:
        df = yf.download(ticker, period="3mo", progress=False)
        if df is None or len(df) < period + 1:
            return None

        high = df["High"].squeeze() if isinstance(df["High"], pd.DataFrame) else df["High"]
        low = df["Low"].squeeze() if isinstance(df["Low"], pd.DataFrame) else df["Low"]
        close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return round(float(atr.iloc[-1]), 2)
    except Exception:
        return None


# ── 3-3: Anomaly detection ──


def check_anomalies(config: dict) -> list:
    """Run all anomaly checks. Returns list of alert dicts."""
    alerts = []

    # 1. Consecutive days with zero BUY signals
    zero_buy = _check_zero_buy_streak(threshold=3)
    if zero_buy:
        alerts.append(zero_buy)

    # 2. Drawdown threshold
    balance = config.get("account", {}).get("balance", 300000)
    dd_alert = _check_drawdown_from_history(balance, max_dd_pct=0.10)
    if dd_alert:
        alerts.append(dd_alert)

    # 3. Stale execution (last run > 24h ago on weekday)
    stale = _check_stale_execution(max_hours=26)
    if stale:
        alerts.append(stale)

    # 4. Data freshness (most recent price data is stale)
    freshness = _check_data_freshness()
    if freshness:
        alerts.append(freshness)

    return alerts


def _check_zero_buy_streak(threshold: int = 3) -> Optional[dict]:
    """Check if the last N execution sessions had zero BUY signals."""
    history = _load_execution_history()
    if len(history) < threshold:
        return None

    recent = history[-threshold:]
    all_zero = all(h.get("scan", {}).get("buy_count", 0) == 0 for h in recent)
    if all_zero:
        dates = [h.get("date", "?") for h in recent]
        return {
            "type": "zero_buy_streak",
            "severity": "warning",
            "message": f"{threshold}回連続でBUYシグナルがゼロ（{dates[0]}〜{dates[-1]}）",
        }
    return None


def _check_drawdown_from_history(initial_balance: float,
                                 max_dd_pct: float = 0.10) -> Optional[dict]:
    """Check portfolio drawdown from execution history peak."""
    history = _load_execution_history()
    if not history:
        return None

    peak = initial_balance
    for h in history:
        assets = h.get("portfolio_snapshot", {}).get("total_assets", 0)
        if assets > peak:
            peak = assets

    latest = history[-1].get("portfolio_snapshot", {}).get("total_assets", 0)
    if peak <= 0 or latest <= 0:
        return None

    dd = (latest / peak - 1)
    if dd <= -max_dd_pct:
        return {
            "type": "drawdown_alert",
            "severity": "critical",
            "message": f"ポートフォリオDD {dd*100:.1f}%（ピーク ¥{peak:,.0f} → 現在 ¥{latest:,.0f}）",
            "drawdown_pct": round(dd * 100, 2),
        }
    return None


def _check_stale_execution(max_hours: int = 26) -> Optional[dict]:
    """Check if last execution is too old (accounting for weekends/holidays)."""
    history = _load_execution_history()
    if not history:
        return {
            "type": "stale_execution",
            "severity": "warning",
            "message": "実行履歴がありません",
        }

    last_ts = history[-1].get("timestamp")
    if not last_ts:
        return None

    try:
        last_dt = datetime.fromisoformat(last_ts)
        now = datetime.now(JST)
        # Only alert on weekdays
        if now.weekday() >= 5:  # Saturday/Sunday
            return None
        hours_ago = (now - last_dt).total_seconds() / 3600
        if hours_ago > max_hours:
            return {
                "type": "stale_execution",
                "severity": "warning",
                "message": f"最終実行から{hours_ago:.0f}時間経過（{last_dt.strftime('%m/%d %H:%M')}）",
            }
    except Exception:
        pass
    return None


def _check_data_freshness() -> Optional[dict]:
    """Spot-check that market data is recent (not stale)."""
    try:
        df = yf.download("7203.T", period="5d", progress=False)  # Toyota as proxy
        if df is None or df.empty:
            return {
                "type": "data_stale",
                "severity": "warning",
                "message": "株価データを取得できません",
            }
        last_date = df.index[-1]
        now = datetime.now(JST)
        # Allow up to 3 calendar days (weekends + holidays)
        if (now - last_date.to_pydatetime().replace(tzinfo=JST)).days > 3:
            return {
                "type": "data_stale",
                "severity": "warning",
                "message": f"最新株価データが古い（最終: {last_date.strftime('%Y-%m-%d')}）",
            }
    except Exception:
        pass
    return None


# ── Helpers ──


def _load_execution_history(profile: str = "default") -> list:
    """Load execution history JSON."""
    filename = "execution_history.json" if profile == "default" else f"execution_history_{profile}.json"
    path = os.path.join(_BASE_DIR, filename)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def format_risk_report(positions: list, total_assets: float, config: dict) -> dict:
    """Generate a comprehensive risk report dict for CLI output."""
    balance = config.get("account", {}).get("balance", 300000)

    # Sector concentration
    sector_alerts = check_sector_concentration(positions, total_assets)

    # Sector breakdown (always show)
    sector_values = defaultdict(lambda: {"value": 0, "count": 0, "tickers": []})
    for pos in positions:
        value = pos.get("current_price", pos["entry_price"]) * pos["shares"]
        sector = get_sector(pos["ticker"])
        sector_values[sector]["value"] += value
        sector_values[sector]["count"] += 1
        sector_values[sector]["tickers"].append(NIKKEI_225.get(pos["ticker"], pos["ticker"]))

    sectors = []
    for sector, info in sorted(sector_values.items(), key=lambda x: -x[1]["value"]):
        pct = info["value"] / total_assets * 100 if total_assets > 0 else 0
        sectors.append({
            "sector": sector,
            "value": round(info["value"]),
            "pct": round(pct, 1),
            "count": info["count"],
            "tickers": info["tickers"],
        })

    # Drawdown
    dd = check_portfolio_drawdown(total_assets, balance)

    # Anomalies
    anomalies = check_anomalies(config)

    return {
        "total_assets": round(total_assets),
        "initial_balance": balance,
        "position_count": len(positions),
        "sectors": sectors,
        "sector_alerts": sector_alerts,
        "drawdown": dd,
        "anomalies": anomalies,
    }
