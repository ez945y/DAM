"""Recording failures must cross the worker boundary and permit fresh recovery."""

import pytest
from dam_rs import McapWriter
from mcap.reader import make_reader


def test_background_open_failure_is_reported_on_close(tmp_path):
    writer = McapWriter()
    writer.start(str(tmp_path / "missing" / "session.mcap"))
    with pytest.raises(OSError, match="create file"):
        writer.stop()


def test_close_waits_for_footer_and_worker_completion(tmp_path):
    path = tmp_path / "complete.mcap"
    writer = McapWriter()
    writer.start(str(path))
    writer.stop()
    with path.open("rb") as handle:
        assert make_reader(handle).get_summary() is not None
    writer.stop()  # idempotent successful close


def test_failed_writer_can_be_replaced_with_fresh_writer(tmp_path):
    writer = McapWriter()
    writer.start(str(tmp_path / "missing" / "session.mcap"))
    with pytest.raises(OSError):
        writer.stop()
    replacement = McapWriter()
    path = tmp_path / "recovered.mcap"
    replacement.start(str(path))
    replacement.stop()
    with path.open("rb") as handle:
        assert make_reader(handle).get_summary() is not None


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 1e300])
def test_invalid_stop_deadline_is_rejected_without_losing_worker(timeout):
    writer = McapWriter()
    try:
        with pytest.raises(ValueError):
            writer.stop(timeout=timeout)
    finally:
        writer.stop()
