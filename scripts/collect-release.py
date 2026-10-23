#!/usr/bin/env python3
"""Stage only validated firmware and its provenance, with complete checksums."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile

from project import DEFAULT_RELEASE, load_release

spec = importlib.util.spec_from_file_location(
    "package_feed", Path(__file__).with_name("package-feed.py"))
package_feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_feed)

spec = importlib.util.spec_from_file_location(
    "check_firmware", Path(__file__).with_name("check-firmware.py"))
firmware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(firmware)


def collect(directory, output, build_info, series=DEFAULT_RELEASE,
            package_source=None, package_archive=None):
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
    if bool(package_source) != bool(package_archive):
        raise ValueError("Package source and archive name must be specified together")
    inventory = package_feed.verify(Path(package_source), series) if package_source else None
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
        if inventory is not None:
            if Path(package_archive).name != package_archive or not package_archive.endswith(".tar.zst"):
                raise ValueError("Expected a local .tar.zst package archive name")
            collect_packages(Path(package_source), staging / package_archive, inventory)
            shutil.copyfile(Path(package_source) / package_feed.PLAN, staging / package_feed.PLAN)
            shutil.copyfile(Path(package_source) / package_feed.INVENTORY,
                            staging / package_feed.INVENTORY)
            names += [package_archive, package_feed.PLAN, package_feed.INVENTORY]
        sums = "".join(f"{firmware.sha256(staging / name)}  {name}\n"
                       for name in sorted(names + ["build-info.json"]))
        (staging / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
        staging.rename(output)
    return sorted(names + ["build-info.json", "SHA256SUMS"])


def collect_packages(source, output, inventory):
    """Keep every audited repository intact, including its native signed index."""
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError(f"Package archive already exists: {output}")
    with tempfile.TemporaryDirectory(prefix=".package-feed-", dir=output.parent) as temporary:
        tar_path = Path(temporary) / "packages.tar"
        with tarfile.open(tar_path, "w") as archive:
            for relative, expected in inventory["files_sha256"].items():
                path = source / "bin" / relative
                if firmware.sha256(path) != expected:
                    raise ValueError(f"Package repository changed during staging: {relative}")
                # OpenWrt may symlink shared packages; include their actual contents.
                archive.dereference = True
                archive.add(path, relative, recursive=False)
            for name in (package_feed.PLAN, package_feed.INVENTORY):
                archive.add(source / name, name, recursive=False)
        try:
            subprocess.run(["zstd", "-q", "-f", "-19", str(tar_path), "-o", str(output)],
                           check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise ValueError("zstd is required to create package archive") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--build-info", type=Path, required=True)
    parser.add_argument("--package-source", type=Path)
    parser.add_argument("--package-archive")
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    args = parser.parse_args()
    for name in collect(args.directory, args.output, args.build_info, args.release,
                        args.package_source, args.package_archive):
        print(f"Staged release attachment: {name}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Release collection failed: {error}")
