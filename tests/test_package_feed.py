"""Cover missed modules, unpublished themes, native indexes and release hashing."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_validation import FirmwareFixture, COLLECT

spec = importlib.util.spec_from_file_location("package_feed", ROOT / "scripts/package-feed.py")
FEED = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEED)


class PackageFeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source"
        (self.source / "tmp").mkdir(parents=True)
        (self.source / ".config").write_text(
            'CONFIG_TARGET_ARCH_PACKAGES="aarch64_cortex-a53"\n'
            'CONFIG_ALL_KMODS=y\nCONFIG_ALL_NONSHARED=y\n'
            'CONFIG_PACKAGE_kmod-tun=y\nCONFIG_PACKAGE_kmod-wireguard=m\n'
            'CONFIG_PACKAGE_kernel=y\nCONFIG_PACKAGE_fstools=y\n'
            'CONFIG_PACKAGE_libtest=m\n', encoding="utf-8")
        (self.source / "feeds.conf.default").write_text(
            "src-git luci https://example.org/luci.git^" + "a" * 40 + "\n",
            encoding="utf-8")
        records = [
            ("kmod-tun", "", ""), ("kmod-wireguard", "", ""),
            ("kmod-other-platform", "", "Depends: @TARGET_x86\n"),
            ("kernel", "", ""), ("fstools", "", ""),
            ("luci-theme-argon", "base", ""), ("official-app", "luci", ""),
            ("libtest", "base", "ABI-Version: 42\n"),
        ]
        (self.source / "tmp/.packageinfo").write_text("".join(
            f"Source-Makefile: package/{name}/Makefile\nPackage: {name}\nType: ipkg\n"
            + (f"Repository: {repository}\n" if repository else "") + extra
            + "Description: package\nPackage: fake-description\n@@\n"
            for name, repository, extra in records), encoding="utf-8")
        self.target = self.source / "bin/targets/qualcommax/ipq60xx"

    def fetch(self, url):
        return json.dumps({"version": 2, "architecture": "aarch64_cortex-a53",
                           "packages": {"official-app": "1-r1", "libtest": "1-r1"}}).encode()

    def resolve(self):
        FEED.select(self.source, "25.12", self.fetch)
        return FEED.finalize(self.source, "25.12")

    def create_repositories(self):
        self.resolve()
        for directory, names in (
                (self.target / "packages", ["kmod-tun", "kmod-wireguard", "kernel", "fstools"]),
                (self.source / "bin/packages/aarch64_cortex-a53/base", ["luci-theme-argon", "libtest"])):
            directory.mkdir(parents=True)
            (directory / "index.json").write_text(json.dumps(
                {"version": 2, "architecture": "aarch64_cortex-a53",
                 "packages": {name: "1-r1" for name in names}}), encoding="utf-8")
            (directory / "packages.adb").write_bytes(b"synthetic native APK index")
            for name in names:
                runtime = "libtest42" if name == "libtest" else name
                (directory / f"{runtime}-1-r1.apk").write_bytes(b"synthetic package")

    def test_selects_unpublished_shared_packages_and_preserves_firmware_selections(self):
        plan = self.resolve()
        self.assertEqual(plan["selected"]["kmod-tun"], "y")
        self.assertEqual(plan["selected"]["kmod-wireguard"], "m")
        self.assertEqual(plan["selected"]["luci-theme-argon"], "m")
        self.assertNotIn("official-app", plan["candidates"])
        self.assertNotIn("fake-description", plan["candidates"])
        self.assertIn("kmod-other-platform", plan["excluded"])
        self.assertEqual(len(plan["official_indexes"]), 2)

    def test_missing_module_file_or_lowercase_native_index_blocks_publication(self):
        self.create_repositories()
        inventory = FEED.verify(self.source, "25.12")
        self.assertEqual(len(inventory["packages"]), 6)
        self.assertIn("libtest42", inventory["packages"]["libtest"]["file"])
        module = self.target / "packages/kmod-wireguard-1-r1.apk"
        module.unlink()
        with self.assertRaisesRegex(ValueError, "kmod-wireguard"):
            FEED.verify(self.source, "25.12")
        module.write_bytes(b"synthetic package")
        (self.target / "packages/packages.adb").unlink()
        with self.assertRaisesRegex(ValueError, "packages.adb"):
            FEED.verify(self.source, "25.12")

    def test_configuration_changes_require_a_new_package_plan(self):
        self.create_repositories()
        with (self.source / ".config").open("a", encoding="utf-8") as config:
            config.write("CONFIG_PACKAGE_new-module=m\n")
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            FEED.verify(self.source, "25.12")

    @unittest.skipUnless(shutil.which("zstd"), "requires zstd")
    def test_complete_repositories_and_archive_are_in_release_checksums(self):
        self.create_repositories()
        fixture = FirmwareFixture(self.target, "25.12")
        output = Path(self.temporary.name) / "release"
        COLLECT.collect(self.target, output, fixture.build_info, "25.12",
                        self.source, "packages.tar.zst")
        compressed = output / "packages.tar.zst"
        tar_path = output / "uncompressed.tar"
        subprocess.run(["zstd", "-q", "-d", str(compressed), "-o", str(tar_path)], check=True)
        with tarfile.open(tar_path) as archive:
            names = archive.getnames()
        self.assertIn("targets/qualcommax/ipq60xx/packages/packages.adb", names)
        self.assertIn("targets/qualcommax/ipq60xx/packages/kernel-1-r1.apk", names)
        self.assertIn("packages/aarch64_cortex-a53/base/luci-theme-argon-1-r1.apk", names)
        self.assertIn("package-plan.json", names)
        checksums = dict(line.split("  ")[::-1] for line in
                         (output / "SHA256SUMS").read_text().splitlines())
        for name in ("packages.tar.zst", "package-plan.json", "package-inventory.json"):
            self.assertEqual(checksums[name], FEED.digest(output / name))


if __name__ == "__main__":
    unittest.main()
