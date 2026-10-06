#!/usr/bin/env bash
# Run on Linux. Always start with a new source directory.
set -euo pipefail

PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DIR=${1:-"$PROJECT_DIR/openwrt"}
SOURCE_DIR=$(realpath -m -- "$SOURCE_DIR")
LOCK_FILE=${2:-"$PROJECT_DIR/sources.lock.json"}
PATCH_DIR=${3:-"$PROJECT_DIR/patches"}
[[ "$LOCK_FILE" = /* ]] || LOCK_FILE="$PROJECT_DIR/$LOCK_FILE"
[[ "$PATCH_DIR" = /* ]] || PATCH_DIR="$PROJECT_DIR/$PATCH_DIR"
LOCK_FILE=$(realpath -m -- "$LOCK_FILE")
PATCH_DIR=$(realpath -m -- "$PATCH_DIR")

[[ -f "$LOCK_FILE" ]] || { echo "Missing source lock file: $LOCK_FILE" >&2; exit 1; }
[[ -d "$PATCH_DIR" ]] || { echo "Missing patch directory: $PATCH_DIR" >&2; exit 1; }

if [[ -e "$SOURCE_DIR" ]]; then
    echo "Source directory already exists: $SOURCE_DIR. Choose a new empty path." >&2
    exit 1
fi

readarray -t SOURCES < <(python3 - "$LOCK_FILE" <<'PY'
import json, re, sys
lock = json.load(open(sys.argv[1], encoding="utf-8"))
for name in ("openwrt", "argon"):
    assert re.fullmatch(r"[0-9a-f]{40}", lock[name]["commit"]), name
    print(lock[name]["url"])
    print(lock[name]["commit"])
PY
)
[[ ${#SOURCES[@]} == 4 ]]

checkout_locked() {
    local url=$1 commit=$2 destination=$3 attempt
    git init "$destination"
    git -C "$destination" remote add origin "$url"
    for attempt in 1 2 3; do
        if git -C "$destination" fetch --depth 1 origin "$commit"; then
            break
        fi
        [[ $attempt -lt 3 ]] || return 1
        sleep 5
    done
    git -C "$destination" checkout --detach FETCH_HEAD
    [[ $(git -C "$destination" rev-parse HEAD) == "$commit" ]]
}

checkout_locked "${SOURCES[0]}" "${SOURCES[1]}" "$SOURCE_DIR"
cd -- "$SOURCE_DIR"

# Release feeds are pinned upstream. Fail if an update introduces floating feeds.
python3 - <<'PY'
import pathlib, re
lines = pathlib.Path("feeds.conf.default").read_text().splitlines()
feeds = [line for line in lines if line.strip() and not line.startswith("#")]
assert feeds, "Missing release feeds"
for line in feeds:
    assert re.fullmatch(r"src-git\s+\S+\s+https://\S+\^[0-9a-f]{40}", line), line
PY

shopt -s nullglob
patches=("$PATCH_DIR"/*.patch)
[[ ${#patches[@]} -gt 0 ]] || { echo 'Missing device support patches' >&2; exit 1; }
for patch in "${patches[@]}"; do
    git apply --check "$patch"
    git apply --index "$patch"
done

# A failed pinned fetch can leave a feed at its initial branch HEAD.
verify_feeds() {
    python3 - <<'PY'
import pathlib, subprocess
for line in pathlib.Path("feeds.conf.default").read_text().splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    _, name, url = line.split()
    expected = url.rsplit("^", 1)[1]
    actual = subprocess.check_output(
        ["git", "-C", f"feeds/{name}", "rev-parse", "HEAD"], text=True).strip()
    assert actual == expected, f"Feed {name}: expected {expected}, got {actual}"
PY
}
for attempt in 1 2 3; do
    if ./scripts/feeds update -a && verify_feeds; then
        break
    fi
    [[ $attempt -lt 3 ]] || { echo 'Unable to fetch locked feeds' >&2; exit 1; }
    echo "Retrying locked feeds after attempt $attempt" >&2
    sleep 5
done
./scripts/feeds install -a
checkout_locked "${SOURCES[2]}" "${SOURCES[3]}" package/luci-theme-argon

cp -- "$PROJECT_DIR/config/ax1800pro.config" .config
mkdir -p files
cp -a -- "$PROJECT_DIR/files/." files/
chmod 0755 files/etc/uci-defaults/99-project-settings
make defconfig
python3 "$PROJECT_DIR/scripts/check-config.py" .config

echo "Source ready: $SOURCE_DIR"
echo "Next: make download -j8, then make -j$(nproc) BUILD_LOG=1"
