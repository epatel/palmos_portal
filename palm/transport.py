"""USB transport layer for Handspring Visor communication."""

from __future__ import annotations

import struct
import logging

import usb.core
import usb.util

logger = logging.getLogger(__name__)

VISOR_VENDOR_ID = 0x082D
VISOR_PRODUCT_ID = 0x0100

CMP_TYPE_INIT = 0x01
CMP_TYPE_ABORT = 0x02
CMP_FLAG_CHANGE_BAUD = 0x80

_CMP_FORMAT = ">BBBBHI"
_CMP_SIZE = struct.calcsize(_CMP_FORMAT)


class Connection:
    """USB bulk transfer connection to a Handspring Visor."""

    VENDOR_ID = VISOR_VENDOR_ID
    PRODUCT_ID = VISOR_PRODUCT_ID

    def __init__(self):
        self._dev = None
        self._ep_in = None
        self._ep_out = None

    def open(self) -> None:
        self._dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        if self._dev is None:
            raise ConnectionError(
                "No Handspring Visor found. "
                "Is the device in the cradle? Press HotSync and try again."
            )

        try:
            if self._dev.is_kernel_driver_active(0):
                self._dev.detach_kernel_driver(0)
        except (usb.core.USBError, NotImplementedError):
            pass

        try:
            self._dev.set_configuration()
        except usb.core.USBError:
            pass

        cfg = self._dev.get_active_configuration()
        intf = cfg[(0, 0)]

        for ep in intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN:
                self._ep_in = ep
            elif usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                self._ep_out = ep

        if self._ep_in is None or self._ep_out is None:
            raise ConnectionError("Could not find USB bulk endpoints on Visor")

        logger.info(
            f"Connected to Visor: EP_IN=0x{self._ep_in.bEndpointAddress:02X}, "
            f"EP_OUT=0x{self._ep_out.bEndpointAddress:02X}"
        )

        self._cmp_handshake()

    def _cmp_handshake(self) -> None:
        raw = self.read(_CMP_SIZE, timeout=10.0)
        if len(raw) < _CMP_SIZE:
            raise ConnectionError(f"CMP init too short: {len(raw)} bytes")

        cmp_type, flags, ver_major, ver_minor, unused, max_baud = struct.unpack(
            _CMP_FORMAT, raw[:_CMP_SIZE]
        )
        logger.info(f"CMP init: type={cmp_type}, ver={ver_major}.{ver_minor}, max_baud={max_baud}")

        if cmp_type != CMP_TYPE_INIT:
            raise ConnectionError(f"Expected CMP INIT (1), got type {cmp_type}")

        response = struct.pack(
            _CMP_FORMAT,
            CMP_TYPE_INIT, 0x00, ver_major, ver_minor, 0, 0,
        )
        self.write(response)
        logger.info("CMP handshake complete")

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.reset()
            except usb.core.USBError:
                pass
            self._dev = None
            self._ep_in = None
            self._ep_out = None

    def read(self, n: int, timeout: float = 5.0) -> bytes:
        timeout_ms = int(timeout * 1000)
        try:
            data = self._ep_in.read(n, timeout=timeout_ms)
            return bytes(data)
        except usb.core.USBError as e:
            if "timeout" in str(e).lower():
                raise TimeoutError(f"USB read timeout after {timeout}s") from e
            raise

    def write(self, data: bytes) -> None:
        self._ep_out.write(data)

    def __enter__(self) -> Connection:
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()
