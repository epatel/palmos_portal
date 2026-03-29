from unittest.mock import MagicMock, patch, PropertyMock
import array


def _make_mock_device():
    """Create a mock USB device with endpoints."""
    mock_dev = MagicMock()
    mock_cfg = MagicMock()
    mock_intf = MagicMock()

    mock_ep_in = MagicMock()
    mock_ep_in.bEndpointAddress = 0x82
    type(mock_ep_in).wMaxPacketSize = PropertyMock(return_value=64)
    mock_ep_out = MagicMock()
    mock_ep_out.bEndpointAddress = 0x02
    type(mock_ep_out).wMaxPacketSize = PropertyMock(return_value=64)

    mock_intf.__iter__ = lambda self: iter([mock_ep_in, mock_ep_out])
    mock_cfg.__getitem__ = lambda self, key: mock_intf
    mock_dev.get_active_configuration.return_value = mock_cfg

    return mock_dev, mock_ep_in, mock_ep_out


class TestConnectionOpenClose:
    @patch("palm.transport.usb.core.find")
    def test_open_finds_device(self, mock_find):
        from palm.transport import Connection

        mock_dev, _, _ = _make_mock_device()
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

        mock_dev, _, _ = _make_mock_device()
        mock_find.return_value = mock_dev

        with Connection() as conn:
            assert conn._dev is not None

        mock_dev.reset.assert_called_once()

    @patch("palm.transport.usb.core.find")
    def test_selects_largest_endpoints(self, mock_find):
        """Visor has 2 endpoint pairs; we should pick the 64-byte ones."""
        from palm.transport import Connection

        mock_dev = MagicMock()
        mock_cfg = MagicMock()
        mock_intf = MagicMock()

        # Small endpoints (16 bytes)
        ep_in_small = MagicMock()
        ep_in_small.bEndpointAddress = 0x81
        type(ep_in_small).wMaxPacketSize = PropertyMock(return_value=16)
        ep_out_small = MagicMock()
        ep_out_small.bEndpointAddress = 0x01
        type(ep_out_small).wMaxPacketSize = PropertyMock(return_value=16)

        # Large endpoints (64 bytes)
        ep_in_large = MagicMock()
        ep_in_large.bEndpointAddress = 0x82
        type(ep_in_large).wMaxPacketSize = PropertyMock(return_value=64)
        ep_out_large = MagicMock()
        ep_out_large.bEndpointAddress = 0x02
        type(ep_out_large).wMaxPacketSize = PropertyMock(return_value=64)

        mock_intf.__iter__ = lambda self: iter([ep_in_small, ep_out_small, ep_in_large, ep_out_large])
        mock_cfg.__getitem__ = lambda self, key: mock_intf
        mock_dev.get_active_configuration.return_value = mock_cfg
        mock_find.return_value = mock_dev

        conn = Connection()
        conn.open()
        assert conn._ep_in.bEndpointAddress == 0x82
        assert conn._ep_out.bEndpointAddress == 0x02


class TestConnectionReadWrite:
    def test_write_sends_to_endpoint(self):
        from palm.transport import Connection

        conn = Connection()
        conn._ep_out = MagicMock()
        conn.write(b"\x01\x02\x03")
        conn._ep_out.write.assert_called_once_with(b"\x01\x02\x03")

    def test_read_from_endpoint(self):
        from palm.transport import Connection

        conn = Connection()
        conn._ep_in = MagicMock()
        type(conn._ep_in).wMaxPacketSize = PropertyMock(return_value=64)
        conn._ep_in.read.return_value = array.array("B", b"\xAA\xBB\xCC")
        result = conn.read(3)
        assert result == b"\xAA\xBB\xCC"

    def test_read_buffers_excess(self):
        """If USB returns more bytes than requested, buffer the rest."""
        from palm.transport import Connection

        conn = Connection()
        conn._ep_in = MagicMock()
        type(conn._ep_in).wMaxPacketSize = PropertyMock(return_value=64)
        conn._ep_in.read.return_value = array.array("B", b"\x01\x02\x03\x04\x05")

        result1 = conn.read(2)
        assert result1 == b"\x01\x02"

        # Second read should come from buffer, not USB
        result2 = conn.read(2)
        assert result2 == b"\x03\x04"
        assert conn._ep_in.read.call_count == 1  # Only one USB read
