from unittest.mock import MagicMock
from palm.pdb import PalmDatabase, ATTR_RESOURCE, Record as PDBRecord, Resource as PDBResource
from palm.dlp import Record as DLPRecord, Resource as DLPResource, DLPException, DLPError


class TestFromDevice:
    def test_pull_record_db(self):
        mock_dlp = MagicMock()
        mock_dlp.open_db.return_value = 1
        mock_dlp.read_open_db_info.return_value = 2
        mock_dlp.read_record.side_effect = [
            DLPRecord(index=0, attributes=0x40, unique_id=1, data=b"Record One"),
            DLPRecord(index=1, attributes=0x40, unique_id=2, data=b"Record Two"),
        ]
        mock_dlp.read_app_block.return_value = b"AppInfo"
        mock_dlp.read_sort_block.return_value = b""

        db = PalmDatabase.from_device(
            mock_dlp, name="TestDB",
            db_type="DATA", creator="test", attributes=0x0000,
        )

        assert db.name == "TestDB"
        assert len(db.records) == 2
        assert db.records[0].data == b"Record One"
        assert db.records[1].data == b"Record Two"
        assert db.app_info == b"AppInfo"
        mock_dlp.open_db.assert_called_once()
        mock_dlp.close_db.assert_called_once_with(1)

    def test_pull_resource_db(self):
        mock_dlp = MagicMock()
        mock_dlp.open_db.return_value = 2
        mock_dlp.read_open_db_info.return_value = 1
        mock_dlp.read_resource.return_value = DLPResource(
            res_type="code", res_id=1, index=0, data=b"\x4E\x75",
        )
        mock_dlp.read_app_block.return_value = b""
        mock_dlp.read_sort_block.return_value = b""

        db = PalmDatabase.from_device(
            mock_dlp, name="MyApp",
            db_type="appl", creator="MyAp", attributes=ATTR_RESOURCE,
        )

        assert db.is_resource_db is True
        assert len(db.resources) == 1
        assert db.resources[0].res_type == "code"


class TestToDevice:
    def test_push_record_db(self):
        mock_dlp = MagicMock()
        mock_dlp.create_db.return_value = 3

        db = PalmDatabase(
            name="NewDB", db_type="DATA", creator="test",
            attributes=0x0000, version=1,
            records=[
                PDBRecord(data=b"Rec1", attributes=0x40, unique_id=1),
                PDBRecord(data=b"Rec2", attributes=0x40, unique_id=2),
            ],
            app_info=b"AppBlock",
        )
        db.to_device(mock_dlp)

        mock_dlp.create_db.assert_called_once()
        mock_dlp.write_app_block.assert_called_once_with(3, b"AppBlock")
        assert mock_dlp.write_record.call_count == 2
        mock_dlp.close_db.assert_called_once_with(3)

    def test_push_deletes_existing_db(self):
        mock_dlp = MagicMock()
        mock_dlp.create_db.return_value = 5

        db = PalmDatabase(
            name="Existing", db_type="DATA", creator="test",
            attributes=0x0000,
        )
        db.to_device(mock_dlp)

        mock_dlp.delete_db.assert_called_once_with("Existing")

    def test_push_resource_db(self):
        mock_dlp = MagicMock()
        mock_dlp.create_db.return_value = 4

        db = PalmDatabase(
            name="ResApp", db_type="appl", creator="RsAp",
            attributes=ATTR_RESOURCE,
            resources=[
                PDBResource(res_type="code", res_id=1, data=b"\x4E\x75"),
            ],
        )
        db.to_device(mock_dlp)

        mock_dlp.create_db.assert_called_once()
        assert mock_dlp.write_resource.call_count == 1
        mock_dlp.close_db.assert_called_once_with(4)
