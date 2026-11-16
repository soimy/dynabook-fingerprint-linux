# Dynabook G83/HS: experimental 06cb:00d4 development

This adapts the original L50-G project for a G83/HS, with the observed results
below. Support remains experimental while reboot and suspend/resume coverage
is outstanding. No firmware-version or certificate check is bypassed.

For installation and native authentication setup, see the
[Omarchy guide](OMARCHY.md).

## Observed hardware

Read-only checks on 2026-11-16 found:

| Item | Observation |
|---|---|
| System | Dynabook G83/HS |
| OS | Omarchy 4.5.0, Arch based |
| Kernel | 7.2.15-1-omarchy |
| Fingerprint USB ID | `06cb:00d4` |
| Interface | 0, vendor specific |
| Bulk OUT / IN | `0x01` / `0x81`, 64-byte packets |
| Interrupt IN | `0x83`, 8-byte packets |
| Firmware | 10.1, build 3238595, 2020-06-15 |
| Product ID | 65 (`A`), same value as the tested L50-G |
| Security / interface / provisioning | `0x0f` / `0xa1` / `3` |
| Optional image metrics | `0x0401`; driver's automatic fallback observed |
| TLS / enrollment / matching / rejection | Passed |
| Persistence after daemon restart | Passed with the existing enrollment |
| sudo / Polkit / lock screen | Passed through Omarchy's native PAM integration |
| Reboot / suspend-resume | Not tested |

The endpoint layout matches `synatlsmoc`. This is a reason to test that driver,
not proof that the command protocol or sensor certificate is compatible.
The existing public keys in the pinned driver are for firmware **10.1**.
Even a 10.1 response does not establish that the sensor uses those keys.
The [hardware report](hardware/g83-hs-06cb-00d4.json) includes the sanitized
GET_VERSION fields and subsequent validation results. The initial probe sent
no pairing command; later TLS and enrollment tests created Linux pairing data.

The existing [00d4 support request in synaTudor](https://github.com/Popax21/synaTudor/issues/53)
does not document a working configuration. `synaTudor` and `synatlsmoc` are
different implementations; this project tests the latter.

## 1. Read the firmware version

The probe requires Python 3 and the system `libusb` package, without PyUSB.
Run from the project directory:

```sh
sudo python scripts/probe-synaptics.py --json
```

It enumerates the exact USB ID, claims interface 0 temporarily, sends only
`GET_VERSION` (`0x01`) and releases the interface. It does not reset the USB
device, detach a kernel driver, pair, enroll, format storage or load libfprint.
Each USB transfer times out after two seconds by default. The JSON omits the
sensor serial number, raw packet and pairing material.

Keep the JSON report for the next development step:

- Firmware 10.1: the driver has candidate public keys; TLS still needs testing.
- Other firmware: obtain the corresponding public key/protocol information
  from the OEM Windows driver before extending support. Keep certificate and
  handshake validation enabled.
- Nonzero status or a response layout other than 38 bytes: investigate the
  protocol before running pairing operations.
- `LIBUSB_ERROR_ACCESS`: rerun with USB access via sudo.
- `LIBUSB_ERROR_BUSY`: another process owns interface 0; identify it before
  retrying. The probe intentionally does not detach or reset anything.

## 2. Build locally on Arch / Omarchy

Typical build dependencies, if missing:

```sh
sudo pacman -S --needed base-devel git meson ninja pkgconf python libusb \
  libgusb glib2 openssl pixman cairo polkit dbus systemd
```

Then build as your ordinary user:

```sh
bash scripts/build-g83.sh
```

The helper uses [sources.env](../sources.env) to pin both upstream sources:

- libfprint `0c67248a5a4af188d6c1417fb348f655e80f6c8e`, including the original
  L50-G registration and security fixes. Apply **0004 and 0005** to this revision.
- fprintd v1.94.5, commit `b54a007ccf58ac0ae074c7151b223f35cbd17306`, with
  **0003** to save pairing data immediately and with mode 0600.

The G83 patch adds `06cb:00d4` to the driver table and autosuspend hwdb. Patch
0005 removes a stale unsupported-device entry for the already registered
`06cb:00be`, fixing the pinned fork's hwdb generator under fatal warnings. This
was discovered by running the upstream tests, before any hardware operation.
The G83 patch
does not change firmware checks, public keys, certificate validation or TLS.
The local build disables udev installation and introspection and only enables
the `synatlsmoc` driver. Consequently, its tests do not include introspection
or hardware replay tests. The helper runs the C unit-test suites, checks hwdb
generation and the G83 entry, and validates the fprintd Polkit XML when xmllint
is installed. It excludes upstream's AppStream network validation and fprintd
integration tests, which need additional mocks and virtual-driver bindings.
Compilation is not a functional fingerprint test.

Outputs:

```text
.work/libfprint/                     pinned source + G83 patch
.work/libfprint-build/               build + C unit-test results
.work/prefix/lib/libfprint-2.so.2     local library
.work/fprintd/                       pinned source + persistence patch
.work/fprintd-build/src/fprintd      local daemon, not started
.work/fprintd-build/utils/           local client utilities
```

The helper installs libfprint only into its local prefix. It deliberately does
not install fprintd: upstream obtains its Polkit installation path from a
system `.pc` file, which can point outside that prefix. No packages, PAM files,
udev rules or D-Bus services are replaced by this build.

Use `G83_BUILD_ROOT=/absolute/path` for another dedicated workspace, or
`G83_BUILD_JOBS=4` to change build parallelism. Reruns accept already applied
patches, refuse a different base revision, and preserve local source edits.
For a clean reproduction, use a fresh build root.

## 3. Observed hardware validation

The firmware report was followed by an isolated
fprintd hardware test with the patched library and persistent-data support.
Do not use libfprint's standalone `examples/enroll`: it does not preserve the
pairing data needed by this sensor family. Do not enable PAM while basic
device operation remains unverified.

Opening the driver may create new pairing material, which can invalidate
existing Windows fingerprint enrollment. The original driver's protocol notes
explain that Windows and Linux enrollment are not currently shared. Establish
that this is acceptable before the first pairing test.

On 2026-11-16, this G83/HS passed:

1. GET_VERSION, firmware 10.1 and product ID 65.
2. A successful device Claim/open with certificate and TLS checks enabled.
3. Right-index enrollment, followed by `verify-match`.
4. `verify-no-match` for an unenrolled finger.
5. Installation of the isolated Arch package, a fresh systemd daemon and a
   successful match with the earlier enrollment, before re-enrolling.
6. `omarchy setup security fingerprint`, including a fresh enrollment and match.
7. Fresh sudo and Polkit authentication after invalidating cached authorization.
8. A real Wayland session lock followed by fingerprint unlock, confirmed by the
   owner. Shell logs showed a secure lock followed by a normal unlock.

A follow-up Polkit test explicitly revoked temporary authorization, started
after the owner was ready, observed D-Bus `VerifyStatus ('verify-match', true)`
and received UID `0` from `pkexec /usr/bin/id -u`. A root command's success
alone does not distinguish a fingerprint match from password fallback or
cached authorization. An earlier request fell back to a password, and a
separate scan timed out while waiting for a sensor event; those attempts are
not counted as successful fingerprint authentication.

The pairing file was confirmed root-owned with mode 0600. No pairing material,
template data or raw packets are included in the report.

The sensor returns `0x0401` for the optional `get_image_metrics` command. The
existing driver handles this by disabling that query for the current device
instance and continuing to sensor-side matching. A fresh daemon can log this
warning once again; positive and negative matching both passed with this
fallback. No hardcoded firmware/key bypass was added to suppress the warning.

Whole-system reboot and suspend/resume verification remain to be done; a
daemon restart is not a reboot test. Results apply to this G83/HS and firmware,
not automatically to all Dynabook readers. Keep
pairing files and raw debug logs out of commits; debug logs can include device
identifiers even though the read-only probe report omits them.

## Development checks

```sh
python -m unittest discover -s tests -v
bash -n scripts/build-g83.sh
git diff --check
```

The probe tests include malformed responses, unknown firmware, USB timeouts,
command selection and resource cleanup using a fake libusb backend.

Local validation on this G83: eight probe tests passed; libfprint's
`fpi-assembling`, `fpi-ssm` and `fpi-device` unit-test executables passed;
hwdb generation passed with the G83 ID present; patched fprintd compiled and
its Polkit policy XML validation passed. `ldd` confirmed that the local daemon
links to the local patched library. The Arch package also passed these checks,
and its installed daemon has no build-directory RPATH.
