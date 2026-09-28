from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from batch_demo import DemoClient, PACKAGE, demo_payloads
from batch_engine import BatchEngine, History, PENDING, RETRYABLE, previous_delta, redact
from detector_client import ImportIssue


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = History(Path(self.temp.name))

    def test_bounded_parallel_results_and_secret_free_history(self):
        client = DemoClient(steps=2)
        result = BatchEngine(client, self.history, interval=0).run(demo_payloads(), PACKAGE, "gpt-6-astra")
        self.assertEqual(client.max_active, 2)
        self.assertEqual([r["status"] for r in result["rows"]], ["complete", "complete", "partial", "failed"])
        self.assertEqual([r["verdict"] for r in result["rows"][:3]], ["match", "mismatch", "insufficient"])
        self.assertEqual(result["rows"][1]["top"], "gpt-5.6-sol")
        self.assertIsNotNone(result["finished_at"])
        saved = list(Path(self.temp.name).glob("*.json"))[0].read_text(encoding="utf-8")
        self.assertNotIn("fake-demo-key", saved)
        self.assertNotIn("https://demo", saved)
        self.assertEqual(len(self.history.all()), 1)

    def test_stop_cancels_queue_and_stops_only_owned_sessions(self):
        client = DemoClient(steps=100)
        engine = BatchEngine(client, self.history, interval=0)
        def emit(batch):
            if any(r.get("completed", 0) > 0 for r in batch["rows"]):
                engine.stop()
        engine.emit = emit
        result = engine.run(demo_payloads(), PACKAGE, "gpt-6-astra")
        self.assertEqual([r["status"] for r in result["rows"]], ["stopped", "stopped", "cancelled", "cancelled"])
        self.assertEqual(len(client.sessions), 2)
        self.assertEqual(client.calls.count("/api/run/stop"), 2)

    def test_unknown_start_never_retries_post_or_starts_next_route(self):
        class Timeout(DemoClient):
            def request(self, path, body=None):
                if path == "/api/run/start":
                    self.calls.append(path)
                    raise ImportIssue("Connection timed out")
                return super().request(path, body)
        client = Timeout()
        result = BatchEngine(client, self.history, interval=0).run(demo_payloads(), PACKAGE, "gpt-6-astra")
        self.assertEqual(client.calls.count("/api/run/start"), 1)
        self.assertEqual(result["rows"][0]["status"], "unknown")
        self.assertIsNone(result["finished_at"])

    def test_recover_known_sessions_queries_without_starting_again(self):
        class Disconnect(DemoClient):
            disconnected = True
            def request(self, path, body=None):
                if path.startswith("/api/report/") and self.disconnected:
                    raise ImportIssue("offline")
                return super().request(path, body)
        client = Disconnect(steps=1)
        first = BatchEngine(client, self.history, interval=0).run(demo_payloads(), PACKAGE, "gpt-6-astra")
        self.assertIsNone(first["finished_at"])
        first["request_model"] = "legacy-custom-alias"
        starts = client.calls.count("/api/run/start")
        client.disconnected = False
        result = BatchEngine(client, self.history, interval=0).run(demo_payloads(), PACKAGE, "gpt-6-astra", existing=first)
        self.assertEqual(client.calls.count("/api/run/start"), starts)
        self.assertEqual(result["request_model"], "legacy-custom-alias")
        self.assertFalse(any(r["status"] in PENDING for r in result["rows"]))

    def test_delta_only_compares_same_baseline_and_settings(self):
        client = DemoClient(steps=1)
        previous = BatchEngine(client, self.history, interval=0).run(demo_payloads()[:1], PACKAGE, "gpt-6-astra")
        current = deepcopy(previous)
        current["id"] = "b" * 32
        row = current["rows"][0]
        row["claimed_score"] += .03
        self.assertAlmostEqual(previous_delta(current, row, [previous]), 3)
        current["benchmark"]["content_sha256"] = "changed"
        self.assertIsNone(previous_delta(current, row, [previous]))

    def test_deep_redaction(self):
        self.assertEqual(redact({"secret-key": ["text secret-key"]}, ["secret-key"]),
                         {"[redacted]": ["text [redacted]"]})

    def test_restart_without_saved_report_uses_paused_progress(self):
        client = DemoClient(steps=1)
        first = BatchEngine(client, self.history, interval=0).run(demo_payloads()[:1], PACKAGE, "gpt-6-astra")
        first["rows"][0]["status"] = "unknown"
        class Restarted(DemoClient):
            def request(self, path, body=None):
                if path.startswith("/api/report/"):
                    raise ImportIssue("Local detector returned HTTP 404.")
                if path.startswith("/api/progress/"):
                    return {"status": "paused", "planned": 36, "logical_completed": 2,
                            "valid_samples": 2, "errors": 0, "http_attempts": 2}
                return super().request(path, body)
        result = BatchEngine(Restarted(), self.history, interval=0).run(demo_payloads()[:1], PACKAGE, "gpt-6-astra", existing=first)
        self.assertEqual(result["rows"][0]["status"], "stopped")
        self.assertIsNotNone(result["finished_at"])

    def test_recover_starting_without_id_does_not_resubmit(self):
        client = DemoClient(steps=1)
        first = BatchEngine(client, self.history, interval=0).run(demo_payloads()[:1], PACKAGE, "gpt-6-astra")
        first["rows"][0].update(status="starting", session_id=None)
        client.calls.clear()
        result = BatchEngine(client, self.history, interval=0).run(demo_payloads()[:1], PACKAGE, "gpt-6-astra", existing=first)
        self.assertEqual(result["rows"][0]["status"], "unknown")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
