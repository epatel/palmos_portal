# PalmOS Communication Tool — Design Spec

## Overview

A Python CLI tool for reading and writing PalmOS databases (`.pdb`) and applications (`.prc`) to/from a Handspring Visor via its USB cradle. Implements the HotSync protocol stack from scratch on top of `pyserial` for USB-serial transport.

## Target Device

- **Handspring Visor** (all models) connected via USB cradle
- The Visor's cradle presents as a USB-serial device on macOS
- Serial port appears as `/dev/tty.usbserial-*` or `/dev/tty.usbmodem-*` when HotSync button is pressed

## Project Structure

```
palm_com/
├── palm/
│   ├── __init__.py
│   ├── transport.py    # USB-serial connection
│   ├── slp.py          # Serial Link Protocol
│   ├── padp.py         # Packet Assembly/Disassembly Protocol
│   ├── dlp.py          # Desktop Link Protocol
│   └── pdb.py          # .prc/.pdb file format
├── cli.py              # CLI entry point
├── requirements.txt
└── pyproject.toml
```

## Layer 1: Transport (`transport.py`)

### Responsibility
Manage the raw serial connection to the Visor's USB cradle.

### Interface
```python
class Connection:
    def open(self, port: str | None = None, baudrate: int = 9600) -> None
    def close(self) -> None
    def read(self, n: int, timeout: float = 5.0) -> bytes
    def write(self, data: bytes) -> None
    def detect_port(self) -> str
```

### Details
- **Auto-detection:** Scan `/dev/tty.usb*` ports. The Visor's cradle typically identifies with a known USB vendor/product ID (Handspring: `0x082D`).
- **Baud rate:** Start at 9600 baud. The Visor may negotiate a higher rate (57600) during the initial handshake — handle rate switching after negotiation.
- **Timeouts:** Default 5-second read timeout. The Visor has a limited window after pressing HotSync before it times out (~30 seconds).
- **Context manager:** Support `with Connection() as conn:` for clean open/close.

## Layer 2: SLP — Serial Link Protocol (`slp.py`)

### Responsibility
Packet framing over the raw serial byte stream. Each SLP packet wraps a payload with addressing, type info, and integrity checks.

### Packet Format
```
Offset  Size  Field
0       3     Signature: 0xBE 0xEF 0xED
3       1     Destination socket ID
4       1     Source socket ID
5       1     Packet type (0x00=data, 0x01=loopback, 0x02=ACK)
6       2     Data length (big-endian)
8       1     Transaction ID
9       1     Header checksum (sum of bytes 0-8 mod 256)
10      N     Body (payload)
10+N    2     CRC-16 over body
```

### Interface
```python
class SLPSocket:
    def __init__(self, connection: Connection)
    def send(self, dest: int, src: int, ptype: int, txn_id: int, data: bytes) -> None
    def receive(self) -> SLPPacket
```

### Details
- **CRC-16:** CRC-CCITT (polynomial 0x1021), standard for Palm HotSync.
- **Socket IDs:** DLP uses socket 3 (desktop) and socket 3 (device). Loopback uses socket 0.
- **Validation:** On receive, verify header checksum and CRC-16. Discard corrupt packets.
- **Sync detection:** Scan byte stream for the 0xBEEFED signature to find packet boundaries.

## Layer 3: PADP — Packet Assembly/Disassembly Protocol (`padp.py`)

### Responsibility
Reliable delivery and fragmentation on top of SLP. Ensures packets are acknowledged and large payloads are split into manageable chunks.

### Packet Format (PADP header, inside SLP body)
```
Offset  Size  Field
0       1     Type (0x01=data, 0x02=ACK, 0x04=tickle)
1       1     Flags (0x80=first fragment, 0x40=last fragment, 0x20=more fragments)
2       2     Payload size (big-endian)
4       N     Payload
```

### Interface
```python
class PADPConnection:
    def __init__(self, slp: SLPSocket)
    def send(self, data: bytes) -> None
    def receive(self) -> bytes
```

### Details
- **Fragmentation:** Max SLP body is ~1024 bytes. PADP splits larger payloads across multiple SLP packets with fragment flags.
- **Reliability:** After sending a data packet, wait for an ACK with matching transaction ID. Retransmit up to 3 times on timeout (2-second intervals).
- **Tickle:** The device sends tickle packets as keep-alives during long operations. Respond with a tickle ACK to prevent timeout.
- **Transaction IDs:** Increment per send. Start from 0xFF (wraps to 0x01, skipping 0x00).
- **Reassembly:** Collect fragments until "last fragment" flag is set, concatenate payloads.

## Layer 4: DLP — Desktop Link Protocol (`dlp.py`)

### Responsibility
High-level commands for database operations. This is where "list databases" and "read record" live.

### Request Format
```
Offset  Size  Field
0       1     Function ID
1       1     Argument count
2       1     Error code (0x00 in requests)
3+      var   Arguments (each: 2-byte ID, 2-byte size, N-byte data)
```

### Response Format
```
Offset  Size  Field
0       1     Function ID + 0x80 (response flag)
1       1     Argument count
2       2     Error code (0=success)
4+      var   Return arguments
```

### Commands to Implement

| Command | ID | Purpose |
|---|---|---|
| `ReadSysInfo` | 0x12 | Device OS version, ROM version, name |
| `OpenConduit` | 0x2E | Required handshake before DB operations |
| `ReadDBList` | 0x16 | Enumerate databases (RAM/ROM, by index) |
| `OpenDB` | 0x17 | Open database by name, returns handle |
| `CloseDB` | 0x19 | Close a database handle |
| `DeleteDB` | 0x1A | Delete a database by name |
| `CreateDB` | 0x18 | Create new database on device |
| `ReadOpenDBInfo` | 0x28 | Get record/resource count for open DB |
| `ReadAppBlock` | 0x1B | Read AppInfo block |
| `WriteAppBlock` | 0x1C | Write AppInfo block |
| `ReadSortBlock` | 0x1D | Read SortInfo block |
| `WriteSortBlock` | 0x1E | Write SortInfo block |
| `ReadRecord` | 0x20 | Read record by index from open DB |
| `WriteRecord` | 0x21 | Write record to open DB |
| `ReadResource` | 0x22 | Read resource by index from open DB |
| `WriteResource` | 0x23 | Write resource to open DB |
| `EndOfSync` | 0x2F | Clean disconnect |

### Interface
```python
class DLPClient:
    def __init__(self, padp: PADPConnection)
    def read_sys_info(self) -> SysInfo
    def open_conduit(self) -> None
    def list_databases(self, ram: bool = True, rom: bool = False) -> list[DatabaseInfo]
    def open_db(self, name: str, mode: int = READ) -> int  # returns handle
    def close_db(self, handle: int) -> None
    def delete_db(self, name: str) -> None
    def create_db(self, name: str, creator: str, db_type: str, flags: int) -> int
    def read_open_db_info(self, handle: int) -> OpenDBInfo
    def read_record(self, handle: int, index: int) -> Record
    def write_record(self, handle: int, record: Record) -> None
    def read_resource(self, handle: int, index: int) -> Resource
    def write_resource(self, handle: int, resource: Resource) -> None
    def read_app_block(self, handle: int) -> bytes
    def write_app_block(self, handle: int, data: bytes) -> None
    def read_sort_block(self, handle: int) -> bytes
    def write_sort_block(self, handle: int, data: bytes) -> None
    def end_of_sync(self) -> None
```

### HotSync Lifecycle
Every CLI operation follows this sequence:
1. User presses HotSync button on cradle
2. Open serial connection (auto-detect port)
3. Receive/send initial CMP (Connection Management Protocol) handshake
4. `OpenConduit` — required before any DB operations
5. Execute DLP commands for the requested operation
6. `EndOfSync` — tells device sync is complete
7. Close serial connection

**Note on CMP:** Before DLP begins, there's a brief CMP exchange where the device and desktop negotiate baud rate and protocol version. This is a simple 2-packet exchange (device sends CMP init, desktop responds with CMP init) handled in `transport.py` as part of connection setup.

## Layer 5: PDB/PRC File Format (`pdb.py`)

### Responsibility
Parse and serialize PalmOS database files (`.pdb` for data, `.prc` for applications/resources).

### File Format
```
Offset  Size  Field
0       32    Database name (null-terminated)
32      2     Attributes (bit 0: resource DB = .prc)
34      2     Version
36      4     Creation timestamp (seconds since 1904-01-01)
40      4     Modification timestamp
44      4     Last backup timestamp
48      4     Modification number
52      4     AppInfo offset (0 if none)
56      4     SortInfo offset (0 if none)
60      4     Type code (4 chars, e.g., 'DATA')
64      4     Creator code (4 chars, e.g., 'Memo')
68      4     Unique ID seed
72      4     Next record list (always 0)
76      2     Number of records/resources
78+     var   Record/resource list entries
var     var   AppInfo block (if present)
var     var   SortInfo block (if present)
var     var   Record/resource data
```

**Record list entry (for .pdb):** 8 bytes — offset (4), attributes (1), unique ID (3)
**Resource list entry (for .prc):** 10 bytes — type (4), ID (2), offset (4)

### Interface
```python
class PalmDatabase:
    name: str
    attributes: int
    version: int
    creation_time: datetime
    modification_time: datetime
    db_type: str       # 4-char code
    creator: str       # 4-char code
    app_info: bytes | None
    sort_info: bytes | None
    records: list[Record]      # for .pdb
    resources: list[Resource]  # for .prc

    @classmethod
    def from_file(cls, path: str) -> PalmDatabase
    def to_file(self, path: str) -> None

    @property
    def is_resource_db(self) -> bool

    @classmethod
    def from_device(cls, dlp: DLPClient, name: str) -> PalmDatabase
    def to_device(self, dlp: DLPClient) -> None
```

### Details
- **Timestamps:** PalmOS epoch is 1904-01-01 00:00:00 UTC (Mac epoch). Convert to/from Python `datetime`.
- **4-char codes:** Stored as 4 raw bytes, displayed as ASCII strings.
- **`from_device`:** Opens the DB via DLP, reads all records/resources and AppInfo/SortInfo, assembles into a `PalmDatabase`.
- **`to_device`:** Creates the DB via DLP (or deletes+recreates if it exists), writes AppInfo/SortInfo, then writes all records/resources.

## CLI (`cli.py`)

### Commands

```
palm list [--rom] [--ram]        List databases on device (default: RAM only)
palm info <name>                 Show database header info
palm pull <name> [--out FILE]    Download database to local file
palm push <file>                 Upload .prc/.pdb to device
palm delete <name>               Delete database from device
palm sysinfo                     Show device info (OS version, memory, name)
```

### Options
- `--port PORT` — Override auto-detected serial port
- `--verbose` / `-v` — Show protocol-level debug output (packet hex dumps)
- `--timeout SECS` — Connection timeout (default: 10)

### Behavior
- Each command opens a full HotSync session (connect → handshake → command → end sync → disconnect).
- `palm push` reads the local file, determines if it's `.prc` or `.pdb` from the attributes flag, and uploads accordingly. If a database with the same name exists, it is deleted first and recreated.
- `palm pull` auto-generates the output filename from the database name + appropriate extension if `--out` is not specified.
- `palm list` prints a formatted table: name, type, creator, size, record count.
- Error messages guide the user: "No device found — is the Visor in the cradle? Press HotSync and try again."

## Dependencies

- `pyserial` — serial port communication
- `click` — CLI framework
- Python 3.10+ (for `match` statements and modern type hints)

## Error Handling

- **Connection errors:** Timeout waiting for device → clear message about pressing HotSync.
- **Protocol errors:** CRC mismatch, unexpected packet type → retry at PADP level (up to 3 times), then fail with diagnostic info.
- **DLP errors:** Device returns error code → map to human-readable message (e.g., "database not found", "out of memory").
- **File errors:** Invalid `.pdb`/`.prc` file → validate header before attempting upload.

## Out of Scope (for now)

- Conduit system (app-specific sync logic like Memo Pad sync)
- Calendar/contact sync
- ROM flashing
- Support for non-Visor Palm devices (can be added later)
- Network HotSync (WiFi/Bluetooth)
