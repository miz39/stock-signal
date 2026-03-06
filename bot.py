#!/usr/bin/env python3
"""
Discord Bot — S株スイングトレード シグナル通知
コマンドに応答して分析結果やポートフォリオ情報を返す。
"""

import os
import yaml
import discord
from discord.ext import commands

from data import fetch_stock_data
from strategy import generate_signal
from risk import calculate_stop_loss, calculate_position_size
from portfolio import get_open_positions, get_performance_summary, get_weekly_report, record_entry, record_exit
from notifier import TICKER_NAMES, SIGNAL_COLORS, SIGNAL_LABELS
from agents.coordinator import analyze_all, analyze_ticker
from backtest import run_backtest
from backtest_multi import run_multi_backtest
from nikkei225 import NIKKEI_225

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    if config.get("watchlist") == "nikkei225":
        config["watchlist"] = list(NIKKEI_225.keys())
    return config


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


POLICY_WEBHOOK = None
_COMMAND_HASH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".command_list_hash")

_COMMAND_LIST_EMBED = {
    "title": "🤖 Botコマンド一覧",
    "color": 0x7C4DFF,
    "description": "シグナル通知チャンネルで以下のコマンドが使えます。",
    "fields": [
        {"name": "📊 分析", "value": " ", "inline": False},
        {"name": "!analyze", "value": "全銘柄のマルチエージェント総合分析", "inline": False},
        {"name": "!analyze 7203", "value": "特定銘柄の詳細分析（各エージェントの根拠付き）", "inline": False},
        {"name": "!signal", "value": "テクニカルシグナル簡易表示（SMA/RSI）", "inline": False},
        {"name": "!backtest 7203 3y", "value": "過去データでの戦略シミュレーション", "inline": False},
        {"name": "📝 売買記録", "value": " ", "inline": False},
        {"name": "!buy 7203 2534 3", "value": "買い記録（銘柄コード 価格 株数）", "inline": False},
        {"name": "!sell 7203 2600", "value": "売り記録（銘柄コード 価格）→ 損益自動計算", "inline": False},
        {"name": "📈 確認", "value": " ", "inline": False},
        {"name": "!status", "value": "保有ポジション一覧（評価額・含み損益・合計）", "inline": False},
        {"name": "!weekly", "value": "週次レポート（勝敗・損益・最大DD）", "inline": False},
        {"name": "!watchlist", "value": "監視銘柄一覧", "inline": False},
        {"name": "!rule", "value": "運用ルール表示（売買条件・リスク管理）", "inline": False},
    ],
}


def _post_command_list_if_changed():
    """コマンド一覧の内容が変わった場合のみ投稿する。"""
    if not POLICY_WEBHOOK:
        return
    import hashlib
    import requests

    current_hash = hashlib.sha256(json.dumps(_COMMAND_LIST_EMBED, sort_keys=True).encode()).hexdigest()

    prev_hash = ""
    if os.path.exists(_COMMAND_HASH_FILE):
        with open(_COMMAND_HASH_FILE, "r") as f:
            prev_hash = f.read().strip()

    if current_hash == prev_hash:
        print("コマンド一覧: 変更なし（スキップ）")
        return

    try:
        requests.post(POLICY_WEBHOOK, json={"embeds": [_COMMAND_LIST_EMBED]}, timeout=10)
        with open(_COMMAND_HASH_FILE, "w") as f:
            f.write(current_hash)
        print("コマンド一覧: 更新を投稿しました")
    except Exception as e:
        print(f"コマンド一覧投稿エラー: {e}")


@bot.event
async def on_ready():
    print(f"Bot起動: {bot.user}")


@bot.command(name="signal", help="全銘柄の現在のシグナルを分析")
async def cmd_signal(ctx):
    config = load_config()
    account = config["account"]

    for ticker in config["watchlist"]:
        try:
            df = fetch_stock_data(ticker)
            sig = generate_signal(df, config)
            name = TICKER_NAMES.get(ticker, ticker)
            signal = sig["signal"]
            color = SIGNAL_COLORS.get(signal, 0x9E9E9E)
            label = SIGNAL_LABELS.get(signal, signal)

            embed = discord.Embed(
                title=f"{label} — {name}（{ticker.replace('.T', '')}）",
                color=color,
            )
            embed.add_field(name="価格", value=f"¥{sig['price']:,.0f}", inline=True)
            embed.add_field(name="RSI", value=f"{sig['rsi']:.1f}", inline=True)
            embed.add_field(
                name="トレンド",
                value="上昇" if sig["price"] > sig["sma_trend"] else "下降",
                inline=True,
            )
            embed.add_field(name="根拠", value=sig["reason"], inline=False)

            if signal == "BUY":
                stop = calculate_stop_loss(sig["price"])
                shares = calculate_position_size(
                    account["balance"],
                    account["risk_per_trade"],
                    sig["price"],
                    stop,
                    account["unit"],
                    account.get("max_allocation", 0.15),
                )
                risk_amount = round((sig["price"] - stop) * shares, 0)
                stop_pct = (stop / sig["price"] - 1) * 100
                embed.add_field(
                    name="損切り / 推奨株数",
                    value=(
                        f"¥{stop:,.0f}（{stop_pct:.1f}%）/ "
                        f"{shares}株（リスク ¥{risk_amount:,.0f}以内）"
                    ),
                    inline=False,
                )

            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ {ticker}: {e}")


@bot.command(name="status", help="保有ポジション一覧")
async def cmd_status(ctx):
    config = load_config()
    mode = config.get("mode", "paper")
    positions = get_open_positions()

    if not positions:
        await ctx.send("📭 保有ポジションなし")
        return

    embed = discord.Embed(
        title="📈 仮想保有中" if mode == "paper" else "📈 保有中",
        color=0x2196F3,
    )

    total_cost = 0
    total_value = 0
    total_pnl = 0

    from datetime import date

    for pos in positions:
        ticker = pos["ticker"]
        name = TICKER_NAMES.get(ticker, ticker)
        entry = pos["entry_price"]
        shares = pos["shares"]

        # 現在価格を取得
        try:
            df = fetch_stock_data(ticker, period="5d")
            current = float(df["Close"].iloc[-1])
        except Exception:
            current = entry

        cost = entry * shares
        value = current * shares
        pnl_pct = (current / entry - 1) * 100
        pnl_amount = (current - entry) * shares
        sign = "+" if pnl_amount >= 0 else ""

        total_cost += cost
        total_value += value
        total_pnl += pnl_amount

        entry_date = date.fromisoformat(pos["entry_date"])
        days = (date.today() - entry_date).days

        stop = pos.get("stop_price", entry * 0.95)
        embed.add_field(
            name=f"{name}（{ticker.replace('.T', '')}）",
            value=(
                f"¥{entry:,.0f} → ¥{current:,.0f}（{sign}{pnl_pct:.1f}%）\n"
                f"{shares}株 | 評価額 ¥{value:,.0f} | 含み損益 {sign}¥{pnl_amount:,.0f} | {days}日目\n"
                f"ストップ: ¥{stop:,.0f}"
            ),
            inline=False,
        )

    # 合計サマリー
    total_sign = "+" if total_pnl >= 0 else ""
    total_pnl_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0

    embed.add_field(
        name="━━━ 合計 ━━━",
        value=(
            f"取得額: ¥{total_cost:,.0f}\n"
            f"評価額: ¥{total_value:,.0f}\n"
            f"含み損益: {total_sign}¥{total_pnl:,.0f}（{total_sign}{total_pnl_pct:.1f}%）"
        ),
        inline=False,
    )

    await ctx.send(embed=embed)


@bot.command(name="weekly", help="週次レポート")
async def cmd_weekly(ctx):
    config = load_config()
    balance = config["account"]["balance"]
    weekly = get_weekly_report()

    total_pnl_pct = (weekly["total_pnl"] / balance) * 100 if balance else 0
    weekly_pnl_pct = (weekly["weekly_pnl"] / balance) * 100 if balance else 0
    sign_w = "+" if weekly["weekly_pnl"] >= 0 else ""
    sign_t = "+" if weekly["total_pnl"] >= 0 else ""

    embed = discord.Embed(title="📋 週次レポート", color=0xFFC107)
    embed.add_field(
        name="トレード",
        value=f"{weekly['weekly_trades']}回（勝{weekly['weekly_wins']} 負{weekly['weekly_losses']}）",
        inline=True,
    )
    embed.add_field(
        name="週間損益",
        value=f"{sign_w}¥{weekly['weekly_pnl']:,.0f}（{sign_w}{weekly_pnl_pct:.2f}%）",
        inline=True,
    )
    embed.add_field(
        name="累計損益",
        value=f"{sign_t}¥{weekly['total_pnl']:,.0f}（{sign_t}{total_pnl_pct:.2f}%）",
        inline=True,
    )
    embed.add_field(name="最大DD", value=f"¥{weekly['max_drawdown']:,.0f}", inline=True)

    await ctx.send(embed=embed)


@bot.command(name="watchlist", help="監視銘柄一覧")
async def cmd_watchlist(ctx):
    config = load_config()
    lines = []
    for ticker in config["watchlist"]:
        name = TICKER_NAMES.get(ticker, ticker)
        lines.append(f"• **{name}**（{ticker.replace('.T', '')}）")

    embed = discord.Embed(
        title="📋 監視銘柄",
        description="\n".join(lines),
        color=0x2196F3,
    )
    await ctx.send(embed=embed)


@bot.command(name="rule", help="運用ルール表示")
async def cmd_rule(ctx):
    config = load_config()
    strat = config["strategy"]
    acct = config["account"]
    mode = config.get("mode", "paper")

    embed = discord.Embed(title="📘 運用ルール", color=0x1565C0)
    embed.add_field(
        name="買いシグナル",
        value=(
            f"SMA{strat['sma_short']} > SMA{strat['sma_long']}（GC）\n"
            f"RSI({strat['rsi_period']}) < {strat['rsi_overbought']}\n"
            f"価格 > SMA{strat['sma_trend']}"
        ),
        inline=False,
    )
    embed.add_field(
        name="売りシグナル",
        value=f"SMA{strat['sma_short']} < SMA{strat['sma_long']}（DC）\nまたは RSI > 75",
        inline=False,
    )
    embed.add_field(
        name="リスク管理",
        value=(
            f"1トレード: 残高の{int(acct['risk_per_trade']*100)}%\n"
            f"同時保有: {acct['max_positions']}銘柄まで\n"
            f"損切り: -5%（トレーリングストップ）"
        ),
        inline=True,
    )
    embed.add_field(
        name="モード",
        value=f"{'ペーパー（仮想）' if mode == 'paper' else '実運用'} / ¥{acct['balance']:,}",
        inline=True,
    )
    await ctx.send(embed=embed)


@bot.command(name="buy", help="買い記録: !buy 7203 2534 3")
async def cmd_buy(ctx, ticker_input=None, price=None, shares=None):
    if not ticker_input or not price or not shares:
        await ctx.send("⚠️ 使い方: `!buy 銘柄コード 価格 株数`\n例: `!buy 7203 2534 3`")
        return

    try:
        price = float(price)
        shares = int(shares)
    except ValueError:
        await ctx.send("⚠️ 価格は数値、株数は整数で入力してください")
        return

    t = ticker_input if ticker_input.endswith(".T") else ticker_input + ".T"
    name = TICKER_NAMES.get(t, t.replace(".T", ""))

    config = load_config()
    open_pos = get_open_positions()
    if len(open_pos) >= config["account"]["max_positions"]:
        await ctx.send(f"⚠️ 同時保有上限（{config['account']['max_positions']}銘柄）に達しています")
        return

    trade = record_entry(t, price, shares)

    from risk import calculate_stop_loss
    stop = calculate_stop_loss(price)

    embed = discord.Embed(title=f"✅ 買い記録 — {name}", color=0x00C853)
    embed.add_field(name="価格", value=f"¥{price:,.0f}", inline=True)
    embed.add_field(name="株数", value=f"{shares}株", inline=True)
    embed.add_field(name="合計", value=f"¥{price * shares:,.0f}", inline=True)
    embed.add_field(name="損切りライン", value=f"¥{stop:,.0f}（-5%）", inline=True)
    embed.add_field(name="日付", value=trade["entry_date"], inline=True)
    await ctx.send(embed=embed)


@bot.command(name="sell", help="売り記録: !sell 7203 2600")
async def cmd_sell(ctx, ticker_input=None, price=None):
    if not ticker_input or not price:
        await ctx.send("⚠️ 使い方: `!sell 銘柄コード 価格`\n例: `!sell 7203 2600`")
        return

    try:
        price = float(price)
    except ValueError:
        await ctx.send("⚠️ 価格は数値で入力してください")
        return

    t = ticker_input if ticker_input.endswith(".T") else ticker_input + ".T"
    name = TICKER_NAMES.get(t, t.replace(".T", ""))

    trade = record_exit(t, price)
    if not trade:
        await ctx.send(f"⚠️ {name} のオープンポジションが見つかりません")
        return

    pnl = trade["pnl"]
    pnl_pct = (price / trade["entry_price"] - 1) * 100
    sign = "+" if pnl >= 0 else ""
    color = 0x00C853 if pnl >= 0 else 0xFF1744

    embed = discord.Embed(title=f"💰 売り記録 — {name}", color=color)
    embed.add_field(name="買値 → 売値", value=f"¥{trade['entry_price']:,.0f} → ¥{price:,.0f}", inline=False)
    embed.add_field(name="損益", value=f"{sign}¥{pnl:,.0f}（{sign}{pnl_pct:.1f}%）", inline=True)
    embed.add_field(name="株数", value=f"{trade['shares']}株", inline=True)
    embed.add_field(name="日付", value=trade["exit_date"], inline=True)
    await ctx.send(embed=embed)


@bot.command(name="backtest", help="バックテスト: !backtest 7203 3y")
async def cmd_backtest(ctx, ticker_input=None, period="3y"):
    if not ticker_input:
        await ctx.send("⚠️ 使い方: `!backtest 銘柄コード [期間]`\n例: `!backtest 7203 3y`\n期間: 1y / 2y / 3y / 5y")
        return

    t = ticker_input if ticker_input.endswith(".T") else ticker_input + ".T"
    name = TICKER_NAMES.get(t, t.replace(".T", ""))

    await ctx.send(f"⏳ {name} のバックテスト実行中（過去{period}）...")

    config = load_config()
    try:
        result = run_backtest(t, config, period=period)
    except Exception as e:
        await ctx.send(f"❌ エラー: {e}")
        return

    s = result["summary"]
    color = 0x00C853 if s["total_return_pct"] >= 0 else 0xFF1744
    sign = "+" if s["total_return_pct"] >= 0 else ""

    # サマリー Embed
    embed = discord.Embed(
        title=f"📊 バックテスト — {name}（過去{period}）",
        color=color,
    )
    embed.add_field(
        name="リターン",
        value=f"¥{s['initial_balance']:,.0f} → ¥{s['final_balance']:,.0f}（{sign}{s['total_return_pct']}%）",
        inline=False,
    )
    embed.add_field(name="トレード数", value=f"{s['trade_count']}回", inline=True)
    embed.add_field(name="勝率", value=f"{s['win_rate']}%（{s['win_count']}勝{s['loss_count']}敗）", inline=True)
    embed.add_field(name="平均保有日数", value=f"{s['avg_days_held']}日", inline=True)
    embed.add_field(name="平均利益", value=f"+{s['avg_win_pct']}%", inline=True)
    embed.add_field(name="平均損失", value=f"{s['avg_loss_pct']}%", inline=True)
    embed.add_field(name="PF", value=f"{s['profit_factor']}", inline=True)
    embed.add_field(name="最大DD", value=f"{s['max_drawdown_pct']}%", inline=True)

    await ctx.send(embed=embed)

    # 直近5トレードの詳細
    recent = result["trades"][-5:]
    if recent:
        detail = discord.Embed(title="直近トレード", color=0x607D8B)
        for t_rec in recent:
            sign_t = "+" if t_rec["pnl"] >= 0 else ""
            detail.add_field(
                name=f"{t_rec['entry_date']} → {t_rec['exit_date']}（{t_rec['days_held']}日）",
                value=(
                    f"¥{t_rec['entry_price']:,.0f} → ¥{t_rec['exit_price']:,.0f} "
                    f"({sign_t}{t_rec['pnl_pct']}%) | {t_rec['reason']}"
                ),
                inline=False,
            )
        await ctx.send(embed=detail)


@bot.command(name="simulate", help="マルチエージェント3年シミュレーション: !simulate 3y")
async def cmd_simulate(ctx, period="3y"):
    await ctx.send(f"⏳ 日経225 マルチエージェントシミュレーション開始（過去{period}）...\n225銘柄のデータ取得と分析に数分かかります。")

    config = load_config()
    tickers = list(NIKKEI_225.keys())

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: run_multi_backtest(tickers, config, period=period)
        )
    except Exception as e:
        await ctx.send(f"❌ エラー: {e}")
        return

    if "error" in result:
        await ctx.send(f"❌ {result['error']}")
        return

    s = result["summary"]
    color = 0x00C853 if s["total_return_pct"] >= 0 else 0xFF1744
    sign = "+" if s["total_return_pct"] >= 0 else ""

    # メイン結果
    embed = discord.Embed(
        title=f"🧪 マルチエージェント シミュレーション（過去{period}）",
        description=f"日経225から{s['stocks_analyzed']}銘柄を分析、週次判断でポートフォリオ運用",
        color=color,
    )
    embed.add_field(
        name="リターン",
        value=f"¥{s['initial_balance']:,.0f} → ¥{s['final_balance']:,.0f}（{sign}{s['total_return_pct']}%）",
        inline=False,
    )
    embed.add_field(name="トレード数", value=f"{s['trade_count']}回", inline=True)
    embed.add_field(name="勝率", value=f"{s['win_rate']}%（{s['win_count']}勝{s['loss_count']}敗）", inline=True)
    embed.add_field(name="平均保有", value=f"{s['avg_days_held']}日", inline=True)
    embed.add_field(name="平均利益", value=f"+{s['avg_win_pct']}%", inline=True)
    embed.add_field(name="平均損失", value=f"{s['avg_loss_pct']}%", inline=True)
    embed.add_field(name="PF", value=f"{s['profit_factor']}", inline=True)
    embed.add_field(name="最大DD", value=f"{s['max_drawdown_pct']}%", inline=True)

    # ベンチマーク比較
    if s.get("benchmark_return_pct") is not None:
        bm = s["benchmark_return_pct"]
        bm_sign = "+" if bm >= 0 else ""
        alpha = s.get("alpha", 0)
        alpha_sign = "+" if alpha >= 0 else ""
        alpha_emoji = "📈" if alpha > 0 else "📉"
        embed.add_field(
            name="vs 日経225（buy-and-hold）",
            value=(
                f"日経225: {bm_sign}{bm}%\n"
                f"戦略: {sign}{s['total_return_pct']}%\n"
                f"{alpha_emoji} α（超過リターン）: {alpha_sign}{alpha}%"
            ),
            inline=False,
        )

    # 年次リターン
    if s.get("annual_returns"):
        annual_text = "\n".join(
            f"{ar['year']}: {'+' if ar['return'] >= 0 else ''}{ar['return']}%"
            for ar in s["annual_returns"]
        )
        embed.add_field(name="年次リターン", value=annual_text, inline=False)

    await ctx.send(embed=embed)

    # 直近10トレード
    recent = result["trades"][-10:]
    if recent:
        detail = discord.Embed(title="直近トレード", color=0x607D8B)
        for t_rec in recent:
            name = TICKER_NAMES.get(t_rec["ticker"], t_rec["ticker"])
            sign_t = "+" if t_rec["pnl"] >= 0 else ""
            detail.add_field(
                name=f"{name} | {t_rec['entry_date']} → {t_rec['exit_date']}（{t_rec['days_held']}日）",
                value=f"¥{t_rec['entry_price']:,.0f} → ¥{t_rec['exit_price']:,.0f}（{sign_t}{t_rec['pnl_pct']}%）| {t_rec['reason']}",
                inline=False,
            )
        await ctx.send(embed=detail)


AGENT_COLORS = {
    "STRONG_BUY": 0x00C853,
    "BUY": 0x66BB6A,
    "HOLD": 0x9E9E9E,
    "SELL": 0xEF5350,
    "STRONG_SELL": 0xFF1744,
}

SCORE_BAR = {
    (-2.0, -1.0): "🔴🔴",
    (-1.0, -0.3): "🔴",
    (-0.3, 0.3): "⚪",
    (0.3, 1.0): "🟢",
    (1.0, 2.1): "🟢🟢",
}


def _score_icon(score):
    for (lo, hi), icon in SCORE_BAR.items():
        if lo <= score < hi:
            return icon
    return "⚪"


@bot.command(name="analyze", help="マルチエージェント総合分析")
async def cmd_analyze(ctx, ticker_input=None):
    await ctx.send("🔍 分析中...")
    config = load_config()

    if ticker_input:
        # 特定銘柄の詳細分析
        t = ticker_input if ticker_input.endswith(".T") else ticker_input + ".T"
        try:
            result = analyze_ticker(t, config)
        except Exception as e:
            await ctx.send(f"❌ {t}: {e}")
            return

        name = TICKER_NAMES.get(t, t)
        color = AGENT_COLORS.get(result["signal"], 0x9E9E9E)

        # 総合スコア Embed
        embed = discord.Embed(
            title=f"🔎 総合分析 — {name}（{t.replace('.T', '')}）",
            description=f"**{result['signal_label']}**（スコア: {result['total_score']:+.2f}）",
            color=color,
        )

        # 各エージェントの結果
        for agent in result["agents"]:
            icon = _score_icon(agent["score"])
            reasons_text = "\n".join(f"• {r}" for r in agent["reasons"][:3])
            embed.add_field(
                name=f"{icon} {agent['agent']}（{agent['score']:+.2f}）",
                value=reasons_text or "—",
                inline=False,
            )

        await ctx.send(embed=embed)
    else:
        # 全銘柄の総合分析
        results = analyze_all(config)
        for result in results:
            name = TICKER_NAMES.get(result["ticker"], result["ticker"])
            color = AGENT_COLORS.get(result["signal"], 0x9E9E9E)

            embed = discord.Embed(
                title=f"{name}（{result['ticker'].replace('.T', '')}）",
                color=color,
            )
            embed.add_field(
                name="総合判断",
                value=f"**{result['signal_label']}**（{result['total_score']:+.2f}）",
                inline=False,
            )

            # 各エージェントのスコア一覧
            agent_lines = []
            for agent in result["agents"]:
                icon = _score_icon(agent["score"])
                agent_lines.append(f"{icon} {agent['agent']}: {agent['score']:+.2f}")
            embed.add_field(
                name="エージェント別スコア",
                value="\n".join(agent_lines),
                inline=False,
            )

            # 主要な理由
            embed.add_field(
                name="主な根拠",
                value="\n".join(f"• {r}" for r in result["reasons_summary"][:4]) or "—",
                inline=False,
            )

            await ctx.send(embed=embed)


if __name__ == "__main__":
    config = load_config()
    POLICY_WEBHOOK = os.environ.get("DISCORD_POLICY_WEBHOOK_URL") or config["discord"].get("policy_webhook_url")
    token = os.environ.get("DISCORD_BOT_TOKEN") or config["discord"].get("bot_token")
    if not token:
        print("エラー: DISCORD_BOT_TOKEN が設定されていません", file=__import__('sys').stderr)
        __import__('sys').exit(1)
    bot.run(token)
