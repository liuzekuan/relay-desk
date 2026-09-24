"""Exercise real detector routes with an offline fake transport and isolated data."""
import asyncio
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_engine import BatchEngine, History, choose_package
from detector_client import LocalClient
from gpt56_vnext import server


class OfflineTransport:
    requests = 0
    modes = set()

    def __init__(self, *args, **kwargs):
        pass

    async def request(self, mode, base, key, model, cell, *, on_dispatch, **kwargs):
        type(self).requests += 1
        type(self).modes.add(mode)
        on_dispatch()
        await asyncio.sleep(.02)
        return {"answer": "France", "http_status": 200, "elapsed_ms": 20}

    async def close(self):
        pass


def main():
    server.AsyncTransport = OfflineTransport
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = server.create_server(port=0, runs_root=root / "server")
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            client = LocalClient(app.server_port)
            snapshot = client.request("/api/snapshot")
            history = History(root / "history")
            total = 0
            for mode in ("gpt", "claude"):
                package = choose_package(snapshot, mode)
                claimed = package["models"][0]["id"]
                payloads = [{"preset": {"id": f"fixture-{mode}-{i}", "name": f"Fixture {i}", "mode": mode,
                                          "model": claimed, "base_url": "https://example.invalid/v1"},
                             "key": "fake-local-only-fixture-key"} for i in range(2)]
                result = BatchEngine(client, history, interval=.1).run(payloads, package, claimed)
                assert result["finished_at"]
                assert result["mode"] == mode
                assert all(r["status"] == "complete" for r in result["rows"])
                assert all(r["valid"] == result["planned"] for r in result["rows"])
                total += 2 * result["planned"]
            assert OfflineTransport.requests == total
            assert OfflineTransport.modes == {"gpt", "claude"}
            assert "fake-local-only-fixture-key" not in str(history.all())
            print("PASS: OpenAI and Claude real estimate/start/report routes, concurrent tasks and isolated history.")
            print(f"Offline requests: {OfflineTransport.requests}; real provider requests: 0.")
        finally:
            app.shutdown()
            app.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
