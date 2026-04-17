"""
Data provider abstraction layer.
Supports yfinance (default) and J-Quants API as data sources.
"""
import logging
import os
import time
from typing import Optional, Protocol, runtime_checkable

import pandas as pd
import yaml

logger = logging.getLogger("signal")

MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2]
MIN_ROWS = 20

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@runtime_checkable
class DataProvider(Protocol):
    """Protocol for stock data providers."""

    def fetch_ohlcv(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        """Fetch OHLCV data. Returns DataFrame with Open/High/Low/Close/Volume columns."""
        ...

    def fetch_financial_data(self, ticker: str) -> dict:
        """Fetch fundamental/financial data for a ticker."""
        ...


class YFinanceProvider:
    """Default data provider using yfinance."""

    def fetch_ohlcv(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        import yfinance as yf

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                stock = yf.Ticker(ticker)
                df = stock.history(period=period, timeout=10)
                if df.empty:
                    raise ValueError(f"{ticker} のデータを取得できませんでした")
                if len(df) < MIN_ROWS and period not in ("5d", "1d"):
                    raise ValueError(f"{ticker} のデータが不十分です（{len(df)}行 < {MIN_ROWS}行）")
                return df
            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_SECONDS[attempt])
        raise last_error

    def fetch_financial_data(self, ticker: str) -> dict:
        import yfinance as yf

        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return {
                "source": "yfinance",
                "per": info.get("trailingPE") or info.get("forwardPE"),
                "pbr": info.get("priceToBook"),
                "roe": info.get("returnOnEquity"),
                "dividend_yield": info.get("dividendYield"),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "market_cap": info.get("marketCap"),
                "equity_ratio": None,
                "debt_equity_ratio": info.get("debtToEquity"),
                "operating_margin": info.get("operatingMargins"),
                "next_earnings_date": None,
                "raw_info": info,
            }
        except Exception as e:
            logger.warning(f"yfinance financial data fetch failed for {ticker}: {e}")
            return {"source": "yfinance", "error": str(e)}


class JQuantsProvider:
    """Data provider using J-Quants API for Japanese stocks."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("JQUANTS_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import jquantsapi
            except ImportError:
                raise ImportError(
                    "jquants-api-client is required for J-Quants provider. "
                    "Install with: pip install jquants-api-client"
                )
            if not self._api_key:
                raise ValueError("JQUANTS_API_KEY is required for J-Quants provider")
            self._client = jquantsapi.Client(refresh_token=self._api_key)
        return self._client

    def _ticker_to_code(self, ticker: str) -> str:
        """Convert yfinance-style ticker (e.g. '7203.T') to J-Quants code (e.g. '72030')."""
        code = ticker.replace(".T", "")
        if len(code) == 4:
            code = code + "0"
        return code

    def fetch_ohlcv(self, ticker: str, period: str = "1y") -> pd.DataFrame:
        if ticker.startswith("^"):
            # Index data — fall back to yfinance
            yf_provider = YFinanceProvider()
            return yf_provider.fetch_ohlcv(ticker, period)

        client = self._get_client()
        code = self._ticker_to_code(ticker)

        period_days = {"1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}
        days = period_days.get(period, 365)

        from datetime import datetime, timedelta
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                df = client.get_prices_daily_quotes(
                    code=code,
                    from_yyyymmdd=start_date.strftime("%Y%m%d"),
                    to_yyyymmdd=end_date.strftime("%Y%m%d"),
                )
                if df.empty:
                    raise ValueError(f"{ticker} のデータを取得できませんでした (J-Quants)")

                # Normalize column names to match yfinance format
                column_map = {
                    "AdjustmentClose": "Close",
                    "AdjustmentOpen": "Open",
                    "AdjustmentHigh": "High",
                    "AdjustmentLow": "Low",
                    "AdjustmentVolume": "Volume",
                }
                df = df.rename(columns=column_map)

                # Ensure required columns exist
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    if col not in df.columns:
                        raise ValueError(f"J-Quants data missing column: {col}")

                # Set date as index
                if "Date" in df.columns:
                    df["Date"] = pd.to_datetime(df["Date"])
                    df = df.set_index("Date")
                    df = df.sort_index()

                if len(df) < MIN_ROWS and period not in ("5d", "1d"):
                    raise ValueError(f"{ticker} のデータが不十分です（{len(df)}行 < {MIN_ROWS}行）")

                return df[["Open", "High", "Low", "Close", "Volume"]]

            except Exception as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_SECONDS[attempt])
        raise last_error

    def fetch_financial_data(self, ticker: str) -> dict:
        try:
            client = self._get_client()
            code = self._ticker_to_code(ticker)

            fin = client.get_fins_statements(code=code)
            if fin.empty:
                return {"source": "jquants", "error": "No financial data"}

            latest = fin.iloc[-1]

            # Extract key metrics
            revenue = latest.get("NetSales")
            op_income = latest.get("OperatingProfit")
            net_income = latest.get("Profit")
            total_assets = latest.get("TotalAssets")
            equity = latest.get("Equity")
            eps = latest.get("EarningsPerShare")
            dividend = latest.get("DividendPerShare")

            # Calculate ratios
            roe = None
            if net_income and equity and equity > 0:
                roe = net_income / equity

            equity_ratio = None
            if equity and total_assets and total_assets > 0:
                equity_ratio = equity / total_assets

            operating_margin = None
            if op_income and revenue and revenue > 0:
                operating_margin = op_income / revenue

            # Revenue growth (compare with previous period if available)
            revenue_growth = None
            if len(fin) >= 2:
                prev_revenue = fin.iloc[-2].get("NetSales")
                if prev_revenue and prev_revenue > 0 and revenue:
                    revenue_growth = (revenue - prev_revenue) / prev_revenue

            # Next earnings date
            next_earnings_date = None
            announce_date = latest.get("NextEarningsDate") or latest.get("DisclosedDate")
            if announce_date:
                next_earnings_date = str(announce_date)

            return {
                "source": "jquants",
                "per": None,  # Needs price data to calculate
                "pbr": None,  # Needs price data to calculate
                "roe": roe,
                "dividend_yield": None,  # Needs price data
                "revenue_growth": revenue_growth,
                "earnings_growth": None,
                "market_cap": None,
                "equity_ratio": equity_ratio,
                "debt_equity_ratio": None,
                "operating_margin": operating_margin,
                "next_earnings_date": next_earnings_date,
                "eps": eps,
                "dividend_per_share": dividend,
                "revenue": revenue,
                "operating_income": op_income,
                "net_income": net_income,
                "total_assets": total_assets,
                "equity": equity,
                "raw_fin": latest.to_dict() if hasattr(latest, "to_dict") else {},
            }
        except Exception as e:
            logger.warning(f"J-Quants financial data fetch failed for {ticker}: {e}")
            return {"source": "jquants", "error": str(e)}


# --- Provider instance management ---

_provider: Optional[DataProvider] = None


def _load_provider_config() -> dict:
    config_path = os.path.join(_BASE_DIR, "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        return config.get("data", {})
    return {}


def get_provider() -> DataProvider:
    """Get or create the configured data provider."""
    global _provider
    if _provider is not None:
        return _provider

    data_cfg = _load_provider_config()
    provider_name = data_cfg.get("provider", "yfinance")

    if provider_name == "jquants":
        api_key = data_cfg.get("jquants_api_key") or os.environ.get("JQUANTS_API_KEY", "")
        _provider = JQuantsProvider(api_key=api_key)
        logger.info("Data provider: J-Quants API")
    else:
        _provider = YFinanceProvider()

    return _provider


def set_provider(provider: DataProvider) -> None:
    """Override the data provider (useful for testing)."""
    global _provider
    _provider = provider


def reset_provider() -> None:
    """Reset provider to force re-initialization from config."""
    global _provider
    _provider = None


# --- Public API (backward-compatible) ---

def fetch_stock_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """yfinanceで株価データを取得し、pandas DataFrameで返す。

    最大3回リトライ（backoff 1s, 2s）。取得後に最低行数を検証する。
    """
    return get_provider().fetch_ohlcv(ticker, period)


def fetch_financial_data(ticker: str) -> dict:
    """Fetch fundamental/financial data for a ticker.

    Returns dict with standardized keys:
        source, per, pbr, roe, dividend_yield, revenue_growth,
        earnings_growth, market_cap, equity_ratio, operating_margin, etc.
    """
    return get_provider().fetch_financial_data(ticker)


_earnings_date_cache: dict = {}


def fetch_earnings_date(ticker: str):
    """Fetch the next earnings date for a ticker.

    Returns datetime.date or None if unknown / fetch failed.
    Results are cached in-memory for the lifetime of the process.
    """
    from datetime import date as _date
    if ticker in _earnings_date_cache:
        return _earnings_date_cache[ticker]

    result = None
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict):
            edates = cal.get("Earnings Date") or []
            if edates:
                first = edates[0] if isinstance(edates, list) else edates
                if isinstance(first, _date):
                    result = first
    except Exception as e:  # pragma: no cover - network dependent
        logging.getLogger("data").warning(
            f"fetch_earnings_date({ticker}) failed: {e}"
        )

    _earnings_date_cache[ticker] = result
    return result


def fetch_financial_statements(ticker: str) -> dict:
    """Fetch multi-year financial statements from yfinance.

    Returns dict with keys:
        income_statement: DataFrame (annual P&L)
        balance_sheet: DataFrame (annual BS)
        cash_flow: DataFrame (annual CF)
        info: dict (shares outstanding, etc.)

    DataFrames have columns as fiscal year dates, rows as line items.
    NaN values are preserved for downstream handling.
    """
    import yfinance as yf

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            stock = yf.Ticker(ticker)
            result = {
                "income_statement": stock.financials,
                "balance_sheet": stock.balance_sheet,
                "cash_flow": stock.cashflow,
                "info": stock.info or {},
            }
            # Validate at least one statement has data
            has_data = any(
                isinstance(v, pd.DataFrame) and not v.empty
                for k, v in result.items() if k != "info"
            )
            if not has_data:
                raise ValueError(f"{ticker} の財務データを取得できませんでした")
            return result
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS[attempt])
    raise last_error
