"""
Tests for options_executor.py

Pure-logic tests (no network calls, no file I/O):
  - TestCountOpenPositions   : position counting
  - TestSymbolAlreadyOpen    : duplicate symbol check
  - TestCheckPositionFits    : sizing / safety gate logic
"""

import sys
import unittest
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
import options_loop.options_executor as exe
builtins.open = _real_open


_CFG = {
    "position_sizing": {
        "max_positions": 8,
        "max_pct_nav_per_position": 0.07,
        "contracts_per_position": 1,
    },
    "auto_entry": {"enabled": False},
}

def _state(positions=None, pause=False, losses=0):
    return {
        "positions":          positions or [],
        "archived":           [],
        "pause_new_entries":  pause,
        "consecutive_losses": losses,
        "last_updated":       None,
    }

def _open_pos(symbol="AAPL", status="open"):
    return {"symbol": symbol, "status": status}

def _entry(symbol="AAPL", strategy="CSP", capital=25000.0, near_earn=False):
    return {
        "id":              f"{symbol}-20260425-{strategy}-260516-250",
        "symbol":          symbol,
        "strategy":        strategy,
        "capital_at_risk": capital,
        "near_earnings":   near_earn,
    }


class TestCountOpenPositions(unittest.TestCase):

    def test_empty_state(self):
        self.assertEqual(exe.count_open_positions(_state()), 0)

    def test_counts_open_only(self):
        positions = [
            _open_pos("AAPL", "open"),
            _open_pos("MSFT", "closed"),
            _open_pos("GOOG", "open"),
        ]
        self.assertEqual(exe.count_open_positions(_state(positions)), 2)

    def test_all_closed(self):
        positions = [_open_pos("AAPL", "closed"), _open_pos("MSFT", "closed")]
        self.assertEqual(exe.count_open_positions(_state(positions)), 0)


class TestSymbolAlreadyOpen(unittest.TestCase):

    def test_symbol_in_open_positions(self):
        state = _state([_open_pos("AAPL", "open")])
        self.assertTrue(exe.symbol_already_open("AAPL", state))

    def test_symbol_not_open(self):
        state = _state([_open_pos("MSFT", "open")])
        self.assertFalse(exe.symbol_already_open("AAPL", state))

    def test_symbol_closed_not_counted(self):
        """Closed position does not block new entry in same symbol."""
        state = _state([_open_pos("AAPL", "closed")])
        self.assertFalse(exe.symbol_already_open("AAPL", state))

    def test_empty_state(self):
        self.assertFalse(exe.symbol_already_open("AAPL", _state()))


class TestCheckPositionFits(unittest.TestCase):

    EQUITY = 500_000.0    # $500k paper account (realistic for selling options)

    def _check(self, entry, state=None, equity=None, config=None):
        return exe.check_position_fits(
            entry,
            equity  or self.EQUITY,
            config  or _CFG,
            state   or _state(),
        )

    def test_normal_entry_passes(self):
        ok, reason = self._check(_entry())
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_paused_state_blocks(self):
        ok, reason = self._check(_entry(), state=_state(pause=True))
        self.assertFalse(ok)
        self.assertIn("paused", reason)

    def test_max_positions_blocks(self):
        positions = [_open_pos(f"SYM{i}", "open") for i in range(8)]
        ok, reason = self._check(_entry(), state=_state(positions))
        self.assertFalse(ok)
        self.assertIn("max positions", reason)

    def test_symbol_already_open_blocks(self):
        state = _state([_open_pos("AAPL", "open")])
        ok, reason = self._check(_entry("AAPL"), state=state)
        self.assertFalse(ok)
        self.assertIn("already have", reason)

    def test_capital_too_large_blocks(self):
        """$37k capital at risk > 7% of $500k ($35k) -> blocked."""
        ok, reason = self._check(_entry(capital=37_000.0))
        self.assertFalse(ok)
        self.assertIn("capital at risk", reason)

    def test_exactly_at_7pct_nav_passes(self):
        """$35,000 capital at risk = exactly 7% of $500k -> allowed."""
        ok, reason = self._check(_entry(capital=35_000.0))
        self.assertTrue(ok, reason)

    def test_hard_nav_ceiling_applied(self):
        """Even if config says 15%, hard ceiling is 10% ($50k max on $500k)."""
        big_cfg = {
            "position_sizing": {
                "max_positions": 8,
                "max_pct_nav_per_position": 0.15,   # would allow $75k
                "contracts_per_position": 1,
            },
        }
        # $52k exceeds hard ceiling of 10% on $500k equity ($50k)
        ok, reason = self._check(_entry(capital=52_000.0), config=big_cfg)
        self.assertFalse(ok)
        self.assertIn("capital at risk", reason)

    def test_near_earnings_csp_blocked(self):
        """Naked put near earnings is hard-blocked regardless of config."""
        ok, reason = self._check(_entry(strategy="CSP", near_earn=True))
        self.assertFalse(ok)
        self.assertIn("earnings", reason)

    def test_near_earnings_spread_allowed(self):
        """Spread near earnings is NOT blocked (limited risk)."""
        ok, reason = self._check(_entry(strategy="PUT_SPREAD", near_earn=True))
        self.assertTrue(ok, reason)


# ==============================================================================

if __name__ == "__main__":
    unittest.main()
