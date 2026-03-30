"""OnboardC project file builder."""

from __future__ import annotations

import struct
from pathlib import Path

# Template: a known-good 330-byte OBPJ resource from a 2-file project
# (Breakout.proj pulled from real Handspring Visor after device-side fix)
_TEMPLATE_HEX = "0007000200000000000142726b4f6170706c427265616b6f7574000000000000000000000000000000000000000000000000427265616b6f7574000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002427265616b6f75742e527372630000000002000b000000000001000a0000226000007ff6737ffa6d21de0000227a0002000b000201000000000000000000010600fffdc0000000ff3dbd000022fa044c00000000231200008000000000e800fffdb100002327040000000000427265616b6f75742e63002e63005100000000234f051458000000236305154300007ff6670516007ffa7d236c05175500000023724f7074696f6e730044656c65746520480000427265616b6f75742e6f626a00742e6f626a0072002d0041626f7574204f6e42007ff529"


def _get_template() -> bytes:
    return bytes.fromhex(_TEMPLATE_HEX)


def build_obpj(project_name: str, creator: str = "appl",
               db_type: str = "appl", flags: int = 0x0001) -> bytes:
    """Build an OBPJ resource for a 2-file OnboardC project.

    Creates a project with {name}.Rsrc and {name}.c files.

    Args:
        project_name: Project name (max ~10 chars for safety)
        creator: 4-char creator code
        db_type: 4-char type code (usually "appl")
        flags: Project flags (0x0001=Execute, 0x0002=AlwaysRebuild,
               0x0004=Debug, 0x0008=AutoVersion)
    """
    template = _get_template()
    d = bytearray(template)

    # Header fields
    struct.pack_into(">H", d, 2, 2)  # file_count = 2
    struct.pack_into(">H", d, 8, flags)
    d[10:14] = creator.encode("ascii")[:4].ljust(4, b"\x00")
    d[14:18] = db_type.encode("ascii")[:4].ljust(4, b"\x00")
    d[18:50] = (project_name.encode("ascii") + b"\x00").ljust(32, b"\x00")
    d[50:114] = (project_name.encode("ascii") + b"\x00").ljust(64, b"\x00")

    # File references at fixed slot positions
    # Rsrc filename at 115, slot up to 128 (13 bytes)
    rsrc_name = (project_name + ".Rsrc").encode("ascii") + b"\x00"
    d[115:128] = rsrc_name[:13].ljust(13, b"\x00")

    # .c filename at 223, slot up to 234 (11 bytes)
    c_name = (project_name + ".c").encode("ascii") + b"\x00"
    d[223:234] = c_name[:11].ljust(11, b"\x00")

    # .obj filename at 294, slot up to 307 (13 bytes)
    obj_name = (project_name + ".obj").encode("ascii") + b"\x00"
    d[294:307] = obj_name[:13].ljust(13, b"\x00")

    return bytes(d)
