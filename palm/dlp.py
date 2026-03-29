"""DLP - Desktop Link Protocol for PalmOS HotSync."""
from __future__ import annotations

import struct
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, List

from palm.padp import PADPConnection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

class DLPError(IntEnum):
    NONE = 0x0000
    SYSTEM = 0x0001
    MEMORY = 0x0002
    PARAM = 0x0003
    NOT_FOUND = 0x0004
    NONE_OPEN = 0x0005
    ALREADY_OPEN = 0x0006
    TOO_MANY_OPEN = 0x0007
    ALREADY_EXISTS = 0x0008
    OPEN = 0x0009
    DELETED = 0x000A
    BUSY = 0x000B
    NOT_SUPPORTED = 0x000C
    READ_ONLY = 0x000F
    NOT_ENOUGH_SPACE = 0x0010
    LIMIT_EXCEEDED = 0x0011
    CANCELLED = 0x0012


DLP_ERROR_MESSAGES = {
    DLPError.NONE: "No error",
    DLPError.SYSTEM: "General system error",
    DLPError.MEMORY: "Insufficient memory",
    DLPError.PARAM: "Invalid parameter",
    DLPError.NOT_FOUND: "Not found",
    DLPError.NONE_OPEN: "No databases are open",
    DLPError.ALREADY_OPEN: "Database already open",
    DLPError.TOO_MANY_OPEN: "Too many open databases",
    DLPError.ALREADY_EXISTS: "Already exists",
    DLPError.OPEN: "Database is open",
    DLPError.DELETED: "Record deleted",
    DLPError.BUSY: "Record busy",
    DLPError.NOT_SUPPORTED: "Operation not supported",
    DLPError.READ_ONLY: "Read only",
    DLPError.NOT_ENOUGH_SPACE: "Not enough space",
    DLPError.LIMIT_EXCEEDED: "Limit exceeded",
    DLPError.CANCELLED: "Sync cancelled",
}


# ---------------------------------------------------------------------------
# Function IDs
# ---------------------------------------------------------------------------

class DLPFuncID(IntEnum):
    READ_SYS_INFO = 0x12
    READ_DB_LIST = 0x16
    OPEN_DB = 0x17
    CREATE_DB = 0x18
    CLOSE_DB = 0x19
    DELETE_DB = 0x1A
    READ_APP_BLOCK = 0x1B
    WRITE_APP_BLOCK = 0x1C
    READ_SORT_BLOCK = 0x1D
    WRITE_SORT_BLOCK = 0x1E
    READ_RECORD = 0x20
    WRITE_RECORD = 0x21
    DELETE_RECORD = 0x22
    READ_RESOURCE = 0x23
    WRITE_RESOURCE = 0x24
    DELETE_RESOURCE = 0x25
    READ_OPEN_DB_INFO = 0x2B
    OPEN_CONDUIT = 0x2E
    END_OF_SYNC = 0x2F


# ---------------------------------------------------------------------------
# Mode constants
# ---------------------------------------------------------------------------

DB_MODE_READ = 0x80
DB_MODE_WRITE = 0x40
DB_MODE_READ_WRITE = 0xC0
DB_MODE_EXCLUSIVE = 0x20

DBLIST_RAM = 0x80
DBLIST_ROM = 0x40
DBLIST_MULTIPLE = 0x20


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DLPArg:
    arg_id: int
    data: bytes = b""


@dataclass
class SysInfo:
    rom_version: int
    locale: int
    name: str


@dataclass
class DatabaseInfo:
    name: str
    attributes: int
    version: int
    creation_time: int
    modification_time: int
    backup_time: int
    db_type: bytes
    creator: bytes
    num_records: int


@dataclass
class Record:
    index: int
    attributes: int
    unique_id: int
    data: bytes


@dataclass
class Resource:
    res_type: bytes
    res_id: int
    index: int
    data: bytes


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class DLPException(Exception):
    def __init__(self, func_id: int, error_code: int):
        self.func_id = func_id
        self.error_code = error_code
        msg = DLP_ERROR_MESSAGES.get(error_code, f"Unknown error 0x{error_code:04X}")
        super().__init__(f"DLP error 0x{error_code:04X} on func 0x{func_id:02X}: {msg}")


# ---------------------------------------------------------------------------
# Helper: parse SysInfo response arg data
# ---------------------------------------------------------------------------

def parse_sys_info(data: bytes) -> SysInfo:
    rom_version = struct.unpack_from(">I", data, 0)[0]
    locale = struct.unpack_from(">I", data, 4)[0]
    name_bytes = data[8:]
    null_pos = name_bytes.find(b"\x00")
    name = name_bytes[:null_pos].decode("latin-1") if null_pos >= 0 else name_bytes.decode("latin-1")
    return SysInfo(rom_version=rom_version, locale=locale, name=name)


# ---------------------------------------------------------------------------
# DLPClient
# ---------------------------------------------------------------------------

class DLPClient:
    def __init__(self, padp: PADPConnection):
        self._padp = padp

    # ------------------------------------------------------------------
    # Static protocol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build_request(func_id: int, args: List[DLPArg]) -> bytes:
        """Build a raw DLP request packet."""
        header = struct.pack(">BB", func_id, len(args))
        body = bytearray(header)
        for arg in args:
            data = arg.data
            size = len(data)
            if size < 256:
                # Tiny arg: 1-byte ID (with FLAG_TINY=0x00), 1-byte size
                body += bytes([arg.arg_id & 0x3F, size])
                body += data
            elif size < 65536:
                # Short arg: 1-byte ID (with FLAG_SHORT=0x80), pad, 2-byte size
                body += bytes([(arg.arg_id & 0x3F) | 0x80, 0x00])
                body += struct.pack(">H", size)
                body += data
            else:
                # Long arg: 1-byte ID (with FLAG_LONG=0x40), pad, 4-byte size
                body += bytes([(arg.arg_id & 0x3F) | 0x40, 0x00])
                body += struct.pack(">I", size)
                body += data
        return bytes(body)

    @staticmethod
    def parse_response(data: bytes) -> tuple:
        """Parse a raw DLP response packet. Returns (func_id, error_code, args)."""
        func_id = data[0] & 0x7F
        arg_count = data[1]
        error_code = struct.unpack_from(">H", data, 2)[0]

        args = []
        offset = 4
        for _ in range(arg_count):
            if offset >= len(data):
                break
            first_byte = data[offset]
            flag = first_byte & 0xC0
            arg_id = first_byte & 0x3F
            if flag == 0x80:
                # Short arg: 1-byte ID|0x80, pad, 2-byte size
                size = struct.unpack_from(">H", data, offset + 2)[0]
                arg_data = data[offset + 4: offset + 4 + size]
                offset += 4 + size
            elif flag == 0x40:
                # Long arg: 1-byte ID|0x40, pad, 4-byte size
                size = struct.unpack_from(">I", data, offset + 2)[0]
                arg_data = data[offset + 6: offset + 6 + size]
                offset += 6 + size
            else:
                # Tiny arg: 1-byte ID, 1-byte size
                size = data[offset + 1]
                arg_data = data[offset + 2: offset + 2 + size]
                offset += 2 + size
            args.append(DLPArg(arg_id=arg_id, data=arg_data))

        return func_id, error_code, args

    # ------------------------------------------------------------------
    # Internal execute
    # ------------------------------------------------------------------

    def _execute(self, func_id: int, args: Optional[List[DLPArg]] = None) -> List[DLPArg]:
        if args is None:
            args = []
        raw = self.build_request(func_id, args)
        logger.debug("DLP send: func=0x%02X args=%d bytes=%d hex=%s", func_id, len(args), len(raw), raw.hex())
        self._padp.send(raw)
        response = self._padp.receive()
        resp_func_id, error_code, resp_args = self.parse_response(response)
        logger.debug("DLP recv: func=0x%02X error=0x%04X args=%d raw=%s", resp_func_id, error_code, len(resp_args), response[:20].hex())
        if error_code != 0:
            raise DLPException(func_id, error_code)
        return resp_args

    # ------------------------------------------------------------------
    # DLP commands
    # ------------------------------------------------------------------

    def read_sys_info(self) -> SysInfo:
        resp_args = self._execute(DLPFuncID.READ_SYS_INFO)
        if resp_args:
            return parse_sys_info(resp_args[0].data)
        raise DLPException(DLPFuncID.READ_SYS_INFO, DLPError.SYSTEM)

    def open_conduit(self) -> None:
        self._execute(DLPFuncID.OPEN_CONDUIT)

    def list_databases(self, ram: bool = True, rom: bool = False) -> List[DatabaseInfo]:
        flags = 0
        if ram:
            flags |= DBLIST_RAM
        if rom:
            flags |= DBLIST_ROM

        databases = []
        start_index = 0

        while True:
            arg_data = struct.pack(">BBH", flags, 0, start_index)
            arg = DLPArg(arg_id=0x20, data=arg_data)
            try:
                resp_args = self._execute(DLPFuncID.READ_DB_LIST, [arg])
            except DLPException as e:
                if e.error_code == DLPError.NOT_FOUND:
                    break
                raise

            if not resp_args:
                break

            # Response format (from pilot-link dlp.c):
            # p+0: last_index (2 bytes)
            # p+2: more flag (1 byte)
            # p+3: count (1 byte)
            # Per entry (at p+4 for single, repeated for multiple):
            #   +0: total_size (1 byte, only with DBLIST_MULTIPLE)
            #   +1: misc_flags (1 byte)
            #   +2: db_flags (2 bytes)
            #   +4: type (4 bytes)
            #   +8: creator (4 bytes)
            #   +12: version (2 bytes)
            #   +14: modnum (4 bytes)
            #   +18: crdate (4+4=8 bytes, two 32-bit values)
            #   +26: moddate (8 bytes)
            #   +34: backupdate (8 bytes)
            #   +42: index (2 bytes)
            #   +44: name (32 bytes, null-terminated)
            p = resp_args[0].data
            last_index = struct.unpack_from(">H", p, 0)[0]
            more = p[2]
            count = p[3]

            for i in range(count):
                # Entry starts at offset 4 (no total_size byte without MULTIPLE)
                e = 4 + i * 80  # fixed 80-byte entries without MULTIPLE
                misc_flags = p[e + 1]
                db_flags = struct.unpack_from(">H", p, e + 2)[0]
                db_type = p[e + 4:e + 8].decode("latin-1")
                creator = p[e + 8:e + 12].decode("latin-1")
                version = struct.unpack_from(">H", p, e + 12)[0]
                modnum = struct.unpack_from(">I", p, e + 14)[0]
                crdate = struct.unpack_from(">I", p, e + 18)[0]
                moddate = struct.unpack_from(">I", p, e + 26)[0]
                backupdate = struct.unpack_from(">I", p, e + 34)[0]
                db_index = struct.unpack_from(">H", p, e + 42)[0]
                name_raw = p[e + 44:e + 76]
                name = name_raw.split(b"\x00", 1)[0].decode("latin-1")

                databases.append(DatabaseInfo(
                    name=name,
                    attributes=db_flags,
                    version=version,
                    creation_time=crdate,
                    modification_time=moddate,
                    backup_time=backupdate,
                    db_type=db_type,
                    creator=creator,
                    num_records=0,
                ))

            if not more:
                break
            start_index = last_index + 1

        return databases

    def open_db(self, name: str, mode: int = DB_MODE_READ) -> int:
        name_bytes = name.encode("latin-1") + b"\x00"
        arg_data = struct.pack(">BB", 0, mode) + name_bytes
        arg = DLPArg(arg_id=0x20, data=arg_data)
        resp_args = self._execute(DLPFuncID.OPEN_DB, [arg])
        if resp_args:
            return resp_args[0].data[0]
        raise DLPException(DLPFuncID.OPEN_DB, DLPError.SYSTEM)

    def close_db(self, handle: int) -> None:
        arg = DLPArg(arg_id=0x20, data=bytes([handle]))
        self._execute(DLPFuncID.CLOSE_DB, [arg])

    def delete_db(self, name: str) -> None:
        name_bytes = name.encode("latin-1") + b"\x00"
        arg_data = struct.pack(">BB", 0, 0) + name_bytes
        arg = DLPArg(arg_id=0x20, data=arg_data)
        self._execute(DLPFuncID.DELETE_DB, [arg])

    def create_db(self, name: str, creator: str, db_type: str,
                  flags: int = 0, version: int = 0) -> int:
        name_bytes = name.encode("latin-1") + b"\x00"
        creator_b = creator.encode("latin-1")[:4].ljust(4, b"\x00")
        type_b = db_type.encode("latin-1")[:4].ljust(4, b"\x00")
        # Format: creator(4) + type(4) + card(1) + pad(1) + flags(2) + version(2) + name
        arg_data = (creator_b + type_b +
                    struct.pack(">BBHH", 0, 0, flags, version) +
                    name_bytes)
        arg = DLPArg(arg_id=0x20, data=arg_data)
        resp_args = self._execute(DLPFuncID.CREATE_DB, [arg])
        if resp_args:
            return resp_args[0].data[0]
        raise DLPException(DLPFuncID.CREATE_DB, DLPError.SYSTEM)

    def read_open_db_info(self, handle: int) -> int:
        arg = DLPArg(arg_id=0x20, data=bytes([handle]))
        resp_args = self._execute(DLPFuncID.READ_OPEN_DB_INFO, [arg])
        if resp_args:
            return struct.unpack_from(">H", resp_args[0].data, 0)[0]
        raise DLPException(DLPFuncID.READ_OPEN_DB_INFO, DLPError.SYSTEM)

    def read_record(self, handle: int, index: int) -> Record:
        # arg: handle(B) + padding(B) + index(H) + offset(H) + max_len(H)
        arg_data = struct.pack(">BBHHH", handle, 0x00, index, 0, 0xFFFF)
        arg = DLPArg(arg_id=0x21, data=arg_data)
        resp_args = self._execute(DLPFuncID.READ_RECORD, [arg])
        if not resp_args:
            raise DLPException(DLPFuncID.READ_RECORD, DLPError.SYSTEM)
        rdata = resp_args[0].data
        rec_id = struct.unpack_from(">I", rdata, 0)[0]
        rec_index = struct.unpack_from(">H", rdata, 4)[0]
        size = struct.unpack_from(">H", rdata, 6)[0]
        attrs = rdata[8]
        category = rdata[9]
        data = rdata[10:10 + size]
        return Record(index=rec_index, attributes=attrs, unique_id=rec_id, data=data)

    def write_record(self, handle: int, record: Record) -> None:
        size = len(record.data)
        arg_data = (struct.pack(">B", handle) +
                    struct.pack(">I", record.unique_id) +
                    struct.pack(">BB", record.attributes, 0) +
                    struct.pack(">H", size) +
                    record.data)
        arg = DLPArg(arg_id=0x20, data=arg_data)
        self._execute(DLPFuncID.WRITE_RECORD, [arg])

    def read_resource(self, handle: int, index: int) -> Resource:
        # arg: handle(B) + padding(B) + index(H) + offset(H) + max_len(H)
        arg_data = struct.pack(">BBHHH", handle, 0x00, index, 0, 0xFFFF)
        arg = DLPArg(arg_id=0x20, data=arg_data)
        resp_args = self._execute(DLPFuncID.READ_RESOURCE, [arg])
        if not resp_args:
            raise DLPException(DLPFuncID.READ_RESOURCE, DLPError.SYSTEM)
        rdata = resp_args[0].data
        res_type = rdata[0:4].decode("latin-1")
        res_id = struct.unpack_from(">H", rdata, 4)[0]
        res_index = struct.unpack_from(">H", rdata, 6)[0]
        size = struct.unpack_from(">H", rdata, 8)[0]
        data = rdata[10:10 + size]
        return Resource(res_type=res_type, res_id=res_id, index=res_index, data=data)

    def write_resource(self, handle: int, resource: Resource) -> None:
        size = len(resource.data)
        res_type_b = resource.res_type.encode("latin-1")[:4].ljust(4, b"\x00")
        # Format: handle(1) + pad(1) + type(4) + resID(2) + size(2) + data
        arg_data = (struct.pack(">BB", handle, 0) +
                    res_type_b +
                    struct.pack(">HH", resource.res_id, size) +
                    resource.data)
        arg = DLPArg(arg_id=0x20, data=arg_data)
        self._execute(DLPFuncID.WRITE_RESOURCE, [arg])

    def read_app_block(self, handle: int) -> bytes:
        arg_data = struct.pack(">BHH", handle, 0, 0xFFFF)
        arg = DLPArg(arg_id=0x20, data=arg_data)
        resp_args = self._execute(DLPFuncID.READ_APP_BLOCK, [arg])
        if not resp_args:
            return b""
        rdata = resp_args[0].data
        size = struct.unpack_from(">H", rdata, 0)[0]
        return rdata[2:2 + size]

    def write_app_block(self, handle: int, data: bytes) -> None:
        arg_data = struct.pack(">BHH", handle, 0, len(data)) + data
        arg = DLPArg(arg_id=0x20, data=arg_data)
        self._execute(DLPFuncID.WRITE_APP_BLOCK, [arg])

    def read_sort_block(self, handle: int) -> bytes:
        arg_data = struct.pack(">BHH", handle, 0, 0xFFFF)
        arg = DLPArg(arg_id=0x20, data=arg_data)
        resp_args = self._execute(DLPFuncID.READ_SORT_BLOCK, [arg])
        if not resp_args:
            return b""
        rdata = resp_args[0].data
        size = struct.unpack_from(">H", rdata, 0)[0]
        return rdata[2:2 + size]

    def write_sort_block(self, handle: int, data: bytes) -> None:
        arg_data = struct.pack(">BHH", handle, 0, len(data)) + data
        arg = DLPArg(arg_id=0x20, data=arg_data)
        self._execute(DLPFuncID.WRITE_SORT_BLOCK, [arg])

    def end_of_sync(self, status: int = 0) -> None:
        arg = DLPArg(arg_id=0x20, data=struct.pack(">H", status))
        self._execute(DLPFuncID.END_OF_SYNC, [arg])
