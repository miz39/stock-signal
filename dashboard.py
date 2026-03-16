"""
株式ポートフォリオ ダッシュボード
Streamlitで保有銘柄のリアルタイム状況を可視化する。
"""

import json
import os
import numpy as np
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf
import yaml

PORTFOLIO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_portfolio.json")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

SECTOR_MAP = {
    "4452.T": "消費財",
    "4502.T": "医薬品",
    "5020.T": "エネルギー",
    "6981.T": "電機",
    "8031.T": "商社",
    "8306.T": "銀行",
    "9020.T": "鉄道",
    "9433.T": "通信",
}


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


@st.cache_data(ttl=300)
def fetch_prices(tickers):
    prices = {}
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")
            if not hist.empty:
                prices[ticker] = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    return prices


@st.cache_data(ttl=600)
def fetch_history(ticker, period="1y"):
    try:
        stock = yf.Ticker(ticker)
        return stock.history(period=period)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_fundamentals(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return info
    except Exception:
        return {}


@st.cache_data(ttl=3600)
def fetch_financials(ticker):
    try:
        stock = yf.Ticker(ticker)
        income = stock.income_stmt
        balance = stock.balance_sheet
        return income, balance
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    with open(PORTFOLIO_FILE, "r") as f:
        return json.load(f)


def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2, ensure_ascii=False)


def render_overview(portfolio, prices):
    """ポートフォリオ概要"""
    rows = []
    for pos in portfolio:
        ticker = pos["ticker"]
        current = prices.get(ticker, pos["entry_price"])
        entry = pos["entry_price"]
        shares = pos["shares"]
        cost = entry * shares
        value = current * shares
        pnl = value - cost
        pnl_pct = (current / entry - 1) * 100
        sector = SECTOR_MAP.get(ticker, "その他")
        rows.append({
            "銘柄": pos["name"], "コード": ticker.replace(".T", ""),
            "セクター": sector, "取得単価": entry, "現在価格": current,
            "株数": shares, "取得額": cost, "評価額": value,
            "損益": pnl, "損益%": pnl_pct, "ticker": ticker,
        })
    df = pd.DataFrame(rows)

    total_cost = df["取得額"].sum()
    total_value = df["評価額"].sum()
    total_pnl = df["損益"].sum()
    total_pnl_pct = (total_value / total_cost - 1) * 100

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("取得額合計", f"¥{total_cost:,.0f}")
    col2.metric("評価額合計", f"¥{total_value:,.0f}")
    col3.metric("含み損益", f"¥{total_pnl:,.0f}", f"{total_pnl_pct:+.1f}%")
    col4.metric("保有銘柄数", f"{len(df)}銘柄")

    st.divider()

    left, right = st.columns(2)
    with left:
        st.subheader("セクター別配分")
        sector_df = df.groupby("セクター")["評価額"].sum().reset_index()
        fig = px.pie(sector_df, values="評価額", names="セクター", hole=0.4)
        fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=350)
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("銘柄別損益")
        colors = ["#00C853" if v >= 0 else "#FF1744" for v in df["損益"]]
        fig = go.Figure(go.Bar(
            x=df["銘柄"], y=df["損益"], marker_color=colors,
            text=[f"¥{v:,.0f}" for v in df["損益"]], textposition="outside",
        ))
        fig.update_layout(yaxis_title="損益（円）", margin=dict(t=20, b=20, l=20, r=20), height=350)
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("銘柄別配分")
    sorted_df = df.sort_values("評価額", ascending=True)
    fig = px.bar(
        sorted_df, x="評価額", y="銘柄", orientation="h",
        text=[f"¥{v:,.0f}（{v/total_value*100:.1f}%）" for v in sorted_df["評価額"]],
        color="損益%", color_continuous_scale=["#FF1744", "#FFFFFF", "#00C853"],
        color_continuous_midpoint=0,
    )
    fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=400)
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    st.subheader("保有銘柄一覧")
    display_df = df[["銘柄", "コード", "セクター", "取得単価", "現在価格", "株数", "取得額", "評価額", "損益", "損益%"]].copy()
    display_df["取得単価"] = display_df["取得単価"].apply(lambda x: f"¥{x:,.0f}")
    display_df["現在価格"] = display_df["現在価格"].apply(lambda x: f"¥{x:,.0f}")
    display_df["取得額"] = display_df["取得額"].apply(lambda x: f"¥{x:,.0f}")
    display_df["評価額"] = display_df["評価額"].apply(lambda x: f"¥{x:,.0f}")
    display_df["損益"] = display_df["損益"].apply(lambda x: f"¥{x:+,.0f}")
    display_df["損益%"] = display_df["損益%"].apply(lambda x: f"{x:+.1f}%")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    return df


def render_stock_detail(pos, config):
    """個別銘柄の詳細ページ"""
    ticker = pos["ticker"]
    name = pos["name"]
    entry_price = pos["entry_price"]

    st.header(f"{name}（{ticker.replace('.T', '')}）")

    # --- チャート + 移動平均線 ---
    st.subheader("チャート")
    chart_period = st.radio("期間", ["3mo", "6mo", "1y", "2y"], index=2, horizontal=True)
    hist = fetch_history(ticker, period=chart_period)

    if not hist.empty:
        close = hist["Close"]

        sma25 = close.rolling(25).mean()
        sma75 = close.rolling(75).mean()
        sma200 = close.rolling(200).mean()

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=hist.index, y=close, mode="lines", name="株価", line=dict(color="#2196F3", width=2)))
        fig.add_trace(go.Scatter(x=hist.index, y=sma25, mode="lines", name="SMA25", line=dict(color="#FF9800", width=1, dash="dot")))
        fig.add_trace(go.Scatter(x=hist.index, y=sma75, mode="lines", name="SMA75", line=dict(color="#9C27B0", width=1, dash="dot")))
        if not sma200.dropna().empty:
            fig.add_trace(go.Scatter(x=hist.index, y=sma200, mode="lines", name="SMA200", line=dict(color="#607D8B", width=1, dash="dash")))
        fig.add_hline(y=entry_price, line_dash="dash", line_color="#F44336", annotation_text=f"取得単価 ¥{entry_price:,.0f}")

        # 購入日マーカー
        entry_date_str = pos.get("entry_date", "")
        if entry_date_str:
            entry_dt = pd.Timestamp(entry_date_str)
            if hist.index.tz is not None:
                entry_dt = entry_dt.tz_localize(hist.index.tz)
            if entry_dt >= hist.index[0]:
                fig.add_trace(go.Scatter(
                    x=[entry_dt], y=[entry_price], mode="markers+text",
                    marker=dict(size=12, color="#F44336", symbol="triangle-up"),
                    text=[f"購入 {entry_date_str}"], textposition="top center",
                    textfont=dict(color="#F44336", size=11),
                    name="購入日", showlegend=True,
                ))
                fig.add_vline(x=entry_dt, line_dash="dot", line_color="rgba(244,67,54,0.4)")

        # 出来高
        fig.add_trace(go.Bar(x=hist.index, y=hist["Volume"], name="出来高", yaxis="y2", marker_color="rgba(158,158,158,0.3)"))

        fig.update_layout(
            yaxis=dict(title="株価（円）", side="left"),
            yaxis2=dict(title="出来高", side="right", overlaying="y", showgrid=False, range=[0, hist["Volume"].max() * 4]),
            margin=dict(t=20, b=20, l=20, r=60), height=500,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig, use_container_width=True)

    # --- テクニカル指標 ---
    st.subheader("テクニカル分析")
    if not hist.empty and len(hist) > 14:
        close = hist["Close"]
        latest = float(close.iloc[-1])

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1])

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val = float(macd_line.iloc[-1])
        signal_val = float(signal_line.iloc[-1])

        sma25_val = float(sma25.iloc[-1]) if not np.isnan(sma25.iloc[-1]) else None
        sma75_val = float(sma75.iloc[-1]) if not np.isnan(sma75.iloc[-1]) else None

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("RSI(14)", f"{rsi_val:.1f}", "売られすぎ" if rsi_val < 30 else ("買われすぎ" if rsi_val > 70 else "適正"))

        if sma25_val and sma75_val:
            cross = "GC（上昇）" if sma25_val > sma75_val else "DC（下降）"
            c2.metric("SMAクロス", cross)

        c3.metric("MACD", f"{macd_val:.1f}", "買い優勢" if macd_val > signal_val else "売り優勢")
        c4.metric("現在価格", f"¥{latest:,.0f}", f"{(latest/entry_price-1)*100:+.1f}%")

        # RSIチャート
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=rsi.index, y=rsi, mode="lines", name="RSI", line=dict(color="#9C27B0")))
        fig_rsi.add_hline(y=70, line_dash="dash", line_color="#FF1744", annotation_text="買われすぎ")
        fig_rsi.add_hline(y=30, line_dash="dash", line_color="#00C853", annotation_text="売られすぎ")
        fig_rsi.update_layout(yaxis=dict(range=[0, 100]), margin=dict(t=20, b=20, l=20, r=20), height=250)
        st.plotly_chart(fig_rsi, use_container_width=True)

    st.divider()

    # --- ファンダメンタル ---
    st.subheader("ファンダメンタル分析")
    info = fetch_fundamentals(ticker)

    if info:
        f1, f2, f3, f4 = st.columns(4)

        per = info.get("trailingPE") or info.get("forwardPE")
        pbr = info.get("priceToBook")
        roe = info.get("returnOnEquity")
        div_yield = info.get("dividendYield")

        if per and per < 500:
            label = "割安" if per < 15 else ("割高" if per > 25 else "適正")
            f1.metric("PER", f"{per:.1f}倍", label)
        if pbr:
            label = "割安" if pbr < 1.0 else ("割高" if pbr > 3.0 else "適正")
            f2.metric("PBR", f"{pbr:.2f}倍", label)
        if roe:
            roe_pct = roe * 100
            label = "高収益" if roe_pct > 15 else ("低収益" if roe_pct < 5 else "平均的")
            f3.metric("ROE", f"{roe_pct:.1f}%", label)
        if div_yield:
            div_pct = div_yield if div_yield > 0.2 else div_yield * 100
            f4.metric("配当利回り", f"{div_pct:.2f}%")

        # 追加指標
        f5, f6, f7, f8 = st.columns(4)
        market_cap = info.get("marketCap")
        if market_cap:
            if market_cap >= 1e12:
                f5.metric("時価総額", f"¥{market_cap/1e12:.1f}兆")
            else:
                f5.metric("時価総額", f"¥{market_cap/1e8:.0f}億")

        rev_growth = info.get("revenueGrowth")
        if rev_growth:
            f6.metric("売上成長率", f"{rev_growth*100:+.1f}%")

        earn_growth = info.get("earningsGrowth")
        if earn_growth:
            f7.metric("利益成長率", f"{earn_growth*100:+.1f}%")

        profit_margin = info.get("profitMargins")
        if profit_margin:
            f8.metric("利益率", f"{profit_margin*100:.1f}%")

    st.divider()

    # --- 決算解釈 ---
    st.subheader("決算サマリー")
    income, balance = fetch_financials(ticker)

    if not income.empty:
        # 売上・利益の推移
        revenue_row = None
        profit_row = None
        for label in ["Total Revenue", "TotalRevenue"]:
            if label in income.index:
                revenue_row = income.loc[label]
                break
        for label in ["Net Income", "NetIncome"]:
            if label in income.index:
                profit_row = income.loc[label]
                break

        if revenue_row is not None and profit_row is not None:
            years = [str(c.year) if hasattr(c, 'year') else str(c) for c in revenue_row.index]
            rev_values = [float(v) / 1e9 for v in revenue_row.values]
            prof_values = [float(v) / 1e9 for v in profit_row.values]

            fig_fin = go.Figure()
            fig_fin.add_trace(go.Bar(x=years, y=rev_values, name="売上高（十億円）", marker_color="#2196F3"))
            fig_fin.add_trace(go.Bar(x=years, y=prof_values, name="純利益（十億円）", marker_color="#00C853"))
            fig_fin.update_layout(
                barmode="group", margin=dict(t=20, b=20, l=20, r=20), height=350,
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_fin, use_container_width=True)

            # 決算の解釈
            if len(rev_values) >= 2:
                rev_change = (rev_values[0] / rev_values[1] - 1) * 100 if rev_values[1] != 0 else 0
                prof_change = (prof_values[0] / prof_values[1] - 1) * 100 if prof_values[1] != 0 else 0

                interpretations = []

                if rev_change > 10:
                    interpretations.append(f"売上高は前年比 **+{rev_change:.1f}%** と大幅に成長しています。")
                elif rev_change > 0:
                    interpretations.append(f"売上高は前年比 **+{rev_change:.1f}%** と堅調に推移しています。")
                elif rev_change > -5:
                    interpretations.append(f"売上高は前年比 **{rev_change:.1f}%** とほぼ横ばいです。")
                else:
                    interpretations.append(f"売上高は前年比 **{rev_change:.1f}%** と減収です。")

                if prof_change > 20:
                    interpretations.append(f"純利益は前年比 **+{prof_change:.1f}%** と大幅増益。収益力が向上しています。")
                elif prof_change > 0:
                    interpretations.append(f"純利益は前年比 **+{prof_change:.1f}%** の増益。")
                elif prof_change > -10:
                    interpretations.append(f"純利益は前年比 **{prof_change:.1f}%** とやや減益。")
                else:
                    interpretations.append(f"純利益は前年比 **{prof_change:.1f}%** と大幅減益。業績悪化に注意が必要です。")

                # PER/PBRとの組み合わせ解釈
                if per and rev_change > 5 and per < 15:
                    interpretations.append("業績成長に対してPERが低めで、**割安感**があります。")
                elif per and rev_change < 0 and per > 25:
                    interpretations.append("業績悪化にもかかわらずPERが高く、**割高の可能性**があります。")

                if div_yield:
                    div_pct_val = div_yield if div_yield > 0.2 else div_yield * 100
                    if div_pct_val > 3.0:
                        interpretations.append(f"配当利回り {div_pct_val:.2f}% は高水準で、**インカム狙いとしても魅力的**です。")

                st.info("\n\n".join(interpretations))
        else:
            st.caption("決算データの取得に対応していない形式です")
    else:
        st.caption("決算データを取得できませんでした")


def render_manage(portfolio):
    """銘柄追加・削除"""
    st.subheader("銘柄の追加・削除")

    with st.expander("銘柄を追加"):
        col_a, col_b, col_c = st.columns(3)
        new_ticker = col_a.text_input("銘柄コード（例: 7203）")
        new_price = col_b.number_input("取得単価", min_value=1, value=1000)
        new_shares = col_c.number_input("株数", min_value=1, value=1)
        if st.button("追加"):
            t = new_ticker.strip()
            if not t.endswith(".T"):
                t = t + ".T"
            try:
                stock = yf.Ticker(t)
                info = stock.info
                name = info.get("shortName", t)
            except Exception:
                name = t
            portfolio.append({
                "ticker": t, "name": name,
                "entry_price": new_price, "shares": new_shares,
                "entry_date": pd.Timestamp.now().strftime("%Y-%m-%d"),
            })
            save_portfolio(portfolio)
            st.success(f"{name} を追加しました")
            st.rerun()

    with st.expander("銘柄を削除"):
        del_target = st.selectbox("削除する銘柄", [f"{p['name']}（{p['ticker'].replace('.T','')}）" for p in portfolio], key="del")
        if st.button("削除"):
            del_idx = [f"{p['name']}（{p['ticker'].replace('.T','')}）" for p in portfolio].index(del_target)
            removed = portfolio.pop(del_idx)
            save_portfolio(portfolio)
            st.success(f"{removed['name']} を削除しました")
            st.rerun()


def main():
    st.set_page_config(page_title="株式ポートフォリオ", page_icon="📈", layout="wide")

    portfolio = load_portfolio()
    config = load_config()

    if not portfolio:
        st.warning("保有銘柄が登録されていません")
        return

    # サイドバーで銘柄選択
    st.sidebar.title("📈 ポートフォリオ")
    pages = ["概要"] + [f"{p['name']}（{p['ticker'].replace('.T','')}）" for p in portfolio] + ["銘柄管理"]
    selection = st.sidebar.radio("ページ", pages)

    tickers = [p["ticker"] for p in portfolio]
    prices = fetch_prices(tickers)

    # サイドバーにサマリー表示
    total_cost = sum(p["entry_price"] * p["shares"] for p in portfolio)
    total_value = sum(prices.get(p["ticker"], p["entry_price"]) * p["shares"] for p in portfolio)
    total_pnl = total_value - total_cost
    st.sidebar.divider()
    st.sidebar.metric("評価額", f"¥{total_value:,.0f}")
    st.sidebar.metric("含み損益", f"¥{total_pnl:,.0f}", f"{(total_value/total_cost-1)*100:+.1f}%")

    if selection == "概要":
        st.title("📈 ポートフォリオ概要")
        render_overview(portfolio, prices)
    elif selection == "銘柄管理":
        st.title("設定")
        render_manage(portfolio)
    else:
        # 個別銘柄詳細
        idx = pages.index(selection) - 1  # "概要"の分を引く
        render_stock_detail(portfolio[idx], config)


if __name__ == "__main__":
    main()
