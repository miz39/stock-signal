"""
類似企業比較（Comps）エージェント
同セクター内での相対バリュエーションを分析する。
"""
import logging

import numpy as np

from data import fetch_financial_data
from nikkei225 import NIKKEI_225, get_sector

logger = logging.getLogger("signal")


def _get_sector_peers(ticker: str) -> list:
    """Get all tickers in the same sector."""
    target_sector = get_sector(ticker)
    peers = []
    for t in NIKKEI_225:
        if t != ticker and get_sector(t) == target_sector:
            peers.append(t)
    return peers


def _fetch_peer_data(peers: list, max_peers: int = 30) -> list:
    """Fetch financial data for peer companies."""
    peer_data = []
    for t in peers[:max_peers]:
        try:
            fin = fetch_financial_data(t)
            if fin.get("error"):
                continue
            per = fin.get("per")
            pbr = fin.get("pbr")
            roe = fin.get("roe")
            if per is not None or pbr is not None:
                name = NIKKEI_225.get(t, t)
                peer_data.append({
                    "ticker": t,
                    "name": name,
                    "per": per if per and 0 < per < 500 else None,
                    "pbr": pbr if pbr and pbr > 0 else None,
                    "roe": roe,
                    "market_cap": fin.get("market_cap"),
                })
        except Exception as e:
            logger.debug(f"Peer data fetch failed for {t}: {e}")
            continue
    return peer_data


def analyze(df, config, ticker="") -> dict:
    """
    類似企業比較分析を実行する。

    Returns:
        dict: score (-2~+2), confidence, reasons, metrics
    """
    if not ticker:
        return {
            "agent": "類似企業比較",
            "score": 0,
            "confidence": 0,
            "reasons": ["ティッカーが指定されていません"],
            "metrics": {},
        }

    # Fetch target company data
    try:
        target_fin = fetch_financial_data(ticker)
    except Exception as e:
        return {
            "agent": "類似企業比較",
            "score": 0,
            "confidence": 0,
            "reasons": [f"対象銘柄のデータ取得失敗: {e}"],
            "metrics": {},
        }

    if target_fin.get("error"):
        return {
            "agent": "類似企業比較",
            "score": 0,
            "confidence": 0,
            "reasons": [f"対象銘柄のデータ取得失敗: {target_fin['error']}"],
            "metrics": {},
        }

    target_per = target_fin.get("per")
    target_pbr = target_fin.get("pbr")
    sector = get_sector(ticker)

    # Fetch peer data
    peers = _get_sector_peers(ticker)
    peer_data = _fetch_peer_data(peers)

    if not peer_data:
        return {
            "agent": "類似企業比較",
            "score": 0,
            "confidence": 10,
            "reasons": [f"セクター「{sector}」の比較データが不足"],
            "metrics": {"sector": sector},
        }

    score = 0.0
    reasons = []
    metrics = {
        "sector": sector,
        "peer_count": len(peer_data),
    }

    # PER comparison
    peer_pers = [p["per"] for p in peer_data if p["per"] is not None]
    if peer_pers and target_per and 0 < target_per < 500:
        sector_per_median = float(np.median(peer_pers))
        sector_per_mean = float(np.mean(peer_pers))
        metrics["sector_per_median"] = round(sector_per_median, 1)
        metrics["sector_per_mean"] = round(sector_per_mean, 1)
        metrics["stock_per"] = round(target_per, 1)

        if sector_per_median > 0:
            per_discount = (target_per / sector_per_median - 1) * 100
            metrics["per_discount_pct"] = round(per_discount, 1)

            if per_discount < -30:
                score += 1.5
                reasons.append(f"PER {target_per:.1f} → セクター中央値{sector_per_median:.1f}対比 {per_discount:.1f}% 割安")
            elif per_discount < -10:
                score += 0.5
                reasons.append(f"PER {target_per:.1f} → セクター中央値{sector_per_median:.1f}対比 {per_discount:.1f}% やや割安")
            elif per_discount > 30:
                score -= 1.0
                reasons.append(f"PER {target_per:.1f} → セクター中央値{sector_per_median:.1f}対比 +{per_discount:.1f}% 割高")
            elif per_discount > 10:
                score -= 0.5
                reasons.append(f"PER {target_per:.1f} → セクター中央値{sector_per_median:.1f}対比 +{per_discount:.1f}% やや割高")
            else:
                reasons.append(f"PER {target_per:.1f} → セクター中央値{sector_per_median:.1f}と同水準")

    # PBR comparison
    peer_pbrs = [p["pbr"] for p in peer_data if p["pbr"] is not None]
    if peer_pbrs and target_pbr and target_pbr > 0:
        sector_pbr_median = float(np.median(peer_pbrs))
        metrics["sector_pbr_median"] = round(sector_pbr_median, 2)
        metrics["stock_pbr"] = round(target_pbr, 2)

        if sector_pbr_median > 0:
            pbr_discount = (target_pbr / sector_pbr_median - 1) * 100
            metrics["pbr_discount_pct"] = round(pbr_discount, 1)

            if pbr_discount < -30:
                score += 0.5
                reasons.append(f"PBR {target_pbr:.2f} → セクター中央値{sector_pbr_median:.2f}対比 {pbr_discount:.1f}% 割安")
            elif pbr_discount > 30:
                score -= 0.5
                reasons.append(f"PBR {target_pbr:.2f} → セクター中央値{sector_pbr_median:.2f}対比 +{pbr_discount:.1f}% 割高")

    # Top peers for reference
    top_peers = sorted(
        [p for p in peer_data if p["per"] is not None],
        key=lambda x: x["per"],
    )[:5]
    metrics["peers"] = [
        {"ticker": p["ticker"], "name": p["name"],
         "per": round(p["per"], 1) if p["per"] else None,
         "pbr": round(p["pbr"], 2) if p["pbr"] else None}
        for p in top_peers
    ]

    score = max(-2.0, min(2.0, score))

    # Confidence
    data_points = len(peer_pers) + len(peer_pbrs)
    confidence = min(100, int(data_points / 20 * 100))
    if not (target_per or target_pbr):
        confidence = max(confidence, 10)

    if not reasons:
        reasons.append(f"セクター「{sector}」内 {len(peer_data)} 社と比較")

    return {
        "agent": "類似企業比較",
        "score": round(score, 2),
        "confidence": confidence,
        "reasons": reasons,
        "metrics": metrics,
    }
