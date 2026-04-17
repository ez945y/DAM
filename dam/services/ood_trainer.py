"""OOD Trainer Service — Fetches Hugging Face datasets and trains OOD models.

Provides utilities to pull a lerobot dataset from Hugging Face hub,
convert its episodes into DAM Observation objects, and run `OODGuard.train()`.
"""

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from dam.guard.builtin.ood import OODGuard
from dam.types.observation import Observation

logger = logging.getLogger(__name__)


class OODTrainerService:
    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            # Default to data/ood_models in project root
            import os

            project_root = os.getcwd()
            data_dir = os.path.join(project_root, "data", "ood_models")

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def train_from_hf_dataset(
        self,
        repo_id: str,
        split: str = "train",
        episodes: list[int] | None = None,
        backend: str = "memory_bank",
        output_name: str = "ood_model",
        flow_epochs: int = 50,
        flow_lr: float = 1e-3,
        progress_callback: Any | None = None,
        cancel_event: Any | None = None,
    ) -> dict[str, Any]:
        """Download a lerobot dataset, extract observations, and train an OOD model.

        Args:
            repo_id: Hugging Face repo id (e.g. "MikeChenYZ/soarm-fmb-v2")
            split: Dataset split to load
            episodes: Optional list of episode indices to load (default: all)
            backend: OOD backend ("memory_bank" or "normalizing_flow")
            output_name: Base name for the saved files.
            flow_epochs: Epochs for RealNVP if backend="normalizing_flow"
            flow_lr: Learning rate for RealNVP if backend="normalizing_flow"

        Returns:
            Dict containing the paths to the saved models and diagnostics.
        """
        try:
            import datasets
        except ImportError:
            raise ImportError("Please install datasets: pip install datasets")

        # streaming=True avoids the HuggingFace tree-listing API call that
        # causes 429 Too Many Requests on public repos like MikeChenYZ/soarm-fmb-v2.
        logger.info(f"Loading HF dataset {repo_id} (split={split}, streaming=True)...")
        if progress_callback:
            progress_callback(f"Connecting to HuggingFace: {repo_id}…")
        ds = datasets.load_dataset(repo_id, split=split, streaming=True)

        # Determine format — peek at the first item.
        state_key = "observation.state"
        first_item = next(iter(ds))
        if state_key not in first_item:
            available = list(first_item.keys())
            raise ValueError(
                f"Dataset {repo_id} does not contain '{state_key}'. Available: {available}"
            )

        # Re-create the iterator (we consumed one item above).
        ds = datasets.load_dataset(repo_id, split=split, streaming=True)

        # Episode filter: rebuild as a set for O(1) lookup.
        episode_set = set(episodes) if episodes is not None else None

        obs_list: list[Observation] = []

        logger.info("Extracting observations (streaming)…")
        if progress_callback:
            progress_callback("Streaming observations from HuggingFace…")

        for i, item in enumerate(ds):
            if cancel_event and cancel_event.is_set():
                if progress_callback:
                    progress_callback("Cancelled during extraction.")
                return {"status": "cancelled"}

            # Episode filter (streaming datasets can't use .filter() efficiently)
            if episode_set is not None:
                ep_idx = item.get("episode_index")
                if ep_idx not in episode_set:
                    continue

            if i % 200 == 0 and progress_callback:
                progress_callback(f"Extracted {len(obs_list)} observations so far…")

            state = item[state_key]
            obs = Observation(
                timestamp=item.get("timestamp", 0.0),
                joint_positions=np.array(state, dtype=np.float64),
            )
            obs_list.append(obs)

        logger.info(f"Training OOD model ({backend}) with {len(obs_list)} samples...")
        if progress_callback:
            progress_callback(
                f"Training {backend} model with {len(obs_list)} samples. This may take a while..."
            )

        guard = OODGuard(backend=backend)
        if cancel_event and cancel_event.is_set():
            return {"status": "cancelled"}

        guard.train(obs_list, flow_epochs=flow_epochs, flow_lr=flow_lr)

        if cancel_event and cancel_event.is_set():
            return {"status": "cancelled"}

        if progress_callback:
            progress_callback("Training complete. Saving model...")

        model_path = str(self.data_dir / f"{output_name}.pt")
        bank_path = str(self.data_dir / f"{output_name}.npy")

        # Depending on backend, only one might be useful, but save() handles it.
        guard.save(model_path=model_path, bank_path=bank_path)

        # Save metadata
        import json

        meta = {
            "repo_id": repo_id,
            "backend": backend,
            "samples": len(obs_list),
            "flow_epochs": flow_epochs if backend == "normalizing_flow" else 0,
            "flow_lr": flow_lr if backend == "normalizing_flow" else 0,
            "timestamp": time.time(),
        }
        meta_path = self.data_dir / f"{output_name}.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        diagnostics = guard.diagnostics()

        return {
            "status": "success",
            "model_path": model_path,
            "bank_path": bank_path,
            "metadata": meta,
            "diagnostics": diagnostics,
            "samples_processed": len(obs_list),
        }
