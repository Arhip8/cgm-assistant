import unittest

from event_analyzer import analyze_measurement, validate_rules

R = "run-test"
BASE = "2026-11-12T10:"


def m(minute, value, index=None, run_id=R):
    return {"run_id": run_id, "source_index": minute if index is None else index,
            "measured_at": f"2026-11-12T10:{minute:02d}:00+03:00", "glucose_mmol_l": value}


class AnalyzerTests(unittest.TestCase):
    def test_start_one_episode_and_no_repeat(self):
        a = analyze_measurement(m(0, 10.2))
        b = analyze_measurement(m(5, 10.4), state=a["next_state"])
        self.assertEqual([(e["event_type"], e["phase"]) for e in a["events"]],
                         [("demo_above_range", "started")])
        self.assertEqual(b["events"], [])

    def test_hysteresis_and_same_event_id_on_end(self):
        a = analyze_measurement(m(0, 10.3))
        b = analyze_measurement(m(5, 9.9), state=a["next_state"])
        c = analyze_measurement(m(10, 9.5), state=b["next_state"])
        self.assertEqual(b["events"], [])
        self.assertEqual(c["events"][0]["phase"], "ended")
        self.assertEqual(c["events"][0]["event_id"], a["events"][0]["event_id"])

    def test_low_episode(self):
        a = analyze_measurement(m(0, 3.8))
        self.assertEqual(a["events"][0]["event_type"], "demo_below_range")
        b = analyze_measurement(m(5, 4.5), state=a["next_state"])
        self.assertEqual(b["events"][0]["phase"], "ended")

    def test_rapid_rise_requires_two_intervals(self):
        a = analyze_measurement(m(0, 5))
        b = analyze_measurement(m(5, 6.5), state=a["next_state"])
        self.assertFalse(any(e["event_type"] == "demo_rapid_rise" for e in b["events"]))
        c = analyze_measurement(m(10, 8), state=b["next_state"])
        self.assertTrue(any(e["event_type"] == "demo_rapid_rise" and e["phase"] == "started"
                            for e in c["events"]))
        d = analyze_measurement(m(15, 8.1), state=c["next_state"])
        self.assertTrue(any(e["event_type"] == "demo_rapid_rise" and e["phase"] == "ended"
                            for e in d["events"]))

    def test_rapid_fall_requires_two_intervals(self):
        a = analyze_measurement(m(0, 9))
        b = analyze_measurement(m(5, 7.5), state=a["next_state"])
        c = analyze_measurement(m(10, 6), state=b["next_state"])
        self.assertIn("demo_rapid_fall", [e["event_type"] for e in c["events"]])

    def test_gap_interrupts_episode(self):
        a = analyze_measurement(m(0, 11))
        b = analyze_measurement(m(30, 11), state=a["next_state"])
        phases = [(e["event_type"], e["phase"]) for e in b["events"]]
        self.assertIn(("demo_above_range", "interrupted"), phases)
        self.assertIn(("data_gap", "observed"), phases)
        self.assertIn(("demo_above_range", "started"), phases)
        self.assertIsNone(b["metrics"]["rate_mmol_l_min"])

    def test_duplicate_keeps_state_and_no_events(self):
        a = analyze_measurement(m(0, 6))
        b = analyze_measurement(m(0, 7), state=a["next_state"])
        self.assertEqual(b["status"], "duplicate")
        self.assertEqual(b["next_state"], a["next_state"])
        self.assertEqual(b["events"], [])

    def test_out_of_order_keeps_state(self):
        a = analyze_measurement(m(10, 6))
        b = analyze_measurement(m(5, 7), state=a["next_state"])
        self.assertEqual(b["status"], "out_of_order")
        self.assertEqual(b["next_state"], a["next_state"])

    def test_history_cold_start(self):
        a = analyze_measurement(m(5, 7), history=[m(0, 6)])
        self.assertEqual(a["metrics"]["delta_mmol_l"], 1)
        self.assertEqual(a["metrics"]["minutes_since_previous"], 5)

    def test_run_state_mismatch(self):
        a = analyze_measurement(m(0, 7))
        with self.assertRaises(ValueError):
            analyze_measurement(m(5, 7, run_id="other"), state=a["next_state"])

    def test_naive_timestamp_rejected(self):
        x = m(0, 7); x["measured_at"] = "2026-11-12T10:00:00"
        with self.assertRaises(ValueError):
            analyze_measurement(x)

    def test_invalid_value(self):
        with self.assertRaises(ValueError):
            analyze_measurement(m(0, -1))

    def test_invalid_rules(self):
        with self.assertRaises(ValueError):
            validate_rules({"lower_exit": 11})

    def test_determinism(self):
        self.assertEqual(analyze_measurement(m(0, 11)), analyze_measurement(m(0, 11)))

    def test_json_serializable(self):
        import json
        json.dumps(analyze_measurement(m(0, 11)))

    def test_not_emit_every_5_minutes(self):
        state = None
        events = []
        for i, v in enumerate((10.2, 10.3, 10.4, 10.5)):
            res = analyze_measurement(m(i*5, v), state=state)
            state = res["next_state"]
            events += [e for e in res["events"] if e["event_type"] == "demo_above_range" and e["phase"] == "started"]
        self.assertEqual(len(events), 1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
