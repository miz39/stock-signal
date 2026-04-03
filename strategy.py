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

    # 出来高（20日平均）
    avg_volume = None
    if "Volume" in df.columns:
        avg_volume = float(df["Volume"].rolling(window=20).mean().iloc[-1])
    result["avg_volume"] = avg_volume

    # 買いシグナル
    rsi_entry_min = strat.get("rsi_entry_min", 50)
    rsi_entry_max = strat.get("rsi_entry_max", 65)
    min_volume = strat.get("min_volume", 0)
    if (
        latest_sma_short > latest_sma_long
        and rsi_entry_min <= latest_rsi <= rsi_entry_max
        and latest_close > latest_sma_trend
    ):
        if min_volume > 0 and avg_volume is not None and avg_volume < min_volume:
            result["signal"] = "HOLD"
            result["reason"] = f"出来高不足（20日平均 {avg_volume:,.0f}株 < 基準 {min_volume:,.0f}株）"
        else:
            result["signal"] = "BUY"
            result["reason"] = f"ゴールデンクロス（SMA{strat['sma_short']} > SMA{strat['sma_long']}）+ RSI適正（{latest_rsi:.1f}）+ 上昇トレンド"

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


def detect_market_regime(nikkei_df: pd.DataFrame) -> dict:
    """日経225のSMA50/SMA200で相場環境を判定する。

    Returns:
        dict: regime ("bull"/"bear"/"neutral"), sma50, sma200, price
    """
    close = nikkei_df["Close"]

    if len(close) < 200:
        return {"regime": "neutral", "sma50": None, "sma200": None, "price": float(close.iloc[-1]) if len(close) > 0 else 0}

    sma50 = calculate_sma(close, 50)
    sma200 = calculate_sma(close, 200)

    latest_close = float(close.iloc[-1])
    latest_sma50 = float(sma50.iloc[-1])
    latest_sma200 = float(sma200.iloc[-1])

    if np.isnan(latest_sma50) or np.isnan(latest_sma200):
        regime = "neutral"
    elif latest_close > latest_sma50 > latest_sma200:
        regime = "bull"
    elif latest_close < latest_sma50 < latest_sma200:
        regime = "bear"
    else:
        regime = "neutral"

    return {
        "regime": regime,
        "sma50": round(latest_sma50, 1) if not np.isnan(latest_sma50) else None,
        "sma200": round(latest_sma200, 1) if not np.isnan(latest_sma200) else None,
        "price": round(latest_close, 1),
    }


def compute_composite_score(signal_result: dict, df: pd.DataFrame, weights: dict = None) -> float:
    """BUY候補の複合スコアを計算する（0.0〜1.0）。

    4要素:
    - volume_surge: 直近出来高 / 20日平均出来高（上限3.0で正規化）
    - rsi_sweet_spot: RSI 55からの距離（近いほど高スコア）
    - sma_momentum: (SMA25 - SMA75) / SMA75（上限10%で正規化）
    - price_vs_sma200: SMA200乖離率（0〜5%が最高、15%超で0）
    """
    if weights is None:
        weights = {
            "volume_surge": 0.25,
            "rsi_sweet_spot": 0.25,
            "sma_momentum": 0.30,
            "price_vs_sma200": 0.20,
        }

    close = df["Close"]

    # volume_surge
    vol_score = 0.0
    if "Volume" in df.columns:
        avg_vol = df["Volume"].rolling(window=20).mean().iloc[-1]
        if avg_vol > 0 and not np.isnan(avg_vol):
            ratio = float(df["Volume"].iloc[-1]) / float(avg_vol)
            vol_score = min(ratio / 3.0, 1.0)

    # rsi_sweet_spot (RSI 55 = ideal, distance 0-25 mapped to 1.0-0.0)
    rsi = signal_result.get("rsi", 55)
    rsi_dist = abs(rsi - 55)
    rsi_score = max(1.0 - rsi_dist / 25.0, 0.0)

    # sma_momentum
    sma_short_val = signal_result.get("sma_short", 0)
    sma_long_val = signal_result.get("sma_long", 0)
    if sma_long_val > 0 and not np.isnan(sma_short_val) and not np.isnan(sma_long_val):
        momentum = (sma_short_val - sma_long_val) / sma_long_val
        sma_score = min(max(momentum / 0.10, 0.0), 1.0)
    else:
        sma_score = 0.0

    # price_vs_sma200
    sma_trend_val = signal_result.get("sma_trend", 0)
    price = signal_result.get("price", 0)
    if sma_trend_val > 0 and not np.isnan(sma_trend_val):
        deviation = (price - sma_trend_val) / sma_trend_val
        if deviation < 0:
            price_score = 0.0
        elif deviation <= 0.05:
            price_score = 1.0
        elif deviation <= 0.15:
            price_score = 1.0 - (deviation - 0.05) / 0.10
        else:
            price_score = 0.0
    else:
        price_score = 0.0

    score = (
        weights.get("volume_surge", 0.25) * vol_score
        + weights.get("rsi_sweet_spot", 0.25) * rsi_score
        + weights.get("sma_momentum", 0.30) * sma_score
        + weights.get("price_vs_sma200", 0.20) * price_score
    )

    return round(min(max(score, 0.0), 1.0), 4)
