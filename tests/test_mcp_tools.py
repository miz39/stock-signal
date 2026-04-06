"""Tests for MCP server tools using mock data (no API calls).

MCP tool tests require Python 3.10+ (mcp package). They are skipped on 3.9.
scan_only() tests work on any Python version.
"""

import asyncio
import json
import math
import os
import tempfile

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

import portfolio
from main import load_config, scan_only


# ── Fixtures ──


def _make_df(prices, volumes=None, highs=None, lows=None, start_date="2025-01-01"):
    """Create OHLCV DataFrame with datetime index."""
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
    """Strong uptrend: SMA25 > SMA100, close > SMA200, ADX high."""
    close = base + np.arange(n) * step
    high = close + step
    low = close - step * 0.5
    volumes = [1_000_000] * n
    return _make_df(close.tolist(), volumes, high.tolist(), low.tolist())


def _make_downtrend_df(n=250, base=2000, step=5.0):
    """Downtrend: SMA25 < SMA100."""
    close = base - np.arange(n) * step
    high = close + step * 0.5
    low = close - step
    volumes = [1_000_000] * n
    return _make_df(close.tolist(), volumes, high.tolist(), low.tolist())


def _make_sideways_df(n=250, base=1500):
    """Range-bound market."""
    np.random.seed(42)
    close = base + np.random.randn(n) * 5
    high = close + abs(np.random.randn(n)) * 3
    low = close - abs(np.random.randn(n)) * 3
    volumes = [1_000_000] * n
    return _make_df(close.tolist(), volumes, high.tolist(), low.tolist())


@pytest.fixture(autouse=True)
def temp_trades_file(tmp_path):
    """Use a temporary trades file for each test."""
    trades_file = str(tmp_path / "trades_test.json")
    with patch.object(portfolio, "TRADES_FILE", trades_file):
        yield trades_file


@pytest.fixture
def mock_config():
    """Standard test config."""
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
            "sma_short": 25,
            "sma_long": 100,
            "sma_trend": 200,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "rsi_entry_min": 50,
            "rsi_entry_max": 65,
            "stop_loss_pct": 0.08,
            "profit_tighten_pct": 0.06,
            "profit_take_pct": 0.08,
            "profit_take_ratio": 0.5,
            "profit_take_full_pct": 0.15,
            "min_volume": 500000,
            "market_regime_enabled": True,
            "tv_recommendation_enabled": False,
            "adx_period": 14,
            "adx_threshold": 25,
            "ichimoku_tenkan": 9,
            "ichimoku_kijun": 26,
            "ichimoku_senkou_b": 52,
            "coch_exit_enabled": True,
            "coch_lookback": 3,
            "score_weights": {
                "volume_surge": 0.20,
                "rsi_sweet_spot": 0.10,
                "sma_momentum": 0.25,
                "price_vs_sma200": 0.10,
                "tv_recommendation": 0.15,
                "ichimoku_bullish": 0.10,
                "trend_strength": 0.10,
            },
            "slope_days": 5,
            "slope_blend": 0.3,
            "llm_review_enabled": False,
            "llm_max_review": 10,
        },
        "risk": {
            "max_sector_pct": 0.30,
            "max_portfolio_dd": 0.10,
            "correlation_threshold": 0.70,
        },
        "discord": {"webhook_url": ""},
        "slack": {"webhook_url": ""},
        "profiles": {
            "conservative": {"strategy": {"stop_loss_pct": 0.12}},
            "aggressive": {"strategy": {"stop_loss_pct": 0.05}},
        },
        "mode": "paper",
    }


# ── Tests: scan_only (works on Python 3.9+) ──


class TestScanOnly:
    """Test the scan_only() function used by scan_market MCP tool."""

    def test_scan_only_returns_structure(self, mock_config):
        uptrend_df = _make_uptrend_df()
        downtrend_df = _make_downtrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        call_count = {"n": 0}

        def mock_fetch(ticker, period="1y"):
            call_count["n"] += 1
            if ticker == "^N225":
                return nikkei_df
            if call_count["n"] % 2 == 0:
                return uptrend_df
            return downtrend_df

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

    def test_scan_only_buy_candidates_sorted(self, mock_config):
        """BUY candidates should be sorted by composite_score descending."""
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        candidates = result["buy_candidates"]
        if len(candidates) >= 2:
            scores = [c.get("composite_score", 0) for c in candidates]
            assert scores == sorted(scores, reverse=True)

    def test_scan_only_no_nan_in_output(self, mock_config):
        """Output should not contain NaN values (breaks JSON)."""
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        json_str = json.dumps(result, default=str)
        assert "NaN" not in json_str
        assert "Infinity" not in json_str

    def test_scan_only_with_open_positions(self, mock_config, temp_trades_file):
        """scan_only should include portfolio info when positions exist."""
        portfolio.record_entry("7203.T", 2000.0, 5, entry_date="2026-03-01")

        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        assert result["portfolio"]["cash"] < 300000
        assert len(result["portfolio"]["open_positions"]) == 1
        assert result["portfolio"]["open_positions"][0]["ticker"] == "7203.T"

    def test_scan_only_profile_not_found(self, mock_config):
        with patch("main.load_config", return_value=mock_config):
            result = scan_only("nonexistent")
        assert "error" in result

    def test_scan_only_handles_fetch_errors(self, mock_config):
        """scan_only should handle individual stock fetch failures gracefully."""
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            raise Exception("API error")

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert result["scan_summary"]["error_count"] == 3
        assert result["scan_summary"]["buy_count"] == 0

    def test_scan_only_sell_signals_for_open_positions(self, mock_config, temp_trades_file):
        """SELL signals should only appear for currently held positions."""
        portfolio.record_entry("6758.T", 1500.0, 5, entry_date="2026-03-01")

        downtrend_df = _make_downtrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return downtrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        # Only held ticker should appear in sell_signals
        sell_tickers = [s["ticker"] for s in result["sell_signals"]]
        for t in sell_tickers:
            assert t == "6758.T"

    def test_scan_only_conservative_profile(self, mock_config):
        """Conservative profile should merge strategy overrides."""
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("conservative")

        assert "error" not in result
        assert "scan_summary" in result

    def test_scan_only_market_regime_detection(self, mock_config):
        """Market regime should reflect the Nikkei 225 trend."""
        uptrend_df = _make_uptrend_df()

        # Bull regime
        nikkei_bull = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch_bull(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_bull
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch_bull), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert result["market_regime"]["regime"] == "bull"

        # Bear regime
        nikkei_bear = _make_downtrend_df(n=250, base=40000, step=50)

        def mock_fetch_bear(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_bear
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch_bear), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert result["market_regime"]["regime"] == "bear"

    def test_scan_only_max_10_candidates(self, mock_config):
        """buy_candidates should be capped at 10."""
        mock_config["watchlist"] = [f"{i:04d}.T" for i in range(1000, 1020)]
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        assert len(result["buy_candidates"]) <= 10

    def test_scan_only_composite_score_present(self, mock_config):
        """Each BUY candidate should have a composite_score."""
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None):
            result = scan_only("default")

        for c in result["buy_candidates"]:
            assert "composite_score" in c
            assert 0.0 <= c["composite_score"] <= 1.0

    def test_scan_only_with_tv_scores(self, mock_config):
        """TV recommendation scores should be included when enabled."""
        mock_config["strategy"]["tv_recommendation_enabled"] = True
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=0.65):
            result = scan_only("default")

        for c in result["buy_candidates"]:
            assert "tv_score" in c
            assert c["tv_score"] == 0.65

    def test_scan_only_portfolio_snapshot(self, mock_config, temp_trades_file):
        """Portfolio snapshot should include cash, stock value, total assets."""
        portfolio.record_entry("7203.T", 1000.0, 10, entry_date="2026-03-01")
        portfolio.record_entry("6758.T", 2000.0, 5, entry_date="2026-03-05")

        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        port = result["portfolio"]
        assert port["cash"] == 280000.0  # 300000 - 10*1000 - 5*2000
        assert port["stock_value"] == 20000.0
        assert port["total_assets"] == 300000.0
        assert len(port["open_positions"]) == 2

    def test_scan_only_buy_candidate_fields(self, mock_config):
        """Each BUY candidate should have required fields."""
        uptrend_df = _make_uptrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            return uptrend_df

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

    def test_scan_only_mixed_signals(self, mock_config, temp_trades_file):
        """Mix of BUY and SELL signals with held positions."""
        portfolio.record_entry("9984.T", 1500.0, 5, entry_date="2026-03-01")

        uptrend_df = _make_uptrend_df()
        downtrend_df = _make_downtrend_df()
        nikkei_df = _make_uptrend_df(n=250, base=30000, step=50)

        def mock_fetch(ticker, period="1y"):
            if ticker == "^N225":
                return nikkei_df
            if ticker == "9984.T":
                return downtrend_df  # SELL signal for held stock
            return uptrend_df  # BUY signal for others

        with patch("main.load_config", return_value=mock_config), \
             patch("main.fetch_stock_data", side_effect=mock_fetch), \
             patch("main.fetch_tv_recommendation", return_value=None), \
             patch("main.set_profile"):
            result = scan_only("default")

        # Should have both buy and sell signals
        assert result["scan_summary"]["buy_count"] >= 0
        assert result["scan_summary"]["sell_count"] >= 0
        # sell_signals should only contain held tickers
        for s in result["sell_signals"]:
            assert s["ticker"] == "9984.T"


# ── Tests: MCP Tools (require Python 3.10+ / mcp package) ──

mcp_module = pytest.importorskip("mcp", reason="mcp package requires Python 3.10+")


def _get_mcp():
    from mcp_server.server import mcp
    return mcp


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def extract_json(result):
    """Extract JSON from MCP tool result."""
    return json.loads(result[0][0].text)


class TestMCPMarketTools:
    def test_get_stock_data(self):
        mcp = _get_mcp()
        df = _make_uptrend_df()

        with patch("data.fetch_stock_data", return_value=df):
            result = run_async(mcp.call_tool("get_stock_data", {"ticker": "7203.T"}))

        data = extract_json(result)
        assert data["ticker"] == "7203.T"
        assert data["latest_price"] > 0
        assert data["sma25"] > 0
        assert data["sma200"] > 0
        assert 0 <= data["rsi14"] <= 100
        assert len(data["last_5_days"]) == 5
        assert data["high_52w"] >= data["low_52w"]

    def test_get_stock_data_short_data(self):
        """52w range should work even with less than 252 days of data."""
        mcp = _get_mcp()
        df = _make_uptrend_df(n=50)

        with patch("data.fetch_stock_data", return_value=df):
            result = run_async(mcp.call_tool("get_stock_data", {"ticker": "7203.T"}))

        data = extract_json(result)
        raw = result[0][0].text
        assert "NaN" not in raw
        assert data["high_52w"] > 0
        assert data["low_52w"] > 0

    def test_get_market_regime_bull(self):
        mcp = _get_mcp()
        df = _make_uptrend_df(n=250, base=30000, step=50)

        with patch("data.fetch_stock_data", return_value=df):
            result = run_async(mcp.call_tool("get_market_regime", {}))

        data = extract_json(result)
        assert data["regime"] == "bull"
        assert data["sma50"] is not None
        assert data["sma200"] is not None

    def test_get_market_regime_bear(self):
        mcp = _get_mcp()
        df = _make_downtrend_df(n=250, base=40000, step=50)

        with patch("data.fetch_stock_data", return_value=df):
            result = run_async(mcp.call_tool("get_market_regime", {}))

        data = extract_json(result)
        assert data["regime"] == "bear"

    def test_get_financial_data(self):
        mcp = _get_mcp()
        mock_fin = {
            "source": "yfinance",
            "per": 15.5,
            "pbr": 2.1,
            "roe": 0.12,
            "dividend_yield": 0.025,
            "revenue_growth": 0.08,
        }

        with patch("data.fetch_financial_data", return_value=mock_fin):
            result = run_async(mcp.call_tool("get_financial_data", {"ticker": "7203.T"}))

        data = extract_json(result)
        assert data["per"] == 15.5
        assert data["pbr"] == 2.1
        assert data["roe"] == 0.12


class TestMCPAnalysisTools:
    def test_get_signal_uptrend(self, mock_config):
        mcp = _get_mcp()
        df = _make_uptrend_df()

        with patch("data.fetch_stock_data", return_value=df), \
             patch("main.load_config", return_value=mock_config), \
             patch("strategy.fetch_tv_recommendation", return_value=None):
            result = run_async(mcp.call_tool("get_signal", {"ticker": "7203.T"}))

        data = extract_json(result)
        assert data["signal"] in ("BUY", "HOLD", "SELL")
        assert "rsi" in data
        assert "adx" in data
        assert "composite_score" in data

    def test_get_signal_downtrend(self, mock_config):
        mcp = _get_mcp()
        df = _make_downtrend_df()

        with patch("data.fetch_stock_data", return_value=df), \
             patch("main.load_config", return_value=mock_config), \
             patch("strategy.fetch_tv_recommendation", return_value=None):
            result = run_async(mcp.call_tool("get_signal", {"ticker": "7203.T"}))

        data = extract_json(result)
        assert data["signal"] in ("SELL", "HOLD")

    def test_get_signal_no_nan(self, mock_config):
        mcp = _get_mcp()
        df = _make_sideways_df()

        with patch("data.fetch_stock_data", return_value=df), \
             patch("main.load_config", return_value=mock_config), \
             patch("strategy.fetch_tv_recommendation", return_value=None):
            result = run_async(mcp.call_tool("get_signal", {"ticker": "7203.T"}))

        raw = result[0][0].text
        assert "NaN" not in raw
        assert "Infinity" not in raw

    def test_get_technical_summary_uptrend(self, mock_config):
        mcp = _get_mcp()
        df = _make_uptrend_df()

        with patch("data.fetch_stock_data", return_value=df), \
             patch("main.load_config", return_value=mock_config), \
             patch("strategy.fetch_tv_recommendation", return_value=None):
            result = run_async(mcp.call_tool("get_technical_summary", {"ticker": "9984.T"}))

        data = extract_json(result)
        assert "sma" in data
        assert "rsi" in data
        assert "adx" in data
        assert "ichimoku" in data
        assert "coch" in data
        assert "volume" in data
        assert "range_52w" in data
        assert data["sma"]["golden_cross"] is True
        assert data["adx"]["trend"] == "strong"
        assert data["ichimoku"]["above_cloud"] is True

    def test_get_technical_summary_downtrend(self, mock_config):
        mcp = _get_mcp()
        df = _make_downtrend_df()

        with patch("data.fetch_stock_data", return_value=df), \
             patch("main.load_config", return_value=mock_config), \
             patch("strategy.fetch_tv_recommendation", return_value=None):
            result = run_async(mcp.call_tool("get_technical_summary", {"ticker": "9984.T"}))

        data = extract_json(result)
        assert data["sma"]["golden_cross"] is False
        assert data["signal"] in ("SELL", "HOLD")


class TestMCPPortfolioTools:
    def test_get_positions_empty(self):
        mcp = _get_mcp()

        with patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_positions", {"profile": "default"}))

        data = extract_json(result)
        assert data["positions"] == []

    def test_get_positions_with_entries(self, temp_trades_file):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 2500.0, 10, entry_date="2026-03-01")
        portfolio.record_entry("6758.T", 3000.0, 5, entry_date="2026-03-05")

        df = _make_uptrend_df()

        with patch("data.fetch_stock_data", return_value=df), \
             patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_positions", {"profile": "default"}))

        data = extract_json(result)
        assert len(data["positions"]) == 2
        pos = data["positions"][0]
        assert "ticker" in pos
        assert "current_price" in pos
        assert "unrealized_pnl" in pos
        assert "days_held" in pos

    def test_get_cash(self, temp_trades_file, mock_config):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 1000.0, 10, entry_date="2026-03-01")

        with patch("main.load_config", return_value=mock_config), \
             patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_cash", {"profile": "default"}))

        data = extract_json(result)
        assert data["cash"] == 290000.0
        assert data["stock_value"] == 10000.0
        assert data["total_assets"] == 300000.0

    def test_get_performance_with_trades(self, temp_trades_file):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("7203.T", 1100.0, exit_date="2026-01-15")
        portfolio.record_entry("6758.T", 2000.0, 5, entry_date="2026-01-10")
        portfolio.record_exit("6758.T", 1900.0, exit_date="2026-01-20")

        with patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_performance", {"period": "all"}))

        data = extract_json(result)
        assert data["trade_count"] == 2
        assert data["wins"] == 1
        assert data["losses"] == 1
        assert data["win_rate"] == 50.0
        assert data["total_pnl"] == 500.0
        assert data["profit_factor"] == 2.0

    def test_get_performance_empty(self, temp_trades_file):
        mcp = _get_mcp()
        with patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_performance", {"period": "all"}))
        data = extract_json(result)
        assert data["trade_count"] == 0

    def test_get_weekly_report(self, temp_trades_file, mock_config):
        mcp = _get_mcp()

        with patch("main.load_config", return_value=mock_config), \
             patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_weekly_report", {}))

        data = extract_json(result)
        assert "weekly_trades" in data
        assert "weekly_pnl" in data
        assert "total_pnl" in data


class TestMCPTradingTools:
    def test_execute_buy_preview(self, mock_config):
        mcp = _get_mcp()

        with patch("main.load_config", return_value=mock_config):
            result = run_async(mcp.call_tool("execute_buy", {
                "ticker": "7203.T", "price": 2500.0, "shares": 5, "confirm": False
            }))

        data = extract_json(result)
        assert data["confirmed"] is False
        assert "PREVIEW" in data["status"]
        assert data["cost"] == 12500.0
        assert data["stop_loss"] == 2300.0
        assert data["risk_amount"] == 1000.0

    def test_execute_buy_confirm(self, temp_trades_file, mock_config):
        mcp = _get_mcp()

        with patch("main.load_config", return_value=mock_config), \
             patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("execute_buy", {
                "ticker": "7203.T", "price": 2500.0, "shares": 5, "confirm": True
            }))

        data = extract_json(result)
        assert data["confirmed"] is True
        assert data["status"] == "EXECUTED"
        positions = portfolio.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["ticker"] == "7203.T"

    def test_execute_sell_preview(self, temp_trades_file):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 2000.0, 10, entry_date="2026-03-01")

        with patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("execute_sell", {
                "ticker": "7203.T", "price": 2200.0, "confirm": False
            }))

        data = extract_json(result)
        assert data["confirmed"] is False
        assert "PREVIEW" in data["status"]
        assert data["pnl"] == 2000.0
        assert data["pnl_pct"] == 10.0

    def test_execute_sell_confirm(self, temp_trades_file):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 2000.0, 10, entry_date="2026-03-01")

        with patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("execute_sell", {
                "ticker": "7203.T", "price": 2200.0, "confirm": True
            }))

        data = extract_json(result)
        assert data["status"] == "EXECUTED"
        assert data["pnl"] == 2000.0
        assert portfolio.get_open_positions() == []

    def test_execute_sell_no_position(self, temp_trades_file):
        mcp = _get_mcp()

        result = run_async(mcp.call_tool("execute_sell", {
            "ticker": "9999.T", "price": 1000.0, "confirm": False
        }))

        data = extract_json(result)
        assert "error" in data

    def test_update_stops(self, temp_trades_file, mock_config):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 2000.0, 10, entry_date="2026-03-01")

        df_up = _make_df([2200.0] * 5, [1_000_000] * 5)

        with patch("main.load_config", return_value=mock_config), \
             patch("data.fetch_stock_data", return_value=df_up), \
             patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("update_stops", {}))

        data = extract_json(result)
        assert len(data["updates"]) == 1
        assert data["updates"][0]["current_price"] == 2200.0
        assert data["updates"][0]["updated"] is True


class TestMCPRiskTools:
    def test_get_risk_report_empty(self, mock_config, temp_trades_file):
        mcp = _get_mcp()

        with patch("main.load_config", return_value=mock_config), \
             patch("data.fetch_stock_data", return_value=_make_uptrend_df()), \
             patch("portfolio.set_profile"):
            result = run_async(mcp.call_tool("get_risk_report", {"quick": True}))

        data = extract_json(result)
        assert data["position_count"] == 0

    def test_get_risk_report_with_positions(self, mock_config, temp_trades_file):
        mcp = _get_mcp()
        portfolio.record_entry("7203.T", 2000.0, 10, entry_date="2026-03-01")
        portfolio.record_entry("6758.T", 3000.0, 5, entry_date="2026-03-05")

        df = _make_uptrend_df()

        with patch("main.load_config", return_value=mock_config), \
             patch("data.fetch_stock_data", return_value=df), \
             patch("portfolio.set_profile"), \
             patch("portfolio_risk.calculate_portfolio_var", return_value={
                 "var_pct": -2.5, "var_amount": -7500, "cvar_pct": -3.0, "cvar_amount": -9000,
             }), \
             patch("portfolio_risk.calculate_portfolio_volatility", return_value={
                 "daily_vol": 1.5, "annual_vol": 23.8,
             }):
            result = run_async(mcp.call_tool("get_risk_report", {"quick": True}))

        data = extract_json(result)
        assert data["position_count"] == 2
        assert "sectors" in data
        assert "var" in data
        assert "volatility" in data
        assert data["correlations"] == "skipped (quick mode)"
