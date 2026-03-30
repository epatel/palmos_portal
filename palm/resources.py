"""PalmOS resource builders for tFRM, MBAR, Talt."""

from __future__ import annotations

import struct


# FormObjectKind
OBJ_FIELD = 0
OBJ_CONTROL = 1
OBJ_LIST = 2
OBJ_TABLE = 3
OBJ_BITMAP = 4
OBJ_LABEL = 8
OBJ_TITLE = 9
OBJ_POPUP = 10
OBJ_GRAFFITI = 11
OBJ_GADGET = 12
OBJ_SCROLLBAR = 13

# ControlStyles
STYLE_BUTTON = 0
STYLE_PUSHBUTTON = 1
STYLE_CHECKBOX = 2
STYLE_POPUP_TRIGGER = 3
STYLE_SELECTOR_TRIGGER = 4
STYLE_REPEATING_BUTTON = 5


def build_tfrm(form_id: int, width: int, height: int, menu_id: int,
               title: str, objects: list[dict]) -> bytes:
    """Build a tFRM (Form) resource.

    Args:
        form_id: Form resource ID (e.g. 1000)
        width: Form width (usually 160)
        height: Form height (usually 160)
        menu_id: Associated MBAR resource ID (0 for none)
        title: Form title string
        objects: List of object dicts with keys:
            - kind: 'checkbox', 'button', 'label'
            - id: Control ID
            - x, y, w, h: Bounds
            - label: Text label
            - style: (optional) override control style
            - font: (optional) font ID, default 0
    """
    # Form header: 68 bytes
    header = bytearray(68)
    header[8] = 0x12  # window flags: usable | saveBehind
    struct.pack_into(">H", header, 14, width)
    struct.pack_into(">H", header, 16, height)
    header[30] = 0x00
    header[31] = 0x01
    struct.pack_into(">H", header, 40, form_id)
    header[42] = 0x88  # form attr: usable + saveBehind
    struct.pack_into(">H", header, 60, menu_id)
    num_objects = 1 + len(objects)  # title + controls
    struct.pack_into(">H", header, 62, num_objects)

    # Build object data
    # Title object: 12-byte header (zeros) + null-terminated text
    title_data = bytearray(12) + title.encode("cp1252") + b"\x00"
    if len(title_data) % 2:
        title_data += b"\x00"

    obj_datas = [bytes(title_data)]
    obj_types = [OBJ_TITLE]

    for obj in objects:
        kind = obj["kind"]
        if kind in ("checkbox", "button", "pushbutton", "repeating"):
            style_map = {
                "button": STYLE_BUTTON,
                "pushbutton": STYLE_PUSHBUTTON,
                "checkbox": STYLE_CHECKBOX,
                "repeating": STYLE_REPEATING_BUTTON,
            }
            style = obj.get("style", style_map.get(kind, STYLE_BUTTON))
            font = obj.get("font", 0)
            group = obj.get("group", 0)
            label = obj["label"].encode("cp1252") + b"\x00"
            if len(label) % 2:
                label += b"\x00"
            ctl = struct.pack(
                ">HHHHHhhHBBBx",
                obj["id"],
                obj["x"], obj["y"], obj["w"], obj["h"],
                0, 0,  # bitmapId, selectedBitmapId
                0xC000,  # attr: usable + enabled
                style, font, group,
            )
            obj_datas.append(ctl + label)
            obj_types.append(OBJ_CONTROL)
        elif kind == "label":
            label_text = obj["label"].encode("cp1252") + b"\x00"
            if len(label_text) % 2:
                label_text += b"\x00"
            # Label: id(2) + x(2) + y(2) + attr(2) + font(1) + pad(1) + textPtr(4)
            # textPtr points to text immediately after the struct (offset = 14)
            lbl = struct.pack(
                ">HHHHBxI",
                obj["id"],
                obj["x"], obj["y"],
                0x8000,  # usable
                obj.get("font", 0),
                0,  # text pointer (fixed up by OS at load time)
            )
            obj_datas.append(lbl + label_text)
            obj_types.append(OBJ_LABEL)

    # Object directory: 6 bytes each (type:byte, pad:byte, offset:uint32)
    dir_start = 68
    data_start = dir_start + num_objects * 6
    directory = bytearray()
    offset = data_start
    for i in range(num_objects):
        directory += struct.pack(">BxI", obj_types[i], offset)
        offset += len(obj_datas[i])

    return bytes(header) + bytes(directory) + b"".join(obj_datas)


def build_talt(alert_type: int, title: str, message: str,
               buttons: list[str]) -> bytes:
    """Build a Talt (Alert) resource.

    Args:
        alert_type: 0=info, 1=confirm, 2=warning, 3=error
        title: Alert title
        message: Alert message text
        buttons: List of button labels (e.g. ["OK"])
    """
    data = struct.pack(">HHHH", alert_type, 0, len(buttons), 0)
    data += title.encode("cp1252") + b"\x00"
    data += message.encode("cp1252") + b"\x00"
    for btn in buttons:
        data += btn.encode("cp1252") + b"\x00"
    return data


def build_mbar(menu_id: int, menus: list[dict]) -> bytes:
    """Build an MBAR (Menu Bar) resource.

    This is a simplified builder — for complex menus, use RsrcEdit on device.

    Args:
        menu_id: Menu bar resource ID
        menus: List of menu dicts with:
            - title: Menu title (e.g. "Options")
            - items: List of dicts with 'label' and 'id' keys
    """
    # MBAR format is complex with many internal offsets
    # For now, return a copy of the skeleton's MBAR with updated strings
    # This is a placeholder — proper MBAR building requires more research
    raise NotImplementedError("Use skeleton MBAR or RsrcEdit for menu bars")
