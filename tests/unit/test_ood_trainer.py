"""Tests for OOD Trainer service."""

from unittest.mock import MagicMock, patch

import pytest

from dam.services.ood_trainer import OODTrainerService


def test_ood_trainer_requires_datasets():
    trainer = OODTrainerService("/tmp/fake_dir")

    with (
        patch.dict("sys.modules", {"datasets": None}),
        pytest.raises(ImportError, match="pip install datasets"),
    ):
        trainer.train_from_hf_dataset("MikeChenYZ/soarm-fmb-v2")


@patch("dam.services.ood_trainer.OODGuard")
def test_ood_trainer_success(mock_guard_class, tmp_path):
    trainer = OODTrainerService(str(tmp_path))

    # Mock dataset item
    mock_ds = MagicMock()
    mock_ds.features = {"observation.state": {}}
    mock_item = {"observation.state": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6], "timestamp": 1.23}
    mock_ds.__iter__.return_value = [mock_item, mock_item, mock_item]
    mock_ds.__len__.return_value = 3

    mock_datasets = MagicMock()
    mock_datasets.load_dataset.return_value = mock_ds

    with patch.dict("sys.modules", {"datasets": mock_datasets}):
        # Mock Guard
        mock_guard = MagicMock()
        mock_guard.diagnostics.return_value = {"backend": "memory_bank"}
        mock_guard_class.return_value = mock_guard

        result = trainer.train_from_hf_dataset(
            repo_id="lerobot/test", backend="memory_bank", output_name="test_ood_model"
        )

        assert result["status"] == "success"
        assert result["samples_processed"] == 3
        assert result["diagnostics"]["backend"] == "memory_bank"

        mock_guard.train.assert_called_once()
        mock_guard.save.assert_called_once()
        args, kwargs = mock_guard.save.call_args
        assert "test_ood_model.pt" in kwargs["model_path"]
        assert "test_ood_model.npy" in kwargs["bank_path"]
