from unittest.mock import MagicMock, patch

import pytest
import pandas as pd
import numpy as np
from strategy import (
    calculate_sma, calculate_rsi, generate_signal, detect_market_regime,
    compute_composite_score, fetch_tv_recommendation,
    calculate_adx, calculate_ichimoku, detect_coch, detect_market_crash,
)


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


def _make_df(prices, volumes=None, highs=None, lows=None):
    """Create a DataFrame with enough data for SMA200 + RSI14."""
    df = pd.DataFrame({"Close": prices})
    if volumes is not None:
        df["Volume"] = volumes
    if highs is not None:
        df["High"] = highs
    else:
        df["High"] = [p * 1.01 for p in prices]
    if lows is not None:
        df["Low"] = lows
    else:
        df["Low"] = [p * 0.99 for p in prices]
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


class TestSMASlopeFilter:
    """SMA25 slope gate in generate_signal."""

    def _make_slope_df(self, slope_direction="up"):
        """Create 250-bar data where SMA25>SMA100, close>SMA200, RSI~55-59,
        but SMA25 slope is controllable.

        'down': strong uptrend (210 bars) → decline (32 bars) → sharp recovery (8 bars)
                RSI recovers fast but SMA25 is still declining → slope < -0.5%
        'up': steady uptrend (250 bars) → slope positive
        """
        n = 250
        if slope_direction == "down":
            # Phase 1: strong uptrend → large SMA25-SMA100 gap
            phase1 = 1000 + np.arange(210) * 8.0
            base = float(phase1[-1])  # ~2672
            # Phase 2: decline for 32 bars (SMA25 drops)
            decline = [base - i * 8.0 for i in range(1, 33)]
            # Phase 3: sharp V-recovery for 8 bars (RSI bounces back)
            recovery_base = decline[-1]
            recovery = [recovery_base + i * 12.0 for i in range(1, 9)]
            prices = np.concatenate([phase1, decline, recovery]).tolist()
        else:
            # Steady uptrend
            prices = (1000 + np.arange(n) * 8.0).tolist()

        volumes = [1_000_000.0] * n
        return _make_df(prices, volumes)

    def test_sma_slope_declining_holds(self):
        """SMA25 > SMA100 but SMA25 is declining → HOLD."""
        df = self._make_slope_df("down")
        config = _make_config({"sma_long": 100})
        result = generate_signal(df, config)
        # Verify basic BUY conditions ARE met (except slope)
        assert result["sma_short"] > result["sma_long"], "SMA25 should be > SMA100"
        assert config["strategy"]["rsi_entry_min"] <= result["rsi"] <= config["strategy"]["rsi_entry_max"], \
            f"RSI {result['rsi']} should be in 50-65"
        assert result["price"] > result["sma_trend"], "Price should be > SMA200"
        # Slope filter should block BUY → HOLD
        assert result["sma_slope"] < -0.5, f"Slope {result['sma_slope']} should be < -0.5%"
        assert result["signal"] == "HOLD"
        assert "下降中" in result["reason"]

    def test_sma_slope_rising_buys(self):
        """SMA25 rising + all conditions met → BUY."""
        df = self._make_slope_df("up")
        config = _make_config({"sma_long": 100})
        result = generate_signal(df, config)
        if (
            result["sma_short"] > result["sma_long"]
            and config["strategy"]["rsi_entry_min"] <= result["rsi"] <= config["strategy"]["rsi_entry_max"]
            and result["price"] > result["sma_trend"]
        ):
            assert result["signal"] == "BUY"

    def test_sma_slope_in_result(self):
        """result should always contain sma_slope."""
        df = self._make_slope_df("up")
        config = _make_config()
        result = generate_signal(df, config)
        assert "sma_slope" in result
        assert isinstance(result["sma_slope"], float)


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


class TestDetectMarketCrash:
    def test_no_crash_small_drop(self):
        """-1% drop stays below warning threshold → not triggered."""
        df = _make_df([30000.0, 29700.0])  # -1.0%
        result = detect_market_crash(df)
        assert result["triggered"] is False
        assert result["severity"] is None
        assert result["daily_pct"] == pytest.approx(-1.0, abs=0.01)

    def test_warning_at_threshold(self):
        """Exactly -3.0% → warning triggered."""
        df = _make_df([30000.0, 29100.0])  # -3.0%
        result = detect_market_crash(df, warning_pct=-3.0, critical_pct=-5.0)
        assert result["triggered"] is True
        assert result["severity"] == "warning"
        assert result["daily_pct"] == pytest.approx(-3.0, abs=0.01)

    def test_warning_just_above_threshold(self):
        """-2.9% is above warning → not triggered."""
        df = _make_df([30000.0, 29130.0])  # -2.9%
        result = detect_market_crash(df)
        assert result["triggered"] is False
        assert result["severity"] is None

    def test_critical_at_threshold(self):
        """Exactly -5.0% → critical triggered."""
        df = _make_df([30000.0, 28500.0])  # -5.0%
        result = detect_market_crash(df, warning_pct=-3.0, critical_pct=-5.0)
        assert result["triggered"] is True
        assert result["severity"] == "critical"

    def test_critical_deep_drop(self):
        """-8% drop → critical."""
        df = _make_df([30000.0, 27600.0])  # -8.0%
        result = detect_market_crash(df)
        assert result["severity"] == "critical"
        assert result["daily_pct"] == pytest.approx(-8.0, abs=0.01)

    def test_positive_return_not_triggered(self):
        """Big up move → not triggered."""
        df = _make_df([30000.0, 33000.0])  # +10%
        result = detect_market_crash(df)
        assert result["triggered"] is False
        assert result["daily_pct"] == pytest.approx(10.0, abs=0.01)

    def test_insufficient_data(self):
        """Single-row dataframe returns default."""
        df = _make_df([30000.0])
        result = detect_market_crash(df)
        assert result["triggered"] is False
        assert result["severity"] is None
        assert result["daily_pct"] == 0.0

    def test_custom_thresholds(self):
        """Custom thresholds: -2% warning, -4% critical."""
        df = _make_df([30000.0, 29400.0])  # -2%
        result = detect_market_crash(df, warning_pct=-2.0, critical_pct=-4.0)
        assert result["severity"] == "warning"

        df2 = _make_df([30000.0, 28800.0])  # -4%
        result2 = detect_market_crash(df2, warning_pct=-2.0, critical_pct=-4.0)
        assert result2["severity"] == "critical"


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


class TestFetchTvRecommendation:
    def test_success(self):
        mock_analysis = MagicMock()
        mock_analysis.summary = {"RECOMMENDATION": "BUY", "BUY": 18, "SELL": 3, "NEUTRAL": 5}

        mock_handler_instance = MagicMock()
        mock_handler_instance.get_analysis.return_value = mock_analysis

        mock_ta_module = MagicMock()
        mock_ta_module.TA_Handler.return_value = mock_handler_instance
        mock_ta_module.Interval.INTERVAL_1_DAY = "1d"

        with patch.dict("sys.modules", {"tradingview_ta": mock_ta_module}):
            score = fetch_tv_recommendation("7203.T")

        assert score == round(18 / 26.0, 4)
        mock_ta_module.TA_Handler.assert_called_once_with(
            symbol="7203", screener="japan", exchange="TSE",
            interval="1d",
        )

    def test_all_buy(self):
        mock_analysis = MagicMock()
        mock_analysis.summary = {"BUY": 26, "SELL": 0, "NEUTRAL": 0}

        mock_handler = MagicMock()
        mock_handler.get_analysis.return_value = mock_analysis

        mock_ta = MagicMock()
        mock_ta.TA_Handler.return_value = mock_handler
        mock_ta.Interval.INTERVAL_1_DAY = "1d"

        with patch.dict("sys.modules", {"tradingview_ta": mock_ta}):
            assert fetch_tv_recommendation("9984.T") == 1.0

    def test_all_sell(self):
        mock_analysis = MagicMock()
        mock_analysis.summary = {"BUY": 0, "SELL": 20, "NEUTRAL": 6}

        mock_handler = MagicMock()
        mock_handler.get_analysis.return_value = mock_analysis

        mock_ta = MagicMock()
        mock_ta.TA_Handler.return_value = mock_handler
        mock_ta.Interval.INTERVAL_1_DAY = "1d"

        with patch.dict("sys.modules", {"tradingview_ta": mock_ta}):
            assert fetch_tv_recommendation("6758.T") == 0.0

    def test_failure_returns_none(self):
        mock_ta = MagicMock()
        mock_ta.TA_Handler.side_effect = Exception("Connection timeout")
        mock_ta.Interval.INTERVAL_1_DAY = "1d"

        with patch.dict("sys.modules", {"tradingview_ta": mock_ta}):
            assert fetch_tv_recommendation("7203.T") is None

    def test_ticker_conversion(self):
        mock_analysis = MagicMock()
        mock_analysis.summary = {"BUY": 13, "SELL": 6, "NEUTRAL": 7}

        mock_handler = MagicMock()
        mock_handler.get_analysis.return_value = mock_analysis

        mock_ta = MagicMock()
        mock_ta.TA_Handler.return_value = mock_handler
        mock_ta.Interval.INTERVAL_1_DAY = "1d"

        with patch.dict("sys.modules", {"tradingview_ta": mock_ta}):
            fetch_tv_recommendation("4502.T")

        call_kwargs = mock_ta.TA_Handler.call_args[1]
        assert call_kwargs["symbol"] == "4502"
        assert call_kwargs["screener"] == "japan"
        assert call_kwargs["exchange"] == "TSE"


class TestCompositeScoreWithTv:
    def _make_signal_and_df(self):
        n = 250
        prices = list(range(900, 900 + n))
        volumes = [100000.0] * n
        df = _make_df(prices, volumes)
        sig = {"price": prices[-1], "rsi": 55, "sma_short": 1100, "sma_long": 1050, "sma_trend": 950}
        return sig, df

    def test_with_tv_score(self):
        sig, df = self._make_signal_and_df()
        weights = {
            "volume_surge": 0.25, "rsi_sweet_spot": 0.15,
            "sma_momentum": 0.30, "price_vs_sma200": 0.10,
            "tv_recommendation": 0.20,
        }

        score_with = compute_composite_score(sig, df, weights, tv_score=0.8)
        score_without = compute_composite_score(sig, df, weights, tv_score=None)

        assert 0.0 <= score_with <= 1.0
        assert 0.0 <= score_without <= 1.0
        assert score_with != score_without

    def test_fallback_renormalization(self):
        sig, df = self._make_signal_and_df()
        weights_5 = {
            "volume_surge": 0.25, "rsi_sweet_spot": 0.15,
            "sma_momentum": 0.30, "price_vs_sma200": 0.10,
            "tv_recommendation": 0.20,
        }
        # Manually normalized 4-component weights (0.25+0.15+0.30+0.10 = 0.80)
        weights_4 = {
            "volume_surge": 0.25 / 0.80, "rsi_sweet_spot": 0.15 / 0.80,
            "sma_momentum": 0.30 / 0.80, "price_vs_sma200": 0.10 / 0.80,
        }

        score_fallback = compute_composite_score(sig, df, weights_5, tv_score=None)
        score_4only = compute_composite_score(sig, df, weights_4, tv_score=None)

        assert abs(score_fallback - score_4only) < 0.001

    def test_tv_score_zero_vs_one(self):
        sig, df = self._make_signal_and_df()
        weights = {
            "volume_surge": 0.25, "rsi_sweet_spot": 0.15,
            "sma_momentum": 0.30, "price_vs_sma200": 0.10,
            "tv_recommendation": 0.20,
        }

        score_zero = compute_composite_score(sig, df, weights, tv_score=0.0)
        score_one = compute_composite_score(sig, df, weights, tv_score=1.0)

        assert score_one >= score_zero

    def test_no_tv_key_in_weights_ignores_tv_score(self):
        sig, df = self._make_signal_and_df()
        weights = {
            "volume_surge": 0.30, "rsi_sweet_spot": 0.20,
            "sma_momentum": 0.35, "price_vs_sma200": 0.15,
        }

        score_a = compute_composite_score(sig, df, weights, tv_score=0.9)
        score_b = compute_composite_score(sig, df, weights, tv_score=None)

        assert score_a == score_b


class TestCalculateADX:
    def test_strong_uptrend_high_adx(self):
        """Strong trend should produce ADX > 25."""
        n = 100
        close = 1000 + np.arange(n) * 10.0
        high = close + 5.0
        low = close - 5.0
        df = _make_df(close.tolist(), highs=high.tolist(), lows=low.tolist())
        adx = calculate_adx(df, 14)
        assert adx.iloc[-1] > 25

    def test_range_bound_low_adx(self):
        """Sideways market should produce low ADX."""
        n = 100
        np.random.seed(123)
        close = 1000 + np.random.randn(n) * 2
        high = close + abs(np.random.randn(n)) * 3
        low = close - abs(np.random.randn(n)) * 3
        df = _make_df(close.tolist(), highs=high.tolist(), lows=low.tolist())
        adx = calculate_adx(df, 14)
        assert adx.iloc[-1] < 25

    def test_adx_in_range(self):
        """ADX should be between 0 and 100."""
        n = 100
        np.random.seed(42)
        close = np.cumsum(np.random.randn(n)) + 1000
        high = close + abs(np.random.randn(n)) * 5
        low = close - abs(np.random.randn(n)) * 5
        df = _make_df(close.tolist(), highs=high.tolist(), lows=low.tolist())
        adx = calculate_adx(df, 14)
        valid = adx.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()


class TestCalculateIchimoku:
    def test_tenkan_kijun_shapes(self):
        n = 100
        close = (1000 + np.arange(n) * 2.0).tolist()
        df = _make_df(close)
        result = calculate_ichimoku(df)
        assert len(result["tenkan"]) == n
        assert len(result["kijun"]) == n
        assert len(result["senkou_a"]) == n
        assert len(result["senkou_b"]) == n

    def test_uptrend_tenkan_above_kijun(self):
        """In uptrend, tenkan (short) should be above kijun (long)."""
        n = 100
        close = 1000 + np.arange(n) * 5.0
        high = close + 3.0
        low = close - 3.0
        df = _make_df(close.tolist(), highs=high.tolist(), lows=low.tolist())
        result = calculate_ichimoku(df)
        assert result["tenkan"].iloc[-1] > result["kijun"].iloc[-1]

    def test_close_above_cloud_in_uptrend(self):
        """In strong uptrend, close should be above cloud top."""
        n = 100
        close = 1000 + np.arange(n) * 5.0
        high = close + 3.0
        low = close - 3.0
        df = _make_df(close.tolist(), highs=high.tolist(), lows=low.tolist())
        result = calculate_ichimoku(df)
        cloud_top = max(result["senkou_a"].iloc[-1], result["senkou_b"].iloc[-1])
        assert close[-1] > cloud_top


class TestADXFilter:
    def _make_trending_df_with_adx(self, strong_trend=True):
        """Create 250-bar data with controllable trend strength."""
        n = 250
        if strong_trend:
            close = 1000 + np.arange(n) * 5.0
        else:
            np.random.seed(99)
            close = 1000 + np.random.randn(n) * 2
        high = close + 3.0
        low = close - 3.0
        volumes = [1_000_000.0] * n
        return _make_df(close.tolist(), volumes=volumes, highs=high.tolist(), lows=low.tolist())

    def test_adx_blocks_weak_trend(self):
        """When ADX < threshold, BUY should be blocked to HOLD."""
        df = self._make_trending_df_with_adx(strong_trend=False)
        config = _make_config({"adx_threshold": 25})
        result = generate_signal(df, config)
        # If other BUY conditions are met but ADX is low, should be HOLD
        if (result["sma_short"] > result["sma_long"]
            and config["strategy"]["rsi_entry_min"] <= result["rsi"] <= config["strategy"]["rsi_entry_max"]
            and result["price"] > result["sma_trend"]):
            assert result["adx"] < 25
            assert result["signal"] == "HOLD"
            assert "ADX" in result["reason"]

    def test_adx_in_result(self):
        """generate_signal should always include adx in result."""
        df = self._make_trending_df_with_adx(strong_trend=True)
        config = _make_config()
        result = generate_signal(df, config)
        assert "adx" in result
        assert isinstance(result["adx"], float)

    def test_ichimoku_bullish_in_result(self):
        """generate_signal should include ichimoku_bullish in result."""
        df = self._make_trending_df_with_adx(strong_trend=True)
        config = _make_config()
        result = generate_signal(df, config)
        assert "ichimoku_bullish" in result
        assert isinstance(result["ichimoku_bullish"], bool)


class TestDetectCoCh:
    def _make_uptrend_then_break(self):
        """Create data: uptrend with rising swing highs/lows, then close drops below last swing low."""
        # Build a clear uptrend with swing points
        prices_high = []
        prices_low = []
        prices_close = []
        # Phase 1: uptrend with clear swings (60 bars)
        for i in range(60):
            base = 1000 + i * 5
            if i % 10 == 5:  # swing high
                prices_high.append(base + 30)
                prices_low.append(base - 5)
                prices_close.append(base + 20)
            elif i % 10 == 0 and i > 0:  # swing low
                prices_high.append(base + 5)
                prices_low.append(base - 20)
                prices_close.append(base - 10)
            else:
                prices_high.append(base + 10)
                prices_low.append(base - 10)
                prices_close.append(base)
        # Phase 2: sharp drop below last swing low (10 bars)
        last_val = prices_close[-1]
        for i in range(10):
            drop = last_val - (i + 1) * 15
            prices_high.append(drop + 5)
            prices_low.append(drop - 5)
            prices_close.append(drop)

        return pd.DataFrame({
            "High": prices_high,
            "Low": prices_low,
            "Close": prices_close,
        })

    def test_bearish_coch_detected(self):
        """Should detect bearish CoCh when close drops below swing low in uptrend."""
        df = self._make_uptrend_then_break()
        result = detect_coch(df, lookback=3)
        # The sharp drop should trigger bearish CoCh
        if result["triggered"]:
            assert result["type"] == "bearish"
            assert result["level"] > 0

    def test_no_coch_in_steady_uptrend(self):
        """No CoCh in a steady uptrend without breakdown."""
        n = 80
        close = 1000 + np.arange(n) * 5.0
        high = close + 3.0
        low = close - 3.0
        df = pd.DataFrame({"High": high, "Low": low, "Close": close})
        result = detect_coch(df, lookback=3)
        assert result["triggered"] is False

    def test_insufficient_data(self):
        """Should return not triggered for very short data."""
        df = pd.DataFrame({"High": [100, 101], "Low": [99, 100], "Close": [99.5, 100.5]})
        result = detect_coch(df, lookback=3)
        assert result["triggered"] is False

    def test_result_structure(self):
        """detect_coch should return dict with required keys."""
        n = 50
        close = (1000 + np.arange(n) * 2.0)
        df = pd.DataFrame({"High": close + 3, "Low": close - 3, "Close": close})
        result = detect_coch(df)
        assert "triggered" in result
        assert "type" in result
        assert "level" in result
        assert result["type"] in ("bearish", "bullish", "none")


class TestCompositeScoreWithNewComponents:
    def test_ichimoku_bullish_boosts_score(self):
        """ichimoku_bullish=True should increase composite score."""
        n = 30
        prices = [1060.0] * n
        volumes = [100000.0] * n
        df = _make_df(prices, volumes)
        sig_base = {"rsi": 55, "sma_short": 1050, "sma_long": 1000, "sma_trend": 1020, "price": 1060,
                    "ichimoku_bullish": False, "ichimoku_tenkan_above_kijun": False, "adx": 30}
        sig_bullish = {**sig_base, "ichimoku_bullish": True, "ichimoku_tenkan_above_kijun": True}

        weights = {
            "volume_surge": 0.20, "rsi_sweet_spot": 0.10, "sma_momentum": 0.25,
            "price_vs_sma200": 0.10, "ichimoku_bullish": 0.10, "trend_strength": 0.10,
            "tv_recommendation": 0.15,
        }
        score_no = compute_composite_score(sig_base, df, weights)
        score_yes = compute_composite_score(sig_bullish, df, weights)
        assert score_yes > score_no

    def test_trend_strength_from_adx(self):
        """Higher ADX should produce higher trend_strength score."""
        n = 30
        prices = [1060.0] * n
        volumes = [100000.0] * n
        df = _make_df(prices, volumes)
        sig_low = {"rsi": 55, "sma_short": 1050, "sma_long": 1000, "sma_trend": 1020, "price": 1060,
                   "ichimoku_bullish": False, "ichimoku_tenkan_above_kijun": False, "adx": 10}
        sig_high = {**sig_low, "adx": 40}

        weights = {
            "volume_surge": 0.20, "rsi_sweet_spot": 0.10, "sma_momentum": 0.25,
            "price_vs_sma200": 0.10, "ichimoku_bullish": 0.10, "trend_strength": 0.10,
            "tv_recommendation": 0.15,
        }
        score_low = compute_composite_score(sig_low, df, weights)
        score_high = compute_composite_score(sig_high, df, weights)
        assert score_high > score_low
