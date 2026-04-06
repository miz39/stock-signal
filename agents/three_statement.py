"""
三表財務分析エージェント
BS/PL/CF の健全性チェックとトレンド分析を行う。
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


def _analyze_pl(income_statement: pd.DataFrame) -> dict:
    """Analyze income statement trends."""
    result = {"score": 0.0, "reasons": [], "metrics": {}}

    if income_statement is None or income_statement.empty:
        result["reasons"].append("損益計算書データなし")
        return result

    cols = income_statement.columns  # newest first
    years = len(cols)

    # Revenue trend
    revenues = []
    for col in cols:
        rev = _safe_get(income_statement, [
            "Total Revenue", "Revenue", "Net Sales"
        ], col)
        if rev is not None:
            revenues.append(rev)

    if len(revenues) >= 2:
        # Chronological order (oldest first)
        rev_chron = list(reversed(revenues))
        growth_rates = []
        for i in range(1, len(rev_chron)):
            if rev_chron[i - 1] > 0:
                g = (rev_chron[i] - rev_chron[i - 1]) / rev_chron[i - 1] * 100
                growth_rates.append(g)

        if growth_rates:
            avg_growth = np.mean(growth_rates)
            result["metrics"]["revenue_growth_avg"] = round(avg_growth, 1)
            if avg_growth > 10:
                result["score"] += 0.5
                result["reasons"].append(f"売上成長率 平均{avg_growth:+.1f}% → 高成長")
            elif avg_growth > 3:
                result["score"] += 0.2
                result["reasons"].append(f"売上成長率 平均{avg_growth:+.1f}%")
            elif avg_growth < -5:
                result["score"] -= 0.5
                result["reasons"].append(f"売上成長率 平均{avg_growth:+.1f}% → 減収傾向")

    # Operating margin trend
    op_margins = []
    for col in cols:
        rev = _safe_get(income_statement, ["Total Revenue", "Revenue", "Net Sales"], col)
        op_inc = _safe_get(income_statement, [
            "Operating Income", "EBIT", "Operating Profit"
        ], col)
        if rev and op_inc and rev > 0:
            op_margins.append(op_inc / rev * 100)

    if op_margins:
        latest_margin = op_margins[0]
        result["metrics"]["operating_margin_latest"] = round(latest_margin, 1)
        if latest_margin > 15:
            result["score"] += 0.3
            result["reasons"].append(f"営業利益率 {latest_margin:.1f}% → 高収益")
        elif latest_margin < 3:
            result["score"] -= 0.3
            result["reasons"].append(f"営業利益率 {latest_margin:.1f}% → 低収益")

        if len(op_margins) >= 2:
            margin_chron = list(reversed(op_margins))
            if margin_chron[-1] > margin_chron[0] + 2:
                result["score"] += 0.2
                result["reasons"].append("営業利益率 改善傾向")
            elif margin_chron[-1] < margin_chron[0] - 2:
                result["score"] -= 0.2
                result["reasons"].append("営業利益率 悪化傾向")

    # Net margin
    for col in cols[:1]:  # Latest only
        rev = _safe_get(income_statement, ["Total Revenue", "Revenue", "Net Sales"], col)
        net_inc = _safe_get(income_statement, [
            "Net Income", "Net Income Common Stockholders", "Profit"
        ], col)
        if rev and net_inc and rev > 0:
            net_margin = net_inc / rev * 100
            result["metrics"]["net_margin_latest"] = round(net_margin, 1)

    return result


def _analyze_bs(balance_sheet: pd.DataFrame) -> dict:
    """Analyze balance sheet health."""
    result = {"score": 0.0, "reasons": [], "metrics": {}}

    if balance_sheet is None or balance_sheet.empty:
        result["reasons"].append("貸借対照表データなし")
        return result

    latest = balance_sheet.columns[0]

    # Equity ratio
    total_assets = _safe_get(balance_sheet, ["Total Assets"], latest)
    equity = _safe_get(balance_sheet, [
        "Stockholders Equity", "Total Stockholders Equity",
        "Stockholders' Equity", "Total Equity Gross Minority Interest"
    ], latest)

    if total_assets and equity and total_assets > 0:
        equity_ratio = equity / total_assets * 100
        result["metrics"]["equity_ratio"] = round(equity_ratio, 1)
        if equity_ratio > 50:
            result["score"] += 0.3
            result["reasons"].append(f"自己資本比率 {equity_ratio:.1f}% → 財務健全")
        elif equity_ratio > 30:
            result["reasons"].append(f"自己資本比率 {equity_ratio:.1f}% → 標準")
        elif equity_ratio > 0:
            result["score"] -= 0.3
            result["reasons"].append(f"自己資本比率 {equity_ratio:.1f}% → やや低い")
        else:
            result["score"] -= 0.5
            result["reasons"].append("債務超過の可能性")

    # Current ratio
    current_assets = _safe_get(balance_sheet, ["Current Assets"], latest)
    current_liab = _safe_get(balance_sheet, [
        "Current Liabilities", "Current Debt And Capital Lease Obligation"
    ], latest)

    if current_assets and current_liab and current_liab > 0:
        current_ratio = current_assets / current_liab
        result["metrics"]["current_ratio"] = round(current_ratio, 2)
        if current_ratio > 2.0:
            result["score"] += 0.2
            result["reasons"].append(f"流動比率 {current_ratio:.2f} → 高い安全性")
        elif current_ratio < 1.0:
            result["score"] -= 0.3
            result["reasons"].append(f"流動比率 {current_ratio:.2f} → 短期流動性懸念")

    # Debt ratio
    total_debt = _safe_get(balance_sheet, [
        "Total Debt", "Long Term Debt",
        "Long Term Debt And Capital Lease Obligation"
    ], latest)

    if total_debt and equity and equity > 0:
        debt_equity = total_debt / equity
        result["metrics"]["debt_equity_ratio"] = round(debt_equity, 2)
        if debt_equity > 2.0:
            result["score"] -= 0.3
            result["reasons"].append(f"D/E レシオ {debt_equity:.2f} → 高レバレッジ")
        elif debt_equity < 0.5:
            result["score"] += 0.2
            result["reasons"].append(f"D/E レシオ {debt_equity:.2f} → 低負債")

    return result


def _analyze_cf(cash_flow: pd.DataFrame) -> dict:
    """Analyze cash flow quality."""
    result = {"score": 0.0, "reasons": [], "metrics": {}}

    if cash_flow is None or cash_flow.empty:
        result["reasons"].append("キャッシュフローデータなし")
        return result

    cols = cash_flow.columns

    # Operating CF stability
    op_cfs = []
    for col in cols:
        val = _safe_get(cash_flow, [
            "Total Cash From Operating Activities",
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities"
        ], col)
        if val is not None:
            op_cfs.append(val)

    if op_cfs:
        positive_count = sum(1 for cf in op_cfs if cf > 0)
        result["metrics"]["operating_cf_positive_years"] = positive_count
        result["metrics"]["operating_cf_total_years"] = len(op_cfs)

        if positive_count == len(op_cfs):
            result["score"] += 0.4
            result["reasons"].append(f"営業CF {len(op_cfs)}年連続プラス → 安定")
        elif positive_count >= len(op_cfs) * 0.5:
            result["reasons"].append(f"営業CF {positive_count}/{len(op_cfs)}年プラス")
        else:
            result["score"] -= 0.4
            result["reasons"].append(f"営業CF {positive_count}/{len(op_cfs)}年プラス → 不安定")

    # FCF margin (latest year)
    if cols.size > 0:
        latest = cols[0]
        op_cf = _safe_get(cash_flow, [
            "Total Cash From Operating Activities",
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities"
        ], latest)
        capex = _safe_get(cash_flow, [
            "Capital Expenditures", "Capital Expenditure"
        ], latest)

        if op_cf is not None:
            capex_val = capex if capex is not None else 0
            fcf = op_cf + capex_val
            result["metrics"]["latest_fcf"] = round(fcf, 0)
            if fcf > 0:
                result["score"] += 0.2
                result["reasons"].append(f"直近 FCF ¥{fcf/1e9:.1f}B → プラス")
            else:
                result["score"] -= 0.3
                result["reasons"].append(f"直近 FCF ¥{fcf/1e9:.1f}B → マイナス")

    return result


def analyze(df, config, ticker="") -> dict:
    """
    三表財務分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    if not ticker:
        return {
            "agent": "三表財務",
            "score": 0,
            "confidence": 0,
            "reasons": ["ティッカーが指定されていません"],
            "metrics": {},
        }

    try:
        statements = fetch_financial_statements(ticker)
    except Exception as e:
        return {
            "agent": "三表財務",
            "score": 0,
            "confidence": 0,
            "reasons": [f"財務データ取得失敗: {e}"],
            "metrics": {},
        }

    # Analyze each statement
    pl_result = _analyze_pl(statements["income_statement"])
    bs_result = _analyze_bs(statements["balance_sheet"])
    cf_result = _analyze_cf(statements["cash_flow"])

    # Aggregate
    total_score = pl_result["score"] + bs_result["score"] + cf_result["score"]
    total_score = max(-2.0, min(2.0, total_score))

    reasons = []
    reasons.extend(pl_result["reasons"])
    reasons.extend(bs_result["reasons"])
    reasons.extend(cf_result["reasons"])

    metrics = {}
    metrics.update(pl_result["metrics"])
    metrics.update(bs_result["metrics"])
    metrics.update(cf_result["metrics"])

    # Confidence based on data availability
    data_points = len(metrics)
    confidence = min(100, int(data_points / 8 * 100))

    return {
        "agent": "三表財務",
        "score": round(total_score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
