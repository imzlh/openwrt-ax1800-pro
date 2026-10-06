"""Release selection and diagnostics must work before a source tree exists."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project


def run_script(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                          capture_output=True, text=True, check=False)


class ReleaseSelectionTests(unittest.TestCase):
    def test_matrix_selects_one_or_both_releases(self):
        for selection, expected in (("all", ["24.10", "25.12"]),
                                    ("24.10", ["24.10"]), ("25.12", ["25.12"])):
            with self.subTest(selection=selection):
                result = run_script("project.py", "matrix", "--release", selection)
                self.assertEqual(result.returncode, 0, result.stderr)
                # GITHUB_OUTPUT requires the entire value on one line.
                self.assertEqual(len(result.stdout.splitlines()), 1)
                entries = json.loads(result.stdout)["include"]
                self.assertEqual([entry["series"] for entry in entries], expected)

    def test_unknown_release_has_no_matrix_output(self):
        result = run_script("project.py", "matrix", "--release", "26.10")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Unsupported release", result.stderr)

    def test_version_cannot_be_mapped_to_the_wrong_series(self):
        lock = json.loads(project.LOCK_FILE.read_text())
        lock["releases"]["24.10"]["version"] = "25.12.5"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sources.lock.json"
            path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "Version does not belong"):
                project.load_releases(path)

    def test_configuration_cannot_reference_files_outside_project(self):
        lock = json.loads(project.LOCK_FILE.read_text())
        lock["releases"]["24.10"]["config"] = ["../external.config"]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sources.lock.json"
            path.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "within the project"):
                project.load_releases(path)

    def test_failed_preparation_still_has_honest_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "not-created"
            output = Path(temporary) / "diagnostics"
            result = run_script("build-info.py", source, output, "--release", "25.12")
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads((output / "build-info.json").read_text())
            self.assertEqual(info["series"], "25.12")
            self.assertEqual(info["package_manager"], "apk")
            self.assertIsNone(info["openwrt_commit"])
            self.assertIsNone(info["argon_commit"])
            self.assertEqual(info["feeds"], {})
            self.assertFalse(source.exists())
            self.assertTrue(info["patches_sha256"])
            self.assertTrue(all(path.startswith(("patches/common/", "patches/25.12/"))
                                for path in info["patches_sha256"]))


if __name__ == "__main__":
    unittest.main()
