# Dynabook G83/HS: experimental 06cb:00d4 development

This is an adaptation of the original L50-G project, not verified G83 support.
No firmware-version or certificate check is bypassed by the G83 patch.

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
| TLS / enrollment / verification | Not tested |
| Persistence after daemon restart / reboot | Not tested |

The endpoint layout matches `synatlsmoc`. This is a reason to test that driver,
not proof that the command protocol or sensor certificate is compatible.
The existing public keys in the pinned driver are for firmware **10.1**.
Even a 10.1 response does not establish that the sensor uses those keys.
The [sanitized GET_VERSION report](hardware/g83-hs-06cb-00d4.json) was collected
through a system Polkit authorization prompt. No pairing command was sent.

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

## 3. Hardware validation still required

After reviewing the firmware report, the next development step is an isolated
fprintd hardware test with the patched library and persistent-data support.
Do not use libfprint's standalone `examples/enroll`: it does not preserve the
pairing data needed by this sensor family. Do not enable PAM while basic
device operation remains unverified.

Opening the driver may create new pairing material, which can invalidate
existing Windows fingerprint enrollment. The original driver's protocol notes
explain that Windows and Linux enrollment are not currently shared. Establish
that this is acceptable before the first pairing test.

The acceptance sequence is:

1. GET_VERSION report and compatible firmware/key handling.
2. Successful TLS handshake with certificate verification enabled.
3. Enrollment and matching through patched fprintd.
4. Nonmatching finger rejected.
5. Daemon restart followed by a successful match using saved pairing data.
6. Reboot, then suspend/resume, followed by successful verification.
7. Only then consider system packaging and Omarchy's login/lock integration.

Mark G83 support as working only after this sequence is observed. Keep
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
links to the local patched library. Hardware operations beyond GET_VERSION
remain pending.
