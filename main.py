#!/usr/bin/env python3
"""
S株スイングトレード シグナル通知ツール
cronから1日3回実行し、Discord Webhookで売買シグナルを通知する。
日経225全銘柄を簡易スキャンし、シグナルが出た銘柄のみ通知する。
"""

import argparse
import copy
import fcntl
import glob
import math
import logging
import logging.handlers
import os
import sys
import json
import tempfile
import yaml
from datetime import datetime, date, timezone, timedelta

from data import fetch_stock_data
from strategy import generate_signal, detect_market_regime, compute_composite_score
from risk import calculate_stop_loss, calculate_position_size
from portfolio import (
    get_open_positions,
    get_weekly_report,
    get_cash_balance,
    get_recently_stopped_tickers,
    get_consecutive_loss_tickers,
    record_entry,
    record_exit,
    record_partial_exit,
    record_topup,
    update_trailing_stop,
    move_stop_to_breakeven,
    set_profile,
    TRADES_FILE,
)
from notifier import (
    send_discord,
    send_error,
    send_slack,
    send_slack_error,
    format_signal_embeds,
    format_signal_mrkdwn,
    format_weekly_embed,
    format_weekly_mrkdwn,
    TICKER_NAMES,
)
from nikkei225 import NIKKEI_225, get_sector
from holidays import is_market_open
from portfolio_risk import check_sector_concentration, check_portfolio_drawdown, check_anomalies


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger("signal")
logger.setLevel(logging.INFO)
_file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(_BASE_DIR, "signal.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_file_handler)
logger.addHandler(_console_handler)

CONFIG_PATH = os.path.join(_BASE_DIR, "config.yaml")


def validate_config(config: dict) -> None:
    """config.yamlの値を検証する。不正な場合はValueErrorを送出。"""
    account = config.get("account", {})
    strategy = config.get("strategy", {})

    if account.get("balance", 0) <= 0:
        raise ValueError("account.balance must be > 0")
    if not (0 < account.get("risk_per_trade", 0) <= 0.1):
        raise ValueError("account.risk_per_trade must be in (0, 0.1]")
    if not (0 < account.get("max_allocation", 0) <= 1):
        raise ValueError("account.max_allocation must be in (0, 1]")
    if account.get("max_positions", 0) <= 0:
        raise ValueError("account.max_positions must be > 0")

    rsi_min = strategy.get("rsi_entry_min", 50)
    rsi_max = strategy.get("rsi_entry_max", 65)
    rsi_ob = strategy.get("rsi_overbought", 70)
    rsi_os = strategy.get("rsi_oversold", 30)
    if not (rsi_os < rsi_min < rsi_max < rsi_ob):
        raise ValueError(
            f"RSI thresholds must satisfy: oversold({rsi_os}) < entry_min({rsi_min}) "
            f"< entry_max({rsi_max}) < overbought({rsi_ob})"
        )


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

    if current < 11 * 60:       # 11:00 より前
        return "8:50 寄り前"
    elif current < 15 * 60:     # 15:00 より前（Macスリープ遅延を考慮）
        return "12:35 昼"
    else:
        return "15:10 引け後"


def is_friday_close() -> bool:
    """金曜日の引け後（15:00以降）かどうか（JST）。"""
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    return now.weekday() == 4 and now.hour >= 15


def run(profile_name: str = "default"):
    if not is_market_open():
        logger.info("東証休場日のためスキップ")
        return

    config = load_config()

    # Merge profile-specific strategy overrides
    if profile_name != "default":
        profile_overrides = config.get("profiles", {}).get(profile_name, {})
        if not profile_overrides:
            logger.error(f"プロファイル '{profile_name}' が config.yaml に見つかりません")
            return
        strategy_overrides = profile_overrides.get("strategy", {})
        config["strategy"] = {**config.get("strategy", {}), **strategy_overrides}

    validate_config(config)

    # Switch trades file for this profile
    set_profile(profile_name)

    # Pre-execution backup (1 per day, keep 7 days)
    if os.path.exists(TRADES_FILE):
        backup_dir = os.path.join(_BASE_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        today_str = date.today().isoformat()
        backup_name = os.path.basename(TRADES_FILE).replace(".json", f"_{today_str}.json")
        backup_path = os.path.join(backup_dir, backup_name)
        if not os.path.exists(backup_path):
            import shutil
            shutil.copy2(TRADES_FILE, backup_path)
            logger.info(f"バックアップ作成: {backup_path}")
        # Clean old backups (> 7 days)
        prefix = os.path.basename(TRADES_FILE).replace(".json", "_")
        for old in sorted(glob.glob(os.path.join(backup_dir, f"{prefix}*.json")))[:-7]:
            os.remove(old)

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config["discord"].get("webhook_url")
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or config.get("slack", {}).get("webhook_url", "")
    mode = config.get("mode", "paper")
    account = config["account"]
    balance = account["balance"]

    session_name = get_session_name()
    profile_label = f" [{profile_name}]" if profile_name != "default" else ""

    strat = config.get("strategy", {})

    # 市場レジーム判定
    market_regime = {"regime": "neutral", "sma50": None, "sma200": None, "price": 0}
    if strat.get("market_regime_enabled", True):
        try:
            nikkei_df = fetch_stock_data("^N225")
            market_regime = detect_market_regime(nikkei_df)
            logger.info(f"市場レジーム: {market_regime['regime'].upper()} "
                        f"（日経={market_regime['price']:,.1f} / SMA50={market_regime['sma50']} / SMA200={market_regime['sma200']}）")
        except Exception as e:
            logger.warning(f"日経225データ取得失敗（レジーム=neutral扱い）: {e}")

    # 現金残高とポジションサイジング用の総資産を計算
    available_cash = max(get_cash_balance(balance), 0)
    open_positions = get_open_positions()
    stock_value = sum(p["entry_price"] * p["shares"] for p in open_positions)
    total_assets = available_cash + stock_value

    # 保有中の銘柄（売りシグナルチェック用）
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
                stop_pct = strat.get("stop_loss_pct", 0.08)
                stop = calculate_stop_loss(sig["price"], stop_pct)
                shares = calculate_position_size(
                    total_assets,
                    account["risk_per_trade"],
                    sig["price"],
                    stop,
                    account["unit"],
                    account.get("max_allocation", 0.15),
                )
                # Cap by available cash
                max_affordable = math.floor(available_cash / sig["price"]) if sig["price"] > 0 else 0
                shares = min(shares, max(max_affordable, 0))
                sig["stop_loss"] = stop
                sig["recommended_shares"] = shares
                sig["risk_amount"] = round((sig["price"] - stop) * shares, 0)
                sig["_df"] = df
                buy_signals.append(sig)

            elif sig["signal"] == "SELL" and ticker in open_tickers:
                sig["ticker"] = ticker
                sell_signals.append(sig)

        except Exception as e:
            error_count += 1
            if error_count <= 5:
                logger.error(f"{ticker} エラー: {e}")

        # 進捗表示（50銘柄ごと）
        if (i + 1) % 50 == 0:
            logger.info(f"  スキャン中: {i + 1}/{total}")

    logger.info(f"スキャン完了: {total}銘柄 / 買い{len(buy_signals)} / 売り{len(sell_signals)} / エラー{error_count}")

    # 複合スコアを計算してソート（dfキャッシュを利用、追加API呼び出しゼロ）
    score_weights = strat.get("score_weights")
    for sig in buy_signals:
        cached_df = sig.pop("_df", None)
        if cached_df is not None:
            sig["composite_score"] = compute_composite_score(sig, cached_df, score_weights)
        else:
            sig["composite_score"] = 0.0

    buy_signals.sort(key=lambda s: s.get("composite_score", 0), reverse=True)

    # 通知用シグナル（買い上位10 + 保有銘柄の売り）
    notify_signals = sell_signals + buy_signals[:10]

    # 利確パラメータ
    profit_tighten_pct = strat.get("profit_tighten_pct", 0.06)
    profit_take_pct = strat.get("profit_take_pct", 0.08)
    profit_take_ratio = strat.get("profit_take_ratio", 0.5)
    profit_take_full_pct = strat.get("profit_take_full_pct", 0.15)
    stop_loss_pct = strat.get("stop_loss_pct", 0.08)

    # トレーリングストップ更新 + 利確チェック + 損切りチェック
    trailing_stop_exits = []
    partial_exits = []
    full_profit_exits = []
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

        # Phase 1: +6%以上 → ストップを建値（取得価格）に移動
        if gain_pct >= profit_tighten_pct:
            result = move_stop_to_breakeven(ticker)
            if result and result.get("stop_price") >= entry_price:
                logger.info(f"  建値移動: {name}（{ticker}）含み益{gain_pct*100:.1f}% → ストップ=建値¥{entry_price:,.0f}")
                trailing_stop_updates.append({
                    "ticker": ticker, "name": name,
                    "new_stop": result.get("stop_price"),
                    "current_price": current,
                    "gain_pct": round(gain_pct * 100, 1),
                })
            # 建値移動後も高値は更新する
            updated = update_trailing_stop(ticker, current)
        else:
            updated = update_trailing_stop(ticker, current)

        # Phase 2: +15%以上 → 全部利確
        if gain_pct >= profit_take_full_pct and mode == "paper":
            logger.info(f"  → 全部利確: {name}（{ticker}）含み益{gain_pct*100:.1f}% / {pos['shares']}株全売却")
            full_profit_exits.append({
                "ticker": ticker, "price": current,
                "entry_price": entry_price, "shares": pos["shares"],
                "gain_pct": round(gain_pct * 100, 1),
            })
            continue  # 全部利確したので損切りチェック不要

        # Phase 3: +8%以上 & 未利確 → 半分利確売り（1株の場合は全株売却）
        if gain_pct >= profit_take_pct and not pos.get("partial_exit_done") and mode == "paper":
            if pos["shares"] == 1:
                # 1株ポジションは全株売却
                logger.info(f"  → 利確発動（全株）: {name}（{ticker}）含み益{gain_pct*100:.1f}% / 1株売却")
                full_profit_exits.append({
                    "ticker": ticker, "price": current,
                    "entry_price": entry_price, "shares": 1,
                    "gain_pct": round(gain_pct * 100, 1),
                })
                continue
            else:
                exit_shares = max(1, int(pos["shares"] * profit_take_ratio))
                if exit_shares > 0 and exit_shares < pos["shares"]:
                    logger.info(f"  → 半分利確: {name}（{ticker}）含み益{gain_pct*100:.1f}% / {exit_shares}株売却（{pos['shares']}株中）")
                    partial_exits.append({
                        "ticker": ticker, "shares": exit_shares, "price": current,
                        "entry_price": entry_price, "gain_pct": round(gain_pct * 100, 1),
                        "total_shares": pos["shares"],
                    })

        # ストップ価格に達したら損切り（ペーパーモードのみ自動執行）
        if updated and mode == "paper":
            stop = updated.get("stop_price", entry_price * 0.92)
            logger.info(f"  損切りチェック: {name}（{ticker}）現在値={current:.0f} / ストップ={stop:.0f} / 取得={entry_price:.0f}")
            if current <= stop:
                logger.info(f"  → 損切り発動: {name}")
                trailing_stop_exits.append({
                    "ticker": ticker, "price": current,
                    "entry_price": entry_price, "shares": pos["shares"],
                })

    # ペーパートレード: 自動売買
    executed_entries = []
    executed_topups = []
    if mode == "paper":
        # 全部利確の実行
        for fp in full_profit_exits:
            record_exit(fp["ticker"], fp["price"])

        # 部分利確の実行
        for pe in partial_exits:
            record_partial_exit(pe["ticker"], pe["shares"], pe["price"])

        # トレーリングストップによる売却
        for ts in trailing_stop_exits:
            record_exit(ts["ticker"], ts["price"])

        for sig in sell_signals:
            # 既に利確/ストップで売却済みの場合はスキップ
            exited_tickers = {ts["ticker"] for ts in trailing_stop_exits} | {fp["ticker"] for fp in full_profit_exits}
            if sig["ticker"] not in exited_tickers:
                record_exit(sig["ticker"], sig["price"])

        open_positions = get_open_positions()
        open_tickers = {p["ticker"] for p in open_positions}

        # エントリー制限パラメータ（レジームに応じて調整）
        max_daily = account.get("max_daily_entries", 3)
        if market_regime["regime"] == "bear":
            max_daily = min(max_daily, 1)
            logger.info(f"  Bear市場: 新規エントリー上限={max_daily}")
        elif market_regime["regime"] == "neutral":
            max_daily = min(max_daily, 1)
            logger.info(f"  Neutral市場: 新規エントリー上限={max_daily}")
        max_sector = account.get("max_sector_positions", 2)
        cooldown_days = account.get("cooldown_days", 7)
        max_consecutive_losses = account.get("max_consecutive_losses", 2)
        daily_entries = 0

        # 現在のセクター別保有数を集計
        sector_counts = {}
        for t in open_tickers:
            sec = get_sector(t)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

        # 再エントリー禁止銘柄（直近N日以内に損切りした銘柄）
        cooldown_tickers = get_recently_stopped_tickers(cooldown_days)
        # 連続損切り銘柄
        consecutive_loss_tickers = get_consecutive_loss_tickers(max_consecutive_losses)

        for sig in buy_signals:
            if daily_entries >= max_daily:
                break
            if sig["ticker"] not in open_tickers and len(open_tickers) < account["max_positions"]:
                # 再エントリー禁止チェック
                if sig["ticker"] in cooldown_tickers:
                    continue
                # 連続損切りチェック
                if sig["ticker"] in consecutive_loss_tickers:
                    logger.info(f"  連続損切りスキップ: {NIKKEI_225.get(sig['ticker'], sig['ticker'])}")
                    continue
                # セクター制限チェック
                sec = get_sector(sig["ticker"])
                if sector_counts.get(sec, 0) >= max_sector:
                    continue
                if sig.get("recommended_shares"):
                    record_entry(sig["ticker"], sig["price"], sig["recommended_shares"],
                                 stop_pct=stop_loss_pct)
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
                        "sma_short": round(sig["sma_short"], 1) if not math.isnan(sig.get("sma_short", float("nan"))) else None,
                        "sma_long": round(sig["sma_long"], 1) if not math.isnan(sig.get("sma_long", float("nan"))) else None,
                        "sma_trend": round(sig["sma_trend"], 1) if not math.isnan(sig.get("sma_trend", float("nan"))) else None,
                    })

        # Top-up: BUYシグナルが出ている保有銘柄で目標サイズ未満のものを買い増し
        available_cash_now = max(get_cash_balance(balance), 0)
        open_positions_now = get_open_positions()
        stock_value_now = sum(p["entry_price"] * p["shares"] for p in open_positions_now)
        total_assets_now = available_cash_now + stock_value_now

        for sig in buy_signals:
            if sig["ticker"] not in open_tickers:
                continue
            pos = next((p for p in open_positions_now if p["ticker"] == sig["ticker"]), None)
            if not pos:
                continue
            stop_pct = strat.get("stop_loss_pct", 0.08)
            stop = calculate_stop_loss(sig["price"], stop_pct)
            target_shares = calculate_position_size(
                total_assets_now,
                account["risk_per_trade"],
                sig["price"],
                stop,
                account["unit"],
                account.get("max_allocation", 0.15),
            )
            current_shares = pos["shares"]
            additional = target_shares - current_shares
            if additional <= 0:
                continue
            max_affordable = math.floor(available_cash_now / sig["price"]) if sig["price"] > 0 else 0
            additional = min(additional, max_affordable)
            if additional <= 0:
                continue
            record_topup(sig["ticker"], sig["price"], additional, stop_pct)
            available_cash_now -= sig["price"] * additional
            executed_topups.append({
                "ticker": sig["ticker"],
                "name": NIKKEI_225.get(sig["ticker"], sig["ticker"]),
                "price": sig["price"],
                "additional_shares": additional,
                "total_shares": current_shares + additional,
                "old_shares": current_shares,
                "rsi": round(sig["rsi"], 1),
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
    content = f"📊 **シグナル [{session_name}]{profile_label}** {mode_label}"

    embeds = []

    # スキャンサマリー
    regime_label = {"bull": "🟢 Bull", "bear": "🔴 Bear", "neutral": "🟡 Neutral"}.get(market_regime["regime"], "❓")
    regime_info = f"\n市場レジーム: {regime_label}"
    if market_regime.get("sma50") is not None:
        regime_info += f"（日経={market_regime['price']:,.1f} / SMA50={market_regime['sma50']:,.1f} / SMA200={market_regime['sma200']:,.1f}）"
    embeds.append({
        "title": "🔍 日経225スキャン結果",
        "description": f"スキャン: {total}銘柄 → 買い: {len(buy_signals)}銘柄 / 売り: {len(sell_signals)}銘柄{regime_info}",
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
            score_str = f" | Score {sig.get('composite_score', 0):.2f}" if sig.get("composite_score") else ""
            lines.append(
                f"**{name}**（{code}）¥{sig['price']:,.0f} | RSI {sig['rsi']:.1f} | "
                f"{sig.get('recommended_shares', '-')}株推奨{score_str}"
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

    # 買い増し通知
    if executed_topups:
        topup_lines = []
        for tu in executed_topups:
            code = tu["ticker"].replace(".T", "")
            topup_lines.append(
                f"**{tu['name']}**（{code}）+{tu['additional_shares']}株"
                f"（{tu['old_shares']}→{tu['total_shares']}株）"
                f"¥{tu['price']:,.0f} | RSI {tu['rsi']:.1f}"
            )
        embeds.append({
            "title": "🔄 買い増し",
            "description": "\n".join(topup_lines),
            "color": 0xFFA000,
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

    # リスクアラート
    risk_alerts = []
    if open_positions:
        total_value = sum(p.get("current_price", p["entry_price"]) * p["shares"] for p in open_positions)
        current_total = total_value + get_cash_balance(balance)

        # Sector concentration check
        risk_cfg = config.get("risk", {})
        max_sector_pct = risk_cfg.get("max_sector_pct", 0.30)
        sector_alerts = check_sector_concentration(open_positions, current_total, max_pct=max_sector_pct)
        for sa in sector_alerts:
            risk_alerts.append(f"セクター集中: **{sa['sector']}** {sa['pct']}%（¥{sa['value']:,}）")

        # Portfolio drawdown check
        max_dd = risk_cfg.get("max_portfolio_dd", 0.10)
        dd_alert = check_portfolio_drawdown(current_total, balance, max_dd_pct=max_dd)
        if dd_alert:
            risk_alerts.append(
                f"ポートフォリオDD: **{dd_alert['drawdown_pct']}%**"
                f"（ピーク ¥{dd_alert['peak']:,} → 現在 ¥{dd_alert['current']:,}）"
            )

    # Anomaly checks
    anomalies = check_anomalies(config)
    for a in anomalies:
        risk_alerts.append(f"{a['message']}")

    if risk_alerts:
        embeds.append({
            "title": "⚠️ リスクアラート",
            "description": "\n".join(risk_alerts),
            "color": 0xFF6D00,
        })

    # 金曜引け後は週次レポートを追加
    if is_friday_close():
        weekly = get_weekly_report()
        embeds.append(format_weekly_embed(weekly, balance))

    # 1回のWebhookで送信（最大10 embeds）
    send_discord(webhook_url, embeds[:10], content=content)

    # Slack通知（defaultプロファイルのみ）
    if slack_webhook_url and profile_name == "default":
        slack_text = format_signal_mrkdwn(content, embeds[:10])
        if is_friday_close():
            weekly = get_weekly_report()
            slack_text += "\n\n" + format_weekly_mrkdwn(weekly, balance)
        send_slack(slack_webhook_url, slack_text)

    # 実行履歴を保存
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)

    # 売りイグジット情報（original_positionsを使用）
    exit_records = []
    for fp in full_profit_exits:
        name = NIKKEI_225.get(fp["ticker"], fp["ticker"])
        pnl_per_share = fp["price"] - fp["entry_price"]
        reason = f"全部利確（+{fp.get('gain_pct', 0):.1f}%）" if fp.get("gain_pct", 0) >= profit_take_full_pct * 100 else f"利確（+{fp.get('gain_pct', 0):.1f}%）"
        exit_records.append({
            "ticker": fp["ticker"], "name": name,
            "price": fp["price"], "reason": reason,
            "entry_price": fp["entry_price"], "shares": fp["shares"],
            "pnl": round(pnl_per_share * fp["shares"], 1),
            "pnl_pct": round(fp.get("gain_pct", 0), 1),
        })
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
        "profile": profile_name,
        "mode": mode,
        "market_regime": market_regime,
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
                "composite_score": s.get("composite_score"),
                "sma_short": round(s["sma_short"], 1) if not math.isnan(s.get("sma_short", float("nan"))) else None,
                "sma_long": round(s["sma_long"], 1) if not math.isnan(s.get("sma_long", float("nan"))) else None,
                "sma_trend": round(s["sma_trend"], 1) if not math.isnan(s.get("sma_trend", float("nan"))) else None,
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
            "topups": executed_topups,
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
    save_execution_history(run_record, profile_name)


def save_execution_history(record: dict, profile_name: str = "default"):
    """実行履歴をexecution_history.jsonに追記する（設定日数でローテーション+アーカイブ）。"""
    config = load_config()
    history_cfg = config.get("history", {})
    retention_days = history_cfg.get("retention_days", 30)
    archive_enabled = history_cfg.get("archive", True)

    if profile_name == "default":
        filename = "execution_history.json"
        archive_filename = "execution_history_archive.json"
    else:
        filename = f"execution_history_{profile_name}.json"
        archive_filename = f"execution_history_{profile_name}_archive.json"
    base_dir = os.path.dirname(os.path.abspath(__file__))
    history_file = os.path.join(base_dir, filename)
    archive_file = os.path.join(base_dir, archive_filename)

    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    history = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (json.JSONDecodeError, IOError):
            history = []

    # 同一日・同一セッションのエントリがあれば上書き
    history = [h for h in history
               if not (h.get("date") == record["date"] and h.get("session") == record["session"])]
    history.append(record)

    # retention_days より古いエントリを分離
    cutoff = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    keep = [h for h in history if h.get("date", "") >= cutoff]
    expired = [h for h in history if h.get("date", "") < cutoff]

    # expired をアーカイブに追記
    if expired and archive_enabled:
        archived = []
        if os.path.exists(archive_file):
            try:
                with open(archive_file, "r") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        archived = json.load(f)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
            except (json.JSONDecodeError, IOError):
                archived = []

        existing_keys = {(a.get("date"), a.get("session")) for a in archived}
        new_entries = [e for e in expired if (e.get("date"), e.get("session")) not in existing_keys]

        if new_entries:
            archived.extend(new_entries)
            fd, tmp_path = tempfile.mkstemp(dir=base_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(archived, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, archive_file)
            except:
                os.unlink(tmp_path)
                raise
            logger.info(f"アーカイブに{len(new_entries)}件追記: {archive_file}")

    dir_name = os.path.dirname(history_file)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(keep, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, history_file)
    except:
        os.unlink(tmp_path)
        raise
    logger.info(f"実行履歴を保存: {history_file}（{len(keep)}件）")


def main():
    parser = argparse.ArgumentParser(description="S株スイングトレード シグナル通知ツール")
    parser.add_argument(
        "--profile",
        default="default",
        help='プロファイル名（default/conservative/aggressive）。"all" で全プロファイル順次実行',
    )
    args = parser.parse_args()

    config = load_config()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config["discord"].get("webhook_url")
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or config.get("slack", {}).get("webhook_url", "")

    if args.profile == "all":
        profiles = ["default"] + list(config.get("profiles", {}).keys())
    else:
        profiles = [args.profile]

    for profile_name in profiles:
        logger.info(f"=== プロファイル: {profile_name} ===")
        try:
            run(profile_name)
        except Exception as e:
            logger.error(f"致命的エラー [{profile_name}]: {e}")
            try:
                send_error(webhook_url, e)
            except Exception:
                pass
            try:
                if slack_webhook_url:
                    send_slack_error(slack_webhook_url, e)
            except Exception:
                pass
            if len(profiles) == 1:
                sys.exit(1)


if __name__ == "__main__":
    main()
