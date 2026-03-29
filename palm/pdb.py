"""PalmOS PDB/PRC database parser and serializer."""

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Attribute flag: database is a resource database (.prc)
ATTR_RESOURCE = 0x0001

# PalmOS epoch: 1904-01-01 00:00:00 UTC
_PALM_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)
_PALM_EPOCH_OFFSET = 2082844800  # seconds between Unix epoch and Palm epoch

# Header struct: 32s name, H attributes, H version, I creation_time,
# I modification_time, I backup_time, I modification_number,
# I app_info_offset, I sort_info_offset, 4s db_type, 4s creator,
# I unique_id_seed, I next_record_list, H num_records
_HEADER_FORMAT = ">32sHHIIIIII4s4sIIH"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)  # 78 bytes

_RECORD_ENTRY_FORMAT = ">IB3s"   # offset(4), attributes(1), unique_id(3)
_RECORD_ENTRY_SIZE = struct.calcsize(_RECORD_ENTRY_FORMAT)  # 8 bytes

_RESOURCE_ENTRY_FORMAT = ">4sHI"  # type(4), id(2), offset(4)
_RESOURCE_ENTRY_SIZE = struct.calcsize(_RESOURCE_ENTRY_FORMAT)  # 10 bytes


def _palm_to_datetime(palm_ts: int) -> datetime:
    """Convert a PalmOS timestamp (seconds since 1904-01-01) to UTC datetime."""
    unix_ts = palm_ts - _PALM_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


def _datetime_to_palm(dt: datetime) -> int:
    """Convert a UTC datetime to a PalmOS timestamp."""
    unix_ts = dt.timestamp()
    return int(unix_ts) + _PALM_EPOCH_OFFSET


@dataclass
class Record:
    """A single record in a PDB database."""
    data: bytes
    attributes: int = 0
    unique_id: int = 0


@dataclass
class Resource:
    """A single resource in a PRC database."""
    res_type: str  # 4-character type code
    res_id: int
    data: bytes


@dataclass
class PalmDatabase:
    """Represents a PalmOS PDB or PRC database."""
    name: str
    attributes: int = 0
    version: int = 0
    creation_time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    modification_time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    backup_time: datetime = field(default_factory=lambda: datetime(1904, 1, 1, tzinfo=timezone.utc))
    modification_number: int = 0
    db_type: str = ""
    creator: str = ""
    unique_id_seed: int = 0
    app_info: bytes | None = None
    sort_info: bytes | None = None
    records: list[Record] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)

    @property
    def is_resource_db(self) -> bool:
        """True if this is a resource database (.prc)."""
        return bool(self.attributes & ATTR_RESOURCE)

    @classmethod
    def from_bytes(cls, data: bytes) -> "PalmDatabase":
        """Parse a PDB/PRC database from raw bytes."""
        if len(data) < _HEADER_SIZE:
            raise ValueError(f"Data too short for PDB header: {len(data)} bytes")

        (
            name_bytes, attributes, version,
            creation_time_raw, modification_time_raw, backup_time_raw,
            modification_number, app_info_offset, sort_info_offset,
            db_type_bytes, creator_bytes,
            unique_id_seed, _next_record_list, num_records,
        ) = struct.unpack_from(_HEADER_FORMAT, data, 0)

        name = name_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        db_type = db_type_bytes.decode("ascii", errors="replace")
        creator = creator_bytes.decode("ascii", errors="replace")

        creation_time = _palm_to_datetime(creation_time_raw)
        modification_time = _palm_to_datetime(modification_time_raw)
        backup_time = _palm_to_datetime(backup_time_raw)

        is_resource = bool(attributes & ATTR_RESOURCE)

        # Parse record/resource list entries
        entries = []
        pos = _HEADER_SIZE
        if is_resource:
            for _ in range(num_records):
                res_type_bytes, res_id, offset = struct.unpack_from(
                    _RESOURCE_ENTRY_FORMAT, data, pos
                )
                entries.append((res_type_bytes.decode("ascii", errors="replace"), res_id, offset))
                pos += _RESOURCE_ENTRY_SIZE
        else:
            for _ in range(num_records):
                rec_offset, rec_attrs, uid_bytes = struct.unpack_from(
                    _RECORD_ENTRY_FORMAT, data, pos
                )
                unique_id = int.from_bytes(uid_bytes, "big")
                entries.append((rec_offset, rec_attrs, unique_id))
                pos += _RECORD_ENTRY_SIZE

        # Determine app_info and sort_info boundaries
        app_info = None
        sort_info = None

        if app_info_offset:
            # app_info ends at sort_info_offset, or first record/resource, or EOF
            end = len(data)
            if sort_info_offset and sort_info_offset > app_info_offset:
                end = sort_info_offset
            elif entries:
                if is_resource:
                    first_data_offset = min(e[2] for e in entries)
                else:
                    first_data_offset = min(e[0] for e in entries)
                if first_data_offset > app_info_offset:
                    end = first_data_offset
            app_info = data[app_info_offset:end]

        if sort_info_offset:
            end = len(data)
            if entries:
                if is_resource:
                    first_data_offset = min(e[2] for e in entries)
                else:
                    first_data_offset = min(e[0] for e in entries)
                if first_data_offset > sort_info_offset:
                    end = first_data_offset
            sort_info = data[sort_info_offset:end]

        # Parse actual record/resource data
        records: list[Record] = []
        resources: list[Resource] = []

        if is_resource:
            for i, (res_type, res_id, offset) in enumerate(entries):
                if i + 1 < len(entries):
                    next_offset = entries[i + 1][2]
                else:
                    next_offset = len(data)
                resources.append(Resource(
                    res_type=res_type,
                    res_id=res_id,
                    data=data[offset:next_offset],
                ))
        else:
            for i, (offset, rec_attrs, unique_id) in enumerate(entries):
                if i + 1 < len(entries):
                    next_offset = entries[i + 1][0]
                else:
                    next_offset = len(data)
                records.append(Record(
                    data=data[offset:next_offset],
                    attributes=rec_attrs,
                    unique_id=unique_id,
                ))

        return cls(
            name=name,
            attributes=attributes,
            version=version,
            creation_time=creation_time,
            modification_time=modification_time,
            backup_time=backup_time,
            modification_number=modification_number,
            db_type=db_type,
            creator=creator,
            unique_id_seed=unique_id_seed,
            app_info=app_info,
            sort_info=sort_info,
            records=records,
            resources=resources,
        )

    def to_bytes(self) -> bytes:
        """Serialize the database back to PDB/PRC format."""
        is_resource = self.is_resource_db
        items = self.resources if is_resource else self.records
        num_items = len(items)

        entry_size = _RESOURCE_ENTRY_SIZE if is_resource else _RECORD_ENTRY_SIZE
        record_list_size = num_items * entry_size
        padding = 2 if num_items > 0 else 0

        # Calculate offsets
        data_start = _HEADER_SIZE + record_list_size + padding

        app_info_offset = 0
        sort_info_offset = 0

        if self.app_info is not None:
            app_info_offset = data_start
            data_start += len(self.app_info)

        if self.sort_info is not None:
            sort_info_offset = data_start
            data_start += len(self.sort_info)

        # Build record/resource list and collect data offsets
        record_list = b""
        offset = data_start
        if is_resource:
            for res in self.resources:
                record_list += struct.pack(
                    _RESOURCE_ENTRY_FORMAT,
                    res.res_type.encode("ascii"),
                    res.res_id,
                    offset,
                )
                offset += len(res.data)
        else:
            for rec in self.records:
                uid_bytes = rec.unique_id.to_bytes(3, "big")
                record_list += struct.pack(">IB", offset, rec.attributes) + uid_bytes
                offset += len(rec.data)

        # Build header
        name_bytes = self.name.encode("ascii")[:31].ljust(32, b"\x00")
        header = struct.pack(
            _HEADER_FORMAT,
            name_bytes,
            self.attributes,
            self.version,
            _datetime_to_palm(self.creation_time),
            _datetime_to_palm(self.modification_time),
            _datetime_to_palm(self.backup_time),
            self.modification_number,
            app_info_offset,
            sort_info_offset,
            self.db_type.encode("ascii"),
            self.creator.encode("ascii"),
            self.unique_id_seed,
            0,  # next_record_list
            num_items,
        )

        # Assemble output
        output = header + record_list
        if padding:
            output += b"\x00" * padding
        if self.app_info is not None:
            output += self.app_info
        if self.sort_info is not None:
            output += self.sort_info
        if is_resource:
            for res in self.resources:
                output += res.data
        else:
            for rec in self.records:
                output += rec.data

        return output

    @classmethod
    def from_file(cls, path: str | Path) -> "PalmDatabase":
        """Load a PDB/PRC database from a file."""
        return cls.from_bytes(Path(path).read_bytes())

    def to_file(self, path: str | Path) -> None:
        """Write the database to a file."""
        Path(path).write_bytes(self.to_bytes())

    @classmethod
    def from_device(cls, dlp, name: str, db_type: str = "", creator: str = "",
                    attributes: int = 0) -> "PalmDatabase":
        """Download a database from the device via DLP."""
        from palm.dlp import DB_MODE_READ

        is_resource = bool(attributes & ATTR_RESOURCE)
        handle = dlp.open_db(name, DB_MODE_READ)

        try:
            num_items = dlp.read_open_db_info(handle)

            db = cls(
                name=name,
                attributes=attributes,
                db_type=db_type,
                creator=creator,
            )

            # Read app info (may not exist for all databases)
            from palm.dlp import DLPException, DLPError
            try:
                app_info = dlp.read_app_block(handle)
                if app_info:
                    db.app_info = app_info
            except DLPException as e:
                if e.error_code != DLPError.NOT_FOUND:
                    raise

            # Read sort info (may not exist for all databases)
            try:
                sort_info = dlp.read_sort_block(handle)
                if sort_info:
                    db.sort_info = sort_info
            except DLPException as e:
                if e.error_code != DLPError.NOT_FOUND:
                    raise

            # Read records or resources
            for i in range(num_items):
                if is_resource:
                    res = dlp.read_resource(handle, i)
                    db.resources.append(Resource(
                        res_type=res.res_type, res_id=res.res_id, data=res.data,
                    ))
                else:
                    rec = dlp.read_record(handle, i)
                    db.records.append(Record(
                        data=rec.data, attributes=rec.attributes, unique_id=rec.unique_id,
                    ))

            return db
        finally:
            dlp.close_db(handle)

    def to_device(self, dlp) -> None:
        """Upload this database to the device via DLP."""
        from palm.dlp import DLPException, DLPError

        # Delete existing DB (ignore if not found)
        try:
            dlp.delete_db(self.name)
        except DLPException as e:
            if e.error_code not in (DLPError.NOT_FOUND, DLPError.NONE_OPEN):
                raise

        # Create the database, then reopen in read-write mode
        from palm.dlp import DB_MODE_READ_WRITE
        handle = dlp.create_db(
            name=self.name,
            creator=self.creator,
            db_type=self.db_type,
            flags=self.attributes,
            version=self.version,
        )
        dlp.close_db(handle)
        handle = dlp.open_db(self.name, DB_MODE_READ_WRITE)

        try:
            # Write app info
            if self.app_info:
                dlp.write_app_block(handle, self.app_info)

            # Write sort info
            if self.sort_info:
                dlp.write_sort_block(handle, self.sort_info)

            # Write records or resources
            if self.is_resource_db:
                from palm.dlp import Resource as DLPResource
                for res in self.resources:
                    dlp.write_resource(handle, DLPResource(
                        res_type=res.res_type, res_id=res.res_id, index=0, data=res.data,
                    ))
            else:
                from palm.dlp import Record as DLPRecord
                for rec in self.records:
                    dlp.write_record(handle, DLPRecord(
                        index=0, attributes=rec.attributes,
                        unique_id=rec.unique_id, data=rec.data,
                    ))
        finally:
            dlp.close_db(handle)
