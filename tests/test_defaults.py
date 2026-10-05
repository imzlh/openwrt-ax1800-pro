"""Run first-boot defaults in a POSIX shell with isolated UCI/Wi-Fi mocks.

These cover configuration and retry behavior, not actual ath11k hardware.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "files/etc/uci-defaults/99-project-settings"
MOCK = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

path = Path(os.environ["MOCK_STATE"])
state = json.loads(path.read_text())
args = [arg for arg in sys.argv[1:] if arg != "-q"]
tool = Path(sys.argv[0]).name
state["calls"].append([tool, *args])
code = 0
output = None
data = state["data"]
if tool == "uci":
    command, key = args[:2]
    if " ".join(args) == os.environ.get("MOCK_FAIL_COMMAND"):
        code = 1
    elif command == "get":
        output = data.get(key)
        code = 0 if key in data else 1
    elif command == "show":
        output = "\n".join(f"{k}={v}" for k, v in data.items()
                           if k.startswith(key + "."))
    elif command == "set":
        key, value = key.split("=", 1)
        data[key] = value
    elif command == "commit":
        state["committed"][key] = {k: v for k, v in data.items()
                                    if k.startswith(key + ".")}
    elif command == "revert":
        state["data"] = {k: v for k, v in data.items()
                         if not k.startswith(key + ".")}
        state["data"].update(state["committed"].get(key, {}))
    else:
        raise AssertionError(args)
elif tool == "wifi":
    assert args == ["config"], args
    state["wifi_calls"] += 1
    if state["wifi_calls"] >= int(os.environ.get("MOCK_WIFI_READY_AFTER", "1")):
        for key, value in state["generated_wireless"].items():
            data.setdefault(key, value)
elif tool == "touch":
    assert args[0] in ("/etc/config/argon", "/etc/config/project_settings"), args
elif tool == "sleep":
    assert args == ["1"], args
else:
    raise AssertionError(tool)
path.write_text(json.dumps(state))
if output is not None:
    print(output)
sys.exit(code)
'''


@unittest.skipUnless(os.name == "posix", "requires a POSIX shell; run with Linux/WSL")
class DefaultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state_path = self.directory / "state.json"
        self.environment = {**os.environ,
                            "PATH": f"{self.directory}:{os.environ['PATH']}",
                            "MOCK_STATE": str(self.state_path)}
        for command in ("uci", "wifi", "touch", "sleep"):
            executable = self.directory / command
            executable.write_text(MOCK)
            executable.chmod(0o755)
        data = {
            "luci.main": "core", "luci.main.lang": "auto",
            "system.@system[0]": "system", "argon.@global[0]": "global",
            "network.lan": "interface", "network.lan.ipaddr": "192.168.1.1",
        }
        wireless = {}
        # Nonconsecutive IDs and reversed band order catch radio0/1 assumptions.
        for radio, band in (("radio7", "5g"), ("radio2", "2g")):
            wireless.update({
                f"wireless.{radio}": "wifi-device",
                f"wireless.{radio}.band": band,
                f"wireless.default_{radio}": "wifi-iface",
                f"wireless.default_{radio}.device": radio,
                f"wireless.default_{radio}.disabled": "1",
                f"wireless.default_{radio}.ssid": "OpenWrt",
                f"wireless.default_{radio}.encryption": "none",
                f"wireless.default_{radio}.key": "",
            })
        self.save({"data": data, "committed": {}, "calls": [], "wifi_calls": 0,
                   "generated_wireless": wireless})

    def save(self, state):
        self.state_path.write_text(json.dumps(state))

    def read(self):
        return json.loads(self.state_path.read_text())

    def run_defaults(self, **environment):
        return subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True,
                              env={**self.environment, **environment}, check=False)

    def assert_defaults_committed(self):
        committed = self.read()["committed"]
        self.assertEqual(committed["network"]["network.lan.proto"], "static")
        self.assertEqual(committed["network"]["network.lan.ipaddr"], "192.168.10.1")
        self.assertEqual(committed["network"]["network.lan.netmask"], "255.255.255.0")
        self.assertEqual(committed["luci"]["luci.main.lang"], "zh_cn")
        self.assertEqual(committed["argon"]["argon.@global[0].mode"], "dark")
        for radio in ("radio7", "radio2"):
            self.assertEqual(committed["wireless"][f"wireless.{radio}.disabled"], "0")
            for option, value in (("disabled", "0"), ("ssid", "OpenWrt"),
                                  ("encryption", "none"), ("key", ""),
                                  ("mode", "ap"), ("network", "lan")):
                self.assertEqual(committed["wireless"][f"wireless.default_{radio}.{option}"],
                                 value)
        self.assertEqual(committed["project_settings"]["project_settings.defaults.applied"],
                         "1")

    def test_fresh_boot_enables_both_bands_and_changes_lan(self):
        result = self.run_defaults()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_defaults_committed()

    def test_delayed_wifi_probe_is_waited_for(self):
        result = self.run_defaults(MOCK_WIFI_READY_AFTER="3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read()["wifi_calls"], 3)
        self.assert_defaults_committed()

    def test_missing_band_leaves_script_retryable_and_lan_accessible(self):
        state = self.read()
        full_wireless = state["generated_wireless"].copy()
        state["generated_wireless"] = {
            key: value for key, value in full_wireless.items() if "radio7" not in key}
        self.save(state)
        result = self.run_defaults()
        self.assertNotEqual(result.returncode, 0)
        state = self.read()
        self.assertEqual(state["wifi_calls"], 30)
        self.assertNotIn("project_settings.defaults.applied", state["data"])
        self.assertEqual(state["committed"]["network"]["network.lan.ipaddr"], "192.168.10.1")
        state["generated_wireless"] = full_wireless
        self.save(state)
        retry = self.run_defaults()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assert_defaults_committed()

    def test_failed_commit_does_not_mark_defaults_applied(self):
        for package in ("wireless", "project_settings"):
            with self.subTest(package=package):
                result = self.run_defaults(MOCK_FAIL_COMMAND=f"commit {package}")
                self.assertNotEqual(result.returncode, 0)
                # boot's global commit must not accidentally save a staged marker.
                self.assertNotIn("project_settings.defaults.applied", self.read()["data"])
        result = self.run_defaults()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_defaults_committed()

    def test_saved_project_settings_preserve_user_network_and_wifi(self):
        state = self.read()
        state["data"].update({"project_settings.defaults.applied": "1",
                              "network.lan.ipaddr": "10.42.0.1",
                              "wireless.default_radio7.ssid": "MyRouter",
                              "wireless.default_radio7.encryption": "sae"})
        self.save(state)
        result = self.run_defaults()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = self.read()
        self.assertEqual(actual["data"], state["data"])
        self.assertEqual(actual["wifi_calls"], 0)
        self.assertEqual(actual["committed"], {})


if __name__ == "__main__":
    unittest.main()
