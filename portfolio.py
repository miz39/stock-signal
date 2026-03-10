import json
import os
from datetime import datetime, date
from typing import Optional

TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.json")


def _load_trades() -> list:
    if not os.path.exists(TRADES_FILE):
        return []
    with open(TRADES_FILE, "r") as f:
        return json.load(f)


def _save_trades(trades: list) -> None:
    with open(TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False, default=str)


def record_entry(ticker: str, price: float, shares: int, entry_date: str = None) -> dict:
    """仮想エントリーを記録する。"""
    trades = _load_trades()
    stop_price = round(price * 0.95, 1)  # 初期ストップ: -5%
    trade = {
        "ticker": ticker,
        "entry_price": price,
        "shares": shares,
        "entry_date": entry_date or date.today().isoformat(),
        "exit_price": None,
        "exit_date": None,
        "status": "open",
        "high_price": price,
        "stop_price": stop_price,
    }
    trades.append(trade)
    _save_trades(trades)
    return trade


def update_trailing_stop(ticker: str, current_price: float, trail_pct: float = 0.05) -> Optional[dict]:
    """トレーリングストップを更新する。高値更新時にストップも引き上げ。"""
    trades = _load_trades()
    for trade in trades:
        if trade["ticker"] == ticker and trade["status"] == "open":
            high = trade.get("high_price", trade["entry_price"])
            if current_price > high:
                trade["high_price"] = current_price
                trade["stop_price"] = round(current_price * (1 - trail_pct), 1)
                _save_trades(trades)
            return trade
    return None


def record_partial_exit(ticker: str, exit_shares: int, price: float, exit_date: str = None) -> Optional[dict]:
    """保有株の一部を利確売りする。元tradeのsharesを減らし、売却分をclosedトレードとして追加する。"""
    trades = _load_trades()
    for trade in trades:
        if trade["ticker"] == ticker and trade["status"] == "open" and not trade.get("partial_exit_done"):
            if exit_shares >= trade["shares"]:
                # 全株売却の場合は通常のexitとして処理
                return record_exit(ticker, price, exit_date)

            # 売却分をclosedトレードとして記録
            closed_trade = {
                "ticker": ticker,
                "entry_price": trade["entry_price"],
                "shares": exit_shares,
                "entry_date": trade["entry_date"],
                "exit_price": price,
                "exit_date": exit_date or date.today().isoformat(),
                "status": "closed",
                "pnl": round((price - trade["entry_price"]) * exit_shares, 1),
                "high_price": trade.get("high_price", trade["entry_price"]),
                "stop_price": trade["stop_price"],
                "reason": "利確（+7%）",
            }
            trades.append(closed_trade)

            # 元tradeの株数を減らし、partial_exit_doneフラグを設定
            trade["shares"] -= exit_shares
            trade["partial_exit_done"] = True

            _save_trades(trades)
            return closed_trade
    return None


def record_exit(ticker: str, price: float, exit_date: str = None) -> Optional[dict]:
    """仮想イグジットを記録する。最も古いオープンポジションをクローズする。"""
    trades = _load_trades()
    for trade in trades:
        if trade["ticker"] == ticker and trade["status"] == "open":
            trade["exit_price"] = price
            trade["exit_date"] = exit_date or date.today().isoformat()
            trade["status"] = "closed"
            trade["pnl"] = round((price - trade["entry_price"]) * trade["shares"], 1)
            _save_trades(trades)
            return trade
    return None


def get_open_positions() -> list:
    """保有中のポジション一覧を返す。"""
    trades = _load_trades()
    return [t for t in trades if t["status"] == "open"]


def get_performance_summary() -> dict:
    """累計損益、勝率、最大ドローダウンを返す。"""
    trades = _load_trades()
    closed = [t for t in trades if t["status"] == "closed"]

    if not closed:
        return {"total_pnl": 0, "win_rate": 0, "max_drawdown": 0, "trade_count": 0}

    pnls = [t["pnl"] for t in closed]
    wins = sum(1 for p in pnls if p > 0)

    # 最大ドローダウン計算（累積損益ベース）
    cumulative = []
    running = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        running += p
        cumulative.append(running)
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    return {
        "total_pnl": round(sum(pnls), 1),
        "win_rate": round(wins / len(closed) * 100, 1),
        "max_drawdown": round(max_dd, 1),
        "trade_count": len(closed),
    }


def get_weekly_report() -> dict:
    """今週のトレード数、損益、累計を返す。"""
    trades = _load_trades()
    today = date.today()
    # 今週月曜日
    monday = today.toordinal() - today.weekday()

    weekly_closed = []
    for t in trades:
        if t["status"] == "closed" and t.get("exit_date"):
            exit_d = date.fromisoformat(t["exit_date"])
            if exit_d.toordinal() >= monday:
                weekly_closed.append(t)

    weekly_pnl = sum(t["pnl"] for t in weekly_closed)
    weekly_wins = sum(1 for t in weekly_closed if t["pnl"] > 0)
    weekly_losses = sum(1 for t in weekly_closed if t["pnl"] <= 0)

    summary = get_performance_summary()

    return {
        "weekly_trades": len(weekly_closed),
        "weekly_wins": weekly_wins,
        "weekly_losses": weekly_losses,
        "weekly_pnl": round(weekly_pnl, 1),
        "total_pnl": summary["total_pnl"],
        "max_drawdown": summary["max_drawdown"],
    }
