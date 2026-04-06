"""
感応度分析エージェント
DCF の前提（WACC, 成長率）を変動させた場合の理論株価レンジを算出する。
"""
import logging

import numpy as np

from agents.dcf import (
    _extract_fcf, _get_net_debt, _get_shares_outstanding,
    _estimate_growth_rate, _get_sector_wacc,
)
from data import fetch_financial_statements

logger = logging.getLogger("signal")


def _run_dcf_scenario(base_fcf, growth_rate, wacc, terminal_growth,
                      projection_years, net_debt, shares) -> float:
    """Run a single DCF scenario and return fair value per share."""
    if shares <= 0 or wacc <= terminal_growth:
        return 0.0

    # Project FCF
    fcf_projected = []
    for year in range(1, projection_years + 1):
        projected = base_fcf * (1 + growth_rate) ** year
        fcf_projected.append(projected)

    # Discount
    pv_fcfs = sum(
        fcf / (1 + wacc) ** year
        for year, fcf in enumerate(fcf_projected, 1)
    )

    # Terminal Value
    tv = fcf_projected[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_tv = tv / (1 + wacc) ** projection_years

    # Enterprise Value → Equity Value → Per Share
    ev = pv_fcfs + pv_tv
    equity_value = ev - net_debt
    return equity_value / shares


def analyze(df, config, ticker="") -> dict:
    """
    感応度分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    if not ticker:
        return {
            "agent": "感応度",
            "score": 0,
            "confidence": 0,
            "reasons": ["ティッカーが指定されていません"],
            "metrics": {},
        }

    try:
        statements = fetch_financial_statements(ticker)
    except Exception as e:
        return {
            "agent": "感応度",
            "score": 0,
            "confidence": 0,
            "reasons": [f"財務データ取得失敗: {e}"],
            "metrics": {},
        }

    cash_flow = statements["cash_flow"]
    balance_sheet = statements["balance_sheet"]
    info = statements["info"]

    # Extract FCF
    fcf_history = _extract_fcf(cash_flow)
    if not fcf_history:
        return {
            "agent": "感応度",
            "score": 0,
            "confidence": 0,
            "reasons": ["FCF データが取得できません"],
            "metrics": {},
        }

    # Base parameters
    base_fcf = fcf_history[0]
    if base_fcf <= 0:
        positive = [f for f in fcf_history if f > 0]
        if positive:
            base_fcf = np.mean(positive)
        else:
            return {
                "agent": "感応度",
                "score": -1.0,
                "confidence": 20,
                "reasons": ["FCF がマイナスのため感応度分析不可"],
                "metrics": {},
            }

    val_cfg = config.get("valuation", {})
    base_wacc = _get_sector_wacc(ticker, config)
    base_terminal = val_cfg.get("terminal_growth", 0.015)
    projection_years = val_cfg.get("projection_years", 5)
    growth_rate = _estimate_growth_rate(fcf_history)

    net_debt = _get_net_debt(balance_sheet)
    shares = _get_shares_outstanding(info)

    if shares <= 0:
        return {
            "agent": "感応度",
            "score": 0,
            "confidence": 10,
            "reasons": ["発行済株式数が取得できません"],
            "metrics": {},
        }

    current_price = float(df["Close"].iloc[-1]) if len(df) > 0 else 0
    if current_price <= 0:
        return {
            "agent": "感応度",
            "score": 0,
            "confidence": 0,
            "reasons": ["現在株価が取得できません"],
            "metrics": {},
        }

    # WACC range: base ± 2%
    wacc_range = [base_wacc - 0.02, base_wacc, base_wacc + 0.02]
    # Terminal growth range: base ± 1%
    growth_range = [
        max(0.001, base_terminal - 0.01),
        base_terminal,
        base_terminal + 0.01,
    ]

    # Build 3x3 sensitivity table
    sensitivity_table = []
    all_fair_values = []

    for wacc in wacc_range:
        row = []
        for tg in growth_range:
            if wacc <= tg:
                fv = 0  # Invalid scenario
            else:
                fv = _run_dcf_scenario(
                    base_fcf, growth_rate, wacc, tg,
                    projection_years, net_debt, shares,
                )
            row.append(round(fv, 0))
            if fv > 0:
                all_fair_values.append(fv)
        sensitivity_table.append(row)

    if not all_fair_values:
        return {
            "agent": "感応度",
            "score": 0,
            "confidence": 10,
            "reasons": ["有効なシナリオを生成できませんでした"],
            "metrics": {},
        }

    # Bull / Base / Bear scenarios
    bull_fv = max(all_fair_values)
    bear_fv = min(all_fair_values)
    base_fv = _run_dcf_scenario(
        base_fcf, growth_rate, base_wacc, base_terminal,
        projection_years, net_debt, shares,
    )

    # Scoring: how many scenarios are above current price?
    above_count = sum(1 for fv in all_fair_values if fv > current_price)
    total_scenarios = len(all_fair_values)
    above_ratio = above_count / total_scenarios

    if above_ratio >= 1.0:
        score = 2.0
    elif above_ratio >= 0.8:
        score = 1.5
    elif above_ratio >= 0.6:
        score = 0.5
    elif above_ratio >= 0.4:
        score = 0.0
    elif above_ratio >= 0.2:
        score = -0.5
    else:
        score = -1.5

    score = max(-2.0, min(2.0, score))

    # Percentile of current price within scenario range
    if bull_fv > bear_fv:
        price_percentile = (current_price - bear_fv) / (bull_fv - bear_fv) * 100
        price_percentile = max(0, min(100, price_percentile))
    else:
        price_percentile = 50

    reasons = []
    reasons.append(
        f"Bull ¥{bull_fv:,.0f} / Base ¥{base_fv:,.0f} / Bear ¥{bear_fv:,.0f}"
    )
    reasons.append(
        f"現在株価 ¥{current_price:,.0f} → {above_count}/{total_scenarios} シナリオで割安"
    )
    if above_ratio >= 0.8:
        reasons.append("ほぼ全シナリオで割安 → 高確信")
    elif above_ratio <= 0.2:
        reasons.append("ほぼ全シナリオで割高 → 見送り")

    metrics = {
        "scenarios": {
            "bull": {
                "wacc": wacc_range[0],
                "growth": growth_range[2],
                "fair_value": round(bull_fv, 0),
            },
            "base": {
                "wacc": base_wacc,
                "growth": base_terminal,
                "fair_value": round(base_fv, 0),
            },
            "bear": {
                "wacc": wacc_range[2],
                "growth": growth_range[0],
                "fair_value": round(bear_fv, 0),
            },
        },
        "sensitivity_table": sensitivity_table,
        "wacc_range": [round(w, 3) for w in wacc_range],
        "growth_range": [round(g, 3) for g in growth_range],
        "current_price": current_price,
        "current_price_percentile": round(price_percentile, 0),
        "scenarios_above_price": above_count,
        "total_scenarios": total_scenarios,
    }

    confidence = min(100, int(above_ratio * 50 + len(fcf_history) * 15))

    return {
        "agent": "感応度",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
