"""Tests for valuation agents (DCF, Three-Statement, Comps, Operating Model, Sensitivity)."""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from datetime import datetime

import portfolio


# --- Test Fixtures ---

def _make_df(prices, start_date="2025-01-01"):
    """Create OHLCV DataFrame."""
    n = len(prices)
    dates = pd.date_range(start=start_date, periods=n, freq="B")
    df = pd.DataFrame(index=dates)
    df["Close"] = prices
    df["High"] = [p * 1.01 for p in prices]
    df["Low"] = [p * 0.99 for p in prices]
    df["Open"] = [p * 1.005 for p in prices]
    df["Volume"] = [1_000_000] * n
    return df


def _make_income_statement():
    """Create mock income statement DataFrame."""
    dates = pd.to_datetime(["2024-03-31", "2023-03-31", "2022-03-31"])
    data = {
        dates[0]: {
            "Total Revenue": 3e12,
            "Operating Income": 3e11,
            "Net Income": 2e11,
            "Tax Provision": 1e11,
            "Pretax Income": 3e11,
        },
        dates[1]: {
            "Total Revenue": 2.8e12,
            "Operating Income": 2.5e11,
            "Net Income": 1.7e11,
            "Tax Provision": 8e10,
            "Pretax Income": 2.5e11,
        },
        dates[2]: {
            "Total Revenue": 2.5e12,
            "Operating Income": 2e11,
            "Net Income": 1.5e11,
            "Tax Provision": 7e10,
            "Pretax Income": 2.2e11,
        },
    }
    return pd.DataFrame(data)


def _make_balance_sheet():
    """Create mock balance sheet DataFrame."""
    dates = pd.to_datetime(["2024-03-31", "2023-03-31", "2022-03-31"])
    data = {
        dates[0]: {
            "Total Assets": 5e12,
            "Stockholders Equity": 2.5e12,
            "Current Assets": 2e12,
            "Current Liabilities": 1.5e12,
            "Total Debt": 1e12,
            "Long Term Debt": 8e11,
            "Cash And Cash Equivalents": 5e11,
        },
        dates[1]: {
            "Total Assets": 4.5e12,
            "Stockholders Equity": 2.2e12,
            "Current Assets": 1.8e12,
            "Current Liabilities": 1.4e12,
            "Total Debt": 9e11,
            "Long Term Debt": 7e11,
            "Cash And Cash Equivalents": 4e11,
        },
        dates[2]: {
            "Total Assets": 4e12,
            "Stockholders Equity": 2e12,
            "Current Assets": 1.6e12,
            "Current Liabilities": 1.3e12,
            "Total Debt": 8e11,
            "Long Term Debt": 6e11,
            "Cash And Cash Equivalents": 3.5e11,
        },
    }
    return pd.DataFrame(data)


def _make_cash_flow():
    """Create mock cash flow DataFrame."""
    dates = pd.to_datetime(["2024-03-31", "2023-03-31", "2022-03-31"])
    data = {
        dates[0]: {
            "Total Cash From Operating Activities": 4e11,
            "Capital Expenditures": -1e11,
        },
        dates[1]: {
            "Total Cash From Operating Activities": 3.5e11,
            "Capital Expenditures": -9e10,
        },
        dates[2]: {
            "Total Cash From Operating Activities": 3e11,
            "Capital Expenditures": -8e10,
        },
    }
    return pd.DataFrame(data)


def _mock_financial_statements():
    """Create complete mock financial statements."""
    return {
        "income_statement": _make_income_statement(),
        "balance_sheet": _make_balance_sheet(),
        "cash_flow": _make_cash_flow(),
        "info": {
            "sharesOutstanding": 1_500_000_000,
        },
    }


@pytest.fixture
def mock_config():
    return {
        "valuation": {
            "enabled": True,
            "wacc_defaults": {
                "自動車・精密": 0.09,
                "機械・電機": 0.09,
                "金融・商社": 0.08,
            },
            "terminal_growth": 0.015,
            "projection_years": 5,
            "weights": {
                "dcf": 0.30,
                "comps": 0.25,
                "three_statement": 0.20,
                "operating_model": 0.15,
                "sensitivity": 0.10,
            },
        },
        "agents": {"enabled": True},
        "strategy": {
            "sma_short": 25,
            "sma_long": 100,
            "sma_trend": 200,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "adx_period": 14,
            "adx_threshold": 25,
            "ichimoku_tenkan": 9,
            "ichimoku_kijun": 26,
            "ichimoku_senkou_b": 52,
        },
        "account": {"balance": 300000},
    }


@pytest.fixture
def sample_df():
    """DataFrame with 250 days of uptrending prices around 2000."""
    prices = [2000 + i * 2 for i in range(250)]
    return _make_df(prices)


@pytest.fixture(autouse=True)
def temp_trades_file(tmp_path):
    trades_file = str(tmp_path / "trades_test.json")
    with patch.object(portfolio, "TRADES_FILE", trades_file):
        yield trades_file


# --- DCF Agent Tests ---

class TestDCFAgent:
    @patch("agents.dcf.fetch_financial_statements")
    def test_basic_dcf(self, mock_fetch, sample_df, mock_config):
        from agents.dcf import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        assert result["agent"] == "DCF"
        assert -2.0 <= result["score"] <= 2.0
        assert 0 <= result["confidence"] <= 100
        assert len(result["reasons"]) > 0
        assert "fair_value" in result["metrics"]
        assert "upside_pct" in result["metrics"]
        assert "wacc" in result["metrics"]
        assert "fcf_history" in result["metrics"]
        assert "fcf_projected" in result["metrics"]
        assert len(result["metrics"]["fcf_projected"]) == 5

    @patch("agents.dcf.fetch_financial_statements")
    def test_dcf_positive_fcf(self, mock_fetch, sample_df, mock_config):
        from agents.dcf import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        # FCF should be positive (Operating CF + negative CapEx)
        fcf_history = result["metrics"]["fcf_history"]
        assert all(f > 0 for f in fcf_history)

    @patch("agents.dcf.fetch_financial_statements")
    def test_dcf_no_ticker(self, mock_fetch, sample_df, mock_config):
        from agents.dcf import analyze

        result = analyze(sample_df, mock_config, ticker="")
        assert result["score"] == 0
        assert result["confidence"] == 0

    @patch("agents.dcf.fetch_financial_statements")
    def test_dcf_fetch_failure(self, mock_fetch, sample_df, mock_config):
        from agents.dcf import analyze

        mock_fetch.side_effect = Exception("Network error")
        result = analyze(sample_df, mock_config, ticker="7203.T")
        assert result["score"] == 0
        assert "取得失敗" in result["reasons"][0]

    @patch("agents.dcf.fetch_financial_statements")
    def test_dcf_negative_fcf(self, mock_fetch, sample_df, mock_config):
        from agents.dcf import analyze

        statements = _mock_financial_statements()
        # Make all FCF negative
        for col in statements["cash_flow"].columns:
            statements["cash_flow"].loc["Total Cash From Operating Activities", col] = -1e11
            statements["cash_flow"].loc["Capital Expenditures", col] = -5e10
        mock_fetch.return_value = statements

        result = analyze(sample_df, mock_config, ticker="7203.T")
        assert result["score"] <= 0


# --- Three-Statement Agent Tests ---

class TestThreeStatementAgent:
    @patch("agents.three_statement.fetch_financial_statements")
    def test_basic_analysis(self, mock_fetch, sample_df, mock_config):
        from agents.three_statement import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        assert result["agent"] == "三表財務"
        assert -2.0 <= result["score"] <= 2.0
        assert 0 <= result["confidence"] <= 100
        assert len(result["reasons"]) > 0

    @patch("agents.three_statement.fetch_financial_statements")
    def test_healthy_financials_positive_score(self, mock_fetch, sample_df, mock_config):
        from agents.three_statement import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        # Mock data has growing revenue, healthy BS, positive CF → should be positive
        assert result["score"] > 0

    @patch("agents.three_statement.fetch_financial_statements")
    def test_equity_ratio_metric(self, mock_fetch, sample_df, mock_config):
        from agents.three_statement import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        # Equity = 2.5T, Assets = 5T → 50%
        assert "equity_ratio" in result["metrics"]
        assert result["metrics"]["equity_ratio"] == pytest.approx(50.0, abs=1)


# --- Comps Agent Tests ---

class TestCompsAgent:
    @patch("agents.comps.fetch_financial_data")
    def test_basic_comps(self, mock_fetch, sample_df, mock_config):
        from agents.comps import analyze

        # Return data for target and peers
        mock_fetch.return_value = {
            "per": 12.0,
            "pbr": 1.2,
            "roe": 0.10,
            "market_cap": 30e12,
        }

        result = analyze(sample_df, mock_config, ticker="7203.T")

        assert result["agent"] == "類似企業比較"
        assert -2.0 <= result["score"] <= 2.0
        assert "sector" in result["metrics"]
        assert result["metrics"]["sector"] == "自動車・精密"

    @patch("agents.comps.fetch_financial_data")
    def test_comps_no_data(self, mock_fetch, sample_df, mock_config):
        from agents.comps import analyze

        mock_fetch.return_value = {"error": "No data"}
        result = analyze(sample_df, mock_config, ticker="7203.T")
        assert result["score"] == 0


# --- Operating Model Agent Tests ---

class TestOperatingModelAgent:
    @patch("agents.operating_model.fetch_financial_statements")
    def test_basic_analysis(self, mock_fetch, sample_df, mock_config):
        from agents.operating_model import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        assert result["agent"] == "オペレーティング"
        assert -2.0 <= result["score"] <= 2.0

    @patch("agents.operating_model.fetch_financial_statements")
    def test_roic_calculation(self, mock_fetch, sample_df, mock_config):
        from agents.operating_model import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        assert "roic_latest" in result["metrics"]
        # ROIC should be positive for our mock data
        assert result["metrics"]["roic_latest"] > 0


# --- Sensitivity Agent Tests ---

class TestSensitivityAgent:
    @patch("agents.sensitivity.fetch_financial_statements")
    def test_basic_sensitivity(self, mock_fetch, sample_df, mock_config):
        from agents.sensitivity import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        assert result["agent"] == "感応度"
        assert -2.0 <= result["score"] <= 2.0
        assert "scenarios" in result["metrics"]
        assert "sensitivity_table" in result["metrics"]

    @patch("agents.sensitivity.fetch_financial_statements")
    def test_sensitivity_table_dimensions(self, mock_fetch, sample_df, mock_config):
        from agents.sensitivity import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        table = result["metrics"]["sensitivity_table"]
        assert len(table) == 3  # 3 WACC values
        assert all(len(row) == 3 for row in table)  # 3 growth values each

    @patch("agents.sensitivity.fetch_financial_statements")
    def test_bull_base_bear(self, mock_fetch, sample_df, mock_config):
        from agents.sensitivity import analyze

        mock_fetch.return_value = _mock_financial_statements()
        result = analyze(sample_df, mock_config, ticker="7203.T")

        scenarios = result["metrics"]["scenarios"]
        assert "bull" in scenarios
        assert "base" in scenarios
        assert "bear" in scenarios
        # Bull >= Base >= Bear
        assert scenarios["bull"]["fair_value"] >= scenarios["base"]["fair_value"]
        assert scenarios["base"]["fair_value"] >= scenarios["bear"]["fair_value"]


# --- Coordinator Valuation Tests ---

class TestCoordinatorValuation:
    @patch("agents.coordinator.fetch_stock_data")
    @patch("agents.dcf.fetch_financial_statements")
    @patch("agents.three_statement.fetch_financial_statements")
    @patch("agents.operating_model.fetch_financial_statements")
    @patch("agents.sensitivity.fetch_financial_statements")
    @patch("agents.comps.fetch_financial_data")
    @patch("agents.fundamental.fetch_financial_data")
    @patch("agents.sentiment.fetch_stock_data")
    @patch("agents.risk_agent.fetch_stock_data")
    def test_full_analysis(self, mock_risk_data, mock_sent_data, mock_fund_data,
                           mock_comps_data, mock_sens_fs, mock_op_fs,
                           mock_three_fs, mock_dcf_fs, mock_coord_data,
                           sample_df, mock_config):
        from agents.coordinator import full_analysis

        # Setup all mocks
        mock_coord_data.return_value = sample_df
        mock_risk_data.return_value = sample_df
        mock_sent_data.return_value = sample_df

        fin_data = {
            "per": 12.0, "pbr": 1.2, "roe": 0.10, "market_cap": 30e12,
            "dividend_yield": 0.025, "revenue_growth": 0.05,
            "earnings_growth": 0.08, "operating_margin": 0.10,
            "equity_ratio": None, "debt_equity_ratio": None,
            "next_earnings_date": None, "source": "yfinance",
        }
        mock_fund_data.return_value = fin_data
        mock_comps_data.return_value = fin_data

        statements = _mock_financial_statements()
        mock_dcf_fs.return_value = statements
        mock_three_fs.return_value = statements
        mock_op_fs.return_value = statements
        mock_sens_fs.return_value = statements

        result = full_analysis("7203.T", mock_config, df=sample_df)

        assert "trading" in result
        assert "valuation" in result
        assert "combined_signal" in result
        assert "combined_score" in result
        assert -2.0 <= result["combined_score"] <= 2.0
        assert result["combined_signal"] in [
            "STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"
        ]

    def test_analyze_valuation_disabled(self, sample_df):
        from agents.coordinator import analyze_valuation

        config = {"valuation": {"enabled": False}}
        result = analyze_valuation("7203.T", config, df=sample_df)
        assert result["signal"] == "HOLD"
        assert "無効" in result["reasons_summary"][0]


# --- DCF Helper Function Tests ---

class TestDCFHelpers:
    def test_extract_fcf(self):
        from agents.dcf import _extract_fcf

        cf = _make_cash_flow()
        fcf_list = _extract_fcf(cf)
        assert len(fcf_list) == 3
        # FCF = Operating CF + CapEx (negative)
        assert fcf_list[0] == pytest.approx(3e11)  # 4e11 + (-1e11)

    def test_get_net_debt(self):
        from agents.dcf import _get_net_debt

        bs = _make_balance_sheet()
        net_debt = _get_net_debt(bs)
        # Total Debt (1e12) - Cash (5e11) = 5e11
        assert net_debt == pytest.approx(5e11)

    def test_get_shares_outstanding(self):
        from agents.dcf import _get_shares_outstanding

        assert _get_shares_outstanding({"sharesOutstanding": 1e9}) == 1e9
        assert _get_shares_outstanding({}) == 0.0

    def test_estimate_growth_rate(self):
        from agents.dcf import _estimate_growth_rate

        # Increasing FCF
        assert _estimate_growth_rate([300, 260, 220]) > 0
        # Single value
        assert _estimate_growth_rate([100]) == 0.05
        # Capped at 20%
        assert _estimate_growth_rate([1000, 100]) <= 0.20

    def test_extract_fcf_empty(self):
        from agents.dcf import _extract_fcf

        assert _extract_fcf(pd.DataFrame()) == []
        assert _extract_fcf(None) == []
