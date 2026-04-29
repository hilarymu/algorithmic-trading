"""tests/test_options_signal_analyzer.py — Phase 3 signal analyzer unit tests."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "options_loop"))
import options_signal_analyzer as sa


class TestSignalStrength(unittest.TestCase):
    """signal_strength() scoring function."""

    def test_perfect_score(self):
        # IV rank 100, RSI 0, vol 2.5x, no earnings
        s = sa.signal_strength(100, 0, 2.5, False)
        self.assertEqual(s, 90.0)   # 40 + 30 + 20

    def test_earnings_penalty(self):
        s_no  = sa.signal_strength(80, 20, 2.0, False)
        s_yes = sa.signal_strength(80, 20, 2.0, True)
        self.assertEqual(s_no - s_yes, 10.0)

    def test_rsi_at_threshold_zero(self):
        # RSI >= 25 → rsi_score = 0
        s = sa.signal_strength(50, 25, 1.0, False)
        iv_part  = 50 / 100 * 40
        vol_part = 1.0 / 2.5 * 20
        self.assertAlmostEqual(s, iv_part + vol_part, places=1)

    def test_vol_capped_at_2_5x(self):
        s_high = sa.signal_strength(60, 20, 5.0, False)   # vol way above cap
        s_cap  = sa.signal_strength(60, 20, 2.5, False)   # vol at cap
        self.assertEqual(s_high, s_cap)

    def test_typical_candidate(self):
        # TSCO-like: IV rank 100, RSI 20.5, vol 1.99
        s = sa.signal_strength(100, 20.5, 1.99, False)
        self.assertGreater(s, 50)
        self.assertLessEqual(s, 90)

    def test_score_range(self):
        for iv in (0, 40, 70, 100):
            for rsi in (5, 15, 24):
                for vol in (0.5, 1.5, 3.0):
                    s = sa.signal_strength(iv, rsi, vol, False)
                    self.assertGreaterEqual(s, 0)
                    self.assertLessEqual(s, 90)


class TestEstPremiumYield(unittest.TestCase):
    """est_premium_yield() theoretical BSM put premium."""

    def test_reasonable_output(self):
        # S=100, IV=35%, DTE=35, delta=0.30
        r = sa.est_premium_yield(100, 0.35, 35, 0.30)
        self.assertIsNotNone(r["strike"])
        self.assertGreater(r["premium_pct"], 0.5)    # at least 0.5% premium
        self.assertLess(r["premium_pct"], 8.0)        # not absurdly high
        self.assertGreater(r["annual_yield_pct"], 3)  # at least 3%/yr

    def test_higher_iv_gives_more_premium(self):
        lo = sa.est_premium_yield(100, 0.20, 35, 0.30)
        hi = sa.est_premium_yield(100, 0.60, 35, 0.30)
        self.assertGreater(hi["premium_pct"], lo["premium_pct"])

    def test_longer_dte_gives_more_premium(self):
        short = sa.est_premium_yield(100, 0.35, 21, 0.30)
        long_ = sa.est_premium_yield(100, 0.35, 50, 0.30)
        self.assertGreater(long_["premium_pct"], short["premium_pct"])

    def test_longer_dte_gives_less_annual_yield(self):
        # Short DTE has higher annualized yield (theta decay advantage)
        short = sa.est_premium_yield(100, 0.35, 21, 0.30)
        long_ = sa.est_premium_yield(100, 0.35, 50, 0.30)
        self.assertGreater(short["annual_yield_pct"], long_["annual_yield_pct"])

    def test_invalid_inputs_return_none(self):
        r = sa.est_premium_yield(0, 0.35, 35, 0.30)
        self.assertIsNone(r["premium_pct"])
        r2 = sa.est_premium_yield(100, 0, 35, 0.30)
        self.assertIsNone(r2["premium_pct"])
        r3 = sa.est_premium_yield(100, 0.35, 0, 0.30)
        self.assertIsNone(r3["premium_pct"])


class TestIvRankDistribution(unittest.TestCase):
    """iv_rank_distribution() bucket counts."""

    def _make_cache(self, ranks):
        return {f"SYM{i}": {"iv_rank": r} for i, r in enumerate(ranks)}

    def test_all_buckets_correct(self):
        cache = self._make_cache([42, 57, 72, 90, 100, 30, 10])
        dist = sa.iv_rank_distribution(cache)
        self.assertEqual(dist["40-55"]["count"], 1)
        self.assertEqual(dist["55-70"]["count"], 1)
        self.assertEqual(dist["70-85"]["count"], 1)
        self.assertEqual(dist["85-100"]["count"], 2)   # 90 and 100
        self.assertEqual(dist["<40"]["count"], 2)      # 30 and 10

    def test_pct_sums_to_100(self):
        cache = self._make_cache(list(range(0, 105, 5)))
        dist = sa.iv_rank_distribution(cache)
        total_pct = sum(v["pct"] for v in dist.values())
        self.assertAlmostEqual(total_pct, 100.0, delta=1.0)

    def test_empty_bucket(self):
        cache = self._make_cache([10, 20, 30])
        dist = sa.iv_rank_distribution(cache)
        self.assertEqual(dist["40-55"]["count"], 0)
        self.assertEqual(dist["40-55"]["pct"], 0.0)

    def test_symbols_without_rank_excluded(self):
        cache = {"A": {"iv_rank": 50}, "B": {"iv_rank": None}, "C": {}}
        dist = sa.iv_rank_distribution(cache)
        self.assertEqual(dist["40-55"]["count"], 1)


class TestAnalyzeClosedPositions(unittest.TestCase):
    """analyze_closed_positions() outcome statistics."""

    def _pos(self, pnl, hold, strategy="CSP", regime="bull",
             iv_rank=70, rsi=20, reason="profit_target"):
        return {
            "pnl_pct":         pnl,
            "hold_days":       hold,
            "strategy":        strategy,
            "regime":          regime,
            "iv_rank_at_entry": iv_rank,
            "rsi_at_entry":    rsi,
            "exit_reason":     reason,
        }

    def test_empty_returns_no_data(self):
        r = sa.analyze_closed_positions([])
        self.assertEqual(r["n"], 0)
        self.assertEqual(r["status"], "no_data")

    def test_win_rate_correct(self):
        pos = [self._pos(0.3, 15)] * 7 + [self._pos(-0.5, 20)] * 3
        r = sa.analyze_closed_positions(pos)
        self.assertEqual(r["win_rate_pct"], 70.0)
        self.assertEqual(r["n"], 10)

    def test_avg_pnl_correct(self):
        # Two positions: +30% and -10% → avg +10%
        pos = [self._pos(0.30, 15), self._pos(-0.10, 10)]
        r = sa.analyze_closed_positions(pos)
        self.assertAlmostEqual(r["avg_pnl_pct"], 10.0, places=1)

    def test_annualised_yield(self):
        # avg P&L 10% over 15 days → ann = 10 * 252/15 = 168%
        pos = [self._pos(0.10, 15)] * 5
        r = sa.analyze_closed_positions(pos)
        self.assertAlmostEqual(r["ann_yield_pct"], round(10 * 252 / 15, 1), places=0)

    def test_exit_reason_count(self):
        pos = (
            [self._pos(0.3, 15, reason="profit_target")] * 3 +
            [self._pos(-0.5, 20, reason="loss_limit")] * 2
        )
        r = sa.analyze_closed_positions(pos)
        self.assertEqual(r["exit_reasons"]["profit_target"], 3)
        self.assertEqual(r["exit_reasons"]["loss_limit"], 2)

    def test_bucket_stats_require_min_outcomes(self):
        # Only 2 positions in 40-55 bucket; MIN_OUTCOMES = 5 → no bucket stats
        pos = [self._pos(0.3, 15, iv_rank=45)] * 2
        r = sa.analyze_closed_positions(pos)
        self.assertNotIn("40-55", r.get("by_iv_rank", {}))

    def test_bucket_stats_appear_above_min(self):
        pos = [self._pos(0.3, 15, iv_rank=45)] * sa.MIN_OUTCOMES_FOR_STATS
        r = sa.analyze_closed_positions(pos)
        self.assertIn("40-55", r["by_iv_rank"])
        self.assertEqual(r["by_iv_rank"]["40-55"]["win_rate"], 100.0)

    def test_status_sparse_below_threshold(self):
        pos = [self._pos(0.3, 15)] * 3
        r = sa.analyze_closed_positions(pos)
        self.assertEqual(r["status"], "sparse")

    def test_status_active_at_threshold(self):
        pos = [self._pos(0.3, 15)] * sa.MIN_OUTCOMES_FOR_STATS
        r = sa.analyze_closed_positions(pos)
        self.assertEqual(r["status"], "active")


class TestBSMHelpers(unittest.TestCase):
    """Inlined BSM helpers (norm_inv, put_strike_for_delta, bs_put_price)."""

    def test_norm_inv_standard_values(self):
        self.assertAlmostEqual(sa._norm_inv(0.5),  0.0,   places=2)
        self.assertAlmostEqual(sa._norm_inv(0.975), 1.96,  places=1)
        self.assertAlmostEqual(sa._norm_inv(0.025), -1.96, places=1)

    def test_put_price_positive(self):
        price = sa._bs_put_price(100, 95, 0.1389, 0.05, 0.30)
        self.assertGreater(price, 0)

    def test_put_price_intrinsic_lower_bound(self):
        # Deep ITM put: intrinsic = K - S
        price = sa._bs_put_price(90, 110, 0.1389, 0.05, 0.30)
        self.assertGreater(price, 110 - 90 - 1)  # near intrinsic

    def test_put_strike_reasonable(self):
        # 0.30 delta put for S=100, IV=35%, DTE=35
        K = sa._put_strike_for_delta(100, 0.35, 35/252, 0.30)
        self.assertGreater(K, 70)     # not absurdly low
        self.assertLess(K, 100)       # OTM (below spot)


if __name__ == "__main__":
    unittest.main()
