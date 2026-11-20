"""Companion heartbeat behavior on an authenticated socket."""

import asyncio
import socket
from unittest.mock import Mock, patch

import pytest

import pyatv
from pyatv.const import Protocol
from pyatv.protocols.companion.connection import FrameType
from pyatv.storage.memory_storage import MemoryStorage

from tests.utils import until

pytestmark = pytest.mark.asyncio


async def test_keepalive_and_cipher_stay_in_sync(companion_conf, companion_state):
    storage = MemoryStorage()
    settings = await storage.get_settings(companion_conf)
    settings.protocols.companion.keepalive_interval = 0.01
    atv = await pyatv.connect(
        companion_conf, asyncio.get_running_loop(), storage=storage
    )
    try:
        connection = atv.remote_control.get(Protocol.Companion).api._connection
        sock = connection.transport.get_extra_info("socket")
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
        await until(lambda: companion_state.noops >= 2)
        await atv.remote_control.menu()
    finally:
        await asyncio.gather(*atv.close())
    assert connection._heartbeat is None


@pytest.mark.parametrize("blocked", [False, True])
async def test_two_missed_sends_report_connection_lost(companion_client, blocked):
    connection = companion_client.remote_control.get(Protocol.Companion).api._connection
    listener = Mock()
    companion_client.listener = listener

    def tick():
        connection._cancel_heartbeat()
        connection._send_heartbeat()

    with patch.object(connection, "send", side_effect=OSError("send failed")) as send:
        if blocked:
            connection.pause_writing()
        tick()
        assert connection.connected
        assert not connection.transport.is_closing()
        tick()
        await until(lambda: listener.connection_lost.called)
        if not blocked:
            assert [c.args for c in send.call_args_list[:2]] == [
                (FrameType.NoOp, b"")
            ] * 2
    listener.connection_closed.assert_not_called()
