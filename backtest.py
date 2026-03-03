"""
バックテスト — 過去データで戦略をシミュレーションする。
"""
import pandas as pd
import numpy as np
from data import fetch_stock_data
from strategy import calculate_sma, calculate_rsi


def run_backtest(ticker, config, period="3y", initial_balance=100000):
    """
    過去データで売買シミュレーションを実行する。

    Returns:
        dict: trades, summary, equity_curve
    """
    strat = config["strategy"]
    account = config["account"]

    df = fetch_stock_data(ticker, period=period)
    close = df["Close"]

    sma_short = calculate_sma(close, strat["sma_short"])
    sma_long = calculate_sma(close, strat["sma_long"])
    sma_trend = calculate_sma(close, strat["sma_trend"])
    rsi = calculate_rsi(close, strat["rsi_period"])

    balance = initial_balance
    position = None  # {"price": x, "shares": n, "date": d}
    trades = []
    equity_curve = []

    for i in range(strat["sma_trend"], len(close)):
        date = close.index[i]
        price = float(close.iloc[i])
        s_short = float(sma_short.iloc[i])
        s_long = float(sma_long.iloc[i])
        s_trend = float(sma_trend.iloc[i])
        rsi_val = float(rsi.iloc[i])

        if np.isnan(s_trend) or np.isnan(rsi_val):
            equity = balance + (position["shares"] * price if position else 0)
            equity_curve.append({"date": date, "equity": equity})
            continue

        # 買いシグナル（ポジションなし時）
        if position is None:
            if s_short > s_long and rsi_val < strat["rsi_overbought"] and price > s_trend:
                # ポジションサイジング
                risk_amount = balance * account["risk_per_trade"]
                stop_price = price * 0.95
                loss_per_share = price - stop_price
                shares = max(1, int(risk_amount / loss_per_share))
                cost = price * shares

                if cost <= balance:
                    position = {
                        "entry_price": price,
                        "shares": shares,
                        "entry_date": date,
                        "stop_price": stop_price,
                    }
                    balance -= cost

        # 売りシグナル or 損切り（ポジションあり時）
        elif position is not None:
            sell = False
            reason = ""

            # 損切り
            if price <= position["stop_price"]:
                sell = True
                reason = "損切り（-5%）"
            # デッドクロス
            elif s_short < s_long:
                sell = True
                reason = "デッドクロス"
            # RSI過熱
            elif rsi_val > 75:
                sell = True
                reason = "RSI過熱"

            if sell:
                pnl = (price - position["entry_price"]) * position["shares"]
                pnl_pct = (price / position["entry_price"] - 1) * 100
                balance += price * position["shares"]

                trades.append({
                    "entry_date": position["entry_date"].strftime("%Y-%m-%d"),
                    "exit_date": date.strftime("%Y-%m-%d"),
                    "entry_price": round(position["entry_price"], 1),
                    "exit_price": round(price, 1),
                    "shares": position["shares"],
                    "pnl": round(pnl, 1),
                    "pnl_pct": round(pnl_pct, 2),
                    "reason": reason,
                    "days_held": (date - position["entry_date"]).days,
                })
                position = None

        equity = balance + (position["shares"] * price if position else 0)
        equity_curve.append({"date": date, "equity": equity})

    # 未決済ポジションがあればクローズ
    if position:
        price = float(close.iloc[-1])
        pnl = (price - position["entry_price"]) * position["shares"]
        pnl_pct = (price / position["entry_price"] - 1) * 100
        balance += price * position["shares"]
        trades.append({
            "entry_date": position["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": close.index[-1].strftime("%Y-%m-%d"),
            "entry_price": round(position["entry_price"], 1),
            "exit_price": round(price, 1),
            "shares": position["shares"],
            "pnl": round(pnl, 1),
            "pnl_pct": round(pnl_pct, 2),
            "reason": "期間終了（未決済）",
            "days_held": (close.index[-1] - position["entry_date"]).days,
        })
        position = None

    # サマリー計算
    final_equity = balance
    total_return = ((final_equity / initial_balance) - 1) * 100

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0

    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    avg_days = np.mean([t["days_held"] for t in trades]) if trades else 0

    # 最大ドローダウン
    equities = [e["equity"] for e in equity_curve]
    peak = equities[0]
    max_dd = 0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd

    # プロフィットファクター
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    summary = {
        "initial_balance": initial_balance,
        "final_balance": round(final_equity, 0),
        "total_return_pct": round(total_return, 2),
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "avg_days_held": round(avg_days, 1),
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor": round(profit_factor, 2),
    }

    return {
        "ticker": ticker,
        "period": period,
        "trades": trades,
        "summary": summary,
    }
