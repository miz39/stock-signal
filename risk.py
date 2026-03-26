import math


def calculate_stop_loss(entry_price: float, pct: float = 0.08) -> float:
    """損切り価格を計算する。"""
    return round(entry_price * (1 - pct), 1)


def calculate_position_size(
    balance: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    unit: int = 1,
    max_allocation: float = 0.15,
) -> int:
    """
    リスクと損切り幅、および1銘柄あたりの配分上限から推奨株数を計算する。

    Args:
        balance: 口座残高
        risk_pct: 1トレードあたりのリスク割合 (例: 0.02)
        entry_price: エントリー価格
        stop_price: 損切り価格
        unit: 売買単位（S株なら1）
        max_allocation: 1銘柄あたりの資産配分上限 (例: 0.15 = 15%)

    Returns:
        推奨株数（最低1株）
    """
    # リスクベースの株数
    risk_amount = balance * risk_pct
    loss_per_share = entry_price - stop_price

    if loss_per_share <= 0:
        shares_by_risk = unit
    else:
        shares_by_risk = math.floor(risk_amount / loss_per_share)

    # 配分上限ベースの株数
    max_cost = balance * max_allocation
    shares_by_alloc = math.floor(max_cost / entry_price)

    # 両方の制約のうち、小さい方を採用
    shares = min(shares_by_risk, shares_by_alloc)
    return max(shares, unit)
