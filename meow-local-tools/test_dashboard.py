import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from batch_demo import DemoClient, PACKAGE, CLAUDE_PACKAGE
from dashboard import Server, Workbench
from provider_store import ProviderStore
from batch_engine import choose_package
from detector_client import ImportIssue


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProviderStore(Path(self.temp.name) / "providers.toml")

    def add(self, name="Route A", api="https://relay.invalid/v1", key="fake-test-secret"):
        return self.store.mutate("add", {"revision": self.store.revision(), "name": name, "api": api, "key": key})

    def test_crud_roundtrip_empty_key_retained_and_last_delete(self):
        state = self.add(name='Route "A"')
        self.assertNotIn("fake-test-secret", json.dumps(state))
        identity = state["providers"][0]["id"]
        edited = self.store.mutate("edit", {"revision": state["revision"], "id": identity,
            "name": "Renamed", "api": "https://new.invalid/v1", "key": ""})
        self.assertEqual(self.store.read()[1][0]["key"], "fake-test-secret")
        self.assertNotIn("fake-test-secret", json.dumps(edited))
        final = self.store.mutate("delete", {"revision": edited["revision"], "id": edited["providers"][0]["id"]})
        self.assertEqual(final["providers"], [])
        self.assertEqual(self.store.read(), ("gpt-6-astra", []))

    def test_conflicts_and_invalid_changes_do_not_touch_file(self):
        state = self.add()
        original = self.store.path.read_bytes()
        for data in [{"revision": "stale", "name": "B", "api": "https://relay.invalid", "key": "safe-test"},
                     {"revision": state["revision"], "name": "Route A", "api": "https://relay.invalid", "key": "safe-test"},
                     {"revision": state["revision"], "name": "B", "api": "http://relay.invalid", "key": "safe-test"}]:
            with self.assertRaises(ImportIssue):
                self.store.mutate("add", data)
            self.assertEqual(original, self.store.path.read_bytes())
        self.assertFalse(list(self.store.path.parent.glob(".providers-*")))

    def test_secret_in_another_public_field_is_rejected(self):
        self.add()
        with self.assertRaises(ImportIssue):
            self.add(name="fake-test-secret", key="another-secret")

    def test_claude_crud_keeps_openai_config_unchanged(self):
        original = self.add()
        original_bytes = self.store.path.read_bytes()
        claude = ProviderStore(self.store.path.parent / "claude.toml", mode="claude")
        added = claude.mutate("add", {"revision": claude.revision(), "name": "Route A",
            "api": "https://claude.invalid/v1", "key": "fake-claude-secret"})
        identity = added["providers"][0]["id"]
        self.assertNotEqual(identity, original["providers"][0]["id"])
        self.assertEqual(claude.read()[1][0]["preset"]["mode"], "claude")
        edited = claude.mutate("edit", {"revision": added["revision"], "id": identity,
            "name": "Route A", "api": "https://edited.invalid/v1", "key": ""})
        self.assertEqual(claude.read()[1][0]["key"], "fake-claude-secret")
        model = claude.mutate("model", {"revision": edited["revision"], "model": "custom-claude"})
        self.assertEqual(claude.read()[1][0]["preset"]["model"], "custom-claude")
        claude.mutate("delete", {"revision": model["revision"], "id": identity})
        self.assertEqual(claude.public()["providers"], [])
        self.assertEqual(self.store.path.read_bytes(), original_bytes)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = DemoClient(steps=2)
        self.app = Workbench(Path(self.temp.name), demo=True, client=self.client)
        self.app.packages = {"gpt": PACKAGE, "claude": CLAUDE_PACKAGE}
        self.store = self.app.stores["gpt"]
        for i in range(3):
            self.store.mutate("add", {"revision": self.store.revision(), "name": "Route " + str(i),
                "api": f"https://demo-{i}.invalid/v1", "key": "fake-private-key"})

    def body(self, count=1):
        return {"ids": [p["id"] for p in self.store.public()["providers"]][:count],
                "revision": self.store.revision(), "claimed": "gpt-6-astra", "tier": "low", "parallel": 2}

    def test_opening_and_estimates_never_start_tests(self):
        self.app.connect()
        deadline = time.monotonic() + 3
        while self.app.connecting and time.monotonic() < deadline:
            time.sleep(.01)
        self.app.state()
        self.app.estimate("low")
        self.assertFalse(self.client.sessions)
        self.assertNotIn("fake-private-key", json.dumps(self.app.state()))

    def test_only_selected_providers_run_and_mutation_blocked_during_run(self):
        self.app.launch(self.body(2))
        with self.assertRaises(ImportIssue):
            self.app.mutate("delete", {})
        with self.assertRaises(ImportIssue):
            self.app.launch(self.body())
        self.app.worker.join(5)
        self.assertFalse(self.app.busy)
        self.assertEqual(len(self.client.sessions), 2)
        self.assertEqual(self.client.max_active, 2)
        self.assertEqual(len(self.app.history.all()[0]["rows"]), 2)
        self.assertNotIn("fake-private-key", json.dumps(self.app.state()))

    def test_invalid_empty_or_stale_selection_cannot_start(self):
        for patch in [{"ids": []}, {"ids": ["missing"]}, {"revision": "stale"}, {"parallel": 9}, {"tier": "wrong"}]:
            with self.assertRaises(ImportIssue):
                self.app.launch(self.body() | patch)
        self.assertFalse(self.client.sessions)

    def claude_body(self):
        store = self.app.stores["claude"]
        for i in range(2):
            self.app.mutate("add", {"mode": "claude", "revision": store.revision(), "name": "Route " + str(i),
                "api": f"https://demo-{i}.invalid/v1", "key": "fake-claude-key"})
        return {"mode": "claude", "revision": store.revision(), "ids": [p["id"] for p in store.public()["providers"]],
                "claimed": "claude-fable-5.1", "tier": "low", "parallel": 2}

    def test_claude_parallel_run_uses_own_package_and_history(self):
        body = self.claude_body()
        self.app.launch(body)
        self.app.worker.join(5)
        self.assertFalse(self.app.busy)
        self.assertEqual(self.client.max_active, 2)
        for session in self.client.sessions.values():
            self.assertEqual(session["body"]["package_id"], "demo-claude")
            self.assertEqual(session["body"]["request_model"], "claude-fable-5-1")
        claude = self.app.state("claude")
        self.assertEqual(len(claude["history"]), 1)
        self.assertEqual(claude["history"][0]["mode"], "claude")
        self.assertTrue(all(r["top"].startswith("claude-") for r in claude["history"][0]["rows"]))
        self.assertEqual(self.app.state("gpt")["history"], [])
        self.assertNotIn("fake-claude-key", json.dumps(claude))

    def test_cross_family_selection_and_model_rejected(self):
        body = self.claude_body()
        for invalid in [body | {"ids": self.body()["ids"]}, body | {"claimed": "gpt-6-astra"}, body | {"mode": "invalid"}]:
            with self.assertRaises(ImportIssue):
                self.app.launch(invalid)
        self.assertFalse(self.client.sessions)

    def test_legacy_history_defaults_to_openai(self):
        self.app.launch(self.body())
        self.app.worker.join(5)
        saved = self.app.history.all()[0]
        saved.pop("mode")
        self.app.history.save(saved)
        self.assertEqual(len(self.app.state("gpt")["history"]), 1)
        self.assertEqual(self.app.state("claude")["history"], [])

    def test_package_selection_and_missing_claude_are_independent(self):
        snapshot = self.client.request("/api/snapshot")
        self.assertEqual(choose_package(snapshot, "claude")["id"], "demo-claude")
        self.assertEqual(choose_package(snapshot, "gpt")["id"], "demo-gpt")
        with self.assertRaises(ImportIssue):
            choose_package({"packages": [PACKAGE]}, "claude")
        self.app.packages["claude"] = None
        self.app.launch(self.body())
        self.app.worker.join(5)
        self.assertEqual(len(self.client.sessions), 1)

    def test_claude_stop_and_recovery_never_start_openai(self):
        self.client.steps = 100
        self.app.launch(self.claude_body())
        deadline = time.monotonic() + 3
        while not self.client.sessions and time.monotonic() < deadline:
            time.sleep(.01)
        self.app.engine.stop()
        self.app.worker.join(5)
        record = self.app.history.all()[0]
        for row in record["rows"]:
            if row.get("session_id"):
                row["status"] = "unknown"
        record["finished_at"] = None
        self.app.history.save(record)
        self.assertEqual(self.app.state()["blocked_mode"], "claude")
        with self.assertRaises(ImportIssue):
            self.app.launch({}, recover=True)
        before = len(self.client.sessions)
        self.app.launch({"mode": "claude"}, recover=True)
        self.app.worker.join(5)
        self.assertFalse(self.app.busy)
        self.assertEqual(len(self.client.sessions), before)
        self.assertTrue(all(b["mode"] == "claude" for b in self.app.history.all()))

    def test_http_origin_host_token_and_file_boundaries(self):
        server = Server(0, self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path, headers=None, body=None):
            req = urllib.request.Request(server.origin + path, headers=headers or {},
                data=json.dumps(body).encode() if body is not None else None)
            return opener.open(req, timeout=3)
        with request("/api/bootstrap") as response:
            bootstrap = json.load(response)
            self.assertEqual(bootstrap["app"], "codex-relay-desk")
            self.assertEqual(bootstrap["api_version"], 2)
        for path, headers in [("/api/state", {}), ("/api/bootstrap", {"Origin": "https://evil.invalid"}),
                              ("/api/bootstrap", {"Host": "evil.invalid"}), ("/codex-providers.toml", {})]:
            with self.assertRaises(urllib.error.HTTPError):
                request(path, headers)
        with self.assertRaises(urllib.error.HTTPError):
            request("/api/start", {"X-Desk-Token": server.token, "Content-Type": "application/json"}, self.body())
        with request("/api/state", {"X-Desk-Token": server.token}) as response:
            self.assertNotIn("fake-private-key", response.read().decode())
        with request("/api/state?mode=claude", {"X-Desk-Token": server.token}) as response:
            state = json.load(response)
            self.assertEqual(state["mode"], "claude")
            self.assertEqual(state["config"]["providers"], [])
            self.assertEqual(state["package"]["mode"], "claude")
        with self.assertRaises(urllib.error.HTTPError):
            request("/api/state?mode=invalid", {"X-Desk-Token": server.token})
        self.assertFalse(self.client.sessions)

    def test_reopening_cannot_bind_a_duplicate_server(self):
        server = Server(0, self.app)
        self.addCleanup(server.server_close)
        with self.assertRaises(OSError):
            duplicate = Server(server.server_port, self.app)
            duplicate.server_close()

    def test_stopped_batch_preserves_history_and_cancels_queue(self):
        self.client.steps = 100
        self.app.launch(self.body(3) | {"parallel": 1})
        deadline = time.monotonic() + 3
        while not self.client.sessions and time.monotonic() < deadline:
            time.sleep(.01)
        self.app.engine.stop()
        self.app.worker.join(5)
        self.assertFalse(self.app.busy)
        rows = self.app.history.all()[0]["rows"]
        self.assertEqual([r["status"] for r in rows], ["stopped", "cancelled", "cancelled"])


if __name__ == "__main__":
    unittest.main()
