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
    record_entry,
    record_exit,
    set_profile,
)
from agents.coordinator import analyze_ticker, analyze_all
from backtest import run_backtest
from backtest_multi import run_multi_backtest

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
