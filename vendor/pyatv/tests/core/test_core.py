"""Unit tests for pyatv.core."""

import asyncio
from unittest.mock import ANY, MagicMock

import pytest

from pyatv.const import Protocol
from pyatv.core import (
    AbstractPushUpdater,
    CoreStateDispatcher,
    ProtocolStateDispatcher,
    StateMessage,
    UpdatedState,
)
from pyatv.interface import Playing


@pytest.fixture(name="state_dispatcher")
def state_dispatcher_fixture():
    core_dispatcher = CoreStateDispatcher()
    yield ProtocolStateDispatcher(Protocol.MRP, core_dispatcher)


class PushUpdaterDummy(AbstractPushUpdater):
    def active(self) -> bool:
        """Return if push updater has been started."""
        raise exceptions.NotSupportedError()

    def start(self, initial_delay: int = 0) -> None:
        """Begin to listen to updates.

        If an error occurs, start must be called again.
        """
        raise exceptions.NotSupportedError()

    def stop(self) -> None:
        """No longer forward updates to listener."""
        raise exceptions.NotSupportedError()


@pytest.mark.parametrize("updates", [1, 2, 3])
@pytest.mark.asyncio
async def test_post_ignore_duplicate_update(state_dispatcher, updates):
    listener = MagicMock()
    playing = Playing()

    def _state_changed(message):
        assert message.protocol == Protocol.MRP
        assert message.state == UpdatedState.Playing
        listener.state_updated(message.value)

    updater = PushUpdaterDummy(state_dispatcher)
    updater.listener = listener
    state_dispatcher.listen_to(UpdatedState.Playing, _state_changed)
    for _ in range(updates):
        updater.post_update(playing)
    await asyncio.sleep(0)

    assert listener.playstatus_update.call_count == 1
    listener.playstatus_update.assert_called_once_with(ANY, playing)
    listener.state_updated.assert_called_once_with(playing)
