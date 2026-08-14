import asyncio

import state


def test_same_camera_returns_same_event():
    assert state.get_timelapse_wake(0) is state.get_timelapse_wake(0)


def test_different_cameras_are_independent():
    a, b = state.get_timelapse_wake(0), state.get_timelapse_wake(1)
    assert a is not b
    a.set()
    assert a.is_set()
    assert not b.is_set()
    a.clear()


def test_returns_asyncio_event():
    assert isinstance(state.get_timelapse_wake(3), asyncio.Event)


def test_old_shared_event_is_gone():
    """Ein verbliebener Aufrufer würde sonst still das falsche Event benutzen."""
    assert not hasattr(state, "timelapse_wake")
