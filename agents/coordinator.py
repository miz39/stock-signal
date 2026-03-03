"""
統合エージェント（コーディネーター）
各エージェントのスコアを重み付けして総合判断を出す。
"""
from agents.technical import analyze as technical_analyze
from agents.fundamental import analyze as fundamental_analyze
from agents.sentiment import analyze as sentiment_analyze
from agents.risk_agent import analyze as risk_analyze
from data import fetch_stock_data

# 各エージェントの重み（合計1.0）
WEIGHTS = {
    "テクニカル": 0.35,
    "ファンダメンタル": 0.25,
    "センチメント": 0.20,
    "リスク": 0.20,
}

SIGNAL_MAP = {
    (1.0, 2.0): "STRONG_BUY",
    (0.3, 1.0): "BUY",
    (-0.3, 0.3): "HOLD",
    (-1.0, -0.3): "SELL",
    (-2.0, -1.0): "STRONG_SELL",
}

SIGNAL_LABELS = {
    "STRONG_BUY": "強い買い",
    "BUY": "買い",
    "HOLD": "様子見",
    "SELL": "売り",
    "STRONG_SELL": "強い売り",
}


def classify_signal(score):
    """スコアからシグナルを判定する。"""
    if score >= 1.0:
        return "STRONG_BUY"
    elif score >= 0.3:
        return "BUY"
    elif score > -0.3:
        return "HOLD"
    elif score > -1.0:
        return "SELL"
    else:
        return "STRONG_SELL"


def analyze_ticker(ticker, config):
    """
    1銘柄に対して全エージェントの分析を実行し、総合スコアを出す。

    Returns:
        dict: ticker, signal, total_score, agents (list), reasons_summary
    """
    df = fetch_stock_data(ticker)

    # 各エージェント実行
    results = []
    results.append(technical_analyze(df, config))
    results.append(fundamental_analyze(df, config, ticker=ticker))
    results.append(sentiment_analyze(df, config, ticker=ticker))
    results.append(risk_analyze(df, config, ticker=ticker))

    # 重み付きスコア計算
    total_score = 0.0
    for r in results:
        weight = WEIGHTS.get(r["agent"], 0.25)
        total_score += r["score"] * weight

    total_score = max(-2.0, min(2.0, round(total_score, 2)))
    signal = classify_signal(total_score)

    # 主要な理由を抽出（各エージェントの先頭理由）
    reasons_summary = []
    for r in results:
        if r["reasons"]:
            reasons_summary.append(f"【{r['agent']}】{r['reasons'][0]}")

    return {
        "ticker": ticker,
        "signal": signal,
        "signal_label": SIGNAL_LABELS[signal],
        "total_score": total_score,
        "agents": results,
        "reasons_summary": reasons_summary,
    }


def analyze_all(config):
    """全監視銘柄を分析する。"""
    results = []
    for ticker in config["watchlist"]:
        try:
            result = analyze_ticker(ticker, config)
            results.append(result)
        except Exception as e:
            results.append({
                "ticker": ticker,
                "signal": "HOLD",
                "signal_label": "様子見",
                "total_score": 0,
                "agents": [],
                "reasons_summary": [f"分析エラー: {e}"],
            })
    return results
