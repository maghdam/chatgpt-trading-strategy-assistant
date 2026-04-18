import unittest

from analysis import (
    detect_bos,
    detect_fvg,
    detect_ltf_entry,
    detect_sweep,
    detect_trend_bias,
    score_confluence,
    tag_sessions_local,
)


def candle(ts, open_, high, low, close):
    return {
        "time": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
    }


class AnalysisTests(unittest.TestCase):
    def test_detect_fvg_finds_recent_up_gap(self):
        candles = [
            candle("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            candle("2026-01-01T00:05:00+00:00", 100.0, 105.0, 100.0, 104.5),
            candle("2026-01-01T00:10:00+00:00", 105.5, 107.0, 103.5, 106.0),
        ]

        fvg = detect_fvg(candles)

        self.assertIsNotNone(fvg)
        self.assertEqual(fvg["type"], "up_fvg")
        self.assertEqual(fvg["low"], 101.0)
        self.assertEqual(fvg["high"], 103.5)

    def test_detect_sweep_requires_rejection_back_inside_level(self):
        candles = tag_sessions_local(
            [
                candle("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.5, 100.5),
                candle("2026-01-01T07:00:00+00:00", 100.5, 102.0, 100.0, 101.5),
                candle("2026-01-01T12:00:00+00:00", 101.5, 103.5, 100.8, 101.8),
                candle("2026-01-01T12:15:00+00:00", 101.8, 101.9, 98.5, 100.8),
            ]
        )

        sweeps = detect_sweep(candles, pdh=103.0, pdl=99.0, session_levels=None)

        self.assertIn("PDH sweep", sweeps["sweeps"])
        self.assertIn("PDL sweep", sweeps["sweeps"])

    def test_detect_trend_bias_returns_neutral_on_balanced_range(self):
        candles = [
            candle("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            candle("2026-01-01T00:05:00+00:00", 100.0, 102.0, 99.5, 101.0),
            candle("2026-01-01T00:10:00+00:00", 101.0, 101.5, 99.0, 100.0),
            candle("2026-01-01T00:15:00+00:00", 100.0, 102.0, 99.2, 101.0),
            candle("2026-01-01T00:20:00+00:00", 101.0, 101.4, 99.1, 100.0),
            candle("2026-01-01T00:25:00+00:00", 100.0, 102.0, 99.3, 101.0),
            candle("2026-01-01T00:30:00+00:00", 101.0, 101.6, 99.2, 100.0),
        ]

        self.assertEqual(detect_trend_bias(candles), "neutral")

    def test_detect_ltf_entry_requires_sweep_and_displacement(self):
        m15 = tag_sessions_local(
            [
                candle("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.6, 100.5),
                candle("2026-01-01T07:00:00+00:00", 100.5, 101.4, 100.1, 101.0),
                candle("2026-01-01T12:00:00+00:00", 101.0, 101.3, 99.2, 100.8),
                candle("2026-01-01T12:15:00+00:00", 100.8, 101.2, 98.7, 100.9),
                candle("2026-01-01T12:30:00+00:00", 100.9, 101.7, 100.4, 101.6),
                candle("2026-01-01T12:45:00+00:00", 101.6, 102.0, 101.0, 101.8),
            ]
        )
        m5 = [
            candle("2026-01-01T12:20:00+00:00", 100.1, 100.3, 99.5, 99.8),
            candle("2026-01-01T12:25:00+00:00", 99.8, 100.0, 99.2, 99.4),
            candle("2026-01-01T12:30:00+00:00", 99.4, 99.7, 99.0, 99.1),
            candle("2026-01-01T12:35:00+00:00", 99.1, 100.2, 98.9, 100.0),
            candle("2026-01-01T12:40:00+00:00", 100.0, 101.3, 99.8, 101.2),
        ]

        entry = detect_ltf_entry(m15, m5, pdh=103.0, pdl=99.0, session_levels={})

        self.assertEqual(entry["entry_type"], "bullish")
        self.assertGreater(entry["take_profit"], entry["entry_price"])
        self.assertLess(entry["stop_loss"], entry["entry_price"])

    def test_detect_bos_finds_continuation_break(self):
        candles = [
            candle("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.0, 100.0),
            candle("2026-01-01T00:05:00+00:00", 100.0, 102.0, 99.5, 101.5),
            candle("2026-01-01T00:10:00+00:00", 101.5, 104.0, 101.0, 103.5),
            candle("2026-01-01T00:15:00+00:00", 103.5, 102.5, 100.0, 100.5),
            candle("2026-01-01T00:20:00+00:00", 100.5, 103.0, 99.5, 102.0),
            candle("2026-01-01T00:25:00+00:00", 102.0, 105.0, 101.5, 104.6),
            candle("2026-01-01T00:30:00+00:00", 104.6, 104.6, 102.0, 103.2),
            candle("2026-01-01T00:35:00+00:00", 103.2, 105.1, 102.8, 104.8),
            candle("2026-01-01T00:40:00+00:00", 104.8, 104.5, 102.5, 103.3),
            candle("2026-01-01T00:45:00+00:00", 103.3, 104.0, 102.9, 103.8),
            candle("2026-01-01T00:50:00+00:00", 103.8, 106.4, 103.5, 106.0),
        ]

        bos = detect_bos(candles, macro_threshold=4)

        self.assertIsNotNone(bos)
        self.assertEqual(bos["minor"]["direction"], "bullish")

    def test_score_confluence_uses_weighted_components(self):
        checklist = {
            "CHOCH": {
                "macro": {"direction": "bullish"},
                "minor": {"direction": "bullish"},
            },
            "OB": {
                "macro": {"type": "bullish"},
                "minor": {"type": "bullish"},
            },
            "FVG": {"type": "up_fvg"},
            "Sweep": {"sweeps": ["PDL sweep"]},
            "Candle": {"type": "Bullish Engulfing"},
        }
        ltf_entry = {
            "entry_type": "bullish",
            "entry_price": 101.0,
            "stop_loss": 99.5,
            "take_profit": 104.0,
        }

        confluence = score_confluence("bullish", checklist, ltf_entry)

        self.assertEqual(confluence["score"], 100)
        self.assertTrue(confluence["eligible"])
        self.assertEqual(confluence["direction"], "bullish")


if __name__ == "__main__":
    unittest.main()
