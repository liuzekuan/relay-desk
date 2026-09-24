"""Explicitly labelled offline fixtures for desktop QA; never calls a provider."""
from copy import deepcopy
import time

from detector_client import ImportIssue


PACKAGE = {"id": "demo-gpt", "version": "demo-1", "mode": "gpt", "publisher": "maintainer",
           "content_sha256": "demo", "models": [{"id": "gpt-6-astra"}, {"id": "gpt-5.6-sol"}]}
CLAUDE_PACKAGE = {"id": "demo-claude", "version": "demo-claude-1", "mode": "claude", "publisher": "maintainer",
                  "content_sha256": "demo-claude", "models": [{"id": "claude-fable-5.1"}, {"id": "claude-sonnet-5"}]}


def demo_payloads(mode="gpt"):
    return [{"preset": {"id": f"demo-{mode}-{i}" if mode != "gpt" else f"demo-{i}",
                        "name": "演示线路 " + chr(65 + i), "mode": mode,
                        "model": "gpt-6-astra" if mode == "gpt" else "claude-fable-5-1",
                        "base_url": f"https://demo-{i}.invalid/v1"}, "key": "fake-demo-key"}
            for i in range(4)]


class DemoClient:
    def __init__(self, steps=8):
        self.steps = steps
        self.sessions, self.calls = {}, []
        self.max_active = 0

    def request(self, path, body=None):
        self.calls.append(path)
        if path == "/api/snapshot":
            return {"packages": [deepcopy(PACKAGE), deepcopy(CLAUDE_PACKAGE)], "defaults": {}}
        if path == "/api/run/estimate":
            return {"logical_requests": 36, "maximum_http_attempts": 54, "retry_budget": 18}
        if path == "/api/run/start":
            if body["base_url"].startswith("https://demo-3."):
                raise ImportIssue("Local detector returned HTTP 401.")
            identity = "demo-session-" + str(len(self.sessions))
            self.sessions[identity] = {"body": body.copy(), "ticks": 0, "stopped": False}
            active = sum(s["ticks"] < self.steps and not s["stopped"] for s in self.sessions.values())
            self.max_active = max(self.max_active, active)
            return {"session_id": identity}
        if path == "/api/run/stop":
            self.sessions[body["session_id"]]["stopped"] = True
            return {"stopping": body["session_id"]}
        if path.startswith("/api/report/"):
            session = self.sessions[path.rsplit("/", 1)[1]]
            session["ticks"] += 1
            done = min(36, round(session["ticks"] / self.steps * 36))
            route = session["body"]["base_url"]
            verdict = "match" if "demo-0." in route else "mismatch" if "demo-1." in route else "insufficient"
            package = CLAUDE_PACKAGE if session["body"]["package_id"] == CLAUDE_PACKAGE["id"] else PACKAGE
            first, second = [m["id"] for m in package["models"]]
            matches = {first: .924 if verdict == "match" else .312 if verdict == "mismatch" else .603,
                       second: .205 if verdict == "match" else .918 if verdict == "mismatch" else .527}
            errors = done // 4 if verdict == "insufficient" else 0
            status = "paused" if session["stopped"] else "complete" if done == 36 else "running"
            return {"operational_status": status, "claimed_model": session["body"]["claimed_model"],
                    "progress": {"planned": 36, "logical_completed": done, "valid_samples": done-errors,
                                 "errors": errors, "http_attempts": done, "retries": 0},
                    "fingerprint": {"verdict": verdict, "matches": matches,
                                    "thresholds": {first: .8, second: .8},
                                    "reasons": ["samples_incomplete"] if errors else []}}
        raise ImportIssue("Unknown fixture route")
