"""LeRobot v3 video frame loader — decodes mp4 videos from HuggingFace datasets.

Provides frame-level RGB access aligned with the parquet observation data.
Each episode maps to a contiguous segment within a video file; the episode
metadata provides from_timestamp/to_timestamp for seeking.

Usage:
    loader = LeRobotVideoLoader("MikeChenYZ/soarm-fmb-v2", camera="top")
    frames = loader.load_episode_frames(episode_index=0)
    # frames: list of (H, W, 3) uint8 RGB arrays
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class LeRobotVideoLoader:
    """Loads RGB frames from a lerobot v3 HuggingFace dataset's video files."""

    def __init__(
        self,
        repo_id: str,
        camera: str = "top",
        cache_dir: str | None = None,
    ) -> None:
        self.repo_id = repo_id
        self.camera = camera
        self._cache_dir = cache_dir
        self._episode_meta: list[dict[str, Any]] | None = None
        self._video_key = f"observation.images.{camera}"

    def _ensure_meta(self) -> list[dict[str, Any]]:
        if self._episode_meta is not None:
            return self._episode_meta

        import pyarrow.parquet as pq
        from huggingface_hub import HfApi, hf_hub_download

        api = HfApi()
        files = list(api.list_repo_tree(self.repo_id, repo_type="dataset", recursive=True))
        ep_files = sorted(
            f.rfilename
            for f in files
            if hasattr(f, "rfilename")
            and f.rfilename.startswith("meta/episodes/")
            and f.rfilename.endswith(".parquet")
        )

        all_episodes: list[dict[str, Any]] = []
        for ep_file in ep_files:
            path = hf_hub_download(self.repo_id, ep_file, repo_type="dataset")
            table = pq.read_table(path)
            df = table.to_pandas()
            for _, row in df.iterrows():
                vkey = f"videos/{self._video_key}"
                all_episodes.append(
                    {
                        "episode_index": int(row["episode_index"]),
                        "length": int(row["length"]),
                        "video_chunk_index": int(row[f"{vkey}/chunk_index"]),
                        "video_file_index": int(row[f"{vkey}/file_index"]),
                        "from_timestamp": float(row[f"{vkey}/from_timestamp"]),
                        "to_timestamp": float(row[f"{vkey}/to_timestamp"]),
                    }
                )

        self._episode_meta = sorted(all_episodes, key=lambda x: x["episode_index"])
        logger.info(
            "LeRobotVideoLoader: loaded meta for %d episodes from %s",
            len(self._episode_meta),
            self.repo_id,
        )
        return self._episode_meta

    def _download_video(self, chunk_index: int, file_index: int) -> str:
        from huggingface_hub import hf_hub_download

        video_path = f"videos/{self._video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
        return hf_hub_download(self.repo_id, video_path, repo_type="dataset")

    def load_episode_frames(
        self,
        episode_index: int,
        max_frames: int | None = None,
        subsample: int = 1,
    ) -> list[np.ndarray]:
        """Decode all RGB frames for one episode.

        Args:
            episode_index: Episode to load.
            max_frames: Cap number of returned frames.
            subsample: Take every Nth frame (1=all, 2=half, etc).

        Returns:
            List of (H, W, 3) uint8 RGB arrays.
        """
        import av

        meta = self._ensure_meta()
        ep_meta = None
        for m in meta:
            if m["episode_index"] == episode_index:
                ep_meta = m
                break
        if ep_meta is None:
            raise ValueError(f"Episode {episode_index} not found in {self.repo_id}")

        video_path = self._download_video(ep_meta["video_chunk_index"], ep_meta["video_file_index"])

        from_ts = ep_meta["from_timestamp"]
        expected_frames = ep_meta["length"]
        fps = 30.0

        container = av.open(video_path)
        stream = container.streams.video[0]
        if stream.average_rate is not None:
            actual_fps = float(stream.average_rate)
            if actual_fps > 0:
                fps = actual_fps

        start_frame = int(round(from_ts * fps))

        frames: list[np.ndarray] = []
        frame_count = 0
        for i, frame in enumerate(container.decode(video=0)):
            if i < start_frame:
                continue
            if frame_count >= expected_frames:
                break
            if frame_count % subsample == 0:
                img = frame.to_ndarray(format="rgb24")
                frames.append(img)
                if max_frames is not None and len(frames) >= max_frames:
                    break
            frame_count += 1

        container.close()
        return frames

    def load_frames_batch(
        self,
        episode_indices: list[int] | None = None,
        max_frames_per_episode: int | None = None,
        max_total_frames: int | None = None,
        subsample: int = 1,
    ) -> list[tuple[int, int, np.ndarray]]:
        """Load frames from multiple episodes.

        Returns:
            List of (episode_index, frame_index_within_episode, rgb_array).
        """
        meta = self._ensure_meta()
        if episode_indices is None:
            episode_indices = [m["episode_index"] for m in meta]

        results: list[tuple[int, int, np.ndarray]] = []
        for ep_idx in episode_indices:
            frames = self.load_episode_frames(
                ep_idx,
                max_frames=max_frames_per_episode,
                subsample=subsample,
            )
            for frame_idx, rgb in enumerate(frames):
                results.append((ep_idx, frame_idx * subsample, rgb))
                if max_total_frames and len(results) >= max_total_frames:
                    return results
        return results
