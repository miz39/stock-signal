#!/usr/bin/env python3
"""
S株スイングトレード シグナル通知ツール
cronから1日3回実行し、Discord Webhookで売買シグナルを通知する。
日経225全銘柄を簡易スキャンし、シグナルが出た銘柄のみ通知する。
"""

import os
import sys
import yaml
from datetime import datetime, date, timezone, timedelta

from data import fetch_stock_data
from strategy import generate_signal
from risk import calculate_stop_loss, calculate_position_size
from portfolio import (
    get_open_positions,
    get_weekly_report,
    record_entry,
    record_exit,
    update_trailing_stop,
)
from notifier import (
    send_discord,
    send_error,
    format_signal_embeds,
    format_weekly_embed,
    TICKER_NAMES,
)
from nikkei225 import NIKKEI_225


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    # watchlistが"nikkei225"の場合、全銘柄リストに展開
    if config.get("watchlist") == "nikkei225":
        config["watchlist"] = list(NIKKEI_225.keys())

    return config


def get_session_name() -> str:
    """現在時刻（JST）に応じたセッション名を返す。"""
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    hour = now.hour
    minute = now.minute
    current = hour * 60 + minute

    if current < 10 * 60:       # 10:00 より前
        return "8:50 寄り前"
    elif current < 14 * 60:     # 14:00 より前
        return "12:35 昼"
    else:
        return "15:10 引け後"


def is_friday_close() -> bool:
    """金曜日の引け後（15:00以降）かどうか（JST）。"""
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    return now.weekday() == 4 and now.hour >= 15


def run():
    config = load_config()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config["discord"].get("webhook_url")
    mode = config.get("mode", "paper")
    account = config["account"]
    balance = account["balance"]

    session_name = get_session_name()

    # 保有中の銘柄（売りシグナルチェック用）
    open_positions = get_open_positions()
    open_tickers = {p["ticker"] for p in open_positions}

    buy_signals = []
    sell_signals = []
    error_count = 0

    # 全銘柄を簡易スキャン
    total = len(config["watchlist"])
    for i, ticker in enumerate(config["watchlist"]):
        try:
            df = fetch_stock_data(ticker)
            sig = generate_signal(df, config)
            sig["ticker"] = ticker

            if sig["signal"] == "BUY":
                stop = calculate_stop_loss(sig["price"])
                shares = calculate_position_size(
                    balance,
                    account["risk_per_trade"],
                    sig["price"],
                    stop,
                    account["unit"],
                    account.get("max_allocation", 0.15),
                )
                sig["stop_loss"] = stop
                sig["recommended_shares"] = shares
                sig["risk_amount"] = round((sig["price"] - stop) * shares, 0)
                buy_signals.append(sig)

            elif sig["signal"] == "SELL" and ticker in open_tickers:
                sig["ticker"] = ticker
                sell_signals.append(sig)

        except Exception as e:
            error_count += 1
            if error_count <= 5:
                print(f"{ticker} エラー: {e}")

        # 進捗表示（50銘柄ごと）
        if (i + 1) % 50 == 0:
            print(f"  スキャン中: {i + 1}/{total}")

    print(f"スキャン完了: {total}銘柄 / 買い{len(buy_signals)} / 売り{len(sell_signals)} / エラー{error_count}")

    # 買いシグナルをRSIの低い順（まだ過熱していない順）でソート
    buy_signals.sort(key=lambda s: s["rsi"])

    # 通知用シグナル（買い上位10 + 保有銘柄の売り）
    notify_signals = sell_signals + buy_signals[:10]

    # トレーリングストップ更新 + 損切りチェック
    trailing_stop_exits = []
    for pos in open_positions:
        ticker = pos["ticker"]
        try:
            df = fetch_stock_data(ticker, period="5d")
            current = float(df["Close"].iloc[-1])
        except Exception:
            current = pos["entry_price"]

        # 高値更新時にストップを引き上げ
        updated = update_trailing_stop(ticker, current)

        # ストップ価格に達したら損切り（ペーパーモードのみ自動執行）
        if updated and mode == "paper":
            stop = updated.get("stop_price", pos["entry_price"] * 0.95)
            if current <= stop:
                trailing_stop_exits.append({"ticker": ticker, "price": current})

    # ペーパートレード: 自動売買
    if mode == "paper":
        # トレーリングストップによる売却
        for ts in trailing_stop_exits:
            record_exit(ts["ticker"], ts["price"])

        for sig in sell_signals:
            # 既にトレーリングストップで売却済みの場合はスキップ
            ts_tickers = {ts["ticker"] for ts in trailing_stop_exits}
            if sig["ticker"] not in ts_tickers:
                record_exit(sig["ticker"], sig["price"])

        open_positions = get_open_positions()
        open_tickers = {p["ticker"] for p in open_positions}

        for sig in buy_signals:
            if sig["ticker"] not in open_tickers and len(open_tickers) < account["max_positions"]:
                if sig.get("recommended_shares"):
                    record_entry(sig["ticker"], sig["price"], sig["recommended_shares"])
                    open_tickers.add(sig["ticker"])

    # 保有ポジションに現在価格を付与
    open_positions = get_open_positions()
    for pos in open_positions:
        for sig in buy_signals + sell_signals:
            if sig["ticker"] == pos["ticker"]:
                pos["current_price"] = sig["price"]
                break
        # 保有中だがシグナル対象外の場合、個別に価格取得
        if "current_price" not in pos:
            try:
                df = fetch_stock_data(pos["ticker"], period="5d")
                pos["current_price"] = float(df["Close"].iloc[-1])
            except Exception:
                pos["current_price"] = pos["entry_price"]

    # Discord通知（1メッセージにまとめる）
    mode_label = "【試験運用中】" if mode == "paper" else ""
    content = f"📊 **シグナル [{session_name}]** {mode_label}"

    embeds = []

    # スキャンサマリー
    embeds.append({
        "title": "🔍 日経225スキャン結果",
        "description": f"スキャン: {total}銘柄 → 買い: {len(buy_signals)}銘柄 / 売り: {len(sell_signals)}銘柄",
        "color": 0x607D8B,
    })

    # 買いシグナル上位5銘柄をコンパクトに1つのEmbedにまとめる
    if buy_signals:
        top5 = buy_signals[:5]
        lines = []
        for sig in top5:
            name = TICKER_NAMES.get(sig["ticker"], sig["ticker"])
            code = sig["ticker"].replace(".T", "")
            stop_pct = (sig.get("stop_loss", 0) / sig["price"] - 1) * 100 if sig.get("stop_loss") else -5
            lines.append(
                f"**{name}**（{code}）¥{sig['price']:,.0f} | RSI {sig['rsi']:.1f} | "
                f"{sig.get('recommended_shares', '-')}株推奨"
            )
        embeds.append({
            "title": f"🟢 買い候補 TOP5",
            "description": "\n".join(lines),
            "color": 0x00C853,
        })

    # 売りシグナル + トレーリングストップ
    sell_lines = []
    for ts in trailing_stop_exits:
        name = TICKER_NAMES.get(ts["ticker"], ts["ticker"])
        code = ts["ticker"].replace(".T", "")
        sell_lines.append(f"**{name}**（{code}）¥{ts['price']:,.0f} | トレーリングストップ発動")
    for sig in sell_signals:
        ts_tickers = {ts["ticker"] for ts in trailing_stop_exits}
        if sig["ticker"] not in ts_tickers:
            name = TICKER_NAMES.get(sig["ticker"], sig["ticker"])
            code = sig["ticker"].replace(".T", "")
            sell_lines.append(f"**{name}**（{code}）¥{sig['price']:,.0f} | {sig['reason']}")
    if sell_lines:
        embeds.append({
            "title": "🔴 売りシグナル",
            "description": "\n".join(sell_lines),
            "color": 0xFF1744,
        })

    # 保有ポジション
    if open_positions:
        from datetime import date as dt_date
        lines = []
        total_pnl = 0
        total_value = 0
        for pos in open_positions:
            name = TICKER_NAMES.get(pos["ticker"], pos["ticker"])
            entry = pos["entry_price"]
            current = pos.get("current_price", entry)
            pnl = (current - entry) * pos["shares"]
            pnl_pct = (current / entry - 1) * 100
            value = current * pos["shares"]
            sign = "+" if pnl >= 0 else ""
            total_pnl += pnl
            total_value += value
            days = (dt_date.today() - dt_date.fromisoformat(pos["entry_date"])).days
            lines.append(
                f"**{name}** ¥{entry:,.0f}→¥{current:,.0f}（{sign}{pnl_pct:.1f}%）"
                f"{pos['shares']}株 | {sign}¥{pnl:,.0f} | {days}日目"
            )
        total_sign = "+" if total_pnl >= 0 else ""
        lines.append(f"\n**合計** 評価額 ¥{total_value:,.0f} / 含み損益 {total_sign}¥{total_pnl:,.0f}")
        embeds.append({
            "title": "📈 仮想保有中" if mode == "paper" else "📈 保有中",
            "description": "\n".join(lines),
            "color": 0x2196F3,
        })

    # 金曜引け後は週次レポートを追加
    if is_friday_close():
        weekly = get_weekly_report()
        embeds.append(format_weekly_embed(weekly, balance))

    # 1回のWebhookで送信（最大10 embeds）
    send_discord(webhook_url, embeds[:10], content=content)


def main():
    config = load_config()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config["discord"].get("webhook_url")
    try:
        run()
    except Exception as e:
        print(f"致命的エラー: {e}", file=sys.stderr)
        try:
            send_error(webhook_url, e)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
