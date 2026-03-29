from unittest.mock import MagicMock, patch, PropertyMock
import struct
import array as _array

_CMP_INIT_PACKET = _array.array("B", struct.pack(">BBBBHI", 1, 0x80, 1, 0, 0, 57600))


class TestConnectionOpenClose:
    @patch("palm.transport.usb.core.find")
    def test_open_finds_device(self, mock_find):
        from palm.transport import Connection

        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_intf = MagicMock()

        mock_ep_in = MagicMock()
        mock_ep_in.bEndpointAddress = 0x82
        type(mock_ep_in).wMaxPacketSize = PropertyMock(return_value=64)
        mock_ep_out = MagicMock()
        mock_ep_out.bEndpointAddress = 0x02

        mock_intf.__iter__ = lambda self: iter([mock_ep_in, mock_ep_out])
        mock_cfg.__getitem__ = lambda self, key: mock_intf
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_ep_in.read.return_value = _CMP_INIT_PACKET

        mock_find.return_value = mock_dev

        conn = Connection()
        conn.open()
        mock_find.assert_called_once_with(idVendor=0x082D, idProduct=0x0100)
        assert conn._dev is not None

    @patch("palm.transport.usb.core.find")
    def test_open_no_device_raises(self, mock_find):
        import pytest
        from palm.transport import Connection

        mock_find.return_value = None
        conn = Connection()
        with pytest.raises(ConnectionError, match="No Handspring Visor found"):
            conn.open()

    @patch("palm.transport.usb.core.find")
    def test_context_manager(self, mock_find):
        from palm.transport import Connection

        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_intf = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.bEndpointAddress = 0x82
        type(mock_ep_in).wMaxPacketSize = PropertyMock(return_value=64)
        mock_ep_out = MagicMock()
        mock_ep_out.bEndpointAddress = 0x02
        mock_intf.__iter__ = lambda self: iter([mock_ep_in, mock_ep_out])
        mock_cfg.__getitem__ = lambda self, key: mock_intf
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_ep_in.read.return_value = _CMP_INIT_PACKET

        mock_find.return_value = mock_dev

        with Connection() as conn:
            assert conn._dev is not None

        mock_dev.reset.assert_called_once()


class TestConnectionReadWrite:
    def test_write_sends_to_endpoint(self):
        from palm.transport import Connection

        conn = Connection()
        conn._ep_out = MagicMock()
        conn.write(b"\x01\x02\x03")
        conn._ep_out.write.assert_called_once_with(b"\x01\x02\x03")

    def test_read_from_endpoint(self):
        from palm.transport import Connection
        import array

        conn = Connection()
        conn._ep_in = MagicMock()
        conn._ep_in.read.return_value = array.array("B", b"\xAA\xBB\xCC")
        result = conn.read(3)
        assert result == b"\xAA\xBB\xCC"


class TestCMPHandshake:
    @patch("palm.transport.usb.core.find")
    def test_cmp_wakeup_and_response(self, mock_find):
        from palm.transport import Connection

        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_intf = MagicMock()
        mock_ep_in = MagicMock()
        mock_ep_in.bEndpointAddress = 0x82
        type(mock_ep_in).wMaxPacketSize = PropertyMock(return_value=64)
        mock_ep_out = MagicMock()
        mock_ep_out.bEndpointAddress = 0x02
        mock_intf.__iter__ = lambda self: iter([mock_ep_in, mock_ep_out])
        mock_cfg.__getitem__ = lambda self, key: mock_intf
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_find.return_value = mock_dev

        import array
        cmp_init = struct.pack(">BBBBHI", 1, 0x80, 1, 0, 0, 57600)
        mock_ep_in.read.return_value = array.array("B", cmp_init)

        conn = Connection()
        conn.open()

        assert mock_ep_in.read.called
        assert mock_ep_out.write.called
