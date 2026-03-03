"""
ファンダメンタル分析エージェント
PER, PBR, ROE, 配当利回り, 業績成長で企業の割安度と収益力を判断する。
"""
import yfinance as yf


def analyze(df, config, ticker=""):
    """
    ファンダメンタル分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    score = 0.0
    reasons = []
    metrics = {}

    try:
        stock = yf.Ticker(ticker)
        info = stock.info
    except Exception as e:
        return {
            "agent": "ファンダメンタル",
            "score": 0,
            "confidence": 0,
            "reasons": [f"データ取得失敗: {e}"],
            "metrics": {},
        }

    # PER（株価収益率）
    per = info.get("trailingPE") or info.get("forwardPE")
    if per and per < 500:  # 異常値を除外
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

    # PBR（株価純資産倍率）
    pbr = info.get("priceToBook")
    if pbr:
        metrics["PBR"] = round(pbr, 2)
        if pbr < 1.0:
            score += 0.4
            reasons.append(f"PBR {pbr:.2f} → 割安（純資産以下）")
        elif pbr > 3.0:
            score -= 0.3
            reasons.append(f"PBR {pbr:.2f} → 割高")
        else:
            reasons.append(f"PBR {pbr:.2f} → 適正")

    # ROE（自己資本利益率）
    roe = info.get("returnOnEquity")
    if roe:
        roe_pct = roe * 100
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

    # 配当利回り（yfinanceは日本株で%値を返す場合がある）
    div_yield = info.get("dividendYield")
    if div_yield:
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

    # 売上成長率
    revenue_growth = info.get("revenueGrowth")
    if revenue_growth:
        growth_pct = revenue_growth * 100
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

    # 利益成長率
    earnings_growth = info.get("earningsGrowth")
    if earnings_growth:
        eg_pct = earnings_growth * 100
        metrics["利益成長率"] = round(eg_pct, 1)
        if eg_pct > 20:
            score += 0.3
            reasons.append(f"利益成長率 +{eg_pct:.1f}% → 好調")
        elif eg_pct < -10:
            score -= 0.3
            reasons.append(f"利益成長率 {eg_pct:.1f}% → 減益")

    # 時価総額
    market_cap = info.get("marketCap")
    if market_cap:
        cap_billion = market_cap / 1e9
        metrics["時価総額"] = f"{cap_billion:.0f}B"

    if not metrics:
        return {
            "agent": "ファンダメンタル",
            "score": 0,
            "confidence": 0,
            "reasons": ["ファンダメンタルデータが取得できませんでした"],
            "metrics": {},
        }

    score = max(-2.0, min(2.0, score))
    confidence = min(100, int(abs(score) / 2.0 * 100))

    return {
        "agent": "ファンダメンタル",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
