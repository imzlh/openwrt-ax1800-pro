"""Validator regression tests using synthetic data, not built router firmware.

The JSON shape and manifest names follow OpenWrt v25.12.5 image.mk and
scripts/json_add_image_info.py. Passing these tests does not verify a build,
flashability, Ethernet, or Wi-Fi on actual hardware.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEVICE = "jdcloud_re-ss-01"
TARGET_OPTION = f"CONFIG_TARGET_qualcommax_ipq60xx_DEVICE_{DEVICE}"
PREFIX = f"openwrt-25.12.5-qualcommax-ipq60xx-{DEVICE}"
IMAGE_NAME = f"{PREFIX}-squashfs-sysupgrade.bin"

# Explicit expectations, independent of the validators' REQUIRED constants.
PACKAGES = {
    "kmod-qca-nss-dp", "kmod-ath11k-ahb", "ath11k-firmware-ipq6018",
    "ipq-wifi-jdcloud_re-ss-01", "kmod-fs-ext4", "losetup",
    "luci-base", "luci-mod-admin-full", "luci-app-firewall", "luci-theme-argon",
    "luci-i18n-base-zh-cn", "luci-i18n-firewall-zh-cn",
    "luci-proto-ipv6", "luci-proto-ppp", "rpcd-mod-rrdns",
    "uhttpd", "uhttpd-mod-ubus", "libustream-mbedtls", "px5g-mbedtls",
    "ca-bundle", "wget-any", "jsonfilter",
}
MANIFEST_PACKAGES = (PACKAGES - {"libustream-mbedtls"}) | {"libustream-mbedtls20201210"}
CONFIG = "\n".join([
    "CONFIG_TARGET_qualcommax=y", "CONFIG_TARGET_qualcommax_ipq60xx=y",
    f"{TARGET_OPTION}=y", "CONFIG_TARGET_ROOTFS_SQUASHFS=y",
    "CONFIG_JSON_OVERVIEW_IMAGE_INFO=y", "CONFIG_LUCI_LANG_zh_Hans=y",
    *(f"CONFIG_PACKAGE_{package}=y" for package in sorted(PACKAGES)),
]) + "\n"


def load_validator(filename):
    spec = importlib.util.spec_from_file_location(filename, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG_CHECK = load_validator("check-config.py")
FIRMWARE_CHECK = load_validator("check-firmware.py")


def run_validator(filename, path):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / filename), str(path)],
        capture_output=True, text=True, check=False,
    )


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = Path(self.temporary.name) / ".config"
        self.config.write_text(CONFIG, encoding="utf-8")

    def test_success_allows_official_ethernet_driver(self):
        self.assertEqual(CONFIG_CHECK.check(self.config), [])
        result = run_validator("check-config.py", self.config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Configuration verified:", result.stdout)

    def test_lost_device_selection_is_rejected(self):
        self.config.write_text(CONFIG.replace(f"{TARGET_OPTION}=y\n", ""))
        errors = CONFIG_CHECK.check(self.config)
        self.assertTrue(any(TARGET_OPTION in error for error in errors), errors)

    def test_missing_required_package_is_rejected(self):
        self.config.write_text(CONFIG.replace("CONFIG_PACKAGE_luci-theme-argon=y\n", ""))
        self.assertTrue(any("luci-theme-argon" in error
                            for error in CONFIG_CHECK.check(self.config)))

    def test_missing_image_metadata_is_rejected(self):
        self.config.write_text(CONFIG.replace("CONFIG_JSON_OVERVIEW_IMAGE_INFO=y\n", ""))
        self.assertTrue(any("CONFIG_JSON_OVERVIEW_IMAGE_INFO" in error
                            for error in CONFIG_CHECK.check(self.config)))

    def test_nss_is_rejected_even_as_loadable_module(self):
        for selection in ("y", "m"):
            with self.subTest(selection=selection):
                self.config.write_text(CONFIG + f"CONFIG_PACKAGE_kmod-qca-nss-drv={selection}\n")
                self.assertTrue(any("Unexpected NSS/offload" in error
                                    for error in CONFIG_CHECK.check(self.config)))


class FirmwareValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        # Deliberately not a bootable image: only exercise file/hash validation.
        payload = b"synthetic fixture\n".ljust(1024 * 1024, b"\0")
        self.image = self.directory / IMAGE_NAME
        self.image.write_bytes(payload)
        self.metadata = {
            "metadata_version": 1,
            "target": "qualcommax/ipq60xx",
            "version_number": "25.12.5",
            "version_code": "synthetic-fixture",
            "profiles": {DEVICE: {
                "image_prefix": PREFIX,
                "supported_devices": ["jdcloud,re-ss-01"],
                "images": [{
                    "type": "sysupgrade", "name": IMAGE_NAME,
                    "filesystem": "squashfs", "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            }},
        }
        self.write_metadata()
        self.manifest = self.directory / f"{PREFIX}.manifest"
        self.write_manifest(MANIFEST_PACKAGES)

    def write_metadata(self):
        (self.directory / "profiles.json").write_text(json.dumps(self.metadata))

    def write_manifest(self, packages):
        self.manifest.write_text("".join(f"{p} - 1.0-r1\n" for p in sorted(packages)))

    def test_success_reports_verified_image(self):
        self.assertEqual(FIRMWARE_CHECK.check(self.directory), [IMAGE_NAME])
        result = run_validator("check-firmware.py", self.directory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Verified firmware: {IMAGE_NAME}", result.stdout)

    def test_missing_device_profile_is_rejected(self):
        self.metadata["profiles"] = {}
        self.write_metadata()
        result = run_validator("check-firmware.py", self.directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Firmware verification failed:", result.stderr)
        self.assertIn(DEVICE, result.stderr)

    def test_unknown_metadata_schema_is_rejected(self):
        self.metadata["metadata_version"] = 2
        self.write_metadata()
        with self.assertRaises(ValueError):
            FIRMWARE_CHECK.check(self.directory)

    def test_missing_runtime_packages_are_rejected(self):
        for package in ("luci-theme-argon", "luci-i18n-base-zh-cn", "px5g-mbedtls",
                        "libustream-mbedtls20201210"):
            with self.subTest(package=package):
                self.write_manifest(MANIFEST_PACKAGES - {package})
                with self.assertRaisesRegex(ValueError, f"Missing runtime package: {package}"):
                    FIRMWARE_CHECK.check(self.directory)

    def test_nss_package_in_actual_manifest_is_rejected(self):
        self.write_manifest(MANIFEST_PACKAGES | {"kmod-qca-nss-ecm"})
        with self.assertRaisesRegex(ValueError, "NSS/offload found in firmware"):
            FIRMWARE_CHECK.check(self.directory)

    def test_image_corruption_is_rejected(self):
        with self.image.open("r+b") as stream:
            stream.write(b"!")
        with self.assertRaisesRegex(ValueError, "Image checksum mismatch"):
            FIRMWARE_CHECK.check(self.directory)


if __name__ == "__main__":
    unittest.main()
