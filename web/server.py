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
        self.device_name: str = ""
        self.rom_version: str = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
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
                try:
                    self.conn = Connection()
                    self.conn.open()
                    break
                except ConnectionError:
                    time.sleep(1)
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

            except Exception as e:
                logger.error(f"Connection failed: {e}")
                self._send_event({"type": "error", "message": str(e)})
                self._cleanup()
                continue

            # Command serving phase
            while self._running and self.state == "connected":
                self._command_event.wait(timeout=1.0)
                self._command_event.clear()

                with self._lock:
                    commands = self._command_queue[:]
                    self._command_queue.clear()

                for cmd in commands:
                    try:
                        self._handle_command(cmd)
                    except Exception as e:
                        logger.error(f"Command failed: {e}")
                        self._send_event({"type": "error", "message": str(e)})
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
        with self._lock:
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
        """Clean up device connection."""
        self.state = "disconnected"
        self._send_event({"type": "status", "state": "disconnected"})
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        self.dlp = None


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

            if action in ("list", "refresh", "backup"):
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
