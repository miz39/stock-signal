import pytest
from risk import calculate_stop_loss, calculate_position_size


class TestCalculateStopLoss:
    def test_default_8pct(self):
        assert calculate_stop_loss(1000.0) == 920.0

    def test_custom_pct(self):
        assert calculate_stop_loss(1000.0, 0.05) == 950.0
        assert calculate_stop_loss(1000.0, 0.12) == 880.0

    def test_rounding(self):
        assert calculate_stop_loss(1234.5, 0.08) == 1135.7


class TestCalculatePositionSize:
    def test_allocation_is_binding(self):
        # risk_based = 300000 * 0.02 / (1000 * 0.08) = 75 shares
        # alloc_based = 300000 * 0.10 / 1000 = 30 shares  ← binding
        shares = calculate_position_size(300000, 0.02, 1000.0, 920.0, 1, 0.10)
        assert shares == 30

    def test_risk_is_binding(self):
        # risk_based = 300000 * 0.02 / (1000 - 990) = 600 shares
        # alloc_based = 300000 * 0.50 / 1000 = 150 shares
        # Actually risk: 6000 / 10 = 600; alloc: 150000 / 1000 = 150 → alloc binding
        # Use tighter stop to make risk binding:
        # risk_based = 300000 * 0.01 / (1000 - 920) = 3000 / 80 = 37
        # alloc_based = 300000 * 0.20 / 1000 = 60
        shares = calculate_position_size(300000, 0.01, 1000.0, 920.0, 1, 0.20)
        assert shares == 37  # risk is binding

    def test_minimum_one_share(self):
        # Very expensive stock, low balance
        shares = calculate_position_size(10000, 0.02, 50000.0, 46000.0, 1, 0.10)
        assert shares == 1

    def test_zero_loss_per_share(self):
        # stop == entry → loss_per_share = 0
        shares = calculate_position_size(300000, 0.02, 1000.0, 1000.0, 1, 0.10)
        assert shares == 1  # falls back to unit

    def test_negative_loss_per_share(self):
        # stop > entry (shouldn't happen but handle gracefully)
        shares = calculate_position_size(300000, 0.02, 1000.0, 1100.0, 1, 0.10)
        assert shares == 1

    def test_unit_respected(self):
        shares = calculate_position_size(300000, 0.02, 1000.0, 920.0, 100, 0.10)
        # alloc_based = 30000 / 1000 = 30 → floor to 100-unit = 0 → max(0, 100) = 100
        assert shares >= 100
