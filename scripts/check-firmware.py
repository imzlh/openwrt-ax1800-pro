#!/usr/bin/env python3
"""Validate the actual images and package manifests before publishing artifacts."""
import hashlib
import importlib.util
import json
import pathlib
import sys

spec = importlib.util.spec_from_file_location(
    "check_config", pathlib.Path(__file__).with_name("check-config.py"))
config_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_check)

# OpenWrt appends ABI_VERSION to this runtime package name, while Kconfig
# retains the source package name. Pinned v25.12.5 ustream-ssl uses 20201210.
RUNTIME_PACKAGE_NAMES = {"libustream-mbedtls": "libustream-mbedtls20201210"}


def check(directory):
    data = json.loads((directory / "profiles.json").read_text())
    if data.get("metadata_version") != 1:
        raise ValueError("Unsupported profiles.json schema")
    if data.get("target") != "qualcommax/ipq60xx":
        raise ValueError("Wrong firmware target")
    profile = data["profiles"][config_check.DEVICE]
    if "jdcloud,re-ss-01" not in profile["supported_devices"]:
        raise ValueError("Missing JDCloud RE-SS-01 compatibility metadata")
    images = [item for item in profile["images"] if item["type"] == "sysupgrade"]
    if not images:
        raise ValueError("No sysupgrade image produced")
    for item in images:
        name = item["name"]
        if pathlib.PurePosixPath(name).name != name or "\\" in name:
            raise ValueError(f"Invalid image name: {name}")
        image = directory / name
        if image.stat().st_size < 1024 * 1024:
            raise ValueError(f"Truncated image: {name}")
        with image.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Image checksum mismatch: {name}")
    manifests = list(directory.glob(f"*{config_check.DEVICE}*.manifest"))
    if not manifests:
        raise ValueError("Missing device package manifest")
    for manifest in manifests:
        packages = {line.split()[0] for line in manifest.read_text().splitlines()
                    if line.strip()}
        required_packages = {key.removeprefix("CONFIG_PACKAGE_")
                             for key in config_check.REQUIRED
                             if key.startswith("CONFIG_PACKAGE_")}
        for package in sorted(required_packages):
            runtime_name = RUNTIME_PACKAGE_NAMES.get(package, package)
            if runtime_name not in packages:
                raise ValueError(f"Missing runtime package: {runtime_name}")
        forbidden = sorted(p for p in packages if config_check.is_offload_package(p))
        if forbidden:
            raise ValueError(f"NSS/offload found in firmware: {forbidden}")
    return [item["name"] for item in images]


if __name__ == "__main__":
    try:
        for name in check(pathlib.Path(sys.argv[1])):
            print(f"Verified firmware: {name}")
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"Firmware verification failed: {error}")
