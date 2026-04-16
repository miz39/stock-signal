import time
import traceback

import requests

# 銘柄コードから日本語名へのマッピング（日経225から読み込み）
from nikkei225 import NIKKEI_225
TICKER_NAMES = NIKKEI_225

SIGNAL_COLORS = {
    "BUY": 0x00C853,   # 緑
    "SELL": 0xFF1744,   # 赤
    "HOLD": 0x9E9E9E,  # グレー
}

SIGNAL_LABELS = {
    "BUY": "買いシグナル",
    "SELL": "売りシグナル",
    "HOLD": "様子見",
}


def send_discord(webhook_url: str, embeds: list, content: str = None) -> bool:
    """Discord Webhookでメッセージを送信する。"""
    if not webhook_url or webhook_url == "YOUR_WEBHOOK_URL":
        # Webhook未設定の場合はコンソール出力にフォールバック
        print("=== Discord通知（コンソール出力） ===")
        if content:
            print(content)
        for embed in embeds:
            print(f"[{embed.get('title', '')}]")
            for field in embed.get("fields", []):
                print(f"  {field['name']}: {field['value']}")
        print("=" * 40)
        return False

    payload = {"embeds": embeds}
    if content:
        payload["content"] = content

    for attempt in range(3):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Discord送信エラー（試行{attempt + 1}/3）: {e}")
            if attempt < 2:
                time.sleep(2)
    return False


def send_error(webhook_url: str, error: Exception) -> None:
    """エラー内容をDiscordに通知する。"""
    embed = {
        "title": "エラー発生",
        "description": f"```\n{traceback.format_exc()}\n```",
        "color": 0xFF0000,
    }
    send_discord(webhook_url, [embed])


def format_signal_embeds(
    signals: list,
    open_positions: list,
    session_name: str,
    mode: str,
    balance: float,
) -> tuple:
    """
    シグナルとポートフォリオ情報からDiscord Embedのリストを生成する。

    Returns:
        (content, embeds) のタプル
    """
    mode_label = "【試験運用中】" if mode == "paper" else ""
    content = f"📊 **シグナル [{session_name}]** {mode_label}"

    embeds = []

    # 各銘柄のシグナル Embed
    for sig in signals:
        ticker = sig["ticker"]
        name = TICKER_NAMES.get(ticker, ticker)
        signal = sig["signal"]
        color = SIGNAL_COLORS.get(signal, 0x9E9E9E)
        label = SIGNAL_LABELS.get(signal, signal)

        embed = {
            "title": f"{label} — {name}（{ticker.replace('.T', '')}）",
            "color": color,
            "fields": [
                {
                    "name": "価格",
                    "value": f"¥{sig['price']:,.0f}",
                    "inline": True,
                },
                {
                    "name": "RSI",
                    "value": f"{sig['rsi']:.1f}",
                    "inline": True,
                },
                {
                    "name": "トレンド",
                    "value": "上昇" if sig["price"] > sig["sma_trend"] else "下降",
                    "inline": True,
                },
                {
                    "name": "根拠",
                    "value": sig["reason"],
                    "inline": False,
                },
            ],
        }

        # 買いシグナルの場合は損切り・推奨株数を追加
        if signal == "BUY" and "stop_loss" in sig:
            stop_pct = (sig["stop_loss"] / sig["price"] - 1) * 100
            embed["fields"].append({
                "name": "損切り / 推奨株数",
                "value": (
                    f"¥{sig['stop_loss']:,.0f}（{stop_pct:.1f}%）/ "
                    f"{sig['recommended_shares']}株（リスク ¥{sig['risk_amount']:,.0f}以内）"
                ),
                "inline": False,
            })

        embeds.append(embed)

    # 保有中ポジションの Embed
    if open_positions:
        fields = []
        for pos in open_positions:
            ticker = pos["ticker"]
            name = TICKER_NAMES.get(ticker, ticker)
            entry = pos["entry_price"]
            current = pos.get("current_price", entry)
            pnl_pct = (current / entry - 1) * 100
            pnl_amount = (current - entry) * pos["shares"]
            sign = "+" if pnl_amount >= 0 else ""

            # 保有日数
            from datetime import date
            entry_date = date.fromisoformat(pos["entry_date"])
            days = (date.today() - entry_date).days

            fields.append({
                "name": f"{name}（{ticker.replace('.T', '')}）",
                "value": (
                    f"¥{entry:,.0f} → ¥{current:,.0f}（{sign}{pnl_pct:.1f}%）\n"
                    f"含み損益: {sign}¥{pnl_amount:,.0f}（{pos['shares']}株）| 保有{days}日目"
                ),
                "inline": False,
            })

        embeds.append({
            "title": "📈 仮想保有中" if mode == "paper" else "📈 保有中",
            "color": 0x2196F3,
            "fields": fields,
        })

    return content, embeds


def format_weekly_embed(weekly: dict, balance: float) -> dict:
    """週次レポートのEmbedを生成する。"""
    total_pnl_pct = (weekly["total_pnl"] / balance) * 100 if balance else 0
    weekly_pnl_pct = (weekly["weekly_pnl"] / balance) * 100 if balance else 0

    sign_w = "+" if weekly["weekly_pnl"] >= 0 else ""
    sign_t = "+" if weekly["total_pnl"] >= 0 else ""

    return {
        "title": "📋 週次レポート",
        "color": 0xFFC107,
        "fields": [
            {
                "name": "トレード",
                "value": f"{weekly['weekly_trades']}回（勝{weekly['weekly_wins']} 負{weekly['weekly_losses']}）",
                "inline": True,
            },
            {
                "name": "週間損益",
                "value": f"{sign_w}¥{weekly['weekly_pnl']:,.0f}（{sign_w}{weekly_pnl_pct:.2f}%）",
                "inline": True,
            },
            {
                "name": "累計損益",
                "value": f"{sign_t}¥{weekly['total_pnl']:,.0f}（{sign_t}{total_pnl_pct:.2f}%）",
                "inline": True,
            },
            {
                "name": "最大DD",
                "value": f"¥{weekly['max_drawdown']:,.0f}",
                "inline": True,
            },
        ],
    }


# --- Slack Webhook ---

def send_slack(webhook_url: str, text: str) -> bool:
    """Slack Incoming Webhookでメッセージを送信する。"""
    if not webhook_url:
        print("=== Slack通知（コンソール出力） ===")
        print(text)
        print("=" * 40)
        return False

    for attempt in range(3):
        try:
            resp = requests.post(webhook_url, json={"text": text}, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"Slack送信エラー（試行{attempt + 1}/3）: {e}")
            if attempt < 2:
                time.sleep(2)
    return False


def send_slack_error(webhook_url: str, error: Exception) -> None:
    """エラー内容をSlackに通知する。"""
    text = f":x: *エラー発生*\n```\n{traceback.format_exc()}\n```"
    send_slack(webhook_url, text)


def format_signal_mrkdwn(
    content: str,
    embeds: list,
) -> str:
    """Discord embeds をSlack mrkdwnテキストに変換する。"""
    lines = [content, ""]
    for embed in embeds:
        title = embed.get("title", "")
        if title:
            lines.append(f"*{title}*")
        desc = embed.get("description", "")
        if desc:
            # Discord markdown (**bold**) -> Slack mrkdwn (*bold*)
            desc = desc.replace("**", "*")
            lines.append(desc)
        for field in embed.get("fields", []):
            name = field.get("name", "")
            value = field.get("value", "").replace("**", "*")
            lines.append(f"_{name}:_ {value}")
        lines.append("")
    return "\n".join(lines).strip()


def format_daily_summary_mrkdwn(
    positions: list,
    summary: dict,
    cash: float,
    market_regime: dict,
    actions: dict,
    today: str = None,
) -> str:
    """引け後の日次サマリ Slack mrkdwn を生成する。

    Args:
        positions: オープンポジション。各要素に ticker, name, current_price,
                   entry_price, shares, stop_price, pnl_pct を含む。
        summary: {"weekly_trades": int, "total_pnl": float,
                  "total_pnl_pct": float, "balance": float}
        cash: 現金残高
        market_regime: {"regime": str, "price": float}
        actions: {"buy": int, "sell": int, "topup": int}
        today: YYYY-MM-DD（省略時は現在日付 JST）
    """
    from datetime import date as _date
    from datetime import datetime as _dt
    from datetime import timezone as _tz, timedelta as _td

    if today is None:
        today = _dt.now(_tz(_td(hours=9))).strftime("%Y-%m-%d")

    lines = [f"*日次サマリ {today}*", ""]

    lines.append(f"*保有状況* ({len(positions)}銘柄)")
    if positions:
        for p in positions:
            ticker = p.get("ticker", "")
            code = ticker.replace(".T", "")
            name = p.get("name") or TICKER_NAMES.get(ticker, code)
            current = p.get("current_price", p.get("entry_price", 0))
            pnl_pct = p.get("pnl_pct")
            if pnl_pct is None:
                entry = p.get("entry_price", 0)
                pnl_pct = (current / entry - 1) * 100 if entry else 0.0
            sign = "+" if pnl_pct >= 0 else ""
            stop = p.get("stop_price")
            stop_str = f" / Stop ¥{stop:,.0f}" if stop else ""
            lines.append(
                f"• {name} ({code}): ¥{current:,.0f} {sign}{pnl_pct:.1f}%{stop_str}"
            )
    else:
        lines.append("• なし")
    lines.append("")

    buy_n = actions.get("buy", 0)
    sell_n = actions.get("sell", 0)
    topup_n = actions.get("topup", 0)
    lines.append("*本日のアクション*")
    lines.append(f"• BUY: {buy_n}件  SELL: {sell_n}件  Topup: {topup_n}件")
    lines.append("")

    weekly_trades = summary.get("weekly_trades", 0)
    total_pnl = summary.get("total_pnl", 0)
    total_pnl_pct = summary.get("total_pnl_pct", 0.0)
    t_sign = "+" if total_pnl >= 0 else ""
    p_sign = "+" if total_pnl_pct >= 0 else ""
    lines.append("*週次パフォーマンス*")
    lines.append(
        f"今週: {weekly_trades}トレード / 累計: ¥{t_sign}{total_pnl:,.0f} ({p_sign}{total_pnl_pct:.2f}%)"
    )
    lines.append("")

    regime = (market_regime or {}).get("regime", "neutral")
    nikkei_price = (market_regime or {}).get("price", 0)
    cash_str = f" / 現金: ¥{cash:,.0f}"
    if nikkei_price:
        lines.append(f"*市場レジーム*: {regime}  (日経225 ¥{nikkei_price:,.0f}){cash_str}")
    else:
        lines.append(f"*市場レジーム*: {regime}{cash_str}")

    return "\n".join(lines)


def format_weekly_mrkdwn(weekly: dict, balance: float) -> str:
    """週次レポートのSlack mrkdwn版。"""
    total_pnl_pct = (weekly["total_pnl"] / balance) * 100 if balance else 0
    weekly_pnl_pct = (weekly["weekly_pnl"] / balance) * 100 if balance else 0

    sign_w = "+" if weekly["weekly_pnl"] >= 0 else ""
    sign_t = "+" if weekly["total_pnl"] >= 0 else ""

    return (
        f"*週次レポート*\n"
        f"トレード: {weekly['weekly_trades']}回（勝{weekly['weekly_wins']} 負{weekly['weekly_losses']}）\n"
        f"週間損益: {sign_w}¥{weekly['weekly_pnl']:,.0f}（{sign_w}{weekly_pnl_pct:.2f}%）\n"
        f"累計損益: {sign_t}¥{weekly['total_pnl']:,.0f}（{sign_t}{total_pnl_pct:.2f}%）\n"
        f"最大DD: ¥{weekly['max_drawdown']:,.0f}"
    )
