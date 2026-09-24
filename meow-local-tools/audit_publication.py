"""Check staged content and reachable Git history without printing private values."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FILES = frozenset("""
.gitignore .gitattributes .githooks/pre-commit .githooks/pre-push
.github/workflows/checks.yml .github/workflows/upstream.yml
README.md LICENSE THIRD_PARTY_NOTICES.md SECURITY.md start.cmd start.command
meow-local-tools/audit_publication.py meow-local-tools/fetch_upstream.py
meow-local-tools/batch_demo.py meow-local-tools/batch_engine.py
meow-local-tools/dashboard.py meow-local-tools/detector_client.py
meow-local-tools/package-lock.json meow-local-tools/package.json
meow-local-tools/provider_config.py meow-local-tools/provider_store.py
meow-local-tools/qa_browser.js meow-local-tools/test_batch.py
meow-local-tools/test_config.py meow-local-tools/test_dashboard.py
meow-local-tools/test_publication.py meow-local-tools/test_upstream.py
meow-local-tools/test_results.cjs meow-local-tools/vendor.cjs
meow-local-tools/verify_detector.py meow-local-tools/web/app.js
meow-local-tools/web/favicon.svg meow-local-tools/web/index.html
meow-local-tools/web/style.css meow-local-tools/web/vendor/lucide.min.js
meow-local-tools/web/vendor/LUCIDE-LICENSE
""".split())
TOKEN = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def private_values(root, required=False):
    values = set()
    names = set()
    found = False
    for filename in ("codex-providers.toml", "claude-providers.toml"):
        path = root / filename
        if not path.exists():
            continue
        found = True
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        for row in data.get("providers", []):
            for field in ("name", "api", "key"):
                value = row.get(field, "").strip()
                if value:
                    (names if field == "name" else values).add(value)
            host = urlsplit(row.get("api", "")).hostname
            if host:
                values.add(host)
            name = row.get("name", "").strip().casefold()
            if name:
                values.add(hashlib.sha256(name.encode()).hexdigest())
    if required and (not found or not values):
        raise ValueError("Private configuration required")
    for path in (root / "batch-results").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for row in data.get("rows", []):
            for field in ("name", "provider_id", "api", "base_url", "key"):
                if isinstance(row.get(field), str) and row[field]:
                    (names if field == "name" else values).add(row[field])
    return values, names


def needles_for(values, names=()):
    needles = set()
    for value in set(values) | set(names):
        for variant in (value, json.dumps(value, ensure_ascii=True)[1:-1],
                        quote(value, safe=""), base64.b64encode(value.encode()).decode()):
            # Short ASCII names can also be ordinary code words. Match labels
            # and serialized values, without mistaking identifiers for providers.
            if value not in values and value.isascii() and value.isalpha() and len(value) <= 8:
                for left, right in (('"', '"'), ("'", "'"), (">", "<")):
                    needles.add((left + variant + right).casefold().encode())
            else:
                needles.add(variant.casefold().encode())
    return needles


def contains_private(content, needles):
    normalized = content.decode("utf-8", errors="replace").casefold().encode()
    return bool(TOKEN.search(content)) or any(n in normalized for n in needles)


def check_tree(entries):
    for mode, path in entries:
        if path not in PUBLIC_FILES or mode not in {"100644", "100755"}:
            raise ValueError("Unapproved file or file type (path withheld)")


def audit(root=ROOT, history=False, required=False):
    needles = needles_for(*private_values(root, required))
    staged = []
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        mode, oid, stage = header.decode().split()
        if stage != "0":
            raise ValueError("Unmerged index")
        staged.append((mode, path.decode(), oid))
    if not staged:
        raise ValueError("No staged publication files")
    check_tree((mode, path) for mode, path, _ in staged)
    for _, path, oid in staged:
        if contains_private(path.encode() + b"\n" + git(root, "cat-file", "blob", oid), needles):
            raise ValueError("Sensitive content in approved file: " + path)
    object_count = 0
    if history:
        for commit in git(root, "rev-list", "--all").decode().splitlines():
            entries = []
            for entry in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
                if entry:
                    meta, path = entry.split(b"\t", 1)
                    entries.append((meta.decode().split()[0], path.decode()))
            check_tree(entries)
        for line in git(root, "rev-list", "--objects", "--all").splitlines():
            oid = line.split(b" ", 1)[0].decode()
            kind = git(root, "cat-file", "-t", oid).decode().strip()
            if contains_private(git(root, "cat-file", kind, oid), needles):
                raise ValueError("Sensitive content in Git history (object withheld)")
            object_count += 1
    print(f"PASS: {len(staged)} approved staged files; {object_count} history objects; "
          f"private comparison {'enabled' if needles else 'unavailable'}; no detected matches.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--require-private", action="store_true")
    args = parser.parse_args()
    try:
        audit(history=args.history, required=args.require_private)
    except Exception as exc:
        # Parsing and Git errors can contain secrets or private filenames.
        if type(exc) is ValueError and str(exc).startswith(("Sensitive content", "Unapproved", "Unmerged", "No staged", "Private configuration")):
            print("FAIL: " + str(exc))
        else:
            print("FAIL: audit could not complete; inspect local inputs privately.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
