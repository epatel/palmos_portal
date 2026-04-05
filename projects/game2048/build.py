#!/usr/bin/env python3
"""Build script for the 2048 PalmOS game.

Generates three files for OnboardC:
  Game2048.c.pdb   — PalmDoc source database
  Game2048.Rsrc.prc — Resource database (form + alert)
  Game2048.proj.prc — OnboardC project file

Usage:
    python projects/game2048/build.py
"""

import struct
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from palm.pdb import PalmDatabase, Record, Resource, ATTR_RESOURCE
from palm.resources import build_tfrm, build_talt
from palm.project import build_obpj

PROJECT_NAME = "Game2048"
CREATOR = "G48a"
OUTPUT_DIR = Path(__file__).resolve().parent


def build_source_pdb():
    """Build the .c.pdb PalmDoc source database."""
    source_path = OUTPUT_DIR / "game2048.c"
    text_data = source_path.read_text(encoding="utf-8").encode("cp1252")

    # PalmDoc header: version(2) + pad(2) + uncompressed_len(4) +
    #                 record_count(2) + record_size(2) + pad(4)  = 16 bytes
    header = struct.pack(">HHIHHI", 1, 0, len(text_data), 1, 4096, 0)
    header = header.ljust(16, b"\x00")

    db = PalmDatabase(
        name=f"{PROJECT_NAME}.c",
        db_type="TEXt",
        creator="REAd",
        attributes=0,
        version=0,
        records=[
            Record(data=header, attributes=0, unique_id=1),
            Record(data=text_data, attributes=0, unique_id=2),
        ],
    )
    out = OUTPUT_DIR / f"{PROJECT_NAME}.c.pdb"
    db.to_file(out)
    print(f"  {out.name} ({out.stat().st_size} bytes)")


def build_resource_prc():
    """Build the .Rsrc.prc resource database (form + alert)."""
    # Main form: full screen, no menu, title "2048", no controls
    tfrm = build_tfrm(
        form_id=1000,
        width=160,
        height=160,
        menu_id=0,
        title="2048",
        objects=[],
    )

    # Game over alert
    talt = build_talt(
        alert_type=0,  # info
        title="Game Over",
        message="No moves remaining!",
        buttons=["New Game"],
    )

    db = PalmDatabase(
        name=f"{PROJECT_NAME}.Rsrc",
        db_type="Rsrc",
        creator="OnBD",
        attributes=ATTR_RESOURCE,
        version=0,
        resources=[
            Resource(res_type="tFRM", res_id=1000, data=tfrm),
            Resource(res_type="Talt", res_id=1000, data=talt),
        ],
    )
    out = OUTPUT_DIR / f"{PROJECT_NAME}.Rsrc.prc"
    db.to_file(out)
    print(f"  {out.name} ({out.stat().st_size} bytes)")


def build_project_prc():
    """Build the .proj.prc OnboardC project file."""
    obpj = build_obpj(
        project_name=PROJECT_NAME,
        creator=CREATOR,
        db_type="appl",
        flags=0x0001,  # Execute after compile
    )

    db = PalmDatabase(
        name=f"{PROJECT_NAME}.proj",
        db_type="Proj",
        creator="OnBD",
        attributes=ATTR_RESOURCE,
        version=0,
        resources=[
            Resource(res_type="OBPJ", res_id=1, data=obpj),
        ],
    )
    out = OUTPUT_DIR / f"{PROJECT_NAME}.proj.prc"
    db.to_file(out)
    print(f"  {out.name} ({out.stat().st_size} bytes)")


def main():
    print(f"Building {PROJECT_NAME}...")
    build_source_pdb()
    build_resource_prc()
    build_project_prc()
    print("Done!")


if __name__ == "__main__":
    main()
