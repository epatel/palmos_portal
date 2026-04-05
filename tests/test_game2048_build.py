"""Tests for the 2048 PalmOS game build output."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from palm.pdb import PalmDatabase, ATTR_RESOURCE


BUILD_DIR = Path(__file__).resolve().parent.parent / "projects" / "game2048"
BUILD_SCRIPT = BUILD_DIR / "build.py"


@pytest.fixture(scope="module", autouse=True)
def build_game():
    """Run the build script once before all tests in this module."""
    subprocess.check_call([sys.executable, str(BUILD_SCRIPT)])


class TestSourcePDB:
    """Tests for Game2048.c.pdb."""

    def test_file_exists(self):
        assert (BUILD_DIR / "Game2048.c.pdb").exists()

    def test_database_type(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.c.pdb")
        assert db.db_type == "TEXt"
        assert db.creator == "REAd"
        assert db.name == "Game2048.c"

    def test_has_header_and_text_records(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.c.pdb")
        assert len(db.records) == 2
        # Record 0: PalmDoc header (16 bytes)
        assert len(db.records[0].data) == 16
        # Record 1: source text
        assert len(db.records[1].data) > 100

    def test_source_contains_pilotmain(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.c.pdb")
        text = db.records[1].data.decode("cp1252")
        assert "PilotMain" in text
        assert "board" in text
        assert "slideLine" in text

    def test_not_resource_db(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.c.pdb")
        assert not db.is_resource_db


class TestResourcePRC:
    """Tests for Game2048.Rsrc.prc."""

    def test_file_exists(self):
        assert (BUILD_DIR / "Game2048.Rsrc.prc").exists()

    def test_database_type(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.Rsrc.prc")
        assert db.db_type == "Rsrc"
        assert db.creator == "OnBD"
        assert db.is_resource_db

    def test_has_form_and_alert(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.Rsrc.prc")
        types = [(r.res_type, r.res_id) for r in db.resources]
        assert ("tFRM", 1000) in types
        assert ("Talt", 1000) in types

    def test_form_resource_size(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.Rsrc.prc")
        tfrm = [r for r in db.resources if r.res_type == "tFRM"][0]
        # Form header (68) + directory (1 obj * 6) + title object data
        assert len(tfrm.data) >= 68 + 6


class TestProjectPRC:
    """Tests for Game2048.proj.prc."""

    def test_file_exists(self):
        assert (BUILD_DIR / "Game2048.proj.prc").exists()

    def test_database_type(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.proj.prc")
        assert db.db_type == "Proj"
        assert db.creator == "OnBD"
        assert db.is_resource_db

    def test_has_obpj_resource(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.proj.prc")
        assert len(db.resources) == 1
        assert db.resources[0].res_type == "OBPJ"
        assert db.resources[0].res_id == 1

    def test_obpj_contains_filenames(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.proj.prc")
        data = db.resources[0].data
        assert b"Game2048.Rsrc" in data
        assert b"Game2048.c" in data
        assert b"Game2048.obj" in data

    def test_obpj_creator_code(self):
        db = PalmDatabase.from_file(BUILD_DIR / "Game2048.proj.prc")
        data = db.resources[0].data
        assert b"G48a" in data
