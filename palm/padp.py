"""PADP - Packet Assembly/Disassembly Protocol for PalmOS HotSync."""

import logging
import struct

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from palm.slp import SLPSocket

# Packet types
PADP_TYPE_DATA = 0x01
PADP_TYPE_ACK = 0x02
PADP_TYPE_TICKLE = 0x04

# Flags
PADP_FLAG_FIRST = 0x80
PADP_FLAG_LAST = 0x40

# Default max payload size per fragment
PADP_MAX_PAYLOAD = 1024


def parse_padp_header(data: bytes) -> tuple:
    """Parse 4-byte PADP header. Returns (type, flags, payload_size)."""
    ptype, flags, size = struct.unpack(">BBH", data[:4])
    return ptype, flags, size


def fragment_payload(data: bytes, max_size: int = PADP_MAX_PAYLOAD) -> list:
    """Split data into list of (flags, chunk) tuples.

    First fragment gets PADP_FLAG_FIRST, last gets PADP_FLAG_LAST.
    A single fragment gets both flags.
    """
    if not data:
        return [(PADP_FLAG_FIRST | PADP_FLAG_LAST, b"")]

    chunks = [data[i:i + max_size] for i in range(0, len(data), max_size)]
    fragments = []
    for i, chunk in enumerate(chunks):
        flags = 0
        if i == 0:
            flags |= PADP_FLAG_FIRST
        if i == len(chunks) - 1:
            flags |= PADP_FLAG_LAST
        fragments.append((flags, chunk))
    return fragments


def reassemble_fragments(payloads: list) -> bytes:
    """Concatenate a list of fragment byte payloads into the original data."""
    return b"".join(payloads)


class PADPConnection:
    def __init__(self, slp: "SLPSocket"):
        self._slp = slp
        self._txn_id = 0xFF

    def _next_txn_id(self) -> int:
        self._txn_id = (self._txn_id % 0xFF) + 1  # wraps 0xFF -> 0x01, skips 0x00
        return self._txn_id

    @staticmethod
    def build_padp_header(ptype: int, flags: int, payload_size: int) -> bytes:
        """Pack a 4-byte PADP header."""
        return struct.pack(">BBH", ptype, flags, payload_size)

    def send(self, data: bytes) -> None:
        """Fragment data and send each fragment via SLP, waiting for ACK."""
        from palm.slp import SLP_TYPE_PADP, SLP_SOCKET_DLP

        txn_id = self._next_txn_id()
        fragments = fragment_payload(data)

        for flags, chunk in fragments:
            header = self.build_padp_header(PADP_TYPE_DATA, flags, len(chunk))
            body = header + chunk
            max_attempts = 3
            for attempt in range(max_attempts):
                self._slp.send(
                    dest=SLP_SOCKET_DLP,
                    src=SLP_SOCKET_DLP,
                    ptype=SLP_TYPE_PADP,
                    txn_id=txn_id,
                    data=body,
                )
                # Wait for ACK — skip stale/unrelated packets
                got_ack = False
                for _ in range(5):  # read up to 5 packets looking for our ACK
                    try:
                        ack_pkt = self._slp.receive()
                    except (TimeoutError, EOFError):
                        break
                    if not ack_pkt.data or len(ack_pkt.data) < 4:
                        continue
                    ack_type, _, _ = parse_padp_header(ack_pkt.data)
                    if ack_type == PADP_TYPE_ACK and ack_pkt.txn_id == txn_id:
                        got_ack = True
                        break
                    # Got a stale/unrelated packet, keep reading
                    logger.debug(f"Skipped packet: txn=0x{ack_pkt.txn_id:02X} padp_type={ack_type}")
                if got_ack:
                    break
            else:
                raise TimeoutError("No ACK received after retries")

    def receive(self) -> bytes:
        """Read SLP packets, handle tickles, collect PADP fragments, return reassembled payload."""
        from palm.slp import SLP_TYPE_PADP, SLP_SOCKET_DLP

        payloads = []
        while True:
            pkt = self._slp.receive()
            if not pkt.data:
                continue

            ptype, flags, size = parse_padp_header(pkt.data)

            if ptype == PADP_TYPE_TICKLE:
                # Respond with ACK tickle
                ack_header = self.build_padp_header(PADP_TYPE_ACK, PADP_FLAG_FIRST | PADP_FLAG_LAST, 0)
                self._slp.send(
                    dest=SLP_SOCKET_DLP,
                    src=SLP_SOCKET_DLP,
                    ptype=SLP_TYPE_PADP,
                    txn_id=pkt.txn_id,
                    data=ack_header,
                )
                continue

            if ptype == PADP_TYPE_DATA:
                payload = pkt.data[4:4 + size]
                payloads.append(payload)

                # Send ACK
                ack_header = self.build_padp_header(PADP_TYPE_ACK, PADP_FLAG_FIRST | PADP_FLAG_LAST, size)
                self._slp.send(
                    dest=SLP_SOCKET_DLP,
                    src=SLP_SOCKET_DLP,
                    ptype=SLP_TYPE_PADP,
                    txn_id=pkt.txn_id,
                    data=ack_header,
                )

                if flags & PADP_FLAG_LAST:
                    return reassemble_fragments(payloads)
