#!/usr/bin/env python3
"""Collect provenance even when preparation or compilation failed."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone

project = pathlib.Path(__file__).resolve().parent.parent
source = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)


def git(path, *args):
    if not (path / ".git").exists():
        return None
    result = subprocess.run(["git", "-C", str(path), *args],
                            capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


info = {
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "project_commit": git(project, "rev-parse", "HEAD"),
    "locked_sources": json.loads((project / "sources.lock.json").read_text()),
    "openwrt_commit": git(source, "rev-parse", "HEAD"),
    "argon_commit": git(source / "package/luci-theme-argon", "rev-parse", "HEAD"),
    "feeds": {},
    "patches_sha256": {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((project / "patches").glob("*.patch"))
    },
    "github_run": os.environ.get("GITHUB_RUN_ID"),
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
