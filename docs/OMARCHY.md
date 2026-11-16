# G83 fingerprint enrollment and Omarchy integration

Tested on a Dynabook G83/HS with Synaptics `06cb:00d4`, firmware 10.1,
Omarchy 4.5.0 and kernel 7.2.15-1-omarchy. The original L50-G Debian scripts
are not the installation workflow for Omarchy.

The G83 passed TLS/certificate validation, enrollment, positive and negative
matching, reuse of the enrollment after a daemon restart, sudo, Polkit and
fingerprint lock-screen unlock. Whole-system reboot and suspend/resume remain
untested. See the [hardware report](G83-HS.md).

## Build and install

Install missing build dependencies through Omarchy:

```sh
omarchy pkg add base-devel git meson ninja pkgconf python libusb libgusb \
  glib2 openssl pixman cairo polkit dbus
```

Build as your ordinary user:

```sh
bash scripts/build-arch-package.sh
```

This copies the recipe and checksummed patches into `.work/arch-package`,
builds the pinned sources and runs the C unit tests, hwdb check and available
Polkit XML validation. It creates a local `dynabook-g83-fingerprint` package.
No privileged command runs during the build.

In a visible terminal, install it with:

```sh
package=$(cd .work/arch-package && makepkg --packagelist)
sudo bash scripts/install-g83-package.sh "$package"
```

An agent without an interactive terminal can invoke the same installer through
`pkexec /usr/bin/bash`, using the system authorization dialog. Do not enter an
administrator password into chat.

The installer backs up existing PAM files under
`/var/lib/dynabook-g83-fingerprint/backups/`, stops the earlier local test daemon
if present, installs the package with pacman and starts the system service.
It preserves fingerprint enrollment and pairing data. It does not edit PAM.

The package contains:

| Path | Purpose |
|---|---|
| `/opt/dynabook-g83-fingerprint/lib/` | Private patched libfprint |
| `/opt/dynabook-g83-fingerprint/libexec/fprintd` | Patched daemon with persistent pairing data |
| `/usr/lib/systemd/system/fprintd.service.d/90-dynabook-g83.conf` | Selects the private daemon/library and sets UMask=0077 |
| `/usr/lib/udev/hwdb.d/61-dynabook-g83-fingerprint.hwdb` | G83 autosuspend/persistence metadata |

The distribution's fprintd package supplies its D-Bus policy, clients, PAM
module and sandboxed systemd unit. Runtime files under `/opt` are root-owned,
and the installed daemon has no RPATH into the development workspace.

## Enroll and integrate

Run Omarchy's native workflow in a visible terminal:

```sh
omarchy setup security fingerprint
```

It authenticates the administrator, enrolls the right index finger and verifies
it. For this press sensor, lift the finger between touches and continue until
enrollment completes. A step can need several touches.

Only after enrollment and matching succeed does the native workflow configure:

- sudo and Polkit fingerprint PAM, with the existing password fallback.
- A closed-lid gate so an inaccessible reader does not delay password prompts.
- Quickshell's separate `omarchy-lock-fingerprint` PAM service.
- The native fprintd resume-recovery hook and a bounded service stop timeout.

No file under `/usr/share/omarchy` is edited. There is no GDM configuration in
this workflow, and no manual shell or Hyprland configuration is required.

Useful commands after setup:

```sh
fprintd-list "$USER"
fprintd-verify -f right-index-finger "$USER"
omarchy system lock
```

To enroll an additional finger from a terminal:

```sh
sudo fprintd-enroll -f left-index-finger "$USER"
```

Use fprintd for enrollment: standalone libfprint examples do not save the
pairing material required by this driver. The pairing file stays under
`/var/lib/fprint/0-persistent/synatlsmoc`, owned by root with mode 0600. Do not
delete or publish it; losing it can require pairing and enrollment again.

## Check the active stack

```sh
systemctl show fprintd.service -p ExecStart -p Environment -p DropInPaths
fprintd-list "$USER"
omarchy-shell lock status
journalctl -u fprintd.service -b --no-pager
```

The service exits after roughly 30 seconds without a client; D-Bus starts it
again on demand. An inactive service between requests is expected.

Before extending the tested scope, verify the existing fingerprint after a
whole-system reboot and again after suspend/resume. The Omarchy recovery hook
is installed, but that alone does not prove a successful resume on this G83.

## Roll back

To remove just this private driver package:

```sh
sudo systemctl stop fprintd.service
sudo pacman -R dynabook-g83-fingerprint
sudo systemctl daemon-reload
```

The next D-Bus activation uses the distribution daemon. Native PAM settings
and stored enrollments are separate from this package.

To remove the complete fingerprint setup, delete sensor enrollments while this
driver is still installed, then remove the private package and use Omarchy's
native removal workflow:

```sh
sudo fprintd-delete "$USER"
sudo systemctl stop fprintd.service
sudo pacman -R dynabook-g83-fingerprint
omarchy remove security fingerprint
```

The order matters: the private package depends on the distribution fprintd
package, and the sensor must be reachable when its stored prints are deleted.
