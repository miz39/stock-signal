import fcntl
import json
import os
import tempfile
from datetime import datetime, date
from typing import Optional

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE = os.path.join(_BASE_DIR, "trades.json")


def set_profile(name: str) -> None:
    """Switch trades file based on profile name.

    "default" -> trades.json (backward compatible)
    others   -> trades_{name}.json
    """
    global TRADES_FILE
    if name == "default":
        TRADES_FILE = os.path.join(_BASE_DIR, "trades.json")
    else:
        TRADES_FILE = os.path.join(_BASE_DIR, f"trades_{name}.json")


def _load_trades() -> list:
    if not os.path.exists(TRADES_FILE):
        return []
    with open(TRADES_FILE, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _save_trades(trades: list) -> None:
    dir_name = os.path.dirname(TRADES_FILE)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(trades, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, TRADES_FILE)
    except:
        os.unlink(tmp_path)
        raise


def record_entry(ticker: str, price: float, shares: int, entry_date: str = None) -> dict:
    """仮想エントリーを記録する。"""
    trades = _load_trades()
    stop_price = round(price * 0.92, 1)  # 初期ストップ: -8%
    trade = {
        "ticker": ticker,
        "entry_price": price,
        "shares": shares,
        "original_shares": shares,
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


def update_trailing_stop(ticker: str, current_price: float, trail_pct: float = 0.08) -> Optional[dict]:
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


def move_stop_to_breakeven(ticker: str) -> Optional[dict]:
    """ストップを建値（取得価格）に移動する。"""
    trades = _load_trades()
    for trade in trades:
        if trade["ticker"] == ticker and trade["status"] == "open":
            entry = trade["entry_price"]
            if trade["stop_price"] < entry:
                trade["stop_price"] = round(entry, 1)
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
                "reason": "利確（+8%）",
            }
            trades.append(closed_trade)

            # original_sharesを保存（初回のpartial exitのみ）
            if "original_shares" not in trade:
                trade["original_shares"] = trade["shares"]

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


def get_cash_balance(initial_balance: float = 300000) -> float:
    """現金残高を計算する。初期資金 + 確定損益 - 保有株エントリーコスト。

    PnLベースで計算することで、partial exitの二重計上を回避する。
    """
    trades = _load_trades()
    cash = initial_balance
    for t in trades:
        if t["status"] == "closed" and "pnl" in t:
            cash += t["pnl"]
        elif t["status"] == "open":
            cash -= t["entry_price"] * t["shares"]
    return round(cash, 1)


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


def get_recently_stopped_tickers(cooldown_days: int = 7) -> set:
    """直近N日以内に損切り（損失クローズ）された銘柄のセットを返す。"""
    trades = _load_trades()
    today = date.today()
    cutoff = date.fromordinal(today.toordinal() - cooldown_days)
    stopped = set()
    for t in trades:
        if (
            t["status"] == "closed"
            and t.get("pnl", 0) < 0
            and t.get("exit_date")
            and date.fromisoformat(t["exit_date"]) >= cutoff
        ):
            stopped.add(t["ticker"])
    return stopped


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
