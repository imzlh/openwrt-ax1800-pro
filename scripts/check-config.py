#!/usr/bin/env python3
"""Reject lost device selections, missing runtime packages, or NSS offload."""
import pathlib
import re
import sys

DEVICE = "jdcloud_re-ss-01"
TARGET = f"CONFIG_TARGET_qualcommax_ipq60xx_DEVICE_{DEVICE}"
REQUIRED = {
    "CONFIG_TARGET_qualcommax", "CONFIG_TARGET_qualcommax_ipq60xx", TARGET,
    "CONFIG_TARGET_ROOTFS_SQUASHFS", "CONFIG_JSON_OVERVIEW_IMAGE_INFO",
    *(f"CONFIG_PACKAGE_{p}" for p in (
        "kmod-qca-nss-dp", "kmod-ath11k-ahb", "ath11k-firmware-ipq6018",
        "ipq-wifi-jdcloud_re-ss-01", "e2fsprogs", "kmod-fs-ext4", "losetup",
        "luci-mod-admin-full", "luci-app-firewall", "luci-theme-argon",
        "luci-i18n-base-zh-cn", "luci-i18n-firewall-zh-cn",
        "luci-proto-ipv6", "luci-proto-ppp", "uhttpd", "uhttpd-mod-ubus",
        "libustream-mbedtls", "px5g-mbedtls",
    )),
}


def is_offload_package(name):
    # qca-nss-dp is the official Ethernet driver, not the optional offload stack.
    return name != "kmod-qca-nss-dp" and bool(re.search(
        r"(^|[-_])(nss|ecm|sfe)([-_]|$)|^luci-app-(turboacc|nss)", name
    ))


def check(path):
    config = {}
    for line in path.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            config[key] = value
    errors = [f"Missing selection: {key}=y" for key in sorted(REQUIRED)
              if config.get(key) != "y"]
    devices = [key for key, value in config.items()
               if re.match(r"CONFIG_TARGET_.*_DEVICE_", key) and value == "y"]
    if devices != [TARGET]:
        errors.append(f"Expected only {TARGET}, found {devices}")
    for key, value in config.items():
        if key.startswith("CONFIG_PACKAGE_") and value in ("y", "m"):
            if is_offload_package(key.removeprefix("CONFIG_PACKAGE_")):
                errors.append(f"Unexpected NSS/offload package: {key}")
    return errors


if __name__ == "__main__":
    errors = check(pathlib.Path(sys.argv[1]))
    if errors:
        sys.exit("\n".join(errors))
    print("Configuration verified: AX1800 Pro, LuCI/Argon, no NSS offload.")
