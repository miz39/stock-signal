"""
東証（東京証券取引所）休場日判定。

年1回、JPXカレンダー（https://www.jpx.co.jp/corporate/about-jpx/calendar/）を参照して更新する。
"""

from datetime import date

# 2026年の東証休場日（祝日・年末年始）
# Source: https://nikkeiyosoku.com/stock/holiday/2026/
TSE_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # 元日
    date(2026, 1, 2),   # 年始休業
    date(2026, 1, 3),   # 年始休業（土曜）
    date(2026, 1, 12),  # 成人の日
    date(2026, 2, 11),  # 建国記念の日
    date(2026, 2, 23),  # 天皇誕生日
    date(2026, 3, 20),  # 春分の日
    date(2026, 4, 29),  # 昭和の日
    date(2026, 5, 3),   # 憲法記念日
    date(2026, 5, 4),   # みどりの日
    date(2026, 5, 5),   # こどもの日
    date(2026, 5, 6),   # 振替休日
    date(2026, 7, 20),  # 海の日
    date(2026, 8, 11),  # 山の日
    date(2026, 9, 21),  # 敬老の日
    date(2026, 9, 22),  # 国民の休日
    date(2026, 9, 23),  # 秋分の日
    date(2026, 10, 12), # スポーツの日
    date(2026, 11, 3),  # 文化の日
    date(2026, 11, 23), # 勤労感謝の日
    date(2026, 12, 31), # 大納会
}


def is_market_open(target: date = None) -> bool:
    """東証が開場しているかどうかを返す。土日・祝日はFalse。"""
    if target is None:
        target = date.today()
    # Saturday=5, Sunday=6
    if target.weekday() >= 5:
        return False
    if target in TSE_HOLIDAYS_2026:
        return False
    return True
