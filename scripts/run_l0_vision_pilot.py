"""Pilot: Vision-based L0 OOD — quick feasibility check.

Loads a small sample of video frames from normal and abnormal HF datasets,
extracts MobileNetV3 features, trains Real-NVP on normal features, then
compares NLL distributions. This validates the approach before full integration.

Usage:
    python scripts/run_l0_vision_pilot.py [--samples 200] [--model mobilenet_v3_large]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="L0 Vision OOD Pilot")
    parser.add_argument("--samples", type=int, default=200, help="Max frames per dataset")
    parser.add_argument("--subsample", type=int, default=5, help="Take every Nth frame")
    parser.add_argument("--model", type=str, default="mobilenet_v3_large")
    parser.add_argument("--flow-epochs", type=int, default=30)
    parser.add_argument("--normal-repo", type=str, default="MikeChenYZ/soarm-fmb-v2")
    parser.add_argument("--anomaly-repo", type=str, default="MikeChenYZ/soarm-recover-failure")
    parser.add_argument("--legal-repo", type=str, default="MikeChenYZ/eval_soarm_fmb")
    parser.add_argument("--camera", type=str, default="top")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    from dam.guard.lerobot_video_loader import LeRobotVideoLoader
    from dam.guard.vision_feature_extractor import (
        VisionFeatureExtractor,
        VisionFeatureExtractorConfig,
    )

    # --- Step 1: Load frames ---
    print(f"\n{'=' * 60}")
    print(f"L0 Vision OOD Pilot — model={args.model} camera={args.camera}")
    print(f"{'=' * 60}\n")

    # Load normal dataset — sample uniformly across episodes for coverage
    print(f"Loading normal frames from {args.normal_repo} (camera={args.camera})...")
    t0 = time.perf_counter()
    normal_loader = LeRobotVideoLoader(args.normal_repo, camera=args.camera)
    normal_meta = normal_loader._ensure_meta()
    n_episodes = len(normal_meta)
    n_train_eps = max(1, int(n_episodes * 0.7))
    train_episode_ids = [m["episode_index"] for m in normal_meta[:n_train_eps]]
    test_episode_ids = [m["episode_index"] for m in normal_meta[n_train_eps:]]
    print(
        f"  Episodes: {n_episodes} total, train={len(train_episode_ids)}, test={len(test_episode_ids)}"
    )

    # Sample frames across ALL train episodes (not just first few)
    frames_per_train_ep = max(1, args.samples // len(train_episode_ids))
    # Subsample heavily within each episode to get diversity
    ep_subsample = max(args.subsample, 30)  # at least every 30th frame (1 per second at 30fps)
    normal_train_frames = normal_loader.load_frames_batch(
        episode_indices=train_episode_ids,
        max_frames_per_episode=frames_per_train_ep,
        max_total_frames=args.samples,
        subsample=ep_subsample,
    )
    frames_per_test_ep = max(1, (args.samples // 2) // max(len(test_episode_ids), 1))
    normal_test_frames = normal_loader.load_frames_batch(
        episode_indices=test_episode_ids,
        max_frames_per_episode=frames_per_test_ep,
        max_total_frames=args.samples // 2,
        subsample=ep_subsample,
    )
    elapsed = time.perf_counter() - t0
    print(f"  Train frames: {len(normal_train_frames)} (from {len(train_episode_ids)} eps)")
    print(
        f"  Test frames: {len(normal_test_frames)} (from {len(test_episode_ids)} eps) ({elapsed:.1f}s)"
    )

    # Load other datasets — also sample across episodes
    all_frames: dict[str, list[tuple[int, int, np.ndarray]]] = {
        "normal_train": normal_train_frames,
        "normal_test": normal_test_frames,
    }
    for label, repo_id in [("legal_variation", args.legal_repo), ("abnormal", args.anomaly_repo)]:
        print(f"Loading {label} frames from {repo_id}...")
        t0 = time.perf_counter()
        loader = LeRobotVideoLoader(repo_id, camera=args.camera)
        meta = loader._ensure_meta()
        frames_per_ep = max(1, args.samples // max(len(meta), 1))
        frames = loader.load_frames_batch(
            max_frames_per_episode=frames_per_ep,
            max_total_frames=args.samples,
            subsample=ep_subsample,
        )
        elapsed = time.perf_counter() - t0
        all_frames[label] = frames
        print(f"  Loaded {len(frames)} frames from {len(meta)} episodes in {elapsed:.1f}s")

    # --- Step 2: Extract features ---
    print(f"\nInitializing vision model: {args.model}...")
    cfg = VisionFeatureExtractorConfig(model_name=args.model, device="cpu")
    extractor = VisionFeatureExtractor(cfg)

    features: dict[str, np.ndarray] = {}
    for label, frame_list in all_frames.items():
        print(f"Extracting features for {label} ({len(frame_list)} frames)...")
        t0 = time.perf_counter()
        batch_features = []
        images = [f[2] for f in frame_list]

        for i in range(0, len(images), args.batch_size):
            batch = np.stack(images[i : i + args.batch_size], axis=0)
            feat = extractor.extract(batch)
            batch_features.append(feat)

        features[label] = np.concatenate(batch_features, axis=0)
        elapsed = time.perf_counter() - t0
        print(f"  Shape: {features[label].shape}, took {elapsed:.1f}s")

    # --- Step 3: Dimensionality reduction (PCA) ---
    from scipy.linalg import svd

    train_feat = features["normal_train"]
    n_components = min(128, train_feat.shape[0] - 1, train_feat.shape[1])
    print(f"\nApplying PCA: {train_feat.shape[1]} -> {n_components} dims...")
    feat_mean = train_feat.mean(axis=0)
    centered = train_feat - feat_mean
    _, S, Vt = svd(centered, full_matrices=False)
    pca_components = Vt[:n_components]
    variance_explained = np.sum(S[:n_components] ** 2) / np.sum(S**2)
    print(f"  Variance explained: {variance_explained:.4f}")

    # Project all features
    features_pca: dict[str, np.ndarray] = {}
    for label, feat in features.items():
        features_pca[label] = ((feat - feat_mean) @ pca_components.T).astype(np.float32)
        print(f"  {label}: {features_pca[label].shape}")

    # --- Step 4: Train & Score with multiple methods ---
    from dam.guard.builtin.ood import MemoryBank, RealNVPFlow

    methods = {}

    # Method 1: Real-NVP on PCA features
    print(f"\nTraining Real-NVP on PCA features (dim={n_components})...")
    flow = RealNVPFlow(dim=n_components, device="cpu")
    flow.fit(features_pca["normal_train"], epochs=args.flow_epochs, verbose=True)
    methods["real_nvp_pca"] = {}
    for label, feat in features_pca.items():
        if label == "normal_train":
            continue
        methods["real_nvp_pca"][label] = flow.neg_log_prob_batch(feat)

    # Method 2: MemoryBank (kNN) on PCA features
    print("\nTraining MemoryBank on PCA features...")
    bank = MemoryBank()
    bank_dim = 128
    train_for_bank = np.zeros((len(features_pca["normal_train"]), bank_dim), dtype=np.float32)
    dim_to_copy = min(n_components, bank_dim)
    train_for_bank[:, :dim_to_copy] = features_pca["normal_train"][:, :dim_to_copy]
    bank.train(train_for_bank)
    methods["memory_bank_pca"] = {}
    for label, feat in features_pca.items():
        if label == "normal_train":
            continue
        padded = np.zeros((len(feat), bank_dim), dtype=np.float32)
        padded[:, :dim_to_copy] = feat[:, :dim_to_copy]
        methods["memory_bank_pca"][label] = np.array(
            [bank.nearest_distance(z) for z in padded], dtype=np.float32
        )

    # Method 3: kNN on raw features (L2 in 960-dim)
    print("\nComputing kNN on raw 960-dim features...")
    train_raw = features["normal_train"]
    methods["knn_raw"] = {}
    for label, feat in features.items():
        if label == "normal_train":
            continue
        dists = np.array(
            [np.min(np.linalg.norm(train_raw - z[None, :], axis=1)) for z in feat], dtype=np.float32
        )
        methods["knn_raw"][label] = dists

    # --- Step 5: Report ---
    print(f"\n{'=' * 60}")
    print("RESULTS — Vision-based OOD Score Distributions")
    print(f"{'=' * 60}")

    for method_name, method_scores in methods.items():
        print(f"\n--- {method_name} ---")
        print(f"{'Dataset':<20} {'N':>5} {'Median':>10} {'Mean':>10} {'P05':>10} {'P95':>10}")
        print("-" * 65)
        for label in ["normal_test", "legal_variation", "abnormal"]:
            s = method_scores[label]
            print(
                f"{label:<20} {len(s):>5} {np.median(s):>10.3f} {np.mean(s):>10.3f} "
                f"{np.percentile(s, 5):>10.3f} {np.percentile(s, 95):>10.3f}"
            )

        # AUROC
        s0 = method_scores["normal_test"]
        s1 = method_scores["abnormal"]
        auroc = float(
            np.mean(s1[:, None] > s0[None, :]) + 0.5 * np.mean(s1[:, None] == s0[None, :])
        )

        # Gap
        gap = np.median(s1) - np.median(s0)
        gap_sigma = gap / max(np.std(s0), 1e-9)

        # Legal FPR
        threshold = np.percentile(s0, 95)
        legal_fpr = float(np.mean(method_scores["legal_variation"] > threshold))

        print(f"  AUROC={auroc:.4f}  Gap={gap_sigma:.2f}σ  Legal_FPR@p95={legal_fpr:.4f}")


if __name__ == "__main__":
    main()
