"""
マルチエージェント バックテスト
過去データで4エージェントの総合判断をシミュレーションする。
各時点で未来のデータは一切使わない（look-ahead bias排除）。
"""

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from strategy import calculate_sma, calculate_rsi


def _macd(prices, fast=12, slow=26, signal=9):
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(prices, period=20, num_std=2):
    sma = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower


def _technical_score(close, sma_s, sma_l, sma_t, rsi_val, macd_hist, macd_hist_prev, bb_upper, bb_lower, price):
    """テクニカルスコアを計算（その時点のデータのみ）"""
    score = 0.0
    if sma_s > sma_l:
        score += 0.5
    else:
        score -= 0.5
    if not np.isnan(sma_t) and price > sma_t:
        score += 0.3
    elif not np.isnan(sma_t):
        score -= 0.3
    if rsi_val < 30:
        score += 0.5
    elif rsi_val < 50:
        score += 0.2
    elif rsi_val > 75:
        score -= 0.5
    elif rsi_val > 70:
        score -= 0.3
    if macd_hist > 0 and macd_hist_prev <= 0:
        score += 0.4
    elif macd_hist < 0 and macd_hist_prev >= 0:
        score -= 0.4
    elif macd_hist > 0:
        score += 0.1
    else:
        score -= 0.1
    if price <= bb_lower:
        score += 0.3
    elif price >= bb_upper:
        score -= 0.3
    return max(-2.0, min(2.0, score))


def _sentiment_score(close, volume, nikkei_close, idx, lookback=20):
    """センチメントスコアを計算（その時点まで）"""
    score = 0.0
    if idx < lookback:
        return 0.0

    # 出来高比率
    vol_avg = volume.iloc[idx - lookback:idx].mean()
    if vol_avg > 0:
        vol_ratio = volume.iloc[idx] / vol_avg
        price_change = close.iloc[idx] - close.iloc[idx - 1]
        if vol_ratio > 2.0 and price_change > 0:
            score += 0.5
        elif vol_ratio > 2.0 and price_change < 0:
            score -= 0.5
        elif vol_ratio > 1.5 and price_change > 0:
            score += 0.2

    # 5日モメンタム
    if idx >= 5:
        mom_5d = (close.iloc[idx] / close.iloc[idx - 5] - 1) * 100
        if mom_5d > 3:
            score += 0.3
        elif mom_5d < -3:
            score -= 0.3

    # 20日モメンタム
    mom_20d = (close.iloc[idx] / close.iloc[idx - lookback] - 1) * 100
    if mom_20d > 5:
        score += 0.2
    elif mom_20d < -5:
        score -= 0.2

    # 対日経平均
    if nikkei_close is not None and idx < len(nikkei_close) and idx >= lookback:
        try:
            stock_ret = (close.iloc[idx] / close.iloc[idx - lookback] - 1) * 100
            nk_ret = (nikkei_close.iloc[idx] / nikkei_close.iloc[idx - lookback] - 1) * 100
            relative = stock_ret - nk_ret
            if relative > 3:
                score += 0.4
            elif relative < -3:
                score -= 0.4
        except Exception:
            pass

    return max(-2.0, min(2.0, score))


def _risk_score(close, high, low, nikkei_close, idx):
    """リスクスコアを計算（その時点まで）"""
    score = 0.0
    if idx < 60:
        return 0.0

    # ATR
    if idx >= 15:
        tr_vals = []
        for j in range(idx - 14, idx):
            tr = max(
                high.iloc[j] - low.iloc[j],
                abs(high.iloc[j] - close.iloc[j - 1]),
                abs(low.iloc[j] - close.iloc[j - 1]),
            )
            tr_vals.append(tr)
        atr = np.mean(tr_vals)
        atr_pct = (atr / close.iloc[idx]) * 100
        if atr_pct > 4:
            score -= 0.5
        elif atr_pct > 2.5:
            score -= 0.2
        elif atr_pct < 1.0:
            score += 0.2

    # ベータ
    if nikkei_close is not None and idx >= 60:
        try:
            stock_ret = close.iloc[idx - 60:idx].pct_change().dropna()
            nk_ret = nikkei_close.iloc[idx - 60:idx].pct_change().dropna()
            common_len = min(len(stock_ret), len(nk_ret))
            if common_len > 20:
                sr = stock_ret.iloc[-common_len:].values
                nr = nk_ret.iloc[-common_len:].values
                cov = np.cov(sr, nr)[0][1]
                var_m = np.var(nr)
                beta = cov / var_m if var_m > 0 else 1.0
                if beta > 1.5:
                    score -= 0.4
                elif beta > 1.1:
                    score -= 0.1
                elif beta < 0.5:
                    score += 0.3
                elif beta < 0.9:
                    score += 0.1
        except Exception:
            pass

    # 最大DD（直近60日）
    recent = close.iloc[idx - 60:idx + 1]
    running_max = recent.cummax()
    dd = (recent / running_max - 1) * 100
    max_dd = float(dd.min())
    if max_dd < -20:
        score -= 0.5
    elif max_dd < -10:
        score -= 0.2
    elif max_dd > -5:
        score += 0.2

    return max(-2.0, min(2.0, score))


def _combined_score(tech, sent, risk):
    """
    3エージェントの重み付き合算（ファンダメンタルは過去データ取得不可のため除外）
    テクニカル: 45%, センチメント: 30%, リスク: 25%
    """
    score = tech * 0.45 + sent * 0.30 + risk * 0.25
    return max(-2.0, min(2.0, round(score, 3)))


def download_all_data(tickers, period="4y"):
    """全銘柄のデータを一括ダウンロード"""
    print(f"  データダウンロード中: {len(tickers)}銘柄...")
    data = {}
    # バッチダウンロード
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            df = yf.download(batch, period=period, progress=False, group_by="ticker", threads=True)
            for ticker in batch:
                try:
                    if len(batch) == 1:
                        ticker_df = df
                    else:
                        ticker_df = df[ticker]
                    if ticker_df is not None and not ticker_df.empty and len(ticker_df) > 200:
                        data[ticker] = ticker_df.dropna()
                except Exception:
                    pass
        except Exception as e:
            print(f"  バッチ {i}-{i+batch_size} エラー: {e}")
        print(f"  ダウンロード: {min(i + batch_size, len(tickers))}/{len(tickers)}")

    print(f"  有効データ: {len(data)}銘柄")
    return data


def run_multi_backtest(tickers, config, period="3y", initial_balance=100000):
    """
    マルチエージェントによるポートフォリオシミュレーション。

    各時点で:
    1. 全銘柄をスコアリング（テクニカル/センチメント/リスク）
    2. スコア上位をBUY候補
    3. 保有銘柄のスコアが悪化したらSELL
    4. ポートフォリオを更新

    Returns:
        dict: summary, trades, equity_curve, monthly_returns
    """
    account = config["account"]
    strat = config["strategy"]
    max_positions = account["max_positions"]

    # 4年分DL（3年シミュレーション + 1年ウォームアップ）
    all_data = download_all_data(tickers, period="5y" if period == "3y" else "6y")

    # 日経平均もDL
    try:
        nikkei_df = yf.download("^N225", period="5y", progress=False)
        nikkei_close = nikkei_df["Close"].squeeze() if not nikkei_df.empty else None
    except Exception:
        nikkei_close = None

    if not all_data:
        return {"error": "データが取得できませんでした"}

    # 全銘柄のインジケーターを事前計算
    print("  インジケーター計算中...")
    indicators = {}
    for ticker, df in all_data.items():
        close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
        high = df["High"].squeeze() if isinstance(df["High"], pd.DataFrame) else df["High"]
        low = df["Low"].squeeze() if isinstance(df["Low"], pd.DataFrame) else df["Low"]
        volume = df["Volume"].squeeze() if isinstance(df["Volume"], pd.DataFrame) else df["Volume"]

        sma_short = calculate_sma(close, strat["sma_short"])
        sma_long = calculate_sma(close, strat["sma_long"])
        sma_trend = calculate_sma(close, strat["sma_trend"])
        rsi = calculate_rsi(close, strat["rsi_period"])
        _, _, macd_hist = _macd(close)
        bb_upper, _, bb_lower = _bollinger(close)

        indicators[ticker] = {
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "sma_short": sma_short,
            "sma_long": sma_long,
            "sma_trend": sma_trend,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
        }

    # シミュレーション期間を決定
    # 最初の銘柄から共通日付を取得
    sample_ticker = list(indicators.keys())[0]
    all_dates = indicators[sample_ticker]["close"].index

    # ウォームアップ後のシミュレーション開始（SMA200 + α = 250日目から）
    years = int(period.replace("y", ""))
    sim_days = years * 252
    start_idx = max(250, len(all_dates) - sim_days)

    # 週1回（毎週月曜）にスキャン・判断する（毎日だとノイズが多い）
    check_interval = 5

    balance = initial_balance
    positions = {}  # {ticker: {price, shares, date, stop_price, high_price}}
    trades = []
    equity_curve = []
    trailing_stop_pct = 0.05  # 5% トレーリングストップ

    print(f"  シミュレーション開始: {all_dates[start_idx].strftime('%Y-%m-%d')} 〜 {all_dates[-1].strftime('%Y-%m-%d')}")

    for idx in range(start_idx, len(all_dates)):
        date = all_dates[idx]

        # 保有銘柄の現在価値計算 + トレーリングストップ更新
        portfolio_value = balance
        for ticker, pos in list(positions.items()):
            if ticker in indicators:
                ind = indicators[ticker]
                if idx < len(ind["close"]):
                    current_price = float(ind["close"].iloc[idx])
                    portfolio_value += current_price * pos["shares"]

                    # トレーリングストップ: 高値更新でストップも引き上げ
                    if current_price > pos["high_price"]:
                        pos["high_price"] = current_price
                        pos["stop_price"] = current_price * (1 - trailing_stop_pct)

                    # 損切りチェック（毎日）
                    if current_price <= pos["stop_price"]:
                        pnl = (current_price - pos["price"]) * pos["shares"]
                        balance += current_price * pos["shares"]
                        pnl_pct = round((current_price / pos["price"] - 1) * 100, 2)
                        reason = "トレーリングストップ" if pnl >= 0 else "損切り（トレーリング）"
                        trades.append({
                            "ticker": ticker,
                            "entry_date": pos["date"].strftime("%Y-%m-%d"),
                            "exit_date": date.strftime("%Y-%m-%d"),
                            "entry_price": round(pos["price"], 1),
                            "exit_price": round(current_price, 1),
                            "shares": pos["shares"],
                            "pnl": round(pnl, 1),
                            "pnl_pct": pnl_pct,
                            "reason": reason,
                            "days_held": (date - pos["date"]).days,
                        })
                        del positions[ticker]

        equity_curve.append({"date": date, "equity": portfolio_value})

        # 週次チェック（判断日）
        if (idx - start_idx) % check_interval != 0:
            continue

        # 全銘柄スコアリング
        scores = {}
        for ticker, ind in indicators.items():
            if idx >= len(ind["close"]):
                continue
            try:
                price = float(ind["close"].iloc[idx])
                sma_s = float(ind["sma_short"].iloc[idx])
                sma_l = float(ind["sma_long"].iloc[idx])
                sma_t = float(ind["sma_trend"].iloc[idx])
                rsi_val = float(ind["rsi"].iloc[idx])
                mh = float(ind["macd_hist"].iloc[idx])
                mh_prev = float(ind["macd_hist"].iloc[idx - 1]) if idx > 0 else 0
                bb_u = float(ind["bb_upper"].iloc[idx])
                bb_l = float(ind["bb_lower"].iloc[idx])

                if np.isnan(sma_s) or np.isnan(sma_l) or np.isnan(rsi_val):
                    continue

                # テクニカルスコア
                tech = _technical_score(
                    ind["close"], sma_s, sma_l, sma_t, rsi_val,
                    mh, mh_prev, bb_u, bb_l, price
                )

                # センチメントスコア（日経平均のインデックス位置を合わせる）
                nk_aligned = None
                if nikkei_close is not None:
                    common = ind["close"].index.intersection(nikkei_close.index)
                    if len(common) > 60:
                        nk_aligned = nikkei_close.reindex(ind["close"].index).ffill()

                sent = _sentiment_score(ind["close"], ind["volume"], nk_aligned, idx)

                # リスクスコア
                risk = _risk_score(ind["close"], ind["high"], ind["low"], nk_aligned, idx)

                combined = _combined_score(tech, sent, risk)
                scores[ticker] = {"score": combined, "price": price, "rsi": rsi_val}

            except Exception:
                continue

        # 保有銘柄の売却判断（スコアが-0.3以下）
        for ticker in list(positions.keys()):
            if ticker in scores and scores[ticker]["score"] < -0.3:
                ind = indicators[ticker]
                current_price = float(ind["close"].iloc[idx])
                pos = positions[ticker]
                pnl = (current_price - pos["price"]) * pos["shares"]
                balance += current_price * pos["shares"]
                trades.append({
                    "ticker": ticker,
                    "entry_date": pos["date"].strftime("%Y-%m-%d"),
                    "exit_date": date.strftime("%Y-%m-%d"),
                    "entry_price": round(pos["price"], 1),
                    "exit_price": round(current_price, 1),
                    "shares": pos["shares"],
                    "pnl": round(pnl, 1),
                    "pnl_pct": round((current_price / pos["price"] - 1) * 100, 2),
                    "reason": f"スコア悪化（{scores[ticker]['score']:+.2f}）",
                    "days_held": (date - pos["date"]).days,
                })
                del positions[ticker]

        # 買い判断（スコア上位、空きポジションがある場合）
        if len(positions) < max_positions:
            # スコア上位でまだ保有していない銘柄
            candidates = [
                (t, s) for t, s in scores.items()
                if t not in positions and s["score"] >= 0.5 and s["rsi"] < 70
            ]
            candidates.sort(key=lambda x: x[1]["score"], reverse=True)

            for ticker, sig in candidates:
                if len(positions) >= max_positions:
                    break

                price = sig["price"]
                stop_price = price * 0.95
                risk_amount = balance * account["risk_per_trade"]
                loss_per_share = price - stop_price
                if loss_per_share <= 0:
                    continue
                shares = max(1, int(risk_amount / loss_per_share))
                cost = price * shares

                if cost <= balance * 0.95:  # 余裕を持たせる
                    positions[ticker] = {
                        "price": price,
                        "shares": shares,
                        "date": date,
                        "stop_price": stop_price,
                        "high_price": price,  # トレーリングストップ用の高値
                    }
                    balance -= cost

    # 未決済ポジションをクローズ
    for ticker, pos in positions.items():
        if ticker in indicators:
            ind = indicators[ticker]
            price = float(ind["close"].iloc[-1])
            pnl = (price - pos["price"]) * pos["shares"]
            balance += price * pos["shares"]
            trades.append({
                "ticker": ticker,
                "entry_date": pos["date"].strftime("%Y-%m-%d"),
                "exit_date": all_dates[-1].strftime("%Y-%m-%d"),
                "entry_price": round(pos["price"], 1),
                "exit_price": round(price, 1),
                "shares": pos["shares"],
                "pnl": round(pnl, 1),
                "pnl_pct": round((price / pos["price"] - 1) * 100, 2),
                "reason": "期間終了",
                "days_held": (all_dates[-1] - pos["date"]).days,
            })

    # サマリー
    final_equity = balance
    total_return = ((final_equity / initial_balance) - 1) * 100

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["pnl_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl_pct"] for t in losses]) if losses else 0
    avg_days = np.mean([t["days_held"] for t in trades]) if trades else 0

    # 最大DD
    equities = [e["equity"] for e in equity_curve]
    peak = equities[0]
    max_dd = 0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (eq / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # 月次リターン
    monthly = {}
    for e in equity_curve:
        key = e["date"].strftime("%Y-%m")
        monthly[key] = e["equity"]
    monthly_returns = []
    months = sorted(monthly.keys())
    for i in range(1, len(months)):
        ret = (monthly[months[i]] / monthly[months[i - 1]] - 1) * 100
        monthly_returns.append({"month": months[i], "return": round(ret, 2)})

    # 年次リターン
    yearly = {}
    for e in equity_curve:
        key = e["date"].strftime("%Y")
        yearly[key] = e["equity"]
    yearly_list = sorted(yearly.keys())
    annual_returns = []
    prev_eq = initial_balance
    for y in yearly_list:
        ret = (yearly[y] / prev_eq - 1) * 100
        annual_returns.append({"year": y, "return": round(ret, 2)})
        prev_eq = yearly[y]

    # ベンチマーク（日経225 buy-and-hold）
    benchmark_return_pct = None
    if nikkei_close is not None and len(nikkei_close) > 0:
        try:
            sim_start = all_dates[start_idx]
            # シミュレーション開始日以降の日経データを取得
            nk_sim = nikkei_close[nikkei_close.index >= sim_start]
            if len(nk_sim) >= 2:
                nk_start = float(nk_sim.iloc[0])
                nk_end = float(nk_sim.iloc[-1])
                benchmark_return_pct = round((nk_end / nk_start - 1) * 100, 2)
        except Exception:
            pass

    alpha = round(total_return - benchmark_return_pct, 2) if benchmark_return_pct is not None else None

    summary = {
        "initial_balance": initial_balance,
        "final_balance": round(final_equity, 0),
        "total_return_pct": round(total_return, 2),
        "benchmark_return_pct": benchmark_return_pct,
        "alpha": alpha,
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "avg_days_held": round(avg_days, 1),
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor": round(profit_factor, 2),
        "annual_returns": annual_returns,
        "stocks_analyzed": len(all_data),
    }

    return {
        "summary": summary,
        "trades": trades,
        "equity_curve": equity_curve,
        "monthly_returns": monthly_returns,
    }
