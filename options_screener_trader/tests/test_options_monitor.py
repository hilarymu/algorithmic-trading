"""
Tests for options_monitor.py

Pure-logic tests (no network calls, no file I/O):
  - TestComputePnlPct    : P&L calculation for CSP and spreads
  - TestDteRemaining     : days-to-expiry calculation
  - TestExitConditions   : profit target / loss limit / DTE / RSI checkers
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

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
import options_loop.options_monitor as mon
builtins.open = _real_open


def _position(strategy="CSP", entry_credit=3.50,
              short_contract="AAPL260516P00250000",
              long_contract=None, qty=1,
              expiry=None, symbol="AAPL"):
    """Helper: build a minimal position dict for testing."""
    if expiry is None:
        expiry = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    short_leg = {"contract": short_contract} if short_contract else None
    long_leg  = {"contract": long_contract}  if long_contract  else None
    return {
        "symbol":        symbol,
        "strategy":      strategy,
        "entry_credit":  entry_credit,
        "net_credit":    entry_credit,
        "short_leg":     short_leg,
        "long_leg":      long_leg,
        "qty":           qty,
        "expiry":        expiry,
        "status":        "open",
    }


_CFG = {
    "exits": {
        "profit_target_pct":    0.50,
        "loss_limit_multiplier": 2.0,
        "close_at_dte":         21,
        "rsi_recovery_exit":    50,
    }
}


class TestDteRemaining(unittest.TestCase):

    def test_future_expiry(self):
        exp = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        self.assertEqual(mon.dte_remaining(exp), 30)

    def test_expired(self):
        exp = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        self.assertEqual(mon.dte_remaining(exp), -5)

    def test_today_is_zero(self):
        exp = date.today().strftime("%Y-%m-%d")
        self.assertEqual(mon.dte_remaining(exp), 0)


class TestComputePnlPct(unittest.TestCase):
    """P&L percentage calculation for different strategy types."""

    SC = "AAPL260516P00250000"
    LC = "AAPL260516P00240000"

    def _snaps(self, short_bid=1.75, short_ask=1.95,
               long_bid=0.80, long_ask=0.90):
        return {
            self.SC: {"bid": short_bid, "ask": short_ask,
                      "mid": (short_bid + short_ask) / 2},
            self.LC: {"bid": long_bid,  "ask": long_ask,
                      "mid": (long_bid + long_ask) / 2},
        }

    # ── CSP ────────────────────────────────────────────────────────────────────

    def test_csp_50pct_profit(self):
        """Short put at half its original value -> 50% profit."""
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = {self.SC: {"bid": 1.75, "ask": 1.95, "mid": 1.85}}
        pnl  = mon.compute_pnl_pct(pos, snaps)
        self.assertAlmostEqual(pnl, 0.50, delta=0.01)

    def test_csp_full_profit_at_zero(self):
        """Put expires worthless -> 100% profit."""
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = {self.SC: {"bid": 0.01, "ask": 0.05, "mid": 0.03}}
        pnl  = mon.compute_pnl_pct(pos, snaps)
        self.assertAlmostEqual(pnl, 1.0 - 0.01/3.50, delta=0.01)

    def test_csp_loss_at_doubled_bid(self):
        """Put doubled in value -> 100% loss of premium (pnl_pct = -1.0)."""
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = {self.SC: {"bid": 7.00, "ask": 7.20, "mid": 7.10}}
        pnl  = mon.compute_pnl_pct(pos, snaps)
        self.assertAlmostEqual(pnl, -1.0, delta=0.01)

    def test_csp_missing_snapshot_returns_none(self):
        pos = _position("CSP", short_contract=self.SC)
        pnl = mon.compute_pnl_pct(pos, {})
        self.assertIsNone(pnl)

    def test_csp_zero_entry_credit_returns_none(self):
        pos  = _position("CSP", entry_credit=0.0, short_contract=self.SC)
        snaps = {self.SC: {"bid": 1.0, "ask": 1.2, "mid": 1.1}}
        pnl  = mon.compute_pnl_pct(pos, snaps)
        self.assertIsNone(pnl)

    # ── PUT_SPREAD ──────────────────────────────────────────────────────────────

    def test_spread_profit(self):
        """Spread: entry net credit 2.0, cost to close 0.8 -> 60% profit."""
        pos  = _position("PUT_SPREAD", entry_credit=2.0,
                         short_contract=self.SC, long_contract=self.LC)
        # short_ask=0.90, long_bid=0.50 -> net debit to close = 0.40
        snaps = self._snaps(short_bid=0.80, short_ask=0.90,
                            long_bid=0.50, long_ask=0.60)
        pnl  = mon.compute_pnl_pct(pos, snaps)
        expected = (2.0 - (0.90 - 0.50)) / 2.0   # = (2.0 - 0.40) / 2.0 = 0.80
        self.assertAlmostEqual(pnl, expected, delta=0.01)

    def test_spread_loss(self):
        """Spread: widening spread -> negative P&L."""
        pos  = _position("PUT_SPREAD", entry_credit=2.0,
                         short_contract=self.SC, long_contract=self.LC)
        snaps = self._snaps(short_bid=3.80, short_ask=4.00,
                            long_bid=1.40, long_ask=1.60)
        pnl  = mon.compute_pnl_pct(pos, snaps)
        # cost to close = 4.00 - 1.40 = 2.60 > 2.00 entry -> loss
        self.assertLess(pnl, 0)

    def test_spread_missing_long_returns_none(self):
        pos   = _position("PUT_SPREAD", entry_credit=2.0,
                          short_contract=self.SC, long_contract=self.LC)
        snaps = {self.SC: {"bid": 1.0, "ask": 1.2, "mid": 1.1}}   # no long
        pnl   = mon.compute_pnl_pct(pos, snaps)
        self.assertIsNone(pnl)


class TestExitConditions(unittest.TestCase):
    """Check each exit condition independently."""

    SC = "AAPL260516P00250000"

    def _snaps(self, bid, ask=None):
        if ask is None:
            ask = bid + 0.20
        return {self.SC: {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}}

    def test_profit_target_triggers(self):
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = self._snaps(1.75)    # exactly 50%
        self.assertTrue(mon.should_take_profit(pos, snaps, _CFG))

    def test_profit_target_does_not_trigger_prematurely(self):
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = self._snaps(2.00)    # only 43% profit
        self.assertFalse(mon.should_take_profit(pos, snaps, _CFG))

    def test_loss_limit_triggers(self):
        """pnl_pct <= -1.0 triggers loss limit (2x multiplier means loss = premium)."""
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = self._snaps(7.00)    # doubled -> pnl_pct = -1.0
        self.assertTrue(mon.should_cut_loss(pos, snaps, _CFG))

    def test_loss_limit_does_not_trigger_early(self):
        pos  = _position("CSP", entry_credit=3.50, short_contract=self.SC)
        snaps = self._snaps(5.00)    # pnl_pct = -0.43
        self.assertFalse(mon.should_cut_loss(pos, snaps, _CFG))

    def test_dte_close_triggers_at_threshold(self):
        exp = (date.today() + timedelta(days=21)).strftime("%Y-%m-%d")
        pos = _position(expiry=exp)
        self.assertTrue(mon.should_close_for_dte(pos, _CFG))

    def test_dte_close_does_not_trigger_above_threshold(self):
        exp = (date.today() + timedelta(days=25)).strftime("%Y-%m-%d")
        pos = _position(expiry=exp)
        self.assertFalse(mon.should_close_for_dte(pos, _CFG))

    def test_dte_close_triggers_below_threshold(self):
        exp = (date.today() + timedelta(days=10)).strftime("%Y-%m-%d")
        pos = _position(expiry=exp)
        self.assertTrue(mon.should_close_for_dte(pos, _CFG))

    def test_rsi_recovery_triggers_above_threshold(self):
        pos = _position()
        with patch.object(mon, "fetch_rsi", return_value=52.0):
            self.assertTrue(mon.should_close_for_rsi(pos, _CFG))

    def test_rsi_recovery_does_not_trigger_below(self):
        pos = _position()
        with patch.object(mon, "fetch_rsi", return_value=45.0):
            self.assertFalse(mon.should_close_for_rsi(pos, _CFG))

    def test_rsi_recovery_does_not_trigger_on_none(self):
        pos = _position()
        with patch.object(mon, "fetch_rsi", return_value=None):
            self.assertFalse(mon.should_close_for_rsi(pos, _CFG))

    def test_rsi_at_threshold_triggers(self):
        """RSI exactly at threshold triggers the exit (>= not >)."""
        pos = _position()
        with patch.object(mon, "fetch_rsi", return_value=50.0):
            self.assertTrue(mon.should_close_for_rsi(pos, _CFG))


# ==============================================================================

if __name__ == "__main__":
    unittest.main()
