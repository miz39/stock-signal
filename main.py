#!/usr/bin/env python3
"""
S株スイングトレード シグナル通知ツール
cronから1日3回実行し、Discord Webhookで売買シグナルを通知する。
日経225全銘柄を簡易スキャンし、シグナルが出た銘柄のみ通知する。
"""

import os
import sys
import json
import yaml
from datetime import datetime, date, timezone, timedelta

from data import fetch_stock_data
from strategy import generate_signal
from risk import calculate_stop_loss, calculate_position_size
from portfolio import (
    get_open_positions,
    get_weekly_report,
    get_cash_balance,
    get_recently_stopped_tickers,
    record_entry,
    record_exit,
    record_partial_exit,
    update_trailing_stop,
)
from notifier import (
    send_discord,
    send_error,
    format_signal_embeds,
    format_weekly_embed,
    TICKER_NAMES,
)
from nikkei225 import NIKKEI_225, get_sector


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

    # 利確パラメータ
    strat = config.get("strategy", {})
    profit_tighten_pct = strat.get("profit_tighten_pct", 0.03)
    profit_tighten_trail = strat.get("profit_tighten_trail", 0.04)
    profit_take_pct = strat.get("profit_take_pct", 0.07)
    profit_take_ratio = strat.get("profit_take_ratio", 0.5)

    # トレーリングストップ更新 + 利確チェック + 損切りチェック
    trailing_stop_exits = []
    partial_exits = []
    trailing_stop_updates = []
    original_positions = list(open_positions)  # 履歴用に元のポジションを保存
    for pos in open_positions:
        ticker = pos["ticker"]
        name = NIKKEI_225.get(ticker, ticker)
        entry_price = pos["entry_price"]
        try:
            df = fetch_stock_data(ticker, period="5d")
            current = float(df["Close"].iloc[-1])
        except Exception:
            current = entry_price

        # 含み益%を計算
        gain_pct = (current - entry_price) / entry_price

        # Phase 1: +3%以上 → トレーリングストップを-4%に引き締め
        if gain_pct >= profit_tighten_pct:
            updated = update_trailing_stop(ticker, current, trail_pct=profit_tighten_trail)
            if updated:
                print(f"  ストップ引き締め: {name}（{ticker}）含み益{gain_pct*100:.1f}% → トレーリング-{profit_tighten_trail*100:.0f}%")
                trailing_stop_updates.append({
                    "ticker": ticker, "name": name,
                    "new_stop": updated.get("stop_price"),
                    "current_price": current,
                    "gain_pct": round(gain_pct * 100, 1),
                })
        else:
            updated = update_trailing_stop(ticker, current)

        # Phase 2: +7%以上 & 未利確 → 半分利確売り
        if gain_pct >= profit_take_pct and not pos.get("partial_exit_done") and mode == "paper":
            exit_shares = max(1, int(pos["shares"] * profit_take_ratio))
            if exit_shares > 0 and exit_shares < pos["shares"]:
                print(f"  → 利確発動: {name}（{ticker}）含み益{gain_pct*100:.1f}% / {exit_shares}株売却（{pos['shares']}株中）")
                partial_exits.append({
                    "ticker": ticker, "shares": exit_shares, "price": current,
                    "entry_price": entry_price, "gain_pct": round(gain_pct * 100, 1),
                    "total_shares": pos["shares"],
                })

        # ストップ価格に達したら損切り（ペーパーモードのみ自動執行）
        if updated and mode == "paper":
            stop = updated.get("stop_price", entry_price * 0.95)
            print(f"  損切りチェック: {name}（{ticker}）現在値={current:.0f} / ストップ={stop:.0f} / 取得={entry_price:.0f}")
            if current <= stop:
                print(f"  → 損切り発動: {name}")
                trailing_stop_exits.append({
                    "ticker": ticker, "price": current,
                    "entry_price": entry_price, "shares": pos["shares"],
                })

    # ペーパートレード: 自動売買
    executed_entries = []
    if mode == "paper":
        # 部分利確の実行
        for pe in partial_exits:
            record_partial_exit(pe["ticker"], pe["shares"], pe["price"])

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

        # エントリー制限パラメータ
        max_daily = account.get("max_daily_entries", 3)
        max_sector = account.get("max_sector_positions", 2)
        cooldown_days = account.get("cooldown_days", 7)
        daily_entries = 0

        # 現在のセクター別保有数を集計
        sector_counts = {}
        for t in open_tickers:
            sec = get_sector(t)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

        # 再エントリー禁止銘柄（直近N日以内に損切りした銘柄）
        cooldown_tickers = get_recently_stopped_tickers(cooldown_days)

        for sig in buy_signals:
            if daily_entries >= max_daily:
                break
            if sig["ticker"] not in open_tickers and len(open_tickers) < account["max_positions"]:
                # 再エントリー禁止チェック
                if sig["ticker"] in cooldown_tickers:
                    continue
                # セクター制限チェック
                sec = get_sector(sig["ticker"])
                if sector_counts.get(sec, 0) >= max_sector:
                    continue
                if sig.get("recommended_shares"):
                    record_entry(sig["ticker"], sig["price"], sig["recommended_shares"])
                    open_tickers.add(sig["ticker"])
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
                    daily_entries += 1
                    executed_entries.append({
                        "ticker": sig["ticker"],
                        "name": NIKKEI_225.get(sig["ticker"], sig["ticker"]),
                        "price": sig["price"],
                        "shares": sig["recommended_shares"],
                        "stop_loss": sig.get("stop_loss"),
                        "rsi": round(sig["rsi"], 1),
                        "reason": sig.get("reason", ""),
                    })

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
        cash = get_cash_balance(balance)
        total_assets = total_value + cash
        lines.append(f"\n**合計** 株時価 ¥{total_value:,.0f} / 現金 ¥{cash:,.0f} / 総資産 ¥{total_assets:,.0f}"
                     f"\n含み損益 {total_sign}¥{total_pnl:,.0f}")
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

    # 実行履歴を保存
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)

    # 売りイグジット情報（original_positionsを使用）
    exit_records = []
    for ts in trailing_stop_exits:
        name = NIKKEI_225.get(ts["ticker"], ts["ticker"])
        entry_price = ts.get("entry_price") or next((p["entry_price"] for p in original_positions if p["ticker"] == ts["ticker"]), ts["price"])
        shares = ts.get("shares", 0)
        pnl_per_share = ts["price"] - entry_price
        exit_records.append({
            "ticker": ts["ticker"], "name": name,
            "price": ts["price"], "reason": "トレーリングストップ発動",
            "entry_price": entry_price, "shares": shares,
            "pnl": round(pnl_per_share * shares, 1),
            "pnl_pct": round(pnl_per_share / entry_price * 100, 1) if entry_price else 0,
        })
    for sig in sell_signals:
        ts_tickers = {ts["ticker"] for ts in trailing_stop_exits}
        if sig["ticker"] not in ts_tickers:
            name = NIKKEI_225.get(sig["ticker"], sig["ticker"])
            orig = next((p for p in original_positions if p["ticker"] == sig["ticker"]), None)
            entry_price = orig["entry_price"] if orig else sig["price"]
            shares = orig["shares"] if orig else 0
            pnl_per_share = sig["price"] - entry_price
            exit_records.append({
                "ticker": sig["ticker"], "name": name,
                "price": sig["price"], "reason": sig.get("reason", "売りシグナル"),
                "entry_price": entry_price, "shares": shares,
                "pnl": round(pnl_per_share * shares, 1),
                "pnl_pct": round(pnl_per_share / entry_price * 100, 1) if entry_price else 0,
            })

    partial_exit_records = []
    for pe in partial_exits:
        name = NIKKEI_225.get(pe["ticker"], pe["ticker"])
        entry_price = pe.get("entry_price", pe["price"])
        pnl_per_share = pe["price"] - entry_price
        partial_exit_records.append({
            "ticker": pe["ticker"], "name": name,
            "shares": pe["shares"], "price": pe["price"],
            "entry_price": entry_price,
            "gain_pct": pe.get("gain_pct", 0),
            "total_shares": pe.get("total_shares", 0),
            "pnl": round(pnl_per_share * pe["shares"], 1),
        })

    run_record = {
        "timestamp": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "session": session_name,
        "mode": mode,
        "scan": {
            "total": total,
            "buy_count": len(buy_signals),
            "sell_count": len(sell_signals),
            "error_count": error_count,
        },
        "buy_signals": [
            {
                "ticker": s["ticker"],
                "name": NIKKEI_225.get(s["ticker"], s["ticker"]),
                "price": s["price"],
                "rsi": round(s["rsi"], 1),
                "reason": s.get("reason", ""),
                "stop_loss": s.get("stop_loss"),
                "shares": s.get("recommended_shares"),
            }
            for s in buy_signals[:10]
        ],
        "sell_signals": [
            {
                "ticker": s["ticker"],
                "name": NIKKEI_225.get(s["ticker"], s["ticker"]),
                "price": s["price"],
                "reason": s.get("reason", ""),
            }
            for s in sell_signals
        ],
        "executions": {
            "entries": executed_entries,
            "exits": exit_records,
            "partial_exits": partial_exit_records,
            "trailing_stop_updates": trailing_stop_updates,
        },
        "portfolio_snapshot": {
            "open_count": len(open_positions),
            "stock_value": round(sum(
                p.get("current_price", p["entry_price"]) * p["shares"]
                for p in open_positions
            ), 0),
            "cash": get_cash_balance(balance),
            "total_assets": round(sum(
                p.get("current_price", p["entry_price"]) * p["shares"]
                for p in open_positions
            ) + get_cash_balance(balance), 0),
        },
    }
    save_execution_history(run_record)


def save_execution_history(record: dict):
    """実行履歴をexecution_history.jsonに追記する（30日ローテーション）。"""
    history_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "execution_history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = []

    # 同一日・同一セッションのエントリがあれば上書き
    history = [h for h in history
               if not (h.get("date") == record["date"] and h.get("session") == record["session"])]
    history.append(record)

    # 30日より古いエントリを削除
    cutoff = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=30)).strftime("%Y-%m-%d")
    history = [h for h in history if h.get("date", "") >= cutoff]

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"実行履歴を保存: {history_file}（{len(history)}件）")


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
