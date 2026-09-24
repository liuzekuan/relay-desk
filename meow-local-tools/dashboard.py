"""Loopback-only manual batch workbench. Opening it never starts a test."""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_engine import BatchEngine, History, PENDING, choose_package, now, redact
from provider_store import ProviderStore
from provider_config import MODES
from detector_client import ImportIssue, LocalClient

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent / "web"
API_VERSION = 2


def detector_command(app):
    embedded = app / "python/python.exe"
    if os.name != "nt":
        return ["sh", "start.sh", "--no-browser", "--port", "8765"]
    # System Python needs the source directory on sys.path for the launcher.
    python = [str(embedded), "-I"] if embedded.exists() else [sys.executable]
    return python + ["-B", "-X", "utf8", "launch.py", "--no-browser", "--port", "8765"]


def ensure_detector():
    try:
        return LocalClient(8765)
    except ImportIssue:
        pass
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8765)) == 0:
            raise ImportIssue("8765 端口被其他程序占用，请先释放端口。")
    configured = os.environ.get("MEOW_DETECTOR_DIR")
    candidates = [Path(configured)] if configured else sorted(ROOT.glob("meow-llm-detector*"))
    app = next((p for p in candidates if (p / "launch.py").is_file()
                and (os.name == "nt" or (p / "start.sh").is_file())), None)
    if app is None:
        raise ImportIssue("未找到官方检测器。请安装官方源码包，并设置 MEOW_DETECTOR_DIR 指向该目录。")
    command = detector_command(app)
    logs = ROOT / "meow-local-logs"
    logs.mkdir(exist_ok=True)
    with (logs / "detector-start.log").open("ab") as log:
        subprocess.Popen(command, cwd=app, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                         start_new_session=os.name != "nt")
    for _ in range(120):
        time.sleep(.5)
        try:
            return LocalClient(8765)
        except ImportIssue:
            pass
    raise ImportIssue("检测器尚未就绪，请查看 meow-local-logs，然后重新连接。")


class Workbench:
    def __init__(self, root=ROOT, demo=False, client=None):
        self.root, self.demo = Path(root), demo
        self.stores = {"gpt": ProviderStore(self.root / "codex-providers.toml"),
                       "claude": ProviderStore(self.root / "claude-providers.toml", mode="claude")}
        self.history = History(self.root / "batch-results")
        self.lock = threading.RLock()
        self.client = client
        self.packages = {mode: None for mode in MODES}
        self.package_errors = {mode: "" for mode in MODES}
        self.active_mode = None
        self.busy = False
        self.connecting = False
        self.error = ""
        self.engine = None
        self.worker = None
        self.current = None

    def connect(self):
        with self.lock:
            if self.connecting or self.busy:
                return
            self.connecting = True
        def work():
            try:
                client = self.client if self.demo else ensure_detector()
                snapshot = client.request("/api/snapshot")
                packages, errors = {}, {}
                for mode in MODES:
                    try:
                        packages[mode] = choose_package(snapshot, mode)
                        errors[mode] = ""
                    except ImportIssue as exc:
                        packages[mode], errors[mode] = None, str(exc)
                with self.lock:
                    self.client, self.packages, self.package_errors, self.error = client, packages, errors, ""
            except Exception as exc:
                with self.lock:
                    self.error = str(exc) if isinstance(exc, ImportIssue) else "检测器连接失败，请重新连接。"
                    self.packages = {mode: None for mode in MODES}
            finally:
                with self.lock:
                    self.connecting = False
        threading.Thread(target=work, daemon=True).start()

    def store_for(self, mode):
        if mode not in MODES:
            raise ImportIssue("检测类型无效，请选择 OpenAI 或 Claude。")
        return self.stores[mode]

    def state(self, mode="gpt"):
        with self.lock:
            store = self.store_for(mode)
            records = self.history.all()
            unresolved = next((b for b in records if any(r["status"] in PENDING for r in b["rows"])), None)
            current = self.current if self.current and self.current.get("mode", "gpt") == mode else None
            result = {"mode": mode, "busy": self.busy, "active_mode": self.active_mode,
                      "blocked_mode": unresolved.get("mode", "gpt") if unresolved else None,
                      "connecting": self.connecting, "error": self.error or self.package_errors[mode],
                      "demo": self.demo, "package": deepcopy(self.packages[mode]), "current": deepcopy(current),
                      "history": [b for b in records if b.get("mode", "gpt") == mode]}
            try:
                result["config"] = store.public()
                keys = []
                for source in self.stores.values():
                    try:
                        keys.extend(p["key"] for p in source.read()[1])
                    except ImportIssue:
                        pass
                result = redact(result, keys)
            except ImportIssue as exc:
                result["config"] = None
                result["error"] = "配置读取失败：" + str(exc)
            return result

    def estimate(self, tier, mode="gpt"):
        self.store_for(mode)
        package = self.packages[mode]
        if not package or not self.client:
            raise ImportIssue("检测器未连接。")
        if tier not in {"low", "medium", "high"}:
            raise ImportIssue("检测档位无效。")
        return self.client.request("/api/run/estimate", {"package_id": package["id"],
            "package_version": package["version"], "tier": tier,
            "runtime": {"workers": 4, "timeout": 120, "retain_raw": False}})

    def mutate(self, action, data):
        with self.lock:
            if self.busy:
                raise ImportIssue("检测进行中，请停止或等待完成后修改配置。")
            return self.store_for(data.get("mode", "gpt")).mutate(action, data)

    def launch(self, data, recover=False):
        with self.lock:
            if self.busy:
                raise ImportIssue("已有批次正在执行。")
            mode = data.get("mode", "gpt")
            store = self.store_for(mode)
            package = self.packages[mode]
            if not package or not self.client:
                raise ImportIssue("检测器尚未连接，请重新连接。")
            _, payloads = store.read()
            unresolved = next((b for b in self.history.all() if any(r["status"] in PENDING for r in b["rows"])), None)
            existing = unresolved if recover else None
            if recover and not existing:
                raise ImportIssue("没有需要恢复查询的任务。")
            if existing and existing.get("mode", "gpt") != mode:
                raise ImportIssue("请切换到未完成任务所属的检测类型，再恢复查询。")
            if unresolved and not recover:
                raise ImportIssue("上次任务尚未确认结束，请先恢复查询或核对未知任务。")
            if existing and any(r["status"] in {"starting", "unknown"} and not r.get("session_id") for r in existing["rows"]):
                raise ImportIssue("存在启动结果未知的任务，请先在官方检测器中核对，再确认解除阻塞。")
            if not recover:
                if data.get("revision") != store.revision():
                    raise ImportIssue("配置已变化，请刷新后重新选择。")
                ids = data.get("ids")
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                    raise ImportIssue("请至少选择一个中转。")
                selected = [p for p in payloads if p["preset"]["id"] in ids]
                if len(selected) != len(set(ids)):
                    raise ImportIssue("选择中包含已删除的中转，请刷新。")
                claimed, tier, parallel = data.get("claimed"), data.get("tier"), data.get("parallel")
                if claimed not in {m["id"] for m in package["models"]} or tier not in {"low", "medium", "high"}:
                    raise ImportIssue("模型或档位无效。")
                if type(parallel) is not int or not 1 <= parallel <= 4:
                    raise ImportIssue("并行中转数须为 1 到 4。")
            else:
                selected = payloads
                claimed, tier, parallel = existing["claimed_model"], existing["tier"], existing["parallel"]
            def emit(batch):
                with self.lock:
                    self.current = batch
            self.engine = BatchEngine(self.client, self.history, emit, interval=.5 if self.demo else 1.5)
            engine = self.engine
            package = deepcopy(package)
            self.active_mode = mode
            self.busy, self.error, self.current = True, "", existing
            def work():
                try:
                    engine.run(selected, package, claimed, tier, parallel, existing=existing)
                except Exception as exc:
                    logging.error("Batch interrupted (%s)", type(exc).__name__)
                    with self.lock:
                        self.error = "批测中断。已保存的任务可恢复查询；不会自动重新发起。"
                finally:
                    with self.lock:
                        self.busy = False
            self.worker = threading.Thread(target=work, daemon=True)
            self.worker.start()
            return {"started": True}

    def acknowledge(self, mode="gpt"):
        with self.lock:
            self.store_for(mode)
            if self.busy:
                raise ImportIssue("请等待当前操作结束。")
            for batch in self.history.all():
                if batch.get("mode", "gpt") != mode:
                    continue
                changed = False
                for row in batch["rows"]:
                    if row["status"] in {"starting", "unknown"} and not row.get("session_id"):
                        row.update(status="cancelled", note="用户已在官方检测器核对启动状态")
                        changed = True
                if changed:
                    if not any(r["status"] in PENDING for r in batch["rows"]):
                        batch["finished_at"] = now()
                    self.history.save(batch)
            return {"ok": True}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, port, app):
        super().__init__(("127.0.0.1", port), Handler)
        self.app, self.token = app, secrets.token_urlsafe(32)
        self.origin = "http://127.0.0.1:" + str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, value, mime="application/json; charset=utf-8"):
        payload = json.dumps(value, ensure_ascii=False).encode() if not isinstance(value, bytes) else value
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def allowed(self, mutation=False):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return False
        if self.headers.get("Origin") not in {None, self.server.origin}:
            return False
        if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
            return False
        if mutation and self.headers.get("Origin") != self.server.origin:
            return False
        return True

    def authenticated(self):
        return secrets.compare_digest(self.headers.get("X-Desk-Token", ""), self.server.token)

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {"error": "仅允许本机同源访问。"})
        if self.path == "/api/bootstrap":
            return self.send(200, {"app": "codex-relay-desk", "api_version": API_VERSION, "token": self.server.token})
        if urlsplit(self.path).path == "/api/state":
            if not self.authenticated():
                return self.send(403, {"error": "请刷新页面。"})
            mode = parse_qs(urlsplit(self.path).query).get("mode", ["gpt"])[0]
            if mode not in MODES:
                return self.send(400, {"error": "检测类型无效。"})
            return self.send(200, self.server.app.state(mode))
        files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                 "/vendor/lucide.min.js": "vendor/lucide.min.js", "/favicon.svg": "favicon.svg"}
        if self.path not in files:
            return self.send(404, {"error": "Not found"})
        path = WEB / files[self.path]
        self.send(200, path.read_bytes(), mimetypes.guess_type(str(path))[0] or "application/octet-stream")

    def do_POST(self):
        if not self.allowed(True) or not self.authenticated():
            return self.send(403, {"error": "会话无效，请刷新页面。"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536 or self.headers.get("Content-Type") != "application/json":
                return self.send(400, {"error": "请求格式无效。"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            app = self.server.app
            if self.path.startswith("/api/provider/"):
                value = app.mutate(self.path.rsplit("/", 1)[1], data)
            elif self.path == "/api/start":
                value = app.launch(data)
            elif self.path == "/api/recover":
                value = app.launch(data, recover=True)
            elif self.path == "/api/acknowledge":
                if data.get("confirmed") is not True:
                    raise ImportIssue("需要先确认已核对。")
                value = app.acknowledge(data.get("mode", "gpt"))
            elif self.path == "/api/stop":
                with app.lock:
                    if app.engine:
                        app.engine.stop()
                value = {"ok": True}
            elif self.path == "/api/connect":
                app.connect()
                value = {"ok": True}
            elif self.path == "/api/estimate":
                value = app.estimate(data.get("tier"), data.get("mode", "gpt"))
            else:
                return self.send(404, {"error": "Not found"})
            self.send(200, value)
        except ImportIssue as exc:
            self.send(409, {"error": str(exc)})
        except (ValueError, TypeError):
            self.send(400, {"error": "请求格式无效。"})
        except Exception:
            self.send(500, {"error": "操作失败，未显示诊断详情以保护 Key。请刷新后重试。"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    temporary = None
    if args.demo:
        from batch_demo import DemoClient, demo_payloads
        temporary = tempfile.TemporaryDirectory(prefix="relay-desk-demo-")
        app = Workbench(Path(temporary.name), True, DemoClient(steps=16))
        for mode, store in app.stores.items():
            for payload in demo_payloads(mode):
                store.mutate("add", {"revision": store.revision(), "name": payload["preset"]["name"],
                    "api": payload["preset"]["base_url"], "key": payload["key"]})
    else:
        app = Workbench()
    for port in range(args.port, args.port + 20):
        try:
            server = Server(port, app)
            break
        except OSError:
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(f"http://127.0.0.1:{port}/api/bootstrap", timeout=1) as response:
                    existing = json.load(response)
                if existing.get("app") == "codex-relay-desk" and existing.get("api_version") == API_VERSION:
                    # Never reuse a demo instance for real credentials, or vice versa.
                    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                        headers={"X-Desk-Token": existing["token"]})
                    with opener.open(request, timeout=2) as response:
                        same_mode = json.load(response).get("demo") == args.demo
                    if same_mode:
                        if not args.no_browser:
                            webbrowser.open(f"http://127.0.0.1:{port}/")
                        return
            except Exception:
                pass
    else:
        raise SystemExit("No free local port")
    app.connect()
    if not args.no_browser:
        webbrowser.open(server.origin + "/")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    main()
