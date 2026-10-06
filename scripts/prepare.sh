#!/usr/bin/env bash
# Run on Linux. Always start with a new source directory.
set -euo pipefail

PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RELEASE=24.10
SOURCE_DIR=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --release)
            [[ $# -ge 2 ]] || { echo '--release needs a series, e.g. 25.12' >&2; exit 1; }
            RELEASE=$2
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [SOURCE_DIR] [--release 24.10|25.12]"
            exit 0
            ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            [[ -z "$SOURCE_DIR" ]] || { echo 'Only one source directory is accepted' >&2; exit 1; }
            SOURCE_DIR=$1
            shift
            ;;
    esac
done

# Validate before creating any directories or contacting upstream.
python3 "$PROJECT_DIR/scripts/project.py" show --release "$RELEASE" >/dev/null
SOURCE_DIR=$(realpath -m -- "${SOURCE_DIR:-$PROJECT_DIR/openwrt-$RELEASE}")

if [[ -e "$SOURCE_DIR" ]]; then
    echo "Source directory already exists: $SOURCE_DIR. Choose a new empty path." >&2
    exit 1
fi

readarray -t SOURCES < <(python3 "$PROJECT_DIR/scripts/project.py" sources --release "$RELEASE")
[[ ${#SOURCES[@]} == 4 ]]
readarray -t PATCHES < <(python3 "$PROJECT_DIR/scripts/project.py" files --release "$RELEASE" --kind patches)
readarray -t CONFIGS < <(python3 "$PROJECT_DIR/scripts/project.py" files --release "$RELEASE" --kind config)
[[ ${#PATCHES[@]} -gt 0 && ${#CONFIGS[@]} -gt 0 ]]

checkout_locked() {
    local url=$1 commit=$2 destination=$3 attempt
    git init "$destination"
    git -C "$destination" remote add origin "$url"
    for attempt in 1 2 3; do
        if git -C "$destination" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=60 \
            fetch --depth 1 origin "$commit"; then
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

# Fetch the pinned feed commit directly, then let OpenWrt index it. This avoids
# downloading an unrelated branch HEAD before fetching the release revision.
readarray -t FEEDS < <(python3 - <<'PY'
import pathlib, re
lines = pathlib.Path("feeds.conf.default").read_text().splitlines()
feeds = [line for line in lines if line.strip() and not line.startswith("#")]
assert feeds, "Missing release feeds"
for line in feeds:
    assert re.fullmatch(r"src-git\s+[a-zA-Z0-9_-]+\s+https://[^\s^]+\^[0-9a-f]{40}", line), line
for line in feeds:
    _, name, url = line.split()
    url, commit = url.rsplit("^", 1)
    print(name, url, commit)
PY
)
[[ ${#FEEDS[@]} -gt 0 ]] || { echo 'Missing or unpinned release feeds' >&2; exit 1; }

for patch in "${PATCHES[@]}"; do
    git apply --check "$patch"
    git apply --index "$patch"
done

for feed in "${FEEDS[@]}"; do
    read -r name url commit <<< "$feed"
    checkout_locked "$url" "$commit" "feeds/$name"
done
./scripts/feeds update -i -a
./scripts/feeds install -a
checkout_locked "${SOURCES[2]}" "${SOURCES[3]}" package/luci-theme-argon

cat -- "${CONFIGS[@]}" > .config
mkdir -p files
cp -a -- "$PROJECT_DIR/files/." files/
chmod 0755 files/etc/uci-defaults/99-project-settings
make defconfig
python3 "$PROJECT_DIR/scripts/check-config.py" .config --release "$RELEASE"

echo "OpenWrt $RELEASE source ready: $SOURCE_DIR"
echo "Next: make download -j8, then make -j$(nproc) BUILD_LOG=1"
