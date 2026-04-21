import logging
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger("signal")


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


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX（Average Directional Index）を計算する。Wilder's smoothing使用。"""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    # True Range
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # +DM / -DM
    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    # Wilder's smoothing (EMA with alpha=1/period)
    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr

    # DX → ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    return adx


def calculate_ichimoku(df: pd.DataFrame, tenkan_period: int = 9, kijun_period: int = 26,
                       senkou_b_period: int = 52) -> dict:
    """一目均衡表を計算する。"""
    high = df["High"]
    low = df["Low"]

    tenkan = (high.rolling(window=tenkan_period).max() + low.rolling(window=tenkan_period).min()) / 2
    kijun = (high.rolling(window=kijun_period).max() + low.rolling(window=kijun_period).min()) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (high.rolling(window=senkou_b_period).max() + low.rolling(window=senkou_b_period).min()) / 2

    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b}


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

    # SMA25 slope check
    slope_days = strat.get("slope_days", 5)
    if len(sma_short) > slope_days:
        sma_cur = float(sma_short.iloc[-1])
        sma_prev = float(sma_short.iloc[-1 - slope_days])
        if sma_prev > 0 and not np.isnan(sma_cur) and not np.isnan(sma_prev):
            sma_slope = (sma_cur - sma_prev) / sma_prev
        else:
            sma_slope = 0.0
    else:
        sma_slope = 0.0
    result["sma_slope"] = round(sma_slope * 100, 2)

    # ADX calculation
    adx_period = strat.get("adx_period", 14)
    adx = calculate_adx(df, adx_period)
    latest_adx = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 0.0
    result["adx"] = round(latest_adx, 1)

    # Ichimoku calculation
    ichimoku = calculate_ichimoku(
        df,
        tenkan_period=strat.get("ichimoku_tenkan", 9),
        kijun_period=strat.get("ichimoku_kijun", 26),
        senkou_b_period=strat.get("ichimoku_senkou_b", 52),
    )
    latest_tenkan = float(ichimoku["tenkan"].iloc[-1]) if not np.isnan(ichimoku["tenkan"].iloc[-1]) else 0.0
    latest_kijun = float(ichimoku["kijun"].iloc[-1]) if not np.isnan(ichimoku["kijun"].iloc[-1]) else 0.0
    senkou_a_val = float(ichimoku["senkou_a"].iloc[-1]) if not np.isnan(ichimoku["senkou_a"].iloc[-1]) else 0.0
    senkou_b_val = float(ichimoku["senkou_b"].iloc[-1]) if not np.isnan(ichimoku["senkou_b"].iloc[-1]) else 0.0
    cloud_top = max(senkou_a_val, senkou_b_val)
    result["ichimoku_bullish"] = bool(latest_tenkan > latest_kijun and latest_close > cloud_top)
    result["ichimoku_tenkan_above_kijun"] = bool(latest_tenkan > latest_kijun)

    # 買いシグナル
    rsi_entry_min = strat.get("rsi_entry_min", 50)
    rsi_entry_max = strat.get("rsi_entry_max", 65)
    min_volume = strat.get("min_volume", 0)
    if (
        latest_sma_short > latest_sma_long
        and rsi_entry_min <= latest_rsi <= rsi_entry_max
        and latest_close > latest_sma_trend
    ):
        adx_threshold = strat.get("adx_threshold", 25)
        if min_volume > 0 and avg_volume is not None and avg_volume < min_volume:
            result["signal"] = "HOLD"
            result["reason"] = f"出来高不足（20日平均 {avg_volume:,.0f}株 < 基準 {min_volume:,.0f}株）"
        elif sma_slope < -0.005:
            result["signal"] = "HOLD"
            result["reason"] = f"SMA{strat['sma_short']}下降中（傾き {sma_slope*100:+.2f}%、様子見）"
        elif latest_adx < adx_threshold:
            result["signal"] = "HOLD"
            result["reason"] = f"トレンド弱（ADX {latest_adx:.1f} < {adx_threshold}、様子見）"
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


def detect_coch(df: pd.DataFrame, lookback: int = 3) -> dict:
    """Change of Character（トレンド構造崩壊）を検出する。

    swing high/low を lookback バー両側で確認し、
    上昇トレンド中に直近の swing low を下回ったら bearish CoCh、
    下降トレンド中に直近の swing high を上回ったら bullish CoCh。

    Returns:
        dict: {"triggered": bool, "type": "bearish"/"bullish"/"none", "level": float}
    """
    result = {"triggered": False, "type": "none", "level": 0.0}

    high = df["High"].values
    low = df["Low"].values
    close = df["Close"].values
    n = len(high)

    if n < lookback * 2 + 2:
        return result

    # Find swing highs and swing lows
    swing_highs = []
    swing_lows = []
    for i in range(lookback, n - lookback):
        if all(high[i] >= high[i - j] for j in range(1, lookback + 1)) and \
           all(high[i] >= high[i + j] for j in range(1, lookback + 1)):
            swing_highs.append((i, high[i]))
        if all(low[i] <= low[i - j] for j in range(1, lookback + 1)) and \
           all(low[i] <= low[i + j] for j in range(1, lookback + 1)):
            swing_lows.append((i, low[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return result

    latest_close = close[-1]

    # Uptrend: swing highs and swing lows are rising
    last_two_lows = swing_lows[-2:]
    last_two_highs = swing_highs[-2:]

    if last_two_highs[-1][1] > last_two_highs[-2][1] and last_two_lows[-1][1] > last_two_lows[-2][1]:
        # Uptrend — check for bearish CoCh (close below last swing low)
        last_swing_low = last_two_lows[-1][1]
        if latest_close < last_swing_low:
            result["triggered"] = True
            result["type"] = "bearish"
            result["level"] = float(last_swing_low)
            return result

    if last_two_highs[-1][1] < last_two_highs[-2][1] and last_two_lows[-1][1] < last_two_lows[-2][1]:
        # Downtrend — check for bullish CoCh (close above last swing high)
        last_swing_high = last_two_highs[-1][1]
        if latest_close > last_swing_high:
            result["triggered"] = True
            result["type"] = "bullish"
            result["level"] = float(last_swing_high)
            return result

    return result


def detect_market_crash(nikkei_df: pd.DataFrame,
                        warning_pct: float = -3.0,
                        critical_pct: float = -5.0) -> dict:
    """日経225の前日終値比で急落を検出する。

    Args:
        nikkei_df: 日経225の価格データ（Closeカラム必須、最低2日分）。
        warning_pct: warning 閾値（%）。デフォルト -3.0。
        critical_pct: critical 閾値（%）。デフォルト -5.0（エントリー停止）。

    Returns:
        {
            "triggered": bool,       # warning 以下まで下落した場合 True
            "daily_pct": float,      # 前日終値比（%）。データ不足時は 0.0
            "severity": str | None,  # "critical" / "warning" / None
        }
    """
    result = {"triggered": False, "daily_pct": 0.0, "severity": None}
    close = nikkei_df.get("Close") if nikkei_df is not None else None
    if close is None or len(close) < 2:
        return result

    prev = float(close.iloc[-2])
    curr = float(close.iloc[-1])
    if prev <= 0 or np.isnan(prev) or np.isnan(curr):
        return result

    daily_pct = (curr - prev) / prev * 100
    result["daily_pct"] = round(daily_pct, 2)

    if daily_pct <= critical_pct:
        result["triggered"] = True
        result["severity"] = "critical"
    elif daily_pct <= warning_pct:
        result["triggered"] = True
        result["severity"] = "warning"

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

    if np.isnan(latest_close) or np.isnan(latest_sma50) or np.isnan(latest_sma200):
        regime = "unknown"
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


def fetch_tv_recommendation(ticker: str) -> Optional[float]:
    """TradingView TAから推奨スコアを取得（0.0〜1.0）。失敗時はNone。"""
    try:
        from tradingview_ta import TA_Handler, Interval

        symbol = ticker.replace(".T", "")
        handler = TA_Handler(
            symbol=symbol,
            screener="japan",
            exchange="TSE",
            interval=Interval.INTERVAL_1_DAY,
        )
        analysis = handler.get_analysis()
        buy_count = analysis.summary.get("BUY", 0)
        return round(buy_count / 26.0, 4)
    except Exception as e:
        logger.warning(f"TradingView TA取得失敗 ({ticker}): {e}")
        return None


def compute_composite_score(signal_result: dict, df: pd.DataFrame, weights: dict = None,
                            slope_days: int = 5, slope_blend: float = 0.3,
                            tv_score: Optional[float] = None) -> float:
    """BUY候補の複合スコアを計算する（0.0〜1.0）。

    5要素:
    - volume_surge: 直近出来高 / 20日平均出来高（上限3.0で正規化）
    - rsi_sweet_spot: RSI 55からの距離（近いほど高スコア）
    - sma_momentum: SMA乖離率 × (1-slope_blend) + SMA25傾き × slope_blend
    - price_vs_sma200: SMA200乖離率（0〜5%が最高、15%超で0）
    - tv_recommendation: TradingView TA推奨スコア（tv_scoreがNoneの場合は4要素にフォールバック）
    """
    if weights is None:
        weights = {
            "volume_surge": 0.25,
            "rsi_sweet_spot": 0.15,
            "sma_momentum": 0.30,
            "price_vs_sma200": 0.10,
            "tv_recommendation": 0.20,
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

    # sma_momentum (divergence + SMA25 slope blend)
    sma_short_val = signal_result.get("sma_short", 0)
    sma_long_val = signal_result.get("sma_long", 0)
    if sma_long_val > 0 and not np.isnan(sma_short_val) and not np.isnan(sma_long_val):
        momentum = (sma_short_val - sma_long_val) / sma_long_val
        base_sma_score = min(max(momentum / 0.10, 0.0), 1.0)
    else:
        base_sma_score = 0.0

    slope_score = 0.5
    sma_short_period = 25
    if len(close) > slope_days + sma_short_period:
        sma25 = calculate_sma(close, sma_short_period)
        sma_cur = float(sma25.iloc[-1])
        sma_prev = float(sma25.iloc[-1 - slope_days])
        if sma_prev > 0 and not np.isnan(sma_cur) and not np.isnan(sma_prev):
            slope_pct = (sma_cur - sma_prev) / sma_prev
            slope_score = min(max(slope_pct / 0.02, 0.0), 1.0)

    sma_score = (1 - slope_blend) * base_sma_score + slope_blend * slope_score

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

    # ichimoku_bullish component
    ichimoku_score = 0.0
    if signal_result.get("ichimoku_bullish"):
        ichimoku_score = 1.0
    elif signal_result.get("ichimoku_tenkan_above_kijun"):
        ichimoku_score = 0.5

    # trend_strength component (ADX normalized: 0-50 mapped to 0.0-1.0)
    adx_val = signal_result.get("adx", 0)
    trend_strength_score = min(max(adx_val / 50.0, 0.0), 1.0)

    # Build component scores dict
    components = {
        "volume_surge": vol_score,
        "rsi_sweet_spot": rsi_score,
        "sma_momentum": sma_score,
        "price_vs_sma200": price_score,
        "ichimoku_bullish": ichimoku_score,
        "trend_strength": trend_strength_score,
    }

    # Add tv_recommendation if available
    if tv_score is not None and "tv_recommendation" in weights:
        components["tv_recommendation"] = tv_score

    # Calculate weighted score (re-normalize if some components are missing from weights)
    active_weights = {k: weights.get(k, 0) for k in components}
    total_weight = sum(active_weights.values())
    if total_weight > 0:
        score = sum(active_weights[k] / total_weight * components[k] for k in components)
    else:
        score = 0.0

    return round(min(max(score, 0.0), 1.0), 4)
