"""Atomic edits of the existing local TOML, with optimistic concurrency."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import tomllib

from detector_client import ImportIssue
from provider_config import MODES, load_connections


class ProviderStore:
    def __init__(self, path, mode="gpt"):
        if mode not in MODES:
            raise ImportIssue("Unsupported detection mode.")
        self.path = Path(path)
        self.mode = mode
        self.lock = threading.RLock()

    def revision(self):
        return hashlib.sha256(self.path.read_bytes() if self.path.exists() else b"").hexdigest()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return MODES[self.mode]["model"], []
            payloads = load_connections(self.path, allow_empty=True, mode=self.mode)
            config = tomllib.loads(self.path.read_text(encoding="utf-8-sig"))
            return config.get("model", MODES[self.mode]["model"]), payloads

    def public(self):
        with self.lock:
            model, payloads = self.read()
            return {"revision": self.revision(), "model": model, "providers": [
                {"id": p["preset"]["id"], "name": p["preset"]["name"],
                 "api": p["preset"]["base_url"], "has_key": bool(p["key"])} for p in payloads]}

    def mutate(self, action, data):
        with self.lock:
            revision = self.revision()
            if data.get("revision") != revision:
                raise ImportIssue("配置已在其他窗口或文件中修改，请刷新后重新编辑。")
            model, payloads = self.read()
            rows = [{"name": p["preset"]["name"], "api": p["preset"]["base_url"], "key": p["key"]}
                    for p in payloads]
            index = next((i for i, p in enumerate(payloads) if p["preset"]["id"] == data.get("id")), None)
            if action in {"edit", "delete"} and index is None:
                raise ImportIssue("该中转已不存在，请刷新配置。")
            if action == "delete":
                rows.pop(index)
            elif action in {"add", "edit"}:
                row = {k: data.get(k, "") for k in ("name", "api", "key")}
                if action == "edit":
                    if row["key"] == "":
                        row["key"] = rows[index]["key"]
                    rows[index] = row
                else:
                    rows.append(row)
            elif action == "model":
                model = data.get("model", "")
            else:
                raise ImportIssue("未知配置操作。")
            quote = lambda value: json.dumps(value, ensure_ascii=False)
            text = "model = " + quote(model) + "\n"
            if not rows:
                text += "providers = []\n"
            for row in rows:
                text += "\n[[providers]]\n" + "".join(k + " = " + quote(v) + "\n" for k, v in row.items())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".providers-", suffix=".toml", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                checked = load_connections(Path(temporary), allow_empty=True, mode=self.mode)
                # A secret from any provider must not leak through another public field.
                public_values = [model] + [p["preset"][k] for p in checked for k in ("name", "base_url")]
                if any(p["key"] in value for p in checked for value in public_values):
                    raise ImportIssue("Key 只能出现在 Key 字段。")
                if self.revision() != revision:
                    raise ImportIssue("配置刚刚发生变化，请刷新后重试。")
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return self.public()
