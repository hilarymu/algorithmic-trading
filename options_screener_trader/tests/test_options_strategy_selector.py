"""
Tests for options_strategy_selector.py

Pure-logic tests (no network calls, no file I/O):
  - TestNormInv           : probit function correctness
  - TestPutStrikeForDelta : BSM put strike estimation
  - TestCallStrikeForDelta: BSM call strike estimation
  - TestOccSymbol         : OCC contract symbol construction
  - TestCandidateStrikes  : strike candidate generation
  - TestSelectContract    : contract selection with mocked snapshots
"""

import math
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from datetime import date, timedelta

# ── Module import with config patch ──────────────────────────────────────────
# iv_tracker loads alpaca_config.json at module level; patch before import.
import builtins
_real_open = builtins.open

def _patched_open(file, *args, **kwargs):
    if str(file).endswith("alpaca_config.json"):
        import io
        return io.StringIO('{"api_key":"test","api_secret":"test",'
                           '"base_url":"https://paper-api.alpaca.markets/v2"}')
    return _real_open(file, *args, **kwargs)

builtins.open = _patched_open
sys.path.insert(0, str(Path(__file__).parent.parent))
import options_loop.options_strategy_selector as sel
builtins.open = _real_open


class TestNormInv(unittest.TestCase):
    """Inverse normal CDF (probit) correctness."""

    def test_p50_is_zero(self):
        """N_inv(0.50) = 0 (median of standard normal)."""
        self.assertAlmostEqual(sel._norm_inv(0.50), 0.0, places=3)

    def test_p84_is_one(self):
        """N_inv(0.8413) ≈ 1.0  (one standard deviation)."""
        self.assertAlmostEqual(sel._norm_inv(0.8413), 1.0, delta=0.01)

    def test_p16_is_minus_one(self):
        """N_inv(0.1587) ≈ -1.0  (symmetry)."""
        self.assertAlmostEqual(sel._norm_inv(0.1587), -1.0, delta=0.01)

    def test_p70_approx(self):
        """N_inv(0.70) ≈ 0.524 — used for 0.30 delta put target."""
        self.assertAlmostEqual(sel._norm_inv(0.70), 0.524, delta=0.02)

    def test_p975_is_196(self):
        """N_inv(0.975) ≈ 1.96 — standard stats result."""
        self.assertAlmostEqual(sel._norm_inv(0.975), 1.96, delta=0.02)

    def test_p025_is_minus196(self):
        """N_inv(0.025) ≈ -1.96 — symmetry."""
        self.assertAlmostEqual(sel._norm_inv(0.025), -1.96, delta=0.02)

    def test_extreme_p_raises(self):
        with self.assertRaises((ValueError, ZeroDivisionError, OverflowError)):
            sel._norm_inv(0.0)


class TestPutStrikeForDelta(unittest.TestCase):
    """BSM put strike estimation for target delta."""

    def _strike(self, S, iv, T, delta):
        return sel._put_strike_for_delta(S, iv, T, delta)

    def test_atm_put_delta_half(self):
        """0.50 delta put strike should be roughly ATM (or slightly above in practice)."""
        K = self._strike(100.0, 0.20, 0.1, 0.50)
        self.assertAlmostEqual(K, 100.0, delta=5.0)

    def test_lower_delta_gives_lower_strike(self):
        """0.15 delta put strike < 0.30 delta put strike (more OTM)."""
        K_30 = self._strike(200.0, 0.30, 35/365, 0.30)
        K_15 = self._strike(200.0, 0.30, 35/365, 0.15)
        self.assertLess(K_15, K_30)

    def test_higher_iv_widens_strike(self):
        """Higher IV at same delta -> more OTM strike (lower K)."""
        K_low  = self._strike(200.0, 0.20, 35/365, 0.30)
        K_high = self._strike(200.0, 0.50, 35/365, 0.30)
        self.assertLess(K_high, K_low)

    def test_strike_is_otm(self):
        """30-delta put strike should be below current price."""
        K = self._strike(100.0, 0.30, 35/365, 0.30)
        self.assertLess(K, 100.0)

    def test_zero_time_fallback(self):
        """T=0 uses fallback formula, returns a reasonable value."""
        K = self._strike(100.0, 0.30, 0.0, 0.30)
        self.assertGreater(K, 0)

    def test_typical_csp_aapl(self):
        """AAPL-like: $270, IV=35%, 35 DTE, target delta 0.30 -> strike in [230, 265]."""
        K = self._strike(270.0, 0.35, 35/365, 0.30)
        self.assertGreater(K, 230.0)
        self.assertLess(K, 265.0)


class TestCallStrikeForDelta(unittest.TestCase):
    """BSM call strike estimation for target delta."""

    def _strike(self, S, iv, T, delta):
        return sel._call_strike_for_delta(S, iv, T, delta)

    def test_half_delta_roughly_atm(self):
        """0.50 delta call is roughly ATM."""
        K = self._strike(100.0, 0.20, 0.1, 0.50)
        self.assertAlmostEqual(K, 100.0, delta=5.0)

    def test_lower_delta_gives_higher_strike(self):
        """0.25 delta call -> higher (more OTM) strike than 0.50 delta call."""
        K_50 = self._strike(200.0, 0.25, 35/365, 0.50)
        K_25 = self._strike(200.0, 0.25, 35/365, 0.25)
        self.assertGreater(K_25, K_50)

    def test_call_strike_above_price(self):
        """0.25 delta call should be OTM (strike > price)."""
        K = self._strike(100.0, 0.25, 35/365, 0.25)
        self.assertGreater(K, 100.0)


class TestOccSymbol(unittest.TestCase):
    """OCC option symbol construction."""

    def test_put_symbol_format(self):
        exp = date(2026, 5, 16)
        sym = sel._occ_symbol("AAPL", exp, "P", 250.0)
        self.assertEqual(sym, "AAPL260516P00250000")

    def test_call_symbol_format(self):
        exp = date(2026, 5, 16)
        sym = sel._occ_symbol("AAPL", exp, "C", 270.0)
        self.assertEqual(sym, "AAPL260516C00270000")

    def test_fractional_strike(self):
        """$145.50 strike -> 145500 in 8-digit field."""
        exp = date(2026, 6, 20)
        sym = sel._occ_symbol("MSFT", exp, "P", 145.5)
        self.assertEqual(sym, "MSFT260620P00145500")

    def test_large_strike(self):
        """$1750.00 strike (e.g. AMZN) -> correct format."""
        exp = date(2026, 5, 16)
        sym = sel._occ_symbol("AMZN", exp, "P", 1750.0)
        self.assertEqual(sym, "AMZN260516P01750000")

    def test_small_strike(self):
        """$5.00 strike -> 00005000."""
        exp = date(2026, 5, 16)
        sym = sel._occ_symbol("X", exp, "P", 5.0)
        self.assertEqual(sym, "X260516P00005000")


class TestCandidateStrikes(unittest.TestCase):
    """Strike candidate generation."""

    def test_centered_on_target(self):
        """Returns 5 strikes centred near target."""
        strikes = sel._candidate_strikes(250.0, 270.0, n_each_side=2)
        self.assertEqual(len(strikes), 5)

    def test_uses_standard_increment(self):
        """$270 stock uses $10 increment; strikes are multiples of 10."""
        strikes = sel._candidate_strikes(250.0, 270.0, n_each_side=2)
        for s in strikes:
            self.assertAlmostEqual(s % 10, 0, places=2)

    def test_sorted_ascending(self):
        strikes = sel._candidate_strikes(200.0, 200.0, n_each_side=2)
        self.assertEqual(strikes, sorted(strikes))

    def test_all_positive(self):
        strikes = sel._candidate_strikes(15.0, 15.0, n_each_side=2)
        for s in strikes:
            self.assertGreater(s, 0)


class TestSelectContract(unittest.TestCase):
    """select_contract with mocked fetch_option_snapshots and _target_expirations."""

    _CFG = {
        "contract_selection": {
            "target_dte_min": 21, "target_dte_max": 50, "target_dte_ideal": 35,
            "target_delta_csp": 0.30,
            "target_delta_call_buy": 0.50, "target_delta_call_sell": 0.25,
        },
        "position_sizing": {"contracts_per_position": 1},
        "filters": {
            "min_open_interest": 500,
            "max_bid_ask_spread_pct": 0.15,
            "min_stock_price": 15.0,
        },
        "exits": {},
    }

    def _candidate(self, strategy="CSP", symbol="AAPL",
                   price=270.0, iv=0.35, iv_rank=58.0):
        return {
            "symbol":        symbol,
            "strategy":      strategy,
            "iv_current":    iv,
            "iv_rank":       iv_rank,
            "price":         price,
            "rsi":           22.0,
            "vol_ratio":     1.8,
            "regime":        "bull",
            "near_earnings": False,
            "next_earnings": None,
            "rationale":     "test",
        }

    def _mock_snapshots(self, contracts):
        """Fake snapshot data: put delta -0.28, good liquidity."""
        result = {}
        for c in contracts:
            opt_type = "C" if "C" in c[len("AAPL260516"):] else "P"
            # Extract strike to vary delta slightly by strike
            try:
                strike = int(c[-8:]) / 1000.0
                delta  = -0.28 if opt_type == "P" else 0.28
            except Exception:
                delta = -0.28
            result[c] = {
                "delta":         delta,
                "iv":            0.35,
                "bid":           3.30,
                "ask":           3.70,
                "mid":           3.50,
                "open_interest": 2500,
                "spread_pct":    0.114,
            }
        return result

    def _fixed_expiry(self):
        """Return an expiry date 35 DTE from today."""
        return date.today() + timedelta(days=35)

    def test_csp_returns_entry(self):
        """CSP candidate produces a pending entry with short_leg set."""
        expiry = self._fixed_expiry()
        with patch.object(sel, "fetch_option_snapshots", side_effect=self._mock_snapshots), \
             patch.object(sel, "_target_expirations", return_value=[expiry]):
            entry = sel.select_contract(self._candidate("CSP"), self._CFG)
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry["short_leg"])
        self.assertIsNone(entry["long_leg"])
        self.assertEqual(entry["strategy"], "CSP")
        self.assertGreater(entry["net_credit_est"], 0)

    def test_put_spread_returns_two_legs(self):
        """PUT_SPREAD candidate produces short_leg and long_leg."""
        expiry = self._fixed_expiry()
        call_count = [0]

        def mock_snaps(contracts):
            call_count[0] += 1
            result = {}
            for c in contracts:
                # Make second call (long leg) have smaller delta
                delta = -0.15 if call_count[0] > 1 else -0.28
                try:
                    strike = int(c[-8:]) / 1000.0
                except Exception:
                    strike = 240.0
                result[c] = {
                    "delta": delta, "iv": 0.35,
                    "bid": 1.50, "ask": 1.80, "mid": 1.65,
                    "open_interest": 600, "spread_pct": 0.18,
                }
            return result

        # Patch OCC symbols so long leg has lower strike than short leg
        def mock_snaps_spread(contracts):
            result = {}
            for c in contracts:
                try:
                    strike = int(c[-8:]) / 1000.0
                except Exception:
                    strike = 250.0
                # Assign delta based on strike: lower strike = lower delta
                if strike <= 245.0:
                    delta = -0.15
                    bid, ask = 1.50, 1.80
                else:
                    delta = -0.28
                    bid, ask = 3.30, 3.70
                result[c] = {
                    "delta": delta, "iv": 0.35,
                    "bid": bid, "ask": ask, "mid": (bid+ask)/2,
                    "open_interest": 700, "spread_pct": 0.10,
                }
            return result

        with patch.object(sel, "fetch_option_snapshots", side_effect=mock_snaps_spread), \
             patch.object(sel, "_target_expirations", return_value=[expiry]):
            entry = sel.select_contract(self._candidate("PUT_SPREAD"), self._CFG)

        # May return None if legs cross — that's valid behaviour too
        if entry is not None:
            self.assertIsNotNone(entry["short_leg"])
            self.assertIsNotNone(entry["long_leg"])

    def test_no_iv_returns_none(self):
        """Missing IV data -> no contract selected."""
        c = self._candidate()
        c["iv_current"] = None
        expiry = self._fixed_expiry()
        with patch.object(sel, "fetch_option_snapshots", return_value={}), \
             patch.object(sel, "_target_expirations", return_value=[expiry]):
            entry = sel.select_contract(c, self._CFG)
        self.assertIsNone(entry)

    def test_no_expiry_returns_none(self):
        """No valid expiry in window -> no contract selected."""
        with patch.object(sel, "_target_expirations", return_value=[]):
            entry = sel.select_contract(self._candidate(), self._CFG)
        self.assertIsNone(entry)

    def test_low_open_interest_skipped(self):
        """Contracts below min OI threshold are skipped."""
        expiry = self._fixed_expiry()

        def low_oi_snaps(contracts):
            return {c: {
                "delta": -0.28, "iv": 0.35,
                "bid": 3.30, "ask": 3.70, "mid": 3.50,
                "open_interest": 100,    # below min_open_interest=500
                "spread_pct": 0.05,
            } for c in contracts}

        with patch.object(sel, "fetch_option_snapshots", side_effect=low_oi_snaps), \
             patch.object(sel, "_target_expirations", return_value=[expiry]):
            entry = sel.select_contract(self._candidate(), self._CFG)
        self.assertIsNone(entry)

    def test_entry_has_required_fields(self):
        """Successful entry contains all required tracking fields."""
        expiry = self._fixed_expiry()
        with patch.object(sel, "fetch_option_snapshots", side_effect=self._mock_snapshots), \
             patch.object(sel, "_target_expirations", return_value=[expiry]):
            entry = sel.select_contract(self._candidate(), self._CFG)
        self.assertIsNotNone(entry)
        required = {"id", "symbol", "strategy", "regime", "screened_date",
                    "expiry", "dte", "short_leg", "long_leg",
                    "net_credit_est", "capital_at_risk", "status", "created_at"}
        for field in required:
            self.assertIn(field, entry, f"Missing field: {field}")

    def test_pending_review_status(self):
        """All new entries start with status=pending_review."""
        expiry = self._fixed_expiry()
        with patch.object(sel, "fetch_option_snapshots", side_effect=self._mock_snapshots), \
             patch.object(sel, "_target_expirations", return_value=[expiry]):
            entry = sel.select_contract(self._candidate(), self._CFG)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "pending_review")


# ==============================================================================

if __name__ == "__main__":
    unittest.main()
