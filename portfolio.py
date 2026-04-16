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


def record_entry(
    ticker: str,
    price: float,
    shares: int,
    entry_date: str = None,
    stop_pct: float = 0.08,
    signal_meta: Optional[dict] = None,
) -> dict:
    """仮想エントリーを記録する。

    signal_meta: エントリー時点の指標値（後日分析用）。例:
        {"rsi": 58.3, "adx": 28.1, "sma_slope": 1.2,
         "ichimoku_bullish": True, "market_regime": "bull"}
    """
    trades = _load_trades()
    stop_price = round(price * (1 - stop_pct), 1)
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
    if signal_meta:
        trade["entry_meta"] = signal_meta
    trades.append(trade)
    _save_trades(trades)
    return trade


def record_topup(ticker: str, price: float, additional_shares: int, stop_pct: float = 0.08) -> Optional[dict]:
    """既存ポジションに買い増しする。平均取得単価とストップを再計算する。"""
    trades = _load_trades()
    for trade in trades:
        if trade["ticker"] == ticker and trade["status"] == "open":
            old_shares = trade["shares"]
            old_price = trade["entry_price"]
            new_total = old_shares + additional_shares
            new_avg = round((old_price * old_shares + price * additional_shares) / new_total, 1)
            trade["entry_price"] = new_avg
            trade["shares"] = new_total
            if not trade.get("partial_exit_done"):
                trade["original_shares"] = new_total
            new_stop = round(new_avg * (1 - stop_pct), 1)
            if new_stop > trade.get("stop_price", 0):
                trade["stop_price"] = new_stop
            _save_trades(trades)
            return trade
    return None


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


def set_stop_price(ticker: str, new_stop: float) -> Optional[dict]:
    """ストップ価格を指定値に設定する（現在値より高い場合のみ引き上げ）。"""
    trades = _load_trades()
    for trade in trades:
        if trade["ticker"] == ticker and trade["status"] == "open":
            if new_stop > trade.get("stop_price", 0):
                trade["stop_price"] = round(new_stop, 1)
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


def get_consecutive_loss_tickers(max_losses: int = 2) -> set:
    """N回以上連続で損失クローズした銘柄のセットを返す。"""
    trades = _load_trades()
    from collections import defaultdict
    ticker_trades = defaultdict(list)
    for t in trades:
        if t["status"] == "closed" and "pnl" in t and t.get("exit_date"):
            ticker_trades[t["ticker"]].append(t)

    blocked = set()
    for ticker, ttrades in ticker_trades.items():
        ttrades.sort(key=lambda t: t["exit_date"])
        consecutive = 0
        for t in reversed(ttrades):
            if t["pnl"] < 0:
                consecutive += 1
            else:
                break
        if consecutive >= max_losses:
            blocked.add(ticker)
    return blocked


def get_monthly_performance() -> list:
    """月次パフォーマンス（トレード数・勝率・損益）を返す。古→新の順。"""
    trades = _load_trades()
    from collections import OrderedDict
    monthly = OrderedDict()
    for t in trades:
        if t.get("status") != "closed" or not t.get("exit_date"):
            continue
        month = t["exit_date"][:7]  # YYYY-MM
        if month not in monthly:
            monthly[month] = {"trades": 0, "wins": 0, "pnl": 0.0}
        monthly[month]["trades"] += 1
        if t.get("pnl", 0) > 0:
            monthly[month]["wins"] += 1
        monthly[month]["pnl"] += t.get("pnl", 0)

    result = []
    for month in sorted(monthly.keys()):
        m = monthly[month]
        win_rate = round(m["wins"] / m["trades"] * 100, 1) if m["trades"] > 0 else 0.0
        result.append({
            "month": month,
            "trades": m["trades"],
            "wins": m["wins"],
            "losses": m["trades"] - m["wins"],
            "win_rate": win_rate,
            "pnl": round(m["pnl"], 1),
        })
    return result


def get_readiness_metrics(initial_balance: float = 300000) -> dict:
    """ペーパー→リアル移行判定用メトリクスを返す。

    基準（全5項目）:
    - trade_count ≥ 100
    - profit_factor ≥ 1.5
    - max_dd_pct ≤ 10
    - consecutive_profitable_months ≥ 3
    - win_rate ≥ 45

    Returns:
        {
            "criteria": [{"name", "label", "actual", "threshold",
                          "passed", "display"}, ...],
            "passed_count": int,
            "total_count": int,
            "score_pct": float,
            "ready": bool,
        }
    """
    trades = _load_trades()
    closed = [t for t in trades if t.get("status") == "closed" and "pnl" in t]
    trade_count = len(closed)

    gross_profit = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in closed if t["pnl"] < 0))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
        pf_display = f"{profit_factor:.2f} / 1.5"
    elif gross_profit > 0:
        profit_factor = float("inf")
        pf_display = "∞ / 1.5"
    else:
        profit_factor = 0.0
        pf_display = "0.00 / 1.5"

    wins = sum(1 for t in closed if t["pnl"] > 0)
    win_rate = (wins / trade_count * 100) if trade_count > 0 else 0.0

    # Max drawdown as percentage of running equity peak
    sorted_closed = sorted(closed, key=lambda t: t.get("exit_date", ""))
    running = float(initial_balance)
    peak = float(initial_balance)
    max_dd_pct = 0.0
    for t in sorted_closed:
        running += t["pnl"]
        if running > peak:
            peak = running
        if peak > 0:
            dd_pct = (peak - running) / peak * 100
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

    # Trailing consecutive profitable months
    monthly = get_monthly_performance()
    consecutive_months = 0
    for m in reversed(monthly):
        if m["pnl"] > 0:
            consecutive_months += 1
        else:
            break

    criteria = [
        {
            "name": "trade_count",
            "label": "サンプル数",
            "actual": trade_count,
            "threshold": 100,
            "passed": trade_count >= 100,
            "display": f"{trade_count}/100",
        },
        {
            "name": "profit_factor",
            "label": "PF",
            "actual": None if profit_factor == float("inf") else round(profit_factor, 2),
            "threshold": 1.5,
            "passed": profit_factor >= 1.5,
            "display": pf_display,
        },
        {
            "name": "max_dd_pct",
            "label": "最大DD",
            "actual": round(max_dd_pct, 1),
            "threshold": 10,
            "passed": max_dd_pct <= 10,
            "display": f"{max_dd_pct:.1f}% / 10%",
        },
        {
            "name": "consecutive_profitable_months",
            "label": "連続黒字月",
            "actual": consecutive_months,
            "threshold": 3,
            "passed": consecutive_months >= 3,
            "display": f"{consecutive_months}/3ヶ月",
        },
        {
            "name": "win_rate",
            "label": "勝率",
            "actual": round(win_rate, 1),
            "threshold": 45,
            "passed": win_rate >= 45,
            "display": f"{win_rate:.1f}% / 45%",
        },
    ]

    passed_count = sum(1 for c in criteria if c["passed"])
    total = len(criteria)
    score_pct = round(passed_count / total * 100, 1) if total else 0.0

    return {
        "criteria": criteria,
        "passed_count": passed_count,
        "total_count": total,
        "score_pct": score_pct,
        "ready": passed_count == total,
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
