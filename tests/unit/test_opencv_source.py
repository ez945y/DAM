from __future__ import annotations

import time

import pytest

pytest.importorskip("cv2")

from dam.adapter.opencv.source import OpenCVSourceAdapter


class _FailingCapture:
    def __init__(self, index: int | str) -> None:
        self.index = index
        self.released = False

    def isOpened(self) -> bool:
        return not self.released

    def set(self, _prop: int, _value: float) -> bool:
        return True

    def read(self):
        return False, None

    def release(self) -> None:
        self.released = True


def test_opencv_source_stops_after_consecutive_capture_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("dam.adapter.opencv.source.cv2.VideoCapture", _FailingCapture)
    src = OpenCVSourceAdapter(0, name="wrist", max_consecutive_failures=2)

    src.connect()
    deadline = time.monotonic() + 1.0
    while src.is_healthy() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not src.is_healthy()
    with pytest.raises(RuntimeError, match="wrist.*disconnected"):
        src.read()
