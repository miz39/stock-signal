"""Tests for scan_only() function — works on Python 3.9+, no MCP dependency."""

import json
import math

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

import portfolio
from main import scan_only


# ── Fixtures ──


def _make_df(prices, volumes=None, highs=None, lows=None, start_date="2025-01-01"):
    n = len(prices)
    dates = pd.date_range(start=start_date, periods=n, freq="B")
    df = pd.DataFrame(index=dates)
    df["Close"] = prices
    df["High"] = highs if highs is not None else [p * 1.01 for p in prices]
    df["Low"] = lows if lows is not None else [p * 0.99 for p in prices]
    df["Open"] = [p * 1.005 for p in prices]
    df["Volume"] = volumes if volumes is not None else [1_000_000] * n
    return df


def _make_uptrend_df(n=250, base=1000, step=5.0):
    close = base + np.arange(n) * step
    high = close + step
    low = close - step * 0.5
    return _make_df(close.tolist(), [1_000_000] * n, high.tolist(), low.tolist())


def _make_downtrend_df(n=250, base=2000, step=5.0):
    close = base - np.arange(n) * step
    high = close + step * 0.5
    low = close - step
    return _make_df(close.tolist(), [1_000_000] * n, high.tolist(), low.tolist())


@pytest.fixture(autouse=True)
def temp_trades_file(tmp_path):
    trades_file = str(tmp_path / "trades_test.json")
    with patch.object(portfolio, "TRADES_FILE", trades_file):
        yield trades_file


@pytest.fixture
def mock_config():
    return {
        "watchlist": ["7203.T", "6758.T", "9984.T"],
        "account": {
            "balance": 300000,
            "risk_per_trade": 0.02,
            "max_allocation": 0.10,
            "max_positions": 10,
            "unit": 1,
            "max_daily_entries": 3,
            "max_sector_positions": 2,
            "cooldown_days": 7,
            "max_consecutive_losses": 2,
        },
        "strategy": {
            "sma_short": 25, "sma_long": 100, "sma_trend": 200,
            "rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30,
            "rsi_entry_min": 50, "rsi_entry_max": 65,
            "stop_loss_pct": 0.08, "profit_tighten_pct": 0.06,
            "profit_take_pct": 0.08, "profit_take_ratio": 0.5,
            "profit_take_full_pct": 0.15,
            "min_volume": 500000, "market_regime_enabled": True,
            "tv_recommendation_enabled": False,
            "adx_period": 14, "adx_threshold": 25,
            "ichimoku_tenkan": 9, "ichimoku_kijun": 26, "ichimoku_senkou_b": 52,
            "coch_exit_enabled": True, "coch_lookback": 3,
            "score_weights": {
                "volume_surge": 0.20, "rsi_sweet_spot": 0.10,
                "sma_momentum": 0.25, "price_vs_sma200": 0.10,
                "tv_recommendation": 0.15, "ichimoku_bullish": 0.10,
                "trend_strength": 0.10,
            },
            "slope_days": 5, "slope_blend": 0.3,
            "llm_review_enabled": False, "llm_max_review": 10,
        },
        "risk": {"max_sector_pct": 0.30, "max_portfolio_dd": 0.10, "correlation_threshold": 0.70},
        "discord": {"webhook_url": ""}, "slack": {"webhook_url": ""},
        "profiles": {
            "conservative": {"strategy": {"stop_loss_pct": 0.12}},
            "aggressive": {"strategy": {"stop_loss_pct": 0.05}},
        },
        "mode": "paper",
    }


# ── Tests ──


class TestScanOnly:
    def test_returns_structure(self, mock_config):
        uptrend = _make_uptrend_df()
        downtrend = _make_downtrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)
        call_count = {"n": 0}

        def mock_fetch(ticker, period="1y"):
            call_count["n"] += 1
            if ticker == "^N225":
                return nikkei
            return uptrend if call_count["n"] % 2 == 0 else downtrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert "market_regime" in result
        assert "scan_summary" in result
        assert "buy_candidates" in result
        assert "sell_signals" in result
        assert "portfolio" in result
        assert result["scan_summary"]["total_scanned"] == 3

    def test_buy_candidates_sorted(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        candidates = result["buy_candidates"]
        if len(candidates) >= 2:
            scores = [c.get("composite_score", 0) for c in candidates]
            assert scores == sorted(scores, reverse=True)

    def test_no_nan_in_output(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        json_str = json.dumps(result, default=str)
        assert "NaN" not in json_str
        assert "Infinity" not in json_str

    def test_with_open_positions(self, mock_config, temp_trades_file):
        portfolio.record_entry("7203.T", 2000.0, 5, entry_date="2026-03-01")
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        assert result["portfolio"]["cash"] < 300000
        assert len(result["portfolio"]["open_positions"]) == 1

    def test_profile_not_found(self, mock_config):
        with patch("main.load_config", return_value=mock_config):
            result = scan_only("nonexistent")
        assert "error" in result

    def test_handles_fetch_errors(self, mock_config):
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei
            raise Exception("API error")

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert result["scan_summary"]["error_count"] == 3
        assert result["scan_summary"]["buy_count"] == 0

    def test_sell_signals_only_for_held(self, mock_config, temp_trades_file):
        portfolio.record_entry("6758.T", 1500.0, 5, entry_date="2026-03-01")
        downtrend = _make_downtrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else downtrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        for s in result["sell_signals"]:
            assert s["ticker"] == "6758.T"

    def test_conservative_profile(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("conservative")

        assert "error" not in result

    def test_market_regime_bull(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei_bull = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei_bull if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert result["market_regime"]["regime"] == "bull"

    def test_market_regime_bear(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei_bear = _make_downtrend_df(n=250, base=40000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei_bear if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert result["market_regime"]["regime"] == "bear"

    def test_max_10_candidates(self, mock_config):
        mock_config["watchlist"] = [f"{i:04d}.T" for i in range(1000, 1020)]
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert len(result["buy_candidates"]) <= 10

    def test_composite_score_present(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        for c in result["buy_candidates"]:
            assert "composite_score" in c
            assert 0.0 <= c["composite_score"] <= 1.0

    def test_with_tv_scores(self, mock_config):
        mock_config["strategy"]["tv_recommendation_enabled"] = True
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=0.65):
            result = scan_only("default")

        for c in result["buy_candidates"]:
            assert c.get("tv_score") == 0.65

    def test_portfolio_snapshot(self, mock_config, temp_trades_file):
        portfolio.record_entry("7203.T", 1000.0, 10, entry_date="2026-03-01")
        portfolio.record_entry("6758.T", 2000.0, 5, entry_date="2026-03-05")
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        port = result["portfolio"]
        assert port["cash"] == 280000.0
        assert port["stock_value"] == 20000.0
        assert len(port["open_positions"]) == 2

    def test_buy_candidate_fields(self, mock_config):
        uptrend = _make_uptrend_df()
        nikkei = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            return nikkei if ticker == "^N225" else uptrend

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        for c in result["buy_candidates"]:
            assert "ticker" in c
            assert "price" in c
            assert "rsi" in c
            assert "signal" in c
            assert "composite_score" in c
            assert "stop_loss" in c
            assert "recommended_shares" in c
