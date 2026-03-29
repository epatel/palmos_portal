"""SLP - Serial Link Protocol packet framing layer for PalmOS HotSync."""

import struct
from dataclasses import dataclass
from typing import Optional

SLP_SIGNATURE = b"\xbe\xef\xed"
SLP_TYPE_DATA = 0x00
SLP_TYPE_LOOPBACK = 0x01
SLP_TYPE_ACK = 0x02
SLP_SOCKET_DLP = 3
_HEADER_SIZE = 10


def crc16(data: bytes) -> int:
    """CRC-CCITT: polynomial 0x1021, init 0x0000."""
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc


@dataclass
class SLPPacket:
    dest: int
    src: int
    ptype: int
    txn_id: int
    data: bytes

    @classmethod
    def from_bytes(cls, raw: bytes) -> "SLPPacket":
        if raw[:3] != SLP_SIGNATURE:
            raise ValueError("Invalid SLP signature")

        header = raw[:_HEADER_SIZE]
        dest, src, ptype = header[3], header[4], header[5]
        data_len = struct.unpack(">H", header[6:8])[0]
        txn_id = header[8]
        stored_cksum = header[9]

        expected_cksum = sum(header[:9]) % 256
        if stored_cksum != expected_cksum:
            raise ValueError(f"Invalid header checksum: got {stored_cksum:#04x}, expected {expected_cksum:#04x}")

        body = raw[_HEADER_SIZE: _HEADER_SIZE + data_len]
        stored_crc = struct.unpack(">H", raw[_HEADER_SIZE + data_len: _HEADER_SIZE + data_len + 2])[0]
        expected_crc = crc16(body)
        if stored_crc != expected_crc:
            raise ValueError(f"Invalid CRC: got {stored_crc:#06x}, expected {expected_crc:#06x}")

        return cls(dest=dest, src=src, ptype=ptype, txn_id=txn_id, data=body)


class SLPSocket:
    def __init__(self, stream=None):
        self._stream = stream

    @staticmethod
    def build_packet(dest: int, src: int, ptype: int, txn_id: int, data: bytes) -> bytes:
        data_len = len(data)
        header_without_cksum = struct.pack(">3sBBBHB", SLP_SIGNATURE, dest, src, ptype, data_len, txn_id)
        cksum = sum(header_without_cksum) % 256
        header = header_without_cksum + bytes([cksum])
        crc = crc16(data)
        return header + data + struct.pack(">H", crc)

    def send(self, dest: int, src: int, ptype: int, txn_id: int, data: bytes) -> None:
        packet = self.build_packet(dest=dest, src=src, ptype=ptype, txn_id=txn_id, data=data)
        self._stream.write(packet)

    def receive(self) -> SLPPacket:
        # Scan for signature
        buf = b""
        while True:
            byte = self._stream.read(1)
            if not byte:
                raise EOFError("Stream ended before SLP signature found")
            buf += byte
            if buf[-3:] == SLP_SIGNATURE:
                break

        # Read remainder of header (7 bytes after the 3-byte signature)
        rest_of_header = self._stream.read(7)
        header = SLP_SIGNATURE + rest_of_header
        data_len = struct.unpack(">H", header[6:8])[0]

        body = self._stream.read(data_len)
        crc_bytes = self._stream.read(2)
        raw = header + body + crc_bytes
        return SLPPacket.from_bytes(raw)
