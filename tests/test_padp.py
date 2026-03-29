import struct


class TestPADPBuild:
    def test_build_data_header(self):
        from palm.padp import PADPConnection, PADP_TYPE_DATA, PADP_FLAG_FIRST, PADP_FLAG_LAST

        header = PADPConnection.build_padp_header(
            ptype=PADP_TYPE_DATA,
            flags=PADP_FLAG_FIRST | PADP_FLAG_LAST,
            payload_size=100,
        )
        assert len(header) == 4
        assert header[0] == PADP_TYPE_DATA
        assert header[1] == PADP_FLAG_FIRST | PADP_FLAG_LAST
        assert struct.unpack(">H", header[2:4])[0] == 100

    def test_build_ack_header(self):
        from palm.padp import PADPConnection, PADP_TYPE_ACK, PADP_FLAG_FIRST, PADP_FLAG_LAST

        header = PADPConnection.build_padp_header(
            ptype=PADP_TYPE_ACK,
            flags=PADP_FLAG_FIRST | PADP_FLAG_LAST,
            payload_size=0,
        )
        assert header[0] == PADP_TYPE_ACK
        assert struct.unpack(">H", header[2:4])[0] == 0


class TestPADPParse:
    def test_parse_padp_header(self):
        from palm.padp import parse_padp_header, PADP_TYPE_DATA, PADP_FLAG_FIRST

        raw = struct.pack(">BBH", PADP_TYPE_DATA, PADP_FLAG_FIRST, 256) + b"\x00" * 256
        ptype, flags, size = parse_padp_header(raw)
        assert ptype == PADP_TYPE_DATA
        assert flags == PADP_FLAG_FIRST
        assert size == 256

    def test_parse_padp_extracts_payload(self):
        from palm.padp import parse_padp_header

        payload = b"Hello PADP"
        raw = struct.pack(">BBH", 0x01, 0xC0, len(payload)) + payload
        ptype, flags, size = parse_padp_header(raw)
        assert size == len(payload)
        assert raw[4:4 + size] == payload


class TestPADPFragmentation:
    def test_fragment_small_payload(self):
        from palm.padp import fragment_payload, PADP_FLAG_FIRST, PADP_FLAG_LAST

        fragments = fragment_payload(b"small", max_size=1024)
        assert len(fragments) == 1
        flags, data = fragments[0]
        assert flags == PADP_FLAG_FIRST | PADP_FLAG_LAST
        assert data == b"small"

    def test_fragment_large_payload(self):
        from palm.padp import fragment_payload, PADP_FLAG_FIRST, PADP_FLAG_LAST

        payload = b"\xAA" * 2048
        fragments = fragment_payload(payload, max_size=1024)
        assert len(fragments) == 2
        flags0, data0 = fragments[0]
        assert flags0 == PADP_FLAG_FIRST
        assert len(data0) == 1024
        flags1, data1 = fragments[1]
        assert flags1 == PADP_FLAG_LAST
        assert len(data1) == 1024
        assert data0 + data1 == payload

    def test_fragment_exact_multiple(self):
        from palm.padp import fragment_payload

        payload = b"\xBB" * 3072
        fragments = fragment_payload(payload, max_size=1024)
        assert len(fragments) == 3
        reassembled = b"".join(data for _, data in fragments)
        assert reassembled == payload

    def test_reassemble_fragments(self):
        from palm.padp import fragment_payload, reassemble_fragments

        original = b"A" * 500 + b"B" * 500 + b"C" * 500
        fragments = fragment_payload(original, max_size=600)
        payloads = [data for _, data in fragments]
        result = reassemble_fragments(payloads)
        assert result == original
