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
