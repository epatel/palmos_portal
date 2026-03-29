from unittest.mock import patch, MagicMock
from click.testing import CliRunner


class TestCLISysinfo:
    @patch("cli.Connection")
    def test_sysinfo_command(self, MockConn):
        from cli import cli
        from palm.dlp import SysInfo

        mock_conn = MagicMock()
        MockConn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        MockConn.return_value.__exit__ = MagicMock(return_value=False)

        with patch("cli.SLPSocket") as MockSLP, \
             patch("cli.PADPConnection") as MockPADP, \
             patch("cli.DLPClient") as MockDLP:
            mock_dlp = MagicMock()
            MockDLP.return_value = mock_dlp
            mock_dlp.read_sys_info.return_value = SysInfo(
                rom_version=0x03503000, locale=0, name="Visor"
            )

            runner = CliRunner()
            result = runner.invoke(cli, ["sysinfo"])
            assert result.exit_code == 0
            assert "Visor" in result.output


class TestCLIList:
    @patch("cli.Connection")
    def test_list_command(self, MockConn):
        from cli import cli
        from palm.dlp import DatabaseInfo

        mock_conn = MagicMock()
        MockConn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        MockConn.return_value.__exit__ = MagicMock(return_value=False)

        with patch("cli.SLPSocket"), \
             patch("cli.PADPConnection"), \
             patch("cli.DLPClient") as MockDLP:
            mock_dlp = MagicMock()
            MockDLP.return_value = mock_dlp
            mock_dlp.list_databases.return_value = [
                DatabaseInfo(
                    name="MemoDB", attributes=0, version=1,
                    creation_time=0, modification_time=0, backup_time=0,
                    db_type="DATA", creator="memo", num_records=5,
                ),
                DatabaseInfo(
                    name="AddressDB", attributes=0, version=1,
                    creation_time=0, modification_time=0, backup_time=0,
                    db_type="DATA", creator="addr", num_records=10,
                ),
            ]

            runner = CliRunner()
            result = runner.invoke(cli, ["list"])
            assert result.exit_code == 0
            assert "MemoDB" in result.output
            assert "AddressDB" in result.output


class TestCLIPull:
    @patch("cli.Connection")
    def test_pull_creates_file(self, MockConn, tmp_path):
        from cli import cli
        from palm.dlp import DatabaseInfo, Record

        mock_conn = MagicMock()
        MockConn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        MockConn.return_value.__exit__ = MagicMock(return_value=False)

        with patch("cli.SLPSocket"), \
             patch("cli.PADPConnection"), \
             patch("cli.DLPClient") as MockDLP:
            mock_dlp = MagicMock()
            MockDLP.return_value = mock_dlp

            mock_dlp.list_databases.return_value = [
                DatabaseInfo(
                    name="TestDB", attributes=0, version=1,
                    creation_time=0, modification_time=0, backup_time=0,
                    db_type="DATA", creator="test", num_records=1,
                ),
            ]
            mock_dlp.open_db.return_value = 1
            mock_dlp.read_open_db_info.return_value = 1
            mock_dlp.read_record.return_value = Record(
                index=0, attributes=0x40, unique_id=1, data=b"Hello",
            )
            mock_dlp.read_app_block.return_value = b""
            mock_dlp.read_sort_block.return_value = b""

            out_file = str(tmp_path / "TestDB.pdb")
            runner = CliRunner()
            result = runner.invoke(cli, ["pull", "TestDB", "--out", out_file])
            assert result.exit_code == 0

            import os
            assert os.path.exists(out_file)
