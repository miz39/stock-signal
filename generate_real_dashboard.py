#!/usr/bin/env python3
"""
real_trades.json を読んで docs/real.html を生成する。
このファイルは .gitignore で除外されており、ローカル専用。
"""

import json
import os
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_TRADES_FILE = os.path.join(BASE_DIR, "real_trades.json")
PAPER_TRADES_FILE = os.path.join(BASE_DIR, "trades.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "docs", "real.html")

from nikkei225 import NIKKEI_225


def load_trades(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def fetch_current_prices(tickers: list) -> dict:
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
        print(f"価格取得エラー: {e}")
        return {}


def calc_paper_comparison(paper_trades: list, real_start_date: str) -> dict:
    """リアル開始日以降のペーパー成績を集計して比較データを返す。"""
    if not real_start_date:
        return {}
    closed = [
        t for t in paper_trades
        if t.get("status") == "closed"
        and t.get("entry_date", "") >= real_start_date
    ]
    if not closed:
        return {"count": 0, "pnl": 0, "win_rate": 0, "pf": 0}
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    gross_profit = sum(t["pnl"] for t in closed if t.get("pnl", 0) > 0)
    gross_loss = abs(sum(t["pnl"] for t in closed if t.get("pnl", 0) < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    return {
        "count": len(closed),
        "pnl": round(sum(t.get("pnl", 0) for t in closed), 0),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "pf": pf,
    }


def generate_html(real_trades: list, paper_trades: list) -> str:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    open_trades = [t for t in real_trades if t.get("status") == "open"]
    closed_trades = sorted(
        [t for t in real_trades if t.get("status") == "closed"],
        key=lambda t: t.get("exit_date", ""),
        reverse=True,
    )

    # 現在価格取得
    tickers = [t["ticker"] for t in open_trades]
    prices = fetch_current_prices(tickers)

    # サマリー集計
    closed_pnl = sum(t.get("pnl", 0) for t in closed_trades)
    unrealized = sum(
        (prices.get(t["ticker"], t["entry_price"]) - t["entry_price"]) * t["shares"]
        for t in open_trades
    )
    wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
    win_rate = round(len(wins) / len(closed_trades) * 100, 1) if closed_trades else 0
    gross_profit = sum(t["pnl"] for t in closed_trades if t.get("pnl", 0) > 0)
    gross_loss = abs(sum(t["pnl"] for t in closed_trades if t.get("pnl", 0) < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # ペーパー比較（リアル開始日以降）
    real_start = min((t.get("entry_date", "") for t in real_trades), default="")
    paper_comp = calc_paper_comparison(paper_trades, real_start)

    # ---- オープンポジション行 ----
    open_rows = ""
    for t in open_trades:
        ticker = t["ticker"]
        name = NIKKEI_225.get(ticker, {}).get("name", ticker) if isinstance(NIKKEI_225.get(ticker), dict) else NIKKEI_225.get(ticker, ticker)
        current = prices.get(ticker, t["entry_price"])
        unrealized_t = round((current - t["entry_price"]) * t["shares"], 0)
        pnl_pct = round((current / t["entry_price"] - 1) * 100, 1)
        pnl_color = "#00C853" if unrealized_t >= 0 else "#FF1744"
        sign = "+" if unrealized_t >= 0 else ""
        days = (datetime.now(JST).date() - datetime.fromisoformat(t["entry_date"]).date()).days
        stop = t.get("stop_price", "—")
        stop_str = f"¥{stop:,.0f}" if isinstance(stop, (int, float)) else "—"
        target = t.get("target_price", "—")
        target_str = f"¥{target:,.0f}" if isinstance(target, (int, float)) else "—"
        open_rows += f"""
        <tr>
          <td>{t['entry_date']}</td>
          <td><span class="ticker">{ticker}</span> {name}</td>
          <td class="num">¥{t['entry_price']:,.0f}</td>
          <td class="num">¥{current:,.0f}</td>
          <td class="num" style="color:{pnl_color}">{sign}{pnl_pct}%</td>
          <td class="num" style="color:{pnl_color}">{sign}¥{abs(unrealized_t):,.0f}</td>
          <td class="num">{t['shares']}株</td>
          <td class="num" style="color:#FF8A80">{stop_str}</td>
          <td class="num" style="color:#69F0AE">{target_str}</td>
          <td class="num">{days}日</td>
        </tr>"""

    if not open_rows:
        open_rows = '<tr><td colspan="10" class="empty">保有なし</td></tr>'

    # ---- クローズ済み行 ----
    closed_rows = ""
    for t in closed_trades:
        ticker = t["ticker"]
        name = NIKKEI_225.get(ticker, {}).get("name", ticker) if isinstance(NIKKEI_225.get(ticker), dict) else NIKKEI_225.get(ticker, ticker)
        pnl = t.get("pnl", 0)
        pnl_color = "#00C853" if pnl >= 0 else "#FF1744"
        sign = "+" if pnl >= 0 else ""
        days = ""
        if t.get("entry_date") and t.get("exit_date"):
            d = (datetime.fromisoformat(t["exit_date"]).date() - datetime.fromisoformat(t["entry_date"]).date()).days
            days = f"{d}日"
        reason = t.get("exit_reason", "—")
        closed_rows += f"""
        <tr>
          <td>{t.get('entry_date','—')}</td>
          <td>{t.get('exit_date','—')}</td>
          <td><span class="ticker">{ticker}</span> {name}</td>
          <td class="num">¥{t['entry_price']:,.0f}</td>
          <td class="num">¥{t.get('exit_price', 0):,.0f}</td>
          <td class="num" style="color:{pnl_color}">{sign}¥{abs(pnl):,.0f}</td>
          <td class="num">{days}</td>
          <td class="small">{reason}</td>
        </tr>"""

    if not closed_rows:
        closed_rows = '<tr><td colspan="8" class="empty">クローズ済みトレードなし</td></tr>'

    # ---- ペーパー比較行 ----
    if paper_comp and paper_comp.get("count", 0) > 0:
        real_pnl_sign = "+" if closed_pnl >= 0 else ""
        paper_pnl_sign = "+" if paper_comp["pnl"] >= 0 else ""
        comp_section = f"""
    <div class="section">
      <h2>ペーパー vs リアル（{real_start} 以降）</h2>
      <table>
        <thead><tr>
          <th></th><th class="num">トレード数</th><th class="num">勝率</th><th class="num">PF</th><th class="num">累積PnL</th>
        </tr></thead>
        <tbody>
          <tr>
            <td>📄 ペーパー</td>
            <td class="num">{paper_comp['count']}件</td>
            <td class="num">{paper_comp['win_rate']}%</td>
            <td class="num">{paper_comp['pf']}</td>
            <td class="num">{paper_pnl_sign}¥{abs(paper_comp['pnl']):,.0f}</td>
          </tr>
          <tr>
            <td>💴 リアル</td>
            <td class="num">{len(closed_trades)}件</td>
            <td class="num">{win_rate}%</td>
            <td class="num">{pf if closed_trades else '—'}</td>
            <td class="num">{real_pnl_sign}¥{abs(closed_pnl):,.0f}</td>
          </tr>
        </tbody>
      </table>
    </div>"""
    else:
        comp_section = ""

    total_pnl = closed_pnl + unrealized
    total_sign = "+" if total_pnl >= 0 else ""
    closed_sign = "+" if closed_pnl >= 0 else ""
    unr_sign = "+" if unrealized >= 0 else ""
    pf_display = str(pf) if closed_trades else "—"
    wr_display = f"{win_rate}%" if closed_trades else "—"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Real Trade Dashboard</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#121212; color:#E0E0E0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:16px; max-width:960px; margin:0 auto; }}
h1 {{ font-size:1.3rem; margin-bottom:4px; }}
.updated {{ font-size:0.75rem; color:#757575; margin-bottom:16px; }}
.badge {{ display:inline-block; background:#FF6D00; color:#fff; font-size:0.65rem; font-weight:700; padding:2px 7px; border-radius:10px; margin-left:8px; vertical-align:middle; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:20px; }}
.card {{ background:#1E1E1E; border-radius:10px; padding:14px; text-align:center; }}
.card .label {{ font-size:0.7rem; color:#9E9E9E; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }}
.card .value {{ font-size:1.4rem; font-weight:700; }}
.card .sub {{ font-size:0.7rem; color:#757575; margin-top:2px; }}
.section {{ margin-bottom:24px; }}
.section h2 {{ font-size:1rem; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid #333; }}
table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ background:#1E1E1E; color:#9E9E9E; text-align:left; padding:8px 6px; font-weight:500; font-size:0.7rem; text-transform:uppercase; position:sticky; top:0; }}
td {{ padding:8px 6px; border-bottom:1px solid #2A2A2A; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.ticker {{ color:#757575; font-size:0.7rem; }}
.small {{ font-size:0.7rem; color:#9E9E9E; }}
.empty {{ text-align:center; color:#757575; padding:24px; }}
.green {{ color:#00C853; }}
.red {{ color:#FF1744; }}
a.back {{ color:#64B5F6; text-decoration:none; font-size:0.8rem; display:inline-block; margin-bottom:12px; }}
@media(max-width:600px) {{
  .cards {{ grid-template-columns:repeat(2,1fr); }}
  table {{ font-size:0.72rem; }}
}}
</style>
</head>
<body>
<a class="back" href="index.html">← ペーパーダッシュボード</a>
<h1>Real Trade Dashboard <span class="badge">REAL</span></h1>
<p class="updated">更新: {now}</p>

<div class="cards">
  <div class="card">
    <div class="label">累積損益（確定）</div>
    <div class="value {'green' if closed_pnl >= 0 else 'red'}">{closed_sign}¥{abs(closed_pnl):,.0f}</div>
    <div class="sub">{len(closed_trades)}件クローズ</div>
  </div>
  <div class="card">
    <div class="label">含み損益</div>
    <div class="value {'green' if unrealized >= 0 else 'red'}">{unr_sign}¥{abs(unrealized):,.0f}</div>
    <div class="sub">{len(open_trades)}件保有</div>
  </div>
  <div class="card">
    <div class="label">合計損益</div>
    <div class="value {'green' if total_pnl >= 0 else 'red'}">{total_sign}¥{abs(total_pnl):,.0f}</div>
    <div class="sub">確定 + 含み</div>
  </div>
  <div class="card">
    <div class="label">勝率</div>
    <div class="value">{wr_display}</div>
    <div class="sub">{len(wins)}/{len(closed_trades)}件</div>
  </div>
  <div class="card">
    <div class="label">PF</div>
    <div class="value">{pf_display}</div>
    <div class="sub">総利益÷総損失</div>
  </div>
</div>

<div class="section">
  <h2>保有ポジション</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>エントリー日</th><th>銘柄</th><th class="num">取得価格</th><th class="num">現在値</th>
      <th class="num">損益%</th><th class="num">含み損益</th><th class="num">株数</th>
      <th class="num">損切ライン</th><th class="num">目標</th><th class="num">保有</th>
    </tr></thead>
    <tbody>{open_rows}</tbody>
  </table>
  </div>
</div>

<div class="section">
  <h2>クローズ済みトレード</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>エントリー</th><th>クローズ</th><th>銘柄</th>
      <th class="num">取得価格</th><th class="num">決済価格</th>
      <th class="num">確定損益</th><th class="num">保有期間</th><th>理由</th>
    </tr></thead>
    <tbody>{closed_rows}</tbody>
  </table>
  </div>
</div>

{comp_section}

</body>
</html>"""


def main():
    real_trades = load_trades(REAL_TRADES_FILE)
    paper_trades = load_trades(PAPER_TRADES_FILE)

    html = generate_html(real_trades, paper_trades)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html)

    open_count = sum(1 for t in real_trades if t.get("status") == "open")
    closed_count = sum(1 for t in real_trades if t.get("status") == "closed")
    print(f"Real dashboard generated: {OUTPUT_FILE}")
    print(f"  Open: {open_count}件 / Closed: {closed_count}件")


if __name__ == "__main__":
    main()
