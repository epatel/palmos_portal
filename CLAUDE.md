# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install
pip install -r requirements.txt

# Run tests (53 unit tests, no device needed)
pytest tests/ -v
pytest tests/test_slp.py -v                                    # single file
pytest tests/test_pdb.py::TestPDBHeader::test_roundtrip_pdb -v # single test

# CLI (each command requires HotSync button press on device)
python cli.py list
python cli.py pull MemoDB
python cli.py push file.prc
python cli.py delete TestDB
python cli.py sysinfo

# Web dashboard (opens browser, polls for device)
python cli.py web
```

## Architecture

### Protocol Stack

```
palm/transport.py  — USB bulk transfers via PyUSB, Visor vendor init
    palm/slp.py    — Serial Link Protocol: packet framing, CRC-CCITT
        palm/padp.py   — Packet Assembly/Disassembly: reliable delivery, fragmentation
            palm/dlp.py    — Desktop Link Protocol: database commands
```

Each layer wraps the one below. Connection lifecycle: USB detect → vendor control transfer (0x03) → select 64-byte endpoints → CMP handshake over PADP (receive WAKEUP 0x01, send INIT 0x02) → DLP OpenConduit → commands → EndOfSync.

### Web Dashboard Threading (`web/server.py`)

Main thread runs FastAPI/asyncio. Device thread does blocking USB I/O (polling, CMP, DLP commands, tickle keepalives). All DLP access serialized through `_dlp_lock`. Communication via `asyncio.Queue`. REST endpoints use `run_in_executor` but always acquire `_dlp_lock`.

### PalmOS App Development (`palm/resources.py`, `palm/project.py`)

Three files per OnboardC project: `Name.c.pdb` (TEXt/REAd source), `Name.Rsrc.prc` (Rsrc/OnBD resources), `Name.proj.prc` (Proj/OnBD project). Push all three, compile on device. See `docs/palmos-dev-guide.md` for full reference.

## Critical Conventions

### Encoding
- **All protocol data**: big-endian (`struct.pack(">...")`)
- **PalmOS text**: Windows-1252 (`cp1252`) — not latin-1, not utf-8
- **4-char codes**: ASCII, null-padded to 4 bytes

### DLP Argument Format
```
Tiny  (< 256 bytes): ID|0x00 (1B) + size (1B) + data
Short (< 64K):       ID|0x80 (1B) + pad (1B) + size (2B) + data
Long  (>= 64K):      ID|0x40 (1B) + pad (1B) + size (4B) + data
```
Arg ID is always 1 byte with top 2 bits as format flag. First arg ID is `0x20`. DLP requests are 2-byte header (func_id + argc) — no error byte. Responses have `func_id|0x80 + argc + error_code(2B)`.

### PADP Fragmentation
Max 512 bytes per fragment. First fragment size field = total length; subsequent = byte offset. Transaction IDs start 0xFF, wrap to 0x01, skip 0x00.

### SLP CRC
CRC-CCITT (poly 0x1021, init 0x0000) over **header + body** combined — not body alone.

### PalmOS Timestamps
Epoch 1904-01-01 (Mac epoch). Unix offset: `2082844800` seconds.

### tFRM Resource Format
68-byte form header → N × 6-byte directory (`type:B, pad:B, offset:I`) → object data. Object types: 1=control, 8=label, 9=title. Controls: `id(2) + bounds(8) + bitmapIds(4) + attr(2) + style(1) + font(1) + group(1) + pad(1) + text\0`.

### OBPJ Project Format
Fixed 330 bytes for 2-file projects. Template-based — filename slots at offsets 115 (.Rsrc, 13B), 223 (.c, 11B), 294 (.obj, 13B). Project names max ~10 chars.

### USB Specifics
Handspring Visor: vendor `0x082D`, product `0x0100`. Four endpoints — use the 64-byte pair (0x82/0x02), not the 16-byte pair. Device only enumerates when HotSync pressed (~30s window). After unclean disconnect, avoid `dev.reset()` (segfaults libusb) — just drop references.
