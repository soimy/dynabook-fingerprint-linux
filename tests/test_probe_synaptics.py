import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "probe", Path(__file__).resolve().parents[1] / "scripts/probe-synaptics.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def version_response(major=10, minor=1):
    packet = bytearray(38)
    packet[6:10] = (3031663).to_bytes(4, "little")
    packet[10:12] = bytes((major, minor))
    packet[13] = 65
    packet[18:24] = b"SECRET"
    packet[24:26] = bytes((1, 2))
    packet[34:36] = (0x1234).to_bytes(2, "little")
    packet[37] = 3
    return bytes(packet)


class FakeLibUSB:
    """Exercise the ctypes boundary without USB permissions or real hardware."""

    def __init__(self, fail_read=False):
        self.calls = []
        self.fail_read = fail_read
        self.devices = (probe.C.c_void_p * 2)(99, None)
        for name in ("init", "exit", "get_device_list", "free_device_list",
                     "get_device_descriptor", "open", "close", "claim_interface",
                     "release_interface", "bulk_transfer", "error_name"):
            setattr(self, "libusb_" + name, self.function(name))

    def function(self, name):
        def call(*args):
            self.calls.append(name)
            if name == "init":
                args[0]._obj.value = 1
            elif name == "get_device_list":
                pointer = probe.C.cast(args[1], probe.C.POINTER(probe.C.POINTER(probe.C.c_void_p)))
                pointer[0] = probe.C.cast(self.devices, probe.C.POINTER(probe.C.c_void_p))
                return 1
            elif name == "get_device_descriptor":
                args[1]._obj.vendor = 0x06CB
                args[1]._obj.product = 0x00D4
            elif name == "open":
                args[1]._obj.value = 2
            elif name == "bulk_transfer":
                _, endpoint, buffer, length, transferred, _ = args
                if endpoint == 0x01:
                    self.calls.append((endpoint, bytes(buffer[:length])))
                    transferred._obj.value = length
                else:
                    self.calls.append((endpoint, length))
                    if self.fail_read:
                        return -7
                    packet = version_response()
                    probe.C.memmove(buffer, packet, len(packet))
                    transferred._obj.value = len(packet)
            elif name == "error_name":
                return b"LIBUSB_ERROR_TIMEOUT"
            return 0
        return call


class VersionTests(unittest.TestCase):
    def test_usb_query_only_sends_get_version_and_cleans_up(self):
        library = FakeLibUSB()
        with patch.object(probe.C, "CDLL", return_value=library):
            with probe.USBDevice(0x06CB, 0x00D4) as device:
                report = device.get_version(2000)
        self.assertEqual(report["firmware"], "10.1")
        transfers = [call for call in library.calls if isinstance(call, tuple)]
        self.assertEqual(transfers, [(0x01, b"\x01"), (0x81, 64)])
        self.assertEqual(library.calls[-3:], ["release_interface", "close", "exit"])

    def test_read_timeout_still_releases_device_without_retry_or_reset(self):
        library = FakeLibUSB(fail_read=True)
        with patch.object(probe.C, "CDLL", return_value=library):
            with self.assertRaisesRegex(probe.ProbeError, "LIBUSB_ERROR_TIMEOUT"):
                with probe.USBDevice(0x06CB, 0x00D4) as device:
                    device.get_version(2000)
        self.assertEqual(library.calls.count("bulk_transfer"), 2)
        self.assertEqual(library.calls[-3:], ["release_interface", "close", "exit"])

    def test_known_packet_offsets_and_private_serial(self):
        report = probe.parse_version(version_response())
        self.assertEqual(report["firmware"], "10.1")
        self.assertEqual(report["build_number"], 3031663)
        self.assertEqual(report["product_id"], 65)
        self.assertEqual(report["security"], "0x01")
        self.assertEqual(report["interface"], "0x02")
        self.assertEqual(report["device_type"], "0x1234")
        self.assertEqual(report["provision_state"], 3)
        self.assertTrue(report["known_firmware_keys"])
        self.assertIn("unverified", report["compatibility"])
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertFalse(any("serial" in key for key in report))

    def test_other_firmware_is_reported_without_claiming_support(self):
        report = probe.parse_version(version_response(10, 2))
        self.assertEqual(report["firmware"], "10.2")
        self.assertFalse(report["known_firmware_keys"])
        self.assertIn("unverified", report["compatibility"])

    def test_malformed_packets_are_rejected_without_exposing_payload(self):
        packet = version_response()
        for data in (b"", b"\0", packet[:2], packet[:-1], packet + b"\0"):
            with self.subTest(length=len(data)):
                with self.assertRaises(probe.ProbeError) as caught:
                    probe.parse_version(data)
                self.assertNotIn("SECRET", str(caught.exception))

    def test_firmware_error_is_not_decoded_as_version(self):
        with self.assertRaisesRegex(probe.ProbeError, "0x0401"):
            probe.parse_version(b"\x01\x04" + version_response()[2:])

    def test_usb_descriptor_matches_libusb_abi(self):
        self.assertEqual(probe.C.sizeof(probe.DeviceDescriptor), 18)
        self.assertEqual(probe.DeviceDescriptor.vendor.offset, 8)
        self.assertEqual(probe.DeviceDescriptor.product.offset, 10)

    def test_access_failure_returns_actionable_json(self):
        output = io.StringIO()
        with patch.object(probe, "USBDevice", side_effect=probe.ProbeError("USB access denied")):
            with contextlib.redirect_stdout(output):
                code = probe.main(["--json"])
        self.assertEqual(code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["usb_id"], "06cb:00d4")
        self.assertEqual(report["error"], "USB access denied")


if __name__ == "__main__":
    unittest.main()
