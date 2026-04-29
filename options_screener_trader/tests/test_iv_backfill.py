"""
tests/test_iv_backfill.py
=========================
Unit tests for iv_backfill.py — focusing on the Black-Scholes IV engine
and date/contract helper functions.  No API calls are made.

Run from project root:
    py -3 -m unittest tests.test_iv_backfill -v
"""

import builtins
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

# ── Patch config before importing iv_backfill (which imports iv_tracker) ──────
_FAKE_CFG = json.dumps({
    "api_key":    "TESTKEY",
    "api_secret": "TESTSECRET",
    "base_url":   "https://paper-api.alpaca.markets/v2",
})
_orig_open = builtins.open


def _patched_open(path, *args, **kwargs):
    if "alpaca_config.json" in str(path):
        import io
        return io.StringIO(_FAKE_CFG)
    return _orig_open(path, *args, **kwargs)


builtins.open = _patched_open
sys.path.insert(0, str(Path(__file__).parent.parent / "options_loop"))
import iv_backfill   # noqa: E402
builtins.open = _orig_open


# ══════════════════════════════════════════════════════════════════════════════
#  Normal CDF
# ══════════════════════════════════════════════════════════════════════════════

class TestNormCdf(unittest.TestCase):

    def test_at_zero_is_half(self):
        self.assertAlmostEqual(iv_backfill._norm_cdf(0.0), 0.5, places=10)

    def test_positive_tail(self):
        """N(1.96) ≈ 0.975 (standard stats table value)."""
        self.assertAlmostEqual(iv_backfill._norm_cdf(1.96), 0.975, places=2)

    def test_negative_tail(self):
        """N(-1.96) ≈ 0.025 — symmetry of the normal distribution."""
        self.assertAlmostEqual(iv_backfill._norm_cdf(-1.96), 0.025, places=2)

    def test_symmetry(self):
        for x in [0.5, 1.0, 2.0, 3.0]:
            self.assertAlmostEqual(
                iv_backfill._norm_cdf(x) + iv_backfill._norm_cdf(-x),
                1.0, places=10
            )

    def test_bounds(self):
        self.assertGreater(iv_backfill._norm_cdf(10.0), 0.9999)
        self.assertLess(iv_backfill._norm_cdf(-10.0),   0.0001)


# ══════════════════════════════════════════════════════════════════════════════
#  Black-Scholes call price
# ══════════════════════════════════════════════════════════════════════════════

class TestBsCallPrice(unittest.TestCase):
    """
    Reference values computed via the Black-Scholes formula directly.
    All tests use S=100, r=0.05 and vary K, T, σ.
    """

    def test_atm_approximation(self):
        """ATM call ≈ 0.4 × S × σ × √T  (Brenner-Subrahmanyam approximation).
        Approximation is most accurate for short-dated options (T ≈ 0.25).
        """
        S, K, T, r, sigma = 100.0, 100.0, 0.25, 0.05, 0.20
        price  = iv_backfill.bs_call_price(S, K, T, r, sigma)
        approx = 0.4 * S * sigma * T ** 0.5
        self.assertAlmostEqual(price, approx, delta=1.0)   # within $1.00

    def test_deep_itm_approaches_intrinsic(self):
        """Deep ITM call ≈ S − K·e^(−rT)."""
        S, K, T, r, sigma = 200.0, 100.0, 1.0, 0.05, 0.20
        price    = iv_backfill.bs_call_price(S, K, T, r, sigma)
        expected = S - K * __import__('math').exp(-r * T)
        self.assertAlmostEqual(price, expected, delta=0.5)

    def test_never_below_intrinsic(self):
        """Call price ≥ max(0, S − K) for various inputs."""
        for S, K, T, sigma in [
            (100, 95,  0.5, 0.25),
            (100, 110, 0.5, 0.20),
            (50,  50,  1.0, 0.30),
            (200, 150, 0.1, 0.40),
        ]:
            price     = iv_backfill.bs_call_price(S, K, T, 0.05, sigma)
            intrinsic = max(0.0, S - K)
            self.assertGreaterEqual(price, intrinsic - 1e-8,
                                    f"Below intrinsic: S={S} K={K}")

    def test_zero_time_returns_intrinsic(self):
        price = iv_backfill.bs_call_price(100.0, 95.0, 0.0, 0.05, 0.30)
        self.assertAlmostEqual(price, max(0.0, 100.0 - 95.0), places=4)

    def test_increases_with_volatility(self):
        """Higher σ → higher call price (monotonic)."""
        prices = [
            iv_backfill.bs_call_price(100.0, 100.0, 0.5, 0.05, sigma)
            for sigma in [0.10, 0.20, 0.30, 0.50, 0.80]
        ]
        self.assertEqual(prices, sorted(prices))

    def test_call_price_known_value(self):
        """
        Known BS value: S=100, K=100, T=1, r=0.05, σ=0.20 → ≈ 10.45.
        Verified against standard BS calculators.
        """
        price = iv_backfill.bs_call_price(100.0, 100.0, 1.0, 0.05, 0.20)
        self.assertAlmostEqual(price, 10.45, delta=0.05)


# ══════════════════════════════════════════════════════════════════════════════
#  Implied volatility (Newton-Raphson inversion)
# ══════════════════════════════════════════════════════════════════════════════

class TestImpliedVolatility(unittest.TestCase):

    def _roundtrip(self, S, K, T, sigma_true, r=0.05):
        """Compute BS price then invert — should recover sigma_true."""
        market_price = iv_backfill.bs_call_price(S, K, T, r, sigma_true)
        recovered    = iv_backfill.implied_volatility(market_price, S, K, T, r)
        return recovered, sigma_true

    def test_roundtrip_low_vol(self):
        recovered, true_vol = self._roundtrip(100.0, 100.0, 0.5, 0.15)
        self.assertIsNotNone(recovered)
        self.assertAlmostEqual(recovered, true_vol, places=4)

    def test_roundtrip_medium_vol(self):
        recovered, true_vol = self._roundtrip(100.0, 100.0, 1.0, 0.30)
        self.assertIsNotNone(recovered)
        self.assertAlmostEqual(recovered, true_vol, places=4)

    def test_roundtrip_high_vol(self):
        recovered, true_vol = self._roundtrip(100.0, 100.0, 0.25, 0.80)
        self.assertIsNotNone(recovered)
        self.assertAlmostEqual(recovered, true_vol, places=3)

    def test_roundtrip_otm(self):
        """OTM call: strike above current price."""
        recovered, true_vol = self._roundtrip(100.0, 110.0, 0.5, 0.25)
        self.assertIsNotNone(recovered)
        self.assertAlmostEqual(recovered, true_vol, places=4)

    def test_roundtrip_itm(self):
        """ITM call: strike below current price."""
        recovered, true_vol = self._roundtrip(100.0, 90.0, 0.5, 0.25)
        self.assertIsNotNone(recovered)
        self.assertAlmostEqual(recovered, true_vol, places=4)

    def test_various_sigma_levels(self):
        """Roundtrip for a range of volatility levels."""
        for sigma in [0.10, 0.20, 0.30, 0.50, 1.00, 1.50]:
            recovered, true_vol = self._roundtrip(150.0, 155.0, 0.25, sigma)
            self.assertIsNotNone(recovered,
                                 f"IV inversion returned None for sigma={sigma}")
            self.assertAlmostEqual(recovered, true_vol, places=3,
                                   msg=f"Roundtrip failed for sigma={sigma}")

    def test_zero_time_returns_none(self):
        self.assertIsNone(iv_backfill.implied_volatility(5.0, 100.0, 100.0, 0.0))

    def test_zero_price_returns_none(self):
        self.assertIsNone(iv_backfill.implied_volatility(0.0, 100.0, 100.0, 0.5))

    def test_negative_price_returns_none(self):
        self.assertIsNone(iv_backfill.implied_volatility(-1.0, 100.0, 100.0, 0.5))

    def test_below_intrinsic_returns_none(self):
        """A price below intrinsic value is not a valid option price."""
        intrinsic = 10.0   # S=110, K=100
        below = intrinsic - 1.0
        self.assertIsNone(iv_backfill.implied_volatility(below, 110.0, 100.0, 0.5))

    def test_zero_stock_price_returns_none(self):
        self.assertIsNone(iv_backfill.implied_volatility(2.0, 0.0, 100.0, 0.5))

    def test_result_within_valid_range(self):
        """All successful IV results must be in (0, MAX_IV]."""
        for S, K, T, sigma in [
            (100, 100, 0.5, 0.20),
            (200, 220, 0.25, 0.45),
            (50,  48,  0.75, 0.60),
        ]:
            price = iv_backfill.bs_call_price(S, K, T, 0.05, sigma)
            result = iv_backfill.implied_volatility(price, S, K, T)
            self.assertIsNotNone(result)
            self.assertGreater(result, 0.0)
            self.assertLessEqual(result, iv_backfill.MAX_IV)


# ══════════════════════════════════════════════════════════════════════════════
#  Trading-day helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestGetTradingDays(unittest.TestCase):

    def test_no_weekends(self):
        days = iv_backfill.get_trading_days(30)
        for d in days:
            self.assertLess(d.weekday(), 5,
                            f"{d} is a weekend day (weekday={d.weekday()})")

    def test_count_reasonable(self):
        """270 calendar days contain roughly 190–196 weekdays."""
        days = iv_backfill.get_trading_days(270)
        self.assertGreater(len(days), 180)
        self.assertLess(len(days), 200)

    def test_sorted_ascending(self):
        days = iv_backfill.get_trading_days(60)
        self.assertEqual(days, sorted(days))

    def test_ends_at_today_or_before(self):
        days = iv_backfill.get_trading_days(30)
        self.assertLessEqual(days[-1], date.today())


# ══════════════════════════════════════════════════════════════════════════════
#  _hist_target_expiry
# ══════════════════════════════════════════════════════════════════════════════

class TestHistTargetExpiry(unittest.TestCase):

    def test_returns_a_friday(self):
        result = iv_backfill._hist_target_expiry(date(2025, 6, 15))
        self.assertEqual(result.weekday(), 4,
                         f"{result} is not a Friday")

    def test_dte_near_35(self):
        """Result DTE should be within 21 days of target (35 DTE)."""
        as_of  = date(2025, 6, 15)
        result = iv_backfill._hist_target_expiry(as_of)
        dte    = (result - as_of).days
        self.assertGreaterEqual(dte, 14)
        self.assertLessEqual(abs(dte - 35), 21)

    def test_is_in_future(self):
        as_of  = date(2025, 9, 1)
        result = iv_backfill._hist_target_expiry(as_of)
        self.assertGreater(result, as_of)

    def test_is_third_friday_of_month(self):
        result = iv_backfill._hist_target_expiry(date(2025, 10, 1))
        fridays = sum(
            1 for d in range(1, result.day + 1)
            if date(result.year, result.month, d).weekday() == 4
        )
        self.assertEqual(fridays, 3)

    def test_year_boundary(self):
        """Works correctly when target expiry falls in the next year."""
        result = iv_backfill._hist_target_expiry(date(2025, 12, 1))
        self.assertIsNotNone(result)
        self.assertGreater(result, date(2025, 12, 1))


# ══════════════════════════════════════════════════════════════════════════════
#  build_date_contract_map
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildDateContractMap(unittest.TestCase):

    def _equity_history(self, sym="AAPL", price=270.0, n_days=5):
        start = date(2025, 6, 2)
        return {
            sym: {
                (start + timedelta(i)).strftime("%Y-%m-%d"): price
                for i in range(n_days)
                if (start + timedelta(i)).weekday() < 5
            }
        }

    def _trading_days(self, n=5):
        start = date(2025, 6, 2)
        return [
            start + timedelta(i)
            for i in range(n)
            if (start + timedelta(i)).weekday() < 5
        ]

    def test_contract_occ_format(self):
        import re
        eq_hist = self._equity_history()
        days    = self._trading_days()
        dcm, _  = iv_backfill.build_date_contract_map(eq_hist, days)
        pattern = re.compile(r'^[A-Z]{1,5}\d{6}C\d{8}$')
        for contract in dcm.values():
            self.assertRegex(contract, pattern)

    def test_low_price_skipped(self):
        """Stocks below $5 are excluded (options not viable)."""
        eq_hist = self._equity_history(price=3.0)
        days    = self._trading_days()
        dcm, _  = iv_backfill.build_date_contract_map(eq_hist, days)
        self.assertEqual(len(dcm), 0)

    def test_missing_date_skipped(self):
        """Days with no equity price entry are skipped."""
        eq_hist = {"AAPL": {"2025-06-02": 270.0}}   # only 1 day
        days    = [date(2025, 6, 2), date(2025, 6, 3)]
        dcm, _  = iv_backfill.build_date_contract_map(eq_hist, days)
        # Only 1 entry (2025-06-03 has no price)
        self.assertEqual(len(dcm), 1)

    def test_contract_symbols_subset_of_map_values(self):
        eq_hist = self._equity_history()
        days    = self._trading_days()
        dcm, contract_set = iv_backfill.build_date_contract_map(eq_hist, days)
        self.assertEqual(contract_set, set(dcm.values()))


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
