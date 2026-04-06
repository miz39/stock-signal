"""
ファンダメンタル分析エージェント
PER, PBR, ROE, 配当利回り, 業績成長で企業の割安度と収益力を判断する。
data.py の fetch_financial_data() を使用し、J-Quants / yfinance 両対応。
"""
import logging
from datetime import date, datetime, timedelta

from data import fetch_financial_data

logger = logging.getLogger("signal")


def _is_near_earnings(next_earnings_date_str, days_before=5, days_after=2):
    """Check if we're close to an earnings announcement (avoid crossing earnings)."""
    if not next_earnings_date_str:
        return False
    try:
        earn_date = datetime.strptime(str(next_earnings_date_str)[:10], "%Y-%m-%d").date()
        today = date.today()
        diff = (earn_date - today).days
        return -days_after <= diff <= days_before
    except (ValueError, TypeError):
        return False


def _financial_health_score(fin_data):
    """Calculate financial health sub-score from equity ratio and debt levels."""
    score = 0.0
    reasons = []

    equity_ratio = fin_data.get("equity_ratio")
    if equity_ratio is not None:
        equity_pct = equity_ratio * 100 if equity_ratio <= 1 else equity_ratio
        if equity_pct > 50:
            score += 0.3
            reasons.append(f"自己資本比率 {equity_pct:.1f}% → 財務健全")
        elif equity_pct > 30:
            reasons.append(f"自己資本比率 {equity_pct:.1f}% → 標準")
        elif equity_pct > 0:
            score -= 0.2
            reasons.append(f"自己資本比率 {equity_pct:.1f}% → やや低い")

    debt_eq = fin_data.get("debt_equity_ratio")
    if debt_eq is not None:
        # yfinance returns as percentage (e.g. 50.0 for 50%)
        debt_ratio = debt_eq / 100 if debt_eq > 5 else debt_eq
        if debt_ratio > 2.0:
            score -= 0.3
            reasons.append(f"有利子負債比率 {debt_ratio:.1f}倍 → 高リスク")
        elif debt_ratio > 1.0:
            score -= 0.1
            reasons.append(f"有利子負債比率 {debt_ratio:.1f}倍 → やや高い")
        elif debt_ratio < 0.3:
            score += 0.2
            reasons.append(f"有利子負債比率 {debt_ratio:.1f}倍 → 低負債")

    return score, reasons


def analyze(df, config, ticker=""):
    """
    ファンダメンタル分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    score = 0.0
    reasons = []
    metrics = {}
    data_available = 0

    try:
        fin = fetch_financial_data(ticker)
    except Exception as e:
        return {
            "agent": "ファンダメンタル",
            "score": 0,
            "confidence": 0,
            "reasons": [f"データ取得失敗: {e}"],
            "metrics": {},
        }

    if fin.get("error"):
        return {
            "agent": "ファンダメンタル",
            "score": 0,
            "confidence": 0,
            "reasons": [f"データ取得失敗: {fin['error']}"],
            "metrics": {},
        }

    source = fin.get("source", "unknown")
    metrics["data_source"] = source

    # --- PER ---
    per = fin.get("per")
    if per and per < 500:
        data_available += 1
        metrics["PER"] = round(per, 1)
        if per < 10:
            score += 0.5
            reasons.append(f"PER {per:.1f} → 割安")
        elif per < 15:
            score += 0.2
            reasons.append(f"PER {per:.1f} → 適正〜やや割安")
        elif per > 30:
            score -= 0.5
            reasons.append(f"PER {per:.1f} → 割高")
        elif per > 20:
            score -= 0.2
            reasons.append(f"PER {per:.1f} → やや割高")
        else:
            reasons.append(f"PER {per:.1f} → 適正")

    # --- PBR ---
    pbr = fin.get("pbr")
    if pbr:
        data_available += 1
        metrics["PBR"] = round(pbr, 2)
        if pbr < 1.0:
            score += 0.4
            reasons.append(f"PBR {pbr:.2f} → 割安（純資産以下）")
        elif pbr > 3.0:
            score -= 0.3
            reasons.append(f"PBR {pbr:.2f} → 割高")
        else:
            reasons.append(f"PBR {pbr:.2f} → 適正")

    # --- ROE ---
    roe = fin.get("roe")
    if roe is not None:
        data_available += 1
        roe_pct = roe * 100 if abs(roe) <= 1 else roe
        metrics["ROE"] = round(roe_pct, 1)
        if roe_pct > 15:
            score += 0.4
            reasons.append(f"ROE {roe_pct:.1f}% → 高収益")
        elif roe_pct > 8:
            score += 0.1
            reasons.append(f"ROE {roe_pct:.1f}% → 平均的")
        elif roe_pct > 0:
            score -= 0.2
            reasons.append(f"ROE {roe_pct:.1f}% → 低収益")
        else:
            score -= 0.5
            reasons.append(f"ROE {roe_pct:.1f}% → 赤字")

    # --- 配当利回り ---
    div_yield = fin.get("dividend_yield")
    if div_yield:
        data_available += 1
        div_pct = div_yield if div_yield > 0.2 else div_yield * 100
        metrics["配当利回り"] = round(div_pct, 2)
        if div_pct > 4.0:
            score += 0.3
            reasons.append(f"配当利回り {div_pct:.2f}% → 高配当")
        elif div_pct > 2.0:
            score += 0.1
            reasons.append(f"配当利回り {div_pct:.2f}%")
        else:
            reasons.append(f"配当利回り {div_pct:.2f}%")
    else:
        reasons.append("配当なし")

    # --- 売上成長率 ---
    revenue_growth = fin.get("revenue_growth")
    if revenue_growth is not None:
        data_available += 1
        growth_pct = revenue_growth * 100 if abs(revenue_growth) <= 5 else revenue_growth
        metrics["売上成長率"] = round(growth_pct, 1)
        if growth_pct > 20:
            score += 0.4
            reasons.append(f"売上成長率 +{growth_pct:.1f}% → 高成長")
        elif growth_pct > 5:
            score += 0.2
            reasons.append(f"売上成長率 +{growth_pct:.1f}% → 成長")
        elif growth_pct > 0:
            reasons.append(f"売上成長率 +{growth_pct:.1f}%")
        else:
            score -= 0.3
            reasons.append(f"売上成長率 {growth_pct:.1f}% → 減収")

    # --- 利益成長率 ---
    earnings_growth = fin.get("earnings_growth")
    if earnings_growth is not None:
        data_available += 1
        eg_pct = earnings_growth * 100 if abs(earnings_growth) <= 5 else earnings_growth
        metrics["利益成長率"] = round(eg_pct, 1)
        if eg_pct > 20:
            score += 0.3
            reasons.append(f"利益成長率 +{eg_pct:.1f}% → 好調")
        elif eg_pct < -10:
            score -= 0.3
            reasons.append(f"利益成長率 {eg_pct:.1f}% → 減益")

    # --- 営業利益率 ---
    op_margin = fin.get("operating_margin")
    if op_margin is not None:
        data_available += 1
        op_pct = op_margin * 100 if abs(op_margin) <= 1 else op_margin
        metrics["営業利益率"] = round(op_pct, 1)
        if op_pct > 15:
            score += 0.2
            reasons.append(f"営業利益率 {op_pct:.1f}% → 高利益率")
        elif op_pct < 3:
            score -= 0.2
            reasons.append(f"営業利益率 {op_pct:.1f}% → 低利益率")

    # --- 時価総額 ---
    market_cap = fin.get("market_cap")
    if market_cap:
        cap_billion = market_cap / 1e9
        metrics["時価総額"] = f"{cap_billion:.0f}B"

    # --- 財務健全性 ---
    health_score, health_reasons = _financial_health_score(fin)
    score += health_score
    reasons.extend(health_reasons)
    if health_reasons:
        data_available += 1

    # --- 決算跨ぎチェック ---
    next_earnings = fin.get("next_earnings_date")
    if _is_near_earnings(next_earnings):
        score -= 0.5
        reasons.append(f"決算発表間近（{next_earnings}）→ 跨ぎリスク回避推奨")
        metrics["決算日"] = str(next_earnings)

    if data_available == 0:
        return {
            "agent": "ファンダメンタル",
            "score": 0,
            "confidence": 0,
            "reasons": ["ファンダメンタルデータが取得できませんでした"],
            "metrics": metrics,
        }

    score = max(-2.0, min(2.0, score))
    # Confidence based on data availability (more data = higher confidence)
    max_data_points = 8
    data_confidence = min(100, int(data_available / max_data_points * 100))
    score_confidence = min(100, int(abs(score) / 2.0 * 100))
    confidence = min(data_confidence, score_confidence) if score_confidence > 0 else data_confidence

    return {
        "agent": "ファンダメンタル",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
