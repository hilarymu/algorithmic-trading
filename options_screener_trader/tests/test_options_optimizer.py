"""tests/test_options_optimizer.py — Phase 3 optimizer unit tests."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "options_loop"))
import options_optimizer as oo


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_stats(n=15, win_rate=70, avg_pnl=0.8, avg_hold=15,
                loss_limit=2, profit_target=10, dte_reached=1,
                by_iv=None):
    """Build an outcome_stats dict with sensible defaults."""
    return {
        "n":             n,
        "win_rate_pct":  win_rate,
        "avg_pnl_pct":   avg_pnl,
        "avg_hold_days": avg_hold,
        "exit_reasons": {
            "loss_limit":    loss_limit,
            "profit_target": profit_target,
            "dte_reached":   dte_reached,
        },
        "by_iv_rank": by_iv or {},
    }


def _default_cfg():
    return {
        "indicators":         {"iv_rank_min_sell": 40},
        "contract_selection": {"target_delta_csp": 0.30},
        "exits":              {"profit_target_pct": 0.50, "close_at_dte": 21},
    }


# ══════════════════════════════════════════════════════════════════════════════
#  generate_insights() tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateInsights(unittest.TestCase):
    """generate_insights() — rule triggering and confidence labels."""

    # ── Gate ──────────────────────────────────────────────────────────────

    def test_no_insights_below_min(self):
        stats = _make_stats(n=oo.MIN_FOR_INSIGHTS - 1)
        self.assertEqual(oo.generate_insights(stats, _default_cfg()), [])

    def test_no_insights_at_zero(self):
        self.assertEqual(oo.generate_insights({"n": 0}, _default_cfg()), [])

    # ── Confidence labels ─────────────────────────────────────────────────

    def test_confidence_low_below_20(self):
        stats = _make_stats(n=15, avg_hold=5)   # triggers profit_target raise
        insights = oo.generate_insights(stats, _default_cfg())
        self.assertTrue(insights)
        self.assertTrue(all(i["confidence"] == "low" for i in insights))

    def test_confidence_medium_20_to_49(self):
        stats = _make_stats(n=25, avg_hold=5)
        insights = oo.generate_insights(stats, _default_cfg())
        self.assertTrue(insights)
        self.assertTrue(all(i["confidence"] == "medium" for i in insights))

    def test_confidence_high_at_50(self):
        stats = _make_stats(n=50, avg_hold=5)
        insights = oo.generate_insights(stats, _default_cfg())
        self.assertTrue(insights)
        self.assertTrue(all(i["confidence"] == "high" for i in insights))

    # ── IV rank minimum ────────────────────────────────────────────────────

    def test_iv_rank_raise_triggered(self):
        by_iv = {"40-55": {"n": 8, "win_rate": 30}}
        stats  = _make_stats(n=15, by_iv=by_iv)
        ins    = oo.generate_insights(stats, _default_cfg())
        iv_ins = next((i for i in ins if i["param"] == "iv_rank_min_sell"), None)
        self.assertIsNotNone(iv_ins)
        self.assertEqual(iv_ins["direction"], "raise")
        self.assertEqual(iv_ins["suggested"], 45)   # 40 + 5

    def test_iv_rank_not_triggered_win_rate_above_threshold(self):
        by_iv = {"40-55": {"n": 8, "win_rate": 60}}   # 60% > 40%
        stats  = _make_stats(n=15, by_iv=by_iv)
        ins    = oo.generate_insights(stats, _default_cfg())
        self.assertFalse(any(i["param"] == "iv_rank_min_sell" for i in ins))

    def test_iv_rank_not_triggered_insufficient_bucket(self):
        by_iv = {"40-55": {"n": 3, "win_rate": 20}}   # n < 5
        stats  = _make_stats(n=15, by_iv=by_iv)
        ins    = oo.generate_insights(stats, _default_cfg())
        self.assertFalse(any(i["param"] == "iv_rank_min_sell" for i in ins))

    def test_iv_rank_capped_at_upper_bound(self):
        by_iv = {"40-55": {"n": 6, "win_rate": 10}}
        cfg   = _default_cfg()
        cfg["indicators"]["iv_rank_min_sell"] = 70   # already at ceiling
        stats  = _make_stats(n=15, by_iv=by_iv)
        ins    = oo.generate_insights(stats, cfg)
        iv_ins = next((i for i in ins if i["param"] == "iv_rank_min_sell"), None)
        if iv_ins:
            self.assertLessEqual(iv_ins["suggested"], oo.BOUNDS["iv_rank_min_sell"][1])

    # ── Put delta (CSP strike) ────────────────────────────────────────────

    def test_delta_lowered_on_high_loss_limit(self):
        # loss_limit=4 of n=10 → 40% > 30%
        stats = _make_stats(n=10, loss_limit=4, profit_target=6)
        ins   = oo.generate_insights(stats, _default_cfg())
        d     = next((i for i in ins if i["param"] == "target_delta_csp"), None)
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d["suggested"], 0.25, places=2)   # 0.30 - 0.05
        self.assertIn("OTM", d["direction"])

    def test_delta_raised_on_high_win_rate(self):
        # win_rate=85, n=25 >= 20, loss_limit_pct=8% (not > 30%)
        stats = _make_stats(n=25, win_rate=85, loss_limit=2, profit_target=23)
        ins   = oo.generate_insights(stats, _default_cfg())
        d     = next((i for i in ins if i["param"] == "target_delta_csp"), None)
        self.assertIsNotNone(d)
        self.assertAlmostEqual(d["suggested"], 0.35, places=2)   # 0.30 + 0.05
        self.assertIn("premium", d["direction"])

    def test_delta_raise_requires_n_at_least_20(self):
        # win_rate=85 but only 15 trades
        stats = _make_stats(n=15, win_rate=85, loss_limit=0, profit_target=15)
        ins   = oo.generate_insights(stats, _default_cfg())
        self.assertFalse(any(i["param"] == "target_delta_csp" for i in ins))

    def test_delta_lower_takes_precedence_over_raise(self):
        # Both loss_limit > 30% AND win_rate > 80% — lower should win (elif)
        stats = _make_stats(n=25, win_rate=85, loss_limit=10, profit_target=15)
        ins   = [i for i in oo.generate_insights(stats, _default_cfg())
                 if i["param"] == "target_delta_csp"]
        self.assertLessEqual(len(ins), 1)   # only one delta insight
        if ins:
            self.assertIn("OTM", ins[0]["direction"])

    def test_delta_capped_at_lower_bound(self):
        cfg  = _default_cfg()
        cfg["contract_selection"]["target_delta_csp"] = 0.15   # at floor
        stats = _make_stats(n=10, loss_limit=5, profit_target=5)   # 50% loss
        ins   = oo.generate_insights(stats, cfg)
        d     = next((i for i in ins if i["param"] == "target_delta_csp"), None)
        if d:
            self.assertGreaterEqual(d["suggested"], oo.BOUNDS["target_delta_csp"][0])

    # ── Profit target ─────────────────────────────────────────────────────

    def test_profit_target_raised_on_short_hold(self):
        stats  = _make_stats(n=15, avg_hold=7)   # avg_hold < 10
        ins    = oo.generate_insights(stats, _default_cfg())
        p      = next((i for i in ins if i["param"] == "profit_target_pct"), None)
        self.assertIsNotNone(p)
        self.assertEqual(p["direction"], "raise")
        self.assertAlmostEqual(p["suggested"], 0.60, places=2)

    def test_profit_target_lowered_on_long_hold(self):
        stats  = _make_stats(n=15, avg_hold=35)  # avg_hold > 30
        ins    = oo.generate_insights(stats, _default_cfg())
        p      = next((i for i in ins if i["param"] == "profit_target_pct"), None)
        self.assertIsNotNone(p)
        self.assertEqual(p["direction"], "lower")
        self.assertAlmostEqual(p["suggested"], 0.40, places=2)

    def test_profit_target_neutral_on_normal_hold(self):
        stats = _make_stats(n=15, avg_hold=15)   # neither extreme
        ins   = oo.generate_insights(stats, _default_cfg())
        self.assertFalse(any(i["param"] == "profit_target_pct" for i in ins))

    def test_profit_target_capped_at_upper_bound(self):
        cfg  = _default_cfg()
        cfg["exits"]["profit_target_pct"] = 0.70   # at ceiling
        stats = _make_stats(n=15, avg_hold=5)
        ins   = oo.generate_insights(stats, cfg)
        p     = next((i for i in ins if i["param"] == "profit_target_pct"), None)
        if p:
            self.assertLessEqual(p["suggested"], oo.BOUNDS["profit_target_pct"][1])

    def test_profit_target_capped_at_lower_bound(self):
        cfg  = _default_cfg()
        cfg["exits"]["profit_target_pct"] = 0.35   # at floor
        stats = _make_stats(n=15, avg_hold=35)
        ins   = oo.generate_insights(stats, cfg)
        p     = next((i for i in ins if i["param"] == "profit_target_pct"), None)
        if p:
            self.assertGreaterEqual(p["suggested"], oo.BOUNDS["profit_target_pct"][0])

    # ── Close-at-DTE ──────────────────────────────────────────────────────

    def test_close_dte_raised_on_co_occurrence(self):
        # dte_reached > 0 AND loss_limit_pct = 4/15 = 27% > 20%
        stats  = _make_stats(n=15, dte_reached=2, loss_limit=4, profit_target=9)
        ins    = oo.generate_insights(stats, _default_cfg())
        dte_i  = next((i for i in ins if i["param"] == "close_at_dte"), None)
        self.assertIsNotNone(dte_i)
        self.assertEqual(dte_i["suggested"], 28)   # 21 + 7

    def test_close_dte_not_triggered_without_dte_exits(self):
        stats = _make_stats(n=15, dte_reached=0, loss_limit=5, profit_target=10)
        ins   = oo.generate_insights(stats, _default_cfg())
        self.assertFalse(any(i["param"] == "close_at_dte" for i in ins))

    def test_close_dte_not_triggered_loss_below_20pct(self):
        # loss_limit_pct = 1/15 = 6.7% < 20%
        stats = _make_stats(n=15, dte_reached=3, loss_limit=1, profit_target=11)
        ins   = oo.generate_insights(stats, _default_cfg())
        self.assertFalse(any(i["param"] == "close_at_dte" for i in ins))

    def test_close_dte_capped_at_upper_bound(self):
        cfg  = _default_cfg()
        cfg["exits"]["close_at_dte"] = 35   # at ceiling
        stats = _make_stats(n=15, dte_reached=2, loss_limit=4, profit_target=9)
        ins   = oo.generate_insights(stats, cfg)
        dte_i = next((i for i in ins if i["param"] == "close_at_dte"), None)
        if dte_i:
            self.assertLessEqual(dte_i["suggested"], oo.BOUNDS["close_at_dte"][1])


# ══════════════════════════════════════════════════════════════════════════════
#  apply_insights() tests
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyInsights(unittest.TestCase):
    """apply_insights() — gate, confidence filter, config update, applied list."""

    def _insight(self, param, suggested, confidence="high"):
        current_map = {
            "iv_rank_min_sell":  40,
            "target_delta_csp":  0.30,
            "profit_target_pct": 0.50,
            "close_at_dte":      21,
        }
        return {
            "param":      param,
            "current":    current_map.get(param, 0),
            "suggested":  suggested,
            "direction":  "raise",
            "reason":     "test reason",
            "confidence": confidence,
        }

    # ── Gate ──────────────────────────────────────────────────────────────

    def test_no_changes_below_min_for_changes(self):
        ins = [self._insight("iv_rank_min_sell", 45)]
        new_cfg, applied = oo.apply_insights(_default_cfg(), ins,
                                             oo.MIN_FOR_CHANGES - 1)
        self.assertEqual(applied, [])
        self.assertEqual(new_cfg["indicators"]["iv_rank_min_sell"], 40)

    def test_no_changes_on_empty_insights(self):
        new_cfg, applied = oo.apply_insights(_default_cfg(), [], oo.MIN_FOR_CHANGES)
        self.assertEqual(applied, [])

    # ── Confidence filter ─────────────────────────────────────────────────

    def test_high_confidence_applied(self):
        ins = [self._insight("iv_rank_min_sell", 45, "high")]
        new_cfg, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(len(applied), 1)
        self.assertEqual(new_cfg["indicators"]["iv_rank_min_sell"], 45)

    def test_medium_confidence_not_applied(self):
        ins = [self._insight("iv_rank_min_sell", 45, "medium")]
        new_cfg, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(applied, [])
        self.assertEqual(new_cfg["indicators"]["iv_rank_min_sell"], 40)

    def test_low_confidence_not_applied(self):
        ins = [self._insight("profit_target_pct", 0.60, "low")]
        new_cfg, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(applied, [])

    def test_mixed_confidence_only_high_applied(self):
        ins = [
            self._insight("iv_rank_min_sell", 45, "high"),
            self._insight("close_at_dte", 28, "medium"),
        ]
        _, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0]["param"], "iv_rank_min_sell")

    # ── Config update correctness ─────────────────────────────────────────

    def test_all_four_params_applied_correctly(self):
        ins = [
            self._insight("iv_rank_min_sell",  45,   "high"),
            self._insight("target_delta_csp",  0.25, "high"),
            self._insight("profit_target_pct", 0.60, "high"),
            self._insight("close_at_dte",      28,   "high"),
        ]
        new_cfg, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(len(applied), 4)
        self.assertEqual(new_cfg["indicators"]["iv_rank_min_sell"], 45)
        self.assertAlmostEqual(new_cfg["contract_selection"]["target_delta_csp"], 0.25, places=2)
        self.assertAlmostEqual(new_cfg["exits"]["profit_target_pct"], 0.60, places=2)
        self.assertEqual(new_cfg["exits"]["close_at_dte"], 28)

    def test_multiple_high_insights_all_applied(self):
        ins = [
            self._insight("iv_rank_min_sell",  45,   "high"),
            self._insight("profit_target_pct", 0.60, "high"),
        ]
        new_cfg, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(len(applied), 2)

    # ── Applied list fields ────────────────────────────────────────────────

    def test_applied_list_contains_required_fields(self):
        ins = [self._insight("target_delta_csp", 0.25, "high")]
        _, applied = oo.apply_insights(_default_cfg(), ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(len(applied), 1)
        ch = applied[0]
        for field in ("param", "from", "to", "reason", "at"):
            self.assertIn(field, ch)
        self.assertEqual(ch["to"], 0.25)

    # ── Immutability ──────────────────────────────────────────────────────

    def test_original_cfg_not_mutated(self):
        cfg = _default_cfg()
        original_val = cfg["indicators"]["iv_rank_min_sell"]
        ins = [self._insight("iv_rank_min_sell", 45, "high")]
        oo.apply_insights(cfg, ins, oo.MIN_FOR_CHANGES)
        self.assertEqual(cfg["indicators"]["iv_rank_min_sell"], original_val)


# ══════════════════════════════════════════════════════════════════════════════
#  load_config / save_config round-trip
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigRoundTrip(unittest.TestCase):
    """load_config() / save_config() — disk persistence and error handling."""

    def test_save_and_reload(self):
        cfg = {"key": "value", "nested": {"a": 1, "b": 2.5}}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                        mode="w") as tf:
            tf_path = Path(tf.name)
        try:
            with patch.object(oo, "CONFIG_PATH", tf_path):
                oo.save_config(cfg)
                loaded = oo.load_config()
            self.assertEqual(loaded, cfg)
        finally:
            tf_path.unlink(missing_ok=True)

    def test_load_missing_file_returns_empty(self):
        with patch.object(oo, "CONFIG_PATH", Path("/nonexistent/path/cfg.json")):
            result = oo.load_config()
        self.assertEqual(result, {})

    def test_load_corrupt_json_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                        mode="w") as tf:
            tf.write("{not valid json at all")
            tf_path = Path(tf.name)
        try:
            with patch.object(oo, "CONFIG_PATH", tf_path):
                result = oo.load_config()
            self.assertEqual(result, {})
        finally:
            tf_path.unlink(missing_ok=True)

    def test_save_preserves_nested_structure(self):
        cfg = _default_cfg()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False,
                                        mode="w") as tf:
            tf_path = Path(tf.name)
        try:
            with patch.object(oo, "CONFIG_PATH", tf_path):
                oo.save_config(cfg)
                loaded = oo.load_config()
            self.assertEqual(loaded["exits"]["profit_target_pct"], 0.50)
            self.assertEqual(loaded["indicators"]["iv_rank_min_sell"], 40)
        finally:
            tf_path.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  run() smoke tests
# ══════════════════════════════════════════════════════════════════════════════

class TestOptimizerRun(unittest.TestCase):
    """run() — end-to-end with patched file I/O."""

    def _write_signal(self, tmp_path, n_closed=0, win_rate=0, avg_hold=None,
                      by_iv=None, loss_limit=0, dte_reached=0):
        data = {
            "outcome_stats": {
                "n":             n_closed,
                "status":        "no_data" if n_closed == 0 else "active",
                "win_rate_pct":  win_rate,
                "avg_pnl_pct":   1.0,
                "avg_hold_days": avg_hold,
                "exit_reasons": {
                    "loss_limit":    loss_limit,
                    "profit_target": max(n_closed - loss_limit, 0),
                    "dte_reached":   dte_reached,
                },
                "by_iv_rank": by_iv or {},
            },
            "sell_zone_pct": 68,
            "regime":        "bull",
            "candidates":    [],
        }
        sig_path = Path(tmp_path) / "options_signal_quality.json"
        sig_path.write_text(json.dumps(data))
        return sig_path

    def _patch_paths(self, tmp, sig_path=None):
        tmp = Path(tmp)
        return (
            patch.object(oo, "SIGNAL_PATH", sig_path or tmp / "missing.json"),
            patch.object(oo, "REPORT_PATH", tmp / "options_improvement_report.json"),
            patch.object(oo, "CONFIG_PATH", tmp / "options_config.json"),
        )

    # ── Bootstrapping (0 closed positions) ────────────────────────────────

    def test_bootstrapping_returns_zero_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig = self._write_signal(tmp, n_closed=0)
            with self._patch_paths(tmp, sig)[0], \
                 self._patch_paths(tmp, sig)[1], \
                 self._patch_paths(tmp, sig)[2]:
                result = oo.run(auto_optimize=False)
        self.assertEqual(result["n_closed"], 0)
        self.assertEqual(result["n_insights"], 0)
        self.assertEqual(result["n_applied"], 0)

    # ── Sparse (<MIN_FOR_INSIGHTS) ─────────────────────────────────────────

    def test_sparse_no_insights(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig = self._write_signal(tmp, n_closed=oo.MIN_FOR_INSIGHTS - 1)
            p0, p1, p2 = self._patch_paths(tmp, sig)
            with p0, p1, p2:
                result = oo.run(auto_optimize=False)
        self.assertEqual(result["n_insights"], 0)

    # ── Report file written ────────────────────────────────────────────────

    def test_report_written_with_required_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            sig = self._write_signal(tmp, n_closed=0)
            report_path = Path(tmp) / "options_improvement_report.json"
            p0, p1, p2 = self._patch_paths(tmp, sig)
            with p0, p1, p2:
                oo.run(auto_optimize=False)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text())
            for key in ("generated_at", "pipeline_phase", "n_closed_positions",
                        "current_insights", "applied_this_run"):
                self.assertIn(key, report)
            self.assertEqual(report["pipeline_phase"], 3)

    # ── auto_optimize=False never applies changes ─────────────────────────

    def test_auto_optimize_false_no_changes_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            by_iv = {"40-55": {"n": 8, "win_rate": 20}}
            sig   = self._write_signal(tmp, n_closed=50, win_rate=40,
                                       avg_hold=35, by_iv=by_iv)
            cfg_path = Path(tmp) / "options_config.json"
            cfg_path.write_text(json.dumps(_default_cfg()))
            p0, p1, p2 = self._patch_paths(tmp, sig)
            with p0, p1, p2:
                result = oo.run(auto_optimize=False)
        self.assertEqual(result["n_applied"], 0)

    # ── Missing signal file is handled gracefully ─────────────────────────

    def test_missing_signal_file_returns_zero_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p0, p1, p2 = self._patch_paths(tmp)   # sig_path → missing.json
            with p0, p1, p2:
                result = oo.run(auto_optimize=False)
        self.assertEqual(result["n_closed"], 0)
        self.assertEqual(result["n_insights"], 0)

    # ── auto_optimize=True applies high-confidence when n >= MIN_FOR_CHANGES

    def test_auto_optimize_true_applies_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            # avg_hold=5 → profit_target raise insight; n=50 → high confidence
            sig = self._write_signal(tmp, n_closed=50, avg_hold=5)
            cfg_path = Path(tmp) / "options_config.json"
            cfg_path.write_text(json.dumps(_default_cfg()))
            report_path = Path(tmp) / "options_improvement_report.json"
            with (patch.object(oo, "SIGNAL_PATH", sig),
                  patch.object(oo, "REPORT_PATH", report_path),
                  patch.object(oo, "CONFIG_PATH", cfg_path)):
                result = oo.run(auto_optimize=True)
            self.assertGreater(result["n_applied"], 0)
            # Config on disk should have been updated (read while tmpdir still open)
            updated_cfg = json.loads(cfg_path.read_text())
            self.assertAlmostEqual(
                updated_cfg["exits"]["profit_target_pct"], 0.60, places=2)


if __name__ == "__main__":
    unittest.main()
