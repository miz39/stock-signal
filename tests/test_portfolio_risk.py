import json
import os
import pytest
from unittest.mock import patch, MagicMock

import portfolio_risk


def _make_positions(tickers_and_prices):
    """Helper: create position dicts."""
    positions = []
    for ticker, price, shares in tickers_and_prices:
        positions.append({
            "ticker": ticker,
            "entry_price": price,
            "shares": shares,
            "current_price": price,
        })
    return positions


class TestSectorConcentration:
    def test_no_alert_when_balanced(self):
        # 3 positions in different sectors, each ~33%
        positions = _make_positions([
            ("7203.T", 1000, 10),  # Automobile
            ("6758.T", 1000, 10),  # Electric
            ("9984.T", 1000, 10),  # IT
        ])
        alerts = portfolio_risk.check_sector_concentration(positions, 30000, max_pct=0.40)
        assert len(alerts) == 0

    def test_alert_when_concentrated(self):
        # 2 of 3 in same sector
        positions = _make_positions([
            ("7203.T", 1000, 20),  # Automobile - 66%
            ("7267.T", 1000, 10),  # Automobile
            ("6758.T", 1000, 10),  # Electric - 33%
        ])
        alerts = portfolio_risk.check_sector_concentration(positions, 40000, max_pct=0.50)
        # Automobile = 30000/40000 = 75% → over 50%
        assert len(alerts) >= 1
        assert alerts[0]["pct"] > 50

    def test_empty_positions(self):
        alerts = portfolio_risk.check_sector_concentration([], 300000)
        assert alerts == []

    def test_zero_total_assets(self):
        positions = _make_positions([("7203.T", 1000, 10)])
        alerts = portfolio_risk.check_sector_concentration(positions, 0)
        assert alerts == []


class TestPortfolioDrawdown:
    def test_no_alert_within_limit(self):
        # Total = 280000, initial = 300000, DD = -6.7% < 10%
        with patch.object(portfolio_risk, "_load_execution_history", return_value=[]):
            result = portfolio_risk.check_portfolio_drawdown(280000, 300000, max_dd_pct=0.10)
        assert result is None

    def test_alert_when_exceeds(self):
        # Total = 260000, initial = 300000, DD = -13.3% > 10%
        with patch.object(portfolio_risk, "_load_execution_history", return_value=[]):
            result = portfolio_risk.check_portfolio_drawdown(260000, 300000, max_dd_pct=0.10)
        assert result is not None
        assert result["drawdown_pct"] < -10

    def test_tracks_peak_from_history(self):
        history = [
            {"portfolio_snapshot": {"total_assets": 350000}},
            {"portfolio_snapshot": {"total_assets": 340000}},
        ]
        with patch.object(portfolio_risk, "_load_execution_history", return_value=history):
            # Peak = 350000, current = 300000, DD = -14.3%
            result = portfolio_risk.check_portfolio_drawdown(300000, 300000, max_dd_pct=0.10)
        assert result is not None
        assert result["peak"] == 350000


class TestAnomalies:
    def test_zero_buy_streak(self):
        history = [
            {"scan": {"buy_count": 0}, "date": "2026-04-01"},
            {"scan": {"buy_count": 0}, "date": "2026-04-01"},
            {"scan": {"buy_count": 0}, "date": "2026-04-02"},
        ]
        with patch.object(portfolio_risk, "_load_execution_history", return_value=history):
            result = portfolio_risk._check_zero_buy_streak(threshold=3)
        assert result is not None
        assert result["type"] == "zero_buy_streak"

    def test_no_zero_buy_streak_when_signals_present(self):
        history = [
            {"scan": {"buy_count": 5}, "date": "2026-04-01"},
            {"scan": {"buy_count": 0}, "date": "2026-04-01"},
            {"scan": {"buy_count": 3}, "date": "2026-04-02"},
        ]
        with patch.object(portfolio_risk, "_load_execution_history", return_value=history):
            result = portfolio_risk._check_zero_buy_streak(threshold=3)
        assert result is None

    def test_stale_execution_alert(self):
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(portfolio_risk.JST) - timedelta(hours=30)).isoformat()
        history = [{"timestamp": old_ts}]
        with patch.object(portfolio_risk, "_load_execution_history", return_value=history):
            # Only triggers on weekdays
            from unittest.mock import PropertyMock
            result = portfolio_risk._check_stale_execution(max_hours=26)
        # Result depends on current weekday
        if datetime.now(portfolio_risk.JST).weekday() < 5:
            assert result is not None
            assert result["type"] == "stale_execution"


class TestFormatRiskReport:
    def test_basic_report(self):
        positions = _make_positions([
            ("7203.T", 1000, 10),
            ("6758.T", 2000, 5),
        ])
        config = {"account": {"balance": 300000}}
        with patch.object(portfolio_risk, "_load_execution_history", return_value=[]):
            report = portfolio_risk.format_risk_report(positions, 300000, config)
        assert "sectors" in report
        assert "sector_alerts" in report
        assert "anomalies" in report
        assert report["position_count"] == 2
