# Palm-Com Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web dashboard at `http://localhost:8000` that provides real-time control of a Handspring Visor over USB with persistent connection.

**Architecture:** FastAPI server with a background device thread for blocking USB I/O, WebSocket for real-time status/commands, REST endpoints for file transfers. Single HTML file with vanilla JS for the dashboard UI.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, existing palm protocol stack

---

## File Structure

```
palm_com/
├── web/
│   ├── __init__.py        # Package init
│   ├── server.py          # FastAPI app, DeviceManager, WebSocket, REST
│   └── static/
│       └── index.html     # Single-page dashboard
├── requirements.txt       # Updated with fastapi, uvicorn
└── cli.py                 # Add `palm web` command
```

---

### Task 1: Dependencies and Scaffolding

**Files:**
- Create: `web/__init__.py`
- Create: `web/static/` (directory)
- Modify: `requirements.txt`

- [ ] **Step 1: Create web package directory**

```bash
mkdir -p web/static
```

- [ ] **Step 2: Create `web/__init__.py`**

```python
"""Palm-Com web dashboard."""
```

- [ ] **Step 3: Update `requirements.txt`**

Add to existing file:

```
fastapi>=0.104.0
uvicorn>=0.24.0
```

- [ ] **Step 4: Install dependencies**

Run: `pip install fastapi uvicorn`
Expected: Installs successfully.

- [ ] **Step 5: Verify imports work**

Run: `python -c "import fastapi; import uvicorn; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add web/__init__.py requirements.txt
git commit -m "feat: scaffold web dashboard package and add dependencies"
```

---

### Task 2: DeviceManager

**Files:**
- Create: `web/server.py`

The DeviceManager handles the device connection lifecycle in a background thread. It polls for the device, performs the CMP handshake, and executes DLP commands.

- [ ] **Step 1: Create `web/server.py` with DeviceManager**

```python
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


def run():
    """Run the web dashboard server."""
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    print("Palm-Com Dashboard: http://localhost:8000")
    print("Press HotSync on your Visor to connect.")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Verify server starts (no device needed)**

Run: `python -c "from web.server import app; print('Server module OK')"`
Expected: `Server module OK`

- [ ] **Step 3: Commit**

```bash
git add web/server.py
git commit -m "feat: implement web server with DeviceManager, WebSocket, and REST endpoints"
```

---

### Task 3: Dashboard HTML/CSS/JS

**Files:**
- Create: `web/static/index.html`

- [ ] **Step 1: Create the dashboard page**

Create `web/static/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Palm-Com Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f5; color: #333; }

  header { background: #2c3e50; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 20px; font-weight: 600; }
  .status { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; }
  .status-dot.connected { background: #2ecc71; }
  .status-dot.waiting { background: #f1c40f; }
  .status-dot.disconnected { background: #e74c3c; }

  .container { max-width: 960px; margin: 0 auto; padding: 24px; }

  .device-info { background: white; border-radius: 8px; padding: 16px 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .device-info h2 { font-size: 16px; color: #666; margin-bottom: 4px; }
  .device-info .details { font-size: 18px; }

  .section { background: white; border-radius: 8px; padding: 16px 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .section-header h2 { font-size: 16px; }
  .section-header .actions { display: flex; gap: 8px; }

  button { background: #3498db; color: white; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  button:hover { background: #2980b9; }
  button:disabled { background: #bdc3c7; cursor: not-allowed; }
  button.danger { background: #e74c3c; }
  button.danger:hover { background: #c0392b; }
  button.secondary { background: #95a5a6; }
  button.secondary:hover { background: #7f8c8d; }

  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; padding: 8px; border-bottom: 2px solid #eee; color: #666; font-weight: 600; }
  td { padding: 8px; border-bottom: 1px solid #eee; }
  tr:hover td { background: #f8f9fa; }
  .actions-cell { white-space: nowrap; }
  .actions-cell button { padding: 3px 8px; font-size: 12px; margin-left: 4px; }

  .drop-zone { border: 2px dashed #bdc3c7; border-radius: 8px; padding: 32px; text-align: center; color: #999; cursor: pointer; transition: all 0.2s; }
  .drop-zone:hover, .drop-zone.dragover { border-color: #3498db; background: #ebf5fb; color: #3498db; }
  .drop-zone input { display: none; }

  .message { padding: 12px 24px; text-align: center; color: #999; font-size: 14px; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #2c3e50; color: white; padding: 12px 20px; border-radius: 6px; font-size: 14px; opacity: 0; transition: opacity 0.3s; z-index: 100; }
  .toast.show { opacity: 1; }
  .toast.error { background: #e74c3c; }

  .progress-bar { background: #ecf0f1; border-radius: 4px; height: 6px; margin-top: 8px; overflow: hidden; display: none; }
  .progress-bar .fill { background: #3498db; height: 100%; width: 0%; transition: width 0.3s; }
</style>
</head>
<body>

<header>
  <h1>Palm-Com Dashboard</h1>
  <div class="status">
    <span class="status-dot disconnected" id="statusDot"></span>
    <span id="statusText">Disconnected</span>
  </div>
</header>

<div class="container">
  <div class="device-info" id="deviceInfo" style="display:none">
    <h2>Device</h2>
    <div class="details">
      <span id="deviceName">—</span> &nbsp;·&nbsp; ROM <span id="romVersion">—</span>
    </div>
  </div>

  <div class="section">
    <div class="section-header">
      <h2>Databases <span id="dbCount"></span></h2>
      <div class="actions">
        <button id="btnRefresh" onclick="send({action:'refresh'})" disabled>Refresh</button>
        <button id="btnBackup" onclick="startBackup()" disabled>Backup All</button>
      </div>
    </div>
    <div class="progress-bar" id="progressBar"><div class="fill" id="progressFill"></div></div>
    <table id="dbTable" style="display:none">
      <thead>
        <tr><th>Name</th><th>Type</th><th>Creator</th><th>Actions</th></tr>
      </thead>
      <tbody id="dbBody"></tbody>
    </table>
    <div class="message" id="dbMessage">Press HotSync button to connect...</div>
  </div>

  <div class="section">
    <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()">
      Drop .prc / .pdb files here to install<br><small>or click to browse</small>
      <input type="file" id="fileInput" accept=".prc,.pdb,.PRC,.PDB" multiple>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let ws;
let isConnected = false;

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    handle(msg);
  };

  ws.onclose = () => {
    setTimeout(connect, 1000);
  };
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

function handle(msg) {
  switch (msg.type) {
    case 'status':
      setStatus(msg.state);
      break;
    case 'sysinfo':
      document.getElementById('deviceName').textContent = msg.device;
      document.getElementById('romVersion').textContent = msg.rom_version;
      document.getElementById('deviceInfo').style.display = '';
      break;
    case 'db_list':
      renderDatabases(msg.databases);
      break;
    case 'deleted':
      toast(`Deleted ${msg.name}`);
      break;
    case 'push_done':
      toast(`Installed ${msg.name}`);
      break;
    case 'progress':
      toast(msg.detail);
      break;
    case 'backup_progress':
      showProgress(msg.current, msg.total, `Backing up ${msg.name}...`);
      break;
    case 'backup_done':
      hideProgress();
      toast('Backup complete — downloading...');
      window.location = '/api/backup';
      break;
    case 'error':
      toast(msg.message, true);
      break;
  }
}

function setStatus(state) {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  dot.className = 'status-dot ' + state;
  const labels = { connected: 'Connected', waiting: 'Waiting for HotSync...', disconnected: 'Disconnected' };
  text.textContent = labels[state] || state;

  isConnected = state === 'connected';
  document.getElementById('btnRefresh').disabled = !isConnected;
  document.getElementById('btnBackup').disabled = !isConnected;

  if (state === 'waiting') {
    document.getElementById('dbMessage').textContent = 'Press HotSync button to connect...';
    document.getElementById('dbMessage').style.display = '';
    document.getElementById('dbTable').style.display = 'none';
    document.getElementById('deviceInfo').style.display = 'none';
  } else if (state === 'disconnected') {
    document.getElementById('dbMessage').textContent = 'Device disconnected';
    document.getElementById('dbMessage').style.display = '';
    document.getElementById('dbTable').style.display = 'none';
    document.getElementById('deviceInfo').style.display = 'none';
  }
}

function renderDatabases(dbs) {
  const body = document.getElementById('dbBody');
  body.innerHTML = '';
  dbs.forEach(db => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${esc(db.name)}</td>
      <td>${esc(db.db_type)}</td>
      <td>${esc(db.creator)}</td>
      <td class="actions-cell">
        <button onclick="pullDb('${esc(db.name)}')">Download</button>
        <button class="danger" onclick="deleteDb('${esc(db.name)}')">Delete</button>
      </td>`;
    body.appendChild(tr);
  });
  document.getElementById('dbCount').textContent = `(${dbs.length})`;
  document.getElementById('dbTable').style.display = '';
  document.getElementById('dbMessage').style.display = 'none';
}

function pullDb(name) {
  window.location = '/api/pull/' + encodeURIComponent(name);
}

function deleteDb(name) {
  if (confirm(`Delete "${name}" from device?`)) {
    send({ action: 'delete', name: name });
  }
}

function startBackup() {
  send({ action: 'backup' });
  showProgress(0, 1, 'Starting backup...');
}

function showProgress(current, total, label) {
  const bar = document.getElementById('progressBar');
  const fill = document.getElementById('progressFill');
  bar.style.display = '';
  fill.style.width = (current / total * 100) + '%';
}

function hideProgress() {
  document.getElementById('progressBar').style.display = 'none';
  document.getElementById('progressFill').style.width = '0%';
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

let toastTimer;
function toast(msg, isError) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.className = 'toast', 3000);
}

// File upload
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');

dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  uploadFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', () => {
  uploadFiles(fileInput.files);
  fileInput.value = '';
});

function uploadFiles(files) {
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    toast(`Uploading ${file.name}...`);
    fetch('/api/push', { method: 'POST', body: form })
      .then(r => { if (!r.ok) return r.text().then(t => { throw new Error(t); }); })
      .catch(e => toast(e.message, true));
  }
}

connect();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify static file is served**

Run: `python -c "from web.server import app, STATIC_DIR; print(STATIC_DIR); print((STATIC_DIR / 'index.html').exists())"`
Expected: Path to static dir, `True`

- [ ] **Step 3: Commit**

```bash
git add web/static/index.html
git commit -m "feat: implement dashboard UI with WebSocket, file upload, and backup"
```

---

### Task 4: CLI Integration and Startup

**Files:**
- Modify: `cli.py`

- [ ] **Step 1: Add `palm web` command to CLI**

Add to `cli.py` after the existing commands:

```python
@cli.command()
@click.option("--port", default=8000, help="Port to serve on")
def web(port):
    """Launch web dashboard."""
    from web.server import run
    run()
```

- [ ] **Step 2: Add `__main__.py` for `python -m web.server`**

Create `web/__main__.py`:

```python
from web.server import run
run()
```

- [ ] **Step 3: Verify both entry points work**

Run: `python cli.py web --help`
Expected: Shows help for web command.

Run: `python -m web.server &` then `curl -s http://localhost:8000 | head -5` then kill the server.
Expected: Returns HTML.

- [ ] **Step 4: Commit**

```bash
git add cli.py web/__main__.py
git commit -m "feat: add 'palm web' CLI command and python -m web.server entry point"
```

---

### Task 5: Integration Test with Device

**Files:** None — manual testing.

- [ ] **Step 1: Start the web server**

Run: `python cli.py web`
Expected: Prints `Palm-Com Dashboard: http://localhost:8000` and `Press HotSync on your Visor to connect.`

- [ ] **Step 2: Open dashboard in browser**

Open `http://localhost:8000`
Expected: Dashboard shows with yellow "Waiting for HotSync..." status.

- [ ] **Step 3: Press HotSync on Visor**

Expected: Status turns green "Connected", device info appears, database list populates.

- [ ] **Step 4: Test download**

Click "Download" on MemoDB.
Expected: Browser downloads `MemoDB.pdb` file.

- [ ] **Step 5: Test delete**

Click "Delete" on a test database (e.g. TestRes if present).
Expected: Confirmation dialog, then row disappears from table.

- [ ] **Step 6: Test upload**

Drag a .prc file onto the drop zone.
Expected: Toast shows "Uploading...", then "Installed", database list refreshes.

- [ ] **Step 7: Test backup**

Click "Backup All".
Expected: Progress bar advances, then browser downloads `palm_backup.zip`.

- [ ] **Step 8: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration adjustments for web dashboard"
```
