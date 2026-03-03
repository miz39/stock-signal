"""
テクニカル分析エージェント
SMA, RSI, MACD, ボリンジャーバンドで総合的にトレンドと売買タイミングを判断する。
"""
import pandas as pd
import numpy as np


def _sma(prices, period):
    return prices.rolling(window=period).mean()


def _rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(prices, fast=12, slow=26, signal=9):
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(prices, period=20, num_std=2):
    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower


def analyze(df, config):
    """
    テクニカル分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    close = df["Close"]
    strat = config["strategy"]

    sma_short = _sma(close, strat["sma_short"])
    sma_long = _sma(close, strat["sma_long"])
    sma_trend = _sma(close, strat["sma_trend"])
    rsi = _rsi(close, strat["rsi_period"])
    macd_line, signal_line, histogram = _macd(close)
    bb_upper, bb_mid, bb_lower = _bollinger(close)

    latest = close.iloc[-1]
    prev_close = close.iloc[-2] if len(close) > 1 else latest

    metrics = {
        "price": float(latest),
        "sma_short": float(sma_short.iloc[-1]),
        "sma_long": float(sma_long.iloc[-1]),
        "sma_trend": float(sma_trend.iloc[-1]) if not np.isnan(sma_trend.iloc[-1]) else None,
        "rsi": float(rsi.iloc[-1]),
        "macd": float(macd_line.iloc[-1]),
        "macd_signal": float(signal_line.iloc[-1]),
        "macd_hist": float(histogram.iloc[-1]),
        "bb_upper": float(bb_upper.iloc[-1]),
        "bb_lower": float(bb_lower.iloc[-1]),
    }

    score = 0.0
    reasons = []

    # SMAクロス
    if metrics["sma_short"] > metrics["sma_long"]:
        score += 0.5
        reasons.append("SMAゴールデンクロス（短期 > 中期）")
    else:
        score -= 0.5
        reasons.append("SMAデッドクロス（短期 < 中期）")

    # トレンド方向
    if metrics["sma_trend"] and latest > metrics["sma_trend"]:
        score += 0.3
        reasons.append("長期上昇トレンド（価格 > SMA200）")
    elif metrics["sma_trend"]:
        score -= 0.3
        reasons.append("長期下降トレンド（価格 < SMA200）")

    # RSI
    rsi_val = metrics["rsi"]
    if rsi_val < 30:
        score += 0.5
        reasons.append(f"RSI売られすぎ（{rsi_val:.1f}）")
    elif rsi_val < 50:
        score += 0.2
        reasons.append(f"RSI低め（{rsi_val:.1f}）")
    elif rsi_val > 75:
        score -= 0.5
        reasons.append(f"RSI過熱（{rsi_val:.1f}）")
    elif rsi_val > 70:
        score -= 0.3
        reasons.append(f"RSI高め（{rsi_val:.1f}）")

    # MACD
    if metrics["macd_hist"] > 0 and histogram.iloc[-2] <= 0:
        score += 0.4
        reasons.append("MACDゴールデンクロス")
    elif metrics["macd_hist"] < 0 and histogram.iloc[-2] >= 0:
        score -= 0.4
        reasons.append("MACDデッドクロス")
    elif metrics["macd_hist"] > 0:
        score += 0.1
        reasons.append("MACDプラス圏")
    else:
        score -= 0.1
        reasons.append("MACDマイナス圏")

    # ボリンジャーバンド
    if latest <= metrics["bb_lower"]:
        score += 0.3
        reasons.append("ボリンジャー下限タッチ（反発期待）")
    elif latest >= metrics["bb_upper"]:
        score -= 0.3
        reasons.append("ボリンジャー上限タッチ（反落注意）")

    score = max(-2.0, min(2.0, score))
    confidence = min(100, int(abs(score) / 2.0 * 100))

    return {
        "agent": "テクニカル",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
