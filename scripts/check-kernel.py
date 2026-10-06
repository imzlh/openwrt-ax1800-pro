#!/usr/bin/env python3
"""Verify that the selected networking features are linked into the kernel."""
import argparse
from pathlib import Path
import re
import sys

from project import DEFAULT_RELEASE, load_release, project_path


BUILTIN_MODULES = {
    "CONFIG_TUN": "drivers/net/tun.ko",
    "CONFIG_INET_DIAG": "net/ipv4/inet_diag.ko",
    "CONFIG_INET_TCP_DIAG": "net/ipv4/tcp_diag.ko",
    "CONFIG_INET_UDP_DIAG": "net/ipv4/udp_diag.ko",
    "CONFIG_INET_RAW_DIAG": "net/ipv4/raw_diag.ko",
    "CONFIG_NFT_SOCKET": "net/netfilter/nft_socket.ko",
    "CONFIG_NFT_TPROXY": "net/netfilter/nft_tproxy.ko",
    "CONFIG_NF_SOCKET_IPV4": "net/ipv4/netfilter/nf_socket_ipv4.ko",
    "CONFIG_NF_SOCKET_IPV6": "net/ipv6/netfilter/nf_socket_ipv6.ko",
    "CONFIG_NF_TPROXY_IPV4": "net/ipv4/netfilter/nf_tproxy_ipv4.ko",
    "CONFIG_NF_TPROXY_IPV6": "net/ipv6/netfilter/nf_tproxy_ipv6.ko",
    "CONFIG_NF_TABLES": "net/netfilter/nf_tables.ko",
    "CONFIG_NETFILTER_NETLINK": "net/netfilter/nfnetlink.ko",
    "CONFIG_NF_CONNTRACK": "net/netfilter/nf_conntrack.ko",
    "CONFIG_NF_DEFRAG_IPV4": "net/ipv4/netfilter/nf_defrag_ipv4.ko",
    "CONFIG_NF_DEFRAG_IPV6": "net/ipv6/netfilter/nf_defrag_ipv6.ko",
    "CONFIG_LIBCRC32C": "lib/libcrc32c.ko",
    "CONFIG_CRYPTO_CRC32C": "crypto/crc32c_generic.ko",
}


def find_kernel(source, release):
    version = re.compile(r"linux-" + re.escape(release["kernel_series"]) + r"\.\d+")
    candidates = sorted(path.parent for path in Path(source).glob(
        "build_dir/target-*/linux-qualcommax_ipq60xx/linux-*/.config"
    ) if path.is_file() and version.fullmatch(path.parent.name))
    if len(candidates) != 1:
        raise ValueError(f"Expected one configured Linux {release['kernel_series']} "
                         f"build for qualcommax/ipq60xx, found {len(candidates)}: {candidates}")
    return candidates[0]


def required_builtins(release):
    symbols = set()
    fragment = project_path(release["kernel_config"])
    for line in fragment.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(CONFIG_[A-Z0-9_]+)=y", line)
        if not match:
            raise ValueError(f"Expected a built-in selection in {fragment}: {line}")
        symbols.add(match[1])
    if symbols != BUILTIN_MODULES.keys():
        raise ValueError("Kernel fragment and built-in module checks disagree: "
                         f"{sorted(symbols ^ BUILTIN_MODULES.keys())}")
    return symbols


def check(source, series=DEFAULT_RELEASE, config_only=False):
    release = load_release(series)
    kernel = find_kernel(source, release)
    required = required_builtins(release)
    config = {}
    for line in (kernel / ".config").read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            config[key] = value
        elif match := re.fullmatch(r"# (CONFIG_[A-Z0-9_]+) is not set", line):
            config[match[1]] = "n"
    errors = [f"Expected {symbol}=y, found {config.get(symbol, 'missing')}"
              for symbol in sorted(required) if config.get(symbol) != "y"]
    if not config_only:
        module_file = kernel / "modules.builtin"
        if not module_file.is_file():
            errors.append(f"Missing built-in module list: {module_file}")
        else:
            modules = {line.strip().removeprefix("kernel/") for line in
                       module_file.read_text(encoding="utf-8").splitlines()}
            errors.extend(f"Missing from modules.builtin: {BUILTIN_MODULES[symbol]}"
                          for symbol in sorted(required)
                          if BUILTIN_MODULES[symbol] not in modules)
    if errors:
        raise ValueError("\n".join(errors))
    return kernel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    parser.add_argument("--config-only", action="store_true",
                        help="Check the resolved .config before kernel compilation")
    args = parser.parse_args()
    kernel = check(args.source, args.release, args.config_only)
    evidence = ".config" if args.config_only else ".config and modules.builtin"
    print(f"Kernel built-ins verified: OpenWrt {args.release}, {kernel.name}, {evidence}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Kernel verification failed: {error}")
