"""Palm-Com web dashboard server."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import struct
import threading
import time
import zipfile
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from palm.transport import Connection
from palm.slp import SLPSocket
from palm.padp import PADPConnection
from palm.dlp import (
    DLPClient, DLPException, DLPError, DatabaseInfo,
    DB_MODE_READ, DBLIST_RAM, DBLIST_ROM,
)
from palm.pdb import PalmDatabase, ATTR_RESOURCE

logger = logging.getLogger(__name__)

# CMP constants (same as cli.py)
CMP_TYPE_INIT = 0x02
_CMP_FORMAT = ">BBBBHI"
_CMP_SIZE = struct.calcsize(_CMP_FORMAT)


class DeviceManager:
    """Manages the Visor USB connection in a background thread."""

    def __init__(self):
        self.state: str = "disconnected"
        self.conn: Connection | None = None
        self.dlp: DLPClient | None = None
        self._padp: PADPConnection | None = None
        self.device_name: str = ""
        self.rom_version: str = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._dlp_lock = threading.Lock()  # Serialize all DLP operations
        self._command_queue: list = []
        self._command_event = threading.Event()
        self._running = False

    def start(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
        """Start the background device thread."""
        self._loop = loop
        self._queue = queue
        self._running = True
        self._thread = threading.Thread(target=self._device_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the background thread."""
        self._running = False
        self._command_event.set()
        if self.conn:
            try:
                if self.dlp:
                    self.dlp.end_of_sync()
            except Exception:
                pass
            try:
                self.conn.close()
            except Exception:
                pass

    def _send_event(self, event: dict):
        """Send event to the async queue (thread-safe)."""
        if self._loop and self._queue:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def _device_loop(self):
        """Main device thread loop: poll → connect → serve commands."""
        while self._running:
            # Polling phase
            self.state = "waiting"
            self._send_event({"type": "status", "state": "waiting"})

            while self._running:
                import usb.core
                # Only try to open if device is on the bus
                dev = usb.core.find(idVendor=0x082D, idProduct=0x0100)
                if dev is None:
                    time.sleep(1)
                    continue
                try:
                    self.conn = Connection()
                    self.conn.open()
                    break
                except Exception:
                    self.conn = None
                    time.sleep(3)
                    continue

            if not self._running:
                break

            # CMP handshake
            try:
                slp = SLPSocket(self.conn)
                padp = PADPConnection(slp)

                cmp_data = padp.receive()
                if len(cmp_data) >= _CMP_SIZE:
                    _, _, ver_major, ver_minor, _, _ = struct.unpack(
                        _CMP_FORMAT, cmp_data[:_CMP_SIZE]
                    )
                    response = struct.pack(
                        _CMP_FORMAT,
                        CMP_TYPE_INIT, 0x00, ver_major, ver_minor, 0, 0,
                    )
                    padp.send(response)

                self._padp = padp
                self.dlp = DLPClient(padp)
                self.dlp.open_conduit()

                # Read device info
                info = self.dlp.read_sys_info()
                rom_major = (info.rom_version >> 24) & 0xFF
                rom_minor = (info.rom_version >> 20) & 0x0F
                self.rom_version = f"{rom_major}.{rom_minor}"
                self.device_name = info.name or self.conn._dev.product or "Unknown"

                self.state = "connected"
                self._send_event({"type": "status", "state": "connected"})
                self._send_event({
                    "type": "sysinfo",
                    "device": self.device_name,
                    "rom_version": self.rom_version,
                })

                # Auto-list databases on connect
                self._do_list()

            except Exception:
                self._cleanup()
                continue

            # Command serving phase — send tickles to keep device alive
            tickle_interval = 5
            last_tickle = time.time()
            while self._running and self.state == "connected":
                self._command_event.wait(timeout=1.0)
                self._command_event.clear()

                with self._lock:
                    commands = self._command_queue[:]
                    self._command_queue.clear()

                if commands:
                    for cmd in commands:
                        with self._dlp_lock:
                            last_tickle = time.time()
                            try:
                                self._handle_command(cmd)
                            except Exception as e:
                                logger.error(f"Command failed: {e}")
                                self._send_event({"type": "error", "message": str(e)})
                                self._cleanup()
                                break
                elif time.time() - last_tickle >= tickle_interval:
                    with self._dlp_lock:
                        try:
                            self._padp.send_tickle()
                            last_tickle = time.time()
                        except Exception:
                            self._cleanup()
                            break

        self._cleanup()

    def submit_command(self, cmd: dict):
        """Submit a command to the device thread (thread-safe)."""
        with self._lock:
            self._command_queue.append(cmd)
        self._command_event.set()

    def _handle_command(self, cmd: dict):
        """Execute a command on the device thread."""
        action = cmd.get("action")
        if action == "list" or action == "refresh":
            self._do_list()
        elif action == "delete":
            self._do_delete(cmd["name"])
        elif action == "push":
            self._do_push(cmd["data"], cmd["filename"])
        elif action == "backup":
            self._do_backup()
        elif action == "disconnect":
            try:
                self.dlp.end_of_sync()
            except Exception:
                pass
            self._cleanup()

    def _do_list(self):
        """List databases and send to clients."""
        databases = self.dlp.list_databases(ram=True, rom=False)
        db_list = []
        for db in databases:
            db_list.append({
                "name": db.name,
                "db_type": db.db_type,
                "creator": db.creator,
                "is_resource": bool(db.attributes & ATTR_RESOURCE),
            })
        self._send_event({"type": "db_list", "databases": db_list})

    def _do_delete(self, name: str):
        """Delete a database from the device."""
        self.dlp.delete_db(name)
        self._send_event({"type": "deleted", "name": name})
        self._do_list()

    def _do_push(self, file_data: bytes, filename: str):
        """Push a .pdb/.prc file to the device."""
        db = PalmDatabase.from_bytes(file_data)
        self._send_event({"type": "progress", "action": "push", "detail": f"Installing {db.name}..."})
        db.to_device(self.dlp)
        self._send_event({"type": "push_done", "name": db.name})
        self._do_list()

    def _do_backup(self):
        """Backup all RAM databases to a zip and store for download."""
        databases = self.dlp.list_databases(ram=True, rom=False)
        total = len(databases)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, db_info in enumerate(databases):
                self._send_event({
                    "type": "backup_progress",
                    "current": i + 1,
                    "total": total,
                    "name": db_info.name,
                })
                try:
                    db = PalmDatabase.from_device(
                        self.dlp, name=db_info.name,
                        db_type=db_info.db_type,
                        creator=db_info.creator,
                        attributes=db_info.attributes,
                    )
                    ext = ".prc" if db.is_resource_db else ".pdb"
                    zf.writestr(db_info.name + ext, db.to_bytes())
                except DLPException as e:
                    logger.warning(f"Skipping {db_info.name}: {e}")
        self._backup_data = buf.getvalue()
        self._send_event({"type": "backup_done"})

    def pull_database(self, name: str) -> tuple[bytes, str]:
        """Pull a database from device. Returns (file_bytes, extension)."""
        with self._dlp_lock:
            databases = self.dlp.list_databases(ram=True, rom=True)
            db_info = next((d for d in databases if d.name == name), None)
            if db_info is None:
                raise ValueError(f"Database '{name}' not found")
            db = PalmDatabase.from_device(
                self.dlp, name=name,
                db_type=db_info.db_type,
                creator=db_info.creator,
                attributes=db_info.attributes,
            )
            ext = ".prc" if db.is_resource_db else ".pdb"
            return db.to_bytes(), ext

    def _cleanup(self):
        """Clean up device connection without USB reset (avoids segfault)."""
        self.state = "disconnected"
        self._send_event({"type": "status", "state": "disconnected"})
        if self.conn:
            # Don't call conn.close() which does dev.reset() — just drop refs
            try:
                self.conn._dev = None
                self.conn._ep_in = None
                self.conn._ep_out = None
            except Exception:
                pass
        self.conn = None
        self.dlp = None
        self._padp = None


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(title="Palm-Com Dashboard")
device_manager = DeviceManager()
connected_websockets: list[WebSocket] = []
event_queue: asyncio.Queue = asyncio.Queue()

STATIC_DIR = Path(__file__).parent / "static"


@app.on_event("startup")
async def startup():
    loop = asyncio.get_event_loop()
    device_manager.start(loop, event_queue)
    asyncio.create_task(broadcast_events())


@app.on_event("shutdown")
async def shutdown():
    device_manager.stop()


async def broadcast_events():
    """Read events from device thread and broadcast to all WebSocket clients."""
    while True:
        event = await event_queue.get()
        message = json.dumps(event)
        disconnected = []
        for ws in connected_websockets:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            connected_websockets.remove(ws)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_websockets.append(ws)

    # Send current state
    await ws.send_text(json.dumps({
        "type": "status", "state": device_manager.state,
    }))
    if device_manager.state == "connected":
        await ws.send_text(json.dumps({
            "type": "sysinfo",
            "device": device_manager.device_name,
            "rom_version": device_manager.rom_version,
        }))
        # Trigger a fresh list
        device_manager.submit_command({"action": "list"})

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            action = msg.get("action")

            if action in ("list", "refresh", "backup", "disconnect"):
                device_manager.submit_command(msg)
            elif action == "delete":
                device_manager.submit_command(msg)
            else:
                await ws.send_text(json.dumps({
                    "type": "error",
                    "message": f"Unknown action: {action}",
                }))
    except WebSocketDisconnect:
        connected_websockets.remove(ws)


@app.get("/api/pull/{name:path}")
async def pull_database(name: str):
    """Download a database as a .pdb/.prc file."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        file_bytes, ext = await asyncio.get_event_loop().run_in_executor(
            None, device_manager.pull_database, name,
        )
        filename = name + ext
        return Response(
            content=file_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return Response(content=str(e), status_code=500)


@app.post("/api/push")
async def push_file(file: UploadFile = File(...)):
    """Upload and install a .pdb/.prc file."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    data = await file.read()
    try:
        PalmDatabase.from_bytes(data)  # Validate
    except Exception as e:
        return Response(content=f"Invalid file: {e}", status_code=400)
    device_manager.submit_command({
        "action": "push",
        "data": data,
        "filename": file.filename,
    })
    return {"status": "ok", "message": "Upload started"}


@app.get("/api/backup")
async def backup_all():
    """Download all RAM databases as a zip file."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    if hasattr(device_manager, "_backup_data") and device_manager._backup_data:
        data = device_manager._backup_data
        device_manager._backup_data = None
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="palm_backup.zip"'},
        )
    return Response(content="No backup available. Trigger backup first.", status_code=404)


from pydantic import BaseModel

class EditRequest(BaseModel):
    text: str

class TodoEditRequest(BaseModel):
    description: str
    priority: int = 1
    completed: bool = False
    note: str = ""
    due: str = ""  # "YYYY-MM-DD" or ""

@app.post("/api/edit/{name}/{index}")
async def edit_record(name: str, index: int, req: EditRequest):
    """Edit a memo record on the device."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_edit():
            from palm.dlp import DB_MODE_READ_WRITE, Record as DLPRecord
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    existing = dlp.read_record(handle, index)
                    new_data = req.text.encode("cp1252", errors="replace") + b"\x00"
                    dlp.write_record(handle, DLPRecord(
                        index=index,
                        attributes=existing.attributes & 0x0F,
                        unique_id=existing.unique_id,
                        data=new_data,
                    ))
                finally:
                    dlp.close_db(handle)

        await asyncio.get_event_loop().run_in_executor(None, _do_edit)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


def _pack_palm_date(date_str: str) -> bytes:
    """Pack a date string (YYYY-MM-DD) into 2-byte PalmOS date."""
    if not date_str:
        return struct.pack(">H", 0xFFFF)
    parts = date_str.split("-")
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    val = ((year - 1904) << 9) | (month << 5) | day
    return struct.pack(">H", val)


@app.post("/api/edit-todo/{name}/{index}")
async def edit_todo(name: str, index: int, req: TodoEditRequest):
    """Edit a todo record on the device."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_edit():
            from palm.dlp import DB_MODE_READ_WRITE, Record as DLPRecord
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    existing = dlp.read_record(handle, index)
                    # Build todo record: date(2) + priority(1) + desc\0 + note\0
                    data = _pack_palm_date(req.due)
                    data += bytes([req.priority])
                    data += req.description.encode("cp1252", errors="replace") + b"\x00"
                    data += req.note.encode("cp1252", errors="replace") + b"\x00"
                    # Set completed flag in attributes
                    attrs = existing.attributes & 0x0F  # keep category
                    if req.completed:
                        attrs |= 0x80
                    dlp.write_record(handle, DLPRecord(
                        index=index, attributes=attrs,
                        unique_id=existing.unique_id, data=data,
                    ))
                finally:
                    dlp.close_db(handle)

        await asyncio.get_event_loop().run_in_executor(None, _do_edit)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


class NewMemoRequest(BaseModel):
    text: str

@app.post("/api/new-record/{name}")
async def new_record(name: str, req: NewMemoRequest):
    """Create a new record in a database."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_create():
            from palm.dlp import DB_MODE_READ_WRITE, Record as DLPRecord
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    new_data = req.text.encode("cp1252", errors="replace") + b"\x00"
                    dlp.write_record(handle, DLPRecord(
                        index=0, attributes=0, unique_id=0, data=new_data,
                    ))
                finally:
                    dlp.close_db(handle)
        await asyncio.get_event_loop().run_in_executor(None, _do_create)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


@app.delete("/api/record/{name}/{index}")
async def delete_record(name: str, index: int):
    """Delete a record from a database by index."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_delete():
            from palm.dlp import DB_MODE_READ_WRITE, DLPFuncID, DLPArg
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    rec = dlp.read_record(handle, index)
                    # DeleteRecord: handle(1) + flags(1) + recID(4)
                    arg_data = struct.pack(">BBI", handle, 0, rec.unique_id)
                    dlp._execute(DLPFuncID.DELETE_RECORD, [DLPArg(arg_id=0x20, data=arg_data)])
                finally:
                    dlp.close_db(handle)
        await asyncio.get_event_loop().run_in_executor(None, _do_delete)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


@app.post("/api/move-record/{name}/{from_idx}/{to_idx}")
async def move_record(name: str, from_idx: int, to_idx: int):
    """Move a record by deleting and re-inserting."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_move():
            from palm.dlp import DB_MODE_READ_WRITE, Record as DLPRecord, DLPFuncID, DLPArg
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                # Read all records, reorder, rewrite
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    num = dlp.read_open_db_info(handle)
                    records = []
                    for i in range(num):
                        records.append(dlp.read_record(handle, i))
                    # Delete all
                    for rec in records:
                        arg_data = struct.pack(">BBI", handle, 0, rec.unique_id)
                        dlp._execute(DLPFuncID.DELETE_RECORD, [DLPArg(arg_id=0x20, data=arg_data)])
                    # Reorder
                    item = records.pop(from_idx)
                    records.insert(to_idx, item)
                    # Rewrite
                    for rec in records:
                        dlp.write_record(handle, DLPRecord(
                            index=0, attributes=rec.attributes & 0x0F,
                            unique_id=0, data=rec.data,
                        ))
                finally:
                    dlp.close_db(handle)
        await asyncio.get_event_loop().run_in_executor(None, _do_move)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


@app.get("/api/preview/{name}")
async def preview_database(name: str):
    """Return database content for preview (records as text, resources as list)."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        file_bytes, ext = await asyncio.get_event_loop().run_in_executor(
            None, device_manager.pull_database, name,
        )
        db = PalmDatabase.from_bytes(file_bytes)

        # TGL0 3D model
        if db.creator == "TGL0" and not db.is_resource_db:
            model = _parse_tgl0_model(db)
            return {"kind": "model3d", "name": db.name, "data": model}

        # Resource database (.prc) — show app info
        if db.is_resource_db:
            app_info = {
                "name": db.name,
                "type": db.db_type,
                "creator": db.creator,
                "version": None,
                "total_size": sum(len(r.data) for r in db.resources),
                "code_size": 0,
                "num_forms": 0,
                "num_bitmaps": 0,
                "num_strings": 0,
                "num_resources": len(db.resources),
            }
            resources = []
            for r in db.resources:
                rinfo = {"type": r.res_type, "id": r.res_id, "size": len(r.data)}
                # Extract version string
                if r.res_type == "tver":
                    try:
                        rinfo["text"] = r.data.rstrip(b"\x00").decode("cp1252")
                        app_info["version"] = rinfo["text"]
                    except Exception:
                        pass
                # Extract app name from tAIN
                if r.res_type == "tAIN":
                    try:
                        rinfo["text"] = r.data.rstrip(b"\x00").decode("cp1252")
                    except Exception:
                        pass
                # Categorize
                if r.res_type == "code":
                    app_info["code_size"] += len(r.data)
                elif r.res_type == "tFRM":
                    app_info["num_forms"] += 1
                elif r.res_type in ("Tbmp", "tAIB"):
                    app_info["num_bitmaps"] += 1
                elif r.res_type in ("tSTR", "tSTL"):
                    app_info["num_strings"] += 1
                resources.append(rinfo)
            # OnboardC project file
            if db.db_type.strip() == "Proj" and db.creator.strip() == "OnBD":
                return _preview_obpj(db)

            # For Rsrc-type databases (RsrcEdit files), parse known resource types
            if db.db_type.strip() == "Rsrc":
                return {"kind": "rsrc_edit", "name": db.name, "creator": db.creator,
                        "resources": _parse_rsrc_resources(db)}

            return {"kind": "app", "info": app_info, "resources": resources}

        # Format-aware record parsing for known database types
        creator = db.creator.strip()
        if creator == "memo":
            return _preview_memo(db)
        elif creator == "date":
            return _preview_datebook(db)
        elif creator == "todo":
            return _preview_todo(db)
        elif creator == "addr":
            return _preview_address(db)
        elif db.db_type.strip() == "TEXt" and creator == "REAd":
            return _preview_palmdoc(db)

        # Generic record database — try to show as text
        records = []
        for i, r in enumerate(db.records):
            try:
                text = r.data.rstrip(b"\x00").decode("cp1252")
                if all(c == '\x00' or c.isprintable() or c in '\n\r\t' for c in text):
                    records.append({"index": i, "text": text, "size": len(r.data)})
                else:
                    records.append({"index": i, "hex": r.data[:64].hex(), "size": len(r.data)})
            except Exception:
                records.append({"index": i, "hex": r.data[:64].hex(), "size": len(r.data)})
        return {"kind": "records", "name": db.name, "type": db.db_type,
                "creator": db.creator, "records": records}
    except Exception as e:
        return Response(content=str(e), status_code=500)


def _palm_date(raw: bytes, offset: int) -> str:
    """Parse a packed PalmOS date (2 bytes) into a string."""
    val = struct.unpack(">H", raw[offset:offset + 2])[0]
    if val == 0xFFFF or val == 0:
        return ""
    year = ((val >> 9) & 0x7F) + 1904
    month = (val >> 5) & 0x0F
    day = val & 0x1F
    return f"{year}-{month:02d}-{day:02d}"


def _palm_time(raw: bytes, offset: int) -> str:
    """Parse a PalmOS time (start/end as hour+minute bytes)."""
    hour = raw[offset]
    minute = raw[offset + 1]
    if hour == 0xFF:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _preview_memo(db) -> dict:
    """Parse MemoDB — records are null-terminated text, first line is title."""
    entries = []
    for i, r in enumerate(db.records):
        text = r.data.rstrip(b"\x00").decode("cp1252", errors="replace")
        lines = text.split("\n", 1)
        entries.append({
            "index": i,
            "title": lines[0] if lines else "",
            "body": lines[1].strip() if len(lines) > 1 else "",
        })
    return {"kind": "memos", "name": db.name, "entries": entries}


def _preview_datebook(db) -> dict:
    """Parse DatebookDB records.

    Record format:
    - Byte 0: start hour (0xFF = untimed)
    - Byte 1: start minute
    - Byte 2: end hour
    - Byte 3: end minute
    - Bytes 4-5: packed date (year/month/day)
    - Bytes 6-7: attributes/flags
    - Then optional description and note (null-separated strings)
    """
    entries = []
    for i, r in enumerate(db.records):
        d = r.data
        if len(d) < 8:
            continue
        start_time = _palm_time(d, 0)
        end_time = _palm_time(d, 2)
        date = _palm_date(d, 4)
        # Find description after the fixed header
        # Attributes byte 6 has flags for alarm, repeat, note, etc.
        attr = struct.unpack(">H", d[6:8])[0]
        has_alarm = bool(attr & 0x4000)
        has_repeat = bool(attr & 0x2000)
        has_note = bool(attr & 0x1000)
        has_exceptions = bool(attr & 0x0800)
        has_description = bool(attr & 0x0400)
        # Skip optional fields to find description
        offset = 8
        if has_alarm:
            offset += 2  # advance, units
        if has_repeat:
            offset += 8  # repeat info
        if has_exceptions:
            num_ex = struct.unpack(">H", d[offset:offset + 2])[0]
            offset += 2 + num_ex * 2
        description = ""
        note = ""
        if offset < len(d):
            rest = d[offset:]
            parts = rest.split(b"\x00")
            if len(parts) >= 1:
                description = parts[0].decode("cp1252", errors="replace")
            if has_note and len(parts) >= 2:
                note = parts[1].decode("cp1252", errors="replace")
        entry = {"date": date, "description": description}
        if start_time:
            entry["time"] = f"{start_time} - {end_time}"
        if note:
            entry["note"] = note
        entries.append(entry)
    return {"kind": "datebook", "name": db.name, "entries": entries}


def _preview_todo(db) -> dict:
    """Parse ToDoDB records.

    Record format:
    - Bytes 0-1: packed due date (0xFFFF = no date)
    - Byte 2: priority (1-5)
    - Then: null-terminated description, optional null-terminated note
    """
    entries = []
    for i, r in enumerate(db.records):
        d = r.data
        if len(d) < 3:
            continue
        due_date = _palm_date(d, 0)
        priority = d[2]
        rest = d[3:]
        parts = rest.split(b"\x00")
        description = parts[0].decode("cp1252", errors="replace") if parts else ""
        note = parts[1].decode("cp1252", errors="replace") if len(parts) > 1 else ""
        completed = bool(r.attributes & 0x80)
        entry = {
            "index": i,
            "description": description,
            "priority": priority,
            "completed": completed,
        }
        if due_date:
            entry["due"] = due_date
        if note:
            entry["note"] = note
        entries.append(entry)
    return {"kind": "todo", "name": db.name, "entries": entries}


def _preview_address(db) -> dict:
    """Parse AddressDB records.

    Record format:
    - Bytes 0-3: phone flags (which phone to show, phone label assignments)
    - Byte 4: field flags (which fields are present) — actually a bitmask
    - Then: null-separated field strings in order:
      Last, First, Company, Phone1-5, Address, City, State, Zip, Country, Title, Custom1-4, Note
    """
    entries = []
    field_names = ["Last", "First", "Company", "Phone1", "Phone2", "Phone3",
                   "Phone4", "Phone5", "Address", "City", "State", "Zip",
                   "Country", "Title", "Custom1", "Custom2", "Custom3", "Custom4", "Note"]
    for i, r in enumerate(db.records):
        d = r.data
        if len(d) < 9:
            continue
        # Skip phone flags (4 bytes) and field bitmask (4 bytes) + company offset (1 byte)
        offset = 9
        fields = d[offset:].split(b"\x00")
        entry = {}
        for j, val in enumerate(fields):
            if j < len(field_names) and val:
                text = val.decode("cp1252", errors="replace")
                if text:
                    entry[field_names[j]] = text
        if entry:
            entries.append(entry)
    return {"kind": "address", "name": db.name, "entries": entries}


def _preview_obpj(db) -> dict:
    """Parse OnboardC project file (OBPJ resource)."""
    r = db.resources[0]
    d = r.data
    version = struct.unpack(">H", d[0:2])[0]
    file_count = struct.unpack(">H", d[2:4])[0]
    flags = struct.unpack(">H", d[8:10])[0]
    creator = d[10:14].decode("ascii", errors="replace").rstrip("\x00")
    db_type = d[14:18].decode("ascii", errors="replace").rstrip("\x00")
    prc_name = d[18:50].split(b"\x00")[0].decode("cp1252", errors="replace")
    project_name = d[50:114].split(b"\x00")[0].decode("cp1252", errors="replace")

    # Find source/resource file references (null-terminated strings)
    # Look for .c, .Rsrc, .h files — skip .obj files (auto-generated)
    files = []
    seen = set()
    for pattern in [b".c\x00", b".Rsrc\x00", b".h\x00"]:
        pos = 110
        while True:
            idx = d.find(pattern, pos)
            if idx < 0:
                break
            start = idx
            while start > 0 and d[start - 1] >= 0x20:
                start -= 1
            fname = d[start:idx + len(pattern) - 1].decode("cp1252", errors="replace")
            if fname and fname not in seen:
                files.append(fname)
                seen.add(fname)
            pos = idx + len(pattern)

    return {
        "kind": "obpj",
        "name": db.name,
        "project_name": project_name,
        "prc_name": prc_name,
        "creator": creator,
        "type": db_type,
        "version": version,
        "file_count": file_count,
        "execute": bool(flags & 0x0001),
        "always_rebuild": bool(flags & 0x0002),
        "debug": bool(flags & 0x0004),
        "auto_version": bool(flags & 0x0008),
        "flags_raw": flags,
        "files": files,
    }


def _parse_rsrc_resources(db) -> list:
    """Parse known PalmOS resource types for display."""
    results = []
    for r in db.resources:
        info = {"type": r.res_type, "id": r.res_id, "size": len(r.data)}
        if r.res_type == "Talt":
            info.update(_parse_talt(r.data))
        elif r.res_type == "tFRM":
            info.update(_parse_tfrm(r.data))
        elif r.res_type == "MBAR":
            info.update(_parse_mbar(r.data))
        else:
            info["hex"] = r.data[:64].hex()
        results.append(info)
    return results


def _parse_talt(data: bytes) -> dict:
    """Parse Talt (Alert) resource."""
    alert_types = ["Information", "Confirmation", "Warning", "Error"]
    alert_type = struct.unpack(">H", data[0:2])[0]
    help_id = struct.unpack(">H", data[2:4])[0]
    num_buttons = struct.unpack(">H", data[4:6])[0]
    default_btn = struct.unpack(">H", data[6:8])[0]
    strings = data[8:].split(b"\x00")
    title = strings[0].decode("cp1252", errors="replace") if len(strings) > 0 else ""
    message = strings[1].decode("cp1252", errors="replace") if len(strings) > 1 else ""
    buttons = []
    for s in strings[2:]:
        if s:
            buttons.append(s.decode("cp1252", errors="replace"))
    return {
        "parsed": "alert",
        "alert_type": alert_types[alert_type] if alert_type < 4 else str(alert_type),
        "alert_type_id": alert_type,
        "title": title,
        "message": message,
        "buttons": buttons,
    }


def _parse_tfrm(data: bytes) -> dict:
    """Parse tFRM (Form) resource — extract basic info."""
    # Form bounds
    x, y = struct.unpack(">HH", data[0:4])
    w, h = struct.unpack(">HH", data[4:8])
    # Find title string (after fixed header, usually near end before padding)
    # Look for printable ASCII strings
    title = ""
    # Search for the form title - it's a null-terminated string in the data
    for i in range(40, len(data)):
        if data[i:i+1].isalpha():
            end = data.index(b"\x00", i) if b"\x00" in data[i:] else len(data)
            candidate = data[i:end].decode("cp1252", errors="replace")
            if len(candidate) > 1 and all(c.isprintable() for c in candidate):
                title = candidate
                break
    return {
        "parsed": "form",
        "bounds": f"({x},{y}) {w}x{h}",
        "title": title,
    }


def _parse_mbar(data: bytes) -> dict:
    """Parse MBAR (Menu Bar) resource — extract menu titles and items."""
    # Find readable strings in the data
    menus = []
    parts = data.split(b"\x00")
    for p in parts:
        try:
            text = p.decode("cp1252").strip()
            if text and len(text) > 1 and all(c.isprintable() for c in text) and not all(c == 'i' for c in text):
                menus.append(text)
        except Exception:
            pass
    return {"parsed": "menubar", "items": menus}


class ObpjEditRequest(BaseModel):
    prc_name: str
    type: str
    creator: str
    execute: bool = False
    always_rebuild: bool = False
    debug: bool = False
    auto_version: bool = False


@app.post("/api/edit-obpj/{name}")
async def edit_obpj(name: str, req: ObpjEditRequest):
    """Edit OnboardC project settings on the device."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_edit():
            from palm.dlp import DB_MODE_READ_WRITE, Resource as DLPResource, DLPFuncID, DLPArg
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    res = dlp.read_resource(handle, 0)
                    d = bytearray(res.data)

                    # Update flags at offset 8-9
                    flags = 0
                    if req.execute: flags |= 0x0001
                    if req.always_rebuild: flags |= 0x0002
                    if req.debug: flags |= 0x0004
                    if req.auto_version: flags |= 0x0008
                    struct.pack_into(">H", d, 8, flags)

                    # Update creator at offset 10-13
                    creator_b = req.creator.encode("ascii")[:4].ljust(4, b"\x00")
                    d[10:14] = creator_b

                    # Update type at offset 14-17
                    type_b = req.type.encode("ascii")[:4].ljust(4, b"\x00")
                    d[14:18] = type_b

                    # Update PRC name at offset 18-49 (32 bytes, null-padded)
                    prc_b = req.prc_name.encode("cp1252", errors="replace")[:31]
                    d[18:50] = prc_b.ljust(32, b"\x00")

                    # Delete old resource and write new
                    arg_data = struct.pack(">BB", handle, 0)
                    arg_data += b"OBPJ"
                    arg_data += struct.pack(">H", res.res_id)
                    dlp._execute(DLPFuncID.DELETE_RESOURCE, [DLPArg(arg_id=0x20, data=arg_data)])

                    dlp.write_resource(handle, DLPResource(
                        res_type="OBPJ", res_id=res.res_id, index=0, data=bytes(d),
                    ))
                finally:
                    dlp.close_db(handle)
        await asyncio.get_event_loop().run_in_executor(None, _do_edit)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


class ObpjFileRequest(BaseModel):
    action: str  # "add" or "remove"
    filename: str  # e.g. "Test2.3.c"


@app.post("/api/edit-obpj-files/{name}")
async def edit_obpj_files(name: str, req: ObpjFileRequest):
    """Add or remove a file from an OnboardC project."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_edit():
            from palm.dlp import DB_MODE_READ_WRITE, Resource as DLPResource, DLPFuncID, DLPArg
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    res = dlp.read_resource(handle, 0)
                    d = bytearray(res.data)
                    file_count = struct.unpack(">H", d[2:4])[0]

                    if req.action == "remove":
                        # Find the last occurrence of the filename
                        fname_bytes = req.filename.encode("cp1252") + b"\x00"
                        idx = d.rfind(fname_bytes)
                        if idx < 0:
                            raise ValueError(f"File '{req.filename}' not found in project")
                        # Find the start of this entry by scanning back for previous null
                        # The entry starts at the filename
                        entry_start = idx
                        # Truncate everything from this entry onwards
                        d = d[:entry_start]
                        # Pad with zeros to ensure clean termination
                        d += b"\x00" * 6
                        # Decrement file count
                        struct.pack_into(">H", d, 2, file_count - 1)

                    elif req.action == "add":
                        if not req.filename.endswith(".c"):
                            raise ValueError("Only .c files can be added")
                        # Build a minimal entry: filename + zeroed metadata + .obj name
                        fname_bytes = req.filename.encode("cp1252") + b"\x00"
                        obj_name = req.filename.replace(".c", ".obj")
                        # Minimal metadata template (97 bytes total after filename)
                        # Zeroed compiler state + obj filename + padding
                        meta = b"\x00" * 68  # zeroed compiler state
                        meta += obj_name.encode("cp1252") + b"\x00"
                        # Pad to reasonable size
                        meta = meta.ljust(97, b"\x00")
                        # Strip trailing zeros from current data to find append point
                        while len(d) > 114 and d[-1] == 0 and d[-2] == 0:
                            d = d[:-1]
                        d += b"\x00"  # separator
                        d += fname_bytes + meta
                        # Increment file count
                        struct.pack_into(">H", d, 2, file_count + 1)

                    # Delete old resource and write new
                    arg_data = struct.pack(">BB", handle, 0)
                    arg_data += b"OBPJ"
                    arg_data += struct.pack(">H", res.res_id)
                    dlp._execute(DLPFuncID.DELETE_RESOURCE, [DLPArg(arg_id=0x20, data=arg_data)])
                    dlp.write_resource(handle, DLPResource(
                        res_type="OBPJ", res_id=res.res_id, index=0, data=bytes(d),
                    ))
                finally:
                    dlp.close_db(handle)
        await asyncio.get_event_loop().run_in_executor(None, _do_edit)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


class TaltEditRequest(BaseModel):
    title: str
    message: str
    buttons: list[str]
    alert_type_id: int = 0


@app.post("/api/edit-talt/{name}/{res_id}")
async def edit_talt(name: str, res_id: int, req: TaltEditRequest):
    """Edit a Talt (Alert) resource on the device."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_edit():
            from palm.dlp import DB_MODE_READ_WRITE, Resource as DLPResource
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    # Build new Talt data
                    data = struct.pack(">HHHH",
                        req.alert_type_id, 0,
                        len(req.buttons), 0)
                    data += req.title.encode("cp1252", errors="replace") + b"\x00"
                    data += req.message.encode("cp1252", errors="replace") + b"\x00"
                    for btn in req.buttons:
                        data += btn.encode("cp1252", errors="replace") + b"\x00"
                    # Find and delete old resource, write new
                    num = dlp.read_open_db_info(handle)
                    for i in range(num):
                        res = dlp.read_resource(handle, i)
                        if res.res_type.strip() == "Talt" and res.res_id == res_id:
                            from palm.dlp import DLPFuncID, DLPArg
                            arg_data = struct.pack(">BB", handle, 0)
                            arg_data += "Talt".encode("ascii")
                            arg_data += struct.pack(">H", res_id)
                            dlp._execute(DLPFuncID.DELETE_RESOURCE, [DLPArg(arg_id=0x20, data=arg_data)])
                            break
                    dlp.write_resource(handle, DLPResource(
                        res_type="Talt", res_id=res_id, index=0, data=data,
                    ))
                finally:
                    dlp.close_db(handle)
        await asyncio.get_event_loop().run_in_executor(None, _do_edit)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


def _palmdoc_decompress(data: bytes) -> bytes:
    """Decompress PalmDoc LZ77 compressed text."""
    out = bytearray()
    i = 0
    while i < len(data):
        c = data[i]; i += 1
        if c == 0:
            out.append(c)
        elif 1 <= c <= 8:
            out.extend(data[i:i + c]); i += c
        elif c < 0x80:
            out.append(c)
        elif c >= 0xC0:
            out.append(0x20); out.append(c ^ 0x80)
        else:
            if i < len(data):
                n = data[i]; i += 1
                dist = ((c << 8) | n) >> 3 & 0x7FF
                count = (n & 7) + 3
                for _ in range(count):
                    out.append(out[-dist])
    return bytes(out)


def _preview_palmdoc(db) -> dict:
    """Parse PalmDoc (TEXt/REAd) database — used by OnboardC for source files."""
    if not db.records:
        return {"kind": "palmdoc", "name": db.name, "text": "", "language": ""}

    # Record 0: header
    h = db.records[0].data
    version = struct.unpack(">H", h[0:2])[0]
    text_length = struct.unpack(">I", h[4:8])[0]

    # Records 1+: text data
    text_parts = []
    for r in db.records[1:]:
        if version == 2:
            text_parts.append(_palmdoc_decompress(r.data))
        else:
            text_parts.append(r.data)
    raw = b"".join(text_parts)[:text_length]
    text = raw.decode("cp1252", errors="replace")

    # Detect language from filename
    name = db.name
    language = ""
    if name.endswith(".c") or name.endswith(".h"):
        language = "c"
    elif name.endswith(".py"):
        language = "python"

    return {"kind": "palmdoc", "name": db.name, "text": text, "language": language}


@app.post("/api/edit-palmdoc/{name}")
async def edit_palmdoc(name: str, req: EditRequest):
    """Edit a PalmDoc text database on the device."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        def _do_edit():
            from palm.dlp import DB_MODE_READ_WRITE, Record as DLPRecord
            with device_manager._dlp_lock:
                dlp = device_manager.dlp
                handle = dlp.open_db(name, DB_MODE_READ_WRITE)
                try:
                    # Read existing header record
                    header_rec = dlp.read_record(handle, 0)
                    num_recs = dlp.read_open_db_info(handle)

                    # Delete old text records (keep header at index 0)
                    from palm.dlp import DLPFuncID, DLPArg
                    for i in range(1, num_recs):
                        rec = dlp.read_record(handle, 1)  # always index 1 since we keep deleting
                        arg_data = struct.pack(">BBI", handle, 0, rec.unique_id)
                        dlp._execute(DLPFuncID.DELETE_RECORD, [DLPArg(arg_id=0x20, data=arg_data)])

                    # Encode new text
                    text_data = req.text.encode("cp1252", errors="replace")
                    rec_size = 4096

                    # Update header: version=1, length, num_recs, rec_size
                    num_text_recs = max(1, (len(text_data) + rec_size - 1) // rec_size)
                    new_header = struct.pack(">HHIHHI",
                        1, 0, len(text_data), num_text_recs, rec_size, 0)
                    # Pad header to original size
                    new_header = new_header.ljust(len(header_rec.data), b"\x00")
                    dlp.write_record(handle, DLPRecord(
                        index=0, attributes=header_rec.attributes & 0x0F,
                        unique_id=header_rec.unique_id, data=new_header,
                    ))

                    # Write text records
                    for i in range(num_text_recs):
                        chunk = text_data[i * rec_size:(i + 1) * rec_size]
                        dlp.write_record(handle, DLPRecord(
                            index=0, attributes=0, unique_id=0, data=chunk,
                        ))
                finally:
                    dlp.close_db(handle)

        await asyncio.get_event_loop().run_in_executor(None, _do_edit)
        return {"status": "ok"}
    except Exception as e:
        return Response(content=str(e), status_code=500)


@app.get("/api/model/{name}")
async def get_model(name: str):
    """Parse a TGL0 database and return 3D model data as JSON."""
    if device_manager.state != "connected":
        return Response(content="Device not connected", status_code=503)
    try:
        file_bytes, ext = await asyncio.get_event_loop().run_in_executor(
            None, device_manager.pull_database, name,
        )
        db = PalmDatabase.from_bytes(file_bytes)
        if db.creator != "TGL0":
            return Response(content="Not a tinyGL model", status_code=400)
        model = _parse_tgl0_model(db)
        return model
    except Exception as e:
        return Response(content=str(e), status_code=500)


def _parse_tgl0_model(db: PalmDatabase) -> dict:
    """Parse a TGL0 PDB into vertices and triangle strips."""
    if len(db.records) < 3:
        raise ValueError("Not enough records for a 3D model")

    # Record 0: header (version, num_vertices, num_strips)
    r0 = db.records[0].data
    version, num_verts, num_strips = struct.unpack(">HHH", r0[:6])

    # Record 1: vertices (16 bytes each: X, Y, Z, W as 16.16 fixed point)
    r1 = db.records[1].data
    vertices = []
    for i in range(num_verts):
        off = i * 16
        x, y, z, w = struct.unpack(">iiii", r1[off:off + 16])
        vertices.append([x / 65536.0, y / 65536.0, z / 65536.0])

    # Records 2+: triangle strips
    # First 2 uint16 values are metadata (flags/color), vertex indices start at offset 2
    strips = []
    for i in range(2, 2 + num_strips):
        if i >= len(db.records):
            break
        r = db.records[i].data
        all_values = []
        for j in range(len(r) // 2):
            all_values.append(struct.unpack(">H", r[j * 2:j * 2 + 2])[0])
        # Skip first 2 values (metadata), rest are vertex indices
        indices = all_values[2:]
        strips.append(indices)

    # Convert triangle strips to triangle list for Three.js
    triangles = []
    for strip in strips:
        for j in range(len(strip) - 2):
            a, b, c = strip[j], strip[j + 1], strip[j + 2]
            # Skip degenerate triangles
            if a == b or b == c or a == c:
                continue
            if j % 2 == 0:
                triangles.append([a, b, c])
            else:
                triangles.append([b, a, c])

    return {
        "name": db.name,
        "version": version,
        "vertices": vertices,
        "triangles": triangles,
        "strips": strips,
        "num_vertices": num_verts,
        "num_strips": num_strips,
    }


def run(port: int = 8000):
    """Run the web dashboard server."""
    import socket
    import subprocess
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    # Find an available port
    for p in range(port, port + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", p)) != 0:
                port = p
                break

    url = f"http://localhost:{port}"
    print(f"Palm-Com Dashboard: {url}")
    print("Press HotSync on your Visor to connect.")
    subprocess.Popen(["open", url])
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    run()
