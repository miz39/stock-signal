"""
オペレーティングモデル分析エージェント
収益構造の質（営業レバレッジ、マージン安定性、ROIC）を分析する。
"""
import logging

import numpy as np
import pandas as pd

from data import fetch_financial_statements

logger = logging.getLogger("signal")


def _safe_get(df: pd.DataFrame, labels: list, col) -> float:
    """Safely get a value from a DataFrame by trying multiple row labels."""
    if df is None or df.empty:
        return None
    for label in labels:
        if label in df.index:
            val = df.loc[label, col]
            if pd.notna(val):
                return float(val)
    return None


def _calc_roic(income_statement: pd.DataFrame, balance_sheet: pd.DataFrame) -> list:
    """Calculate ROIC for available years. Returns list of (year, roic) tuples."""
    if income_statement is None or balance_sheet is None:
        return []

    results = []
    common_cols = set(income_statement.columns) & set(balance_sheet.columns)

    for col in sorted(common_cols, reverse=True):
        # NOPAT = Operating Income * (1 - tax_rate)
        op_income = _safe_get(income_statement, [
            "Operating Income", "EBIT", "Operating Profit"
        ], col)
        tax = _safe_get(income_statement, [
            "Tax Provision", "Income Tax Expense"
        ], col)
        pretax = _safe_get(income_statement, [
            "Pretax Income", "Income Before Tax"
        ], col)

        if op_income is None:
            continue

        # Estimate tax rate
        tax_rate = 0.30  # Default for Japan
        if tax is not None and pretax is not None and pretax > 0:
            tax_rate = min(0.5, max(0, tax / pretax))

        nopat = op_income * (1 - tax_rate)

        # Invested Capital = Total Equity + Total Debt - Cash
        equity = _safe_get(balance_sheet, [
            "Stockholders Equity", "Total Stockholders Equity",
            "Total Equity Gross Minority Interest"
        ], col)
        debt = _safe_get(balance_sheet, [
            "Total Debt", "Long Term Debt",
            "Long Term Debt And Capital Lease Obligation"
        ], col)
        cash = _safe_get(balance_sheet, [
            "Cash And Cash Equivalents", "Cash",
            "Cash Cash Equivalents And Short Term Investments"
        ], col)

        if equity is None:
            continue

        invested_capital = (equity or 0) + (debt or 0) - (cash or 0)
        if invested_capital <= 0:
            continue

        roic = nopat / invested_capital
        results.append(roic)

    return results


def analyze(df, config, ticker="") -> dict:
    """
    オペレーティングモデル分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    if not ticker:
        return {
            "agent": "オペレーティング",
            "score": 0,
            "confidence": 0,
            "reasons": ["ティッカーが指定されていません"],
            "metrics": {},
        }

    try:
        statements = fetch_financial_statements(ticker)
    except Exception as e:
        return {
            "agent": "オペレーティング",
            "score": 0,
            "confidence": 0,
            "reasons": [f"財務データ取得失敗: {e}"],
            "metrics": {},
        }

    income_statement = statements["income_statement"]
    balance_sheet = statements["balance_sheet"]

    score = 0.0
    reasons = []
    metrics = {}
    data_points = 0

    # --- ROIC ---
    roic_values = _calc_roic(income_statement, balance_sheet)
    if roic_values:
        latest_roic = roic_values[0] * 100
        metrics["roic_latest"] = round(latest_roic, 1)
        data_points += 1

        if latest_roic > 15:
            score += 0.6
            reasons.append(f"ROIC {latest_roic:.1f}% → 高い資本効率")
        elif latest_roic > 8:
            score += 0.2
            reasons.append(f"ROIC {latest_roic:.1f}% → 平均的")
        elif latest_roic > 0:
            score -= 0.2
            reasons.append(f"ROIC {latest_roic:.1f}% → WACC 割れの可能性")
        else:
            score -= 0.5
            reasons.append(f"ROIC {latest_roic:.1f}% → 資本コスト未達")

        if len(roic_values) >= 2:
            avg_roic = np.mean(roic_values) * 100
            metrics["roic_avg"] = round(avg_roic, 1)

    # --- Operating Leverage ---
    if income_statement is not None and not income_statement.empty:
        cols = income_statement.columns
        if len(cols) >= 2:
            rev_labels = ["Total Revenue", "Revenue", "Net Sales"]
            op_labels = ["Operating Income", "EBIT", "Operating Profit"]

            rev_new = _safe_get(income_statement, rev_labels, cols[0])
            rev_old = _safe_get(income_statement, rev_labels, cols[1])
            op_new = _safe_get(income_statement, op_labels, cols[0])
            op_old = _safe_get(income_statement, op_labels, cols[1])

            if all(v is not None and v != 0 for v in [rev_new, rev_old, op_new, op_old]):
                rev_growth = (rev_new - rev_old) / abs(rev_old) * 100
                op_growth = (op_new - op_old) / abs(op_old) * 100

                if rev_growth != 0:
                    op_leverage = op_growth / rev_growth
                    metrics["operating_leverage"] = round(op_leverage, 2)
                    metrics["revenue_growth_yoy"] = round(rev_growth, 1)
                    metrics["op_income_growth_yoy"] = round(op_growth, 1)
                    data_points += 1

                    if op_leverage > 1.5 and rev_growth > 0:
                        score += 0.4
                        reasons.append(f"営業レバレッジ {op_leverage:.1f}x → 増収時の利益拡大効果大")
                    elif op_leverage < 0 and rev_growth > 0:
                        score -= 0.3
                        reasons.append(f"営業レバレッジ {op_leverage:.1f}x → 増収減益")

    # --- Margin Stability ---
    if income_statement is not None and not income_statement.empty:
        op_margins = []
        for col in income_statement.columns:
            rev = _safe_get(income_statement, ["Total Revenue", "Revenue", "Net Sales"], col)
            op_inc = _safe_get(income_statement, [
                "Operating Income", "EBIT", "Operating Profit"
            ], col)
            if rev and op_inc and rev > 0:
                op_margins.append(op_inc / rev * 100)

        if len(op_margins) >= 2:
            margin_std = float(np.std(op_margins))
            metrics["margin_stability_std"] = round(margin_std, 2)
            data_points += 1

            if margin_std < 2.0:
                score += 0.3
                reasons.append(f"マージン安定性 σ={margin_std:.1f}pp → 高い安定性")
            elif margin_std > 5.0:
                score -= 0.3
                reasons.append(f"マージン安定性 σ={margin_std:.1f}pp → 変動大")

    score = max(-2.0, min(2.0, score))
    confidence = min(100, int(data_points / 3 * 100))

    if not reasons:
        reasons.append("分析に必要なデータが不足しています")
        confidence = 0

    return {
        "agent": "オペレーティング",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
