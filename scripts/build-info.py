#!/usr/bin/env python3
"""Collect release provenance even when preparation or compilation failed."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from project import DEFAULT_RELEASE, LOCK_FILE, ROOT, load_release, release_files


def git(path, *args):
    if not (path / ".git").exists():
        return None
    result = subprocess.run(["git", "-C", str(path), *args],
                            capture_output=True, text=True, check=False,
                            encoding="utf-8", errors="replace")
    return result.stdout.strip() if result.returncode == 0 else None


def hashes(paths):
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


def collect(source, output, series):
    release = load_release(series)
    output.mkdir(parents=True, exist_ok=True)
    info = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "series": series,
        "version": release["version"],
        "package_manager": release["package_manager"],
        "project_commit": git(ROOT, "rev-parse", "HEAD"),
        "project_dirty": bool(git(ROOT, "status", "--porcelain")),
        "locked_sources": release,
        "source_lock_sha256": hashlib.sha256(LOCK_FILE.read_bytes()).hexdigest(),
        "openwrt_commit": git(source, "rev-parse", "HEAD"),
        "argon_commit": git(source / "package/luci-theme-argon", "rev-parse", "HEAD"),
        "feeds": {},
        "patches_sha256": hashes(release_files(release, "patches")),
        "config_sha256": hashes(release_files(release, "config")),
        "files_sha256": hashes(sorted(p for p in (ROOT / "files").rglob("*") if p.is_file())),
        "github_run": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    feeds = source / "feeds"
    if feeds.is_dir():
        info["feeds"] = {p.name: git(p, "rev-parse", "HEAD")
                         for p in feeds.iterdir() if (p / ".git").exists()}
    for origin, destination in ((".config", "build.config"),
                                ("feeds.conf.default", "feeds.conf.default")):
        if (source / origin).is_file():
            shutil.copyfile(source / origin, output / destination)
    diff = git(source, "diff", "--binary", "HEAD")
    if diff is not None:
        (output / "local-changes.patch").write_text(diff + "\n", encoding="utf-8")
    (output / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Build information saved to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    args = parser.parse_args()
    collect(args.source.resolve(), args.output.resolve(), args.release)
