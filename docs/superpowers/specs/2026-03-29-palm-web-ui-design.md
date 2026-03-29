# Palm-Com Web Dashboard — Design Spec

## Overview

A web-based dashboard for the palm-com tool. Serves a single-page app at `http://localhost:8000` that communicates with the Handspring Visor over USB via the existing protocol stack. Uses WebSocket for real-time device status and command execution, REST endpoints for file upload/download.

## Architecture

- **FastAPI** web server with native WebSocket support
- **Single HTML file** with vanilla JS and CSS — no build step
- **Persistent device connection** — press HotSync once, stay connected for multiple operations
- **Background device thread** — USB I/O is blocking, runs in a separate thread bridged to async via `asyncio.Queue`

## File Structure

```
palm_com/
├── web/
│   ├── __init__.py
│   ├── server.py       # FastAPI app, WebSocket handler, REST endpoints
│   └── static/
│       └── index.html  # Single-page dashboard (HTML + CSS + JS)
├── palm/               # Existing protocol stack (unchanged)
└── cli.py              # Existing CLI (unchanged)
```

## Dependencies

- `fastapi` — web framework
- `uvicorn` — ASGI server
- `websockets` — WebSocket support (FastAPI dependency)

## Server (`web/server.py`)

### Device Manager

A singleton class that manages the device connection lifecycle:

```python
class DeviceManager:
    state: str  # "disconnected", "waiting", "connected"
    dlp: DLPClient | None
    conn: Connection | None
```

- **Polling loop:** Runs in a background thread. When state is "waiting", polls for USB device every 1 second. On detection, opens connection, performs CMP handshake, opens conduit. Sets state to "connected" and sends status to all WebSocket clients.
- **Command execution:** When a WebSocket client sends a command, the device manager runs the corresponding DLP operation on the device thread and returns results via the async queue.
- **Disconnection:** When USB I/O fails or EndOfSync is called, sets state to "disconnected" and notifies clients. Automatically returns to "waiting" state to detect next HotSync.

### WebSocket Protocol

All messages are JSON.

**Server → Client:**

| Message | Payload |
|---------|---------|
| `status` | `{ "type": "status", "state": "waiting" \| "connected" \| "disconnected" }` |
| `sysinfo` | `{ "type": "sysinfo", "device": "Handspring Visor", "rom_version": "3.1" }` |
| `db_list` | `{ "type": "db_list", "databases": [{ "name", "db_type", "creator", "flags", "is_resource" }] }` |
| `progress` | `{ "type": "progress", "action": "push", "detail": "Writing resource 5/22..." }` |
| `error` | `{ "type": "error", "message": "..." }` |
| `deleted` | `{ "type": "deleted", "name": "..." }` |
| `push_done` | `{ "type": "push_done", "name": "..." }` |
| `backup_progress` | `{ "type": "backup_progress", "current": 3, "total": 15, "name": "MemoDB" }` |

**Client → Server:**

| Message | Payload |
|---------|---------|
| `list` | `{ "action": "list" }` |
| `delete` | `{ "action": "delete", "name": "..." }` |
| `refresh` | `{ "action": "refresh" }` |
| `backup` | `{ "action": "backup" }` |

### REST Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serve `index.html` |
| `/api/pull/{name}` | GET | Download database as .pdb/.prc file |
| `/api/push` | POST | Upload .pdb/.prc file (multipart form) |
| `/api/backup` | GET | Download all RAM databases as .zip |

### Threading Model

```
Main thread (asyncio):
  ├── FastAPI/uvicorn event loop
  ├── WebSocket connections
  └── Reads from async Queue for device events

Device thread (blocking):
  ├── USB polling loop (when disconnected)
  ├── CMP handshake + open conduit (on connect)
  ├── DLP command execution (on request)
  └── Writes to async Queue for results
```

Communication between threads uses `asyncio.Queue` (device thread calls `loop.call_soon_threadsafe` to put items on the queue).

## Dashboard UI (`web/static/index.html`)

### Layout

```
+----------------------------------------------------------+
|  Palm-Com Dashboard          [status indicator: ● Connected] |
+----------------------------------------------------------+
|                                                          |
|  Device: Handspring Visor    ROM: 3.1                    |
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  Databases (15)                          [Refresh] [Backup All] |
|  +------------------------------------------------------+|
|  | Name              | Type | Creator | Flags | Actions  ||
|  |-------------------+------+---------+-------+----------||
|  | MemoDB            | DATA | memo    | D     | ⬇  🗑   ||
|  | AddressDB         | DATA | addr    | D     | ⬇  🗑   ||
|  | tinyGL            | appl | TGL0    | R     | ⬇  🗑   ||
|  | ...               |      |         |       |          ||
|  +------------------------------------------------------+|
|                                                          |
+----------------------------------------------------------+
|                                                          |
|  +--------------------------------------------------+    |
|  |  Drop .prc/.pdb files here to install             |    |
|  |  or click to browse                               |    |
|  +--------------------------------------------------+    |
|                                                          |
+----------------------------------------------------------+
```

### Styling

- Clean, minimal design with system fonts
- Responsive — works on any screen width
- Status indicator: green dot when connected, yellow when waiting, red when disconnected
- Table rows highlight on hover
- Drop zone has dashed border, highlights on drag-over
- Delete confirmation via simple `confirm()` dialog

### JavaScript

- Opens WebSocket on page load: `ws://localhost:8000/ws`
- Handles all server messages to update DOM
- Download buttons: navigate to `/api/pull/{name}` in hidden iframe or `window.location`
- Upload: drag-and-drop or file input, POST to `/api/push` via `fetch`
- Backup: navigate to `/api/backup`
- Reconnects WebSocket automatically on close (1 second delay)

### Connection States

| State | UI |
|-------|------|
| Waiting | Yellow dot, "Press HotSync button..." message, table empty |
| Connected | Green dot, device info shown, table populated |
| Disconnected | Red dot, "Device disconnected" message, table grayed out |

## Startup

Run with: `python -m web.server` or add CLI command `palm web`

Starts uvicorn on `http://localhost:8000`, opens device polling thread, prints URL to terminal.

## Error Handling

- **USB disconnect mid-operation:** Catch I/O errors, set state to disconnected, notify clients, return to polling
- **WebSocket disconnect:** Remove client from broadcast list, device stays connected for other clients
- **DLP errors:** Send error message to client with human-readable text
- **Upload invalid file:** Validate PDB/PRC header before attempting push, return error if invalid

## Out of Scope

- Authentication/multi-user
- HTTPS
- Editing database contents in the browser
- Sync/conduit logic
