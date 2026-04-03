#!/usr/bin/env python3
"""
Standalone strategy comparison tool — run directly: python3 backtest_improved.py

Modes:
  python3 backtest_improved.py                  # Profile comparison (default)
  python3 backtest_improved.py --mode compare   # Same as above
  python3 backtest_improved.py --mode sensitivity  # Parameter grid search
  python3 backtest_improved.py --mode stats     # Statistical analysis
  python3 backtest_improved.py --mode walkforward  # Walk-forward validation

Compares strategy profiles with Nikkei 225 index benchmark.
"""
import argparse
import numpy as np
import pandas as pd
import yfinance as yf
import yaml
import os
from collections import defaultdict
from strategy import calculate_sma, calculate_rsi, compute_composite_score
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
                          slippage=0.0,
                          use_regime=False, use_composite_score=False,
                          score_weights=None, score_threshold=0.0,
                          bull_max_daily=None):
    """Run backtest with given strategy parameters."""
    strat = config["strategy"]
    account = config["account"]
    max_positions = account["max_positions"]
    sp = strategy_params

    # Pre-compute indicators (skip ^N225 — it's not a tradeable stock)
    indicators = {}
    for ticker, df in all_data.items():
        if ticker == "^N225":
            continue
        close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
        if len(close) < strat["sma_trend"] + 50:
            continue
        vol = None
        if "Volume" in df.columns:
            vol_s = df["Volume"].squeeze() if isinstance(df["Volume"], pd.DataFrame) else df["Volume"]
            vol = vol_s
        indicators[ticker] = {
            "close": close,
            "sma_short": calculate_sma(close, strat["sma_short"]),
            "sma_long": calculate_sma(close, strat["sma_long"]),
            "sma_trend": calculate_sma(close, strat["sma_trend"]),
            "rsi": calculate_rsi(close, strat["rsi_period"]),
            "volume": vol,
        }

    # Pre-compute Nikkei 225 regime indicators
    nk_regime = None
    if use_regime and "^N225" in all_data:
        nk_df = all_data["^N225"]
        nk_close = nk_df["Close"].squeeze() if isinstance(nk_df["Close"], pd.DataFrame) else nk_df["Close"]
        if len(nk_close) >= 200:
            nk_regime = {
                "close": nk_close,
                "sma50": calculate_sma(nk_close, 50),
                "sma200": calculate_sma(nk_close, 200),
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

        # === Market regime check ===
        regime_max_daily = bull_max_daily if bull_max_daily is not None else max_daily
        cur_regime = "bull"
        if use_regime and nk_regime is not None:
            # Look up by date (nearest available)
            nk_idx = nk_regime["close"].index.get_indexer([cur_date], method="ffill")
            if nk_idx[0] >= 0:
                ni = nk_idx[0]
                nk_price = float(nk_regime["close"].iloc[ni])
                nk_sma50 = float(nk_regime["sma50"].iloc[ni])
                nk_sma200 = float(nk_regime["sma200"].iloc[ni])
                if not (np.isnan(nk_sma50) or np.isnan(nk_sma200)):
                    if nk_price > nk_sma50 > nk_sma200:
                        cur_regime = "bull"
                    elif nk_price < nk_sma50 < nk_sma200:
                        cur_regime = "bear"
                        regime_max_daily = min(regime_max_daily, 1)
                    else:
                        cur_regime = "neutral"
                        regime_max_daily = min(regime_max_daily, 1)

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
                    cand = {"ticker": ticker, "price": price, "rsi": rsi_val,
                            "sma_short": sma_s, "sma_long": sma_l, "sma_trend": sma_t}
                    # Compute composite score if enabled
                    if use_composite_score and ind.get("volume") is not None:
                        vol_avg = float(ind["volume"].rolling(window=20).mean().iloc[idx]) if idx >= 19 else 0
                        vol_cur = float(ind["volume"].iloc[idx])
                        vol_ratio = vol_cur / vol_avg if vol_avg > 0 else 0
                        vol_score = min(vol_ratio / 3.0, 1.0)
                        rsi_score = max(1.0 - abs(rsi_val - 55) / 25.0, 0.0)
                        momentum = (sma_s - sma_l) / sma_l if sma_l > 0 else 0
                        sma_score = min(max(momentum / 0.10, 0.0), 1.0)
                        deviation = (price - sma_t) / sma_t if sma_t > 0 else 0
                        if deviation < 0:
                            price_score = 0.0
                        elif deviation <= 0.05:
                            price_score = 1.0
                        elif deviation <= 0.15:
                            price_score = 1.0 - (deviation - 0.05) / 0.10
                        else:
                            price_score = 0.0
                        w = score_weights or {"volume_surge": 0.25, "rsi_sweet_spot": 0.25,
                                              "sma_momentum": 0.30, "price_vs_sma200": 0.20}
                        cand["composite_score"] = (
                            w["volume_surge"] * vol_score + w["rsi_sweet_spot"] * rsi_score
                            + w["sma_momentum"] * sma_score + w["price_vs_sma200"] * price_score
                        )
                    buy_candidates.append(cand)
            except Exception:
                continue

        if use_composite_score:
            if score_threshold > 0:
                buy_candidates = [c for c in buy_candidates if c.get("composite_score", 0) >= score_threshold]
            buy_candidates.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
        else:
            buy_candidates.sort(key=lambda x: x["rsi"])

        sector_counts = defaultdict(int)
        for t in positions:
            sector_counts[get_sector(t)] += 1

        effective_max_daily = regime_max_daily if use_regime else max_daily
        for cand in buy_candidates:
            if daily_entries >= effective_max_daily:
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


def run_stats(all_data, config, sim_start="2025-03-26", n_bootstrap=1000):
    """Statistical analysis: bootstrap CI, rolling PF, expected value trend, monthly breakdown."""
    sp = build_profile_strategy(config, "default")
    result = run_strategy_backtest(all_data, config, sp, sim_start=sim_start, slippage=SLIPPAGE)
    trades = result["trade_details"]

    w = 80
    print(f"\n{'=' * w}")
    print(f"  統計分析: {sp['label']}（{sim_start} 〜 今日）  [{len(trades)}トレード]")
    print(f"{'=' * w}")

    if len(trades) < 10:
        print("  トレード数が少なすぎます（最低10件必要）")
        print(f"{'=' * w}")
        return

    # --- Bootstrap confidence interval for win rate ---
    pnls = np.array([t["pnl"] for t in trades])
    wins_arr = (pnls > 0).astype(int)
    observed_wr = wins_arr.mean() * 100

    np.random.seed(42)
    boot_wrs = []
    boot_evs = []
    for _ in range(n_bootstrap):
        sample = np.random.choice(pnls, size=len(pnls), replace=True)
        boot_wrs.append((sample > 0).mean() * 100)
        boot_evs.append(sample.mean())

    wr_lo, wr_hi = np.percentile(boot_wrs, [2.5, 97.5])
    ev_lo, ev_hi = np.percentile(boot_evs, [2.5, 97.5])
    observed_ev = pnls.mean()

    print(f"\n  【ブートストラップ分析】（{n_bootstrap}回リサンプリング）")
    print(f"  {'─' * (w - 4)}")
    print(f"  勝率:            {observed_wr:.1f}%   95%CI [{wr_lo:.1f}% — {wr_hi:.1f}%]")
    print(f"  期待値/トレード:  ¥{observed_ev:+,.0f}   95%CI [¥{ev_lo:+,.0f} — ¥{ev_hi:+,.0f}]")

    # --- Rolling Profit Factor (20-trade window) ---
    window = 20
    if len(trades) >= window:
        print(f"\n  【ローリングPF】（{window}トレード窓）")
        print(f"  {'─' * (w - 4)}")
        rolling_pfs = []
        for i in range(window, len(trades) + 1):
            chunk = trades[i - window:i]
            gp = sum(t["pnl"] for t in chunk if t["pnl"] > 0)
            gl = abs(sum(t["pnl"] for t in chunk if t["pnl"] <= 0))
            rpf = gp / gl if gl > 0 else float("inf")
            rolling_pfs.append((i, rpf))

        pf_values = [pf for _, pf in rolling_pfs]
        pf_min = min(pf_values)
        pf_max = max(pf_values)
        pf_mean = np.mean(pf_values)
        pf_below_1 = sum(1 for pf in pf_values if pf < 1.0)
        print(f"  平均PF:  {pf_mean:.2f}   最小: {pf_min:.2f}   最大: {pf_max:.2f}")
        print(f"  PF < 1.0 の区間: {pf_below_1}/{len(pf_values)} ({pf_below_1/len(pf_values)*100:.0f}%)")

        # Show PF trend as simple sparkline
        n_bins = min(10, len(rolling_pfs))
        bin_size = len(rolling_pfs) // n_bins
        sparkline = "  推移: "
        for b in range(n_bins):
            start = b * bin_size
            end = start + bin_size if b < n_bins - 1 else len(rolling_pfs)
            avg = np.mean([pf for _, pf in rolling_pfs[start:end]])
            if avg >= 1.5:
                sparkline += "▆"
            elif avg >= 1.0:
                sparkline += "▃"
            else:
                sparkline += "▁"
        sparkline += f"  (左=古い  右=新しい  ▆≥1.5  ▃≥1.0  ▁<1.0)"
        print(sparkline)

    # --- Monthly breakdown ---
    print(f"\n  【月次パフォーマンス】")
    print(f"  {'─' * (w - 4)}")
    monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    for t in trades:
        month = t["exit_date"][:7]  # YYYY-MM
        monthly[month]["trades"] += 1
        if t["pnl"] > 0:
            monthly[month]["wins"] += 1
        monthly[month]["pnl"] += t["pnl"]

    print(f"  {'月':>8}  {'件数':>6}  {'勝率':>6}  {'損益':>10}  {'PF':>6}")
    for month in sorted(monthly.keys()):
        m = monthly[month]
        wr = m["wins"] / m["trades"] * 100 if m["trades"] else 0
        gp = sum(t["pnl"] for t in trades if t["exit_date"][:7] == month and t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in trades if t["exit_date"][:7] == month and t["pnl"] <= 0))
        mpf = gp / gl if gl > 0 else float("inf")
        print(f"  {month:>8}  {m['trades']:>5}件  {wr:>5.1f}%  ¥{m['pnl']:>+9,.0f}  {mpf:>5.2f}")

    # --- Expected value trend (cumulative average) ---
    print(f"\n  【累積期待値の推移】")
    print(f"  {'─' * (w - 4)}")
    cum_pnl = np.cumsum(pnls)
    milestones = [10, 25, 50, 75, 100, 150, 200]
    for m in milestones:
        if m <= len(pnls):
            ev = cum_pnl[m - 1] / m
            print(f"  {m:>4}トレード時点: ¥{ev:>+8,.0f}/trade  (累計: ¥{cum_pnl[m-1]:>+10,.0f})")

    print(f"\n{'=' * w}")


def run_walkforward(all_data, config, initial_balance=300000):
    """Walk-forward validation: in-sample optimization → out-of-sample test."""
    # 4 quarters of out-of-sample, each preceded by training on all prior data
    periods = [
        ("Q2 2025", "2025-04-01", "2025-07-01"),
        ("Q3 2025", "2025-07-01", "2025-10-01"),
        ("Q4 2025", "2025-10-01", "2026-01-01"),
        ("Q1 2026", "2026-01-01", "2026-04-01"),
    ]

    stop_range = [0.05, 0.08, 0.10, 0.12]
    profit_range = [0.06, 0.08, 0.10, 0.12]

    w = 80
    print(f"\n{'=' * w}")
    print(f"  ウォークフォワード検証  [スリッページ {SLIPPAGE*100:.1f}%]")
    print(f"  In-sample: グリッドサーチで最適パラメータ選定")
    print(f"  Out-of-sample: 最適パラメータで3ヶ月間テスト")
    print(f"{'=' * w}")

    oos_results = []

    for period_label, oos_start, oos_end in periods:
        # In-sample: everything before oos_start (minimum 3 months prior)
        is_start_dt = pd.Timestamp(oos_start) - pd.DateOffset(months=6)
        is_start = is_start_dt.strftime("%Y-%m-%d")

        # Grid search in-sample
        best_pf = 0
        best_params = None
        for sl in stop_range:
            for pt in profit_range:
                sp = {
                    "label": f"s{int(sl*100)}_p{int(pt*100)}",
                    "stop_loss_pct": sl,
                    "trailing_mode": "breakeven",
                    "profit_tighten_pct": pt * 0.75,
                    "default_trail": sl,
                    "profit_take_pct": pt,
                    "profit_take_ratio": 0.5,
                    "profit_take_full_pct": pt * 1.875,
                }
                r = run_strategy_backtest(
                    all_data, config, sp,
                    sim_start=is_start, initial_balance=initial_balance,
                    slippage=SLIPPAGE,
                )
                # Filter: only consider combos with enough trades AND before oos_start
                is_trades = [t for t in r["trade_details"]
                             if t["exit_date"] < oos_start]
                is_gp = sum(t["pnl"] for t in is_trades if t["pnl"] > 0)
                is_gl = abs(sum(t["pnl"] for t in is_trades if t["pnl"] <= 0))
                is_pf = is_gp / is_gl if is_gl > 0 else 0
                if is_pf > best_pf and len(is_trades) >= 5:
                    best_pf = is_pf
                    best_params = (sl, pt)

        if not best_params:
            best_params = (0.08, 0.08)  # fallback to default

        sl, pt = best_params
        oos_sp = {
            "label": f"{period_label}",
            "stop_loss_pct": sl,
            "trailing_mode": "breakeven",
            "profit_tighten_pct": pt * 0.75,
            "default_trail": sl,
            "profit_take_pct": pt,
            "profit_take_ratio": 0.5,
            "profit_take_full_pct": pt * 1.875,
        }

        # Run out-of-sample
        oos_r = run_strategy_backtest(
            all_data, config, oos_sp,
            sim_start=oos_start, initial_balance=initial_balance,
            slippage=SLIPPAGE,
        )
        # Filter to only trades within the OOS window
        oos_trades = [t for t in oos_r["trade_details"]
                      if oos_start <= t["exit_date"] < oos_end]
        oos_gp = sum(t["pnl"] for t in oos_trades if t["pnl"] > 0)
        oos_gl = abs(sum(t["pnl"] for t in oos_trades if t["pnl"] <= 0))
        oos_pnl = sum(t["pnl"] for t in oos_trades)
        oos_pf = oos_gp / oos_gl if oos_gl > 0 else float("inf")
        oos_wins = sum(1 for t in oos_trades if t["pnl"] > 0)
        oos_wr = oos_wins / len(oos_trades) * 100 if oos_trades else 0

        nk225 = fetch_benchmark("^N225", oos_start, oos_end)
        nk_str = f"{nk225:+.2f}%" if nk225 is not None else "N/A"

        oos_results.append({
            "period": period_label,
            "is_pf": best_pf,
            "best_stop": sl,
            "best_profit": pt,
            "trades": len(oos_trades),
            "wins": oos_wins,
            "wr": oos_wr,
            "pnl": oos_pnl,
            "pf": oos_pf,
            "nk225": nk225,
        })

        print(f"\n  {period_label} ({oos_start} 〜 {oos_end})")
        print(f"  {'─' * (w - 4)}")
        print(f"  IS最適: ストップ-{int(sl*100)}% / 利確+{int(pt*100)}%  (IS PF: {best_pf:.2f})")
        print(f"  OOS結果: {len(oos_trades)}件  勝率{oos_wr:.1f}%  PF {oos_pf:.2f}  損益 ¥{oos_pnl:+,.0f}  日経 {nk_str}")

    # Aggregate
    print(f"\n  {'─' * (w - 4)}")
    print(f"  【集計】")
    total_trades = sum(r["trades"] for r in oos_results)
    total_wins = sum(r["wins"] for r in oos_results)
    total_pnl = sum(r["pnl"] for r in oos_results)
    total_wr = total_wins / total_trades * 100 if total_trades else 0
    profitable_quarters = sum(1 for r in oos_results if r["pnl"] > 0)
    print(f"  全期間: {total_trades}トレード  勝率{total_wr:.1f}%  損益 ¥{total_pnl:+,.0f}")
    print(f"  プラス四半期: {profitable_quarters}/{len(oos_results)}")

    # Check if IS→OOS degradation is severe
    for r in oos_results:
        degradation = r["is_pf"] - r["pf"] if r["pf"] != float("inf") else 0
        if degradation > 0.5:
            print(f"  ⚠ {r['period']}: IS PF {r['is_pf']:.2f} → OOS PF {r['pf']:.2f}  (過学習の可能性)")

    print(f"\n{'=' * w}")


def main():
    parser = argparse.ArgumentParser(description="Strategy backtest comparison tool")
    parser.add_argument("--mode", choices=["compare", "sensitivity", "stats", "walkforward"],
                        default="compare")
    args = parser.parse_args()

    config = load_config()
    tickers = list(NIKKEI_225.keys())
    all_data = download_data(tickers, start="2024-03-01")

    if args.mode == "sensitivity":
        run_sensitivity(all_data, config, sim_start="2025-10-01")
        return

    if args.mode == "stats":
        run_stats(all_data, config, sim_start="2025-03-26")
        return

    if args.mode == "walkforward":
        run_walkforward(all_data, config)
        return

    # Profile comparison mode
    profiles = ["default"] + list(config.get("profiles", {}).keys())
    profile_strategies = [build_profile_strategy(config, p) for p in profiles]

    # Download ^N225 for regime detection
    print("日経225指数ダウンロード中...")
    try:
        nk225_df = yf.download("^N225", start="2024-03-01", progress=False)
        if nk225_df is not None and not nk225_df.empty:
            # Flatten MultiIndex columns if present
            if isinstance(nk225_df.columns, pd.MultiIndex):
                nk225_df.columns = nk225_df.columns.get_level_values(0)
            all_data["^N225"] = nk225_df
            print(f"  日経225: {len(nk225_df)}日分")
    except Exception as e:
        print(f"  日経225ダウンロード失敗: {e}")

    sp = profile_strategies[0]

    # === Phase 1: Grid search for best new-strategy params (6-month period) ===
    sim_start_tune = "2025-10-01"
    print("=" * 90)
    print("  Phase 1: 新戦略パラメータ感度分析（6ヶ月）")
    print("=" * 90)

    threshold_range = [0.0, 0.25, 0.35, 0.45]
    bull_daily_range = [2, 3]
    weight_variants = {
        "デフォルト": {"volume_surge": 0.25, "rsi_sweet_spot": 0.25, "sma_momentum": 0.30, "price_vs_sma200": 0.20},
        "安全重視": {"volume_surge": 0.15, "rsi_sweet_spot": 0.30, "sma_momentum": 0.20, "price_vs_sma200": 0.35},
        "モメンタム": {"volume_surge": 0.30, "rsi_sweet_spot": 0.20, "sma_momentum": 0.35, "price_vs_sma200": 0.15},
    }

    # Baseline
    r_base = run_strategy_backtest(
        all_data, config, sp, sim_start=sim_start_tune, slippage=SLIPPAGE,
        use_regime=False, use_composite_score=False,
    )
    print(f"\n  ベースライン（旧戦略）: リターン={r_base['return_pct']:+.2f}%  PF={r_base['pf']:.2f}  "
          f"Sharpe={r_base['sharpe']:.2f}  DD={r_base['max_dd']:.2f}%  {r_base['trades']}件\n")

    print(f"  {'重み':<12} {'閾値':>4} {'Bull日':>5} {'リターン':>8} {'PF':>6} {'Sharpe':>7} {'DD':>7} "
          f"{'件数':>5} {'勝率':>5} {'損切':>4}")
    print(f"  {'─' * 80}")

    best = {"sharpe": -999, "params": None, "result": None}
    for wname, w in weight_variants.items():
        for thr in threshold_range:
            for bd in bull_daily_range:
                r = run_strategy_backtest(
                    all_data, config, sp, sim_start=sim_start_tune, slippage=SLIPPAGE,
                    use_regime=True, use_composite_score=True, score_weights=w,
                    score_threshold=thr, bull_max_daily=bd,
                )
                losses = r["losses"]
                mark = " ◀" if r["sharpe"] > r_base["sharpe"] and r["max_dd"] > r_base["max_dd"] else ""
                print(f"  {wname:<12} {thr:>4.2f} {bd:>5} {r['return_pct']:>+7.2f}% {r['pf']:>5.2f} "
                      f"{r['sharpe']:>6.2f} {r['max_dd']:>6.2f}% {r['trades']:>5} {r['win_rate']:>4.1f}% {losses:>4}{mark}")
                # Best by Sharpe among those that beat baseline DD
                if r["sharpe"] > best["sharpe"] and r["trades"] >= 20:
                    best = {"sharpe": r["sharpe"], "params": (wname, w, thr, bd), "result": r}

    print(f"  {'─' * 80}")
    if best["params"]:
        wname, bw, bthr, bbd = best["params"]
        br = best["result"]
        print(f"  ベスト: {wname}  閾値={bthr}  Bull日={bbd}")
        print(f"    リターン={br['return_pct']:+.2f}%  PF={br['pf']:.2f}  Sharpe={br['sharpe']:.2f}  "
              f"DD={br['max_dd']:.2f}%  {br['trades']}件  勝率={br['win_rate']:.1f}%")
    print("=" * 90)

    # === Phase 2: Compare baseline vs best across all periods ===
    best_w = best["params"][1] if best["params"] else weight_variants["デフォルト"]
    best_thr = best["params"][2] if best["params"] else 0.35
    best_bd = best["params"][3] if best["params"] else 2

    periods = [
        ("3ヶ月", "2026-01-01"),
        ("6ヶ月", "2025-10-01"),
        ("1年", "2025-03-26"),
    ]

    print(f"\n{'=' * 90}")
    print(f"  Phase 2: 旧戦略 vs 最適新戦略 比較")
    print(f"  最適パラメータ: 重み={best['params'][0] if best['params'] else 'default'}  "
          f"閾値={best_thr}  Bull日上限={best_bd}")
    print(f"{'=' * 90}")

    for period_label, sim_start in periods:
        print(f"\n  {period_label}バックテスト実行中...")

        r_old = run_strategy_backtest(
            all_data, config, sp, sim_start=sim_start, slippage=SLIPPAGE,
            use_regime=False, use_composite_score=False,
        )
        r_old["label"] = "旧戦略（RSIソート）"

        r_new = run_strategy_backtest(
            all_data, config, sp, sim_start=sim_start, slippage=SLIPPAGE,
            use_regime=True, use_composite_score=True, score_weights=best_w,
            score_threshold=best_thr, bull_max_daily=best_bd,
        )
        r_new["label"] = "新戦略（最適化済み）"

        nk225 = fetch_benchmark("^N225", sim_start)
        print_multi_comparison(period_label, sim_start, [r_old, r_new], nk225)


if __name__ == "__main__":
    main()
