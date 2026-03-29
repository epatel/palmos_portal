import struct
from datetime import datetime, timezone

# PalmOS epoch: 1904-01-01 00:00:00 UTC
PALM_EPOCH_OFFSET = 2082844800


def make_pdb_bytes(
    name: str = "TestDB",
    db_type: str = "DATA",
    creator: str = "test",
    records: list[bytes] | None = None,
    app_info: bytes | None = None,
) -> bytes:
    """Build a complete PDB file with optional records and app info."""
    records = records or []
    num_records = len(records)

    header_size = 78
    record_list_size = num_records * 8
    padding = 2 if num_records > 0 else 0

    app_info_offset = 0
    data_start = header_size + record_list_size + padding
    if app_info is not None:
        app_info_offset = data_start
        data_start += len(app_info)

    record_list = b""
    offset = data_start
    for i, rec in enumerate(records):
        unique_id = i + 1
        record_list += struct.pack(">IB", offset, 0x40)
        record_list += struct.pack(">I", unique_id)[1:]
        offset += len(rec)

    name_bytes = name.encode("ascii")[:31].ljust(32, b"\x00")
    now = PALM_EPOCH_OFFSET + 1000000
    header = struct.pack(
        ">32sHHIIIIII4s4sIIH",
        name_bytes,
        0x0000, 1, now, now, 0, 0,
        app_info_offset, 0,
        db_type.encode("ascii"),
        creator.encode("ascii"),
        0, 0, num_records,
    )

    data = header + record_list
    if padding:
        data += b"\x00" * padding
    if app_info is not None:
        data += app_info
    for rec in records:
        data += rec

    return data


class TestPDBHeader:
    def test_parse_empty_pdb(self):
        from palm.pdb import PalmDatabase

        raw = make_pdb_bytes(name="MemoDB", db_type="DATA", creator="memo")
        db = PalmDatabase.from_bytes(raw)
        assert db.name == "MemoDB"
        assert db.db_type == "DATA"
        assert db.creator == "memo"
        assert db.is_resource_db is False
        assert len(db.records) == 0
        assert db.app_info is None

    def test_parse_pdb_with_records(self):
        from palm.pdb import PalmDatabase

        records = [b"Hello World", b"Second Record", b"Third"]
        raw = make_pdb_bytes(name="TestDB", records=records)
        db = PalmDatabase.from_bytes(raw)
        assert len(db.records) == 3
        assert db.records[0].data == b"Hello World"
        assert db.records[1].data == b"Second Record"
        assert db.records[2].data == b"Third"

    def test_parse_pdb_with_app_info(self):
        from palm.pdb import PalmDatabase

        app_info = b"\x00" * 32 + b"category data"
        raw = make_pdb_bytes(name="TestDB", app_info=app_info)
        db = PalmDatabase.from_bytes(raw)
        assert db.app_info == app_info

    def test_roundtrip_pdb(self):
        from palm.pdb import PalmDatabase

        records = [b"Record One", b"Record Two"]
        app_info = b"AppInfoBlock"
        raw = make_pdb_bytes(
            name="RoundTrip", db_type="DATA", creator="test",
            records=records, app_info=app_info,
        )
        db = PalmDatabase.from_bytes(raw)
        output = db.to_bytes()
        db2 = PalmDatabase.from_bytes(output)
        assert db2.name == "RoundTrip"
        assert db2.db_type == "DATA"
        assert db2.creator == "test"
        assert len(db2.records) == 2
        assert db2.records[0].data == b"Record One"
        assert db2.records[1].data == b"Record Two"
        assert db2.app_info == b"AppInfoBlock"

    def test_palm_timestamp_conversion(self):
        from palm.pdb import PalmDatabase

        raw = make_pdb_bytes(name="TimeTest")
        db = PalmDatabase.from_bytes(raw)
        assert db.creation_time.year >= 1904
