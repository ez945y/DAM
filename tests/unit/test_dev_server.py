"""Tests for scripts/dev_server.py helpers."""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Helpers to import _resolve_stackfile without running the server ────────────


def _load_dev_server() -> types.ModuleType:
    """Import dam_sim as a module, skipping the __main__ block."""
    project_root = Path(__file__).parent.parent.parent
    scripts_dir = str(project_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Force fresh import so env-var patches take effect
    if "dam_sim" in sys.modules:
        del sys.modules["dam_sim"]
    return importlib.import_module("dam_sim")


# ── _resolve_stackfile ─────────────────────────────────────────────────────────


class TestResolveStackfile:
    def test_fallback_to_builtin(self, tmp_path):
        """No env var and no convention file → returns _STACKFILE constant."""
        mod = _load_dev_server()
        with patch.dict(os.environ, {"DAM_STACKFILE_PATH": ""}, clear=False):
            # Patch the helper to look in tmp_path (no .dam_stackfile.yaml there)
            original_abspath = os.path.abspath

            def fake_abspath(p):
                if p == __file__:
                    return p
                return original_abspath(p)

            # Ensure the convention path doesn't exist by using a temp project root
            with patch("dam_sim.os.path.dirname", side_effect=lambda p: str(tmp_path)):
                result = mod._resolve_stackfile()
        assert isinstance(result, str)
        assert len(result) > 10  # non-trivial built-in YAML

    def test_env_var_takes_priority(self, tmp_path):
        """DAM_STACKFILE_PATH wins over convention file and built-in."""
        custom_yaml = "# custom stackfile\nversion: test\n"
        env_file = tmp_path / "custom.yaml"
        env_file.write_text(custom_yaml)

        convention_file = tmp_path / ".dam_stackfile.yaml"
        convention_file.write_text("# convention file\n")

        mod = _load_dev_server()
        with patch.dict(os.environ, {"DAM_STACKFILE_PATH": str(env_file)}):
            result = mod._resolve_stackfile()
        assert result == custom_yaml

    def test_convention_file_preferred_over_builtin(self, tmp_path):
        """Convention .dam_stackfile.yaml beats the built-in default."""
        convention_yaml = "# user convention file\nversion: user\n"
        convention_file = tmp_path / ".dam_stackfile.yaml"
        convention_file.write_text(convention_yaml)

        mod = _load_dev_server()
        with (
            patch.dict(os.environ, {"DAM_STACKFILE_PATH": ""}, clear=False),
            patch("dam_sim.os.path.dirname", side_effect=lambda p: str(tmp_path)),
        ):
            result = mod._resolve_stackfile()
        assert result == convention_yaml

    def test_env_var_missing_file_falls_through(self, tmp_path):
        """If DAM_STACKFILE_PATH points to a non-existent file, fall through."""
        mod = _load_dev_server()
        with (
            patch.dict(os.environ, {"DAM_STACKFILE_PATH": "/nonexistent/path.yaml"}),
            patch("dam_sim.os.path.dirname", side_effect=lambda p: str(tmp_path)),
        ):
            result = mod._resolve_stackfile()
        # Should return built-in (no convention file in tmp_path)
        assert result == mod._STACKFILE


# ── _SimSink ───────────────────────────────────────────────────────────────────

# ── _SimSink removed (now handled by adapter factory) ──────────────────────────


# ── _hardware_sources ──────────────────────────────────────────────────────────


class TestHardwareSources:
    def test_empty_yaml_returns_empty(self):
        mod = _load_dev_server()
        assert mod._hardware_sources("version: '1'\n") == []

    def test_no_hardware_key_returns_empty(self):
        mod = _load_dev_server()
        yaml = "guards:\n  - L0: ood\n"
        assert mod._hardware_sources(yaml) == []

    def test_extracts_sources(self):
        mod = _load_dev_server()
        yaml = "hardware:\n  sources:\n    arm:\n      type: lerobot\n      port: /dev/ttyUSB0\n"
        sources = mod._hardware_sources(yaml)
        assert len(sources) == 1
        assert sources[0]["type"] == "lerobot"
        assert sources[0]["port"] == "/dev/ttyUSB0"

    def test_invalid_yaml_returns_empty(self):
        mod = _load_dev_server()
        assert mod._hardware_sources("}{invalid}{\n") == []


# ── _validate_hardware ─────────────────────────────────────────────────────────


class TestValidateHardware:
    def test_empty_sources_passes(self):
        mod = _load_dev_server()
        mod._validate_hardware([])  # should not raise

    def test_simulation_source_skipped(self):
        mod = _load_dev_server()
        mod._validate_hardware([{"type": "simulation"}])  # no error

    def test_lerobot_no_port_raises(self):
        mod = _load_dev_server()
        with pytest.raises(RuntimeError, match="no 'port' configured"):
            mod._validate_hardware([{"type": "lerobot"}])

    def test_lerobot_bad_port_raises(self):
        mod = _load_dev_server()
        # Use a Linux-style port so we bypass the macOS→Linux mapping and hit
        # pyserial directly. In CI (no pyserial) → "pyserial is not installed";
        # on a machine with pyserial but no such device → "not accessible".
        # Either is an acceptable error.
        with pytest.raises(RuntimeError, match="Serial port"):
            mod._validate_hardware(
                [{"type": "lerobot", "port": "/dev/ttyACM99_nonexistent_dam_test"}]
            )

    def test_ros2_without_rclpy_raises(self):
        mod = _load_dev_server()
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "rclpy":
                raise ImportError("No module named 'rclpy'")
            return real_import(name, *args, **kwargs)

        with (
            patch.object(builtins, "__import__", side_effect=_fake_import),
            pytest.raises(RuntimeError, match="rclpy is not installed"),
        ):
            mod._validate_hardware([{"type": "ros2"}])

    def test_multiple_bad_sources_all_reported(self):
        mod = _load_dev_server()
        # Use Linux-style ports to bypass macOS→Linux mapping and reach pyserial
        sources = [
            {"type": "lerobot", "port": "/dev/ttyACM_bad1"},
            {"type": "lerobot", "port": "/dev/ttyACM_bad2"},
        ]
        with pytest.raises(RuntimeError) as exc_info:
            mod._validate_hardware(sources)
        msg = str(exc_info.value)
        assert "component(s) unreachable" in msg  # literal string check, no regex

    def test_lerobot_port_accessible_passes(self, tmp_path):
        """If serial.Serial can open the port, no error is raised."""
        mod = _load_dev_server()
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        mock_serial_instance = MagicMock()
        mock_serial_class = MagicMock(return_value=mock_serial_instance)
        mock_serial_class.__enter__ = lambda s: mock_serial_instance
        mock_serial_class.__exit__ = MagicMock(return_value=False)

        mock_serial_module = MagicMock()
        mock_serial_module.Serial = mock_serial_class

        with mock_patch.dict("sys.modules", {"serial": mock_serial_module}):
            # Should not raise — port "opens" without error
            mod._validate_hardware([{"type": "lerobot", "port": "/dev/ttyACM0"}])

    # ── macOS native (running directly on macOS, not in a container) ─────────────

    def test_macos_port_returned_as_is_on_macos(self):
        """On macOS the /dev/tty.usbmodem* path is valid — return it unchanged."""
        mod = _load_dev_server()
        with patch("sys.platform", "darwin"):
            result = mod._resolve_linux_port("/dev/tty.usbmodem5AA90244141")
        assert result == "/dev/tty.usbmodem5AA90244141"

    def test_macos_cu_port_returned_as_is_on_macos(self):
        """On macOS the /dev/cu.* path is valid — return it unchanged."""
        mod = _load_dev_server()
        with patch("sys.platform", "darwin"):
            result = mod._resolve_linux_port("/dev/cu.usbserial-1234")
        assert result == "/dev/cu.usbserial-1234"

    # ── macOS → Linux port mapping (inside a Linux container) ─────────────────

    def test_macos_port_no_linux_candidate_reports_error(self):
        """In a Linux container: macOS /dev/tty.usbmodem* with no /dev/ttyACM* → error."""
        mod = _load_dev_server()
        with patch("sys.platform", "linux"), patch("glob.glob", return_value=[]):
            errors: list = []
            mod._check_serial_port("/dev/tty.usbmodem5AA90244141", errors)
        assert any("ttyACM" in e or "ttyUSB" in e for e in errors)

    def test_macos_port_mapped_to_linux_candidate(self):
        """In a Linux container: macOS port is mapped to the first /dev/ttyACM*."""
        mod = _load_dev_server()
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        mock_serial = MagicMock()
        mock_serial.Serial.return_value.__enter__ = lambda s: s
        mock_serial.Serial.return_value.__exit__ = MagicMock(return_value=False)

        with (
            mock_patch("sys.platform", "linux"),
            mock_patch("glob.glob", side_effect=lambda p: ["/dev/ttyACM0"] if "ACM" in p else []),
            mock_patch.dict("sys.modules", {"serial": mock_serial}),
        ):
            errors: list = []
            mod._check_serial_port("/dev/tty.usbmodem5AA90244141", errors)
        assert errors == []
        mock_serial.Serial.assert_called_once_with("/dev/ttyACM0", timeout=0.5)

    def test_linux_port_passed_through_unchanged(self):
        """Linux-native /dev/ttyACM0 is not remapped."""
        mod = _load_dev_server()
        result = mod._resolve_linux_port("/dev/ttyACM0")
        assert result == "/dev/ttyACM0"

    def test_linux_usb_port_passed_through_unchanged(self):
        """Linux-native /dev/ttyUSB0 is not remapped."""
        mod = _load_dev_server()
        result = mod._resolve_linux_port("/dev/ttyUSB0")
        assert result == "/dev/ttyUSB0"

    def test_macos_cu_port_triggers_mapping_on_linux(self):
        """In a Linux container: /dev/cu.* triggers mapping to /dev/ttyUSB*."""
        mod = _load_dev_server()
        with (
            patch("sys.platform", "linux"),
            patch("glob.glob", return_value=["/dev/ttyUSB0"]),
        ):
            result = mod._resolve_linux_port("/dev/cu.usbserial-1234")
        assert result == "/dev/ttyUSB0"

    def test_macos_port_no_candidates_returns_none_on_linux(self):
        """In a Linux container: /dev/tty.usbmodem* with no Linux devices → None."""
        mod = _load_dev_server()
        with patch("sys.platform", "linux"), patch("glob.glob", return_value=[]):
            result = mod._resolve_linux_port("/dev/tty.usbmodem1234")
        assert result is None

    # ── Camera validation ──────────────────────────────────────────────────────

    def test_camera_no_cv2_reports_error(self):
        mod = _load_dev_server()
        import builtins

        real_import = builtins.__import__

        def _fake(name, *a, **kw):
            if name == "cv2":
                raise ImportError("No module named 'cv2'")
            return real_import(name, *a, **kw)

        errors: list = []
        with patch.object(builtins, "__import__", side_effect=_fake):
            mod._check_cameras({"top": {"index_or_path": 0}}, errors)
        assert any("cv2" in e for e in errors)

    def test_camera_no_index_reports_error(self):
        mod = _load_dev_server()
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        mock_cv2 = MagicMock()
        errors: list = []
        with mock_patch.dict("sys.modules", {"cv2": mock_cv2}):
            mod._check_cameras({"bad_cam": {}}, errors)
        assert any("no index_or_path" in e for e in errors)

    def test_camera_cannot_open_reports_error(self):
        mod = _load_dev_server()
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv2 = MagicMock()
        mock_cv2.VideoCapture.return_value = mock_cap
        errors: list = []
        with mock_patch.dict("sys.modules", {"cv2": mock_cv2}):
            mod._check_cameras({"top": {"index_or_path": 0}}, errors)
        assert any("could not open" in e for e in errors)

    def test_camera_ok_no_error(self):
        mod = _load_dev_server()
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cv2 = MagicMock()
        mock_cv2.VideoCapture.return_value = mock_cap
        errors: list = []
        with mock_patch.dict("sys.modules", {"cv2": mock_cv2}):
            mod._check_cameras({"top": {"index_or_path": 0}}, errors)
        assert errors == []

    def test_lerobot_with_bad_camera_raises(self):
        """Serial OK but camera fails → RuntimeError lists both issues."""
        mod = _load_dev_server()
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        mock_serial = MagicMock()
        mock_serial.Serial.return_value.__enter__ = lambda s: s
        mock_serial.Serial.return_value.__exit__ = MagicMock(return_value=False)

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv2 = MagicMock()
        mock_cv2.VideoCapture.return_value = mock_cap

        src = {"type": "lerobot", "port": "/dev/ttyACM0", "cameras": {"top": {"index_or_path": 99}}}

        with (
            mock_patch.dict("sys.modules", {"serial": mock_serial, "cv2": mock_cv2}),
            pytest.raises(RuntimeError, match=r"component\(s\) unreachable"),
        ):
            mod._validate_hardware([src])


# ── perception_ood removed ─────────────────────────────────────────────────────


class TestPerceptionOodRemoved:
    """Verify the stub perception_ood callback no longer exists."""

    @pytest.fixture(autouse=True)
    def _fresh_registry(self):
        """Use a clean registry per test to avoid duplicate-register errors."""
        from dam.registry import callback as cb_mod

        old = cb_mod._registry
        cb_mod._registry = cb_mod.CallbackRegistry()
        yield
        cb_mod._registry = old

    def test_perception_ood_not_registered(self):
        from dam.boundary.builtin_callbacks import register_all
        from dam.registry.callback import get_global_registry

        register_all()
        names = get_global_registry().list_all()
        assert "perception_ood" not in names, (
            "perception_ood stub was re-added — it should be deleted entirely"
        )

    def test_ood_detector_registered(self):
        from dam.boundary.builtin_callbacks import register_all
        from dam.registry.callback import get_global_registry

        register_all()
        names = get_global_registry().list_all()
        assert "ood_detector" in names
