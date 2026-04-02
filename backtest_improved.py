#!/usr/bin/env python3
"""
Standalone strategy comparison tool — run directly: python3 backtest_improved.py

Modes:
  python3 backtest_improved.py                  # Profile comparison (default)
  python3 backtest_improved.py --mode compare   # Same as above
  python3 backtest_improved.py --mode sensitivity  # Parameter grid search

Compares strategy profiles with Nikkei 225 index benchmark.
"""
import argparse
import numpy as np
import pandas as pd
import yfinance as yf
import yaml
import os
from collections import defaultdict
from strategy import calculate_sma, calculate_rsi
from nikkei225 import NIKKEI_225, get_sector


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

SLIPPAGE = 0.005  # S-stock spread: 0.5%


def build_profile_strategy(config, profile_name="default"):
    """Build strategy_params dict from config.yaml profile."""
    strat = dict(config.get("strategy", {}))
    if profile_name != "default":
        overrides = config.get("profiles", {}).get(profile_name, {}).get("strategy", {})
        strat.update(overrides)
    return {
        "label": f"{profile_name}（-{int(strat['stop_loss_pct']*100)}%ストップ）",
        "stop_loss_pct": strat["stop_loss_pct"],
        "trailing_mode": "breakeven",
        "profit_tighten_pct": strat.get("profit_tighten_pct", 0.06),
        "default_trail": strat["stop_loss_pct"],
        "profit_take_pct": strat.get("profit_take_pct", 0.08),
        "profit_take_ratio": strat.get("profit_take_ratio", 0.5),
        "profit_take_full_pct": strat.get("profit_take_full_pct", 0.15),
    }


# Strategy parameter presets
OLD_STRATEGY = {
    "label": "旧戦略（-5%ストップ）",
    "stop_loss_pct": 0.05,
    "trailing_mode": "percentage",   # trail by fixed pct from high
    "profit_tighten_pct": 0.03,      # +3% gain → tighten trail
    "profit_tighten_trail": 0.04,    # tightened trail -4%
    "default_trail": 0.05,           # default trail -5%
    "profit_take_pct": 0.07,         # +7% → partial exit
    "profit_take_ratio": 0.5,
    "profit_take_full_pct": None,    # no full profit exit
}

NEW_STRATEGY = {
    "label": "新戦略（-8%+建値移動）",
    "stop_loss_pct": 0.08,
    "trailing_mode": "breakeven",    # at threshold, move stop to entry
    "profit_tighten_pct": 0.06,     # +6% → move stop to breakeven
    "default_trail": 0.08,          # trail -8% from high
    "profit_take_pct": 0.08,        # +8% → partial exit
    "profit_take_ratio": 0.5,
    "profit_take_full_pct": 0.15,   # +15% → full exit
}


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def download_data(tickers, start="2024-03-01"):
    """Download price data for all tickers (with SMA200 warmup)."""
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


def run_strategy_backtest(all_data, config, strategy_params,
                          rsi_min=50, rsi_max=65,
                          max_daily=3, max_sector=2, cooldown_days=7,
                          sim_start="2026-01-01", initial_balance=300000,
                          slippage=0.0):
    """Run backtest with given strategy parameters."""
    strat = config["strategy"]
    account = config["account"]
    max_positions = account["max_positions"]
    sp = strategy_params

    # Pre-compute indicators
    indicators = {}
    for ticker, df in all_data.items():
        close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
        if len(close) < strat["sma_trend"] + 50:
            continue
        indicators[ticker] = {
            "close": close,
            "sma_short": calculate_sma(close, strat["sma_short"]),
            "sma_long": calculate_sma(close, strat["sma_long"]),
            "sma_trend": calculate_sma(close, strat["sma_trend"]),
            "rsi": calculate_rsi(close, strat["rsi_period"]),
        }

    if not indicators:
        return _empty_result(sp["label"], initial_balance)

    sample = list(indicators.values())[0]
    all_dates = sample["close"].index
    sim_start_dt = pd.Timestamp(sim_start)
    start_idx = 0
    for i, d in enumerate(all_dates):
        if d >= sim_start_dt:
            start_idx = i
            break

    balance = initial_balance
    positions = {}
    trades = []
    equity_curve = []
    cooldown_map = {}

    for idx in range(start_idx, len(all_dates)):
        cur_date = all_dates[idx]
        daily_entries = 0

        # === Position management ===
        for ticker in list(positions.keys()):
            if ticker not in indicators:
                continue
            ind = indicators[ticker]
            if idx >= len(ind["close"]):
                continue
            pos = positions[ticker]
            market_price = float(ind["close"].iloc[idx])
            sell_price = market_price * (1 - slippage)  # Sell at bid
            entry_price = pos["price"]
            price = market_price  # Use market price for stop/trail logic
            gain_pct = (sell_price - entry_price) / entry_price

            # --- Stop / trail logic ---
            if sp["trailing_mode"] == "percentage":
                # Old: trail by fixed pct, tighten at threshold
                if price > pos["high_price"]:
                    pos["high_price"] = price
                    if gain_pct >= sp["profit_tighten_pct"]:
                        pos["stop_price"] = round(price * (1 - sp["profit_tighten_trail"]), 1)
                    else:
                        pos["stop_price"] = round(price * (1 - sp["default_trail"]), 1)
            else:
                # New: breakeven at threshold, then trail (stop never goes down)
                if gain_pct >= sp["profit_tighten_pct"] and not pos.get("breakeven_done"):
                    pos["stop_price"] = max(pos["stop_price"], entry_price)
                    pos["breakeven_done"] = True
                if price > pos["high_price"]:
                    pos["high_price"] = price
                    new_stop = round(price * (1 - sp["default_trail"]), 1)
                    if new_stop > pos["stop_price"]:
                        pos["stop_price"] = new_stop

            # --- Full profit exit ---
            if sp.get("profit_take_full_pct") and gain_pct >= sp["profit_take_full_pct"]:
                balance += sell_price * pos["shares"]
                trades.append(_make_trade(
                    ticker, pos, cur_date, sell_price, gain_pct, pos["shares"],
                    f"全利確（+{int(sp['profit_take_full_pct'] * 100)}%）",
                ))
                del positions[ticker]
                continue

            # --- Partial profit exit ---
            if gain_pct >= sp["profit_take_pct"] and not pos.get("partial_exit_done"):
                if pos["shares"] == 1:
                    balance += sell_price
                    trades.append(_make_trade(
                        ticker, pos, cur_date, sell_price, gain_pct, 1,
                        f"利確（+{int(sp['profit_take_pct'] * 100)}%・1株）",
                    ))
                    del positions[ticker]
                    continue
                else:
                    exit_shares = max(1, int(pos["shares"] * sp["profit_take_ratio"]))
                    if 0 < exit_shares < pos["shares"]:
                        balance += sell_price * exit_shares
                        trades.append(_make_trade(
                            ticker, pos, cur_date, sell_price, gain_pct, exit_shares,
                            f"利確（+{int(sp['profit_take_pct'] * 100)}%）",
                        ))
                        pos["shares"] -= exit_shares
                        pos["partial_exit_done"] = True

            # --- Stop loss ---
            if ticker not in positions:
                continue
            if market_price <= pos["stop_price"]:
                balance += sell_price * pos["shares"]
                reason = "損切り" if gain_pct < 0 else "トレーリングストップ"
                trades.append(_make_trade(
                    ticker, pos, cur_date, sell_price, gain_pct, pos["shares"], reason,
                ))
                cooldown_map[ticker] = cur_date
                del positions[ticker]
                continue

            # --- Dead cross / RSI overheat ---
            sma_s = float(ind["sma_short"].iloc[idx])
            sma_l = float(ind["sma_long"].iloc[idx])
            rsi_val = float(ind["rsi"].iloc[idx])
            if sma_s < sma_l or rsi_val > 75:
                balance += sell_price * pos["shares"]
                reason = "デッドクロス" if sma_s < sma_l else "RSI過熱"
                trades.append(_make_trade(
                    ticker, pos, cur_date, sell_price, gain_pct, pos["shares"], reason,
                ))
                del positions[ticker]

        # === Buy signal scan ===
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
                    buy_candidates.append({"ticker": ticker, "price": price, "rsi": rsi_val})
            except Exception:
                continue

        buy_candidates.sort(key=lambda x: x["rsi"])

        sector_counts = defaultdict(int)
        for t in positions:
            sector_counts[get_sector(t)] += 1

        for cand in buy_candidates:
            if daily_entries >= max_daily:
                break
            if len(positions) >= max_positions:
                break
            ticker = cand["ticker"]
            if ticker in cooldown_map:
                if (cur_date - cooldown_map[ticker]).days < cooldown_days:
                    continue
            sec = get_sector(ticker)
            if sector_counts[sec] >= max_sector:
                continue

            price = cand["price"] * (1 + slippage)  # Buy at ask
            stop_price = round(price * (1 - sp["stop_loss_pct"]), 1)
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

        # Daily equity
        portfolio_value = balance
        for ticker, pos in positions.items():
            if ticker in indicators:
                ind = indicators[ticker]
                if idx < len(ind["close"]):
                    portfolio_value += float(ind["close"].iloc[idx]) * pos["shares"]
        equity_curve.append({"date": cur_date, "equity": portfolio_value})

    # Close remaining positions at last price
    for ticker, pos in list(positions.items()):
        if ticker in indicators:
            ind = indicators[ticker]
            price = float(ind["close"].iloc[-1])
            gain_pct = (price / pos["price"] - 1)
            balance += price * pos["shares"]
            trades.append(_make_trade(
                ticker, pos, all_dates[-1], price, gain_pct, pos["shares"],
                "期間終了（未決済）",
            ))

    return _summarize(sp["label"], initial_balance, balance, trades, equity_curve)


def _make_trade(ticker, pos, exit_date, exit_price, gain_pct, shares, reason):
    """Build a trade record dict."""
    return {
        "ticker": ticker,
        "name": NIKKEI_225.get(ticker, ticker),
        "entry_date": pos["date"].strftime("%Y-%m-%d"),
        "exit_date": exit_date.strftime("%Y-%m-%d"),
        "entry_price": round(pos["price"], 1),
        "exit_price": round(exit_price, 1),
        "shares": shares,
        "pnl": round((exit_price - pos["price"]) * shares, 1),
        "pnl_pct": round(gain_pct * 100, 2),
        "reason": reason,
    }


def _empty_result(label, initial_balance):
    """Return empty result when no data available."""
    return {
        "label": label, "initial": initial_balance, "final": initial_balance,
        "return_pct": 0, "trades": 0, "wins": 0, "losses": 0,
        "win_rate": 0, "avg_win": 0, "avg_loss": 0, "max_dd": 0,
        "pf": 0, "sharpe": 0, "sortino": 0, "calmar": 0,
        "max_consec_win": 0, "max_consec_loss": 0,
        "total_pnl": 0, "trade_details": [], "equity_curve": [],
    }


def _summarize(label, initial_balance, final_balance, trades, equity_curve):
    """Compute summary stats from trade list."""
    total_return = ((final_balance / initial_balance) - 1) * 100
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equities = [e["equity"] for e in equity_curve]
    peak = equities[0] if equities else initial_balance
    max_dd = 0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd

    # Sharpe / Sortino / Calmar from daily equity
    sharpe = 0.0
    sortino = 0.0
    calmar = 0.0
    if len(equities) > 2:
        eq_arr = np.array(equities)
        daily_returns = np.diff(eq_arr) / eq_arr[:-1]
        mean_r = np.mean(daily_returns)
        std_r = np.std(daily_returns, ddof=1)
        if std_r > 0:
            sharpe = round(mean_r / std_r * np.sqrt(252), 2)
        downside = daily_returns[daily_returns < 0]
        down_std = np.std(downside, ddof=1) if len(downside) > 1 else 0
        if down_std > 0:
            sortino = round(mean_r / down_std * np.sqrt(252), 2)
        if max_dd < 0:
            annual_return = total_return * 252 / len(equities)
            calmar = round(annual_return / abs(max_dd), 2)

    # Consecutive wins/losses
    max_consec_win = 0
    max_consec_loss = 0
    cur_win = 0
    cur_loss = 0
    for t in trades:
        if t["pnl"] > 0:
            cur_win += 1
            cur_loss = 0
            max_consec_win = max(max_consec_win, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_consec_loss = max(max_consec_loss, cur_loss)

    return {
        "label": label,
        "initial": initial_balance,
        "final": round(final_balance, 0),
        "return_pct": round(total_return, 2),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_dd": round(max_dd, 2),
        "pf": round(pf, 2),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_consec_win": max_consec_win,
        "max_consec_loss": max_consec_loss,
        "total_pnl": round(sum(t["pnl"] for t in trades), 0),
        "trade_details": trades,
        "equity_curve": equity_curve,
    }


def fetch_benchmark(ticker, start, end=None):
    """Fetch benchmark index return for the period."""
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


def print_multi_comparison(period_label, sim_start, results, nk225_return):
    """Print comparison table for multiple strategies."""
    w = 90
    n = len(results)
    col_w = max(16, 72 // n)

    print(f"\n{'=' * w}")
    print(f"  バックテスト比較: {period_label}（{sim_start} 〜 今日）  [スリッページ {SLIPPAGE*100:.1f}%]")
    print(f"{'=' * w}")

    nk = f"{nk225_return:+.2f}%" if nk225_return is not None else "N/A"

    metric_keys = [
        ("リターン", lambda r: f"{r['return_pct']:+.2f}%"),
        ("最終資産", lambda r: f"¥{r['final']:,.0f}"),
        ("損益合計", lambda r: f"¥{r['total_pnl']:+,.0f}"),
        ("トレード数", lambda r: f"{r['trades']}件"),
        ("勝敗", lambda r: f"{r['wins']}W/{r['losses']}L"),
        ("勝率", lambda r: f"{r['win_rate']}%"),
        ("平均利益", lambda r: f"{r['avg_win']:+.2f}%"),
        ("平均損失", lambda r: f"{r['avg_loss']:+.2f}%"),
        ("最大DD", lambda r: f"{r['max_dd']:.2f}%"),
        ("PF", lambda r: f"{r['pf']:.2f}"),
        ("Sharpe", lambda r: f"{r['sharpe']:.2f}"),
        ("Sortino", lambda r: f"{r['sortino']:.2f}"),
        ("最大連勝", lambda r: f"{r['max_consec_win']}"),
        ("最大連敗", lambda r: f"{r['max_consec_loss']}"),
    ]

    # Header
    header = f"  {'項目':<12}"
    for r in results:
        header += f"  {r['label']:>{col_w}}"
    header += f"  {'日経225':>10}"
    print(header)
    print(f"  {'─' * (w - 4)}")

    for label, fmt in metric_keys:
        line = f"  {label:<12}"
        for r in results:
            line += f"  {fmt(r):>{col_w}}"
        bench = nk if label == "リターン" else "—"
        line += f"  {bench:>10}"
        print(line)

    # Alpha
    if nk225_return is not None:
        print(f"  {'─' * (w - 4)}")
        line = f"  {'α(vs日経)':<12}"
        for r in results:
            alpha = round(r["return_pct"] - nk225_return, 2)
            line += f"  {alpha:>+{col_w}.2f}%"
        line += f"  {'—':>10}"
        print(line)

    print(f"{'=' * w}")

    # Exit reason breakdown
    for result in results:
        if not result["trade_details"]:
            continue
        print(f"\n  【{result['label']}】イグジット内訳:")
        reasons = defaultdict(lambda: {"count": 0, "pnl": 0})
        for t in result["trade_details"]:
            reasons[t["reason"]]["count"] += 1
            reasons[t["reason"]]["pnl"] += t["pnl"]
        for reason, info in sorted(reasons.items(), key=lambda x: -x[1]["count"]):
            print(f"    {reason}: {info['count']}件 (損益: ¥{info['pnl']:+,.0f})")


def run_sensitivity(all_data, config, sim_start="2026-01-01"):
    """Grid search over stop_loss_pct and profit_take_pct."""
    stop_range = [0.05, 0.08, 0.10, 0.12]
    profit_range = [0.06, 0.08, 0.10, 0.12]

    print(f"\n{'=' * 80}")
    print(f"  パラメータ感度分析（{sim_start} 〜 今日）  [スリッページ {SLIPPAGE*100:.1f}%]")
    print(f"{'=' * 80}")

    # Header
    col_header = "stop / profit"
    header = f"  {col_header:>14}"
    for pt in profit_range:
        header += f"  {f'+{int(pt*100)}%利確':>14}"
    print(header)
    print(f"  {'─' * 76}")

    best = {"pf": 0, "stop": 0, "profit": 0, "result": None}

    for sl in stop_range:
        line = f"  {f'-{int(sl*100)}%ストップ':>14}"
        for pt in profit_range:
            sp = {
                "label": f"s{int(sl*100)}_p{int(pt*100)}",
                "stop_loss_pct": sl,
                "trailing_mode": "breakeven",
                "profit_tighten_pct": pt * 0.75,  # Breakeven at 75% of profit target
                "default_trail": sl,
                "profit_take_pct": pt,
                "profit_take_ratio": 0.5,
                "profit_take_full_pct": pt * 1.875,  # Full exit at ~1.9x partial
            }
            r = run_strategy_backtest(all_data, config, sp, sim_start=sim_start, slippage=SLIPPAGE)
            cell = f"PF{r['pf']:.1f} {r['return_pct']:+.1f}%"
            line += f"  {cell:>14}"
            if r["pf"] > best["pf"] and r["trades"] >= 10:
                best = {"pf": r["pf"], "stop": sl, "profit": pt, "result": r}
        print(line)

    print(f"  {'─' * 76}")
    if best["result"]:
        b = best["result"]
        print(f"  ベスト: ストップ-{int(best['stop']*100)}% / 利確+{int(best['profit']*100)}%"
              f"  →  PF {b['pf']:.2f} / リターン {b['return_pct']:+.2f}%"
              f" / Sharpe {b['sharpe']:.2f} / {b['trades']}トレード")
    print(f"{'=' * 80}")


def main():
    parser = argparse.ArgumentParser(description="Strategy backtest comparison tool")
    parser.add_argument("--mode", choices=["compare", "sensitivity"], default="compare")
    args = parser.parse_args()

    config = load_config()
    tickers = list(NIKKEI_225.keys())
    all_data = download_data(tickers, start="2024-03-01")

    if args.mode == "sensitivity":
        run_sensitivity(all_data, config, sim_start="2025-10-01")
        return

    # Profile comparison mode
    profiles = ["default"] + list(config.get("profiles", {}).keys())
    profile_strategies = [build_profile_strategy(config, p) for p in profiles]

    periods = [
        ("3ヶ月", "2026-01-01"),
        ("6ヶ月", "2025-10-01"),
        ("1年", "2025-03-26"),
    ]

    print("=" * 90)
    print("  プロファイル比較バックテスト（スリッページ込み）")
    for ps in profile_strategies:
        print(f"    {ps['label']}")
    print("=" * 90)

    for period_label, sim_start in periods:
        print(f"\n  {period_label}バックテスト実行中...")

        results = []
        for sp in profile_strategies:
            r = run_strategy_backtest(all_data, config, sp, sim_start=sim_start, slippage=SLIPPAGE)
            results.append(r)

        nk225 = fetch_benchmark("^N225", sim_start)
        print_multi_comparison(period_label, sim_start, results, nk225)


if __name__ == "__main__":
    main()
