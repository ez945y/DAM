"""Unit tests for ActionBus (Rust extension via dam.bus)."""

from __future__ import annotations

from dam.bus import ActionBus


def test_write_read():
    """write bytes, read returns them."""
    bus = ActionBus(capacity=1)
    bus.write(b"hello")
    result = bus.read()
    assert result == b"hello"


def test_empty_returns_none():
    """read from empty → None."""
    bus = ActionBus(capacity=1)
    assert bus.read() is None


def test_overwrite():
    """write twice at capacity=1, read returns second (latest-wins)."""
    bus = ActionBus(capacity=1)
    bus.write(b"first")
    bus.write(b"second")
    result = bus.read()
    assert result == b"second"


def test_read_removes():
    """write, read, read → second read is None."""
    bus = ActionBus(capacity=1)
    bus.write(b"data")
    first = bus.read()
    assert first == b"data"
    second = bus.read()
    assert second is None


def test_is_empty():
    """write then read, then is_empty() → True."""
    bus = ActionBus(capacity=1)
    assert bus.is_empty()
    bus.write(b"x")
    assert not bus.is_empty()
    bus.read()
    assert bus.is_empty()


def test_larger_capacity():
    """capacity > 1 retains order."""
    bus = ActionBus(capacity=3)
    for i in range(3):
        bus.write(bytes([i]))
    for i in range(3):
        assert bus.read() == bytes([i])
    assert bus.is_empty()
