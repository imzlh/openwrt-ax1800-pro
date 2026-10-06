"""Regression tests for both pinned releases and publication failure modes.

Fixtures model upstream tar, fwtool and SquashFS headers; they are not bootable
router firmware and cannot validate actual flashability, Ethernet or Wi-Fi.
"""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
DEVICE = "jdcloud_re-ss-01"
TARGET_OPTION = f"CONFIG_TARGET_qualcommax_ipq60xx_DEVICE_{DEVICE}"
VERSIONS = {"24.10": "24.10.8", "25.12": "25.12.5"}
# Explicit expectations, independent of the validators' constants.
PACKAGES = {
    "kmod-qca-nss-dp", "kmod-ath11k-ahb", "ath11k-firmware-ipq6018",
    "ipq-wifi-jdcloud_re-ss-01", "e2fsprogs", "kmod-fs-ext4", "losetup",
    "luci-base", "luci-mod-admin-full", "luci-app-firewall", "luci-theme-argon",
    "luci-i18n-base-zh-cn", "luci-i18n-firewall-zh-cn", "luci-proto-ipv6",
    "luci-proto-ppp", "rpcd-mod-rrdns", "uhttpd", "uhttpd-mod-ubus",
    "libustream-mbedtls", "px5g-mbedtls", "ca-bundle",
    "kmod-tun", "kmod-inet-diag", "kmod-nft-socket", "kmod-nft-tproxy", "ip-full",
}
RUNTIME_PACKAGES = (PACKAGES - {"libustream-mbedtls"}) | {"libustream-mbedtls20201210"}


def config_text(series):
    manager = "opkg" if series == "24.10" else "apk-mbedtls"
    lines = ["CONFIG_TARGET_qualcommax=y", "CONFIG_TARGET_qualcommax_ipq60xx=y",
             f"{TARGET_OPTION}=y", "CONFIG_TARGET_ROOTFS_SQUASHFS=y",
             "CONFIG_JSON_OVERVIEW_IMAGE_INFO=y", "CONFIG_LUCI_LANG_zh_Hans=y",
             *(f"CONFIG_PACKAGE_{package}=y" for package in sorted(PACKAGES | {manager}))]
    if series == "25.12":
        lines.append("CONFIG_USE_APK=y")
    return "\n".join(lines) + "\n"


def load_script(filename):
    spec = importlib.util.spec_from_file_location(filename, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFIG_CHECK = load_script("check-config.py")
FIRMWARE_CHECK = load_script("check-firmware.py")
COLLECT = load_script("collect-release.py")


def run_script(filename, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / filename), *map(str, args)],
                          capture_output=True, text=True, check=False)


def fwtool_append(payload, metadata=None, signature=None):
    chunk = bytes(8) + json.dumps(metadata).encode() if metadata is not None else signature
    contents = payload + chunk
    trailer = struct.pack(">IIB3xI", 0x46577830, zlib.crc32(contents) ^ 0xFFFFFFFF,
                          1 if metadata is not None else 0, len(chunk) + 16)
    return contents + trailer


class FirmwareFixture:
    def __init__(self, directory, series="24.10"):
        self.directory, self.series = directory, series
        self.version = VERSIONS[series]
        self.prefix = f"openwrt-{self.version}-qualcommax-ipq60xx-{DEVICE}"
        self.names = [f"{self.prefix}-squashfs-{kind}.bin" for kind in ("sysupgrade", "factory")]
        self.kernel = bytearray(1024 * 1024)
        struct.pack_into(">II", self.kernel, 0, 0xD00DFEED, len(self.kernel))
        self.rootfs = bytearray(4096)
        self.rootfs[:4] = b"hsqs"
        struct.pack_into("<H", self.rootfs, 28, 4)
        struct.pack_into("<Q", self.rootfs, 40, len(self.rootfs))
        self.embedded = {"metadata_version": "1.1", "compat_version": "1.0",
                         "supported_devices": ["jdcloud,re-ss-01"],
                         "version": {"dist": "OpenWrt", "version": self.version,
                                     "target": "qualcommax/ipq60xx", "board": DEVICE,
                                     "revision": "r0-synthetic-dynamic-revision"}}
        self.profile = {"image_prefix": self.prefix, "supported_devices": ["jdcloud,re-ss-01"],
                        "images": []}
        self.metadata = {"metadata_version": 1, "target": "qualcommax/ipq60xx",
                         "version_number": self.version, "version_code": "r1-static-version-code",
                         "profiles": {DEVICE: self.profile}}
        self.payloads = [self.make_tar(), bytes(self.kernel).ljust(6144 * 1024, b"\0") + self.rootfs]
        for index in range(2):
            self.set_image(index, fwtool_append(self.payloads[index], self.embedded))
        self.manifest = directory / f"{self.prefix}.manifest"
        self.packages = RUNTIME_PACKAGES | {"opkg" if series == "24.10" else "apk-mbedtls"}
        self.write_manifest()
        release = FIRMWARE_CHECK.load_release(series)
        self.info = {"series": series, "version": self.version,
                     "openwrt_commit": release["commit"], "argon_commit": release["argon"]["commit"],
                     "package_manager": "opkg" if series == "24.10" else "apk"}
        self.build_info = directory / "build-info.json"
        self.write_build_info()

    def make_tar(self, control=None, extra=False, kernel=None):
        buffer = io.BytesIO()
        prefix = f"sysupgrade-{DEVICE}"
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            directory = tarfile.TarInfo(prefix)
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            data = {"CONTROL": control if control is not None else f"BOARD={DEVICE}\n".encode(),
                    "kernel": kernel if kernel is not None else self.kernel, "root": self.rootfs}
            if extra:
                data["unexpected"] = b"extra"
            for name, contents in data.items():
                member = tarfile.TarInfo(f"{prefix}/{name}")
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        return buffer.getvalue()

    def set_image(self, index, contents):
        name = self.names[index]
        (self.directory / name).write_bytes(contents)
        item = {"type": ("sysupgrade", "factory")[index], "filesystem": "squashfs",
                "name": name, "sha256": hashlib.sha256(contents).hexdigest()}
        if self.series == "25.12":
            item["size"] = len(contents)
        if index < len(self.profile["images"]):
            self.profile["images"][index] = item
        else:
            self.profile["images"].append(item)
        self.write_metadata()

    def write_metadata(self):
        (self.directory / "profiles.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def write_manifest(self, packages=None):
        packages = self.packages if packages is None else packages
        self.manifest.write_text("".join(f"{package} - 1.0-r1\n" for package in sorted(packages)),
                                 encoding="utf-8")

    def write_build_info(self):
        self.build_info.write_text(json.dumps(self.info), encoding="utf-8")


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = Path(self.temporary.name) / ".config"

    def test_both_release_configs_and_cli(self):
        for series in VERSIONS:
            with self.subTest(series=series):
                self.config.write_text(config_text(series))
                self.assertEqual(CONFIG_CHECK.check(self.config, series), [])
                result = run_script("check-config.py", self.config, "--release", series)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_lost_device_packages_or_metadata_are_rejected(self):
        for option in (TARGET_OPTION, "CONFIG_PACKAGE_luci-theme-argon",
                       "CONFIG_JSON_OVERVIEW_IMAGE_INFO", "CONFIG_PACKAGE_px5g-mbedtls"):
            with self.subTest(option=option):
                self.config.write_text(config_text("24.10").replace(f"{option}=y\n", ""))
                self.assertTrue(any(option in error for error in CONFIG_CHECK.check(self.config)))

    def test_nss_or_second_device_is_rejected(self):
        for selection in ("CONFIG_PACKAGE_kmod-qca-nss-drv=m",
                          "CONFIG_PACKAGE_luci-app-turboacc=y",
                          "CONFIG_TARGET_qualcommax_ipq60xx_DEVICE_other=y"):
            self.config.write_text(config_text("24.10") + selection + "\n")
            self.assertTrue(CONFIG_CHECK.check(self.config))

    def test_package_managers_cannot_be_swapped_or_mixed(self):
        for series, wrong in (("24.10", "CONFIG_USE_APK=y"),
                              ("24.10", "CONFIG_PACKAGE_apk-mbedtls=m"),
                              ("25.12", "CONFIG_PACKAGE_opkg=y")):
            self.config.write_text(config_text(series) + wrong + "\n")
            self.assertTrue(CONFIG_CHECK.check(self.config, series))
        self.config.write_text(config_text("25.12").replace("CONFIG_USE_APK=y\n", ""))
        self.assertTrue(CONFIG_CHECK.check(self.config, "25.12"))


class FirmwareValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.fixture = FirmwareFixture(self.directory)

    def assert_rejected(self, message):
        with self.assertRaisesRegex(ValueError, message):
            FIRMWARE_CHECK.check(self.directory, self.fixture.series)

    def test_both_release_formats_and_cli(self):
        for series in VERSIONS:
            fixture = FirmwareFixture(self.directory, series)
            self.assertEqual(FIRMWARE_CHECK.check(self.directory, series), fixture.names)
            result = run_script("check-firmware.py", self.directory, "--release", series)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Verified firmware: {fixture.names[1]}", result.stdout)

    def test_fwtool_signature_after_metadata_is_supported(self):
        for index, name in enumerate(self.fixture.names):
            signed = fwtool_append((self.directory / name).read_bytes(), signature=b"synthetic ucert")
            self.fixture.set_image(index, signed)
        self.assertEqual(FIRMWARE_CHECK.check(self.directory), self.fixture.names)

    def test_missing_or_duplicate_image_is_rejected(self):
        images = copy.deepcopy(self.fixture.profile["images"])
        for changed in (images[:1], images + [images[1]]):
            self.fixture.profile["images"] = changed
            self.fixture.write_metadata()
            self.assert_rejected("Expected exactly one factory")

    def test_wrong_schema_target_version_prefix_and_device(self):
        for key, value in (("metadata_version", 2), ("target", "qualcommax/ipq807x"),
                           ("version_number", "25.12.5")):
            original = self.fixture.metadata[key]
            self.fixture.metadata[key] = value
            self.fixture.write_metadata()
            self.assert_rejected("schema|target or version")
            self.fixture.metadata[key] = original
        self.fixture.profile["image_prefix"] += "-other"
        self.fixture.write_metadata()
        self.assert_rejected("image prefix")

    def test_missing_device_profile_has_friendly_cli_error(self):
        self.fixture.metadata["profiles"] = {}
        self.fixture.write_metadata()
        result = run_script("check-firmware.py", self.directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Firmware verification failed:", result.stderr)
        self.assertIn(DEVICE, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unsafe_names_are_rejected(self):
        for name in ("../escape.bin", "..\\escape.bin", "C:escape.bin", "/escape.bin",
                     "image\nfile.bin", "-image.bin", "image bin", "image:stream"):
            with self.subTest(name=name):
                self.fixture.profile["images"][0]["name"] = name
                self.fixture.write_metadata()
                self.assert_rejected("Invalid attachment name")

    def test_image_symlink_cannot_publish_an_external_file(self):
        image = self.directory / self.fixture.names[0]
        external = self.directory / "external-image.bin"
        image.rename(external)
        try:
            image.symlink_to(external)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        self.assert_rejected("Missing or non-local attachment")

    def test_invalid_profile_shapes_have_friendly_errors(self):
        for value in (None, [], {"images": None}, {"images": ["invalid"]}):
            self.fixture.metadata["profiles"][DEVICE] = value
            self.fixture.write_metadata()
            self.assert_rejected("Invalid firmware image metadata")

    def test_size_is_optional_only_for_24_10(self):
        self.fixture.profile["images"][0]["size"] = 1
        self.fixture.write_metadata()
        self.assert_rejected("size mismatch")
        self.fixture = FirmwareFixture(self.directory, "25.12")
        del self.fixture.profile["images"][0]["size"]
        self.fixture.write_metadata()
        self.assert_rejected("size mismatch")

    def test_checksum_and_truncation_are_rejected(self):
        image = self.directory / self.fixture.names[0]
        with image.open("r+b") as stream:
            stream.write(b"!")
        self.assert_rejected("checksum mismatch")
        self.fixture.set_image(0, b"short")
        self.assert_rejected("Truncated image")

    def test_manifest_requires_exact_prefix_and_runtime_packages(self):
        for package in ("luci-theme-argon", "libustream-mbedtls20201210", "px5g-mbedtls", "opkg"):
            self.fixture.write_manifest(self.fixture.packages - {package})
            self.assert_rejected(f"Missing runtime package: {package}")
        self.fixture.write_manifest()
        self.fixture.manifest.rename(self.directory / f"wrong-{DEVICE}.manifest")
        self.assert_rejected("Missing or non-local attachment")

    def test_manifest_rejects_offload_mixed_managers_and_malformed_lines(self):
        for package, message in (("kmod-qca-nss-ecm", "NSS/offload"),
                                 ("apk-mbedtls", "Unexpected runtime package manager")):
            self.fixture.write_manifest(self.fixture.packages | {package})
            self.assert_rejected(message)
        self.fixture.manifest.write_text("opkg\n")
        self.assert_rejected("Invalid or duplicate package manifest")

    def test_embedded_metadata_version_and_crc_are_checked(self):
        self.fixture.embedded["version"]["version"] = "wrong"
        self.fixture.set_image(0, fwtool_append(self.fixture.payloads[0], self.fixture.embedded))
        self.assert_rejected("Wrong embedded image version")
        original = bytearray((self.directory / self.fixture.names[0]).read_bytes())
        original[-12] ^= 1
        self.fixture.set_image(0, original)
        self.assert_rejected("fwtool checksum mismatch")

    def test_renamed_upgrade_is_not_a_factory_image(self):
        self.fixture.set_image(1, (self.directory / self.fixture.names[0]).read_bytes())
        self.assert_rejected("Truncated factory|Missing FIT")

    def test_factory_rootfs_must_start_after_6_mib(self):
        wrong = bytes(self.fixture.kernel).ljust(7 * 1024 * 1024, b"\0") + self.fixture.rootfs
        self.fixture.set_image(1, fwtool_append(wrong, self.fixture.embedded))
        self.assert_rejected("Missing SquashFS")

    def test_fit_and_squashfs_declared_sizes_must_fit_the_image(self):
        for offset, format_, size, message in ((4, ">I", 7 * 1024 * 1024, "FIT kernel size"),
                                               (6144 * 1024 + 40, "<Q", 2 ** 32, "SquashFS size")):
            payload = bytearray(self.fixture.payloads[1])
            struct.pack_into(format_, payload, offset, size)
            self.fixture.set_image(1, fwtool_append(payload, self.fixture.embedded))
            self.assert_rejected(message)

    def test_sysupgrade_requires_board_control_and_expected_entries(self):
        for payload, message in ((self.fixture.make_tar(control=b"BOARD=other\n"), "CONTROL board"),
                                 (self.fixture.make_tar(extra=True), "archive entries")):
            self.fixture.set_image(0, fwtool_append(payload, self.fixture.embedded))
            self.assert_rejected(message)

    def test_factory_and_upgrade_must_contain_same_rootfs(self):
        payload = bytearray(self.fixture.payloads[1])
        payload[-1] ^= 1
        self.fixture.set_image(1, fwtool_append(payload, self.fixture.embedded))
        self.assert_rejected("contents differ")


class ReleaseCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.fixture = FirmwareFixture(self.source)
        self.output = self.root / "release"

    def test_only_verified_attachments_are_staged_and_all_are_hashed(self):
        (self.source / "unverified.bin").write_bytes(b"not firmware")
        (self.source / "build.log").write_text("log")
        (self.source / f"unrelated-{DEVICE}.manifest").write_text("invalid")
        result = run_script("collect-release.py", self.source, self.output,
                            "--build-info", self.fixture.build_info)
        self.assertEqual(result.returncode, 0, result.stderr)
        names = set(self.fixture.names) | {self.fixture.manifest.name, "profiles.json", "build-info.json"}
        self.assertEqual({p.name for p in self.output.iterdir()}, names | {"SHA256SUMS"})
        checksums = {}
        for line in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            checksums[name] = digest
            self.assertEqual(digest, hashlib.sha256((self.output / name).read_bytes()).hexdigest())
        self.assertEqual(set(checksums), names)

    def test_bad_firmware_never_creates_release_directory(self):
        (self.source / self.fixture.names[1]).write_bytes(b"truncated")
        with self.assertRaises(ValueError):
            COLLECT.collect(self.source, self.output, self.fixture.build_info)
        self.assertFalse(self.output.exists())

    def test_wrong_build_provenance_never_creates_release_directory(self):
        for key in ("series", "version", "openwrt_commit", "argon_commit", "package_manager"):
            original = self.fixture.info[key]
            self.fixture.info[key] = "wrong"
            self.fixture.write_build_info()
            with self.assertRaisesRegex(ValueError, "Build provenance mismatch"):
                COLLECT.collect(self.source, self.output, self.fixture.build_info)
            self.assertFalse(self.output.exists())
            self.fixture.info[key] = original

    def test_existing_output_cannot_retain_unverified_stale_files(self):
        self.output.mkdir()
        stale = self.output / "old.bin"
        stale.write_bytes(b"keep user data")
        with self.assertRaisesRegex(ValueError, "output already exists"):
            COLLECT.collect(self.source, self.output, self.fixture.build_info)
        self.assertEqual(stale.read_bytes(), b"keep user data")


if __name__ == "__main__":
    unittest.main()
