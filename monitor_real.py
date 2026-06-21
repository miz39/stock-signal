#!/usr/bin/env python3
"""
リアル取引ポジション監視スクリプト。
real_trades.json を読み、現在価格でストップ・目標到達をチェックして Slack 通知する。
launchd から平日 7:50 / 14:10 に実行される。ローカル専用（gitignore 対象外）。
"""

import json
import os
import sys
from datetime import date, datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_TRADES_FILE = os.path.join(BASE_DIR, "real_trades.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
ENV_FILE = os.path.join(BASE_DIR, ".env")

JST = timezone(timedelta(hours=9))


def load_env() -> None:
    """Load .env file into os.environ if it exists."""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def load_config() -> dict:
    import yaml
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_real_trades() -> list:
    if not os.path.exists(REAL_TRADES_FILE):
        return []
    with open(REAL_TRADES_FILE) as f:
        return json.load(f)


def save_real_trades(trades: list) -> None:
    with open(REAL_TRADES_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)


def fetch_prices(tickers: list) -> dict:
    if not tickers:
        return {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period="1d", progress=False)
        prices = {}
        if len(tickers) == 1:
            close = data["Close"]
            if not close.empty:
                val = close.iloc[-1]
                if hasattr(val, "iloc"):
                    val = val.iloc[0]
                prices[tickers[0]] = round(float(val), 1)
        else:
            for t in tickers:
                try:
                    val = data["Close"][t].iloc[-1]
                    if val == val:
                        prices[t] = round(float(val), 1)
                except Exception:
                    pass
        return prices
    except Exception as e:
        print(f"価格取得エラー: {e}", file=sys.stderr)
        return {}


def send_slack(webhook_url: str, text: str) -> None:
    import urllib.request
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"Slack送信エラー: {e}", file=sys.stderr)


def main():
    load_env()

    # 週末はスキップ
    today = date.today()
    if today.weekday() >= 5:
        print("週末のためスキップ")
        return

    config = load_config()
    slack_url = os.environ.get("SLACK_WEBHOOK_URL") or config.get("slack", {}).get("webhook_url", "")
    if not slack_url:
        print("SLACK_WEBHOOK_URL が設定されていません", file=sys.stderr)
        return

    strat = config.get("strategy", {})
    stop_loss_pct = strat.get("stop_loss_pct", 0.08)
    trail_activation_pct = strat.get("profit_tighten_pct", 0.06)
    trail_stop_pct = stop_loss_pct  # trailing stop distance = same as initial stop
    half_profit_pct = strat.get("profit_take_pct", 0.10)
    full_profit_pct = strat.get("profit_take_full_pct", 0.15)

    trades = load_real_trades()
    open_trades = [t for t in trades if t.get("status") == "open"]

    if not open_trades:
        print("保有ポジションなし")
        return

    tickers = [t["ticker"] for t in open_trades]
    prices = fetch_prices(tickers)

    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    alerts = []
    updated = False

    from nikkei225 import NIKKEI_225

    for t in open_trades:
        ticker = t["ticker"]
        current = prices.get(ticker)
        if current is None:
            continue

        entry = t["entry_price"]
        shares = t["shares"]
        stop = t.get("stop_price", round(entry * (1 - stop_loss_pct), 1))
        high = t.get("high_price", entry)
        name = NIKKEI_NAME = NIKKEI_225.get(ticker, ticker)
        if isinstance(name, dict):
            name = name.get("name", ticker)

        pnl = round((current - entry) * shares, 0)
        pnl_pct = round((current / entry - 1) * 100, 1)
        sign = "+" if pnl >= 0 else ""

        # high_price 更新
        if current > high:
            t["high_price"] = current
            high = current
            updated = True

        # トレーリングストップ更新（高値から-8%）
        if high >= entry * (1 + trail_activation_pct):
            new_stop = round(high * (1 - trail_stop_pct), 1)
            if new_stop > stop:
                t["stop_price"] = new_stop
                stop = new_stop
                updated = True

        # ---- アラート判定 ----
        if current <= stop:
            alerts.append(
                f"🔴 *[REAL] 損切りライン到達*\n"
                f"*{name}*（{ticker.replace('.T','')}）\n"
                f"現在値 ¥{current:,.0f} ≤ ストップ ¥{stop:,.0f}\n"
                f"損益: {sign}¥{abs(pnl):,.0f}（{sign}{pnl_pct}%）| {shares}株"
            )
        elif current >= entry * (1 + full_profit_pct):
            alerts.append(
                f"🟢 *[REAL] 全利確ライン到達*\n"
                f"*{name}*（{ticker.replace('.T','')}）\n"
                f"現在値 ¥{current:,.0f}（+{pnl_pct}%）\n"
                f"利益: +¥{abs(pnl):,.0f} | {shares}株"
            )
        elif current >= entry * (1 + half_profit_pct) and shares == t.get("original_shares", shares):
            alerts.append(
                f"🟡 *[REAL] 半分利確ライン到達*\n"
                f"*{name}*（{ticker.replace('.T','')}）\n"
                f"現在値 ¥{current:,.0f}（+{pnl_pct}%）\n"
                f"利益: +¥{abs(pnl):,.0f} | {shares}株 → 半分売り検討"
            )

    if updated:
        save_real_trades(trades)

    if alerts:
        header = f"*💴 リアル取引アラート* （{now_str}）\n"
        message = header + "\n\n".join(alerts)
        send_slack(slack_url, message)
        print(f"{len(alerts)}件のアラートを送信")
    else:
        pnl_summary = []
        for t in open_trades:
            ticker = t["ticker"]
            current = prices.get(ticker, t["entry_price"])
            pnl_pct = round((current / t["entry_price"] - 1) * 100, 1)
            sign = "+" if pnl_pct >= 0 else ""
            name = NIKKEI_225.get(ticker, ticker)
            if isinstance(name, dict):
                name = name.get("name", ticker)
            pnl_summary.append(f"• {name}: {sign}{pnl_pct}%")

        message = (
            f"*💴 リアル取引チェック* （{now_str}）\n"
            f"保有 {len(open_trades)}件 — アラートなし\n"
            + "\n".join(pnl_summary)
        )
        send_slack(slack_url, message)
        print("アラートなし、サマリー送信")


if __name__ == "__main__":
    main()
