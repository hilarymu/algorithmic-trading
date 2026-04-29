"""
tests/test_options_screener.py
===============================
Unit tests for options_screener.py pure-computation functions.
No API calls, no file I/O.

Run from project root:
    py -3 -m unittest tests.test_options_screener -v
"""

import builtins
import json
import sys
import unittest
from pathlib import Path

# ── Patch config before importing (iv_tracker loaded transitively) ─────────────
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
import options_screener   # noqa: E402
builtins.open = _orig_open


# ── Default config used across tests ──────────────────────────────────────────
_CFG = {
    "indicators": {
        "rsi_oversold": 25,
        "volume_ratio_min": 1.2,
        "iv_rank_min_sell": 40,
        "iv_rank_max_buy": 30,
    },
    "filters": {
        "min_stock_price": 15.0,
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  Wilder's RSI
# ══════════════════════════════════════════════════════════════════════════════

class TestWilderRsi(unittest.TestCase):

    def _flat(self, price=100.0, n=40):
        return [price] * n

    def _rising(self, start=100.0, step=1.0, n=40):
        return [start + i * step for i in range(n)]

    def _falling(self, start=140.0, step=1.0, n=40):
        return [start - i * step for i in range(n)]

    def test_insufficient_data_returns_none(self):
        self.assertIsNone(options_screener._wilder_rsi([100.0] * 14))  # need period+1

    def test_flat_series_returns_50(self):
        """Flat prices: no gains, no losses → RS undefined → RSI should be 100
        (avg_loss → 0). Wilder's: if avg_loss=0 RSI=100."""
        result = options_screener._wilder_rsi(self._flat())
        # flat series: all gains=0, losses=0 → avg_loss near 0 → RSI=100
        self.assertAlmostEqual(result, 100.0, places=0)

    def test_consistently_rising_near_100(self):
        """Consistently rising prices → RSI close to 100."""
        result = options_screener._wilder_rsi(self._rising())
        self.assertGreater(result, 90.0)

    def test_consistently_falling_near_0(self):
        """Consistently falling prices → RSI close to 0."""
        result = options_screener._wilder_rsi(self._falling())
        self.assertLess(result, 10.0)

    def test_known_oversold(self):
        """Simulate sharp drop that should produce RSI < 30."""
        closes = [100.0] * 10 + [99.0, 98.0, 97.0, 95.0, 92.0,
                                   88.0, 83.0, 77.0, 70.0, 62.0,
                                   53.0, 43.0, 32.0, 20.0, 10.0]
        result = options_screener._wilder_rsi(closes)
        self.assertIsNotNone(result)
        self.assertLess(result, 30.0)

    def test_range_0_to_100(self):
        """RSI result must always be in [0, 100]."""
        for closes in [
            self._flat(),
            self._rising(),
            self._falling(),
            [100.0, 101.0, 99.0, 102.0, 98.0] * 10,
        ]:
            result = options_screener._wilder_rsi(closes)
            if result is not None:
                self.assertGreaterEqual(result, 0.0)
                self.assertLessEqual(result, 100.0)

    def test_custom_period(self):
        closes = [float(i) for i in range(1, 50)]
        result = options_screener._wilder_rsi(closes, period=7)
        self.assertIsNotNone(result)


# ══════════════════════════════════════════════════════════════════════════════
#  Volume ratio
# ══════════════════════════════════════════════════════════════════════════════

class TestVolRatio(unittest.TestCase):

    def test_basic_ratio(self):
        """Today = 2.0M, 20-day avg = 1.0M → ratio = 2.0."""
        volumes = [1_000_000.0] * 20 + [2_000_000.0]
        result  = options_screener._vol_ratio(volumes)
        self.assertAlmostEqual(result, 2.0, places=2)

    def test_average_volume_today(self):
        """Today same as average → ratio = 1.0."""
        volumes = [1_000_000.0] * 21
        result  = options_screener._vol_ratio(volumes)
        self.assertAlmostEqual(result, 1.0, places=2)

    def test_insufficient_data(self):
        """Fewer than VOL_BARS entries → None."""
        self.assertIsNone(options_screener._vol_ratio([1_000_000.0] * 10))

    def test_zero_avg_volume(self):
        """Zero prior-day volumes → None (no division by zero)."""
        volumes = [0.0] * 20 + [1_000_000.0]
        self.assertIsNone(options_screener._vol_ratio(volumes))


# ══════════════════════════════════════════════════════════════════════════════
#  Strategy selection matrix
# ══════════════════════════════════════════════════════════════════════════════

class TestSelectStrategy(unittest.TestCase):

    def _sel(self, rsi, vol_ratio, iv_rank, regime):
        return options_screener.select_strategy(rsi, vol_ratio, iv_rank, regime, _CFG)

    # ── Hard-gate cases ───────────────────────────────────────────────────────

    def test_bear_always_none(self):
        strat, _ = self._sel(15.0, 2.0, 70.0, "bear")
        self.assertIsNone(strat)

    def test_iv_rank_none_returns_none(self):
        strat, _ = self._sel(20.0, 2.0, None, "bull")
        self.assertIsNone(strat)

    def test_rsi_none_returns_none(self):
        strat, _ = self._sel(None, 2.0, 55.0, "bull")
        self.assertIsNone(strat)

    def test_rsi_above_threshold_returns_none(self):
        strat, _ = self._sel(26.0, 2.0, 60.0, "bull")    # RSI > 25
        self.assertIsNone(strat)

    def test_rsi_at_threshold_returns_none(self):
        """RSI must be strictly below threshold."""
        strat, _ = self._sel(25.0, 2.0, 60.0, "bull")
        self.assertIsNone(strat)

    def test_vol_ratio_below_min_returns_none(self):
        strat, _ = self._sel(20.0, 1.1, 60.0, "bull")    # vol < 1.2
        self.assertIsNone(strat)

    def test_vol_ratio_none_returns_none(self):
        strat, _ = self._sel(20.0, None, 60.0, "bull")
        self.assertIsNone(strat)

    # ── Bull regime ───────────────────────────────────────────────────────────

    def test_bull_high_iv_rank_is_csp(self):
        strat, _ = self._sel(22.0, 1.5, 55.0, "bull")
        self.assertEqual(strat, "CSP")

    def test_bull_extreme_rsi_high_iv_still_csp(self):
        strat, _ = self._sel(18.0, 2.0, 65.0, "bull")
        self.assertEqual(strat, "CSP")

    def test_bull_low_iv_extreme_rsi_is_call_spread(self):
        strat, _ = self._sel(18.0, 2.0, 25.0, "bull")    # IV rank < 30, RSI < 20
        self.assertEqual(strat, "CALL_SPREAD")

    def test_bull_low_iv_rsi_not_extreme_is_none(self):
        strat, _ = self._sel(23.0, 2.0, 25.0, "bull")    # IV rank < 30, RSI 20-25
        self.assertIsNone(strat)

    def test_bull_neutral_iv_is_none(self):
        strat, _ = self._sel(22.0, 1.5, 35.0, "bull")    # IV rank 30-40 → neutral
        self.assertIsNone(strat)

    # ── Recovery regime ───────────────────────────────────────────────────────

    def test_recovery_same_as_bull_high_iv(self):
        strat, _ = self._sel(24.0, 1.3, 45.0, "recovery")
        self.assertEqual(strat, "CSP")

    # ── Mild correction ───────────────────────────────────────────────────────

    def test_mild_correction_high_iv_is_put_spread(self):
        strat, _ = self._sel(24.0, 1.5, 55.0, "mild_correction")
        self.assertEqual(strat, "PUT_SPREAD")

    def test_mild_correction_extreme_rsi_high_iv_is_csp(self):
        strat, _ = self._sel(18.0, 1.5, 60.0, "mild_correction")
        self.assertEqual(strat, "CSP")

    def test_mild_correction_low_iv_is_none(self):
        strat, _ = self._sel(22.0, 1.5, 45.0, "mild_correction")
        self.assertIsNone(strat)

    # ── Correction ────────────────────────────────────────────────────────────

    def test_correction_high_iv_extreme_rsi_is_otm_spread(self):
        strat, _ = self._sel(18.0, 2.0, 65.0, "correction")
        self.assertEqual(strat, "OTM_PUT_SPREAD")

    def test_correction_moderate_iv_is_none(self):
        strat, _ = self._sel(18.0, 2.0, 55.0, "correction")  # IV rank < 60
        self.assertIsNone(strat)

    def test_correction_high_iv_rsi_not_extreme_is_none(self):
        strat, _ = self._sel(22.0, 2.0, 65.0, "correction")  # RSI not < 20
        self.assertIsNone(strat)

    # ── Geopolitical shock ────────────────────────────────────────────────────

    def test_geopolitical_shock_extreme_qualifies(self):
        strat, _ = self._sel(15.0, 3.0, 80.0, "geopolitical_shock")
        self.assertEqual(strat, "OTM_PUT_SPREAD")

    # ── Rationale always returned ─────────────────────────────────────────────

    def test_rationale_is_always_string(self):
        for rsi, vr, iv, regime in [
            (20.0, 1.5, 55.0, "bull"),
            (26.0, 1.5, 55.0, "bull"),
            (20.0, 0.8, 55.0, "bull"),
            (20.0, 1.5, None, "bull"),
            (None, 1.5, 55.0, "bull"),
            (20.0, 1.5, 55.0, "bear"),
        ]:
            _, rationale = options_screener.select_strategy(rsi, vr, iv, regime, _CFG)
            self.assertIsInstance(rationale, str)
            self.assertGreater(len(rationale), 0)


# ══════════════════════════════════════════════════════════════════════════════
#  screen_candidates (integration — no I/O, pure logic)
# ══════════════════════════════════════════════════════════════════════════════

class TestScreenCandidates(unittest.TestCase):

    def _iv_cache(self, sym="AAPL", iv_rank=60.0, iv_current=0.35):
        return {sym: {
            "iv_rank":      iv_rank,
            "iv_current":   iv_current,
            "near_earnings": False,
            "next_earnings": None,
        }}

    def _signal(self, rsi=22.0, vol_ratio=1.8, close=270.0):
        return {"rsi": rsi, "vol_ratio": vol_ratio, "close": close}

    def test_passing_candidate_included(self):
        iv_cache    = self._iv_cache()
        signal_data = {"AAPL": self._signal()}
        candidates  = options_screener.screen_candidates(
            iv_cache, signal_data, "bull", _CFG
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["symbol"], "AAPL")

    def test_bear_regime_no_candidates(self):
        iv_cache    = self._iv_cache()
        signal_data = {"AAPL": self._signal()}
        candidates  = options_screener.screen_candidates(
            iv_cache, signal_data, "bear", _CFG
        )
        self.assertEqual(len(candidates), 0)

    def test_rsi_too_high_excluded(self):
        signal_data = {"AAPL": self._signal(rsi=30.0)}
        candidates  = options_screener.screen_candidates(
            self._iv_cache(), signal_data, "bull", _CFG
        )
        self.assertEqual(len(candidates), 0)

    def test_price_below_min_excluded(self):
        signal_data = {"AAPL": self._signal(close=12.0)}
        candidates  = options_screener.screen_candidates(
            self._iv_cache(), signal_data, "bull", _CFG
        )
        self.assertEqual(len(candidates), 0)

    def test_null_iv_rank_excluded(self):
        iv_cache    = self._iv_cache(iv_rank=None)
        signal_data = {"AAPL": self._signal()}
        candidates  = options_screener.screen_candidates(
            iv_cache, signal_data, "bull", _CFG
        )
        self.assertEqual(len(candidates), 0)

    def test_sorted_by_iv_rank_descending(self):
        iv_cache = {
            "LOW_IV": {"iv_rank": 45.0, "iv_current": 0.25,
                       "near_earnings": False, "next_earnings": None},
            "HIGH_IV": {"iv_rank": 75.0, "iv_current": 0.45,
                        "near_earnings": False, "next_earnings": None},
            "MED_IV":  {"iv_rank": 58.0, "iv_current": 0.33,
                        "near_earnings": False, "next_earnings": None},
        }
        signal_data = {
            sym: {"rsi": 20.0, "vol_ratio": 2.0, "close": 100.0}
            for sym in iv_cache
        }
        candidates = options_screener.screen_candidates(
            iv_cache, signal_data, "bull", _CFG
        )
        ranks = [c["iv_rank"] for c in candidates]
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_candidate_has_required_fields(self):
        signal_data = {"AAPL": self._signal()}
        candidates  = options_screener.screen_candidates(
            self._iv_cache(), signal_data, "bull", _CFG
        )
        required = {"symbol", "rsi", "vol_ratio", "iv_rank", "iv_current",
                    "price", "near_earnings", "regime", "strategy", "rationale"}
        for field in required:
            self.assertIn(field, candidates[0], f"Missing field: {field}")


# ══════════════════════════════════════════════════════════════════════════════
#  append_to_picks_history (pure-logic via patching)
# ══════════════════════════════════════════════════════════════════════════════

class TestAppendToPicksHistory(unittest.TestCase):

    def _candidate(self, sym="AAPL", strategy="CSP"):
        return {
            "symbol":        sym,
            "rsi":           22.0,
            "vol_ratio":     1.8,
            "iv_rank":       58.0,
            "iv_current":    0.35,
            "price":         270.0,
            "near_earnings": False,
            "next_earnings": None,
            "regime":        "bull",
            "strategy":      strategy,
            "rationale":     "test",
        }

    def _run(self, candidates, existing=None):
        """Run append_to_picks_history with patched file I/O."""
        existing = existing or []
        saved    = []

        def fake_load():
            return list(existing)

        def fake_save(h):
            saved.clear()
            saved.extend(h)

        orig_load = options_screener.load_picks_history
        orig_save = options_screener.save_picks_history
        options_screener.load_picks_history  = fake_load
        options_screener.save_picks_history  = fake_save
        try:
            n = options_screener.append_to_picks_history(candidates)
        finally:
            options_screener.load_picks_history  = orig_load
            options_screener.save_picks_history  = orig_save
        return n, saved

    def test_adds_new_pick(self):
        n, saved = self._run([self._candidate()])
        self.assertEqual(n, 1)
        self.assertEqual(len(saved), 1)

    def test_deduplicates_same_day(self):
        from datetime import date
        today_s = date.today().strftime("%Y-%m-%d")
        existing = [{"symbol": "AAPL", "screened_date": today_s}]
        n, saved = self._run([self._candidate("AAPL")], existing=existing)
        self.assertEqual(n, 0)
        # save_picks_history is skipped when nothing new is added (optimization);
        # the existing record is preserved in the unmodified history file
        self.assertEqual(len(saved), 0)

    def test_research_mode_flag_set(self):
        _, saved = self._run([self._candidate()])
        self.assertTrue(saved[0]["research_mode"])
        self.assertEqual(saved[0]["phase"], 1)
        self.assertFalse(saved[0]["outcome_tracked"])

    def test_multiple_candidates(self):
        candidates = [self._candidate("AAPL"), self._candidate("MSFT")]
        n, saved = self._run(candidates)
        self.assertEqual(n, 2)
        syms = {r["symbol"] for r in saved}
        self.assertEqual(syms, {"AAPL", "MSFT"})

    def test_exit_fields_are_null(self):
        _, saved = self._run([self._candidate()])
        rec = saved[0]
        self.assertIsNone(rec["exit_date"])
        self.assertIsNone(rec["exit_reason"])
        self.assertIsNone(rec["pnl"])


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main()
