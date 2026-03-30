<a href="https://claude.ai"><img src="made-with-claude.png" height="32" alt="Made with Claude"></a>

# palmos-portal

A Python toolkit for communicating with PalmOS devices over USB. Built from scratch — implements the full HotSync protocol stack (SLP, PADP, DLP) to read and write databases and applications to a Handspring Visor.

Includes a web dashboard with real-time device management, 3D model viewer, and a PalmOS app development toolchain.

## Features

- **CLI** — `list`, `pull`, `push`, `delete`, `sysinfo` commands
- **Web Dashboard** — browser-based device manager with live WebSocket connection
- **Database Preview** — view memos, contacts, calendar, todos with format-aware parsing
- **Inline Editing** — edit memos, todos, and source code directly from the browser
- **3D Model Viewer** — render tinyGL models with Three.js, export as STL
- **App Development** — create PalmOS apps from the host, compile on-device with OnboardC
- **Resource Builder** — programmatically generate PalmOS forms, controls, and alerts

## Requirements

- Python 3.10+
- libusb (`brew install libusb` on macOS)
- A PalmOS device with USB cradle (tested with Handspring Visor)

## Install

```bash
git clone <repo>
cd palm_com
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

### CLI

```bash
# List databases on device (press HotSync when prompted)
python cli.py list

# Show device info
python cli.py sysinfo

# Download a database
python cli.py pull MemoDB

# Upload a file
python cli.py push MyApp.prc

# Delete a database
python cli.py delete TestDB
```

### Web Dashboard

```bash
python cli.py web
```

Opens a browser dashboard at `http://localhost:8000`. Press HotSync to connect. Features:

- Split-panel layout: database list on the left, content preview on the right
- Click any database to preview its contents
- Download, delete, upload databases
- Edit memos and todos inline
- View and edit OnboardC source code with syntax highlighting
- View tinyGL 3D models with interactive rotation
- Backup all databases as a zip file
- Dark mode support

### Developing PalmOS Apps

Apps are created on the host and compiled on-device using [OnboardC](https://onboardc.sourceforge.net/) with UI resources edited via [RsrcEdit](https://palmdb.net/app/rsrcedit). See the [OnboardC Cookbook](https://onboardc.sourceforge.net/cookbook.html) for PalmOS programming examples and [docs/palmos-dev-guide.md](docs/palmos-dev-guide.md) for the full guide.

```python
from palm.pdb import PalmDatabase, Record, Resource, ATTR_RESOURCE
from palm.resources import build_tfrm, build_talt
from palm.project import build_obpj

# Create a form with a checkbox
tfrm = build_tfrm(1000, 160, 160, 1000, "MyApp", [
    {"kind": "label", "id": 1002, "x": 10, "y": 20, "label": "Hello PalmOS!"},
    {"kind": "checkbox", "id": 1001, "x": 10, "y": 40, "w": 100, "h": 14, "label": "Done"},
])
```

## Example Projects

Ready-to-install projects in `projects/`:

- **breakout** — Brick-breaker game. OnboardC project (source + resources + project file). Push all three files, compile on device.
- **tinygl** — 3D model viewer app with sample models (Glider1, Plane1). The web dashboard renders these with Three.js and can export to STL.
- **asciimation** — Star Wars ASCII animation player. Pre-built PRC + data.

## Protocol Stack

```
USB Transport (PyUSB/libusb)
    └── SLP — Serial Link Protocol (packet framing, CRC-CCITT)
        └── PADP — Packet Assembly/Disassembly (reliable delivery, fragmentation)
            └── DLP — Desktop Link Protocol (database commands)
```

All protocol layers implemented from scratch based on the pilot-link source code and empirical testing with real hardware.

## Project Structure

```
palm_com/
├── palm/
│   ├── transport.py    # USB connection, CMP handshake
│   ├── slp.py          # Serial Link Protocol
│   ├── padp.py         # Packet Assembly/Disassembly
│   ├── dlp.py          # Desktop Link Protocol
│   ├── pdb.py          # PDB/PRC file format
│   ├── resources.py    # tFRM/Talt resource builder
│   └── project.py      # OnboardC project builder
├── web/
│   ├── server.py       # FastAPI + WebSocket server
│   └── static/
│       └── index.html  # Dashboard UI
├── cli.py              # Command-line interface
├── tests/              # Unit tests (53 tests)
└── docs/
    └── palmos-dev-guide.md
```

## Testing

```bash
pytest tests/ -v
```

## License

MIT
