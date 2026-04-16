from notifier import format_daily_summary_mrkdwn


def _base_summary():
    return {
        "weekly_trades": 0,
        "total_pnl": 9165.0,
        "total_pnl_pct": 3.05,
        "balance": 300000,
    }


def _base_regime():
    return {"regime": "bull", "price": 39000.0}


def _base_actions(buy=0, sell=0, topup=0):
    return {"buy": buy, "sell": sell, "topup": topup}


class TestFormatDailySummaryMrkdwn:
    def test_header_includes_date(self):
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*日次サマリ 2026-04-16*" in text

    def test_empty_positions(self):
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*保有状況* (0銘柄)" in text
        assert "• なし" in text

    def test_position_line_format(self):
        positions = [{
            "ticker": "9502.T",
            "name": "中部電力",
            "entry_price": 2600.0,
            "current_price": 2720.0,
            "shares": 10,
            "stop_price": 2701.0,
            "pnl_pct": 4.6,
        }]
        text = format_daily_summary_mrkdwn(
            positions=positions, summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*保有状況* (1銘柄)" in text
        assert "中部電力 (9502)" in text
        assert "¥2,720" in text
        assert "+4.6%" in text
        assert "Stop ¥2,701" in text

    def test_position_loss_sign(self):
        positions = [{
            "ticker": "4911.T",
            "name": "資生堂",
            "entry_price": 3365.0,
            "current_price": 3130.0,
            "shares": 5,
            "stop_price": 3097.0,
            "pnl_pct": -7.0,
        }]
        text = format_daily_summary_mrkdwn(
            positions=positions, summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "資生堂 (4911)" in text
        assert "-7.0%" in text

    def test_pnl_pct_computed_when_missing(self):
        """pnl_pct 未指定時は entry_price/current_price から算出。"""
        positions = [{
            "ticker": "1111.T",
            "name": "テスト",
            "entry_price": 1000.0,
            "current_price": 1100.0,
            "shares": 1,
            "stop_price": 900.0,
        }]
        text = format_daily_summary_mrkdwn(
            positions=positions, summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "+10.0%" in text

    def test_actions_line(self):
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime=_base_regime(),
            actions=_base_actions(buy=2, sell=1, topup=3),
            today="2026-04-16",
        )
        assert "*本日のアクション*" in text
        assert "BUY: 2件" in text
        assert "SELL: 1件" in text
        assert "Topup: 3件" in text

    def test_weekly_performance_positive(self):
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*週次パフォーマンス*" in text
        assert "0トレード" in text
        assert "¥+9,165" in text
        assert "(+3.05%)" in text

    def test_weekly_performance_negative(self):
        summary = {
            "weekly_trades": 2,
            "total_pnl": -5000.0,
            "total_pnl_pct": -1.5,
            "balance": 300000,
        }
        text = format_daily_summary_mrkdwn(
            positions=[], summary=summary, cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "2トレード" in text
        assert "¥-5,000" in text
        assert "(-1.50%)" in text

    def test_market_regime_with_price(self):
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime={"regime": "bull", "price": 39000.0},
            actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*市場レジーム*: bull" in text
        assert "日経225 ¥39,000" in text
        assert "現金: ¥290,000" in text

    def test_market_regime_without_price(self):
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime={"regime": "neutral", "price": 0},
            actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*市場レジーム*: neutral" in text
        assert "日経225" not in text
        assert "現金: ¥290,000" in text

    def test_today_default_is_jst_now(self):
        """today=None のときは実行時のJST日付が入る。"""
        text = format_daily_summary_mrkdwn(
            positions=[], summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
        )
        # Should contain a YYYY-MM-DD format
        import re
        assert re.search(r"\*日次サマリ \d{4}-\d{2}-\d{2}\*", text)

    def test_multiple_positions_order(self):
        positions = [
            {"ticker": "1111.T", "name": "A", "entry_price": 1000.0,
             "current_price": 1050.0, "shares": 1, "stop_price": 920.0, "pnl_pct": 5.0},
            {"ticker": "2222.T", "name": "B", "entry_price": 2000.0,
             "current_price": 1900.0, "shares": 2, "stop_price": 1840.0, "pnl_pct": -5.0},
        ]
        text = format_daily_summary_mrkdwn(
            positions=positions, summary=_base_summary(), cash=290000,
            market_regime=_base_regime(), actions=_base_actions(),
            today="2026-04-16",
        )
        assert "*保有状況* (2銘柄)" in text
        # Order preserved
        a_idx = text.index("A (1111)")
        b_idx = text.index("B (2222)")
        assert a_idx < b_idx
