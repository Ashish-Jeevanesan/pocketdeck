"""Minimal client for Apple's USB multiplexer (usbmuxd).

On Windows this is the "Apple Mobile Device Service" that comes with iTunes or the
"Apple Devices" app. It listens on 127.0.0.1:27015 and can open a TCP connection to
a port on an iPhone that is plugged in by cable.

Only devices with ConnectionType == "USB" are used, so Wi-Fi-synced phones are ignored.
"""
import os
import plistlib
import socket
import struct

PROTOCOL_PLIST = 1
MSG_PLIST = 8


class UsbmuxError(Exception):
    pass


class ServiceUnavailable(UsbmuxError):
    """Apple Mobile Device Service is not running / not installed."""


class ConnectRefused(UsbmuxError):
    """Phone is plugged in but the USB Deck app is not listening (not open)."""


def _address():
    # Override for testing, e.g. USBMUXD_SOCKET_ADDRESS=127.0.0.1:27015 (same var libimobiledevice uses)
    env = os.environ.get("USBMUXD_SOCKET_ADDRESS")
    if env:
        if env.startswith("UNIX:"):
            return socket.AF_UNIX, env[5:]
        host, port = env.rsplit(":", 1)
        return socket.AF_INET, (host, int(port))
    if os.name == "nt":
        return socket.AF_INET, ("127.0.0.1", 27015)
    return socket.AF_UNIX, "/var/run/usbmuxd"


def _open(timeout=3.0):
    family, addr = _address()
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(addr)
    except OSError as e:
        s.close()
        raise ServiceUnavailable(
            "Cannot reach Apple Mobile Device Service (%s). Install iTunes or the "
            "'Apple Devices' app and make sure the service is running." % e) from e
    return s


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise UsbmuxError("usbmuxd closed the connection")
        buf += chunk
    return buf


def _request(s, payload, tag=1):
    body = plistlib.dumps(payload)
    s.sendall(struct.pack("<IIII", 16 + len(body), PROTOCOL_PLIST, MSG_PLIST, tag) + body)
    length, _ver, _type, _tag = struct.unpack("<IIII", _recv_exact(s, 16))
    return plistlib.loads(_recv_exact(s, length - 16))


def _base(message_type):
    return {
        "MessageType": message_type,
        "ClientVersionString": "usbdeck-1.0",
        "ProgName": "usbdeck",
        "kLibUSBMuxVersion": 3,
    }


def list_usb_devices():
    """Return [{'id': DeviceID, 'serial': UDID}] for cable-connected devices only."""
    s = _open()
    try:
        reply = _request(s, _base("ListDevices"))
    finally:
        s.close()
    out = []
    for d in reply.get("DeviceList", []):
        props = d.get("Properties", {})
        if props.get("ConnectionType", "USB") != "USB":
            continue
        out.append({"id": d.get("DeviceID", props.get("DeviceID")),
                    "serial": props.get("SerialNumber", "")})
    return out


def connect(device_id, port, timeout=3.0):
    """Open a raw TCP tunnel to `port` on the phone. Returns a connected socket."""
    s = _open(timeout)
    msg = _base("Connect")
    msg["DeviceID"] = device_id
    msg["PortNumber"] = socket.htons(port)   # usbmuxd wants network byte order
    try:
        reply = _request(s, msg)
    except Exception:
        s.close()
        raise
    code = reply.get("Number", -1)
    if code != 0:
        s.close()
        if code == 3:
            raise ConnectRefused("USB Deck app is not open on the phone")
        raise UsbmuxError("usbmuxd Connect failed (code %s)" % code)
    s.settimeout(None)
    return s
