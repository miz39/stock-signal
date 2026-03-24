import time

import yfinance as yf
import pandas as pd

MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2]
MIN_ROWS = 20


def fetch_stock_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """yfinanceで株価データを取得し、pandas DataFrameで返す。

    最大3回リトライ（backoff 1s, 2s）。取得後に最低行数を検証する。
    """
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
