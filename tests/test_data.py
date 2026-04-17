from datetime import date
from unittest.mock import patch, MagicMock

import data


class TestFetchEarningsDate:
    def setup_method(self):
        # Reset cache between tests
        data._earnings_date_cache.clear()

    def test_returns_date_when_calendar_has_earnings(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Earnings Date": [date(2026, 5, 8)]}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        assert result == date(2026, 5, 8)

    def test_returns_none_when_calendar_missing_key(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Ex-Dividend Date": date(2026, 3, 30)}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        assert result is None

    def test_returns_none_when_calendar_is_none(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = None
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        assert result is None

    def test_returns_none_when_earnings_date_empty_list(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Earnings Date": []}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        assert result is None

    def test_handles_yfinance_exception(self):
        with patch("yfinance.Ticker", side_effect=RuntimeError("network error")):
            result = data.fetch_earnings_date("7203.T")
        assert result is None

    def test_caches_result_across_calls(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Earnings Date": [date(2026, 5, 8)]}
        with patch("yfinance.Ticker", return_value=mock_ticker) as mock_yf:
            data.fetch_earnings_date("7203.T")
            data.fetch_earnings_date("7203.T")
            data.fetch_earnings_date("7203.T")
        # Should only call yfinance once due to caching
        assert mock_yf.call_count == 1

    def test_caches_none_result(self):
        with patch("yfinance.Ticker", side_effect=RuntimeError("err")) as mock_yf:
            data.fetch_earnings_date("7203.T")
            data.fetch_earnings_date("7203.T")
        # Should not retry on failure (cached as None)
        assert mock_yf.call_count == 1

    def test_returns_first_when_list_has_multiple(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {
            "Earnings Date": [date(2026, 5, 8), date(2026, 8, 7)]
        }
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        assert result == date(2026, 5, 8)

    def test_handles_scalar_date_value(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Earnings Date": date(2026, 5, 8)}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        assert result == date(2026, 5, 8)

    def test_ignores_non_date_value(self):
        mock_ticker = MagicMock()
        mock_ticker.calendar = {"Earnings Date": ["2026-05-08"]}
        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = data.fetch_earnings_date("7203.T")
        # Strings should not be returned (only datetime.date)
        assert result is None
