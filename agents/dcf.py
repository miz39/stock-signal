"""
DCF バリュエーションエージェント
FCF ベースの理論株価を算出し、現在株価との乖離からスコアリングする。
"""
import logging

import numpy as np
import pandas as pd

from data import fetch_financial_statements

logger = logging.getLogger("signal")


def _extract_fcf(cash_flow: pd.DataFrame) -> list:
    """Extract Free Cash Flow (Operating CF - CapEx) for each year."""
    if cash_flow is None or cash_flow.empty:
        return []

    fcf_list = []
    for col in cash_flow.columns:
        operating_cf = None
        capex = None

        # Operating Cash Flow
        for label in ["Total Cash From Operating Activities",
                      "Operating Cash Flow",
                      "Cash Flow From Continuing Operating Activities"]:
            if label in cash_flow.index:
                val = cash_flow.loc[label, col]
                if pd.notna(val):
                    operating_cf = float(val)
                    break

        # Capital Expenditure (negative in statements)
        for label in ["Capital Expenditures", "Capital Expenditure"]:
            if label in cash_flow.index:
                val = cash_flow.loc[label, col]
                if pd.notna(val):
                    capex = float(val)
                    break

        if operating_cf is not None:
            # capex is typically negative; if missing, assume 0
            capex_val = capex if capex is not None else 0
            fcf = operating_cf + capex_val  # capex is negative, so this subtracts
            fcf_list.append(fcf)

    return fcf_list


def _get_net_debt(balance_sheet: pd.DataFrame) -> float:
    """Calculate net debt = total debt - cash."""
    if balance_sheet is None or balance_sheet.empty:
        return 0.0

    latest = balance_sheet.iloc[:, 0]  # Most recent year

    total_debt = 0.0
    for label in ["Total Debt", "Long Term Debt", "Long Term Debt And Capital Lease Obligation"]:
        if label in latest.index and pd.notna(latest[label]):
            total_debt = float(latest[label])
            break

    cash = 0.0
    for label in ["Cash And Cash Equivalents", "Cash",
                   "Cash Cash Equivalents And Short Term Investments"]:
        if label in latest.index and pd.notna(latest[label]):
            cash = float(latest[label])
            break

    return total_debt - cash


def _get_shares_outstanding(info: dict) -> float:
    """Get shares outstanding from yfinance info."""
    for key in ["sharesOutstanding", "impliedSharesOutstanding"]:
        val = info.get(key)
        if val and val > 0:
            return float(val)
    return 0.0


def _estimate_growth_rate(fcf_list: list) -> float:
    """Estimate FCF growth rate from historical data."""
    if len(fcf_list) < 2:
        return 0.05  # Default 5%

    # Filter out non-positive values for growth calculation
    positive_fcfs = [f for f in fcf_list if f > 0]
    if len(positive_fcfs) < 2:
        return 0.03

    # Calculate year-over-year growth rates
    # fcf_list is newest-first from yfinance; reverse for chronological
    chronological = list(reversed(positive_fcfs))
    growth_rates = []
    for i in range(1, len(chronological)):
        if chronological[i - 1] > 0:
            g = (chronological[i] - chronological[i - 1]) / chronological[i - 1]
            growth_rates.append(g)

    if not growth_rates:
        return 0.03

    avg_growth = np.mean(growth_rates)
    # Cap growth rate to reasonable range
    return max(-0.05, min(0.20, avg_growth))


def _get_sector_wacc(ticker: str, config: dict) -> float:
    """Get WACC for the ticker's sector from config."""
    from nikkei225 import get_sector

    val_cfg = config.get("valuation", {})
    wacc_defaults = val_cfg.get("wacc_defaults", {})
    sector = get_sector(ticker)
    return wacc_defaults.get(sector, 0.09)


def analyze(df, config, ticker="") -> dict:
    """
    DCF バリュエーション分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    if not ticker:
        return {
            "agent": "DCF",
            "score": 0,
            "confidence": 0,
            "reasons": ["ティッカーが指定されていません"],
            "metrics": {},
        }

    try:
        statements = fetch_financial_statements(ticker)
    except Exception as e:
        return {
            "agent": "DCF",
            "score": 0,
            "confidence": 0,
            "reasons": [f"財務データ取得失敗: {e}"],
            "metrics": {},
        }

    cash_flow = statements["cash_flow"]
    balance_sheet = statements["balance_sheet"]
    info = statements["info"]

    # Extract FCF history
    fcf_history = _extract_fcf(cash_flow)
    if not fcf_history:
        return {
            "agent": "DCF",
            "score": 0,
            "confidence": 0,
            "reasons": ["FCF データが取得できませんでした"],
            "metrics": {},
        }

    # Current price
    current_price = float(df["Close"].iloc[-1]) if len(df) > 0 else 0
    if current_price <= 0:
        return {
            "agent": "DCF",
            "score": 0,
            "confidence": 0,
            "reasons": ["現在株価が取得できません"],
            "metrics": {},
        }

    # Parameters
    val_cfg = config.get("valuation", {})
    wacc = _get_sector_wacc(ticker, config)
    terminal_growth = val_cfg.get("terminal_growth", 0.015)
    projection_years = val_cfg.get("projection_years", 5)

    # Growth rate estimation
    growth_rate = _estimate_growth_rate(fcf_history)

    # Base FCF (most recent, which is first in yfinance order)
    base_fcf = fcf_history[0]
    if base_fcf <= 0:
        # Use average of positive FCFs if latest is negative
        positive = [f for f in fcf_history if f > 0]
        if positive:
            base_fcf = np.mean(positive)
        else:
            return {
                "agent": "DCF",
                "score": -1.0,
                "confidence": 30,
                "reasons": ["FCF がマイナス → DCF 算出不可"],
                "metrics": {"fcf_history": fcf_history},
            }

    # Project FCF
    fcf_projected = []
    for year in range(1, projection_years + 1):
        projected = base_fcf * (1 + growth_rate) ** year
        fcf_projected.append(projected)

    # Discount projected FCFs
    pv_fcfs = sum(
        fcf / (1 + wacc) ** year
        for year, fcf in enumerate(fcf_projected, 1)
    )

    # Terminal Value
    if wacc <= terminal_growth:
        # Avoid division by zero or negative denominator
        terminal_growth_adj = wacc - 0.02
    else:
        terminal_growth_adj = terminal_growth

    terminal_value = fcf_projected[-1] * (1 + terminal_growth_adj) / (wacc - terminal_growth_adj)
    pv_terminal = terminal_value / (1 + wacc) ** projection_years

    # Enterprise Value
    enterprise_value = pv_fcfs + pv_terminal

    # Net debt
    net_debt = _get_net_debt(balance_sheet)

    # Equity Value
    equity_value = enterprise_value - net_debt

    # Shares outstanding
    shares = _get_shares_outstanding(info)
    if shares <= 0:
        return {
            "agent": "DCF",
            "score": 0,
            "confidence": 10,
            "reasons": ["発行済株式数が取得できません"],
            "metrics": {},
        }

    # Fair value per share
    fair_value = equity_value / shares
    upside_pct = (fair_value / current_price - 1) * 100

    # Scoring
    if upside_pct >= 30:
        score = 2.0
    elif upside_pct >= 20:
        score = 1.5
    elif upside_pct >= 10:
        score = 1.0
    elif upside_pct >= 0:
        score = 0.5
    elif upside_pct >= -10:
        score = 0.0
    elif upside_pct >= -20:
        score = -0.5
    elif upside_pct >= -30:
        score = -1.0
    else:
        score = -2.0

    score = max(-2.0, min(2.0, score))

    # Confidence based on data quality
    confidence_factors = []
    confidence_factors.append(min(100, len(fcf_history) * 30))  # More years = higher
    if all(f > 0 for f in fcf_history):
        confidence_factors.append(80)
    else:
        confidence_factors.append(40)
    if shares > 0:
        confidence_factors.append(70)
    confidence = min(100, int(np.mean(confidence_factors)))

    reasons = []
    reasons.append(f"理論株価 ¥{fair_value:,.0f} vs 現在 ¥{current_price:,.0f}（{upside_pct:+.1f}%）")
    reasons.append(f"WACC {wacc*100:.1f}%, 永続成長率 {terminal_growth_adj*100:.1f}%")
    reasons.append(f"FCF 成長率推定 {growth_rate*100:.1f}%（過去{len(fcf_history)}年）")

    if upside_pct > 20:
        reasons.append("大幅な割安 → 強い買い")
    elif upside_pct > 0:
        reasons.append("割安圏 → 買い検討")
    elif upside_pct > -20:
        reasons.append("適正〜やや割高")
    else:
        reasons.append("大幅な割高 → 見送り")

    metrics = {
        "fair_value": round(fair_value, 0),
        "upside_pct": round(upside_pct, 1),
        "wacc": wacc,
        "terminal_growth": terminal_growth_adj,
        "fcf_growth_rate": round(growth_rate, 3),
        "fcf_history": [round(f, 0) for f in fcf_history],
        "fcf_projected": [round(f, 0) for f in fcf_projected],
        "enterprise_value": round(enterprise_value, 0),
        "net_debt": round(net_debt, 0),
        "shares_outstanding": shares,
        "current_price": current_price,
    }

    return {
        "agent": "DCF",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
