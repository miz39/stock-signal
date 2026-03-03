"""
センチメント分析エージェント
出来高変化、価格モメンタム、日経平均との相対強度で市場の勢いを判断する。
"""
import numpy as np
from data import fetch_stock_data


def analyze(df, config, ticker=""):
    """
    センチメント分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    close = df["Close"]
    volume = df["Volume"]

    score = 0.0
    reasons = []
    metrics = {}

    # --- 出来高分析 ---
    vol_sma20 = volume.rolling(20).mean()
    latest_vol = volume.iloc[-1]
    avg_vol = vol_sma20.iloc[-1]

    if not np.isnan(avg_vol) and avg_vol > 0:
        vol_ratio = latest_vol / avg_vol
        metrics["出来高比率"] = round(vol_ratio, 2)

        price_change = close.iloc[-1] - close.iloc[-2] if len(close) > 1 else 0

        if vol_ratio > 2.0 and price_change > 0:
            score += 0.5
            reasons.append(f"出来高急増（{vol_ratio:.1f}倍）+ 上昇 → 強い買い意欲")
        elif vol_ratio > 2.0 and price_change < 0:
            score -= 0.5
            reasons.append(f"出来高急増（{vol_ratio:.1f}倍）+ 下落 → 強い売り圧力")
        elif vol_ratio > 1.5 and price_change > 0:
            score += 0.2
            reasons.append(f"出来高やや増加（{vol_ratio:.1f}倍）+ 上昇")
        elif vol_ratio < 0.5:
            reasons.append(f"出来高低迷（{vol_ratio:.1f}倍）→ 市場の関心薄い")

    # --- 価格モメンタム ---
    if len(close) >= 20:
        mom_5d = (close.iloc[-1] / close.iloc[-5] - 1) * 100
        mom_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
        metrics["5日モメンタム"] = round(mom_5d, 2)
        metrics["20日モメンタム"] = round(mom_20d, 2)

        if mom_5d > 3:
            score += 0.3
            reasons.append(f"5日モメンタム +{mom_5d:.1f}% → 短期上昇")
        elif mom_5d < -3:
            score -= 0.3
            reasons.append(f"5日モメンタム {mom_5d:.1f}% → 短期下落")

        if mom_20d > 5:
            score += 0.2
            reasons.append(f"20日モメンタム +{mom_20d:.1f}% → 中期上昇")
        elif mom_20d < -5:
            score -= 0.2
            reasons.append(f"20日モメンタム {mom_20d:.1f}% → 中期下落")

    # --- 日経平均との相対強度 ---
    try:
        nikkei = fetch_stock_data("^N225", period="3mo")
        nikkei_close = nikkei["Close"]

        # 共通の日付で揃える
        common_dates = close.index.intersection(nikkei_close.index)
        if len(common_dates) >= 20:
            stock_ret = (close[common_dates].iloc[-1] / close[common_dates].iloc[-20] - 1) * 100
            nikkei_ret = (nikkei_close[common_dates].iloc[-1] / nikkei_close[common_dates].iloc[-20] - 1) * 100
            relative = stock_ret - nikkei_ret
            metrics["対日経20日"] = round(relative, 2)

            if relative > 3:
                score += 0.4
                reasons.append(f"日経平均を +{relative:.1f}%アウトパフォーム")
            elif relative < -3:
                score -= 0.4
                reasons.append(f"日経平均を {relative:.1f}%アンダーパフォーム")
            else:
                reasons.append(f"日経平均と同程度（差 {relative:+.1f}%）")
    except Exception:
        reasons.append("日経平均データ取得失敗（相対比較スキップ）")

    # --- 連続上昇/下落日数 ---
    if len(close) >= 5:
        streak = 0
        for i in range(-1, -min(len(close), 11), -1):
            if close.iloc[i] > close.iloc[i - 1]:
                if streak >= 0:
                    streak += 1
                else:
                    break
            elif close.iloc[i] < close.iloc[i - 1]:
                if streak <= 0:
                    streak -= 1
                else:
                    break
            else:
                break

        metrics["連続日数"] = streak
        if streak >= 5:
            score -= 0.2
            reasons.append(f"{streak}日連続上昇 → 過熱感に注意")
        elif streak <= -5:
            score += 0.2
            reasons.append(f"{abs(streak)}日連続下落 → 反発期待")

    if not reasons:
        reasons.append("特筆すべきセンチメント変化なし")

    score = max(-2.0, min(2.0, score))
    confidence = min(100, int(abs(score) / 2.0 * 100))

    return {
        "agent": "センチメント",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
