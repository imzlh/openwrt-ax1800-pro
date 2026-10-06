#!/usr/bin/env python3
"""One source of truth for local builds, CI matrices and release validation."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / "sources.lock.json"
DEFAULT_RELEASE = "24.10"


def project_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or Path(value).is_absolute():
        raise ValueError(f"Expected a path within the project: {value}")
    return path


def load_releases(lock_file=LOCK_FILE):
    lock = json.loads(Path(lock_file).read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1:
        raise ValueError("Unsupported source lock schema")
    for url in (lock["openwrt_url"], lock["argon"]["url"]):
        if not url.startswith("https://") or any(c.isspace() for c in url):
            raise ValueError(f"Expected an HTTPS source URL: {url}")
    if not re.fullmatch(r"[0-9a-f]{40}", lock["argon"]["commit"]):
        raise ValueError("Argon must be pinned to a full commit")
    if not project_path(lock["kernel_config"]).is_file():
        raise ValueError("Missing kernel configuration fragment")
    releases = {}
    for series, entry in lock["releases"].items():
        if not re.fullmatch(r"\d+\.\d+", series):
            raise ValueError(f"Invalid release series: {series}")
        if not re.fullmatch(re.escape(series) + r"\.\d+", entry["version"]):
            raise ValueError(f"Version does not belong to {series}: {entry['version']}")
        if not re.fullmatch(r"[0-9a-f]{40}", entry["commit"]):
            raise ValueError(f"OpenWrt {series} must be pinned to a full commit")
        if entry["package_manager"] not in ("opkg", "apk"):
            raise ValueError(f"Unknown package manager for {series}")
        for key in ("config", "patch_dirs"):
            if not isinstance(entry[key], list) or not entry[key]:
                raise ValueError(f"Missing {key} for {series}")
            for value in entry[key]:
                path = project_path(value)
                if key == "config" and not path.is_file():
                    raise ValueError(f"Missing config fragment: {value}")
                if key == "patch_dirs" and not any(path.glob("*.patch")):
                    raise ValueError(f"Missing device patches: {value}")
        releases[series] = {**entry, "series": series,
                            "url": lock["openwrt_url"], "argon": lock["argon"],
                            "kernel_config": lock["kernel_config"]}
    if not releases:
        raise ValueError("No releases configured")
    return releases


def load_release(series=DEFAULT_RELEASE):
    releases = load_releases()
    if series not in releases:
        raise ValueError(f"Unsupported release {series}; choose {', '.join(releases)}")
    return releases[series]


def release_files(release, kind):
    if kind == "kernel":
        return [project_path(release["kernel_config"])]
    if kind == "config":
        return [project_path(path) for path in release["config"]]
    return [patch for directory in release["patch_dirs"]
            for patch in sorted(project_path(directory).glob("*.patch"))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    for command in ("matrix", "show", "sources", "files"):
        sub = commands.add_parser(command)
        sub.add_argument("--release", default="all" if command == "matrix" else DEFAULT_RELEASE)
        if command == "files":
            sub.add_argument("--kind", choices=("config", "kernel", "patches"), required=True)
    args = parser.parse_args()
    if args.command == "validate":
        for release in load_releases().values():
            print(f"Verified release: {release['series']} ({release['version']})")
    elif args.command == "matrix":
        releases = (load_releases().values() if args.release == "all"
                    else [load_release(args.release)])
        print(json.dumps({"include": [{key: release[key] for key in
                                      ("series", "version", "commit")}
                                     for release in releases]}, separators=(",", ":")))
    else:
        release = load_release(args.release)
        if args.command == "show":
            print(json.dumps(release, indent=2))
        elif args.command == "sources":
            print(release["url"], release["commit"], release["argon"]["url"],
                  release["argon"]["commit"], sep="\n")
        elif args.command == "files":
            print(*release_files(release, args.kind), sep="\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Project configuration error: {error}")
