"""Reject modular fallback and stale kernel build evidence in both releases."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("check_kernel", ROOT / "scripts/check-kernel.py")
KERNEL_CHECK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(KERNEL_CHECK)

CONFIG = """CONFIG_TUN=y
CONFIG_INET_DIAG=y
CONFIG_INET_TCP_DIAG=y
CONFIG_INET_UDP_DIAG=y
CONFIG_INET_RAW_DIAG=y
CONFIG_NFT_SOCKET=y
CONFIG_NFT_TPROXY=y
CONFIG_NF_SOCKET_IPV4=y
CONFIG_NF_SOCKET_IPV6=y
CONFIG_NF_TPROXY_IPV4=y
CONFIG_NF_TPROXY_IPV6=y
CONFIG_NF_TABLES=y
CONFIG_NETFILTER_NETLINK=y
CONFIG_NF_CONNTRACK=y
CONFIG_NF_DEFRAG_IPV4=y
CONFIG_NF_DEFRAG_IPV6=y
CONFIG_LIBCRC32C=y
CONFIG_CRYPTO_CRC32C=y
CONFIG_NFT_CT=m
"""
MODULES = """drivers/net/tun.ko
net/ipv4/inet_diag.ko
net/ipv4/tcp_diag.ko
net/ipv4/udp_diag.ko
net/ipv4/raw_diag.ko
net/netfilter/nft_socket.ko
net/netfilter/nft_tproxy.ko
net/ipv4/netfilter/nf_socket_ipv4.ko
net/ipv6/netfilter/nf_socket_ipv6.ko
net/ipv4/netfilter/nf_tproxy_ipv4.ko
net/ipv6/netfilter/nf_tproxy_ipv6.ko
net/netfilter/nf_tables.ko
net/netfilter/nfnetlink.ko
net/netfilter/nf_conntrack.ko
net/ipv4/netfilter/nf_defrag_ipv4.ko
net/ipv6/netfilter/nf_defrag_ipv6.ko
lib/libcrc32c.ko
crypto/crc32c_generic.ko
"""


class KernelValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name)

    def kernel(self, version="6.6.144", target="target-aarch64_cortex-a53_musl"):
        kernel = self.source / "build_dir" / target / "linux-qualcommax_ipq60xx" / f"linux-{version}"
        kernel.mkdir(parents=True)
        (kernel / ".config").write_text(CONFIG, encoding="utf-8")
        (kernel / "modules.builtin").write_text(MODULES, encoding="utf-8")
        return kernel

    def test_both_releases_and_optional_kernel_prefix(self):
        for series, version in (("24.10", "6.6.144"), ("25.12", "6.12.99")):
            with self.subTest(series=series):
                kernel = self.kernel(version)
                (kernel / "modules.builtin").write_text(
                    "".join(f"kernel/{line}\n" for line in MODULES.splitlines()), encoding="utf-8")
                self.assertEqual(KERNEL_CHECK.check(self.source, series), kernel)

    def test_modular_missing_or_disabled_selection_fails(self):
        kernel = self.kernel()
        for selection in ("CONFIG_TUN=m", "# CONFIG_TUN is not set", ""):
            with self.subTest(selection=selection):
                (kernel / ".config").write_text(CONFIG.replace("CONFIG_TUN=y", selection))
                with self.assertRaisesRegex(ValueError, "CONFIG_TUN=y"):
                    KERNEL_CHECK.check(self.source, config_only=True)

    def test_config_only_needs_no_compiled_module_list(self):
        kernel = self.kernel()
        (kernel / "modules.builtin").unlink()
        self.assertEqual(KERNEL_CHECK.check(self.source, config_only=True), kernel)
        with self.assertRaisesRegex(ValueError, "Missing built-in module list"):
            KERNEL_CHECK.check(self.source)

    def test_config_y_alone_does_not_prove_module_was_linked(self):
        kernel = self.kernel()
        (kernel / "modules.builtin").write_text(MODULES.replace("drivers/net/tun.ko\n", ""))
        with self.assertRaisesRegex(ValueError, "Missing from modules.builtin: drivers/net/tun.ko"):
            KERNEL_CHECK.check(self.source)

    def test_wrong_series_and_multiple_matching_builds_fail(self):
        release = KERNEL_CHECK.load_release("24.10")
        self.kernel("6.12.99")
        self.kernel("6.60.1")
        with self.assertRaisesRegex(ValueError, "found 0"):
            KERNEL_CHECK.find_kernel(self.source, release)
        self.kernel()
        self.kernel(target="target-aarch64_cortex-a53_musl_other")
        with self.assertRaisesRegex(ValueError, "found 2"):
            KERNEL_CHECK.find_kernel(self.source, release)

    def test_cli_reports_failed_check_without_traceback(self):
        kernel = self.kernel()
        (kernel / "modules.builtin").unlink()
        command = [sys.executable, str(ROOT / "scripts/check-kernel.py"), str(self.source),
                   "--release", "24.10"]
        result = subprocess.run(command + ["--config-only"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kernel verification failed:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
