import pandas as pd
import numpy as np


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    """単純移動平均を計算する。"""
    return prices.rolling(window=period).mean()


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """RSIを計算する（Wilderの平滑化）。"""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def generate_signal(df: pd.DataFrame, config: dict) -> dict:
    """
    株価DataFrameと戦略設定からシグナルを生成する。

    Returns:
        dict: signal ("BUY"/"SELL"/"HOLD"), rsi, sma_short, sma_long, sma_trend, reason
    """
    strat = config["strategy"]
    close = df["Close"]

    sma_short = calculate_sma(close, strat["sma_short"])
    sma_long = calculate_sma(close, strat["sma_long"])
    sma_trend = calculate_sma(close, strat["sma_trend"])
    rsi = calculate_rsi(close, strat["rsi_period"])

    latest_close = close.iloc[-1]
    latest_sma_short = sma_short.iloc[-1]
    latest_sma_long = sma_long.iloc[-1]
    latest_sma_trend = sma_trend.iloc[-1]
    latest_rsi = rsi.iloc[-1]

    result = {
        "price": float(latest_close),
        "rsi": float(latest_rsi),
        "sma_short": float(latest_sma_short),
        "sma_long": float(latest_sma_long),
        "sma_trend": float(latest_sma_trend),
        "signal": "HOLD",
        "reason": "条件未達（様子見）",
    }

    if np.isnan(latest_sma_trend) or np.isnan(latest_rsi):
        result["reason"] = "データ不足（様子見）"
        return result

    # 買いシグナル
    if (
        latest_sma_short > latest_sma_long
        and latest_rsi < strat["rsi_overbought"]
        and latest_close > latest_sma_trend
    ):
        result["signal"] = "BUY"
        result["reason"] = "ゴールデンクロス（SMA25 > SMA75）+ RSI適正 + 上昇トレンド"

    # 売りシグナル
    elif latest_sma_short < latest_sma_long or latest_rsi > 75:
        reasons = []
        if latest_sma_short < latest_sma_long:
            reasons.append("デッドクロス（SMA25 < SMA75）")
        if latest_rsi > 75:
            reasons.append(f"RSI過熱（{latest_rsi:.1f}）")
        result["signal"] = "SELL"
        result["reason"] = " + ".join(reasons)

    return result
