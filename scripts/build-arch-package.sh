#!/usr/bin/env bash
# Build a removable Arch package; do not install it or alter system services.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --help ]]; then
    echo 'Usage: bash scripts/build-arch-package.sh'
    echo 'Builds .work/arch-package/dynabook-g83-fingerprint-*.pkg.tar.zst without installing it.'
    exit 0
fi
(( $# == 0 )) || { echo 'Unexpected arguments (see --help)' >&2; exit 2; }
[[ ${G83_BUILD_JOBS:-2} =~ ^[1-9][0-9]*$ ]] || { echo 'G83_BUILD_JOBS must be positive' >&2; exit 2; }
WORK="$ROOT/.work/arch-package"
mkdir -p -- "$WORK"
install -m644 "$ROOT/packaging/arch/PKGBUILD" "$ROOT/sources.env" "$WORK/"
for patch in "$ROOT"/patches/000[345]-*.patch; do
    install -m644 "$patch" "$WORK/"
done
cd -- "$WORK"
makepkg --cleanbuild --force --noconfirm
makepkg --packagelist
