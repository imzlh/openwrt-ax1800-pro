#!/usr/bin/env python3
"""Select unpublished packages and audit the complete matching package feed."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

from project import DEFAULT_RELEASE, load_release, wrong_package_manager

PLAN = "package-plan.json"
INVENTORY = "package-inventory.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_config(source):
    return dict(line.split("=", 1) for line in
                (source / ".config").read_text(encoding="utf-8").splitlines()
                if line.startswith("CONFIG_") and "=" in line)


def metadata(source):
    packages, current, makefile = {}, None, None
    multiline = False
    for line in (source / "tmp/.packageinfo").read_text(encoding="utf-8").splitlines():
        if line == "@@":
            multiline = False
            continue
        if multiline:
            continue
        if line.startswith("Source-Makefile: "):
            makefile, current = line.split(": ", 1)[1], None
        elif line.startswith("Package: "):
            current = {"makefile": makefile}
            packages[line.split(": ", 1)[1]] = current
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value.strip()
            if key in ("Description", "Config"):
                multiline = True
    if not packages:
        raise ValueError("Missing resolved OpenWrt package metadata")
    return packages


def official_indexes(source, release, fetch=None):
    config = read_config(source)
    architecture = config["CONFIG_TARGET_ARCH_PACKAGES"].strip('"')
    feeds = [line.split()[1] for line in
             (source / "feeds.conf.default").read_text(encoding="utf-8").splitlines()
             if line.startswith("src-git ")]
    result, records = set(), []
    for feed in sorted(set(["base", *feeds])):
        url = (f"https://downloads.openwrt.org/releases/{release['version']}"
               f"/packages/{architecture}/{feed}/index.json")
        if fetch is not None:
            data = fetch(url)
        else:
            for attempt in range(3):
                try:
                    with urlopen(url, timeout=45) as response:
                        data = response.read()
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(2)
        index = json.loads(data)
        if index.get("version") != 2 or index.get("architecture") != architecture:
            raise ValueError(f"Unexpected official package index: {url}")
        result.update(index["packages"])
        records.append({"url": url, "sha256": hashlib.sha256(data).hexdigest(),
                        "packages": len(index["packages"])})
    return result, records


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def select(source, series=DEFAULT_RELEASE, fetch=None):
    release = load_release(series)
    config, packages = read_config(source), metadata(source)
    for symbol in ("CONFIG_ALL_KMODS", "CONFIG_ALL_NONSHARED"):
        if config.get(symbol) != "y":
            raise ValueError(f"Required native package selection: {symbol}=y")
    official, indexes = official_indexes(source, release, fetch)
    candidates, policy_excluded = {}, {}
    for name, package in sorted(packages.items()):
        if "ipkg" not in package.get("Type", "").split() or package.get("Build-Only"):
            continue
        if wrong_package_manager(name, release):
            policy_excluded[name] = {
                "reason": "incompatible-package-manager",
                "explanation": f"This release uses {release['package_manager']}"}
            continue
        reason = ("kernel-abi" if name.startswith("kmod-") else
                  "target-specific" if not package.get("Repository") else
                  "unpublished" if name not in official else None)
        if reason:
            candidates[name] = {"reason": reason,
                                "depends": package.get("Depends", ""),
                                "menu_depends": package.get("Menu-Depends", ""),
                                "hidden": bool(package.get("Hidden"))}
    # Native Kconfig handles nonshared packages. Unpublished shared packages
    # need an explicit m, including themes outside the official feeds.
    missing = [name for name, entry in candidates.items()
               if entry["reason"] == "unpublished" and not entry["hidden"]
               and config.get(f"CONFIG_PACKAGE_{name}") not in ("y", "m")]
    with (source / ".config").open("a", encoding="utf-8", newline="\n") as output:
        output.write("\n# Shared packages absent from official release feeds.\n")
        for name in sorted(policy_excluded):
            output.write(f"# CONFIG_PACKAGE_{name} is not set\n")
        for name in missing:
            output.write(f"CONFIG_PACKAGE_{name}=m\n")
    plan = {"schema_version": 1, "series": series, "version": release["version"],
            "official_indexes": indexes, "candidates": candidates,
            "policy_excluded": policy_excluded,
            "metadata_sha256": digest(source / "tmp/.packageinfo")}
    write_json(source / PLAN, plan)
    print(f"Requested {len(candidates)} matching/unpublished packages; "
          f"added {len(missing)} shared packages as m.")
    return plan


def finalize(source, series=DEFAULT_RELEASE):
    plan = json.loads((source / PLAN).read_text(encoding="utf-8"))
    release = load_release(series)
    if (plan["series"], plan["version"]) != (series, release["version"]):
        raise ValueError("Package plan release mismatch")
    config, packages = read_config(source), metadata(source)
    plan["selected"] = {name: config[f"CONFIG_PACKAGE_{name}"]
                        for name, package in sorted(packages.items())
                        if config.get(f"CONFIG_PACKAGE_{name}") in ("y", "m")
                        and "ipkg" in package.get("Type", "").split()
                        and not package.get("Build-Only")}
    plan["excluded"] = {name: {**entry,
                              "explanation": "Not selected after native target/dependency resolution"}
                        for name, entry in plan["candidates"].items()
                        if name not in plan["selected"]}
    plan["excluded"].update(plan.get("policy_excluded", {}))
    plan["config_sha256"] = digest(source / ".config")
    plan["metadata_sha256"] = digest(source / "tmp/.packageinfo")
    write_json(source / PLAN, plan)
    modules = sum(name.startswith("kmod-") for name in plan["selected"])
    print(f"Resolved {len(plan['selected'])} installable packages, {modules} kmods; "
          f"{len(plan['excluded'])} target/dependency exclusions recorded in {PLAN}.")
    return plan


def repositories(source):
    target = source / "bin/targets/qualcommax/ipq60xx/packages"
    if not target.is_dir():
        raise ValueError(f"Missing target package repository: {target}")
    return [target, *sorted(path.parent for path in
                           (source / "bin/packages").glob("*/*/index.json"))]


def verify(source, series=DEFAULT_RELEASE):
    release = load_release(series)
    plan = json.loads((source / PLAN).read_text(encoding="utf-8"))
    if (plan["series"], plan["version"]) != (series, release["version"]):
        raise ValueError("Package plan release mismatch")
    if plan.get("config_sha256") != digest(source / ".config"):
        raise ValueError("Build configuration changed after package planning")
    if plan.get("metadata_sha256") != digest(source / "tmp/.packageinfo"):
        raise ValueError("Package metadata changed after package planning")
    packages = metadata(source)
    found, files, errors = {}, {}, []
    for repository in repositories(source):
        index_path = repository / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if index.get("version") != 2:
            raise ValueError(f"Expected ABI-normalized v2 package index: {index_path}")
        native = "packages.adb" if release["package_manager"] == "apk" else "Packages.gz"
        if not (repository / native).is_file():
            raise ValueError(f"Missing installable repository index: {repository / native}")
        for name, version in index["packages"].items():
            if name not in plan["selected"]:
                continue
            abi_files = sorted(source.glob(f"staging_dir/target-*/pkginfo/{name}.version"))
            abi = (abi_files[0].read_text(encoding="utf-8").strip() if len(abi_files) == 1
                   else packages[name].get("ABI-Version", ""))
            runtime = name
            if abi and not name.startswith("kmod-"):
                runtime += ("-" if name[-1].isdigit() else "") + abi
            patterns = ([f"{runtime}-{version}.apk"] if native == "packages.adb"
                        else [f"{runtime}_{version}_*.ipk"])
            matches = [p for pattern in patterns for p in repository.glob(pattern) if p.is_file()]
            if len(matches) != 1:
                errors.append(f"Expected one package file for {name} {version}: {repository}")
                continue
            found[name] = {"version": version, "selection": plan["selected"][name],
                           "file": matches[0].relative_to(source / "bin").as_posix()}
        for path in sorted(repository.iterdir()):
            if path.is_file():
                files[path.relative_to(source / "bin").as_posix()] = digest(path)
    errors.extend(f"Missing built package: {name}" for name in
                  sorted(set(plan["selected"]) - found.keys()))
    if errors:
        raise ValueError("\n".join(errors))
    inventory = {"schema_version": 1, "series": series, "version": release["version"],
                 "packages": dict(sorted(found.items())), "files_sha256": files,
                 "excluded": plan["excluded"]}
    write_json(source / INVENTORY, inventory)
    print(f"Verified {len(found)} packages including "
          f"{sum(n.startswith('kmod-') for n in found)} kmods; "
          "all selected packages and native indexes are present.")
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "plan", "verify"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    args = parser.parse_args()
    try:
        {"select": select, "plan": finalize, "verify": verify}[args.command](
            args.source.resolve(), args.release)
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Package feed {args.command} failed: {error}")
