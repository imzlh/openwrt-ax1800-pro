#!/usr/bin/env python3
"""Validate image formats, metadata and runtime packages before publication."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import tarfile
import zlib

from project import DEFAULT_RELEASE, load_release

spec = importlib.util.spec_from_file_location(
    "check_config", Path(__file__).with_name("check-config.py"))
config_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_check)

DEVICE = config_check.DEVICE
COMPATIBLE = "jdcloud,re-ss-01"
TARGET = "qualcommax/ipq60xx"
KERNEL_SIZE = 6144 * 1024
MIN_IMAGE_SIZE = 1024 * 1024
# Both pinned releases use this ABI, including apk's real package names.
RUNTIME_PACKAGE_NAMES = {"libustream-mbedtls": "libustream-mbedtls20201210"}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def local_file(directory, name):
    """Accept a plain, portable attachment name and a regular local file."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError(f"Invalid attachment name: {name!r}")
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
        raise ValueError(f"Missing or non-local attachment: {name}")
    return path


def image_prefix(release):
    return f"openwrt-{release['version']}-qualcommax-ipq60xx-{DEVICE}"


def embedded_metadata(path, release):
    """Read fwtool INFO, allowing an optional appended signature.

    fwtool uses a big-endian 16-byte trailer and CRC32 without the final XOR.
    The INFO payload starts with an eight-byte version/flags header.
    """
    end = path.stat().st_size
    with path.open("rb") as stream:
        for _ in range(2):
            if end < 16:
                break
            stream.seek(end - 16)
            magic, crc, kind, length = struct.unpack(">IIB3xI", stream.read(16))
            if magic != 0x46577830 or length < 16 or length > 32 * 1024 or length > end:
                raise ValueError(f"Missing or invalid fwtool metadata: {path.name}")
            stream.seek(0)
            remaining, actual_crc = end - 16, 0
            while remaining:
                data = stream.read(min(1024 * 1024, remaining))
                if not data:
                    raise ValueError(f"Truncated fwtool payload: {path.name}")
                actual_crc = zlib.crc32(data, actual_crc)
                remaining -= len(data)
            if actual_crc ^ 0xFFFFFFFF != crc:
                raise ValueError(f"fwtool checksum mismatch: {path.name}")
            start = end - length
            if kind == 0:
                end = start
                continue
            if kind != 1 or length < 24:
                raise ValueError(f"Invalid fwtool INFO trailer: {path.name}")
            stream.seek(start)
            if stream.read(8) != bytes(8):
                raise ValueError(f"Unsupported fwtool INFO header: {path.name}")
            data = json.loads(stream.read(length - 24))
            if not isinstance(data, dict) or data.get("metadata_version") != "1.1":
                raise ValueError(f"Unsupported image metadata version: {path.name}")
            supported = data.get("supported_devices")
            if (data.get("compat_version") != "1.0" or not isinstance(supported, list)
                    or COMPATIBLE not in supported):
                raise ValueError(f"Wrong image compatibility metadata: {path.name}")
            version = data.get("version", {})
            expected = {"dist": "OpenWrt", "version": release["version"],
                        "target": TARGET, "board": DEVICE}
            # Image metadata uses dynamic REVISION; profiles uses VERSION_CODE,
            # which is fixed in include/version.mk for a release tag.
            if (not isinstance(version, dict)
                    or any(version.get(key) != value for key, value in expected.items())
                    or not isinstance(version.get("revision"), str) or not version["revision"]):
                raise ValueError(f"Wrong embedded image version or device: {path.name}")
            return start
    raise ValueError(f"Missing fwtool INFO trailer: {path.name}")


def fit_size(stream, available, label):
    header = stream.read(40)
    if len(header) != 40 or header[:4] != b"\xd0\x0d\xfe\xed":
        raise ValueError(f"Missing FIT kernel: {label}")
    total = struct.unpack_from(">I", header, 4)[0]
    if not 40 <= total <= min(available, KERNEL_SIZE):
        raise ValueError(f"Invalid FIT kernel size: {label}")
    return total


def squashfs_size(stream, available, label):
    header = stream.read(96)
    if len(header) != 96 or header[:4] != b"hsqs":
        raise ValueError(f"Missing SquashFS rootfs: {label}")
    major = struct.unpack_from("<H", header, 28)[0]
    used = struct.unpack_from("<Q", header, 40)[0]
    if major != 4 or not 96 <= used <= available:
        raise ValueError(f"Invalid SquashFS size or version: {label}")
    return used


def region_digest(stream, offset, size):
    stream.seek(offset)
    digest = hashlib.sha256()
    while size:
        data = stream.read(min(size, 1024 * 1024))
        if not data:
            raise ValueError("Truncated image component")
        digest.update(data)
        size -= len(data)
    return digest.hexdigest()


def check_factory(path, payload_size, kernel_size):
    if payload_size <= KERNEL_SIZE + 96:
        raise ValueError(f"Truncated factory image: {path.name}")
    with path.open("rb") as stream:
        fit_size(stream, kernel_size, path.name)
        stream.seek(KERNEL_SIZE)
        root_size = squashfs_size(stream, payload_size - KERNEL_SIZE, path.name)
        return (region_digest(stream, 0, kernel_size),
                region_digest(stream, KERNEL_SIZE, root_size), kernel_size)


def check_sysupgrade(path, payload_size):
    prefix = f"sysupgrade-{DEVICE}"
    required = {f"{prefix}/{part}" for part in ("CONTROL", "kernel", "root")}
    try:
        with tarfile.open(path, "r:") as archive:
            entries = archive.getmembers()
            names = [entry.name for entry in entries]
            if len(names) != len(set(names)) or set(names) != required | {prefix}:
                raise ValueError(f"Wrong sysupgrade archive entries: {path.name}")
            for entry in entries:
                if entry.name == prefix:
                    if not entry.isdir():
                        raise ValueError("Invalid sysupgrade directory entry")
                elif not entry.isfile() or entry.offset_data + entry.size > payload_size:
                    raise ValueError(f"Invalid sysupgrade file entry: {entry.name}")
            control = archive.extractfile(f"{prefix}/CONTROL").read(4096)
            if control != f"BOARD={DEVICE}\n".encode():
                raise ValueError(f"Wrong sysupgrade CONTROL board: {path.name}")
            kernel = archive.getmember(f"{prefix}/kernel")
            if kernel.size > KERNEL_SIZE:
                raise ValueError(f"Sysupgrade kernel exceeds eMMC kernel partition: {path.name}")
            with archive.extractfile(kernel) as stream:
                fit_size(stream, kernel.size, path.name)
                # Include the complete kernel file, also covering external FIT data.
                kernel_hash = region_digest(stream, 0, kernel.size)
            root = archive.getmember(f"{prefix}/root")
            with archive.extractfile(root) as stream:
                size = squashfs_size(stream, root.size, path.name)
                root_hash = region_digest(stream, 0, size)
            return kernel_hash, root_hash, kernel.size
    except tarfile.TarError as error:
        raise ValueError(f"Invalid sysupgrade tar: {path.name}: {error}") from error


def check_manifest(path, release):
    packages = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9+_.-]*) - (\S+)", line)
        if not match or match[1] in packages:
            raise ValueError(f"Invalid or duplicate package manifest entry at line {number}")
        packages.add(match[1])
    for package in sorted(config_check.required_packages(release)):
        runtime_name = RUNTIME_PACKAGE_NAMES.get(package, package)
        if runtime_name not in packages:
            raise ValueError(f"Missing runtime package: {runtime_name}")
    forbidden = sorted(p for p in packages if config_check.is_offload_package(p))
    if forbidden:
        raise ValueError(f"NSS/offload found in firmware: {forbidden}")
    wrong_manager = sorted(p for p in packages if config_check.wrong_package_manager(p, release))
    if wrong_manager:
        raise ValueError(f"Unexpected runtime package manager: {wrong_manager}")


def verify(directory, series=DEFAULT_RELEASE):
    directory = Path(directory)
    release = load_release(series)
    profiles_path = local_file(directory, "profiles.json")
    data = json.loads(profiles_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("metadata_version") != 1:
        raise ValueError("Unsupported profiles.json schema")
    if data.get("target") != TARGET or data.get("version_number") != release["version"]:
        raise ValueError("Wrong firmware target or version")
    revision = data.get("version_code")
    if not isinstance(revision, str) or not revision:
        raise ValueError("Missing firmware revision")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or DEVICE not in profiles:
        raise ValueError(f"Missing firmware profile: {DEVICE}")
    profile = profiles[DEVICE]
    if (not isinstance(profile, dict) or not isinstance(profile.get("images"), list)
            or any(not isinstance(item, dict) for item in profile["images"])):
        raise ValueError("Invalid firmware image metadata")
    prefix = image_prefix(release)
    if profile.get("image_prefix") != prefix:
        raise ValueError("Wrong firmware image prefix")
    supported = profile.get("supported_devices")
    if not isinstance(supported, list) or COMPATIBLE not in supported:
        raise ValueError("Missing JDCloud RE-SS-01 compatibility metadata")
    images = []
    components = []
    for kind in ("sysupgrade", "factory"):
        selected = [item for item in profile["images"] if item.get("type") == kind]
        if len(selected) != 1:
            raise ValueError(f"Expected exactly one {kind} image, found {len(selected)}")
        item = selected[0]
        name = item.get("name")
        image = local_file(directory, name)
        if name != f"{prefix}-squashfs-{kind}.bin" or item.get("filesystem") != "squashfs":
            raise ValueError(f"Wrong {kind} image name or filesystem: {name}")
        size = image.stat().st_size
        if size < MIN_IMAGE_SIZE:
            raise ValueError(f"Truncated image: {name}")
        # v24.10 has no size field; v25.12 always emits one.
        if "size" in item or release["package_manager"] == "apk":
            if type(item.get("size")) is not int or size != item["size"]:
                raise ValueError(f"Image size mismatch: {name}")
        if sha256(image) != item.get("sha256"):
            raise ValueError(f"Image checksum mismatch: {name}")
        payload_size = embedded_metadata(image, release)
        components.append(check_sysupgrade(image, payload_size) if kind == "sysupgrade"
                          else check_factory(image, payload_size, components[0][2]))
        images.append(name)
    if components[0] != components[1]:
        raise ValueError("Factory and sysupgrade kernel/rootfs contents differ")
    manifest = f"{prefix}.manifest"
    check_manifest(local_file(directory, manifest), release)
    return {"images": images, "manifest": manifest, "profiles": "profiles.json",
            "kernel_bytes": components[0][2],
            "kernel_free_bytes": KERNEL_SIZE - components[0][2]}


def check(directory, series=DEFAULT_RELEASE):
    """Return only the names of successfully validated release images."""
    return verify(directory, series)["images"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    args = parser.parse_args()
    verified = verify(args.directory, args.release)
    for name in verified["images"]:
        print(f"Verified firmware: {name}")
    print(f"FIT kernel: {verified['kernel_bytes']} bytes; "
          f"free in 6 MiB: {verified['kernel_free_bytes']} bytes")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        sys.exit(f"Firmware verification failed: {error}")
