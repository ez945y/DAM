"""Switch receipts remain truthful when storage or runtime ownership changes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.unit import test_safety_switching as switch_tests
from tests.unit.test_safety_switching import CONTRACT, cycle, stack

running = switch_tests.running


def unreadable(monkeypatch, path):
    original = Path.read_text

    def read(self, *args, **kwargs):
        if self == path:
            raise PermissionError("configuration unavailable")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)


def test_applied_receipt_is_retained_when_saved_file_becomes_unreadable(running, monkeypatch):
    service, runtime, _, path = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    cycle(runtime)
    unreadable(monkeypatch, path)
    status = service.status()
    row = status["history"][0]
    assert row["status"] == "applied"
    assert not row["persisted"]
    assert status["pending"] is None
    assert status["active_hash"] == preview["candidate_hash"]
    assert "unavailable" in status["saved_error"]
    assert "saving" in row["message"]


def test_unreadable_saved_baseline_blocks_preview(running, monkeypatch):
    service, _, _, path = running
    unreadable(monkeypatch, path)
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    assert preview["mode"] == "blocked"
    with pytest.raises(ValueError, match="blocking"):
        service.apply(preview["token"])


@pytest.mark.parametrize("invalid", ["[", "\\xff", "nan"])
def test_invalid_saved_configuration_has_diagnostic_and_cannot_be_overwritten(running, invalid):
    service, _, _, path = running
    if invalid == "\\xff":
        path.write_bytes(b"\xff")
    elif invalid == "nan":
        value = stack()
        value["boundaries"]["limits"]["nodes"][0]["params"]["upper"] = [float("nan"), 1]
        path.write_text(yaml.safe_dump(value))
    else:
        path.write_text(invalid)
    status = service.status()
    assert status["saved_error"]
    assert status["saved_hash"] is None
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    assert preview["mode"] == "blocked"


def test_applied_receipt_survives_runtime_replacement_without_stale_disk_write(running):
    service, runtime, control, path = running
    original = path.read_text()
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    cycle(runtime)
    control._runner = SimpleNamespace(runtime=None)
    row = service.status()["history"][0]
    assert row["status"] == "applied"
    assert not row["persisted"]
    assert path.read_text() == original
    assert "no longer active" in row["message"]


def test_later_runtime_reload_does_not_persist_superseded_candidate(running):
    from dam.config.schema import StackfileConfig

    service, runtime, _, path = running
    original = path.read_text()
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    cycle(runtime)
    runtime.apply_pending_reload(StackfileConfig.model_validate(stack(0.4)))
    cycle(runtime)
    row = service.status()["history"][0]
    assert row["status"] == "applied"
    assert not row["persisted"]
    assert path.read_text() == original
    assert "no longer active" in row["message"]


def test_replaced_runtime_cancels_unconsumed_request(running):
    service, runtime, control, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    control._runner = SimpleNamespace(runtime=None)
    status = service.status()
    assert status["pending"] is None
    assert status["history"][0]["status"] == "rejected"
    assert not runtime._hot_reload.has_pending()
    cycle(runtime)
    assert runtime._config_version == 0


def test_replaced_runtime_waits_for_inflight_receipt(running):
    service, runtime, control, _ = running
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    service.apply(preview["token"])
    runtime._hot_reload.consume_pending()
    control._runner = SimpleNamespace(runtime=None)
    assert service.status()["pending"] is not None
    runtime._hot_reload.finish_reload(False, "Preparation failed")
    status = service.status()
    assert status["pending"] is None
    assert status["history"][0]["status"] == "rejected"
    assert "Preparation failed" in status["history"][0]["message"]


def test_active_nonfinite_configuration_remains_readable_but_blocks_switch(running):
    service, runtime, _, _ = running
    runtime._reload_config.boundaries["limits"].nodes[0].params["upper"] = [float("inf"), 1]
    status = service.status()
    assert status["active_hash"] is None
    assert "infinity" in status["active_error"]
    preview = service.preview(yaml.safe_dump(stack(0.2)), "default", CONTRACT)
    assert preview["mode"] == "blocked"


def test_configuration_hash_does_not_confuse_nan_with_null():
    from dam.config.config_diff import config_hash
    from dam.config.schema import StackfileConfig

    invalid = stack()
    invalid["boundaries"]["limits"]["nodes"][0]["params"]["upper"] = [float("nan"), 1]
    with pytest.raises(ValueError, match="NaN"):
        config_hash(StackfileConfig.model_validate(invalid))
