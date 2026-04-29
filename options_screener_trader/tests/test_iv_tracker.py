"""
tests/test_iv_tracker.py
========================
Unit tests for iv_tracker.py pure-computation functions.
No API calls are made — all network-dependent functions are excluded.

Run from project root:
    py -3 -m unittest tests.test_iv_tracker -v
"""

import builtins
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import mock_open, patch

# ── Patch config file open before importing iv_tracker ────────────────────────
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
import iv_tracker   # noqa: E402
builtins.open = _orig_open  # restore immediately


# ══════════════════════════════════════════════════════════════════════════════

class TestStandardIncrement(unittest.TestCase):

    def test_below_5(self):
        self.assertEqual(iv_tracker._standard_increment(3.00), 0.5)

    def test_below_25(self):
        self.assertEqual(iv_tracker._standard_increment(12.00), 1.0)
        self.assertEqual(iv_tracker._standard_increment(24.99), 1.0)

    def test_below_50(self):
        self.assertEqual(iv_tracker._standard_increment(25.00), 2.5)
        self.assertEqual(iv_tracker._standard_increment(49.99), 2.5)

    def test_below_200(self):
        self.assertEqual(iv_tracker._standard_increment(100.00), 5.0)
        self.assertEqual(iv_tracker._standard_increment(199.99), 5.0)

    def test_below_500(self):
        self.assertEqual(iv_tracker._standard_increment(300.00), 10.0)
        self.assertEqual(iv_tracker._standard_increment(499.99), 10.0)

    def test_above_500(self):
        self.assertEqual(iv_tracker._standard_increment(500.00), 25.0)
        self.assertEqual(iv_tracker._standard_increment(4000.00), 25.0)


# ══════════════════════════════════════════════════════════════════════════════

class TestNearestStrikes(unittest.TestCase):

    def test_default_count_is_three(self):
        strikes = iv_tracker._nearest_strikes(100.0)
        self.assertEqual(len(strikes), 3)

    def test_five_strikes(self):
        strikes = iv_tracker._nearest_strikes(100.0, count=5)
        self.assertEqual(len(strikes), 5)

    def test_atm_is_nearest(self):
        """The middle strike should be closest to current price."""
        price   = 100.0
        strikes = iv_tracker._nearest_strikes(price, count=3)
        dists   = [abs(s - price) for s in strikes]
        self.assertEqual(dists.index(min(dists)), 1)   # middle element

    def test_evenly_spaced_at_100(self):
        """Strikes separated by $5 increment for a $100 stock."""
        strikes = iv_tracker._nearest_strikes(100.0, count=3)
        self.assertAlmostEqual(strikes[1] - strikes[0], 5.0)
        self.assertAlmostEqual(strikes[2] - strikes[1], 5.0)

    def test_low_price_increments(self):
        """$18 stock uses $1 increments."""
        strikes = iv_tracker._nearest_strikes(18.0, count=3)
        self.assertAlmostEqual(strikes[1] - strikes[0], 1.0)

    def test_high_price_increments(self):
        """$600 stock uses $25 increments."""
        strikes = iv_tracker._nearest_strikes(600.0, count=3)
        self.assertAlmostEqual(strikes[1] - strikes[0], 25.0)

    def test_no_negative_strikes(self):
        """All returned strikes must be positive."""
        for price in [5.0, 15.0, 50.0, 200.0, 500.0]:
            strikes = iv_tracker._nearest_strikes(price, count=5)
            self.assertTrue(all(s > 0 for s in strikes),
                            f"Negative strike for price={price}: {strikes}")


# ══════════════════════════════════════════════════════════════════════════════

class TestTargetExpirations(unittest.TestCase):

    def _fixed_today(self, year=2026, month=4, day=24):
        return date(year, month, day)

    def test_returns_list(self):
        result = iv_tracker._target_expirations(self._fixed_today())
        self.assertIsInstance(result, list)

    def test_all_fridays(self):
        """Every returned expiration must fall on a Friday (weekday 4)."""
        result = iv_tracker._target_expirations(self._fixed_today())
        for exp in result:
            self.assertEqual(exp.weekday(), 4,
                             f"{exp} is not a Friday (weekday={exp.weekday()})")

    def test_within_dte_window(self):
        """Each expiration must be between MIN_DTE and MAX_DTE."""
        today  = self._fixed_today()
        result = iv_tracker._target_expirations(today)
        for exp in result:
            dte = (exp - today).days
            self.assertGreaterEqual(dte, iv_tracker.MIN_DTE,
                                    f"{exp} is {dte} DTE, below MIN_DTE")
            self.assertLessEqual(dte, iv_tracker.MAX_DTE,
                                 f"{exp} is {dte} DTE, above MAX_DTE")

    def test_sorted_by_closeness_to_target(self):
        """Result sorted ascending by |DTE - TARGET_DTE|."""
        today  = self._fixed_today()
        result = iv_tracker._target_expirations(today)
        dists  = [abs((exp - today).days - iv_tracker.TARGET_DTE) for exp in result]
        self.assertEqual(dists, sorted(dists))

    def test_is_third_friday(self):
        """Each expiration is the 3rd Friday of its month."""
        today  = self._fixed_today()
        result = iv_tracker._target_expirations(today)
        for exp in result:
            # Count Fridays up to and including exp
            fridays_in_month = sum(
                1 for d in range(1, exp.day + 1)
                if date(exp.year, exp.month, d).weekday() == 4
            )
            self.assertEqual(fridays_in_month, 3,
                             f"{exp} is not the 3rd Friday of its month")


# ══════════════════════════════════════════════════════════════════════════════

class TestBuildContractSymbols(unittest.TestCase):

    def _symbols_prices(self):
        return {"AAPL": 270.0, "MSFT": 420.0}

    def test_occ_format(self):
        """Contract symbols must match the OCC pattern: SYM YYMMDD C STRIKE8."""
        today   = date(2026, 4, 24)
        mapping = iv_tracker.build_contract_symbols(self._symbols_prices(), today)
        import re
        pattern = re.compile(r'^[A-Z]{1,5}\d{6}C\d{8}$')
        for sym in mapping:
            self.assertRegex(sym, pattern, f"Bad OCC format: {sym}")

    def test_strike_encoding(self):
        """$270.00 strike encodes as 00270000 (strike × 1000, 8-digit zero-padded)."""
        today   = date(2026, 4, 24)
        mapping = iv_tracker.build_contract_symbols({"AAPL": 270.0}, today)
        # At least one AAPL contract with strike 00270000
        aapl_contracts = [k for k in mapping if k.startswith("AAPL")]
        strikes = {int(k[-8:]) / 1000.0 for k in aapl_contracts}
        self.assertIn(270.0, strikes)

    def test_three_strikes_per_symbol(self):
        """3 strike candidates generated per underlying symbol."""
        today    = date(2026, 4, 24)
        prices   = {"AAPL": 270.0}
        mapping  = iv_tracker.build_contract_symbols(prices, today)
        aapl_cnt = sum(1 for v in mapping.values() if v == "AAPL")
        self.assertEqual(aapl_cnt, 3)

    def test_underlying_in_values(self):
        """Values of the mapping are the underlying symbols."""
        today   = date(2026, 4, 24)
        mapping = iv_tracker.build_contract_symbols(self._symbols_prices(), today)
        underlyings = set(mapping.values())
        self.assertEqual(underlyings, {"AAPL", "MSFT"})

    def test_empty_prices(self):
        self.assertEqual(
            iv_tracker.build_contract_symbols({}, date(2026, 4, 24)),
            {}
        )


# ══════════════════════════════════════════════════════════════════════════════

class TestSelectAtmIv(unittest.TestCase):

    def _make_contract(self, sym, strike, expiry="260515"):
        stk_int = int(round(strike * 1000))
        return f"{sym}{expiry}C{stk_int:08d}"

    def test_picks_nearest_strike(self):
        """When two strikes are available, the one closer to stock price wins."""
        prices = {"AAPL": 270.0}
        c_close = self._make_contract("AAPL", 270.0)   # ATM: dist = 0
        c_far   = self._make_contract("AAPL", 280.0)   # OTM: dist = 10
        contract_to_sym = {c_close: "AAPL", c_far: "AAPL"}
        iv_snapshots    = {c_close: 0.35, c_far: 0.28}

        result = iv_tracker.select_atm_iv(contract_to_sym, iv_snapshots, prices)
        self.assertAlmostEqual(result["AAPL"], 0.35)

    def test_empty_snapshots(self):
        result = iv_tracker.select_atm_iv({}, {}, {"AAPL": 270.0})
        self.assertEqual(result, {})

    def test_symbol_not_in_prices(self):
        """Contract referencing an unknown symbol is silently skipped."""
        c = self._make_contract("UNKNOWN", 100.0)
        result = iv_tracker.select_atm_iv(
            {c: "UNKNOWN"}, {c: 0.25}, {"AAPL": 270.0}
        )
        self.assertNotIn("UNKNOWN", result)

    def test_multiple_symbols(self):
        prices  = {"AAPL": 270.0, "MSFT": 420.0}
        c_aapl  = self._make_contract("AAPL", 270.0, "260515")
        c_msft  = self._make_contract("MSFT", 420.0, "260515")
        mapping = {c_aapl: "AAPL", c_msft: "MSFT"}
        snaps   = {c_aapl: 0.30, c_msft: 0.22}
        result  = iv_tracker.select_atm_iv(mapping, snaps, prices)
        self.assertAlmostEqual(result["AAPL"], 0.30)
        self.assertAlmostEqual(result["MSFT"], 0.22)


# ══════════════════════════════════════════════════════════════════════════════

class TestComputeIvRank(unittest.TestCase):

    def _series(self, values):
        """Build (date_str, iv) series from a list of iv values."""
        start = date(2025, 1, 2)
        return [(
            (start + timedelta(days=i)).strftime("%Y-%m-%d"), v
        ) for i, v in enumerate(values)]

    def test_insufficient_data_returns_none(self):
        series = self._series([0.25] * (iv_tracker.MIN_IV_HISTORY - 1))
        self.assertIsNone(iv_tracker.compute_iv_rank(series))

    def test_sufficient_data_returns_dict(self):
        series = self._series([0.25] * iv_tracker.MIN_IV_HISTORY)
        result = iv_tracker.compute_iv_rank(series)
        self.assertIsNotNone(result)

    def test_rank_formula(self):
        """IV Rank = (current - low) / (high - low) × 100."""
        values = [0.20] * 100 + [0.40] * 50 + [0.30]   # current=0.30, lo=0.20, hi=0.40
        series = self._series(values)
        result = iv_tracker.compute_iv_rank(series)
        expected = round((0.30 - 0.20) / (0.40 - 0.20) * 100, 1)
        self.assertAlmostEqual(result["iv_rank"], expected, places=1)

    def test_flat_series_returns_50(self):
        """When high == low, rank defaults to 50.0 (avoids div-by-zero)."""
        series = self._series([0.25] * 50)
        result = iv_tracker.compute_iv_rank(series)
        self.assertEqual(result["iv_rank"], 50.0)

    def test_current_at_high(self):
        values = [0.20] * 100 + [0.50]   # current equals 52wk high
        result = iv_tracker.compute_iv_rank(self._series(values))
        self.assertAlmostEqual(result["iv_rank"], 100.0, places=0)

    def test_current_at_low(self):
        values = [0.50] * 100 + [0.20]   # current equals 52wk low
        result = iv_tracker.compute_iv_rank(self._series(values))
        self.assertAlmostEqual(result["iv_rank"], 0.0, places=0)

    def test_output_keys(self):
        series = self._series([0.25] * 50)
        result = iv_tracker.compute_iv_rank(series)
        for key in ("iv_current", "iv_rank", "iv_52wk_high", "iv_52wk_low", "n_days"):
            self.assertIn(key, result, f"Missing key: {key}")


# ══════════════════════════════════════════════════════════════════════════════

class TestAppendTodayIv(unittest.TestCase):

    def test_creates_new_symbol(self):
        hist = {}
        result = iv_tracker.append_today_iv(hist, "2026-04-24", {"AAPL": 0.35})
        self.assertIn("AAPL", result)
        self.assertAlmostEqual(result["AAPL"]["2026-04-24"], 0.35)

    def test_updates_existing_date(self):
        hist = {"AAPL": {"2026-04-23": 0.30}}
        iv_tracker.append_today_iv(hist, "2026-04-24", {"AAPL": 0.35})
        self.assertAlmostEqual(hist["AAPL"]["2026-04-24"], 0.35)
        self.assertAlmostEqual(hist["AAPL"]["2026-04-23"], 0.30)  # still present

    def test_overwrites_same_date(self):
        hist = {"AAPL": {"2026-04-24": 0.25}}
        iv_tracker.append_today_iv(hist, "2026-04-24", {"AAPL": 0.35})
        self.assertAlmostEqual(hist["AAPL"]["2026-04-24"], 0.35)

    def test_prunes_old_entries(self):
        """Dates older than IV_RANK_WINDOW + 20 days are removed."""
        hist   = {}
        old_dt = (date.today() - timedelta(days=iv_tracker.IV_RANK_WINDOW + 25))
        old_s  = old_dt.strftime("%Y-%m-%d")
        hist["AAPL"] = {old_s: 0.30}
        iv_tracker.append_today_iv(hist, "2026-04-24", {"AAPL": 0.35})
        self.assertNotIn(old_s, hist["AAPL"])

    def test_rounds_to_6dp(self):
        hist = {}
        iv_tracker.append_today_iv(hist, "2026-04-24", {"AAPL": 0.3456789999})
        stored = hist["AAPL"]["2026-04-24"]
        self.assertEqual(stored, round(stored, 6))

    def test_multiple_symbols(self):
        hist = {}
        iv_tracker.append_today_iv(hist, "2026-04-24", {"AAPL": 0.30, "MSFT": 0.25})
        self.assertIn("AAPL", hist)
        self.assertIn("MSFT", hist)


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
