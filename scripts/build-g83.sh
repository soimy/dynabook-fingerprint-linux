#!/usr/bin/env bash
# Build the G83 stack locally. Nothing is installed into system directories.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --help ]]; then
    cat <<'EOF'
Usage: bash scripts/build-g83.sh

Build pinned libfprint + fprintd sources with the experimental 06cb:00d4 patch.
Build output is in .work/ by default; no sudo or system installation is used.
The local fprintd binary is compiled but is not installed or started.

Optional environment:
  G83_BUILD_ROOT   A dedicated source/build/output directory (default: .work)
  G83_BUILD_JOBS   Parallel build jobs (default: 2)
EOF
    exit 0
fi
if (( $# )); then
    echo "Unknown argument: $1 (see --help)" >&2
    exit 2
fi

for command in git meson ninja pkg-config python; do
    command -v "$command" >/dev/null || { echo "Required command not found: $command" >&2; exit 1; }
done

# shellcheck source=../sources.env
source "$ROOT/sources.env"
WORK=${G83_BUILD_ROOT:-"$ROOT/.work"}
JOBS=${G83_BUILD_JOBS:-2}
[[ $JOBS =~ ^[1-9][0-9]*$ ]] || { echo "G83_BUILD_JOBS must be a positive integer" >&2; exit 2; }
mkdir -p -- "$WORK"
WORK=$(cd -- "$WORK" && pwd)
PREFIX="$WORK/prefix"

checkout_source() {
    local url=$1 revision=$2 directory=$3
    if [[ ! -e $directory ]]; then
        git init --quiet "$directory"
        git -C "$directory" remote add origin "$url"
    fi
    [[ -d $directory/.git ]] || { echo "Not a dedicated git checkout: $directory" >&2; exit 1; }
    [[ $(git -C "$directory" remote get-url origin) == "$url" ]] || {
        echo "Unexpected source remote in $directory" >&2; exit 1;
    }
    if ! git -C "$directory" cat-file -e "$revision^{commit}" 2>/dev/null; then
        git -C "$directory" fetch --depth 1 origin "$revision"
    fi
    if ! git -C "$directory" rev-parse --verify HEAD >/dev/null 2>&1; then
        git -C "$directory" checkout --quiet --detach "$revision"
    fi
    [[ $(git -C "$directory" rev-parse HEAD) == "$revision" ]] || {
        echo "Unexpected revision in $directory. Use a fresh G83_BUILD_ROOT." >&2; exit 1;
    }
}

apply_patch_once() {
    local directory=$1 patch=$2
    if git -C "$directory" apply --check "$patch" 2>/dev/null; then
        git -C "$directory" apply "$patch"
    elif ! git -C "$directory" apply --reverse --check "$patch" 2>/dev/null; then
        echo "Patch does not match the source: $patch" >&2
        exit 1
    fi
}

setup_build() {
    local build=$1 source=$2
    shift 2
    local options=()
    [[ ! -f $build/build.ninja ]] || options+=(--reconfigure)
    meson setup "${options[@]}" "$build" "$source" "$@"
}

checkout_source "$LIBFPRINT_URL" "$LIBFPRINT_COMMIT" "$WORK/libfprint"
apply_patch_once "$WORK/libfprint" "$ROOT/patches/0004-synatlsmoc-add-experimental-06cb-00d4.patch"
apply_patch_once "$WORK/libfprint" "$ROOT/patches/0005-hwdb-remove-stale-synaptics-00be-allowlist.patch"

setup_build "$WORK/libfprint-build" "$WORK/libfprint" \
    --prefix="$PREFIX" --libdir=lib \
    -Ddrivers=synatlsmoc -Dintrospection=false -Ddoc=false \
    -Dgtk-examples=false -Dinstalled-tests=false \
    -Dudev_rules=disabled -Dudev_hwdb=disabled
ninja -C "$WORK/libfprint-build" -j "$JOBS"
meson test -C "$WORK/libfprint-build" --suite unit-tests --print-errorlogs
# Validate generated registration without the upstream metadata test's network
# dependency or introspection-only replay tests.
G_DEBUG=fatal-warnings "$WORK/libfprint-build/libfprint/fprint-list-udev-hwdb" > "$WORK/generated-g83.hwdb"
grep -Fqx 'usb:v06CBp00D4*' "$WORK/generated-g83.hwdb"
meson install -C "$WORK/libfprint-build" --no-rebuild

checkout_source "$FPRINTD_URL" "$FPRINTD_COMMIT" "$WORK/fprintd"
apply_patch_once "$WORK/fprintd" "$ROOT/patches/0003-fprintd-load-store-persistent-data-0600.patch"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

setup_build "$WORK/fprintd-build" "$WORK/fprintd" \
    --prefix="$PREFIX" --libdir=lib --libexecdir=libexec --sysconfdir=etc \
    -Dpam=false -Dman=false -Dgtk_doc=false \
    -Dsystemd_system_unit_dir="$PREFIX/lib/systemd/system" \
    -Ddbus_service_dir="$PREFIX/share/dbus-1/system-services"
ninja -C "$WORK/fprintd-build" -j "$JOBS"
if command -v xmllint >/dev/null; then
    meson test -C "$WORK/fprintd-build" net.reactivated.fprint.device.policy --print-errorlogs
fi

# Do not run meson install for fprintd: upstream's Polkit install directory
# comes from the system .pc file and can point outside PREFIX.
printf '\nBuilt locally:\n  libfprint: %s\n  fprintd:   %s\n' \
    "$PREFIX/lib/libfprint-2.so.2" "$WORK/fprintd-build/src/fprintd"
printf 'Source revisions: libfprint %s, fprintd %s\n' "$LIBFPRINT_COMMIT" "$FPRINTD_COMMIT"
echo 'This build does not perform hardware pairing, enrollment or verification.'
echo 'fprintd integration tests require additional mocks and virtual-driver bindings and are not run by this helper.'
