#!/usr/bin/env python3
"""
改善後戦略のバックテスト（2026-02-01 〜 今日）
旧戦略（RSI<70, 制限なし）と新戦略（RSI 50-65, セクター/日次/cooldown制限）を比較する。
"""
import numpy as np
import pandas as pd
import yfinance as yf
import yaml
import os
from datetime import datetime, timedelta, date
from collections import defaultdict
from strategy import calculate_sma, calculate_rsi
from nikkei225 import NIKKEI_225, get_sector


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def download_data(tickers, start="2025-10-01"):
    """全銘柄のデータを一括ダウンロード（SMA200用に余裕を持って取得）"""
    print(f"データダウンロード中: {len(tickers)}銘柄...")
    data = {}
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            df = yf.download(batch, start=start, progress=False, group_by="ticker", threads=True)
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        ticker_df = df
                    else:
                        ticker_df = df[ticker]
                    if ticker_df is not None and not ticker_df.empty and len(ticker_df) > 50:
                        data[ticker] = ticker_df.dropna()
                except Exception:
                    pass
        except Exception as e:
            print(f"  バッチエラー: {e}")
        print(f"  {min(i + batch_size, len(tickers))}/{len(tickers)}")
    print(f"有効データ: {len(data)}銘柄")
    return data


def run_strategy_backtest(all_data, config, strategy_label, rsi_min, rsi_max,
                          max_daily, max_sector, cooldown_days,
                          sim_start="2026-02-01", initial_balance=300000):
    """指定パラメータで戦略をバックテストする。"""
    strat = config["strategy"]
    account = config["account"]
    max_positions = account["max_positions"]
    profit_tighten_pct = strat.get("profit_tighten_pct", 0.03)
    profit_tighten_trail = strat.get("profit_tighten_trail", 0.04)
    profit_take_pct = strat.get("profit_take_pct", 0.07)
    profit_take_ratio = strat.get("profit_take_ratio", 0.5)

    # インジケーター事前計算
    indicators = {}
    for ticker, df in all_data.items():
        close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
        if len(close) < strat["sma_trend"] + 50:
            continue
        sma_short = calculate_sma(close, strat["sma_short"])
        sma_long = calculate_sma(close, strat["sma_long"])
        sma_trend = calculate_sma(close, strat["sma_trend"])
        rsi = calculate_rsi(close, strat["rsi_period"])
        indicators[ticker] = {
            "close": close,
            "sma_short": sma_short,
            "sma_long": sma_long,
            "sma_trend": sma_trend,
            "rsi": rsi,
        }

    # 共通の取引日リストを作成
    sample = list(indicators.values())[0]
    all_dates = sample["close"].index
    sim_start_dt = pd.Timestamp(sim_start)
    start_idx = 0
    for i, d in enumerate(all_dates):
        if d >= sim_start_dt:
            start_idx = i
            break

    balance = initial_balance
    positions = {}  # {ticker: {price, shares, date, stop_price, high_price, partial_exit_done}}
    trades = []
    equity_curve = []
    cooldown_map = {}  # {ticker: exit_date}

    for idx in range(start_idx, len(all_dates)):
        cur_date = all_dates[idx]
        daily_entries = 0

        # 保有銘柄の管理（毎日）
        for ticker in list(positions.keys()):
            if ticker not in indicators:
                continue
            ind = indicators[ticker]
            if idx >= len(ind["close"]):
                continue
            pos = positions[ticker]
            price = float(ind["close"].iloc[idx])
            entry_price = pos["price"]
            gain_pct = (price - entry_price) / entry_price

            # 高値更新 → ストップ引き上げ
            if price > pos["high_price"]:
                pos["high_price"] = price
                if gain_pct >= profit_tighten_pct:
                    pos["stop_price"] = round(price * (1 - profit_tighten_trail), 1)
                else:
                    pos["stop_price"] = round(price * 0.95, 1)

            # 利確（+7%で半分）
            if gain_pct >= profit_take_pct and not pos.get("partial_exit_done"):
                exit_shares = max(1, int(pos["shares"] * profit_take_ratio))
                if 0 < exit_shares < pos["shares"]:
                    pnl = (price - entry_price) * exit_shares
                    balance += price * exit_shares
                    trades.append({
                        "ticker": ticker, "name": NIKKEI_225.get(ticker, ticker),
                        "entry_date": pos["date"].strftime("%Y-%m-%d"),
                        "exit_date": cur_date.strftime("%Y-%m-%d"),
                        "entry_price": round(entry_price, 1),
                        "exit_price": round(price, 1),
                        "shares": exit_shares,
                        "pnl": round(pnl, 1),
                        "pnl_pct": round(gain_pct * 100, 2),
                        "reason": "利確（+7%）",
                    })
                    pos["shares"] -= exit_shares
                    pos["partial_exit_done"] = True

            # 損切り
            if price <= pos["stop_price"]:
                pnl = (price - entry_price) * pos["shares"]
                balance += price * pos["shares"]
                reason = "損切り" if gain_pct < 0 else "トレーリングストップ"
                trades.append({
                    "ticker": ticker, "name": NIKKEI_225.get(ticker, ticker),
                    "entry_date": pos["date"].strftime("%Y-%m-%d"),
                    "exit_date": cur_date.strftime("%Y-%m-%d"),
                    "entry_price": round(entry_price, 1),
                    "exit_price": round(price, 1),
                    "shares": pos["shares"],
                    "pnl": round(pnl, 1),
                    "pnl_pct": round(gain_pct * 100, 2),
                    "reason": reason,
                })
                cooldown_map[ticker] = cur_date
                del positions[ticker]
                continue

            # デッドクロス or RSI過熱 → 売り
            sma_s = float(ind["sma_short"].iloc[idx])
            sma_l = float(ind["sma_long"].iloc[idx])
            rsi_val = float(ind["rsi"].iloc[idx])
            if sma_s < sma_l or rsi_val > 75:
                pnl = (price - entry_price) * pos["shares"]
                balance += price * pos["shares"]
                reason = "デッドクロス" if sma_s < sma_l else "RSI過熱"
                trades.append({
                    "ticker": ticker, "name": NIKKEI_225.get(ticker, ticker),
                    "entry_date": pos["date"].strftime("%Y-%m-%d"),
                    "exit_date": cur_date.strftime("%Y-%m-%d"),
                    "entry_price": round(entry_price, 1),
                    "exit_price": round(price, 1),
                    "shares": pos["shares"],
                    "pnl": round(pnl, 1),
                    "pnl_pct": round(gain_pct * 100, 2),
                    "reason": reason,
                })
                del positions[ticker]

        # 買いシグナルスキャン（毎日）
        buy_candidates = []
        for ticker, ind in indicators.items():
            if ticker in positions:
                continue
            if idx >= len(ind["close"]):
                continue
            try:
                price = float(ind["close"].iloc[idx])
                sma_s = float(ind["sma_short"].iloc[idx])
                sma_l = float(ind["sma_long"].iloc[idx])
                sma_t = float(ind["sma_trend"].iloc[idx])
                rsi_val = float(ind["rsi"].iloc[idx])
                if np.isnan(sma_t) or np.isnan(rsi_val):
                    continue
                if sma_s > sma_l and rsi_min <= rsi_val <= rsi_max and price > sma_t:
                    buy_candidates.append({
                        "ticker": ticker, "price": price, "rsi": rsi_val,
                    })
            except Exception:
                continue

        # RSI低い順にソート
        buy_candidates.sort(key=lambda x: x["rsi"])

        # セクター別保有数
        sector_counts = defaultdict(int)
        for t in positions:
            sector_counts[get_sector(t)] += 1

        # エントリー実行
        for cand in buy_candidates:
            if daily_entries >= max_daily:
                break
            if len(positions) >= max_positions:
                break

            ticker = cand["ticker"]

            # cooldownチェック
            if ticker in cooldown_map:
                exit_dt = cooldown_map[ticker]
                if (cur_date - exit_dt).days < cooldown_days:
                    continue

            # セクター制限
            sec = get_sector(ticker)
            if sector_counts[sec] >= max_sector:
                continue

            price = cand["price"]
            stop_price = price * 0.95
            risk_amount = balance * account["risk_per_trade"]
            loss_per_share = price - stop_price
            if loss_per_share <= 0:
                continue
            shares = max(1, int(risk_amount / loss_per_share))
            max_cost = balance * account.get("max_allocation", 0.10)
            shares = min(shares, max(1, int(max_cost / price)))
            cost = price * shares

            if cost <= balance:
                positions[ticker] = {
                    "price": price, "shares": shares, "date": cur_date,
                    "stop_price": stop_price, "high_price": price,
                    "partial_exit_done": False,
                }
                balance -= cost
                sector_counts[sec] += 1
                daily_entries += 1

        # 日次資産推移
        portfolio_value = balance
        for ticker, pos in positions.items():
            if ticker in indicators:
                ind = indicators[ticker]
                if idx < len(ind["close"]):
                    portfolio_value += float(ind["close"].iloc[idx]) * pos["shares"]
        equity_curve.append({"date": cur_date, "equity": portfolio_value})

    # 未決済クローズ
    for ticker, pos in list(positions.items()):
        if ticker in indicators:
            ind = indicators[ticker]
            price = float(ind["close"].iloc[-1])
            pnl = (price - pos["price"]) * pos["shares"]
            balance += price * pos["shares"]
            trades.append({
                "ticker": ticker, "name": NIKKEI_225.get(ticker, ticker),
                "entry_date": pos["date"].strftime("%Y-%m-%d"),
                "exit_date": all_dates[-1].strftime("%Y-%m-%d"),
                "entry_price": round(pos["price"], 1),
                "exit_price": round(price, 1),
                "shares": pos["shares"],
                "pnl": round(pnl, 1),
                "pnl_pct": round((price / pos["price"] - 1) * 100, 2),
                "reason": "期間終了（未決済）",
            })

    # サマリー
    final_equity = balance
    total_return = ((final_equity / initial_balance) - 1) * 100
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equities = [e["equity"] for e in equity_curve]
    peak = equities[0]
    max_dd = 0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd

    return {
        "label": strategy_label,
        "initial": initial_balance,
        "final": round(final_equity, 0),
        "return_pct": round(total_return, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_dd": round(max_dd, 2),
        "pf": round(pf, 2),
        "total_pnl": round(sum(t["pnl"] for t in trades), 0),
        "trade_details": trades,
        "equity_curve": equity_curve,
    }


def fetch_benchmark(ticker, start, end=None):
    """ベンチマークのリターンを計算する。"""
    try:
        df = yf.download(ticker, start=start, end=end, progress=False)
        if df.empty or len(df) < 2:
            return None
        close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
        start_price = float(close.iloc[0])
        end_price = float(close.iloc[-1])
        return round((end_price / start_price - 1) * 100, 2)
    except Exception:
        return None


def main():
    config = load_config()
    tickers = list(NIKKEI_225.keys())

    # 1年/2年/3年のテスト期間定義
    periods = [
        ("1年", "2025-03-16", "2022-03-01"),
        ("2年", "2024-03-16", "2021-03-01"),
        ("3年", "2023-03-16", "2020-03-01"),
    ]

    for period_label, sim_start, data_start in periods:
        print(f"\n{'=' * 70}")
        print(f"  バックテスト: {period_label}（{sim_start} 〜 今日）")
        print(f"{'=' * 70}")

        # データダウンロード
        all_data = download_data(tickers, start=data_start)

        # 新戦略
        print(f"\n  新戦略（RSI 50-65 + 制限）を実行中...")
        new = run_strategy_backtest(
            all_data, config, "新戦略",
            rsi_min=50, rsi_max=65,
            max_daily=3, max_sector=2, cooldown_days=7,
            sim_start=sim_start,
        )

        # ベンチマーク取得
        print(f"  ベンチマーク取得中...")
        nk225 = fetch_benchmark("^N225", sim_start)
        sp500 = fetch_benchmark("^GSPC", sim_start)
        allcountry = fetch_benchmark("ACWI", sim_start)  # iShares MSCI ACWI ETF

        # 結果表示
        print(f"\n{'─' * 70}")
        fmt = "  {:<20} {:>12}"
        print(fmt.format("", period_label))
        print(f"{'─' * 70}")
        print(fmt.format("新戦略リターン", f"{new['return_pct']:+.2f}%"))
        print(fmt.format("新戦略 最終資産", f"¥{new['final']:,.0f}"))
        print(fmt.format("トレード数", f"{new['trades']}件"))
        print(fmt.format("勝率", f"{new['win_rate']}%"))
        print(fmt.format("平均利益", f"{new['avg_win']:+.2f}%"))
        print(fmt.format("平均損失", f"{new['avg_loss']:+.2f}%"))
        print(fmt.format("最大DD", f"{new['max_dd']:.2f}%"))
        print(fmt.format("PF", f"{new['pf']:.2f}"))
        print(f"{'─' * 70}")
        print(fmt.format("日経225", f"{nk225:+.2f}%" if nk225 is not None else "N/A"))
        print(fmt.format("S&P500", f"{sp500:+.2f}%" if sp500 is not None else "N/A"))
        print(fmt.format("オルカン(ACWI)", f"{allcountry:+.2f}%" if allcountry is not None else "N/A"))

        # α（新戦略 - 日経225）
        if nk225 is not None:
            alpha = round(new["return_pct"] - nk225, 2)
            print(f"{'─' * 70}")
            print(fmt.format("α（vs 日経225）", f"{alpha:+.2f}%"))
        print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
