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
HISTORY_FILE = os.path.join(BASE_DIR, "execution_history.json")
HISTORY_OUTPUT = os.path.join(DOCS_DIR, "history.html")
REVIEW_OUTPUT = os.path.join(DOCS_DIR, "weekly-review.html")

# 銘柄名マッピング
from nikkei225 import NIKKEI_225
from portfolio import get_cash_balance


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
    losses = len(pnls) - wins
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

    # 現金残高を計算
    cash = get_cash_balance(initial_balance)
    # 株時価
    stock_value = round(sum(p["pnl"] + p["entry_price"] * p["shares"] for p in open_positions), 1) if open_positions else 0
    # 総資産 = 現金 + 株時価
    total_assets = round(cash + stock_value, 1)
    total_return_pct = round((total_assets - initial_balance) / initial_balance * 100, 2) if initial_balance else 0

    return {
        "initial_balance": initial_balance,
        "total_assets": total_assets,
        "cash": cash,
        "stock_value": stock_value,
        "total_return_pct": total_return_pct,
        "total_pnl": round(total_pnl, 1),
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "trade_count": trade_count,
        "max_dd": round(max_dd, 1),
        "open_count": len(open_trades),
        "unrealized_pnl": round(unrealized_pnl, 1),
        "equity_labels": equity_labels,
        "equity_data": equity_data,
        "open_positions": open_positions,
        "recent_closed": recent_closed,
    }


def build_stock_detail(ticker: str, entry_price: float, entry_date: str = "") -> dict:
    """銘柄の詳細データ（6ヶ月分の価格, SMA, RSI, ファンダメンタル）を取得する。"""
    import yfinance as yf
    import pandas as pd
    import numpy as np
    from strategy import calculate_sma, calculate_rsi

    result = {
        "ticker": ticker,
        "name": NIKKEI_225.get(ticker, ticker),
        "entry_price": entry_price,
        "entry_date": entry_date,
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

    entry_date = data.get("entry_date", "")

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
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3"></script>
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
const entryDate = '{entry_date}';

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
        ...(entryDate && dates.includes(entryDate) ? [{{
          label: '購入日',
          data: dates.map(d => d === entryDate ? entryPrice : null),
          borderColor: 'transparent',
          backgroundColor: '#F44336',
          pointRadius: 8,
          pointStyle: 'triangle',
          pointRotation: 0,
          showLine: false,
          order: 0,
        }}] : []),
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color:'#9E9E9E', font:{{ size:11 }}, usePointStyle:true, pointStyle:'line' }} }},
        annotation: entryDate && dates.includes(entryDate) ? {{
          annotations: {{
            entryLine: {{
              type: 'line',
              xMin: entryDate,
              xMax: entryDate,
              borderColor: 'rgba(244,67,54,0.4)',
              borderWidth: 1,
              borderDash: [4,3],
              label: {{
                display: true,
                content: '購入 ' + entryDate,
                position: 'start',
                backgroundColor: 'rgba(244,67,54,0.8)',
                color: '#fff',
                font: {{ size: 10 }},
                padding: 4,
              }}
            }}
          }}
        }} : {{}},
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
    cash = data.get("cash", 0)
    stock_value = data.get("stock_value", 0)
    total_change = total_assets - initial_balance

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
<div class="updated">最終更新: {now} &nbsp;|&nbsp; <a href="history.html" style="color:#64B5F6;text-decoration:none">実行履歴 &rarr;</a> &nbsp;|&nbsp; <a href="weekly-review.html" style="color:#64B5F6;text-decoration:none">週次レビュー &rarr;</a></div>

<div class="hero-card">
  <div class="label">総資産</div>
  <div class="hero-value">&yen;{total_assets:,.0f}</div>
  <div class="hero-change" style="color:{pnl_color(total_change)}">{pnl_sign(total_change)}（{'+' if total_return_pct >= 0 else ''}{total_return_pct:.2f}%）</div>
  <div class="sub">初期資金 &yen;{initial_balance:,.0f}</div>
</div>

<div class="cards">
  <div class="card">
    <div class="label">株時価</div>
    <div class="value">&yen;{stock_value:,.0f}</div>
    <div class="sub">{data['open_count']}銘柄保有</div>
  </div>
  <div class="card">
    <div class="label">現金</div>
    <div class="value">&yen;{cash:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">確定損益</div>
    <div class="value" style="color:{pnl_color(total_pnl)}">&yen;{pnl_sign(total_pnl)}</div>
    <div class="sub">{data['trade_count']}トレード</div>
  </div>
  <div class="card">
    <div class="label">含み損益</div>
    <div class="value" style="color:{pnl_color(unrealized_pnl)}">&yen;{pnl_sign(unrealized_pnl)}</div>
  </div>
  <div class="card">
    <div class="label">勝率</div>
    <div class="value">{data['win_rate']}%</div>
    <div class="sub">{data['wins']}勝{data['losses']}敗</div>
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


def load_history() -> list:
    """execution_history.json を読み込む。"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def build_weekly_review(trades: list, history: list, config: dict) -> dict:
    """今週のトレードデータを分析し、週次レビュー用データを構築する。"""
    from datetime import date

    today = date.today()
    # 今週の月曜日を算出（weekday: 0=月曜）
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.isoformat()

    # --- 今週クローズドトレード ---
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    weekly_closed = [
        t for t in closed_trades
        if t.get("exit_date") and t["exit_date"] >= monday_str
    ]

    weekly_pnls = [t.get("pnl", 0) for t in weekly_closed]
    weekly_total_pnl = sum(weekly_pnls)
    weekly_wins = sum(1 for p in weekly_pnls if p > 0)
    weekly_losses = sum(1 for p in weekly_pnls if p <= 0) if weekly_pnls else 0
    weekly_win_rate = round(weekly_wins / len(weekly_pnls) * 100, 1) if weekly_pnls else 0

    # --- ベスト & ワースト ---
    best_trade = max(weekly_closed, key=lambda t: t.get("pnl", 0)) if weekly_closed else None
    worst_trade = min(weekly_closed, key=lambda t: t.get("pnl", 0)) if weekly_closed else None

    def enrich_trade(t):
        """トレードにRSIや理由の情報を付加する。"""
        if not t:
            return None
        ticker = t["ticker"]
        name = NIKKEI_225.get(ticker, ticker)
        entry_rsi = None
        entry_reason = ""
        # execution_historyからエントリー時のRSI・理由を探す
        for h in history:
            for e in (h.get("executions", {}).get("entries", [])):
                if e.get("ticker") == ticker:
                    entry_rsi = e.get("rsi")
                    entry_reason = e.get("reason", "")
                    break
            # buy_signalsからも探す
            if entry_rsi is None:
                for s in h.get("buy_signals", []):
                    if s.get("ticker") == ticker:
                        entry_rsi = s.get("rsi")
                        entry_reason = s.get("reason", "")
                        break
        pnl_pct = round((t.get("exit_price", 0) / t["entry_price"] - 1) * 100, 2) if t["entry_price"] else 0
        return {
            "ticker": ticker,
            "name": name,
            "entry_price": t["entry_price"],
            "exit_price": t.get("exit_price", 0),
            "pnl": t.get("pnl", 0),
            "pnl_pct": pnl_pct,
            "entry_date": t.get("entry_date", ""),
            "exit_date": t.get("exit_date", ""),
            "rsi": entry_rsi,
            "reason": entry_reason,
        }

    # --- 損切り分析 ---
    # ストップ発動トレード: exit_dateがあり、かつ損失が出ているもの
    # execution_historyのexitsからreason="トレーリングストップ発動"を検出
    stop_tickers_dates = set()
    for h in history:
        if h.get("date", "") < monday_str:
            continue
        for ex in h.get("executions", {}).get("exits", []):
            if "ストップ" in ex.get("reason", ""):
                stop_tickers_dates.add((ex.get("ticker"), h["date"]))

    stop_trades = []
    for t in weekly_closed:
        if (t["ticker"], t.get("exit_date")) in stop_tickers_dates:
            stop_trades.append(t)
        elif t.get("pnl", 0) < 0 and t.get("stop_price"):
            # ストップ価格とexit_priceが近い場合もストップ発動とみなす
            if t.get("exit_price") and abs(t["exit_price"] - t["stop_price"]) / t["entry_price"] < 0.01:
                stop_trades.append(t)

    stop_count = len(stop_trades)
    stop_avg_loss = round(sum(t.get("pnl", 0) for t in stop_trades) / stop_count, 1) if stop_count else 0
    stop_total_loss = round(sum(t.get("pnl", 0) for t in stop_trades), 1)

    # 「ストップなしなら」の仮想損失: high_priceから現在のexit_priceまでの差分ではなく
    # entry_priceからexit_priceまでの全損失 vs entry_priceからhigh_priceまでの最大利益の機会損失を考慮
    # ここではストップなしで保有し続けた場合の追加損失を推定
    # → 実際にはストップがあったから損失がstop_priceで済んだ。なければさらに下がった可能性
    # 簡易推計: ストップなしならentry_price - exit_priceの方向に更に5%損失が広がったと仮定
    no_stop_est_loss = round(
        sum(t.get("pnl", 0) * 1.5 for t in stop_trades), 1
    ) if stop_trades else 0

    # --- 今週のexecution_historyからアクション集計 ---
    weekly_entries = 0
    weekly_exits = 0
    weekly_partial_exits = 0
    weekly_stop_updates = 0
    weekly_scanned = 0
    for h in history:
        if h.get("date", "") < monday_str:
            continue
        execs = h.get("executions", {})
        weekly_entries += len(execs.get("entries", []))
        weekly_exits += len(execs.get("exits", []))
        weekly_partial_exits += len(execs.get("partial_exits", []))
        weekly_stop_updates += len(execs.get("trailing_stop_updates", []))
        weekly_scanned += h.get("scan", {}).get("total", 0)

    # --- エントリー後の株価推移（買いが利益になったか） ---
    # 全クローズドの勝率（全期間）
    all_pnls = [t.get("pnl", 0) for t in closed_trades]
    all_win_rate = round(sum(1 for p in all_pnls if p > 0) / len(all_pnls) * 100, 1) if all_pnls else 0
    all_total_pnl = sum(all_pnls)

    # 利確トレード（reasonに利確が含まれる）
    profit_take_trades = [t for t in weekly_closed if "利確" in t.get("reason", "")]
    profit_take_count = len(profit_take_trades)

    # --- 来週のヒント ---
    hint = _generate_weekly_hint(weekly_closed, weekly_win_rate, stop_count, profit_take_count, config)

    return {
        "week_start": monday_str,
        "week_end": today.isoformat(),
        "weekly_total_pnl": round(weekly_total_pnl, 1),
        "weekly_trade_count": len(weekly_closed),
        "weekly_wins": weekly_wins,
        "weekly_losses": weekly_losses,
        "weekly_win_rate": weekly_win_rate,
        "best_trade": enrich_trade(best_trade),
        "worst_trade": enrich_trade(worst_trade),
        "stop_count": stop_count,
        "stop_avg_loss": stop_avg_loss,
        "stop_total_loss": stop_total_loss,
        "no_stop_est_loss": no_stop_est_loss,
        "weekly_entries": weekly_entries,
        "weekly_exits": weekly_exits,
        "weekly_partial_exits": weekly_partial_exits,
        "weekly_stop_updates": weekly_stop_updates,
        "weekly_scanned": weekly_scanned,
        "all_win_rate": all_win_rate,
        "all_total_pnl": round(all_total_pnl, 1),
        "all_trade_count": len(closed_trades),
        "profit_take_count": profit_take_count,
        "hint": hint,
        "weekly_closed": [enrich_trade(t) for t in weekly_closed],
    }


def _generate_weekly_hint(weekly_closed, win_rate, stop_count, profit_take_count, config):
    """今週のパターンから1つ具体的改善案を生成する。"""
    if not weekly_closed:
        return "今週はクローズしたトレードがありません。来週のシグナルを待ちましょう。"

    # 全敗パターン
    if win_rate == 0 and len(weekly_closed) > 0:
        return ("今週は全トレードが損失でした。エントリーのタイミングが早すぎる可能性があります。"
                "RSIがより低い水準（30以下）での買いに絞ることを検討してみてください。")

    # ストップ多発パターン
    if stop_count >= 3:
        return (f"ストップ発動が{stop_count}回と多めでした。ボラティリティが高い相場では "
                "エントリーを厳選するか、ポジションサイズを小さくすることでリスクを抑えられます。")

    # 利確なしパターン
    if profit_take_count == 0 and len(weekly_closed) >= 2:
        return ("利確ルールが発動しませんでした。含み益が出ている間に逃さないよう、"
                "トレーリングストップの引き締め条件を見直すのも一案です。")

    # 勝率低パターン
    if win_rate < 40:
        losing = [t for t in weekly_closed if t.get("pnl", 0) < 0]
        avg_loss = abs(sum(t.get("pnl", 0) for t in losing) / len(losing)) if losing else 0
        return (f"勝率{win_rate}%。平均損失¥{avg_loss:,.0f}を抑えるため、"
                "損切りラインを-5%→-4%に引き締めるか、ポジションサイズの見直しを検討してみてください。")

    # デフォルト
    profit_take_pct = config.get("strategy", {}).get("profit_take_pct", 0.07)
    return (f"勝率{win_rate}%と安定しています。現在の利確ライン（+{int(profit_take_pct*100)}%）を"
            "維持しつつ、利益が伸びるトレードではトレーリングストップを活用して利益を伸ばしましょう。")


def generate_weekly_review_html(data: dict) -> str:
    """週次振り返りレポートHTMLを生成する。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    week_start = data["week_start"]
    week_end = data["week_end"]

    def pnl_color(val):
        if val > 0: return "#00C853"
        elif val < 0: return "#FF1744"
        return "#9E9E9E"

    def pnl_sign(val):
        return f"+{val:,.0f}" if val > 0 else f"{val:,.0f}"

    # ベスト/ワーストトレードカード
    best = data.get("best_trade")
    worst = data.get("worst_trade")

    def trade_card(t, label, border_color):
        if not t:
            return f'<div class="bw-card" style="border-left-color:{border_color}"><div class="bw-label">{label}</div><div class="bw-empty">該当なし</div></div>'
        rsi_text = f"RSI {t['rsi']:.1f}" if t.get("rsi") else "RSI -"
        return f'''<div class="bw-card" style="border-left-color:{border_color}">
  <div class="bw-label">{label}</div>
  <div class="bw-name">{t['name']}</div>
  <div class="bw-pnl" style="color:{pnl_color(t['pnl'])}">{pnl_sign(t['pnl'])}（{'+' if t['pnl_pct']>=0 else ''}{t['pnl_pct']:.1f}%）</div>
  <div class="bw-detail">&yen;{t['entry_price']:,.0f} &rarr; &yen;{t['exit_price']:,.0f}</div>
  <div class="bw-detail">{t['entry_date']} 〜 {t['exit_date']}</div>
  <div class="bw-meta">{rsi_text}</div>
  {f'<div class="bw-reason">{t["reason"]}</div>' if t.get('reason') else ''}
</div>'''

    best_html = trade_card(best, "ベストトレード", "#00C853")
    worst_html = trade_card(worst, "ワーストトレード", "#FF1744")

    # 損切りレビュー
    stop_count = data["stop_count"]
    stop_avg_loss = data["stop_avg_loss"]
    stop_total_loss = data["stop_total_loss"]
    no_stop_est = data["no_stop_est_loss"]
    saved_by_stop = round(no_stop_est - stop_total_loss, 1)

    # 週次クローズドトレード一覧
    closed_rows = ""
    for t in data.get("weekly_closed", []):
        if not t:
            continue
        color = pnl_color(t["pnl"])
        rsi_text = f"{t['rsi']:.1f}" if t.get("rsi") else "-"
        closed_rows += f"""<tr>
<td>{t['name']}<br><span class="ticker">{t['ticker'].replace('.T','')}</span></td>
<td class="num">&yen;{t['entry_price']:,.0f}</td>
<td class="num">&yen;{t['exit_price']:,.0f}</td>
<td class="num" style="color:{color}">{pnl_sign(t['pnl'])}<br><span class="small">({'+' if t['pnl_pct']>=0 else ''}{t['pnl_pct']:.1f}%)</span></td>
<td class="num">{rsi_text}</td>
</tr>"""

    if not closed_rows:
        closed_rows = '<tr><td colspan="5" class="empty">今週のクローズドトレードはありません</td></tr>'

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>週次振り返りレポート（{week_start} 〜 {week_end}）</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#121212; color:#E0E0E0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:16px; max-width:960px; margin:0 auto; }}
a.back {{ color:#64B5F6; text-decoration:none; font-size:0.85rem; display:inline-block; margin-bottom:12px; }}
a.back:hover {{ text-decoration:underline; }}
h1 {{ font-size:1.3rem; margin-bottom:4px; }}
.subtitle {{ font-size:0.85rem; color:#9E9E9E; margin-bottom:4px; }}
.updated {{ font-size:0.75rem; color:#757575; margin-bottom:20px; }}
.section {{ margin-bottom:24px; }}
.section h2 {{ font-size:1rem; margin-bottom:10px; padding-bottom:4px; border-bottom:1px solid #333; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-bottom:20px; }}
.card {{ background:#1E1E1E; border-radius:10px; padding:14px; text-align:center; }}
.card .label {{ font-size:0.7rem; color:#9E9E9E; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px; }}
.card .value {{ font-size:1.4rem; font-weight:700; }}
.card .sub {{ font-size:0.7rem; color:#757575; margin-top:2px; }}
.green {{ color:#00C853; }}
.red {{ color:#FF1744; }}

.bw-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.bw-card {{ background:#1E1E1E; border-radius:10px; padding:16px; border-left:4px solid #757575; }}
.bw-label {{ font-size:0.7rem; color:#9E9E9E; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }}
.bw-name {{ font-size:1rem; font-weight:700; margin-bottom:4px; }}
.bw-pnl {{ font-size:1.2rem; font-weight:700; margin-bottom:6px; }}
.bw-detail {{ font-size:0.8rem; color:#B0BEC5; }}
.bw-meta {{ font-size:0.75rem; color:#64B5F6; margin-top:6px; }}
.bw-reason {{ font-size:0.72rem; color:#757575; margin-top:4px; padding:4px 8px; background:#262626; border-radius:4px; }}
.bw-empty {{ color:#757575; font-size:0.85rem; }}

.stop-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }}
.stop-card {{ background:#1E1E1E; border-radius:10px; padding:14px; text-align:center; }}
.stop-card .label {{ font-size:0.7rem; color:#9E9E9E; margin-bottom:4px; }}
.stop-card .value {{ font-size:1.3rem; font-weight:700; }}
.stop-card .sub {{ font-size:0.7rem; color:#757575; margin-top:2px; }}
.saved {{ color:#64B5F6; }}

.hint-box {{ background:#1B2A1B; border:1px solid #2E7D32; border-radius:10px; padding:16px; font-size:0.88rem; line-height:1.6; color:#C8E6C9; }}

.action-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(100px,1fr)); gap:10px; }}
.action-card {{ background:#1E1E1E; border-radius:10px; padding:12px; text-align:center; }}
.action-card .label {{ font-size:0.65rem; color:#9E9E9E; margin-bottom:4px; text-transform:uppercase; }}
.action-card .value {{ font-size:1.2rem; font-weight:700; }}

table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ background:#1E1E1E; color:#9E9E9E; text-align:left; padding:8px 6px; font-weight:500; font-size:0.7rem; text-transform:uppercase; position:sticky; top:0; }}
td {{ padding:8px 6px; border-bottom:1px solid #2A2A2A; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.ticker {{ color:#757575; font-size:0.7rem; }}
.small {{ font-size:0.7rem; }}
.empty {{ text-align:center; color:#757575; padding:24px; }}

@media(max-width:480px) {{
  .cards {{ grid-template-columns:repeat(2,1fr); }}
  .card .value {{ font-size:1.2rem; }}
  .bw-grid {{ grid-template-columns:1fr; }}
  .stop-grid {{ grid-template-columns:repeat(2,1fr); }}
  table {{ font-size:0.75rem; }}
  td, th {{ padding:6px 4px; }}
}}
</style>
</head>
<body>
<a class="back" href="index.html">&larr; Dashboard</a>
<h1>週次振り返りレポート</h1>
<div class="subtitle">{week_start} 〜 {week_end}</div>
<div class="updated">生成: {now}</div>

<!-- 1. 今週のサマリー -->
<div class="section">
  <h2>今週のサマリー</h2>
  <div class="cards">
    <div class="card">
      <div class="label">週間損益</div>
      <div class="value" style="color:{pnl_color(data['weekly_total_pnl'])}">&yen;{pnl_sign(data['weekly_total_pnl'])}</div>
    </div>
    <div class="card">
      <div class="label">勝率</div>
      <div class="value">{data['weekly_win_rate']}%</div>
      <div class="sub">{data['weekly_wins']}勝{data['weekly_losses']}敗</div>
    </div>
    <div class="card">
      <div class="label">トレード数</div>
      <div class="value">{data['weekly_trade_count']}</div>
    </div>
    <div class="card">
      <div class="label">全期間勝率</div>
      <div class="value">{data['all_win_rate']}%</div>
      <div class="sub">全{data['all_trade_count']}件</div>
    </div>
    <div class="card">
      <div class="label">全期間損益</div>
      <div class="value" style="color:{pnl_color(data['all_total_pnl'])}">&yen;{pnl_sign(data['all_total_pnl'])}</div>
    </div>
  </div>
</div>

<!-- 2. ベスト & ワースト -->
<div class="section">
  <h2>ベスト &amp; ワースト</h2>
  <div class="bw-grid">
    {best_html}
    {worst_html}
  </div>
</div>

<!-- 3. 損切りレビュー -->
<div class="section">
  <h2>損切りレビュー</h2>
  <div class="stop-grid">
    <div class="stop-card">
      <div class="label">ストップ発動</div>
      <div class="value">{stop_count}回</div>
    </div>
    <div class="stop-card">
      <div class="label">平均損失</div>
      <div class="value red">&yen;{stop_avg_loss:,.0f}</div>
    </div>
    <div class="stop-card">
      <div class="label">ストップ損失合計</div>
      <div class="value red">&yen;{stop_total_loss:,.0f}</div>
    </div>
    <div class="stop-card">
      <div class="label">ストップなし推定損失</div>
      <div class="value red">&yen;{no_stop_est:,.0f}</div>
    </div>
    <div class="stop-card">
      <div class="label">ストップで回避</div>
      <div class="value saved">&yen;{saved_by_stop:,.0f}</div>
      <div class="sub">推定回避額</div>
    </div>
  </div>
</div>

<!-- 4. 戦略の答え合わせ -->
<div class="section">
  <h2>戦略の答え合わせ</h2>
  <div class="action-grid">
    <div class="action-card">
      <div class="label">スキャン銘柄</div>
      <div class="value">{data['weekly_scanned']}</div>
    </div>
    <div class="action-card">
      <div class="label">新規エントリー</div>
      <div class="value">{data['weekly_entries']}</div>
    </div>
    <div class="action-card">
      <div class="label">全売却</div>
      <div class="value">{data['weekly_exits']}</div>
    </div>
    <div class="action-card">
      <div class="label">利確</div>
      <div class="value">{data['weekly_partial_exits'] + data['profit_take_count']}</div>
    </div>
    <div class="action-card">
      <div class="label">ストップ更新</div>
      <div class="value">{data['weekly_stop_updates']}</div>
    </div>
  </div>
  <div style="overflow-x:auto; margin-top:12px">
  <table>
    <thead><tr><th>銘柄</th><th>取得</th><th>売却</th><th>損益</th><th>RSI</th></tr></thead>
    <tbody>{closed_rows}</tbody>
  </table>
  </div>
</div>

<!-- 5. 来週のヒント -->
<div class="section">
  <h2>来週のヒント</h2>
  <div class="hint-box">
    {data['hint']}
  </div>
</div>

</body>
</html>"""
    return html


def generate_history_html(history: list) -> str:
    """実行履歴のアコーディオンHTMLを生成する。アクション種別ごとにまとめて表示。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    history_json = json.dumps(history, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>実行履歴</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#121212; color:#E0E0E0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:16px; max-width:960px; margin:0 auto; }}
a.back {{ color:#64B5F6; text-decoration:none; font-size:0.85rem; display:inline-block; margin-bottom:12px; }}
a.back:hover {{ text-decoration:underline; }}
h1 {{ font-size:1.3rem; margin-bottom:4px; }}
.updated {{ font-size:0.75rem; color:#757575; margin-bottom:16px; }}
.empty {{ text-align:center; color:#757575; padding:48px 16px; }}

details {{ margin-bottom:8px; }}
details > summary {{ cursor:pointer; list-style:none; }}
details > summary::-webkit-details-marker {{ display:none; }}
details > summary::before {{ content:'\\25B6'; display:inline-block; margin-right:8px; font-size:0.7rem; transition:transform 0.2s; }}
details[open] > summary::before {{ transform:rotate(90deg); }}

.date-group > summary {{
  background:#1E1E1E; border-radius:8px; padding:12px 16px; font-size:0.9rem; font-weight:600;
  display:flex; align-items:center; gap:8px; flex-wrap:wrap;
}}
.date-group > .content {{ padding:8px 0 8px 16px; }}

.session-group {{ margin-bottom:6px; }}
.session-group > summary {{
  background:#262626; border-radius:6px; padding:10px 14px; font-size:0.82rem; font-weight:500;
  display:flex; align-items:center; gap:8px; flex-wrap:wrap;
}}
.session-group > .content {{ padding:10px 0 10px 4px; }}

.badge {{ font-size:0.7rem; padding:2px 8px; border-radius:10px; font-weight:500; }}
.badge-action {{ background:#0D47A1; color:#90CAF9; }}
.badge-none {{ background:#333; color:#757575; }}
.badge-scan {{ background:#263238; color:#90A4AE; font-size:0.65rem; }}

/* Action block: one block per action type */
.act {{ background:#1E1E1E; border-radius:8px; padding:12px 16px; margin-bottom:8px; border-left:3px solid #757575; }}
.act-buy {{ border-left-color:#00C853; }}
.act-sell {{ border-left-color:#FF1744; }}
.act-partial {{ border-left-color:#FF9800; }}
.act-stop {{ border-left-color:#64B5F6; }}

.act-title {{ font-size:0.78rem; font-weight:700; margin-bottom:6px; display:flex; align-items:center; gap:6px; }}
.act-title .tag {{ font-size:0.65rem; padding:1px 6px; border-radius:3px; }}
.tag-buy {{ background:#1B5E20; color:#A5D6A7; }}
.tag-sell {{ background:#B71C1C; color:#EF9A9A; }}
.tag-partial {{ background:#E65100; color:#FFB74D; }}
.tag-stop {{ background:#0D47A1; color:#90CAF9; }}

.act-body {{ font-size:0.82rem; color:#B0BEC5; line-height:1.7; }}
.act-body b {{ color:#E0E0E0; font-weight:600; }}
.act-reason {{ font-size:0.75rem; color:#757575; margin-top:4px; }}
.green {{ color:#00C853; }} .red {{ color:#FF1744; }}

.no-action {{ background:#1E1E1E; border-radius:8px; padding:14px; color:#757575; font-size:0.82rem; text-align:center; }}
.snapshot {{ margin-top:4px; padding:8px 16px; background:#1A1A1A; border-radius:6px; font-size:0.75rem; color:#757575; }}
.snapshot b {{ color:#B0BEC5; font-weight:500; }}

@media(max-width:480px) {{ .act-body {{ font-size:0.78rem; }} }}
</style>
</head>
<body>
<a class="back" href="index.html">&larr; Dashboard</a>
<h1>実行履歴</h1>
<div class="updated">最終更新: {now}</div>
<div id="root"></div>
<script>
const H = {history_json};
const $ = s => s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : '';
const Y = v => v != null ? '&yen;' + Number(v).toLocaleString() : '-';
const P = v => v > 0 ? '<span class="green">+' + v.toFixed(1) + '%</span>' : '<span class="red">' + v.toFixed(1) + '%</span>';
const PNL = v => v > 0 ? '<span class="green">+' + Y(v) + '</span>' : v < 0 ? '<span class="red">' + Y(v) + '</span>' : Y(0);

if (!H.length) {{
  document.getElementById('root').innerHTML = '<div class="empty">データなし</div>';
}} else {{
  const byDate = {{}};
  H.forEach(h => {{ (byDate[h.date] = byDate[h.date] || []).push(h); }});
  const dates = Object.keys(byDate).sort().reverse();
  let o = '';

  dates.forEach((date, di) => {{
    const ss = byDate[date];
    let da = 0;
    ss.forEach(h => {{
      const x = h.executions || {{}};
      da += (x.entries?.length||0)+(x.exits?.length||0)+(x.partial_exits?.length||0)+(x.trailing_stop_updates?.length||0);
    }});
    const latest = di === 0;
    o += '<details class="date-group"' + (latest?' open':'') + '><summary>' + $(date);
    o += da ? ' <span class="badge badge-action">' + da + '件</span>' : ' <span class="badge badge-none">変更なし</span>';
    o += '</summary><div class="content">';

    ss.forEach(h => {{
      const scan = h.scan || {{}};
      const x = h.executions || {{}};
      const entries = x.entries || [], exits = x.exits || [], partials = x.partial_exits || [], stops = x.trailing_stop_updates || [];
      const ac = entries.length + exits.length + partials.length + stops.length;

      o += '<details class="session-group"' + (latest?' open':'') + '><summary>' + $(h.session);
      o += ac ? ' <span class="badge badge-action">' + ac + '件</span>' : ' <span class="badge badge-none">変更なし</span>';
      o += ' <span class="badge badge-scan">scan ' + (scan.total||0) + ' / buy ' + (scan.buy_count||0) + ' / sell ' + (scan.sell_count||0) + '</span>';
      o += '</summary><div class="content">';

      if (!ac) {{
        o += '<div class="no-action">売買・ストップ更新なし</div>';
      }}

      // Exits block
      if (exits.length) {{
        o += '<div class="act act-sell"><div class="act-title"><span class="tag tag-sell">売却</span> ' + exits.length + '件</div><div class="act-body">';
        exits.forEach((e,i) => {{
          if (i) o += '<br>';
          o += '<b>' + $(e.name) + '</b> ';
          if (e.entry_price) o += Y(e.entry_price) + '&rarr;';
          o += Y(e.price);
          if (e.shares) o += ' &times;' + e.shares + '株';
          o += ' &nbsp;損益 ' + PNL(e.pnl);
          if (e.pnl_pct != null) o += '（' + P(e.pnl_pct) + '）';
        }});
        // Common reason
        const reasons = [...new Set(exits.map(e => e.reason).filter(Boolean))];
        if (reasons.length) o += '<div class="act-reason">' + reasons.map($).join(' / ') + '</div>';
        o += '</div></div>';
      }}

      // Partial exits block
      if (partials.length) {{
        o += '<div class="act act-partial"><div class="act-title"><span class="tag tag-partial">利確</span> ' + partials.length + '件</div><div class="act-body">';
        partials.forEach((p,i) => {{
          if (i) o += '<br>';
          o += '<b>' + $(p.name) + '</b> ' + (p.shares||0) + '株/' + (p.total_shares||'?') + '株を' + Y(p.price) + 'で売却';
          o += ' &nbsp;損益 ' + PNL(p.pnl);
        }});
        const gmin = Math.min(...partials.map(p => p.gain_pct||0));
        const gmax = Math.max(...partials.map(p => p.gain_pct||0));
        const gpct = gmin === gmax ? '+' + gmin.toFixed(1) + '%' : '+' + gmin.toFixed(1) + '〜' + gmax.toFixed(1) + '%';
        o += '<div class="act-reason">含み益 ' + gpct + ' で利確ルール適用（+7%到達で半分売却）</div>';
        o += '</div></div>';
      }}

      // Stop updates block
      if (stops.length) {{
        o += '<div class="act act-stop"><div class="act-title"><span class="tag tag-stop">ストップ引き締め</span> ' + stops.length + '件</div><div class="act-body">';
        o += stops.map(s => '<b>' + $(s.name) + '</b>(' + P(s.gain_pct||0) + ') &rarr;' + Y(s.new_stop)).join(' / ');
        const smin = Math.min(...stops.map(s => s.gain_pct||0));
        const smax = Math.max(...stops.map(s => s.gain_pct||0));
        const spct = smin === smax ? '+' + smin.toFixed(1) + '%' : '+' + smin.toFixed(1) + '〜' + smax.toFixed(1) + '%';
        o += '<div class="act-reason">含み益 ' + spct + ' のためトレーリングストップを-4%に引き締め</div>';
        o += '</div></div>';
      }}

      // Entries block
      if (entries.length) {{
        o += '<div class="act act-buy"><div class="act-title"><span class="tag tag-buy">購入</span> ' + entries.length + '件</div><div class="act-body">';
        entries.forEach((e,i) => {{
          if (i) o += '<br>';
          o += '<b>' + $(e.name) + '</b> ' + Y(e.price) + '&times;' + (e.shares||0) + '株';
          o += '（RSI ' + (e.rsi != null ? e.rsi.toFixed(1) : '-') + '）';
          o += ' ストップ ' + Y(e.stop_loss);
        }});
        const reasons = [...new Set(entries.map(e => e.reason).filter(Boolean))];
        if (reasons.length) o += '<div class="act-reason">' + reasons.map($).join(' / ') + '</div>';
        o += '</div></div>';
      }}

      // Snapshot
      if (h.portfolio_snapshot) {{
        const ps = h.portfolio_snapshot;
        const sv = ps.stock_value != null ? ps.stock_value : ps.total_value;
        o += '<div class="snapshot">';
        o += '保有 <b>' + (ps.open_count||0) + '銘柄</b>';
        o += ' / 株時価 <b>' + Y(sv) + '</b>';
        if (ps.cash != null) o += ' / 現金 <b>' + Y(ps.cash) + '</b>';
        if (ps.total_assets != null) o += ' / 総資産 <b>' + Y(ps.total_assets) + '</b>';
        o += '</div>';
      }}

      o += '</div></details>';
    }});
    o += '</div></details>';
  }});
  document.getElementById('root').innerHTML = o;
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

    # 実行履歴ページを生成
    history = load_history()
    if history:
        history_html = generate_history_html(history)
        with open(HISTORY_OUTPUT, "w", encoding="utf-8") as f:
            f.write(history_html)
        print(f"History page generated: {HISTORY_OUTPUT} ({len(history)} records)")
    else:
        print("No execution history found, skipping history page")

    # 週次振り返りレポートを生成
    review_data = build_weekly_review(trades, history, config)
    review_html = generate_weekly_review_html(review_data)
    with open(REVIEW_OUTPUT, "w", encoding="utf-8") as f:
        f.write(review_html)
    print(f"Weekly review generated: {REVIEW_OUTPUT}")
    print(f"  Week: {review_data['week_start']} ~ {review_data['week_end']}")
    print(f"  Weekly P&L: ¥{review_data['weekly_total_pnl']:,.0f} ({review_data['weekly_trade_count']} trades)")

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
            detail = build_stock_detail(ticker, t["entry_price"], t.get("entry_date", ""))
            stock_html = generate_stock_html(detail)
            out_path = os.path.join(stock_dir, f"{code}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(stock_html)
            print("done")
        print(f"  {len(open_trades)} stock pages generated in {stock_dir}/")


if __name__ == "__main__":
    main()
