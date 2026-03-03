"""
リスク分析エージェント
ボラティリティ、ベータ値、最大ドローダウンでリスク水準を評価する。
"""
import numpy as np
from data import fetch_stock_data


def analyze(df, config, ticker=""):
    """
    リスク分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    score = 0.0
    reasons = []
    metrics = {}

    # --- ATR（平均真幅）→ ボラティリティ ---
    if len(df) >= 15:
        tr = np.maximum(
            high.iloc[1:].values - low.iloc[1:].values,
            np.maximum(
                np.abs(high.iloc[1:].values - close.iloc[:-1].values),
                np.abs(low.iloc[1:].values - close.iloc[:-1].values),
            ),
        )
        atr_14 = np.mean(tr[-14:])
        atr_pct = (atr_14 / close.iloc[-1]) * 100
        metrics["ATR(14)"] = round(atr_14, 1)
        metrics["ATR%"] = round(atr_pct, 2)

        if atr_pct > 4:
            score -= 0.5
            reasons.append(f"高ボラティリティ（ATR {atr_pct:.1f}%）→ リスク大")
        elif atr_pct > 2.5:
            score -= 0.2
            reasons.append(f"やや高ボラティリティ（ATR {atr_pct:.1f}%）")
        elif atr_pct < 1.0:
            score += 0.2
            reasons.append(f"低ボラティリティ（ATR {atr_pct:.1f}%）→ 安定")
        else:
            reasons.append(f"ボラティリティ適正（ATR {atr_pct:.1f}%）")

    # --- ベータ値（対日経平均） ---
    try:
        nikkei = fetch_stock_data("^N225", period="1y")
        nikkei_close = nikkei["Close"]

        common_dates = close.index.intersection(nikkei_close.index)
        if len(common_dates) >= 60:
            stock_returns = close[common_dates].pct_change().dropna()
            nikkei_returns = nikkei_close[common_dates].pct_change().dropna()

            # 共通インデックスで揃える
            common = stock_returns.index.intersection(nikkei_returns.index)
            sr = stock_returns[common]
            nr = nikkei_returns[common]

            cov = np.cov(sr, nr)[0][1]
            var_market = np.var(nr)
            beta = cov / var_market if var_market > 0 else 1.0
            metrics["ベータ"] = round(beta, 2)

            if beta > 1.5:
                score -= 0.4
                reasons.append(f"高ベータ（{beta:.2f}）→ 市場より大きく動く")
            elif beta > 1.1:
                score -= 0.1
                reasons.append(f"やや高ベータ（{beta:.2f}）")
            elif beta < 0.5:
                score += 0.3
                reasons.append(f"低ベータ（{beta:.2f}）→ ディフェンシブ")
            elif beta < 0.9:
                score += 0.1
                reasons.append(f"やや低ベータ（{beta:.2f}）")
            else:
                reasons.append(f"ベータ適正（{beta:.2f}）")
    except Exception:
        reasons.append("ベータ値の計算失敗（日経データ取得エラー）")

    # --- 最大ドローダウン（直近3ヶ月） ---
    if len(close) >= 60:
        recent = close.iloc[-60:]
        running_max = recent.cummax()
        drawdown = (recent / running_max - 1) * 100
        max_dd = float(drawdown.min())
        metrics["最大DD(3M)"] = round(max_dd, 1)

        if max_dd < -20:
            score -= 0.5
            reasons.append(f"直近3ヶ月で大幅下落（{max_dd:.1f}%）→ リスク高")
        elif max_dd < -10:
            score -= 0.2
            reasons.append(f"直近3ヶ月の下落（{max_dd:.1f}%）→ 注意")
        elif max_dd > -5:
            score += 0.2
            reasons.append(f"直近3ヶ月の下落軽微（{max_dd:.1f}%）→ 安定")

    # --- 価格の安定性（直近20日の標準偏差） ---
    if len(close) >= 20:
        daily_returns = close.pct_change().dropna()
        vol_20d = float(daily_returns.iloc[-20:].std() * 100)
        metrics["20日ボラ%"] = round(vol_20d, 2)

        if vol_20d > 3:
            score -= 0.3
            reasons.append(f"直近20日の変動大（日次σ {vol_20d:.2f}%）")
        elif vol_20d < 1:
            score += 0.2
            reasons.append(f"直近20日の変動小（日次σ {vol_20d:.2f}%）")

    if not reasons:
        reasons.append("リスク評価に必要なデータが不足")

    score = max(-2.0, min(2.0, score))
    confidence = min(100, int(abs(score) / 2.0 * 100))

    return {
        "agent": "リスク",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
