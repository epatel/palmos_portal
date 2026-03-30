# PalmOS Development Guide for AI Agents

## Overview

This guide documents how to create PalmOS applications for a Handspring Visor using the `palm-com` toolchain. Programs are written as C source on the host machine, pushed to the device, and compiled on-device using OnboardC.

## Architecture

```
Host (Mac/PC)                    Device (Handspring Visor)
─────────────                    ────────────────────────
palm-com CLI/Web UI              OnboardC compiler
  ├── Create .c.pdb (source)     ├── Compiles C to 68k
  ├── Create .Rsrc.prc (UI)      ├── Links with resources
  ├── Create .proj.prc (project) └── Produces runnable .prc
  └── Push via USB HotSync
```

### Files Required

Every OnboardC project needs exactly 3 files:

| File | PDB Type | Creator | Purpose |
|------|----------|---------|---------|
| `Name.c.pdb` | TEXt | REAd | C source code (PalmDoc format) |
| `Name.Rsrc.prc` | Rsrc | OnBD | UI resources (forms, menus, alerts) |
| `Name.proj.prc` | Proj | OnBD | Project settings and file list |

## Creating Source Code (.c.pdb)

Source is stored in PalmDoc format: a record database with a 16-byte header record and text data records.

```python
import struct
from palm.pdb import PalmDatabase, Record

source_code = "... your C code ..."
text_data = source_code.encode("cp1252")
header = struct.pack(">HHIHHI", 1, 0, len(text_data), 1, 4096, 0).ljust(16, b"\x00")

db = PalmDatabase(
    name="MyApp.c", db_type="TEXt", creator="REAd",
    attributes=0, version=0,
    records=[
        Record(data=header, attributes=0, unique_id=1),
        Record(data=text_data, attributes=0, unique_id=2),
    ]
)
db.to_file("MyApp.c.pdb")
```

### PalmDoc Header (Record 0, 16 bytes)

| Offset | Size | Field | Value |
|--------|------|-------|-------|
| 0 | 2 | Version | 1 (uncompressed) or 2 (LZ77) |
| 2 | 2 | Reserved | 0 |
| 4 | 4 | Text length | Length of source text in bytes |
| 8 | 2 | Record count | Number of text records |
| 10 | 2 | Record size | Max bytes per record (4096) |
| 12 | 4 | Position | 0 |

## Creating Resources (.Rsrc.prc)

UI resources are stored in a PRC (resource database). Use `palm.resources` to build them programmatically.

```python
from palm.pdb import PalmDatabase, Resource, ATTR_RESOURCE
from palm.resources import build_tfrm, build_talt

# Build a form with controls
tfrm = build_tfrm(1000, 160, 160, 1000, "My App", [
    {"kind": "label", "id": 1002, "x": 10, "y": 20, "label": "Hello World"},
    {"kind": "checkbox", "id": 1001, "x": 10, "y": 40, "w": 100, "h": 14, "label": "Done"},
    {"kind": "button", "id": 1003, "x": 50, "y": 80, "w": 60, "h": 14, "label": "OK"},
])

# Build an alert dialog
talt = build_talt(0, "About MyApp", "Version 1.0", ["OK"])

db = PalmDatabase(
    name="MyApp.Rsrc", db_type="Rsrc", creator="OnBD",
    attributes=ATTR_RESOURCE, version=0,
    resources=[
        Resource(res_type="tFRM", res_id=1000, data=tfrm),
        Resource(res_type="MBAR", res_id=1000, data=mbar_data),  # from skeleton
        Resource(res_type="Talt", res_id=1000, data=talt),
    ]
)
db.to_file("MyApp.Rsrc.prc")
```

### build_tfrm() Parameters

```python
build_tfrm(form_id, width, height, menu_id, title, objects)
```

- `form_id`: Resource ID (e.g. 1000)
- `width`, `height`: Form size (usually 160×160 for full screen)
- `menu_id`: Associated MBAR resource ID (0 for no menu)
- `title`: Title bar text
- `objects`: List of control dicts

### Control Types

**Checkbox:**
```python
{"kind": "checkbox", "id": 1001, "x": 10, "y": 40, "w": 100, "h": 14, "label": "Check me"}
```

**Button:**
```python
{"kind": "button", "id": 1002, "x": 50, "y": 80, "w": 60, "h": 14, "label": "OK"}
```

**Push Button:**
```python
{"kind": "pushbutton", "id": 1003, "x": 10, "y": 60, "w": 60, "h": 14, "label": "Toggle"}
```

**Repeating Button:**
```python
{"kind": "repeating", "id": 1004, "x": 10, "y": 80, "w": 30, "h": 14, "label": "<"}
```

**Label (static text):**
```python
{"kind": "label", "id": 1005, "x": 10, "y": 20, "label": "Hello World"}
```

### Control Styles (Internal)

| Style | Value | Constant |
|-------|-------|----------|
| Button | 0 | buttonCtl |
| Push Button | 1 | pushButtonCtl |
| Checkbox | 2 | checkboxCtl |
| Popup Trigger | 3 | popupTriggerCtl |
| Selector Trigger | 4 | selectorTriggerCtl |
| Repeating Button | 5 | repeatingButtonCtl |

### Alert Types

```python
build_talt(alert_type, title, message, buttons)
```

| Type | Value | Icon |
|------|-------|------|
| Information | 0 | (i) |
| Confirmation | 1 | ? |
| Warning | 2 | ! |
| Error | 3 | X |

### tFRM Binary Format

```
Form header:     68 bytes (window + form attributes)
Object directory: N × 6 bytes (type:byte, pad:byte, offset:uint32)
Object data:     Variable (title, controls, labels)

Title object:    12 bytes zeros + null-terminated text (word-aligned)
Control object:  id(2) + x(2) + y(2) + w(2) + h(2) + bitmapId(2) +
                 selBitmapId(2) + attr(2) + style(1) + font(1) +
                 group(1) + pad(1) + text\0 (word-aligned)
Label object:    id(2) + x(2) + y(2) + attr(2) + font(1) + pad(1) +
                 textPtr(4) + text\0 (word-aligned)
```

Object type IDs: 0=field, 1=control, 2=list, 3=table, 4=bitmap, 8=label, 9=title, 10=popup, 11=graffiti, 12=gadget, 13=scrollbar

### Menu Bar (MBAR)

MBAR resources have a complex binary format. For simple apps, copy the skeleton MBAR and replace the app name:

```python
from palm.pdb import PalmDatabase
skel = PalmDatabase.from_file("path/to/Skeleton/Test1.Rsrc.prc")
mbar_data = bytes(skel.resources[1].data).replace(b"Test1", b"MyApp")
```

The skeleton MBAR provides an "Options" menu with an "About" item (ID 1000).

## Creating Project Files (.proj.prc)

```python
from palm.pdb import PalmDatabase, Resource, ATTR_RESOURCE
from palm.project import build_obpj

obpj = build_obpj("MyApp", creator="MyAp", db_type="appl", flags=0x0001)

db = PalmDatabase(
    name="MyApp.proj", db_type="Proj", creator="OnBD",
    attributes=ATTR_RESOURCE, version=0,
    resources=[Resource(res_type="OBPJ", res_id=1, data=obpj)]
)
db.to_file("MyApp.proj.prc")
```

### Project Flags

| Bit | Value | Flag |
|-----|-------|------|
| 0 | 0x0001 | Execute (auto-run after compile) |
| 1 | 0x0002 | Always Rebuild |
| 2 | 0x0004 | Debug |
| 3 | 0x0008 | Auto Version |

### OBPJ Binary Format (330 bytes for 2-file project)

| Offset | Size | Field |
|--------|------|-------|
| 0-1 | 2 | Version (7) |
| 2-3 | 2 | File count |
| 4-7 | 4 | Reserved |
| 8-9 | 2 | Flags bitmask |
| 10-13 | 4 | Creator code (ASCII) |
| 14-17 | 4 | Type code (ASCII) |
| 18-49 | 32 | PRC name (null-padded) |
| 50-113 | 64 | Project name (null-padded) |
| 115 | 13 | .Rsrc filename slot |
| 223 | 11 | .c filename slot |
| 294 | 13 | .obj filename slot |

Project name max: ~10 characters (to fit all filename slots).

## Pushing to Device

```python
# Using CLI
# python cli.py push MyApp.c.pdb
# python cli.py push MyApp.Rsrc.prc
# python cli.py push MyApp.proj.prc

# Or using the web dashboard
# python cli.py web
# Then drag-and-drop files in the browser
```

Each push requires a separate HotSync button press on the Visor cradle.

## C Source Code Structure

### Minimal App Template

```c
#ifdef __GNUC__
#    include <PalmOS.h>
#endif

#define MainForm    1000
#define AboutAlert  1000

// Forward declarations
static Boolean appHandleEvent(EventPtr pEvent);
static Boolean mainFormEventHandler(EventPtr pEvent);

UInt32 PilotMain(UInt16 cmd, void *cmdPBP, UInt16 launchFlags) {
    EventType event;
    UInt16 error;
    if (cmd == sysAppLaunchCmdNormalLaunch) {
        FrmGotoForm(MainForm);
        do {
            EvtGetEvent(&event, evtWaitForever);
            if (!SysHandleEvent(&event))
            if (!MenuHandleEvent(0, &event, &error))
            if (!appHandleEvent(&event))
                FrmDispatchEvent(&event);
        } while (event.eType != appStopEvent);
        FrmCloseAllForms();
    }
    return 0;
}

static Boolean appHandleEvent(EventPtr pEvent) {
    FormPtr pForm;
    Boolean handled = false;
    if (pEvent->eType == frmLoadEvent) {
        pForm = FrmInitForm(pEvent->data.frmLoad.formID);
        FrmSetActiveForm(pForm);
        FrmSetEventHandler(pForm, mainFormEventHandler);
        handled = true;
    } else if (pEvent->eType == menuEvent) {
        pForm = FrmGetActiveForm();
        if (pEvent->data.menu.itemID == AboutAlert) {
            FrmAlert(AboutAlert);
            handled = true;
        }
    }
    return handled;
}

static Boolean mainFormEventHandler(EventPtr pEvent) {
    Boolean handled = false;
    FormPtr pForm = FrmGetActiveForm();
    switch (pEvent->eType) {
    case frmOpenEvent:
        FrmDrawForm(pForm);
        handled = true;
        break;
    default:
        break;
    }
    return handled;
}
```

### Game Loop Template

For games or real-time apps, use a short EvtGetEvent timeout and TimGetTicks():

```c
#define TICK_RATE 3  // ~30ms per update

static UInt32 lastTick;

// In PilotMain, use short timeout:
EvtGetEvent(&event, 1);  // 1 tick timeout (~10ms)

// In form event handler:
case nilEvent:
    now = TimGetTicks();
    if (now - lastTick >= TICK_RATE) {
        lastTick = now;
        gameStep();  // Update game state
    }
    handled = true;
    break;

// Pen events for input (don't block game loop):
case penDownEvent:
case penMoveEvent:
    // Handle touch input
    handled = true;
    break;
```

### Common Event Types

| Event | Usage |
|-------|-------|
| `frmLoadEvent` | Form resource loaded — init and set handler |
| `frmOpenEvent` | Form displayed — draw initial content |
| `ctlSelectEvent` | Button/checkbox tapped |
| `menuEvent` | Menu item selected |
| `penDownEvent` | Stylus touched screen |
| `penMoveEvent` | Stylus dragged |
| `penUpEvent` | Stylus lifted |
| `nilEvent` | No events (timeout) — use for game loops |
| `keyDownEvent` | Hardware button pressed |
| `appStopEvent` | App closing |

### Common API Functions

**Forms:**
```c
FrmInitForm(formId)           // Load form resource
FrmSetActiveForm(pForm)       // Set as current form
FrmDrawForm(pForm)            // Draw form and all objects
FrmGotoForm(formId)           // Switch to another form
FrmAlert(alertId)             // Show alert dialog, returns button index
FrmCloseAllForms()            // Cleanup on exit
FrmGetActiveForm()            // Get current form pointer
FrmSetEventHandler(pForm, handler)  // Set form event callback
FrmGetObjectIndex(pForm, id)  // Get object index by ID
FrmGetObjectPtr(pForm, index) // Get object pointer by index
```

**Controls:**
```c
CtlGetValue(controlPtr)            // Get checkbox/button state (0 or 1)
CtlSetValue(controlPtr, value)     // Set checkbox/button state
CtlSetLabel(controlPtr, text)      // Change button/checkbox label
```

**Drawing:**
```c
WinDrawChars(text, len, x, y)      // Draw text at position
WinDrawRectangle(&rect, cornerRadius)  // Draw filled rectangle
WinEraseRectangle(&rect, cornerRadius) // Erase (fill with background)
WinDrawLine(x1, y1, x2, y2)       // Draw line
WinInvertRectangle(&rect, 0)       // Invert pixels in rectangle
```

**Strings:**
```c
StrPrintF(buf, format, ...)   // sprintf equivalent
StrLen(str)                   // String length
StrCopy(dst, src)             // String copy
StrCompare(s1, s2)            // String compare
```

**System:**
```c
TimGetTicks()                 // System tick count (~100 ticks/sec)
SysTaskDelay(ticks)           // Sleep for N ticks
EvtGetEvent(&event, timeout)  // Get event (evtWaitForever or tick count)
SysHandleEvent(&event)        // System event processing
MenuHandleEvent(0, &event, &error)  // Menu event processing
```

**Memory:**
```c
MemPtrNew(size)               // Allocate memory
MemPtrFree(ptr)               // Free memory
MemSet(ptr, size, value)      // Fill memory
MemMove(dst, src, size)       // Copy memory
```

### Data Types

| Type | Size | Description |
|------|------|-------------|
| `UInt8` | 1 | Unsigned 8-bit |
| `UInt16` | 2 | Unsigned 16-bit |
| `UInt32` | 4 | Unsigned 32-bit |
| `Int8` | 1 | Signed 8-bit |
| `Int16` | 2 | Signed 16-bit |
| `Int32` | 4 | Signed 32-bit |
| `Boolean` | 1 | true/false |
| `Char` | 1 | Character |
| `SWord` | 2 | Signed word (same as Int16) |
| `FormPtr` | 4 | Pointer to FormType |
| `EventPtr` | 4 | Pointer to EventType |
| `ControlPtr` | 4 | Pointer to ControlType |

### RectangleType

```c
RectangleType rect;
rect.topLeft.x = 10;
rect.topLeft.y = 20;
rect.extent.x = 50;   // width
rect.extent.y = 30;    // height
```

## PalmOS Screen

- Resolution: **160 × 160 pixels**, 1-bit (black and white)
- Coordinate system: (0,0) at top-left
- Title bar: ~14 pixels tall at top
- Graffiti area: below the screen (not part of display)
- Available drawing area: approximately 160 × 146 pixels

## Resource IDs Convention

| Range | Usage |
|-------|-------|
| 1000-1099 | Main form and its controls |
| 1100-1199 | Second form |
| 1000 | About alert (commonly) |

## Text Encoding

PalmOS uses **Windows-1252 (cp1252)** encoding. Always encode/decode with `"cp1252"` when creating PDB text records.

## Limitations

- **OnboardC compiler**: Subset of C. No floating point, limited stdlib.
- **Project names**: Max ~10 characters (filename slots are fixed size).
- **Screen**: 160×160, 1-bit black and white. No color on Visor.
- **Memory**: Limited RAM (~2-8MB depending on model).
- **Records**: Max ~64KB per DLP transfer.
- **MBAR resources**: Complex binary format — copy from skeleton and modify strings.

## Complete Example: App with Checkbox

```python
import struct
from palm.pdb import PalmDatabase, Record, Resource, ATTR_RESOURCE
from palm.resources import build_tfrm, build_talt
from palm.project import build_obpj

name = "MyApp"
creator = "MyAp"

# 1. Source code
source = '''#ifdef __GNUC__
#    include <PalmOS.h>
#endif
#define MainForm     1000
#define MyCheckbox   1001
#define AboutAlert   1000

static Boolean appHandleEvent(EventPtr pEvent) {
    FormPtr pForm;
    Boolean handled = false;
    if (pEvent->eType == frmLoadEvent) {
        pForm = FrmInitForm(pEvent->data.frmLoad.formID);
        FrmSetActiveForm(pForm);
        FrmSetEventHandler(pForm, mainFormEventHandler);
        handled = true;
    } else if (pEvent->eType == menuEvent) {
        pForm = FrmGetActiveForm();
        if (pEvent->data.menu.itemID == AboutAlert) {
            FrmAlert(AboutAlert);
            handled = true;
        }
    }
    return handled;
}
static Boolean mainFormEventHandler(EventPtr pEvent) {
    Boolean handled = false;
    FormPtr pForm = FrmGetActiveForm();
    if (pEvent->eType == frmOpenEvent) {
        FrmDrawForm(pForm);
        handled = true;
    }
    return handled;
}
UInt32 PilotMain(UInt16 cmd, void *cmdPBP, UInt16 launchFlags) {
    EventType event; UInt16 error;
    if (cmd == sysAppLaunchCmdNormalLaunch) {
        FrmGotoForm(MainForm);
        do {
            EvtGetEvent(&event, evtWaitForever);
            if (!SysHandleEvent(&event))
            if (!MenuHandleEvent(0, &event, &error))
            if (!appHandleEvent(&event))
                FrmDispatchEvent(&event);
        } while (event.eType != appStopEvent);
        FrmCloseAllForms();
    }
    return 0;
}
'''
text_data = source.encode("cp1252")
header = struct.pack(">HHIHHI", 1, 0, len(text_data), 1, 4096, 0).ljust(16, b"\\x00")
PalmDatabase(
    name=f"{name}.c", db_type="TEXt", creator="REAd", attributes=0, version=0,
    records=[Record(data=header, attributes=0, unique_id=1),
             Record(data=text_data, attributes=0, unique_id=2)]
).to_file(f"{name}.c.pdb")

# 2. Resources
tfrm = build_tfrm(1000, 160, 160, 1000, name, [
    {"kind": "checkbox", "id": 1001, "x": 10, "y": 30, "w": 100, "h": 14, "label": "Check me"},
])
talt = build_talt(0, f"About {name}", "Version 1.0", ["OK"])
# Copy MBAR from skeleton (replace name)
skel = PalmDatabase.from_file("path/to/Skeleton/Test1.Rsrc.prc")
mbar = bytes(skel.resources[1].data).replace(b"Test1", bytes(name, "ascii"))
PalmDatabase(
    name=f"{name}.Rsrc", db_type="Rsrc", creator="OnBD",
    attributes=ATTR_RESOURCE, version=0,
    resources=[Resource(res_type="tFRM", res_id=1000, data=tfrm),
               Resource(res_type="MBAR", res_id=1000, data=mbar),
               Resource(res_type="Talt", res_id=1000, data=talt)]
).to_file(f"{name}.Rsrc.prc")

# 3. Project
obpj = build_obpj(name, creator=creator, db_type="appl", flags=0x0001)
PalmDatabase(
    name=f"{name}.proj", db_type="Proj", creator="OnBD",
    attributes=ATTR_RESOURCE, version=0,
    resources=[Resource(res_type="OBPJ", res_id=1, data=obpj)]
).to_file(f"{name}.proj.prc")

# 4. Push all three files via CLI:
#    python cli.py push MyApp.c.pdb
#    python cli.py push MyApp.Rsrc.prc
#    python cli.py push MyApp.proj.prc
# 5. On device: OnboardC → Open MyApp → Compile → Run
```
