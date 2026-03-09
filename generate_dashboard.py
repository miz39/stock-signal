#!/usr/bin/env python3
"""
trades.json を読んで docs/index.html を生成するダッシュボードジェネレーター。
GitHub Pages で公開し、スマホ/PCからペーパートレードの状況を確認できる。
"""

import json
import os
import yaml
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE = os.path.join(BASE_DIR, "trades.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.yaml")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
OUTPUT_FILE = os.path.join(DOCS_DIR, "index.html")

# 銘柄名マッピング
from nikkei225 import NIKKEI_225


def load_config() -> dict:
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def load_trades() -> list:
    if not os.path.exists(TRADES_FILE):
        return []
    with open(TRADES_FILE, "r") as f:
        return json.load(f)


def fetch_current_prices(tickers: list) -> dict:
    """yfinance でオープンポジションの現在価格を取得する。"""
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
                if hasattr(val, 'iloc'):
                    val = val.iloc[0]
                prices[tickers[0]] = round(float(val), 1)
        else:
            for t in tickers:
                try:
                    val = data["Close"][t].iloc[-1]
                    if val == val:  # NaN check
                        prices[t] = round(float(val), 1)
                except Exception:
                    pass
        return prices
    except Exception as e:
        print(f"価格取得エラー: {e}")
        return {}


def build_dashboard_data(trades: list, initial_balance: float = 300000) -> dict:
    """trades.json からダッシュボード用データを構築する。"""
    open_trades = [t for t in trades if t.get("status") == "open"]
    closed_trades = [t for t in trades if t.get("status") == "closed"]

    # オープンポジションの現在価格を取得
    open_tickers = [t["ticker"] for t in open_trades]
    current_prices = fetch_current_prices(open_tickers)

    # サマリー指標
    pnls = [t["pnl"] for t in closed_trades if "pnl" in t]
    total_pnl = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = round(wins / len(pnls) * 100, 1) if pnls else 0
    trade_count = len(closed_trades)

    # 最大ドローダウン
    max_dd = 0
    running = 0
    peak = 0
    for p in pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    # 含み損益
    unrealized_pnl = 0
    open_positions = []
    for t in open_trades:
        cp = current_prices.get(t["ticker"], t["entry_price"])
        pnl = round((cp - t["entry_price"]) * t["shares"], 1)
        pnl_pct = round((cp / t["entry_price"] - 1) * 100, 2)
        name = NIKKEI_225.get(t["ticker"], t["ticker"])
        entry_date = t.get("entry_date", "")
        days = 0
        if entry_date:
            try:
                from datetime import date
                ed = date.fromisoformat(entry_date)
                days = (date.today() - ed).days
            except Exception:
                pass
        open_positions.append({
            "ticker": t["ticker"],
            "name": name,
            "entry_price": t["entry_price"],
            "current_price": cp,
            "shares": t["shares"],
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "entry_date": entry_date,
            "days": days,
        })
        unrealized_pnl += pnl

    # 資産推移データ（運用開始日から今日まで日次の総資産）
    sorted_closed = sorted(
        [t for t in closed_trades if t.get("exit_date") and "pnl" in t],
        key=lambda t: t["exit_date"],
    )

    # 運用開始日を特定
    all_entry_dates = [t.get("entry_date") for t in trades if t.get("entry_date")]
    if all_entry_dates:
        from datetime import date
        start_date = date.fromisoformat(min(all_entry_dates))
        today = date.today()

        # 日ごとの確定損益を集計
        daily_realized = {}
        for t in sorted_closed:
            d = t["exit_date"]
            daily_realized[d] = daily_realized.get(d, 0) + t["pnl"]

        # 日ごとの含み損益を取得（保有銘柄の価格履歴から）
        # まずは確定損益ベースで日次推移を作成
        equity_labels = []
        equity_data = []
        cum_pnl = 0
        d = start_date
        while d <= today:
            ds = d.isoformat()
            cum_pnl += daily_realized.get(ds, 0)
            # 最終日は含み損益も加算
            if d == today:
                total = initial_balance + cum_pnl + unrealized_pnl
            else:
                total = initial_balance + cum_pnl
            equity_labels.append(ds)
            equity_data.append(round(total, 1))
            d += timedelta(days=1)
    else:
        equity_labels = []
        equity_data = []

    # 直近クローズドトレード（最新20件）
    recent_closed = []
    for t in reversed(sorted_closed[-20:]):
        name = NIKKEI_225.get(t["ticker"], t["ticker"])
        recent_closed.append({
            "ticker": t["ticker"],
            "name": name,
            "entry_price": t["entry_price"],
            "exit_price": t.get("exit_price", 0),
            "shares": t.get("shares", 0),
            "pnl": t.get("pnl", 0),
            "entry_date": t.get("entry_date", ""),
            "exit_date": t.get("exit_date", ""),
        })

    # 総資産 = 初期資金 + 確定損益 + 含み損益
    total_assets = initial_balance + total_pnl + unrealized_pnl
    total_return_pct = round((total_pnl + unrealized_pnl) / initial_balance * 100, 2) if initial_balance else 0

    return {
        "initial_balance": initial_balance,
        "total_assets": round(total_assets, 1),
        "total_return_pct": total_return_pct,
        "total_pnl": round(total_pnl, 1),
        "win_rate": win_rate,
        "trade_count": trade_count,
        "max_dd": round(max_dd, 1),
        "open_count": len(open_trades),
        "unrealized_pnl": round(unrealized_pnl, 1),
        "equity_labels": equity_labels,
        "equity_data": equity_data,
        "open_positions": open_positions,
        "recent_closed": recent_closed,
    }


def build_stock_detail(ticker: str, entry_price: float) -> dict:
    """銘柄の詳細データ（6ヶ月分の価格, SMA, RSI, ファンダメンタル）を取得する。"""
    import yfinance as yf
    import pandas as pd
    import numpy as np
    from strategy import calculate_sma, calculate_rsi

    result = {
        "ticker": ticker,
        "name": NIKKEI_225.get(ticker, ticker),
        "entry_price": entry_price,
    }

    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="6mo")
    except Exception as e:
        print(f"  {ticker}: データ取得エラー: {e}")
        return result

    if hist.empty or len(hist) < 2:
        return result

    close = hist["Close"]
    result["dates"] = [d.strftime("%Y-%m-%d") for d in hist.index]
    result["prices"] = [round(float(v), 1) for v in close.values]
    result["current_price"] = round(float(close.iloc[-1]), 1)
    result["change_pct"] = round((close.iloc[-1] / entry_price - 1) * 100, 2)

    # SMA
    sma25 = calculate_sma(close, 25)
    sma75 = calculate_sma(close, 75)
    sma200 = calculate_sma(close, 200)
    result["sma25"] = [round(float(v), 1) if not np.isnan(v) else None for v in sma25.values]
    result["sma75"] = [round(float(v), 1) if not np.isnan(v) else None for v in sma75.values]
    result["sma200"] = [round(float(v), 1) if not np.isnan(v) else None for v in sma200.values]

    latest_sma25 = float(sma25.iloc[-1]) if not np.isnan(sma25.iloc[-1]) else None
    latest_sma75 = float(sma75.iloc[-1]) if not np.isnan(sma75.iloc[-1]) else None
    result["sma_cross"] = None
    if latest_sma25 is not None and latest_sma75 is not None:
        result["sma_cross"] = "GC（上昇）" if latest_sma25 > latest_sma75 else "DC（下降）"

    # RSI
    rsi = calculate_rsi(close, 14)
    latest_rsi = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
    result["rsi"] = latest_rsi

    # 損切りライン
    result["stop_loss"] = round(entry_price * 0.95, 1)

    # ファンダメンタル
    try:
        info = stock.info
        per = info.get("trailingPE") or info.get("forwardPE")
        result["per"] = round(float(per), 1) if per and per < 500 else None
        pbr = info.get("priceToBook")
        result["pbr"] = round(float(pbr), 2) if pbr else None
        roe = info.get("returnOnEquity")
        result["roe"] = round(float(roe) * 100, 1) if roe else None
        div_yield = info.get("dividendYield")
        if div_yield:
            result["div_yield"] = round(float(div_yield * 100), 2) if div_yield < 0.2 else round(float(div_yield), 2)
        else:
            result["div_yield"] = None
        market_cap = info.get("marketCap")
        result["market_cap"] = int(market_cap) if market_cap else None
    except Exception:
        result["per"] = result["pbr"] = result["roe"] = result["div_yield"] = result["market_cap"] = None

    return result


def generate_stock_html(data: dict) -> str:
    """銘柄詳細ページのHTMLを生成する。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    ticker = data.get("ticker", "")
    name = data.get("name", ticker)
    code = ticker.replace(".T", "")
    entry_price = data.get("entry_price", 0)
    current_price = data.get("current_price", entry_price)
    change_pct = data.get("change_pct", 0)
    rsi = data.get("rsi")
    sma_cross = data.get("sma_cross", "-")
    stop_loss = data.get("stop_loss", 0)

    def pnl_color(val):
        if val > 0: return "#00C853"
        elif val < 0: return "#FF1744"
        return "#9E9E9E"

    # テクニカルカード
    rsi_text = f"{rsi:.1f}" if rsi else "-"
    rsi_label = ""
    rsi_color = "#9E9E9E"
    if rsi:
        if rsi > 70:
            rsi_label = "買われすぎ"
            rsi_color = "#FF1744"
        elif rsi < 30:
            rsi_label = "売られすぎ"
            rsi_color = "#00C853"
        else:
            rsi_label = "適正"
            rsi_color = "#64B5F6"

    cross_color = "#00C853" if sma_cross and "GC" in str(sma_cross) else "#FF1744"

    # ファンダメンタルカード
    per = data.get("per")
    pbr = data.get("pbr")
    roe = data.get("roe")
    div_yield = data.get("div_yield")
    market_cap = data.get("market_cap")

    def fmt_market_cap(v):
        if not v: return "-"
        if v >= 1e12: return f"&yen;{v/1e12:.1f}兆"
        return f"&yen;{v/1e8:.0f}億"

    def fund_card(label, value, sub=""):
        sub_html = f'<div class="card-sub">{sub}</div>' if sub else ""
        return f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div>{sub_html}</div>'

    fund_cards = ""
    if per: fund_cards += fund_card("PER", f"{per:.1f}倍", "割安" if per < 15 else ("割高" if per > 25 else "適正"))
    if pbr: fund_cards += fund_card("PBR", f"{pbr:.2f}倍", "割安" if pbr < 1.0 else ("割高" if pbr > 3.0 else "適正"))
    if roe: fund_cards += fund_card("ROE", f"{roe:.1f}%", "高収益" if roe > 15 else ("低収益" if roe < 5 else "平均的"))
    if div_yield: fund_cards += fund_card("配当利回り", f"{div_yield:.2f}%")
    if market_cap: fund_cards += fund_card("時価総額", fmt_market_cap(market_cap))

    # Chart.js データ
    dates_json = json.dumps(data.get("dates", []))
    prices_json = json.dumps(data.get("prices", []))
    sma25_json = json.dumps(data.get("sma25", []))
    sma75_json = json.dumps(data.get("sma75", []))
    sma200_json = json.dumps(data.get("sma200", []))

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}（{code}）- Stock Detail</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#121212; color:#E0E0E0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:16px; max-width:960px; margin:0 auto; }}
a.back {{ color:#64B5F6; text-decoration:none; font-size:0.85rem; display:inline-block; margin-bottom:12px; }}
a.back:hover {{ text-decoration:underline; }}
.header {{ margin-bottom:16px; }}
.header h1 {{ font-size:1.3rem; }}
.header .code {{ color:#757575; font-size:0.85rem; }}
.updated {{ font-size:0.75rem; color:#757575; margin-bottom:16px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:20px; }}
.card {{ background:#1E1E1E; border-radius:10px; padding:14px; text-align:center; }}
.card .label {{ font-size:0.7rem; color:#9E9E9E; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }}
.card .value {{ font-size:1.4rem; font-weight:700; }}
.card .sub, .card-sub {{ font-size:0.7rem; color:#757575; margin-top:2px; }}
.section {{ margin-bottom:20px; }}
.section h2 {{ font-size:1rem; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid #333; }}
.chart-wrap {{ background:#1E1E1E; border-radius:10px; padding:12px; margin-bottom:20px; }}
.green {{ color:#00C853; }}
.red {{ color:#FF1744; }}
@media(max-width:480px) {{
  .cards {{ grid-template-columns:repeat(2,1fr); }}
  .card .value {{ font-size:1.2rem; }}
}}
</style>
</head>
<body>
<a class="back" href="../index.html">&larr; ダッシュボードに戻る</a>
<div class="header">
  <h1>{name}</h1>
  <span class="code">{code}.T</span>
</div>
<div class="updated">最終更新: {now}</div>

<div class="cards">
  <div class="card">
    <div class="label">現在価格</div>
    <div class="value" style="color:{pnl_color(change_pct)}">&yen;{current_price:,.0f}</div>
    <div class="sub" style="color:{pnl_color(change_pct)}">{'+'if change_pct>=0 else ''}{change_pct:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">RSI(14)</div>
    <div class="value" style="color:{rsi_color}">{rsi_text}</div>
    <div class="sub">{rsi_label}</div>
  </div>
  <div class="card">
    <div class="label">SMAクロス</div>
    <div class="value" style="color:{cross_color}">{sma_cross or '-'}</div>
  </div>
  <div class="card">
    <div class="label">損切りライン</div>
    <div class="value" style="color:#FF1744">&yen;{stop_loss:,.0f}</div>
    <div class="sub">取得価格の -5%</div>
  </div>
</div>

<div class="section">
  <h2>株価チャート（6ヶ月）</h2>
  <div class="chart-wrap">
    <canvas id="priceChart" height="300"></canvas>
  </div>
</div>

<div class="section">
  <h2>ファンダメンタル</h2>
  <div class="cards">
    {fund_cards if fund_cards else '<div class="card"><div class="label">データ</div><div class="value">-</div><div class="sub">取得できませんでした</div></div>'}
  </div>
</div>

<script>
const dates = {dates_json};
const prices = {prices_json};
const sma25 = {sma25_json};
const sma75 = {sma75_json};
const sma200 = {sma200_json};
const entryPrice = {entry_price};

if (dates.length > 0) {{
  const ctx = document.getElementById('priceChart').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: dates,
      datasets: [
        {{
          label: '終値',
          data: prices,
          borderColor: '#2196F3',
          backgroundColor: 'rgba(33,150,243,0.05)',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 2,
          order: 1,
        }},
        {{
          label: 'SMA25',
          data: sma25,
          borderColor: '#FF9800',
          borderWidth: 1,
          borderDash: [4,2],
          pointRadius: 0,
          fill: false,
          order: 2,
        }},
        {{
          label: 'SMA75',
          data: sma75,
          borderColor: '#9C27B0',
          borderWidth: 1,
          borderDash: [4,2],
          pointRadius: 0,
          fill: false,
          order: 3,
        }},
        {{
          label: 'SMA200',
          data: sma200,
          borderColor: '#607D8B',
          borderWidth: 1,
          borderDash: [6,3],
          pointRadius: 0,
          fill: false,
          order: 4,
        }},
        {{
          label: '取得単価',
          data: dates.map(() => entryPrice),
          borderColor: '#F44336',
          borderWidth: 1,
          borderDash: [8,4],
          pointRadius: 0,
          fill: false,
          order: 5,
        }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color:'#9E9E9E', font:{{ size:11 }}, usePointStyle:true, pointStyle:'line' }} }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              if (ctx.parsed.y == null) return null;
              return ctx.dataset.label + ': ¥' + ctx.parsed.y.toLocaleString();
            }}
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color:'#757575', maxTicksLimit:8, font:{{ size:10 }} }},
          grid: {{ color:'#2A2A2A' }}
        }},
        y: {{
          ticks: {{
            color:'#757575',
            font:{{ size:10 }},
            callback: function(v) {{ return '¥' + v.toLocaleString(); }}
          }},
          grid: {{ color:'#2A2A2A' }}
        }}
      }}
    }}
  }});
}} else {{
  document.getElementById('priceChart').parentElement.innerHTML =
    '<p style="text-align:center;color:#757575;padding:24px">チャートデータがありません</p>';
}}
</script>
</body>
</html>"""
    return html


def generate_policy_section(config: dict = None) -> str:
    """運用方針セクションHTMLを生成する。"""
    if not config:
        return ""
    strat = config.get("strategy", {})
    acct = config.get("account", {})
    mode = config.get("mode", "paper")
    watchlist = config.get("watchlist", "")

    sma_s = strat.get("sma_short", 25)
    sma_l = strat.get("sma_long", 75)
    sma_t = strat.get("sma_trend", 200)
    rsi_ob = strat.get("rsi_overbought", 70)
    rsi_period = strat.get("rsi_period", 14)
    risk_pct = int(acct.get("risk_per_trade", 0.02) * 100)
    max_alloc = int(acct.get("max_allocation", 0.10) * 100)
    balance = acct.get("balance", 300000)
    max_pos = acct.get("max_positions", 10)
    mode_label = "ペーパー" if mode == "paper" else "リアル"
    wl_label = "日経225全銘柄" if watchlist == "nikkei225" else watchlist

    return f"""<div class="section">
  <h2>運用方針</h2>
  <div class="policy-grid">
    <div class="policy-card">
      <div class="policy-title">戦略</div>
      <div class="policy-body">
        <div class="policy-name">ゴールデンクロス + RSI コンファーメーション</div>
        <div class="policy-rule buy-rule">買い: SMA{sma_s} &gt; SMA{sma_l} &amp; RSI &lt; {rsi_ob} &amp; 株価 &gt; SMA{sma_t}</div>
        <div class="policy-rule sell-rule">売り: SMA{sma_s} &lt; SMA{sma_l} or RSI &gt; 75</div>
      </div>
    </div>
    <div class="policy-card">
      <div class="policy-title">リスク管理</div>
      <div class="policy-body">
        <div class="policy-item">損切り <span class="policy-val">-5%</span></div>
        <div class="policy-item">1トレードリスク <span class="policy-val">{risk_pct}%</span></div>
        <div class="policy-item">1銘柄上限 <span class="policy-val">{max_alloc}%</span></div>
      </div>
    </div>
    <div class="policy-card">
      <div class="policy-title">口座</div>
      <div class="policy-body">
        <div class="policy-item">初期資金 <span class="policy-val">&yen;{balance:,.0f}</span></div>
        <div class="policy-item">最大銘柄数 <span class="policy-val">{max_pos}銘柄</span></div>
        <div class="policy-item">モード <span class="policy-val">{mode_label}</span></div>
      </div>
    </div>
    <div class="policy-card">
      <div class="policy-title">スキャン</div>
      <div class="policy-body">
        <div class="policy-item">対象 <span class="policy-val">{wl_label}</span></div>
        <div class="policy-item">実行 <span class="policy-val">1日3回</span></div>
        <div class="policy-item">時刻 <span class="policy-val">8:50 / 12:35 / 15:10</span></div>
      </div>
    </div>
  </div>
</div>
"""


def generate_html(data: dict, config: dict = None) -> str:
    """ダッシュボードHTMLを生成する。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    # 色判定ヘルパー
    def pnl_color(val):
        if val > 0:
            return "#00C853"
        elif val < 0:
            return "#FF1744"
        return "#9E9E9E"

    def pnl_sign(val):
        return f"+{val:,.0f}" if val > 0 else f"{val:,.0f}"

    # サマリーカード
    total_pnl = data["total_pnl"]
    unrealized_pnl = data["unrealized_pnl"]
    total_assets = data["total_assets"]
    total_return_pct = data["total_return_pct"]
    initial_balance = data["initial_balance"]
    total_change = total_pnl + unrealized_pnl

    # オープンポジション行
    open_rows = ""
    if data["open_positions"]:
        for p in data["open_positions"]:
            color = pnl_color(p["pnl"])
            open_rows += f"""<tr>
<td><a class="stock-link" href="stock/{p['ticker'].replace('.T','')}.html">{p['name']}</a><br><span class="ticker">{p['ticker'].replace('.T','')}</span></td>
<td class="num">&yen;{p['entry_price']:,.0f}</td>
<td class="num">&yen;{p['current_price']:,.0f}</td>
<td class="num">{p['shares']}</td>
<td class="num" style="color:{color}">{pnl_sign(p['pnl'])}<br><span class="small">({'+' if p['pnl_pct']>=0 else ''}{p['pnl_pct']:.1f}%)</span></td>
<td class="num">{p['days']}日</td>
</tr>"""
    else:
        open_rows = '<tr><td colspan="6" class="empty">保有中のポジションはありません</td></tr>'

    # クローズドトレード行
    closed_rows = ""
    if data["recent_closed"]:
        for t in data["recent_closed"]:
            color = pnl_color(t["pnl"])
            closed_rows += f"""<tr>
<td>{t['name']}<br><span class="ticker">{t['ticker'].replace('.T','')}</span></td>
<td class="num">&yen;{t['entry_price']:,.0f}</td>
<td class="num">&yen;{t['exit_price']:,.0f}</td>
<td class="num">{t['shares']}</td>
<td class="num" style="color:{color}">{pnl_sign(t['pnl'])}</td>
<td class="num">{t['entry_date']}<br>{t['exit_date']}</td>
</tr>"""
    else:
        closed_rows = '<tr><td colspan="6" class="empty">クローズドトレードはありません</td></tr>'

    # Chart.js データ
    equity_labels_json = json.dumps(data["equity_labels"])
    equity_data_json = json.dumps(data["equity_data"])

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Paper Trade Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#121212; color:#E0E0E0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:16px; max-width:960px; margin:0 auto; }}
h1 {{ font-size:1.3rem; margin-bottom:4px; }}
.updated {{ font-size:0.75rem; color:#757575; margin-bottom:16px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:20px; }}
.card {{ background:#1E1E1E; border-radius:10px; padding:14px; text-align:center; }}
.card .label {{ font-size:0.7rem; color:#9E9E9E; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }}
.card .value {{ font-size:1.4rem; font-weight:700; }}
.card .sub {{ font-size:0.7rem; color:#757575; margin-top:2px; }}
.section {{ margin-bottom:20px; }}
.section h2 {{ font-size:1rem; margin-bottom:8px; padding-bottom:4px; border-bottom:1px solid #333; }}
.chart-wrap {{ background:#1E1E1E; border-radius:10px; padding:12px; margin-bottom:20px; }}
table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ background:#1E1E1E; color:#9E9E9E; text-align:left; padding:8px 6px; font-weight:500; font-size:0.7rem; text-transform:uppercase; position:sticky; top:0; }}
td {{ padding:8px 6px; border-bottom:1px solid #2A2A2A; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.ticker {{ color:#757575; font-size:0.7rem; }}
.small {{ font-size:0.7rem; }}
.empty {{ text-align:center; color:#757575; padding:24px; }}
.hero-card {{ background:#1E1E1E; border-radius:12px; padding:20px; text-align:center; margin-bottom:12px; }}
.hero-card .label {{ font-size:0.7rem; color:#9E9E9E; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; }}
.hero-value {{ font-size:2rem; font-weight:700; }}
.hero-change {{ font-size:1rem; font-weight:600; margin-top:2px; }}
.green {{ color:#00C853; }}
.red {{ color:#FF1744; }}
.policy-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; }}
.policy-card {{ background:#1E1E1E; border-radius:10px; padding:14px; }}
.policy-title {{ font-size:0.7rem; color:#9E9E9E; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; font-weight:500; }}
.policy-body {{ font-size:0.8rem; }}
.policy-name {{ color:#64B5F6; font-weight:600; margin-bottom:6px; }}
.policy-rule {{ color:#B0BEC5; font-size:0.75rem; margin:3px 0; padding:4px 8px; background:#262626; border-radius:4px; }}
.buy-rule {{ border-left:3px solid #00C853; }}
.sell-rule {{ border-left:3px solid #FF1744; }}
.policy-item {{ display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px solid #2A2A2A; }}
.policy-item:last-child {{ border-bottom:none; }}
.policy-val {{ color:#E0E0E0; font-weight:600; }}
a.stock-link {{ color:#64B5F6; text-decoration:none; }}
a.stock-link:hover {{ text-decoration:underline; }}
@media(max-width:480px) {{
  .cards {{ grid-template-columns:repeat(2,1fr); }}
  .card .value {{ font-size:1.2rem; }}
  .policy-grid {{ grid-template-columns:1fr; }}
  table {{ font-size:0.75rem; }}
  td, th {{ padding:6px 4px; }}
}}
</style>
</head>
<body>
<h1>Paper Trade Dashboard</h1>
<div class="updated">最終更新: {now}</div>

<div class="hero-card">
  <div class="label">総資産</div>
  <div class="hero-value">&yen;{total_assets:,.0f}</div>
  <div class="hero-change" style="color:{pnl_color(total_change)}">{pnl_sign(total_change)}（{'+' if total_return_pct >= 0 else ''}{total_return_pct:.2f}%）</div>
  <div class="sub">初期資金 &yen;{initial_balance:,.0f}</div>
</div>

<div class="cards">
  <div class="card">
    <div class="label">確定損益</div>
    <div class="value" style="color:{pnl_color(total_pnl)}">&yen;{pnl_sign(total_pnl)}</div>
    <div class="sub">{data['trade_count']}トレード</div>
  </div>
  <div class="card">
    <div class="label">含み損益</div>
    <div class="value" style="color:{pnl_color(unrealized_pnl)}">&yen;{pnl_sign(unrealized_pnl)}</div>
    <div class="sub">{data['open_count']}銘柄保有</div>
  </div>
  <div class="card">
    <div class="label">勝率</div>
    <div class="value">{data['win_rate']}%</div>
  </div>
  <div class="card">
    <div class="label">最大DD</div>
    <div class="value red">&yen;{data['max_dd']:,.0f}</div>
  </div>
</div>

<div class="chart-wrap">
  <canvas id="equityChart" height="200"></canvas>
</div>

{generate_policy_section(config)}

<div class="section">
  <h2>保有中ポジション</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>銘柄</th><th>取得</th><th>現在</th><th>株数</th><th>損益</th><th>保有</th></tr></thead>
    <tbody>{open_rows}</tbody>
  </table>
  </div>
</div>

<div class="section">
  <h2>直近クローズドトレード</h2>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>銘柄</th><th>取得</th><th>売却</th><th>株数</th><th>損益</th><th>期間</th></tr></thead>
    <tbody>{closed_rows}</tbody>
  </table>
  </div>
</div>

<script>
const labels = {equity_labels_json};
const data = {equity_data_json};
if (labels.length > 0) {{
  const ctx = document.getElementById('equityChart').getContext('2d');
  const gradient = ctx.createLinearGradient(0,0,0,200);
  gradient.addColorStop(0, 'rgba(33,150,243,0.3)');
  gradient.addColorStop(1, 'rgba(33,150,243,0)');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [{{
        label: '総資産 (¥)',
        data: data,
        borderColor: '#2196F3',
        backgroundColor: gradient,
        fill: true,
        tension: 0.3,
        pointRadius: 2,
        pointHoverRadius: 5,
        borderWidth: 2,
      }},{{
        label: '初期資金 (¥{initial_balance:,.0f})',
        data: labels.map(() => {initial_balance}),
        borderColor: '#757575',
        borderWidth: 1,
        borderDash: [6,3],
        pointRadius: 0,
        fill: false,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ labels: {{ color:'#9E9E9E', font:{{ size:11 }}, usePointStyle:true, pointStyle:'line' }} }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{ return '¥' + ctx.parsed.y.toLocaleString(); }}
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color:'#757575', maxTicksLimit:8, font:{{ size:10 }} }},
          grid: {{ color:'#2A2A2A' }}
        }},
        y: {{
          ticks: {{
            color:'#757575',
            font:{{ size:10 }},
            callback: function(v) {{ return '¥' + v.toLocaleString(); }}
          }},
          grid: {{ color:'#2A2A2A' }}
        }}
      }}
    }}
  }});
}} else {{
  document.getElementById('equityChart').parentElement.innerHTML =
    '<p style="text-align:center;color:#757575;padding:24px">チャートデータがありません</p>';
}}
</script>
</body>
</html>"""
    return html


def main():
    config = load_config()
    balance = config.get("account", {}).get("balance", 300000)
    trades = load_trades()
    data = build_dashboard_data(trades, initial_balance=balance)
    os.makedirs(DOCS_DIR, exist_ok=True)
    html = generate_html(data, config)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard generated: {OUTPUT_FILE}")
    print(f"  Closed trades: {data['trade_count']} / Open: {data['open_count']}")
    print(f"  Total P&L: ¥{data['total_pnl']:,.0f} / Unrealized: ¥{data['unrealized_pnl']:,.0f}")

    # 銘柄別詳細ページを生成
    open_trades = [t for t in trades if t.get("status") == "open"]
    if open_trades:
        stock_dir = os.path.join(DOCS_DIR, "stock")
        os.makedirs(stock_dir, exist_ok=True)
        print(f"\nGenerating stock detail pages...")
        for t in open_trades:
            ticker = t["ticker"]
            code = ticker.replace(".T", "")
            name = NIKKEI_225.get(ticker, ticker)
            print(f"  {name}（{code}）...", end=" ", flush=True)
            detail = build_stock_detail(ticker, t["entry_price"])
            stock_html = generate_stock_html(detail)
            out_path = os.path.join(stock_dir, f"{code}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(stock_html)
            print("done")
        print(f"  {len(open_trades)} stock pages generated in {stock_dir}/")


if __name__ == "__main__":
    main()
