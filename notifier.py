import requests
import traceback


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

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Discord送信エラー: {e}")
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
