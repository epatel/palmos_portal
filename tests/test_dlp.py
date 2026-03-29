import struct


class TestDLPRequestBuild:
    def test_build_simple_request_no_args(self):
        from palm.dlp import DLPClient

        raw = DLPClient.build_request(func_id=0x2E, args=[])  # OpenConduit
        assert raw[0] == 0x2E  # function ID
        assert raw[1] == 0  # arg count
        assert raw[2] == 0  # error code (0 in requests)
        assert len(raw) == 3

    def test_build_request_with_small_arg(self):
        from palm.dlp import DLPClient, DLPArg

        arg = DLPArg(arg_id=0x20, data=b"\x01\x02")
        raw = DLPClient.build_request(func_id=0x12, args=[arg])
        assert raw[0] == 0x12
        assert raw[1] == 1
        assert raw[3] == 0x20  # arg ID
        assert raw[4] == 2  # size
        assert raw[5:7] == b"\x01\x02"

    def test_build_request_with_long_arg(self):
        from palm.dlp import DLPClient, DLPArg

        big_data = b"\xAA" * 300
        arg = DLPArg(arg_id=0x20, data=big_data)
        raw = DLPClient.build_request(func_id=0x17, args=[arg])
        assert raw[0] == 0x17
        assert raw[1] == 1


class TestDLPResponseParse:
    def test_parse_success_response_no_args(self):
        from palm.dlp import DLPClient

        raw = struct.pack(">BBH", 0x2E | 0x80, 0, 0x0000)
        func_id, error_code, args = DLPClient.parse_response(raw)
        assert func_id == 0x2E
        assert error_code == 0
        assert len(args) == 0

    def test_parse_error_response(self):
        from palm.dlp import DLPClient

        raw = struct.pack(">BBH", 0x17 | 0x80, 0, 0x0005)
        func_id, error_code, args = DLPClient.parse_response(raw)
        assert func_id == 0x17
        assert error_code == 5

    def test_parse_response_with_arg(self):
        from palm.dlp import DLPClient

        arg_data = b"\x03\x00\x01\x00"
        arg_bytes = bytes([0x20, 4]) + arg_data
        raw = struct.pack(">BBH", 0x12 | 0x80, 1, 0x0000) + arg_bytes
        func_id, error_code, args = DLPClient.parse_response(raw)
        assert func_id == 0x12
        assert error_code == 0
        assert len(args) == 1
        assert args[0].data == arg_data


class TestDLPReadSysInfo:
    def test_read_sys_info_request_encoding(self):
        from palm.dlp import DLPClient

        raw = DLPClient.build_request(func_id=0x12, args=[])
        assert raw[0] == 0x12
        assert raw[1] == 0

    def test_parse_sys_info_response(self):
        from palm.dlp import parse_sys_info

        rom_version = struct.pack(">I", 0x03503000)
        locale = struct.pack(">I", 0)
        name = b"Visor\x00"
        arg_data = rom_version + locale + name

        info = parse_sys_info(arg_data)
        assert info.rom_version == 0x03503000
        assert info.name == "Visor"


class TestDLPReadDBList:
    def test_build_read_db_list_request(self):
        from palm.dlp import DLPClient, DLPArg

        flags = 0x80
        arg = DLPArg(arg_id=0x20, data=struct.pack(">BBH", flags, 0, 0))
        raw = DLPClient.build_request(func_id=0x16, args=[arg])
        assert raw[0] == 0x16
        assert raw[1] == 1


class TestDLPDatabaseOps:
    def test_build_open_db_request(self):
        from palm.dlp import DLPClient, DLPArg

        arg_data = struct.pack(">BB", 0, 0x80) + b"MemoDB\x00"
        arg = DLPArg(arg_id=0x20, data=arg_data)
        raw = DLPClient.build_request(func_id=0x17, args=[arg])
        assert raw[0] == 0x17

    def test_build_end_of_sync_request(self):
        from palm.dlp import DLPClient, DLPArg

        arg = DLPArg(arg_id=0x20, data=struct.pack(">H", 0))
        raw = DLPClient.build_request(func_id=0x2F, args=[arg])
        assert raw[0] == 0x2F
