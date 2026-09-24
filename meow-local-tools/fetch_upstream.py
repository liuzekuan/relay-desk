"""Inspect the latest official release; optionally install its verified source package."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile

REPOSITORY = "chen-006/meow-llm-detector"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES = f"https://github.com/{REPOSITORY}/releases/download/"


def latest_release():
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Relay-Desk"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(API, headers=headers), timeout=30) as response:
        release = json.load(response)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Expected a stable release")
    asset = next(a for a in release["assets"] if
                 re.fullmatch(r"meow-llm-detector-v[0-9.]+-zh-CN\.zip", a["name"]))
    if not asset["browser_download_url"].startswith(RELEASES):
        raise ValueError("Unexpected release origin")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or ""):
        raise ValueError("Official asset has no SHA-256 digest; install manually after verification")
    return release, asset


def extract_verified(archive, digest, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Destination already exists; use the official in-app updater")
    with open(archive, "rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != digest.removeprefix("sha256:"):
        raise ValueError("SHA-256 mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".meow-extract-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if sum(m.file_size for m in members) > 1024 * 1024 * 1024:
                raise ValueError("Source archive exceeds size limit")
            for member in members:
                path = PurePosixPath(member.filename)
                if (path.is_absolute() or ".." in path.parts or "\\" in member.filename
                        or ":" in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
                    raise ValueError("Unsafe archive member")
            bundle.extractall(stage)
        roots = [stage] if (stage / "launch.py").is_file() else [
            p for p in stage.iterdir() if p.is_dir() and (p / "launch.py").is_file()]
        if len(roots) != 1 or not (roots[0] / "start.sh").is_file() or not (roots[0] / "LICENSE").is_file():
            raise ValueError("Unexpected official source layout")
        roots[0].rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, help="New directory only; existing installations are never overwritten")
    args = parser.parse_args()
    if args.destination is not None and args.destination.exists():
        parser.error("Destination already exists; update through the official detector UI")
    try:
        release, asset = latest_release()
        print("Official release:", release["tag_name"])
        print("Release page:", release["html_url"])
        if args.destination is not None:
            with tempfile.TemporaryDirectory(prefix="relay-desk-download-") as temporary:
                archive = Path(temporary) / "source.zip"
                with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response, archive.open("wb") as target:
                    shutil.copyfileobj(response, target)
                extract_verified(archive, asset["digest"], args.destination.resolve())
            print("Verified official source installed. No model requests were sent.")
    except Exception:
        print("Upstream check/install failed. Check network access and official release assets; no existing installation was overwritten.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
