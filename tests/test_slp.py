import struct


class TestSLPBuild:
    def test_build_data_packet(self):
        from palm.slp import SLPSocket, SLP_TYPE_PADP

        packet = SLPSocket.build_packet(
            dest=3, src=3, ptype=SLP_TYPE_PADP,
            txn_id=0x01, data=b"\x00\x01\x02",
        )
        assert packet[:3] == b"\xbe\xef\xed"
        assert packet[3] == 3
        assert packet[4] == 3
        assert packet[5] == SLP_TYPE_PADP
        assert struct.unpack(">H", packet[6:8])[0] == 3
        assert packet[8] == 0x01
        expected_cksum = sum(packet[:9]) % 256
        assert packet[9] == expected_cksum
        assert packet[10:13] == b"\x00\x01\x02"
        assert len(packet) == 10 + 3 + 2

    def test_build_empty_packet(self):
        from palm.slp import SLPSocket, SLP_TYPE_PADP

        packet = SLPSocket.build_packet(
            dest=3, src=3, ptype=SLP_TYPE_PADP,
            txn_id=0xFF, data=b"",
        )
        assert len(packet) == 10 + 0 + 2

    def test_build_ack_packet(self):
        from palm.slp import SLPSocket, SLP_TYPE_LOOP

        packet = SLPSocket.build_packet(
            dest=3, src=3, ptype=SLP_TYPE_LOOP,
            txn_id=0x05, data=b"",
        )
        assert packet[5] == SLP_TYPE_LOOP
        assert packet[8] == 0x05


class TestSLPParse:
    def test_parse_roundtrip(self):
        from palm.slp import SLPSocket, SLPPacket, SLP_TYPE_PADP

        original_data = b"Hello PalmOS"
        raw = SLPSocket.build_packet(
            dest=3, src=3, ptype=SLP_TYPE_PADP,
            txn_id=0x42, data=original_data,
        )
        pkt = SLPPacket.from_bytes(raw)
        assert pkt.dest == 3
        assert pkt.src == 3
        assert pkt.ptype == SLP_TYPE_PADP
        assert pkt.txn_id == 0x42
        assert pkt.data == original_data

    def test_parse_bad_signature_raises(self):
        import pytest
        from palm.slp import SLPPacket

        bad = b"\x00\x00\x00" + b"\x00" * 9 + b"\x00\x00"
        with pytest.raises(ValueError, match="signature"):
            SLPPacket.from_bytes(bad)

    def test_parse_bad_checksum_raises(self):
        import pytest
        from palm.slp import SLPSocket, SLPPacket, SLP_TYPE_PADP

        raw = bytearray(SLPSocket.build_packet(
            dest=3, src=3, ptype=SLP_TYPE_PADP,
            txn_id=1, data=b"test",
        ))
        raw[9] = (raw[9] + 1) % 256
        with pytest.raises(ValueError, match="checksum"):
            SLPPacket.from_bytes(bytes(raw))

    def test_parse_bad_crc_raises(self):
        import pytest
        from palm.slp import SLPSocket, SLPPacket, SLP_TYPE_PADP

        raw = bytearray(SLPSocket.build_packet(
            dest=3, src=3, ptype=SLP_TYPE_PADP,
            txn_id=1, data=b"test",
        ))
        raw[-1] ^= 0xFF
        with pytest.raises(ValueError, match="CRC"):
            SLPPacket.from_bytes(bytes(raw))


class TestCRC16:
    def test_empty_data(self):
        from palm.slp import crc16

        assert crc16(b"") == 0

    def test_known_value(self):
        from palm.slp import crc16

        result = crc16(b"123456789")
        assert result == 0x31C3
