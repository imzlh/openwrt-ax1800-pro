"""Exercise the router's image-format guard with actual tar commands."""
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERIES = ("24.10", "25.12")


def platform_check_function(series):
    patch = (ROOT / "patches" / series /
             "0001-jdcloud-re-ss-01-integration.patch").read_text()
    section = patch.split(
        "+++ b/target/linux/qualcommax/ipq60xx/base-files/lib/upgrade/platform.sh\n",
        1)[1].split("\ndiff --git ", 1)[0]
    # Reconstruct the resulting function from context and added patch lines.
    updated = "\n".join(line[1:] for line in section.splitlines()
                        if line.startswith((" ", "+"))) + "\n"
    return re.search(r"(?ms)^platform_check_image\(\) \{\n.*?^\}\n", updated)[0]


def upgrade_tar(missing=None, board="jdcloud_re-ss-01"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in ("CONTROL", "kernel", "root"):
            if name == missing:
                continue
            contents = b"payload"
            member = tarfile.TarInfo(f"sysupgrade-{board}/{name}")
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    # fwtool also appends metadata after tar's end-of-archive blocks.
    return output.getvalue() + b"trailing metadata outside the tar archive"


@unittest.skipUnless(os.name == "posix" and shutil.which("sh") and shutil.which("tar"),
                     "Router shell checks require POSIX sh and tar")
class PlatformUpgradeTests(unittest.TestCase):
    def check_image(self, series, contents):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "firmware.bin"
            image.write_bytes(contents)
            script = ("board_name() { echo jdcloud,re-ss-01; }\n" +
                      platform_check_function(series) + '\nplatform_check_image "$1"\n')
            return subprocess.run(["sh", "-c", script, "platform-test", str(image)],
                                  capture_output=True, text=True)

    def test_accepts_sysupgrade_with_trailing_metadata(self):
        for series in SERIES:
            with self.subTest(series=series):
                result = self.check_image(series, upgrade_tar())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_factory_and_incomplete_or_wrong_device_archives(self):
        images = {"raw FIT factory": b"\xd0\x0d\xfe\xed" + bytes(4096),
                  "other device": upgrade_tar(board="other-router")}
        images.update({f"missing {name}": upgrade_tar(missing=name)
                       for name in ("CONTROL", "kernel", "root")})
        for series in SERIES:
            for name, image in images.items():
                with self.subTest(series=series, image=name):
                    result = self.check_image(series, image)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("sysupgrade image", result.stdout)


if __name__ == "__main__":
    unittest.main()
