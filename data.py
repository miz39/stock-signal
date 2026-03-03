import yfinance as yf
import pandas as pd


def fetch_stock_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """yfinanceで株価データを取得し、pandas DataFrameで返す。"""
    stock = yf.Ticker(ticker)
    df = stock.history(period=period)
    if df.empty:
        raise ValueError(f"{ticker} のデータを取得できませんでした")
    return df
