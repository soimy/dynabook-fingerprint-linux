#!/usr/bin/env bash
# Run with sudo in a visible terminal, or pkexec from an agent.
set -euo pipefail
export PATH=/usr/bin:/bin
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --help ]]; then
    echo 'Usage: sudo bash scripts/install-g83-package.sh /absolute/path/to/package.pkg.tar.zst'
    echo 'Backs up existing PAM configuration, installs the local package and starts fprintd.'
    echo 'Enrollment and Omarchy PAM integration are separate steps.'
    exit 0
fi
(( EUID == 0 && $# == 1 )) || { echo 'Run as root with one package path (see --help)' >&2; exit 2; }
package=$(realpath -- "$1")
read -r name version < <(pacman -Qp -- "$package")
[[ $name == dynabook-g83-fingerprint ]] || { echo "Unexpected package: $name" >&2; exit 1; }

backup="/var/lib/dynabook-g83-fingerprint/backups/$(date -u +%Y%m%dT%H%M%S)-$$"
install -dm700 "$backup/pam.d"
for file in sudo polkit-1 omarchy-lock-password omarchy-lock-fingerprint; do
    if [[ -f /etc/pam.d/$file ]]; then
        cp -a -- "/etc/pam.d/$file" "$backup/pam.d/"
    else
        printf '%s\n' "$file" >> "$backup/absent-pam-files"
    fi
done
echo "PAM backup: $backup"

systemctl stop fprintd.service
# An earlier local hardware test may hold the D-Bus name outside systemd.
# Terminate only this repository's exact test binary, never an unrelated PID.
while read -r pid; do
    [[ -n $pid ]] || continue
    executable=$(readlink -f "/proc/$pid/exe" || true)
    [[ $executable == "$ROOT/.work/fprintd-build/src/fprintd" ]] || continue
    kill -TERM "$pid" 2>/dev/null || true
    for (( attempt=0; attempt<30; attempt++ )); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo 'The local test daemon did not stop after SIGTERM.' >&2
        exit 1
    fi
done < <(pgrep -x fprintd || true)

pacman -U --noconfirm -- "$package"
systemctl daemon-reload
systemctl start fprintd.service
systemctl is-active fprintd.service
systemctl show fprintd.service -p ExecStart -p DropInPaths

python - <<'PY'
import glob
import json
import os
import stat
files = glob.glob('/var/lib/fprint/0-persistent/synatlsmoc/*')
print(json.dumps({
    'pairing_files': len(files),
    'pairing_file_modes': [oct(stat.S_IMODE(os.stat(p).st_mode)) for p in files],
    'pairing_file_owner_uids': [os.stat(p).st_uid for p in files],
}, indent=2))
PY
echo "Installed $name $version. Fingerprint enrollment data was preserved."
