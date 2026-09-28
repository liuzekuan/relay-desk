"""Bounded batch scheduling over the official detector's local API."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
import uuid

from detector_client import ImportIssue


PENDING = {"queued", "starting", "running", "stopping", "unknown"}
RETRYABLE = {"failed", "partial", "stopped", "cancelled"}


def now():
    return datetime.now(timezone.utc).isoformat()


def redact(value, keys):
    if isinstance(value, str):
        for key in keys:
            if key:
                value = value.replace(key, "[redacted]")
        return value
    if isinstance(value, list):
        return [redact(item, keys) for item in value]
    if isinstance(value, dict):
        return {redact(str(k), keys): redact(v, keys) for k, v in value.items()}
    return value


class History:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def save(self, batch):
        with self.lock:
            self._save(batch)

    def _save(self, batch):
        identity = batch["id"]
        if not identity or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid batch id")
        target = self.root / (identity + ".json")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows scanners can briefly hold the destination after a prior write.
        for attempt in range(5):
            try:
                temporary.replace(target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.05 * 2 ** attempt)

    def all(self):
        with self.lock:
            return self._all()

    def _all(self):
        records = []
        for path in self.root.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("schema") == 1 and isinstance(value.get("rows"), list):
                    records.append(value)
            except (OSError, ValueError, AttributeError):
                continue
        return sorted(records, key=lambda item: item["created_at"], reverse=True)


def choose_package(snapshot, mode="gpt"):
    packages = [p for p in snapshot["packages"] if p["mode"] == mode
                and p.get("publisher") == "maintainer" and not p.get("withdrawn")]
    selected = snapshot.get("defaults", {}).get(mode, {})
    match = next((p for p in packages if p["id"] == selected.get("id")
                  and p["version"] == selected.get("version")), None)
    if match:
        return match
    if not packages:
        label = "Claude" if mode == "claude" else "OpenAI"
        raise ImportIssue(f"本地没有可用的 {label} 基准，请在检测器中更新基准。")
    return sorted(packages, key=lambda p: p["version"], reverse=True)[0]


def apply_report(row, report):
    progress = report["progress"]
    fingerprint = report.get("fingerprint", {})
    row.update(planned=progress.get("planned", 0), completed=progress.get("logical_completed", 0),
               valid=progress.get("valid_samples", 0), errors=progress.get("errors", 0),
               attempts=progress.get("http_attempts", 0), retries=progress.get("retries", 0))
    matches = {str(k): float(v) for k, v in fingerprint.get("matches", {}).items()
               if isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v <= 1}
    row["matches"] = matches
    row["thresholds"] = {str(k): float(v) for k, v in fingerprint.get("thresholds", {}).items()
                         if isinstance(v, (float, int)) and math.isfinite(v)}
    row["reasons"] = fingerprint.get("reasons", [])
    row["verdict"] = fingerprint.get("verdict", "insufficient")
    row["top"] = max(matches, key=matches.get) if matches and row["valid"] else ""
    row["score"] = matches.get(row["top"]) if row["top"] else None
    row["claimed_score"] = matches.get(report.get("claimed_model")) if row["valid"] else None
    operation = report.get("operational_status", progress.get("status"))
    if operation == "complete":
        row["status"] = "failed" if not row["valid"] else "partial" if row["errors"] else "complete"
    elif operation == "error":
        row["status"] = "failed"
    elif operation == "paused":
        row["status"] = "stopped"
    else:
        row["status"] = "running"
    if report.get("failure"):
        row["note"] = "检测器报告错误：" + str(report["failure"])


def compare_context(batch):
    return (batch.get("mode", "gpt"), batch.get("benchmark", {}).get("content_sha256"), batch.get("claimed_model"),
            batch.get("request_model"), batch.get("tier"))


def previous_delta(batch, row, records):
    if row.get("claimed_score") is None:
        return None
    for previous in records:
        if previous["id"] == batch["id"] or not previous.get("finished_at"):
            continue
        if compare_context(previous) != compare_context(batch):
            continue
        old = next((r for r in previous["rows"] if r["provider_id"] == row["provider_id"]
                    and r.get("claimed_score") is not None), None)
        if old:
            return (row["claimed_score"] - old["claimed_score"]) * 100
    return None


class BatchEngine:
    def __init__(self, client, history, emit=lambda value: None, interval=1, deadline=2700):
        self.client, self.history, self.emit = client, history, emit
        self.interval, self.deadline = interval, deadline
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def publish(self, batch, keys):
        public = redact(batch, keys)
        self.history.save(public)
        self.emit(deepcopy(public))

    def run(self, payloads, package, claimed, tier="low", parallel=2, existing=None):
        if not 1 <= parallel <= 4 or tier not in {"low", "medium", "high"}:
            raise ImportIssue("批测参数无效。")
        keys = [p["key"] for p in payloads]
        runtime = {"workers": 4, "timeout": 120, "retain_raw": False}
        if existing is None:
            mode = package.get("mode", "gpt")
            if not payloads or any(p["preset"].get("mode", "gpt") != mode for p in payloads):
                raise ImportIssue("中转协议与检测基准不一致。")
            if claimed not in {m["id"] for m in package["models"]
                               if not m.get("reference_only") and m["id"] != "other"}:
                raise ImportIssue("验证模型不在当前基准中，请重新选择。")
            estimate = self.client.request("/api/run/estimate", {
                "package_id": package["id"], "package_version": package["version"],
                "tier": tier, "runtime": runtime})
            runtime["retry_budget"] = estimate["retry_budget"]
            batch = {"schema": 1, "mode": mode, "id": uuid.uuid4().hex, "created_at": now(), "finished_at": None,
                     "benchmark": {k: package[k] for k in ("id", "version", "content_sha256")},
                     "claimed_model": claimed, "request_model": claimed,
                     "tier": tier, "parallel": parallel, "planned": estimate["logical_requests"],
                     "max_attempts": estimate["maximum_http_attempts"], "rows": []}
            for payload in payloads:
                preset = payload["preset"]
                batch["rows"].append({"provider_id": preset["id"], "name": preset["name"],
                    "status": "queued", "session_id": None, "planned": batch["planned"],
                    "completed": 0, "valid": 0, "errors": 0, "attempts": 0, "elapsed": 0,
                    "top": "", "score": None, "verdict": "insufficient", "note": ""})
        else:
            batch = deepcopy(existing)
            batch["finished_at"] = None
            for row in batch["rows"]:
                if row["status"] in PENDING:
                    uncertain = row["status"] in {"starting", "unknown"} and not row.get("session_id")
                    row["status"] = "running" if row.get("session_id") else "unknown" if uncertain else "cancelled"
                    row["note"] = "启动结果未知，需人工核对" if uncertain else "已恢复任务状态" if row.get("session_id") else "中断前尚未发起"
        sources = {p["preset"]["id"]: p for p in payloads}
        self.publish(batch, keys)
        if any(r["status"] == "unknown" and not r.get("session_id") for r in batch["rows"]):
            return redact(batch, keys)
        failures, stopping = {}, set()
        started = {r["provider_id"]: time.monotonic() - r.get("elapsed", 0) for r in batch["rows"] if r.get("session_id")}
        lost = False
        while any(r["status"] in PENDING for r in batch["rows"]):
            if self.stop_event.is_set() or lost:
                for row in batch["rows"]:
                    if row["status"] == "queued":
                        row["status"] = "cancelled"
            active = [r for r in batch["rows"] if r.get("session_id") and r["status"] in PENDING]
            if not self.stop_event.is_set() and not lost:
                for row in [r for r in batch["rows"] if r["status"] == "queued"][:max(0, parallel - len(active))]:
                    payload = sources[row["provider_id"]]
                    row["status"] = "starting"
                    self.publish(batch, keys)
                    try:
                        response = self.client.request("/api/run/start", {
                            "base_url": payload["preset"]["base_url"], "key": payload["key"],
                            "request_model": batch["request_model"], "claimed_model": batch["claimed_model"],
                            "package_id": batch["benchmark"]["id"], "package_version": batch["benchmark"]["version"],
                            "tier": batch["tier"], "runtime": runtime, "site_group": row["name"]})
                        row.update(session_id=response["session_id"], status="running", started_at=now())
                        started[row["provider_id"]] = time.monotonic()
                        active.append(row)
                    except ImportIssue as exc:
                        # A timed-out POST may have started a paid run. Never resend it automatically.
                        definite = "HTTP 4" in str(exc)
                        row.update(status="failed" if definite else "unknown",
                                   note="启动被拒绝" if definite else "启动结果未知，请在检测器历史中核对；不会自动重试")
                        lost = not definite
                    self.publish(batch, keys)
                    if lost or self.stop_event.is_set():
                        break
            for row in active:
                identity = row["session_id"]
                row["elapsed"] = round(time.monotonic() - started[row["provider_id"]], 1)
                should_stop = self.stop_event.is_set() or row["elapsed"] >= self.deadline
                try:
                    if should_stop and identity not in stopping:
                        self.client.request("/api/run/stop", {"session_id": identity})
                        stopping.add(identity)
                    try:
                        report = self.client.request("/api/report/" + identity)
                    except ImportIssue as error:
                        if "HTTP 404" not in str(error):
                            raise
                        progress = self.client.request("/api/progress/" + identity)
                        if progress.get("status") not in {"paused", "error"}:
                            raise
                        report = {"progress": progress, "operational_status": progress["status"]}
                        row["note"] = "检测器重启前任务已中断，尚无完整指纹报告"
                    apply_report(row, report)
                    if should_stop and row["status"] == "running":
                        row["status"] = "stopping"
                    if row["elapsed"] >= self.deadline:
                        row["note"] = "已达到 45 分钟上限，已请求停止"
                    failures[identity] = 0
                except ImportIssue:
                    failures[identity] = failures.get(identity, 0) + 1
                    row["note"] = "本地服务连接中断，正在重试查询"
                    if failures[identity] >= 3:
                        row.update(status="unknown", note="服务连接中断；重新打开窗口以恢复查询")
                        lost = True
            self.publish(batch, keys)
            if lost:
                for row in batch["rows"]:
                    if row["status"] == "queued":
                        row["status"] = "cancelled"
                break
            if any(r["status"] in PENDING for r in batch["rows"]):
                time.sleep(self.interval)
        if not any(r["status"] in PENDING for r in batch["rows"]):
            batch["finished_at"] = now()
        records = self.history.all()
        for row in batch["rows"]:
            row["delta"] = previous_delta(batch, row, records)
        self.publish(batch, keys)
        return redact(batch, keys)
