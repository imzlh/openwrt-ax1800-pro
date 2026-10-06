#!/usr/bin/env python3
"""Stage only validated firmware and its provenance, with complete checksums."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

from project import DEFAULT_RELEASE, load_release

spec = importlib.util.spec_from_file_location(
    "check_firmware", Path(__file__).with_name("check-firmware.py"))
firmware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firmware)


def collect(directory, output, build_info, series=DEFAULT_RELEASE):
    directory, output, build_info = map(Path, (directory, output, build_info))
    release = load_release(series)
    verified = firmware.verify(directory, series)
    if build_info.is_symlink() or not build_info.is_file():
        raise ValueError("Missing or non-local build-info.json")
    info_bytes = build_info.read_bytes()
    info = json.loads(info_bytes)
    if not isinstance(info, dict):
        raise ValueError("Invalid build-info.json schema")
    expected = {"series": series, "version": release["version"],
                "openwrt_commit": release["commit"],
                "argon_commit": release["argon"]["commit"],
                "package_manager": release["package_manager"]}
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"Build provenance mismatch: {key}")
    if output.exists():
        raise ValueError(f"Release output already exists; use a new directory: {output}")
    names = verified["images"] + [verified["manifest"], verified["profiles"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build in an isolated sibling and publish the complete directory at once.
    with tempfile.TemporaryDirectory(prefix=".release-", dir=output.parent) as temporary:
        staging = Path(temporary) / "files"
        staging.mkdir()
        for name in names:
            shutil.copyfile(directory / name, staging / name)
        (staging / "build-info.json").write_bytes(info_bytes)
        # Verify copied images too, so staging cannot hide source changes.
        firmware.verify(staging, series)
        sums = "".join(f"{firmware.sha256(staging / name)}  {name}\n"
                       for name in sorted(names + ["build-info.json"]))
        (staging / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
        staging.rename(output)
    return sorted(names + ["build-info.json", "SHA256SUMS"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--build-info", type=Path, required=True)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    args = parser.parse_args()
    for name in collect(args.directory, args.output, args.build_info, args.release):
        print(f"Staged release attachment: {name}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Release collection failed: {error}")
