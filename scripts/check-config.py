#!/usr/bin/env python3
"""Reject lost device selections, missing runtime packages, or NSS offload."""
import argparse
from pathlib import Path
import re
import sys

from project import DEFAULT_RELEASE, load_release

DEVICE = "jdcloud_re-ss-01"
TARGET = f"CONFIG_TARGET_qualcommax_ipq60xx_DEVICE_{DEVICE}"
PACKAGES = {
    "kmod-qca-nss-dp", "kmod-ath11k-ahb", "ath11k-firmware-ipq6018",
    "ipq-wifi-jdcloud_re-ss-01", "e2fsprogs", "kmod-fs-ext4", "losetup",
    "luci-base", "luci-mod-admin-full", "luci-app-firewall", "luci-theme-argon",
    "luci-i18n-base-zh-cn", "luci-i18n-firewall-zh-cn",
    "luci-proto-ipv6", "luci-proto-ppp", "rpcd-mod-rrdns",
    "uhttpd", "uhttpd-mod-ubus", "libustream-mbedtls", "px5g-mbedtls", "ca-bundle",
}
REQUIRED = {
    "CONFIG_TARGET_qualcommax", "CONFIG_TARGET_qualcommax_ipq60xx", TARGET,
    "CONFIG_TARGET_ROOTFS_SQUASHFS", "CONFIG_JSON_OVERVIEW_IMAGE_INFO",
    "CONFIG_LUCI_LANG_zh_Hans",
    *(f"CONFIG_PACKAGE_{package}" for package in PACKAGES),
}


def is_offload_package(name):
    # qca-nss-dp is the official Ethernet driver, not the optional offload stack.
    return name != "kmod-qca-nss-dp" and bool(re.search(
        r"(^|[-_])(nss|ecm|sfe)([-_]|$)|^luci-app-(turboacc|nss)", name
    ))


def required_packages(release):
    manager = "apk-mbedtls" if release["package_manager"] == "apk" else "opkg"
    return PACKAGES | {manager}


def wrong_package_manager(name, release):
    return (name == "opkg" if release["package_manager"] == "apk"
            else name == "apk" or name.startswith("apk-"))


def check(path, series=DEFAULT_RELEASE):
    release = load_release(series)
    config = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            config[key] = value
    required = REQUIRED | {f"CONFIG_PACKAGE_{p}" for p in required_packages(release)}
    if release["package_manager"] == "apk":
        required.add("CONFIG_USE_APK")
    errors = [f"Missing selection: {key}=y" for key in sorted(required)
              if config.get(key) != "y"]
    if release["package_manager"] == "opkg" and config.get("CONFIG_USE_APK") == "y":
        errors.append("OpenWrt release requires opkg, but CONFIG_USE_APK=y")
    devices = [key for key, value in config.items()
               if re.match(r"CONFIG_TARGET_.*_DEVICE_", key) and value == "y"]
    if devices != [TARGET]:
        errors.append(f"Expected only {TARGET}, found {devices}")
    for key, value in config.items():
        if key.startswith("CONFIG_PACKAGE_") and value in ("y", "m"):
            package = key.removeprefix("CONFIG_PACKAGE_")
            if is_offload_package(package):
                errors.append(f"Unexpected NSS/offload package: {key}")
            if wrong_package_manager(package, release):
                errors.append(f"Unexpected package manager: {key}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    args = parser.parse_args()
    errors = check(args.config, args.release)
    if errors:
        raise ValueError("\n".join(errors))
    print(f"Configuration verified: OpenWrt {args.release}, AX1800 Pro, LuCI/Argon, no NSS offload.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Configuration verification failed: {error}")
