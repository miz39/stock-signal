"""
統合エージェント（コーディネーター）
各エージェントのスコアを重み付けして総合判断を出す。
config.yaml の agents 設定に基づき、重みや有効化を制御する。

2層構成:
  - Trading Layer: テクニカル/ファンダ/センチメント/リスク（スイング判断）
  - Valuation Layer: DCF/Comps/三表/オペレーティング/感応度（バリュエーション判断）
"""
import logging

from agents.technical import analyze as technical_analyze
from agents.fundamental import analyze as fundamental_analyze
from agents.sentiment import analyze as sentiment_analyze
from agents.risk_agent import analyze as risk_analyze
from data import fetch_stock_data

logger = logging.getLogger("signal")

# Default weights (overridden by config.yaml agents.weights)
DEFAULT_WEIGHTS = {
    "テクニカル": 0.35,
    "ファンダメンタル": 0.25,
    "センチメント": 0.20,
    "リスク": 0.20,
}

WEIGHT_KEY_MAP = {
    "technical": "テクニカル",
    "fundamental": "ファンダメンタル",
    "sentiment": "センチメント",
    "risk": "リスク",
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


def _get_weights(config):
    """Get agent weights from config, falling back to defaults."""
    agents_cfg = config.get("agents", {})
    weight_cfg = agents_cfg.get("weights", {})
    weights = dict(DEFAULT_WEIGHTS)
    for eng_key, ja_key in WEIGHT_KEY_MAP.items():
        if eng_key in weight_cfg:
            weights[ja_key] = weight_cfg[eng_key]
    return weights


def analyze_ticker(ticker, config, df=None):
    """
    1銘柄に対して全エージェントの分析を実行し、総合スコアを出す。

    Args:
        ticker: Stock ticker (e.g. "7203.T")
        config: Full config dict
        df: Optional pre-fetched DataFrame (avoids redundant API call)

    Returns:
        dict: ticker, signal, total_score, agents (list), reasons_summary, confidence
    """
    if df is None:
        df = fetch_stock_data(ticker)

    weights = _get_weights(config)

    # Run each agent
    results = []
    results.append(technical_analyze(df, config))
    results.append(fundamental_analyze(df, config, ticker=ticker))
    results.append(sentiment_analyze(df, config, ticker=ticker))
    results.append(risk_analyze(df, config, ticker=ticker))

    # Weighted score calculation
    total_score = 0.0
    total_weight = 0.0
    for r in results:
        weight = weights.get(r["agent"], 0.25)
        total_score += r["score"] * weight
        total_weight += weight

    if total_weight > 0 and total_weight != 1.0:
        total_score = total_score / total_weight

    total_score = max(-2.0, min(2.0, round(total_score, 2)))
    signal = classify_signal(total_score)

    # Confidence: weighted average of agent confidences, reduced if data is sparse
    weighted_conf = 0.0
    for r in results:
        weight = weights.get(r["agent"], 0.25)
        weighted_conf += r.get("confidence", 50) * weight
    if total_weight > 0:
        weighted_conf /= total_weight
    avg_confidence = int(min(100, weighted_conf))

    # Reasons summary — each agent's top reason (Japanese text)
    reasons_summary = []
    for r in results:
        if r["reasons"]:
            reasons_summary.append(f"【{r['agent']}】{r['reasons'][0]}")

    # Detailed reasons per agent
    agent_details = []
    for r in results:
        agent_details.append({
            "agent": r["agent"],
            "score": r["score"],
            "confidence": r.get("confidence", 0),
            "reasons": r["reasons"],
            "metrics": r.get("metrics", {}),
        })

    return {
        "ticker": ticker,
        "signal": signal,
        "signal_label": SIGNAL_LABELS[signal],
        "total_score": total_score,
        "confidence": avg_confidence,
        "agents": agent_details,
        "reasons_summary": reasons_summary,
    }


def analyze_candidates(buy_signals, dfs, config, max_analyze=10):
    """
    Analyze top N buy candidates with the multi-agent system.

    Args:
        buy_signals: List of signal dicts (sorted by composite_score)
        dfs: Dict mapping ticker -> DataFrame
        config: Full config dict
        max_analyze: Max candidates to analyze

    Returns:
        list: buy_signals with 'agent_analysis' field added to analyzed ones
    """
    agents_cfg = config.get("agents", {})
    if not agents_cfg.get("enabled", False):
        return buy_signals

    n = min(max_analyze, agents_cfg.get("analyze_top_n", 10))
    analyzed_count = 0

    for sig in buy_signals[:n]:
        ticker = sig["ticker"]
        df = dfs.get(ticker)
        try:
            analysis = analyze_ticker(ticker, config, df=df)
            sig["agent_analysis"] = analysis
            analyzed_count += 1
        except Exception as e:
            logger.warning(f"Agent analysis failed for {ticker}: {e}")
            sig["agent_analysis"] = {
                "ticker": ticker,
                "signal": "HOLD",
                "signal_label": "様子見",
                "total_score": 0,
                "confidence": 0,
                "agents": [],
                "reasons_summary": [f"分析エラー: {e}"],
            }

    logger.info(f"Agent analysis: {analyzed_count}/{n} candidates analyzed")
    return buy_signals


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
                "confidence": 0,
                "agents": [],
                "reasons_summary": [f"分析エラー: {e}"],
            })
    return results


# --- Valuation Layer ---

DEFAULT_VALUATION_WEIGHTS = {
    "DCF": 0.30,
    "類似企業比較": 0.25,
    "三表財務": 0.20,
    "オペレーティング": 0.15,
    "感応度": 0.10,
}

VALUATION_WEIGHT_KEY_MAP = {
    "dcf": "DCF",
    "comps": "類似企業比較",
    "three_statement": "三表財務",
    "operating_model": "オペレーティング",
    "sensitivity": "感応度",
}

VALUATION_SIGNAL_LABELS = {
    "STRONG_BUY": "大幅割安",
    "BUY": "割安",
    "HOLD": "適正",
    "SELL": "割高",
    "STRONG_SELL": "大幅割高",
}


def _get_valuation_weights(config):
    """Get valuation agent weights from config."""
    val_cfg = config.get("valuation", {})
    weight_cfg = val_cfg.get("weights", {})
    weights = dict(DEFAULT_VALUATION_WEIGHTS)
    for eng_key, ja_key in VALUATION_WEIGHT_KEY_MAP.items():
        if eng_key in weight_cfg:
            weights[ja_key] = weight_cfg[eng_key]
    return weights


def analyze_valuation(ticker, config, df=None):
    """
    1銘柄に対してバリュエーション分析を実行する。

    Args:
        ticker: Stock ticker (e.g. "7203.T")
        config: Full config dict
        df: Optional pre-fetched DataFrame

    Returns:
        dict: ticker, signal, total_score, agents, reasons_summary, confidence,
              fair_value, upside_pct
    """
    from agents.dcf import analyze as dcf_analyze
    from agents.three_statement import analyze as three_stmt_analyze
    from agents.comps import analyze as comps_analyze
    from agents.operating_model import analyze as operating_analyze
    from agents.sensitivity import analyze as sensitivity_analyze

    val_cfg = config.get("valuation", {})
    if not val_cfg.get("enabled", True):
        return {
            "ticker": ticker,
            "signal": "HOLD",
            "signal_label": "適正",
            "total_score": 0,
            "confidence": 0,
            "agents": [],
            "reasons_summary": ["バリュエーション分析は無効です"],
        }

    if df is None:
        df = fetch_stock_data(ticker)

    weights = _get_valuation_weights(config)

    # Run valuation agents
    results = []
    results.append(dcf_analyze(df, config, ticker=ticker))
    results.append(three_stmt_analyze(df, config, ticker=ticker))
    results.append(comps_analyze(df, config, ticker=ticker))
    results.append(operating_analyze(df, config, ticker=ticker))
    results.append(sensitivity_analyze(df, config, ticker=ticker))

    # Weighted score
    total_score = 0.0
    total_weight = 0.0
    for r in results:
        weight = weights.get(r["agent"], 0.20)
        total_score += r["score"] * weight
        total_weight += weight

    if total_weight > 0 and total_weight != 1.0:
        total_score = total_score / total_weight

    total_score = max(-2.0, min(2.0, round(total_score, 2)))
    signal = classify_signal(total_score)

    # Confidence
    weighted_conf = 0.0
    for r in results:
        weight = weights.get(r["agent"], 0.20)
        weighted_conf += r.get("confidence", 50) * weight
    if total_weight > 0:
        weighted_conf /= total_weight
    avg_confidence = int(min(100, weighted_conf))

    # Reasons summary
    reasons_summary = []
    for r in results:
        if r["reasons"]:
            reasons_summary.append(f"【{r['agent']}】{r['reasons'][0]}")

    # Agent details
    agent_details = []
    for r in results:
        agent_details.append({
            "agent": r["agent"],
            "score": r["score"],
            "confidence": r.get("confidence", 0),
            "reasons": r["reasons"],
            "metrics": r.get("metrics", {}),
        })

    # Extract fair value from DCF agent if available
    dcf_result = next((r for r in results if r["agent"] == "DCF"), None)
    fair_value = None
    upside_pct = None
    if dcf_result and dcf_result.get("metrics"):
        fair_value = dcf_result["metrics"].get("fair_value")
        upside_pct = dcf_result["metrics"].get("upside_pct")

    return {
        "ticker": ticker,
        "signal": signal,
        "signal_label": VALUATION_SIGNAL_LABELS.get(signal, signal),
        "total_score": total_score,
        "confidence": avg_confidence,
        "agents": agent_details,
        "reasons_summary": reasons_summary,
        "fair_value": fair_value,
        "upside_pct": upside_pct,
    }


def full_analysis(ticker, config, df=None):
    """
    Trading + Valuation 両方を実行し、総合判断を返す。

    Args:
        ticker: Stock ticker (e.g. "7203.T")
        config: Full config dict
        df: Optional pre-fetched DataFrame

    Returns:
        dict: ticker, trading, valuation, combined_signal, combined_score
    """
    if df is None:
        df = fetch_stock_data(ticker)

    trading = analyze_ticker(ticker, config, df=df)
    valuation = analyze_valuation(ticker, config, df=df)

    # Combined score: weighted average of trading and valuation
    # Trading 50% / Valuation 50% (equal weight for both perspectives)
    t_score = trading.get("total_score", 0)
    v_score = valuation.get("total_score", 0)
    combined_score = round((t_score * 0.5 + v_score * 0.5), 2)
    combined_score = max(-2.0, min(2.0, combined_score))
    combined_signal = classify_signal(combined_score)

    # Combined confidence
    t_conf = trading.get("confidence", 0)
    v_conf = valuation.get("confidence", 0)
    combined_confidence = int((t_conf + v_conf) / 2)

    return {
        "ticker": ticker,
        "trading": trading,
        "valuation": valuation,
        "combined_signal": combined_signal,
        "combined_signal_label": SIGNAL_LABELS.get(combined_signal, combined_signal),
        "combined_score": combined_score,
        "combined_confidence": combined_confidence,
    }
