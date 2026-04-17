import json
import os
import tempfile
import pytest
from unittest.mock import patch

import portfolio


@pytest.fixture(autouse=True)
def temp_trades_file(tmp_path):
    """Use a temporary trades file for each test."""
    trades_file = str(tmp_path / "trades_test.json")
    with patch.object(portfolio, "TRADES_FILE", trades_file):
        yield trades_file


class TestRecordEntry:
    def test_basic_entry(self, temp_trades_file):
        trade = portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        assert trade["ticker"] == "1234.T"
        assert trade["entry_price"] == 1000.0
        assert trade["shares"] == 10
        assert trade["original_shares"] == 10
        assert trade["status"] == "open"
        assert trade["stop_price"] == 920.0  # 1000 * 0.92

    def test_custom_stop_pct(self, temp_trades_file):
        trade = portfolio.record_entry("1234.T", 1000.0, 10, stop_pct=0.12)
        assert trade["stop_price"] == 880.0  # 1000 * 0.88

    def test_aggressive_stop(self, temp_trades_file):
        trade = portfolio.record_entry("1234.T", 1000.0, 10, stop_pct=0.05)
        assert trade["stop_price"] == 950.0

    def test_persists_to_file(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        with open(temp_trades_file) as f:
            trades = json.load(f)
        assert len(trades) == 1
        assert trades[0]["ticker"] == "1234.T"

    def test_signal_meta_stored(self, temp_trades_file):
        meta = {"rsi": 58.3, "adx": 28.1, "sma_slope": 1.2,
                "ichimoku_bullish": True, "market_regime": "bull"}
        trade = portfolio.record_entry(
            "1234.T", 1000.0, 10, entry_date="2026-01-01", signal_meta=meta
        )
        assert trade["entry_meta"] == meta
        with open(temp_trades_file) as f:
            trades = json.load(f)
        assert trades[0]["entry_meta"]["adx"] == 28.1
        assert trades[0]["entry_meta"]["market_regime"] == "bull"

    def test_no_meta_key_when_not_provided(self, temp_trades_file):
        trade = portfolio.record_entry("1234.T", 1000.0, 10)
        assert "entry_meta" not in trade


class TestMonthlyPerformance:
    def test_empty(self, temp_trades_file):
        assert portfolio.get_monthly_performance() == []

    def test_multiple_months(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 1100.0, exit_date="2026-01-10")
        portfolio.record_entry("5678.T", 2000.0, 5, entry_date="2026-01-15")
        portfolio.record_exit("5678.T", 1900.0, exit_date="2026-02-05")

        result = portfolio.get_monthly_performance()
        assert len(result) == 2
        assert result[0]["month"] == "2026-01"
        assert result[0]["trades"] == 1
        assert result[0]["wins"] == 1
        assert result[0]["win_rate"] == 100.0
        assert result[0]["pnl"] == 1000.0
        assert result[1]["month"] == "2026-02"
        assert result[1]["wins"] == 0
        assert result[1]["pnl"] == -500.0


class TestRecordExit:
    def test_basic_exit(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        trade = portfolio.record_exit("1234.T", 1100.0, exit_date="2026-01-10")
        assert trade["status"] == "closed"
        assert trade["pnl"] == 1000.0  # (1100-1000) * 10

    def test_loss(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        trade = portfolio.record_exit("1234.T", 900.0)
        assert trade["pnl"] == -1000.0

    def test_nonexistent_ticker(self, temp_trades_file):
        result = portfolio.record_exit("9999.T", 500.0)
        assert result is None


class TestRecordTopup:
    def test_basic_topup(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        result = portfolio.record_topup("1234.T", 1100.0, 5, stop_pct=0.08)
        assert result is not None
        assert result["shares"] == 15
        # Weighted avg: (1000*10 + 1100*5) / 15 = 15500/15 ≈ 1033.3
        assert result["entry_price"] == pytest.approx(1033.3, abs=0.1)
        assert result["original_shares"] == 15

    def test_topup_stop_increases(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        # Initial stop: 920.0
        result = portfolio.record_topup("1234.T", 1100.0, 5, stop_pct=0.08)
        # New avg ≈ 1033.3, new stop ≈ 1033.3 * 0.92 ≈ 950.6
        assert result["stop_price"] >= 920.0  # Stop should not decrease

    def test_topup_nonexistent_ticker(self, temp_trades_file):
        result = portfolio.record_topup("9999.T", 1000.0, 5)
        assert result is None

    def test_topup_preserves_partial_exit_original_shares(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        # Simulate partial exit having happened
        trades = portfolio._load_trades()
        trades[0]["partial_exit_done"] = True
        trades[0]["original_shares"] = 10
        portfolio._save_trades(trades)
        # Topup should NOT update original_shares when partial_exit_done
        result = portfolio.record_topup("1234.T", 1100.0, 5)
        assert result["original_shares"] == 10  # Unchanged


class TestGetCashBalance:
    def test_initial_balance(self, temp_trades_file):
        cash = portfolio.get_cash_balance(300000)
        assert cash == 300000

    def test_with_open_position(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        cash = portfolio.get_cash_balance(300000)
        assert cash == 290000.0  # 300000 - 1000*10

    def test_with_closed_profit(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 1100.0)
        cash = portfolio.get_cash_balance(300000)
        assert cash == 301000.0  # 300000 + (1100-1000)*10

    def test_with_closed_loss(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 900.0)
        cash = portfolio.get_cash_balance(300000)
        assert cash == 299000.0  # 300000 + (900-1000)*10


class TestConsecutiveLossTickers:
    def test_single_loss_not_blocked(self, temp_trades_file):
        """1回の損切りではブロックされない（デフォルト閾値2）"""
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 900.0, exit_date="2026-01-10")
        blocked = portfolio.get_consecutive_loss_tickers(2)
        assert "1234.T" not in blocked

    def test_two_consecutive_losses_blocked(self, temp_trades_file):
        """2回連続損切りでブロックされる"""
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 900.0, exit_date="2026-01-10")
        portfolio.record_entry("1234.T", 950.0, 10, entry_date="2026-01-15")
        portfolio.record_exit("1234.T", 850.0, exit_date="2026-01-25")
        blocked = portfolio.get_consecutive_loss_tickers(2)
        assert "1234.T" in blocked

    def test_loss_win_loss_not_blocked(self, temp_trades_file):
        """負け→勝ち→負けは連続1回なのでブロックされない"""
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 900.0, exit_date="2026-01-10")
        portfolio.record_entry("1234.T", 950.0, 10, entry_date="2026-01-15")
        portfolio.record_exit("1234.T", 1050.0, exit_date="2026-01-25")
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-02-01")
        portfolio.record_exit("1234.T", 900.0, exit_date="2026-02-10")
        blocked = portfolio.get_consecutive_loss_tickers(2)
        assert "1234.T" not in blocked

    def test_different_tickers_independent(self, temp_trades_file):
        """異なる銘柄は独立にカウントされる"""
        # Ticker A: 2 consecutive losses
        portfolio.record_entry("1111.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 900.0, exit_date="2026-01-10")
        portfolio.record_entry("1111.T", 950.0, 10, entry_date="2026-01-15")
        portfolio.record_exit("1111.T", 850.0, exit_date="2026-01-25")
        # Ticker B: 1 loss only
        portfolio.record_entry("2222.T", 500.0, 20, entry_date="2026-01-01")
        portfolio.record_exit("2222.T", 450.0, exit_date="2026-01-10")
        blocked = portfolio.get_consecutive_loss_tickers(2)
        assert "1111.T" in blocked
        assert "2222.T" not in blocked

    def test_win_after_losses_resets(self, temp_trades_file):
        """連敗後に勝ちが入るとリセットされる"""
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1234.T", 900.0, exit_date="2026-01-10")
        portfolio.record_entry("1234.T", 950.0, 10, entry_date="2026-01-15")
        portfolio.record_exit("1234.T", 850.0, exit_date="2026-01-25")
        # Now a win
        portfolio.record_entry("1234.T", 800.0, 10, entry_date="2026-02-01")
        portfolio.record_exit("1234.T", 1000.0, exit_date="2026-02-10")
        blocked = portfolio.get_consecutive_loss_tickers(2)
        assert "1234.T" not in blocked


class TestReadinessMetrics:
    def test_empty_trades(self, temp_trades_file):
        """No trades → all criteria fail except max_dd (0% ≤ 10%)."""
        r = portfolio.get_readiness_metrics(initial_balance=300000)
        assert r["total_count"] == 5
        # Only max_dd passes (0 dd)
        assert r["passed_count"] == 1
        assert r["ready"] is False
        assert r["score_pct"] == 20.0
        names = [c["name"] for c in r["criteria"]]
        assert "trade_count" in names
        assert "profit_factor" in names
        assert "max_dd_pct" in names
        assert "consecutive_profitable_months" in names
        assert "win_rate" in names

    def test_trade_count_threshold(self, temp_trades_file):
        """99 trades fails, 100 trades passes the count criterion."""
        # Create 99 small winning trades
        for i in range(99):
            portfolio.record_entry(f"{1000+i}.T", 1000.0, 1,
                                   entry_date=f"2026-01-01")
            portfolio.record_exit(f"{1000+i}.T", 1100.0,
                                  exit_date=f"2026-01-02")
        r = portfolio.get_readiness_metrics()
        tc = next(c for c in r["criteria"] if c["name"] == "trade_count")
        assert tc["passed"] is False
        assert tc["actual"] == 99

    def test_profit_factor_pass(self, temp_trades_file):
        """PF = 2.0 (gross_win 2000, gross_loss 1000) → pass."""
        portfolio.record_entry("1111.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 1200.0, exit_date="2026-01-05")  # +2000
        portfolio.record_entry("2222.T", 1000.0, 10, entry_date="2026-01-10")
        portfolio.record_exit("2222.T", 900.0, exit_date="2026-01-15")   # -1000
        r = portfolio.get_readiness_metrics()
        pf = next(c for c in r["criteria"] if c["name"] == "profit_factor")
        assert pf["passed"] is True
        assert pf["actual"] == pytest.approx(2.0, abs=0.01)

    def test_profit_factor_fail(self, temp_trades_file):
        """PF = 1.0 → fail (needs ≥ 1.5)."""
        portfolio.record_entry("1111.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 1100.0, exit_date="2026-01-05")  # +1000
        portfolio.record_entry("2222.T", 1000.0, 10, entry_date="2026-01-10")
        portfolio.record_exit("2222.T", 900.0, exit_date="2026-01-15")   # -1000
        r = portfolio.get_readiness_metrics()
        pf = next(c for c in r["criteria"] if c["name"] == "profit_factor")
        assert pf["passed"] is False

    def test_profit_factor_no_losses(self, temp_trades_file):
        """All wins → PF = infinity → display ∞ and passes."""
        portfolio.record_entry("1111.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 1100.0, exit_date="2026-01-05")
        r = portfolio.get_readiness_metrics()
        pf = next(c for c in r["criteria"] if c["name"] == "profit_factor")
        assert pf["passed"] is True
        assert "∞" in pf["display"]

    def test_win_rate_boundary(self, temp_trades_file):
        """Win rate 45% boundary passes, 44% fails."""
        # 45/100 wins = 45%
        for i in range(45):
            portfolio.record_entry(f"{2000+i}.T", 1000.0, 1, entry_date="2026-01-01")
            portfolio.record_exit(f"{2000+i}.T", 1100.0, exit_date="2026-01-02")
        for i in range(55):
            portfolio.record_entry(f"{3000+i}.T", 1000.0, 1, entry_date="2026-01-01")
            portfolio.record_exit(f"{3000+i}.T", 900.0, exit_date="2026-01-02")
        r = portfolio.get_readiness_metrics()
        wr = next(c for c in r["criteria"] if c["name"] == "win_rate")
        assert wr["actual"] == 45.0
        assert wr["passed"] is True

    def test_consecutive_profitable_months(self, temp_trades_file):
        """3 consecutive profitable months at the tail → pass."""
        # Jan: profit, Feb: profit, Mar: profit
        portfolio.record_entry("1111.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 1100.0, exit_date="2026-01-15")
        portfolio.record_entry("2222.T", 1000.0, 10, entry_date="2026-02-01")
        portfolio.record_exit("2222.T", 1100.0, exit_date="2026-02-15")
        portfolio.record_entry("3333.T", 1000.0, 10, entry_date="2026-03-01")
        portfolio.record_exit("3333.T", 1100.0, exit_date="2026-03-15")
        r = portfolio.get_readiness_metrics()
        cm = next(c for c in r["criteria"] if c["name"] == "consecutive_profitable_months")
        assert cm["actual"] == 3
        assert cm["passed"] is True

    def test_consecutive_profitable_months_breaks_on_loss(self, temp_trades_file):
        """Recent month with net loss → streak resets."""
        portfolio.record_entry("1111.T", 1000.0, 10, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 1100.0, exit_date="2026-01-15")
        portfolio.record_entry("2222.T", 1000.0, 10, entry_date="2026-02-01")
        portfolio.record_exit("2222.T", 1100.0, exit_date="2026-02-15")
        # March: loss
        portfolio.record_entry("3333.T", 1000.0, 10, entry_date="2026-03-01")
        portfolio.record_exit("3333.T", 800.0, exit_date="2026-03-15")
        r = portfolio.get_readiness_metrics()
        cm = next(c for c in r["criteria"] if c["name"] == "consecutive_profitable_months")
        assert cm["actual"] == 0  # March loss breaks streak at tail

    def test_max_dd_pct_calculation(self, temp_trades_file):
        """10%超のDDは失敗、10%以下は通過。"""
        # Big loss creates a 10% dd from initial balance 300000
        # Trade loss of 30000 → peak 300000 → trough 270000 → 10% dd
        portfolio.record_entry("1111.T", 10000.0, 3, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 0.0, exit_date="2026-01-15")  # loss = -30000
        r = portfolio.get_readiness_metrics(initial_balance=300000)
        dd = next(c for c in r["criteria"] if c["name"] == "max_dd_pct")
        assert dd["actual"] == pytest.approx(10.0, abs=0.1)
        # 10% is the threshold (≤ 10), so passed
        assert dd["passed"] is True

    def test_max_dd_pct_exceeds_threshold(self, temp_trades_file):
        """15%のDDは失敗。"""
        portfolio.record_entry("1111.T", 15000.0, 3, entry_date="2026-01-01")
        portfolio.record_exit("1111.T", 0.0, exit_date="2026-01-15")  # -45000
        r = portfolio.get_readiness_metrics(initial_balance=300000)
        dd = next(c for c in r["criteria"] if c["name"] == "max_dd_pct")
        assert dd["actual"] == pytest.approx(15.0, abs=0.1)
        assert dd["passed"] is False

    def test_fully_ready(self, temp_trades_file):
        """全基準クリアで ready=True."""
        # 100 wins, no losses → trade_count=100, pf=inf, win_rate=100%,
        # dd=0%, streak=100 (all wins in 1 month... need >=3 months)
        for month in ["2026-01", "2026-02", "2026-03"]:
            for i in range(34):  # 34*3 = 102 trades
                t = f"{4000+i}.T"
                portfolio.record_entry(t, 1000.0, 1, entry_date=f"{month}-01")
                portfolio.record_exit(t, 1100.0, exit_date=f"{month}-15")
        r = portfolio.get_readiness_metrics()
        assert r["ready"] is True
        assert r["score_pct"] == 100.0
        assert r["passed_count"] == 5


class TestTradeAnalysis:
    def _write(self, path, trades):
        with open(path, "w") as f:
            json.dump(trades, f)

    def test_empty_trades(self, temp_trades_file):
        self._write(temp_trades_file, [])
        r = portfolio.get_trade_analysis()
        assert r["summary"]["trade_count"] == 0
        assert r["summary"]["win_rate"] == 0.0
        assert r["exit_reasons"] == []
        assert r["holding_buckets"] == []
        assert r["sectors"] == []
        assert r["tickers"] == []

    def test_summary_basic(self, temp_trades_file):
        self._write(temp_trades_file, [
            {"ticker": "1111.T", "status": "closed", "pnl": 1000.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-05",
             "entry_price": 1000.0, "exit_price": 1100.0, "stop_price": 920.0,
             "shares": 10},
            {"ticker": "2222.T", "status": "closed", "pnl": -500.0,
             "entry_date": "2026-01-02", "exit_date": "2026-01-04",
             "entry_price": 2000.0, "exit_price": 1900.0, "stop_price": 1900.0,
             "shares": 5},
        ])
        r = portfolio.get_trade_analysis()
        s = r["summary"]
        assert s["trade_count"] == 2
        assert s["win_rate"] == 50.0
        assert s["avg_win"] == 1000.0
        assert s["avg_loss"] == -500.0
        assert s["best_trade"] == 1000.0
        assert s["worst_trade"] == -500.0
        # Expectancy = 0.5 * 1000 + 0.5 * -500 = 250
        assert s["expectancy"] == 250.0

    def test_open_trades_excluded(self, temp_trades_file):
        self._write(temp_trades_file, [
            {"ticker": "1111.T", "status": "open",
             "entry_date": "2026-01-01", "entry_price": 1000.0,
             "stop_price": 920.0, "shares": 10},
            {"ticker": "2222.T", "status": "closed", "pnl": 100.0,
             "entry_date": "2026-01-02", "exit_date": "2026-01-04",
             "entry_price": 2000.0, "exit_price": 2050.0, "stop_price": 1840.0,
             "shares": 1},
        ])
        r = portfolio.get_trade_analysis()
        assert r["summary"]["trade_count"] == 1

    def test_exit_reason_classification(self, temp_trades_file):
        # Stop loss: pnl < 0 and exit near stop
        # Trailing stop (profit): pnl > 0 and exit near stop
        # Full profit: exit_price >= entry * 1.14
        self._write(temp_trades_file, [
            # Stop loss
            {"ticker": "1.T", "status": "closed", "pnl": -800.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-03",
             "entry_price": 1000.0, "exit_price": 920.0, "stop_price": 920.0,
             "shares": 10},
            # Full profit (+15% range)
            {"ticker": "2.T", "status": "closed", "pnl": 1500.0,
             "entry_date": "2026-01-02", "exit_date": "2026-01-10",
             "entry_price": 1000.0, "exit_price": 1150.0, "stop_price": 920.0,
             "shares": 10},
            # Trailing stop with profit (exit ≈ stop, pnl > 0)
            {"ticker": "3.T", "status": "closed", "pnl": 600.0,
             "entry_date": "2026-01-03", "exit_date": "2026-01-08",
             "entry_price": 1000.0, "exit_price": 1060.0, "stop_price": 1060.0,
             "shares": 10},
        ])
        r = portfolio.get_trade_analysis()
        reasons = {row["reason"]: row for row in r["exit_reasons"]}
        assert "ストップロス" in reasons
        assert "全部利確 (+15%)" in reasons
        assert "トレーリングストップ (利)" in reasons
        assert reasons["ストップロス"]["count"] == 1
        assert reasons["全部利確 (+15%)"]["count"] == 1

    def test_holding_buckets(self, temp_trades_file):
        # 2 days (0-3), 5 days (4-7), 20 days (15-30)
        self._write(temp_trades_file, [
            {"ticker": "1.T", "status": "closed", "pnl": 100.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-03",
             "entry_price": 1000.0, "exit_price": 1010.0, "stop_price": 920.0,
             "shares": 10},
            {"ticker": "2.T", "status": "closed", "pnl": -50.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-06",
             "entry_price": 1000.0, "exit_price": 995.0, "stop_price": 990.0,
             "shares": 10},
            {"ticker": "3.T", "status": "closed", "pnl": 200.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-21",
             "entry_price": 1000.0, "exit_price": 1020.0, "stop_price": 920.0,
             "shares": 10},
        ])
        r = portfolio.get_trade_analysis()
        buckets = {b["bucket"]: b for b in r["holding_buckets"]}
        assert "0-3日" in buckets and buckets["0-3日"]["count"] == 1
        assert "4-7日" in buckets and buckets["4-7日"]["count"] == 1
        assert "15-30日" in buckets and buckets["15-30日"]["count"] == 1

    def test_holding_buckets_ordered(self, temp_trades_file):
        self._write(temp_trades_file, [
            {"ticker": "1.T", "status": "closed", "pnl": 100.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-21",
             "entry_price": 1000.0, "exit_price": 1010.0, "stop_price": 920.0,
             "shares": 10},
            {"ticker": "2.T", "status": "closed", "pnl": 100.0,
             "entry_date": "2026-01-01", "exit_date": "2026-01-03",
             "entry_price": 1000.0, "exit_price": 1010.0, "stop_price": 920.0,
             "shares": 10},
        ])
        r = portfolio.get_trade_analysis()
        order = [b["bucket"] for b in r["holding_buckets"]]
        # 0-3日 must come before 15-30日 regardless of trade order
        assert order.index("0-3日") < order.index("15-30日")

    def test_ticker_aggregation_top10(self, temp_trades_file):
        # 12 distinct tickers, only top 10 returned
        trades = []
        for i in range(12):
            trades.append({
                "ticker": f"{1000+i}.T", "status": "closed", "pnl": float(i),
                "entry_date": "2026-01-01", "exit_date": "2026-01-05",
                "entry_price": 1000.0, "exit_price": 1010.0, "stop_price": 920.0,
                "shares": 1,
            })
        self._write(temp_trades_file, trades)
        r = portfolio.get_trade_analysis()
        assert len(r["tickers"]) == 10


class TestPartialExit:
    def test_partial_exit(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        result = portfolio.record_partial_exit("1234.T", 5, 1100.0)
        assert result is not None
        assert result["status"] == "closed"
        assert result["pnl"] == 500.0  # (1100-1000) * 5

        # Check remaining position
        positions = portfolio.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["shares"] == 5
        assert positions[0]["partial_exit_done"] is True

    def test_full_exit_via_partial(self, temp_trades_file):
        portfolio.record_entry("1234.T", 1000.0, 10, entry_date="2026-01-01")
        result = portfolio.record_partial_exit("1234.T", 10, 1100.0)
        # Should trigger full exit
        positions = portfolio.get_open_positions()
        assert len(positions) == 0
