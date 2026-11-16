#!/usr/bin/env python3
"""Read Synaptics GET_VERSION without opening a libfprint pairing session.

Requires Python 3 and the system libusb-1.0 library. The only sensor command
sent is 0x01 (GET_VERSION). No reset, detach, pairing or storage command is used.
Serial numbers and raw response bytes are deliberately omitted from reports.
"""

import argparse
import ctypes as C
import datetime
import json
import sys


class ProbeError(Exception):
    """A USB or protocol error safe to include in a diagnostic report."""


class DeviceDescriptor(C.Structure):
    _fields_ = [
        ("length", C.c_uint8), ("descriptor_type", C.c_uint8),
        ("usb_version", C.c_uint16), ("device_class", C.c_uint8),
        ("device_subclass", C.c_uint8), ("protocol", C.c_uint8),
        ("max_packet_size", C.c_uint8), ("vendor", C.c_uint16),
        ("product", C.c_uint16), ("device_version", C.c_uint16),
        ("manufacturer_index", C.c_uint8), ("product_index", C.c_uint8),
        ("serial_index", C.c_uint8), ("configuration_count", C.c_uint8),
    ]


def parse_version(data, vendor=0x06CB, product=0x00D4):
    """Decode the 38-byte Tudor GET_VERSION layout, including its status."""
    if len(data) < 2:
        raise ProbeError("GET_VERSION response has no complete status header")
    status = int.from_bytes(data[:2], "little")
    if status:
        raise ProbeError(f"GET_VERSION returned status 0x{status:04x}; protocol unconfirmed")
    if len(data) != 38:
        raise ProbeError(f"Unexpected GET_VERSION response length: {len(data)} (expected 38)")
    major, minor = data[10:12]
    build_time = int.from_bytes(data[2:6], "little")
    return {
        "usb_id": f"{vendor:04x}:{product:04x}",
        "firmware": f"{major}.{minor}",
        "build_number": int.from_bytes(data[6:10], "little"),
        "build_time_utc": datetime.datetime.fromtimestamp(
            build_time, datetime.timezone.utc
        ).isoformat(),
        "product_id": data[13],
        "silicon_revision": data[14],
        "security": f"0x{data[24]:02x}",
        "interface": f"0x{data[25]:02x}",
        "device_type": f"0x{int.from_bytes(data[34:36], 'little'):04x}",
        "provision_state": data[37],
        "known_firmware_keys": (major, minor) == (10, 1),
        "compatibility": "unverified: GET_VERSION does not validate TLS or fingerprint operations",
    }


class USBDevice:
    def __init__(self, vendor, product):
        self.vendor, self.product = vendor, product
        self.context = C.c_void_p()
        self.handle = C.c_void_p()
        self.claimed = False
        self.initialized = False
        try:
            self.lib = C.CDLL("libusb-1.0.so.0")
        except OSError as error:
            raise ProbeError("libusb-1.0.so.0 is required (Arch package: libusb)") from error
        prototypes = {
            "init": ([C.POINTER(C.c_void_p)], C.c_int),
            "exit": ([C.c_void_p], None),
            "get_device_list": ([C.c_void_p, C.POINTER(C.POINTER(C.c_void_p))], C.c_ssize_t),
            "free_device_list": ([C.POINTER(C.c_void_p), C.c_int], None),
            "get_device_descriptor": ([C.c_void_p, C.POINTER(DeviceDescriptor)], C.c_int),
            "open": ([C.c_void_p, C.POINTER(C.c_void_p)], C.c_int),
            "close": ([C.c_void_p], None),
            "claim_interface": ([C.c_void_p, C.c_int], C.c_int),
            "release_interface": ([C.c_void_p, C.c_int], C.c_int),
            "bulk_transfer": ([C.c_void_p, C.c_ubyte, C.POINTER(C.c_ubyte),
                               C.c_int, C.POINTER(C.c_int), C.c_uint], C.c_int),
            "error_name": ([C.c_int], C.c_char_p),
        }
        for name, (arguments, result) in prototypes.items():
            function = getattr(self.lib, "libusb_" + name)
            function.argtypes, function.restype = arguments, result

    def check(self, result, operation):
        if result < 0:
            name = self.lib.libusb_error_name(result).decode()
            hint = " Run this probe with sudo for USB access." if result == -3 else ""
            raise ProbeError(f"{operation}: {name}.{hint}")
        return result

    def __enter__(self):
        try:
            self.check(self.lib.libusb_init(C.byref(self.context)), "libusb_init")
            self.initialized = True
            devices = C.POINTER(C.c_void_p)()
            count = self.check(
                self.lib.libusb_get_device_list(self.context, C.byref(devices)),
                "enumerate USB devices",
            )
            try:
                matches = []
                for index in range(count):
                    descriptor = DeviceDescriptor()
                    if self.lib.libusb_get_device_descriptor(devices[index], C.byref(descriptor)) < 0:
                        continue
                    if (descriptor.vendor, descriptor.product) == (self.vendor, self.product):
                        matches.append(devices[index])
                if len(matches) != 1:
                    raise ProbeError(f"Expected one {self.vendor:04x}:{self.product:04x} device; found {len(matches)}")
                self.check(self.lib.libusb_open(matches[0], C.byref(self.handle)), "open sensor")
            finally:
                self.lib.libusb_free_device_list(devices, 1)
            self.check(self.lib.libusb_claim_interface(self.handle, 0), "claim interface 0")
            self.claimed = True
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.claimed:
            self.lib.libusb_release_interface(self.handle, 0)
            self.claimed = False
        if self.handle:
            self.lib.libusb_close(self.handle)
            self.handle = C.c_void_p()
        if self.initialized:
            self.lib.libusb_exit(self.context)
            self.initialized = False

    def get_version(self, timeout_ms):
        query = (C.c_ubyte * 1)(0x01)
        transferred = C.c_int()
        self.check(self.lib.libusb_bulk_transfer(
            self.handle, 0x01, query, 1, C.byref(transferred), timeout_ms
        ), "GET_VERSION send")
        if transferred.value != 1:
            raise ProbeError("GET_VERSION command was not fully sent")
        response = (C.c_ubyte * 64)()
        self.check(self.lib.libusb_bulk_transfer(
            self.handle, 0x81, response, len(response), C.byref(transferred), timeout_ms
        ), "GET_VERSION receive")
        return parse_version(bytes(response[:transferred.value]), self.vendor, self.product)


def usb_id(value):
    try:
        number = int(value, 16)
    except ValueError as error:
        raise argparse.ArgumentTypeError("USB IDs must be hexadecimal") from error
    if not 0 <= number <= 0xFFFF:
        raise argparse.ArgumentTypeError("USB IDs must be between 0000 and ffff")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vid", type=usb_id, default=0x06CB)
    parser.add_argument("--pid", type=usb_id, default=0x00D4)
    parser.add_argument("--json", action="store_true", help="print a report without serial numbers")
    parser.add_argument("--timeout-ms", type=int, default=2000)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_ms <= 10000:
        parser.error("--timeout-ms must be between 1 and 10000")
    try:
        with USBDevice(args.vid, args.pid) as device:
            report = device.get_version(args.timeout_ms)
    except ProbeError as error:
        if args.json:
            print(json.dumps({"usb_id": f"{args.vid:04x}:{args.pid:04x}", "error": str(error)}, indent=2))
        else:
            print(f"Probe failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
