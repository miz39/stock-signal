#!/usr/bin/env python3
"""
CLI wrapper for stock-signal commands.
Used by slack-bot to call stock-signal functions via subprocess.
All output is JSON to stdout, errors to stderr.
"""

import argparse
import json
import sys
import os
import traceback
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nikkei225 import NIKKEI_225
from portfolio import (
    get_open_positions,
    get_weekly_report,
    get_cash_balance,
    get_performance_summary,
    get_readiness_metrics,
    record_entry,
    record_exit,
    set_profile,
)
from agents.coordinator import analyze_ticker, analyze_all
from backtest import run_backtest
from backtest_multi import run_multi_backtest
from data import fetch_stock_data
from portfolio_risk import (
    check_correlation,
    calculate_portfolio_var,
    calculate_portfolio_volatility,
    format_risk_report,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(profile_name: str = "default") -> dict:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    if config.get("watchlist") == "nikkei225":
        config["watchlist"] = list(NIKKEI_225.keys())
    if profile_name != "default":
        profile_overrides = config.get("profiles", {}).get(profile_name, {})
        if profile_overrides:
            config["strategy"] = {**config.get("strategy", {}), **profile_overrides.get("strategy", {})}
    return config


def _output(data):
    print(json.dumps(data, ensure_ascii=False, default=str))


def _error(msg):
    print(json.dumps({"error": str(msg)}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


def cmd_analyze(args):
    config = load_config(args.profile)
    if args.ticker:
        t = args.ticker if args.ticker.endswith(".T") else args.ticker + ".T"
        result = analyze_ticker(t, config)
        _output(result)
    else:
        results = analyze_all(config)
        _output(results)


def cmd_backtest(args):
    t = args.ticker if args.ticker.endswith(".T") else args.ticker + ".T"
    config = load_config(args.profile)
    result = run_backtest(t, config, period=args.period)
    _output(result)


def cmd_simulate(args):
    config = load_config(args.profile)
    tickers = list(NIKKEI_225.keys())
    result = run_multi_backtest(tickers, config, period=args.period)
    _output(result)


def cmd_buy(args):
    t = args.ticker if args.ticker.endswith(".T") else args.ticker + ".T"
    price = float(args.price)
    shares = int(args.shares)

    config = load_config(args.profile)
    open_pos = get_open_positions()
    max_pos = config["account"]["max_positions"]
    if len(open_pos) >= max_pos:
        _error(f"同時保有上限（{max_pos}銘柄）に達しています")

    strat = config.get("strategy", {})
    stop_pct = strat.get("stop_loss_pct", 0.08)
    trade = record_entry(t, price, shares, stop_pct=stop_pct)
    name = NIKKEI_225.get(t, t.replace(".T", ""))
    _output({
        "ticker": t,
        "name": name,
        "price": price,
        "shares": shares,
        "total": price * shares,
        "entry_date": trade["entry_date"],
        "stop_price": trade["stop_price"],
    })


def cmd_sell(args):
    t = args.ticker if args.ticker.endswith(".T") else args.ticker + ".T"
    price = float(args.price)

    trade = record_exit(t, price)
    if not trade:
        _error(f"{t} のオープンポジションが見つかりません")

    name = NIKKEI_225.get(t, t.replace(".T", ""))
    pnl_pct = (price / trade["entry_price"] - 1) * 100
    _output({
        "ticker": t,
        "name": name,
        "entry_price": trade["entry_price"],
        "exit_price": price,
        "shares": trade["shares"],
        "pnl": trade["pnl"],
        "pnl_pct": round(pnl_pct, 1),
        "exit_date": trade["exit_date"],
    })


def cmd_weekly(args):
    config = load_config(args.profile)
    balance = config["account"]["balance"]
    weekly = get_weekly_report()
    weekly["balance"] = balance
    _output(weekly)


def cmd_watchlist(args):
    config = load_config(args.profile)
    items = []
    for ticker in config["watchlist"]:
        name = NIKKEI_225.get(ticker, ticker)
        items.append({"ticker": ticker, "name": name})
    _output({"count": len(items), "items": items})


def cmd_rule(args):
    config = load_config(args.profile)
    strat = config["strategy"]
    acct = config["account"]
    mode = config.get("mode", "paper")
    _output({
        "mode": mode,
        "balance": acct["balance"],
        "strategy": {
            "sma_short": strat["sma_short"],
            "sma_long": strat["sma_long"],
            "sma_trend": strat["sma_trend"],
            "rsi_period": strat["rsi_period"],
            "rsi_overbought": strat["rsi_overbought"],
            "rsi_entry_min": strat.get("rsi_entry_min", 50),
            "rsi_entry_max": strat.get("rsi_entry_max", 65),
            "stop_loss_pct": strat.get("stop_loss_pct", 0.08),
            "profit_tighten_pct": strat.get("profit_tighten_pct", 0.06),
            "profit_take_pct": strat.get("profit_take_pct", 0.08),
            "profit_take_full_pct": strat.get("profit_take_full_pct", 0.15),
            "min_volume": strat.get("min_volume", 0),
        },
        "account": {
            "risk_per_trade": acct["risk_per_trade"],
            "max_positions": acct["max_positions"],
            "max_allocation": acct.get("max_allocation", 0.15),
            "max_daily_entries": acct.get("max_daily_entries", 3),
            "max_sector_positions": acct.get("max_sector_positions", 2),
        },
    })


def cmd_risk(args):
    config = load_config(args.profile)
    balance = config["account"]["balance"]
    positions = get_open_positions()

    # Attach current prices
    for pos in positions:
        try:
            df = fetch_stock_data(pos["ticker"], period="5d")
            pos["current_price"] = float(df["Close"].iloc[-1])
        except Exception:
            pos["current_price"] = pos["entry_price"]

    cash = get_cash_balance(balance)
    stock_value = sum(pos["current_price"] * pos["shares"] for pos in positions)
    total_assets = cash + stock_value

    report = format_risk_report(positions, total_assets, config)
    report["cash"] = round(cash)
    report["stock_value"] = round(stock_value)

    # VaR/CVaR and volatility (heavier computation)
    if positions:
        report["var"] = calculate_portfolio_var(positions, total_assets)
        report["volatility"] = calculate_portfolio_volatility(positions)
        if not args.quick:
            report["correlations"] = check_correlation(positions)

    _output(report)


def cmd_performance(args):
    from portfolio import _load_trades
    from datetime import date, timedelta

    config = load_config(args.profile)
    trades = _load_trades()
    closed = [t for t in trades if t.get("status") == "closed"]

    # Filter by period
    if args.period:
        period_map = {"1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365}
        days = period_map.get(args.period)
        if days:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            closed = [t for t in closed if (t.get("exit_date") or "") >= cutoff]

    if not closed:
        _output({"error": "該当期間のクローズドトレードがありません", "trades": 0})
        return

    pnls = [t.get("pnl", 0) for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    # Monthly breakdown
    monthly = {}
    for t in closed:
        month = (t.get("exit_date") or "")[:7]
        if month:
            monthly.setdefault(month, {"pnl": 0, "count": 0, "wins": 0})
            monthly[month]["pnl"] += t.get("pnl", 0)
            monthly[month]["count"] += 1
            if t.get("pnl", 0) > 0:
                monthly[month]["wins"] += 1

    _output({
        "period": args.period or "all",
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "total_pnl": round(sum(pnls)),
        "avg_pnl": round(sum(pnls) / len(pnls)),
        "gross_profit": round(gross_profit),
        "gross_loss": round(gross_loss),
        "profit_factor": pf,
        "best_trade": round(max(pnls)),
        "worst_trade": round(min(pnls)),
        "monthly": {
            m: {"pnl": round(v["pnl"]), "count": v["count"],
                "win_rate": round(v["wins"] / v["count"] * 100) if v["count"] else 0}
            for m, v in sorted(monthly.items())
        },
    })


def cmd_compare(args):
    from portfolio import _load_trades

    config_raw = load_config("default")
    profiles = ["default"] + list(config_raw.get("profiles", {}).keys())
    results = []

    for p in profiles:
        set_profile(p)
        config = load_config(p)
        balance = config["account"]["balance"]
        trades = _load_trades()
        closed = [t for t in trades if t.get("status") == "closed"]
        open_pos = [t for t in trades if t.get("status") == "open"]

        pnls = [t.get("pnl", 0) for t in closed]
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p <= 0))
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0
        cash = get_cash_balance(balance)

        results.append({
            "profile": p,
            "trades": len(closed),
            "open": len(open_pos),
            "win_rate": round(wins / len(closed) * 100, 1) if closed else 0,
            "total_pnl": round(total_pnl),
            "profit_factor": pf,
            "cash": round(cash),
        })

    # Restore default profile
    set_profile(args.profile)
    _output({"profiles": results})


def cmd_review(args):
    import glob as globmod

    config = load_config(args.profile)
    balance = config["account"]["balance"]

    perf = get_performance_summary()

    # Profit factor
    from portfolio import _load_trades
    trades = _load_trades()
    closed = [t for t in trades if t.get("status") == "closed" and "pnl" in t]
    gross_profit = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in closed if t["pnl"] < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

    # Unrealized PnL from open positions
    open_pos = get_open_positions()
    unrealized_pnl = 0
    for pos in open_pos:
        try:
            df = fetch_stock_data(pos["ticker"], period="5d")
            current = float(df["Close"].iloc[-1])
            unrealized_pnl += (current - pos["entry_price"]) * pos["shares"]
        except Exception:
            pass
    unrealized_pnl = round(unrealized_pnl)

    readiness = get_readiness_metrics(balance)

    # Latest review file
    review_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "reviews")
    review_files = sorted(globmod.glob(os.path.join(review_dir, "*.md")))
    latest_review = None
    if review_files:
        latest_file = review_files[-1]
        filename = os.path.basename(latest_file).replace(".md", "")
        parts = filename.split("_", 1)
        review_date = parts[0] if len(parts) >= 1 else ""
        review_title = parts[1] if len(parts) >= 2 else filename
        with open(latest_file, "r") as f:
            content = f.read()[:500]
        latest_review = {
            "date": review_date,
            "title": review_title,
            "content": content,
        }

    _output({
        "performance": {
            "trade_count": perf["trade_count"],
            "win_rate": perf["win_rate"],
            "total_pnl": perf["total_pnl"],
            "profit_factor": profit_factor,
            "max_drawdown": perf["max_drawdown"],
        },
        "unrealized": {
            "pnl": unrealized_pnl,
            "count": len(open_pos),
        },
        "readiness": {
            "score_pct": readiness["score_pct"],
            "ready": readiness["ready"],
            "criteria": readiness["criteria"],
        },
        "latest_review": latest_review,
        "dashboard_url": "https://miz39.github.io/stock-signal/",
    })


def cmd_status(args):
    config = load_config(args.profile)
    balance = config["account"]["balance"]
    _output({
        "profile": args.profile,
        "positions": get_open_positions(),
        "cash": get_cash_balance(balance),
        "performance": get_performance_summary(),
    })


def main():
    parser = argparse.ArgumentParser(description="stock-signal CLI")
    parser.add_argument(
        "--profile",
        default="default",
        help="Profile name (default/conservative/aggressive)",
    )
    sub = parser.add_subparsers(dest="command")

    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("ticker", nargs="?", default=None)

    p_backtest = sub.add_parser("backtest")
    p_backtest.add_argument("ticker")
    p_backtest.add_argument("period", nargs="?", default="3y")

    p_simulate = sub.add_parser("simulate")
    p_simulate.add_argument("period", nargs="?", default="3y")

    p_buy = sub.add_parser("buy")
    p_buy.add_argument("ticker")
    p_buy.add_argument("price")
    p_buy.add_argument("shares")

    p_sell = sub.add_parser("sell")
    p_sell.add_argument("ticker")
    p_sell.add_argument("price")

    sub.add_parser("weekly")
    sub.add_parser("watchlist")
    sub.add_parser("rule")
    sub.add_parser("status")
    sub.add_parser("review")

    p_risk = sub.add_parser("risk")
    p_risk.add_argument("--quick", action="store_true", help="Skip correlation analysis")

    p_perf = sub.add_parser("performance")
    p_perf.add_argument("period", nargs="?", default=None, help="Period: 1w/1m/3m/6m/1y")

    sub.add_parser("compare")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    set_profile(args.profile)

    dispatch = {
        "analyze": cmd_analyze,
        "backtest": cmd_backtest,
        "simulate": cmd_simulate,
        "buy": cmd_buy,
        "sell": cmd_sell,
        "weekly": cmd_weekly,
        "watchlist": cmd_watchlist,
        "rule": cmd_rule,
        "status": cmd_status,
        "review": cmd_review,
        "risk": cmd_risk,
        "performance": cmd_performance,
        "compare": cmd_compare,
    }

    try:
        dispatch[args.command](args)
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
