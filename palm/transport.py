"""USB transport layer for Handspring Visor communication."""

from __future__ import annotations

import struct
import logging

import usb.core
import usb.util

logger = logging.getLogger(__name__)

VISOR_VENDOR_ID = 0x082D
VISOR_PRODUCT_ID = 0x0100

# Visor vendor-specific USB control requests
VISOR_GET_CONNECTION_INFORMATION = 0x03
VISOR_REQUEST_BYTES = 0x04


class Connection:
    """USB bulk transfer connection to a Handspring Visor."""

    VENDOR_ID = VISOR_VENDOR_ID
    PRODUCT_ID = VISOR_PRODUCT_ID

    def __init__(self):
        self._dev = None
        self._ep_in = None
        self._ep_out = None
        self._read_buf = b""

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

        # The Visor has two endpoint pairs. Pick the ones with the largest
        # max packet size (0x82/0x02 at 64 bytes, not 0x81/0x01 at 16 bytes).
        for ep in intf:
            direction = usb.util.endpoint_direction(ep.bEndpointAddress)
            if direction == usb.util.ENDPOINT_IN:
                if self._ep_in is None or ep.wMaxPacketSize > self._ep_in.wMaxPacketSize:
                    self._ep_in = ep
            elif direction == usb.util.ENDPOINT_OUT:
                if self._ep_out is None or ep.wMaxPacketSize > self._ep_out.wMaxPacketSize:
                    self._ep_out = ep

        if self._ep_in is None or self._ep_out is None:
            raise ConnectionError("Could not find USB bulk endpoints on Visor")

        logger.info(
            f"Connected to Visor: EP_IN=0x{self._ep_in.bEndpointAddress:02X}, "
            f"EP_OUT=0x{self._ep_out.bEndpointAddress:02X}"
        )

        # Send vendor-specific control requests to initialize the Visor.
        # Without these, data transfer is unreliable.
        self._visor_init()

    def _visor_init(self) -> None:
        """Send vendor-specific USB control requests to start HotSync.

        The Visor requires GET_CONNECTION_INFORMATION and REQUEST_BYTES
        control transfers before it will reliably send/receive data.
        """
        # GET_CONNECTION_INFORMATION: device-to-host vendor request
        try:
            ret = self._dev.ctrl_transfer(
                bmRequestType=0xC2,  # device-to-host, vendor, endpoint
                bRequest=VISOR_GET_CONNECTION_INFORMATION,
                wValue=0, wIndex=0, data_or_wLength=0x12,
            )
            logger.info(f"Visor connection info: {bytes(ret).hex()}")
        except usb.core.USBError as e:
            raise ConnectionError(f"Device not ready: {e}") from e

        # REQUEST_BYTES: tell the device we're ready to receive
        try:
            ret = self._dev.ctrl_transfer(
                bmRequestType=0xC2,  # device-to-host, vendor, endpoint
                bRequest=VISOR_REQUEST_BYTES,
                wValue=0, wIndex=5, data_or_wLength=2,
            )
            num_bytes = struct.unpack("<H", bytes(ret)[:2])[0] if len(ret) >= 2 else 0
            logger.info(f"Visor ready, {num_bytes} bytes pending")
        except usb.core.USBError as e:
            pass  # Not supported on all Visor models

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.reset()
            except usb.core.USBError:
                pass
            self._dev = None
            self._ep_in = None
            self._ep_out = None
            self._read_buf = b""

    def read(self, n: int, timeout: float = 15.0) -> bytes:
        """Read exactly n bytes, assembling from buffer and USB reads."""
        timeout_ms = int(timeout * 1000)
        result = b""

        # Drain buffer first
        if self._read_buf:
            if len(self._read_buf) >= n:
                result = self._read_buf[:n]
                self._read_buf = self._read_buf[n:]
                return result
            result = self._read_buf
            self._read_buf = b""

        # Read from USB until we have enough
        max_pkt = self._ep_in.wMaxPacketSize
        while len(result) < n:
            try:
                data = bytes(self._ep_in.read(max_pkt, timeout=timeout_ms))
                result += data
            except usb.core.USBError as e:
                if "timeout" in str(e).lower():
                    raise TimeoutError(f"USB read timeout after {timeout}s") from e
                raise

        # Buffer any excess
        if len(result) > n:
            self._read_buf = result[n:]
            result = result[:n]
        return result

    def write(self, data: bytes) -> None:
        logger.debug(f"USB write: {len(data)} bytes")
        self._ep_out.write(data, timeout=15000)

    def __enter__(self) -> Connection:
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()
