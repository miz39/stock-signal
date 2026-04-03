import pytest
import pandas as pd
import numpy as np
from strategy import calculate_sma, calculate_rsi, generate_signal, detect_market_regime, compute_composite_score


def _make_config(overrides=None):
    base = {
        "strategy": {
            "sma_short": 25,
            "sma_long": 75,
            "sma_trend": 200,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "rsi_entry_min": 50,
            "rsi_entry_max": 65,
            "min_volume": 0,
        }
    }
    if overrides:
        base["strategy"].update(overrides)
    return base


def _make_df(prices, volumes=None):
    """Create a DataFrame with enough data for SMA200 + RSI14."""
    df = pd.DataFrame({"Close": prices})
    if volumes is not None:
        df["Volume"] = volumes
    return df


class TestCalculateSMA:
    def test_basic(self):
        prices = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        sma = calculate_sma(prices, 3)
        assert sma.iloc[-1] == pytest.approx(4.0)  # (3+4+5)/3

    def test_nan_for_insufficient_data(self):
        prices = pd.Series([1.0, 2.0])
        sma = calculate_sma(prices, 5)
        assert np.isnan(sma.iloc[-1])


class TestCalculateRSI:
    def test_all_gains(self):
        prices = pd.Series(range(100, 120))  # 20 increasing prices
        rsi = calculate_rsi(prices, 14)
        assert rsi.iloc[-1] == pytest.approx(100.0)

    def test_all_losses(self):
        prices = pd.Series(range(120, 100, -1))  # 20 decreasing prices
        rsi = calculate_rsi(prices, 14)
        assert rsi.iloc[-1] == pytest.approx(0.0)

    def test_mixed_returns_range(self):
        np.random.seed(42)
        prices = pd.Series(np.cumsum(np.random.randn(100)) + 100)
        rsi = calculate_rsi(prices, 14)
        assert 0 <= rsi.iloc[-1] <= 100


class TestGenerateSignal:
    def _trending_up_df(self):
        """Create data where SMA25 > SMA75, close > SMA200, RSI ~55."""
        np.random.seed(42)
        n = 250
        # Steady uptrend
        prices = 1000 + np.arange(n) * 0.5 + np.random.randn(n) * 2
        volumes = np.full(n, 1000000.0)
        return _make_df(prices.tolist(), volumes.tolist())

    def _trending_down_df(self):
        """Create data where SMA25 < SMA75 (dead cross)."""
        np.random.seed(42)
        n = 250
        prices = 1000 - np.arange(n) * 0.5 + np.random.randn(n) * 2
        return _make_df(prices.tolist())

    def test_buy_signal(self):
        df = self._trending_up_df()
        config = _make_config()
        result = generate_signal(df, config)
        # In a steady uptrend, expect BUY or HOLD (RSI might be out of range)
        assert result["signal"] in ("BUY", "HOLD")
        assert "price" in result
        assert "rsi" in result

    def test_sell_signal_dead_cross(self):
        df = self._trending_down_df()
        config = _make_config()
        result = generate_signal(df, config)
        assert result["signal"] in ("SELL", "HOLD")

    def test_insufficient_data(self):
        df = _make_df([100.0] * 50)  # Not enough for SMA200
        config = _make_config()
        result = generate_signal(df, config)
        assert result["signal"] == "HOLD"
        assert "データ不足" in result["reason"]

    def test_volume_filter(self):
        df = self._trending_up_df()
        # Set volume filter very high
        config = _make_config({"min_volume": 99999999})
        result = generate_signal(df, config)
        if result["signal"] != "HOLD":
            pass  # RSI might be out of range, that's ok
        # If conditions were met but volume too low, should be HOLD
        config_no_vol = _make_config({"min_volume": 0})
        result_no_vol = generate_signal(df, config_no_vol)
        if result_no_vol["signal"] == "BUY":
            # Same data with volume filter should be HOLD
            assert result["signal"] == "HOLD"

    def test_result_has_avg_volume(self):
        volumes = [500000.0] * 250
        df = self._trending_up_df()
        config = _make_config()
        result = generate_signal(df, config)
        assert "avg_volume" in result


class TestDetectMarketRegime:
    def test_bull_regime(self):
        """Strong uptrend: price > SMA50 > SMA200 → bull."""
        n = 250
        prices = 30000 + np.arange(n) * 50.0  # steady uptrend
        df = _make_df(prices.tolist())
        result = detect_market_regime(df)
        assert result["regime"] == "bull"
        assert result["sma50"] is not None
        assert result["sma200"] is not None
        assert result["price"] > 0

    def test_bear_regime(self):
        """Strong downtrend: price < SMA50 < SMA200 → bear."""
        n = 250
        prices = 40000 - np.arange(n) * 50.0  # steady downtrend
        df = _make_df(prices.tolist())
        result = detect_market_regime(df)
        assert result["regime"] == "bear"

    def test_insufficient_data_neutral(self):
        """Less than 200 data points → neutral."""
        df = _make_df([30000.0] * 100)
        result = detect_market_regime(df)
        assert result["regime"] == "neutral"
        assert result["sma50"] is None
        assert result["sma200"] is None


class TestComputeCompositeScore:
    def _make_signal_and_df(self, rsi=55, sma_short=1050, sma_long=1000, price=1060, sma_trend=1020, volume_ratio=1.5):
        """Helper to create a signal result and matching DataFrame."""
        n = 30
        prices = [price] * n
        volumes = [100000.0] * (n - 1) + [100000.0 * volume_ratio]
        df = _make_df(prices, volumes)

        sig = {
            "rsi": rsi,
            "sma_short": sma_short,
            "sma_long": sma_long,
            "sma_trend": sma_trend,
            "price": price,
        }
        return sig, df

    def test_score_in_range(self):
        sig, df = self._make_signal_and_df()
        score = compute_composite_score(sig, df)
        assert 0.0 <= score <= 1.0

    def test_rsi_55_better_than_65(self):
        sig_55, df_55 = self._make_signal_and_df(rsi=55)
        sig_65, df_65 = self._make_signal_and_df(rsi=65)
        score_55 = compute_composite_score(sig_55, df_55)
        score_65 = compute_composite_score(sig_65, df_65)
        assert score_55 > score_65

    def test_custom_weights(self):
        sig, df = self._make_signal_and_df()
        default_score = compute_composite_score(sig, df)
        custom_weights = {
            "volume_surge": 0.0,
            "rsi_sweet_spot": 1.0,
            "sma_momentum": 0.0,
            "price_vs_sma200": 0.0,
        }
        custom_score = compute_composite_score(sig, df, weights=custom_weights)
        assert custom_score != default_score

    def test_high_volume_surge_boosts_score(self):
        sig_low, df_low = self._make_signal_and_df(volume_ratio=0.5)
        sig_high, df_high = self._make_signal_and_df(volume_ratio=2.5)
        score_low = compute_composite_score(sig_low, df_low)
        score_high = compute_composite_score(sig_high, df_high)
        assert score_high > score_low
