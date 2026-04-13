"""OOD Guard (L0) — Multi-backend Out-of-Distribution detector.

三種後端，依能力由弱到強：

Backend 1: Welford z-score (warmup fallback)
    不需任何訓練資料、無依賴。熱啟動期間自動採用。

Backend 2: Memory Bank + Nearest Neighbour (推薦預設)
    FeatureExtractor (MLP + optional CNN) → 128-dim L2 z-vector
    MemoryBank: 存所有訓練 z；推斷時算最近鄰距離。
    優點：不管分布多複雜，只要正常樣本附近有「學長」就 PASS。
    依賴：numpy（基線）/ scipy（KDTree）/ faiss（大量資料）

Backend 3: Normalizing Flows — Real-NVP (最強)
    學習一系列可逆仿射耦合層，把正常資料分布映射到標準高斯。
    推斷：計算 -log p(z)；超過閾值 → OOD。
    優點：精確捕捉資料形狀，對微小 OOD 最靈敏。
    依賴：torch（必須）

Architecture
------------
    OODGuard(backend="memory_bank")   # 推薦
    OODGuard(backend="normalizing_flow")
    OODGuard(backend="welford")       # 強制用 fallback（測試用）

Training API
------------
    guard.train(observations)   # list[Observation] → build MemoryBank or fit Flow

Inference (check) API
---------------------
    result = guard.check(obs, nn_threshold=2.0)   # memory_bank
    result = guard.check(obs, nll_threshold=5.0)  # normalizing_flow

Injection keys (config pool)
----------------------------
    backend         : str    "memory_bank"   — which detector to use
    nn_threshold    : float  2.0             — NN distance cutoff (memory_bank)
    nll_threshold   : float  5.0             — -log p(z) cutoff (normalizing_flow)
    ood_model_path  : str  | None            — path to FeatureExtractor / Flow weights (.pt)
    bank_path       : str  | None            — path to MemoryBank vectors (.npy)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from dam.guard.base import Guard
from dam.types.observation import Observation
from dam.types.result import GuardResult

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_EMBED_DIM = 128
_MLP_HIDDEN = 64
_CNN_EMBED = 64
_WARMUP_SAMPLES = 30

# Real-NVP settings
_FLOW_N_COUPLING = 6  # number of affine coupling layers
_FLOW_HIDDEN = 256  # hidden units per coupling MLP
_FLOW_EPOCHS = 50
_FLOW_LR = 1e-3
_FLOW_BATCH = 64


# ── Feature Extractor ─────────────────────────────────────────────────────────


class FeatureExtractor:
    """Multi-modal feature extractor → 128-dim L2-normalised z-vector.

    Behaviour
    ---------
    If ``torch`` is available:
        • joint states → MLP (2-layer, hidden=64) → 64-dim
        • images       → per-cam CNN (3 conv layers) → pooled → 64-dim (if images present)
        • fuse         → concat → linear(128,128) → L2 norm

    If ``torch`` is *not* available:
        • joint states → pad/truncate to _EMBED_DIM → L2 norm
        (no image branch)

    The extractor is *untrained* by default (random weights).  After calling
    ``MemoryBank.train()``, the extractor weights can be saved/loaded.
    """

    def __init__(self) -> None:
        self._torch_available = self._check_torch()
        self._joint_dim: int | None = None
        self._has_images: bool = False
        self._net: Any | None = None  # torch.nn.Module or None
        self._built = False
        self._device: str = "cpu"

    @staticmethod
    def _check_torch() -> bool:
        try:
            import torch  # noqa: F401

            return True
        except ImportError:
            return False

    # ── Build (lazy, first call) ─────────────────────────────────────────────

    def _build(self, joint_dim: int, has_images: bool, device: str = "cpu") -> None:
        self._joint_dim = joint_dim
        self._has_images = has_images
        self._device = device
        if self._torch_available:
            self._net = self._build_torch_net(joint_dim, has_images)
            if self._net is not None:
                self._net.to(self._device)
        self._built = True

    def _build_torch_net(self, joint_dim: int, has_images: bool) -> Any:
        import torch
        import torch.nn as nn

        class _MLP(nn.Module):
            def __init__(self, in_dim: int) -> None:
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(in_dim, _MLP_HIDDEN),
                    nn.ReLU(),
                    nn.Linear(_MLP_HIDDEN, _MLP_HIDDEN),
                )

            def forward(self, x: Any) -> Any:
                return self.net(x)

        class _CNN(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.conv = nn.Sequential(
                    nn.Conv2d(3, 16, 3, stride=2, padding=1),
                    nn.ReLU(),
                    nn.Conv2d(16, 32, 3, stride=2, padding=1),
                    nn.ReLU(),
                    nn.Conv2d(32, 64, 3, stride=2, padding=1),
                    nn.ReLU(),
                    nn.AdaptiveAvgPool2d(1),
                )
                self.proj = nn.Linear(64, _CNN_EMBED)

            def forward(self, x: Any) -> Any:
                h = self.conv(x).squeeze(-1).squeeze(-1)
                return self.proj(h)

        class _Fuser(nn.Module):
            def __init__(self, in_dim: int, use_images: bool) -> None:
                super().__init__()
                self.mlp = _MLP(in_dim)
                self.use_images = use_images
                self.cnn: _CNN | None = _CNN() if use_images else None
                fuse_in = _MLP_HIDDEN + (_CNN_EMBED if use_images else 0)
                self.head = nn.Linear(fuse_in, _EMBED_DIM)

            def forward(self, joints: Any, img: Any | None = None) -> Any:
                import torch.nn.functional as F

                z_j = self.mlp(joints)
                if self.use_images and self.cnn is not None and img is not None:
                    z_i = self.cnn(img)
                    z = torch.cat([z_j, z_i], dim=-1)
                else:
                    z = (
                        z_j
                        if not self.use_images
                        else torch.cat(
                            [z_j, torch.zeros(z_j.shape[0], _CNN_EMBED, device=z_j.device)], dim=-1
                        )
                    )
                out = self.head(z)
                return F.normalize(out, dim=-1)

        net = _Fuser(joint_dim, has_images)
        net.eval()
        return net

    # ── Extract ──────────────────────────────────────────────────────────────

    def extract(self, obs: Observation) -> np.ndarray:
        """Return 128-dim L2-normalised float32 z-vector."""
        # Use joint_positions as the primary source array
        joint_vec = obs.joint_positions.astype(np.float32)
        joint_dim = joint_vec.shape[0]
        has_images = obs.images is not None and len(obs.images) > 0

        if not self._built:
            self._build(joint_dim, has_images)

        if self._torch_available and self._net is not None:
            return self._extract_torch(joint_vec, obs)
        else:
            return self._extract_numpy(joint_vec)

    def _extract_torch(self, joint_vec: np.ndarray, obs: Observation) -> np.ndarray:
        import torch

        # Adaptive sizing: individual guards handle their own needs based on loaded dimension
        if len(joint_vec) != self._joint_dim:
            if len(joint_vec) > self._joint_dim:
                joint_vec = joint_vec[: self._joint_dim]
            else:
                joint_vec = np.pad(joint_vec, (0, self._joint_dim - len(joint_vec)))

        img_t: Any | None = None
        if self._has_images and obs.images:
            # Use first camera image; resize to 64×64
            cam_img = next(iter(obs.images.values()))
            if cam_img.ndim == 3 and cam_img.shape[2] == 3:
                cam_img = cam_img.transpose(2, 0, 1)  # HWC→CHW
            elif cam_img.ndim == 2:
                cam_img = np.stack([cam_img] * 3, axis=0)
            img_arr = cam_img.astype(np.float32) / 255.0
            img_t = torch.tensor(img_arr, dtype=torch.float32).unsqueeze(0).to(self._device)

        joints_t = torch.tensor(joint_vec, dtype=torch.float32).unsqueeze(0).to(self._device)
        self._net.eval()
        with torch.no_grad():
            z = self._net(joints_t, img_t)
        return z.squeeze(0).cpu().numpy().astype(np.float32)

    def _extract_numpy(self, joint_vec: np.ndarray) -> np.ndarray:
        """Fallback: pad/truncate joint_vec to self._joint_dim, etc."""
        v = joint_vec.astype(np.float32)
        target = self._joint_dim or _EMBED_DIM
        if len(v) != target:
            v = v[:target] if len(v) > target else np.pad(v, (0, target - len(v)))

        # Then map to _EMBED_DIM as usual for the final z-vector
        v = v[:_EMBED_DIM] if len(v) >= _EMBED_DIM else np.pad(v, (0, _EMBED_DIM - len(v)))
        norm = np.linalg.norm(v)
        if norm > 1e-9:
            v = v / norm
        return v

    # ── Serialise ────────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        if self._torch_available and self._net is not None:
            import torch

            torch.save(self._net.state_dict(), path)
            logger.info("FeatureExtractor saved to %s", path)

    def load(self, path: str, joint_dim: int, has_images: bool, device: str = "cpu") -> None:
        self._device = device
        if self._torch_available:
            import torch

            try:
                state = torch.load(path, map_location="cpu", weights_only=False)
            except TypeError:
                state = torch.load(path, map_location="cpu")

            # --- Checkpoint-Driven Architecture Detection ---
            # 1. Detect required joint dimension from MLP weights
            k_mlp = "mlp.net.0.weight" if "mlp.net.0.weight" in state else "net.0.weight"
            if k_mlp in state:
                ckpt_in_dim = state[k_mlp].shape[1]
                if ckpt_in_dim != joint_dim:
                    logger.info("OOD: Checkpoint expects %d joints. Overriding.", ckpt_in_dim)
                    joint_dim = ckpt_in_dim

            # 2. Detect image branch requirement from CNN keys
            has_cnn_keys = any(key.startswith("cnn.") for key in state)
            if has_cnn_keys != has_images:
                action = "Enabling" if has_cnn_keys else "Disabling"
                logger.info("OOD: Checkpoint-driven architecture: %s image branch.", action)
                has_images = has_cnn_keys

            # Build and load
            self._build(joint_dim, has_images, device=device)
            if self._net is not None:
                self._net.load_state_dict(state)
                self._net.to(self._device)
                self._net.eval()
                logger.info(
                    "FeatureExtractor loaded (joints=%d, images=%s, device=%s)",
                    joint_dim,
                    has_images,
                    self._device,
                )


# ── Memory Bank ───────────────────────────────────────────────────────────────


class MemoryBank:
    """Stores L2-normalised 128-dim z-vectors; queries nearest-neighbour distance.

    Backend priority:
        1. FAISS (faiss-cpu/faiss-gpu)  — fastest for large banks
        2. scipy.spatial.KDTree         — good for ≤ 10k vectors
        3. numpy brute-force            — always available

    The bank is *read-only after training*.  To update: call ``train()`` again.
    """

    def __init__(self) -> None:
        self._vectors: np.ndarray | None = None  # shape (N, 128)
        self._tree: Any | None = None  # scipy KDTree | faiss index
        self._backend: str = "none"
        self._n_vectors: int = 0

    @property
    def is_trained(self) -> bool:
        return self._n_vectors > 0

    @property
    def size(self) -> int:
        return self._n_vectors

    def train(self, vectors: np.ndarray) -> None:
        """Build the bank from an (N, 128) float32 array."""
        assert vectors.ndim == 2 and vectors.shape[1] == _EMBED_DIM, (
            f"Expected (N, {_EMBED_DIM}), got {vectors.shape}"
        )
        self._vectors = vectors.astype(np.float32)
        self._n_vectors = len(vectors)
        self._build_index()
        logger.info("MemoryBank trained: %d vectors, backend=%s", self._n_vectors, self._backend)

    def _build_index(self) -> None:
        assert self._vectors is not None
        # Try FAISS
        try:
            import faiss  # type: ignore[import]

            index = faiss.IndexFlatL2(self._vectors.shape[1])
            index.add(self._vectors)
            self._tree = index
            self._backend = "faiss"
            return
        except ImportError:
            pass
        # Try scipy
        try:
            from scipy.spatial import KDTree  # type: ignore[import]

            self._tree = KDTree(self._vectors)
            self._backend = "scipy"
            return
        except ImportError:
            pass
        # Fallback
        self._backend = "numpy"

    def nearest_distance(self, z: np.ndarray) -> float:
        """Return the L2 distance to the nearest vector in the bank."""
        if not self.is_trained:
            return 0.0
        z32 = z.astype(np.float32)

        if self._backend == "faiss":
            q = z32.reshape(1, -1)
            distances, _ = self._tree.search(q, 1)
            return float(np.sqrt(distances[0, 0]))

        if self._backend == "scipy":
            dist, _ = self._tree.query(z32, k=1)
            return float(dist)

        # Brute force
        assert self._vectors is not None
        diffs = self._vectors - z32[np.newaxis, :]
        dists = np.linalg.norm(diffs, axis=1)
        return float(np.min(dists))

    def save(self, path: str) -> None:
        if self._vectors is not None:
            np.save(path, self._vectors)

    def load(self, path: str) -> None:
        vectors = np.load(path)
        self.train(vectors)


# ── Normalizing Flow — Real-NVP ───────────────────────────────────────────────


class RealNVPFlow:
    """Real-NVP Normalizing Flow for OOD detection on 128-dim z-vectors.

    Architecture
    ------------
    N affine coupling layers, each alternating which half of the vector is
    transformed.  Each coupling network is a 2-hidden-layer MLP.

    Reference: Dinh et al., "Density Estimation using Real-valued Non-Volume
    Preserving (Real NVP) Transformations", ICLR 2017.

    Fitting
    -------
        flow = RealNVPFlow(dim=128)
        flow.fit(vectors)           # numpy (N, 128)

    Scoring
    -------
        nll = flow.neg_log_prob(z)  # scalar; higher = more OOD
    """

    def __init__(
        self,
        dim: int = _EMBED_DIM,
        n_coupling: int = _FLOW_N_COUPLING,
        hidden: int = _FLOW_HIDDEN,
        device: str = "cpu",
    ) -> None:
        if not self._torch_available():
            raise ImportError("Normalizing Flows require torch. pip install torch")
        self._dim = dim
        self._n_coupling = n_coupling
        self._hidden = hidden
        self._device = device
        self._model: Any | None = None  # torch.nn.Module
        self._is_fitted = False

    @staticmethod
    def _torch_available() -> bool:
        try:
            import torch  # noqa: F401

            return True
        except ImportError:
            return False

    # ── Model construction ────────────────────────────────────────────────────

    def _build_model(self) -> Any:
        import torch
        import torch.nn as nn

        dim = self._dim
        n_coupling = self._n_coupling
        hidden = self._hidden

        class _CouplingMLP(nn.Module):
            """Scale-translation network for one coupling layer."""

            def __init__(self, in_dim: int, out_dim: int) -> None:
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(in_dim, hidden),
                    nn.LeakyReLU(0.2),
                    nn.Linear(hidden, hidden),
                    nn.LeakyReLU(0.2),
                )
                self.scale_head = nn.Linear(hidden, out_dim)
                self.translate_head = nn.Linear(hidden, out_dim)
                # Initialise scale close to zero → identity at start
                nn.init.zeros_(self.scale_head.weight)
                nn.init.zeros_(self.scale_head.bias)

            def forward(self, x: Any) -> tuple[Any, Any]:
                h = self.net(x)
                s = torch.tanh(self.scale_head(h))  # bounded scale
                t = self.translate_head(h)
                return s, t

        class _RealNVP(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                # Alternate: even/odd half split
                half = dim // 2
                d1 = half  # first-half dimension
                d2 = dim - half  # second-half dimension

                self.couplings = nn.ModuleList()
                for i in range(n_coupling):
                    if i % 2 == 0:
                        # Transform second half, condition on first
                        self.couplings.append(_CouplingMLP(d1, d2))
                    else:
                        # Transform first half, condition on second
                        self.couplings.append(_CouplingMLP(d2, d1))

            def forward(self, x: Any) -> tuple[Any, Any]:
                """
                Returns (z, log_det_jacobian) where z ~ N(0,I) if x is in-dist.
                """
                half = dim // 2
                log_det = x.new_zeros(x.shape[0])

                for i, coupling in enumerate(self.couplings):
                    if i % 2 == 0:
                        x1, x2 = x[:, :half], x[:, half:]
                        s, t = coupling(x1)
                        x2 = x2 * torch.exp(s) + t
                        log_det = log_det + s.sum(dim=1)
                        x = torch.cat([x1, x2], dim=1)
                    else:
                        x1, x2 = x[:, :half], x[:, half:]
                        s, t = coupling(x2)
                        x1 = x1 * torch.exp(s) + t
                        log_det = log_det + s.sum(dim=1)
                        x = torch.cat([x1, x2], dim=1)

                return x, log_det

            def log_prob(self, x: Any) -> Any:
                """Log-likelihood under the flow (higher = more likely = in-dist)."""
                import math

                z, log_det = self.forward(x)
                # Standard Gaussian log-prob
                log_pz = -0.5 * (z**2 + math.log(2 * math.pi)).sum(dim=1)
                return log_pz + log_det

        return _RealNVP()

    # ── Fitting ───────────────────────────────────────────────────────────────

    def fit(
        self,
        vectors: np.ndarray,
        epochs: int = _FLOW_EPOCHS,
        lr: float = _FLOW_LR,
        batch_size: int = _FLOW_BATCH,
        verbose: bool = False,
    ) -> None:
        """Fit the flow on (N, dim) float32 normal-sample z-vectors."""
        import torch
        import torch.optim as optim
        from torch.utils.data import DataLoader, TensorDataset

        if self._model is None:
            self._model = self._build_model().to(self._device)

        X = torch.tensor(vectors, dtype=torch.float32)
        dataset = TensorDataset(X)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

        opt = optim.Adam(self._model.parameters(), lr=lr)
        self._model.train()

        for epoch in range(epochs):
            total_loss = 0.0
            n_batches = 0
            for (batch,) in loader:
                opt.zero_grad()
                loss = -self._model.log_prob(batch).mean()
                loss.backward()
                # Gradient clipping for stability
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                opt.step()
                total_loss += loss.item()
                n_batches += 1
            if verbose and (epoch + 1) % 10 == 0:
                logger.info(
                    "RealNVP epoch %d/%d  loss=%.4f",
                    epoch + 1,
                    epochs,
                    total_loss / max(n_batches, 1),
                )

        self._model.eval()
        self._is_fitted = True
        logger.info(
            "RealNVP fitted: dim=%d  n_coupling=%d  n_samples=%d",
            self._dim,
            self._n_coupling,
            len(vectors),
        )

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    # ── Scoring ───────────────────────────────────────────────────────────────

    def neg_log_prob(self, z: np.ndarray) -> float:
        """Return -log p(z).  Higher value = more OOD."""
        if not self._is_fitted or self._model is None:
            return 0.0
        import torch

        z_t = torch.tensor(z, dtype=torch.float32).unsqueeze(0).to(self._device)
        with torch.no_grad():
            lp = self._model.log_prob(z_t)
        return float(-lp.cpu().item())

    # ── Serialise ─────────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        if self._model is not None:
            import torch

            torch.save(
                {
                    "state_dict": self._model.state_dict(),
                    "dim": self._dim,
                    "n_coupling": self._n_coupling,
                    "hidden": self._hidden,
                },
                path,
            )
            logger.info("RealNVP saved to %s", path)

    def load(self, path: str, device: str = "cpu") -> None:
        import torch

        self._device = device
        try:
            ckpt = torch.load(path, map_location="cpu", weights_only=False)
        except TypeError:
            ckpt = torch.load(path, map_location="cpu")
        self._dim = ckpt["dim"]
        self._n_coupling = ckpt["n_coupling"]
        self._hidden = ckpt["hidden"]
        self._model = self._build_model().to(self._device)
        self._model.load_state_dict(ckpt["state_dict"])
        self._model.eval()
        self._is_fitted = True
        logger.info("RealNVP loaded from %s (device=%s)", path, self._device)


# ── Welford fallback (unchanged from Phase 2) ────────────────────────────────


class _WelfordStats:
    """Online Welford mean/variance for graceful degradation."""

    def __init__(self) -> None:
        self.n: int = 0
        self.mean: np.ndarray | None = None
        self.m2: np.ndarray | None = None

    def update(self, x: np.ndarray) -> None:
        self.n += 1
        if self.mean is None:
            self.mean = x.copy()
            self.m2 = np.zeros_like(x)
        else:
            delta = x - self.mean
            self.mean += delta / self.n
            self.m2 += delta * (x - self.mean)

    def z_score_max(self, x: np.ndarray) -> float:
        if self.mean is None or self.m2 is None or self.n < 2:
            return 0.0
        var = self.m2 / (self.n - 1)
        std = np.sqrt(var + 1e-9)
        return float(np.max(np.abs(x - self.mean) / std))


# ── OODGuard ──────────────────────────────────────────────────────────────────


class OODGuard(Guard):
    """L0 gate: rejects observations that appear out-of-distribution.

    Detector priority
    -----------------
    backend="memory_bank"        → MemoryBank + FeatureExtractor (recommended)
    backend="normalizing_flow"   → Real-NVP flow (most sensitive)
    backend="welford"            → Welford online z-score (forced fallback)

    Auto-fallback
    -------------
    If the chosen backend is not yet trained, falls back to Welford z-score
    for the first _WARMUP_SAMPLES cycles.

    Config pool keys
    ----------------
    backend         str    "memory_bank"  — detector variant
    nn_threshold    float  2.0            — NN distance cutoff (memory_bank)
    nll_threshold   float  5.0            — -log p(z) cutoff (normalizing_flow)
    ood_model_path  str    None           — path to saved extractor / flow (.pt)
    bank_path       str    None           — path to MemoryBank vectors (.npy)
    """

    def __init__(self, backend: str = "memory_bank") -> None:
        self._backend_name = backend
        self._extractor = FeatureExtractor()
        self._bank = MemoryBank()
        self._flow: RealNVPFlow | None = None
        self._welford = _WelfordStats()
        self._model_path: str | None = None
        self._bank_path: str | None = None
        self._device: str = "cpu"

    # ── Training API ─────────────────────────────────────────────────────────

    def train(
        self,
        observations: list[Observation],
        flow_epochs: int = _FLOW_EPOCHS,
        flow_lr: float = _FLOW_LR,
    ) -> None:
        """Build the memory bank / fit the flow from normal observations."""
        if not observations:
            logger.warning("OODGuard.train(): empty observation list")
            return
        zs = []
        for obs in observations:
            try:
                z = self._extractor.extract(obs)
                zs.append(z)
            except Exception as e:
                logger.warning("OODGuard.train(): skipping observation: %s", e)
        if not zs:
            logger.error("OODGuard.train(): no valid observations extracted")
            return
        vectors = np.stack(zs, axis=0)

        if self._backend_name == "normalizing_flow":
            try:
                if self._flow is None:
                    self._flow = RealNVPFlow(dim=vectors.shape[1], device=self._device)
                self._flow.fit(vectors, epochs=flow_epochs, lr=flow_lr)
            except ImportError as e:
                logger.warning("RealNVP requires torch. Falling back to MemoryBank. (%s)", e)
                self._backend_name = "memory_bank"
                self._bank.train(vectors)
        else:
            self._bank.train(vectors)

    def save(self, model_path: str, bank_path: str) -> None:
        """Persist extractor weights and memory bank vectors."""
        self._extractor.save(model_path)
        if self._backend_name == "normalizing_flow" and self._flow is not None:
            self._flow.save(model_path.replace(".pt", "_flow.pt"))
        else:
            self._bank.save(bank_path)

    def load(
        self,
        model_path: str,
        bank_path: str,
        joint_dim: int,
        has_images: bool = False,
        device: str = "cpu",
    ) -> None:
        """Restore extractor and memory bank / flow from disk."""
        self._device = device
        self._extractor.load(model_path, joint_dim, has_images, device=device)
        flow_path = model_path.replace(".pt", "_flow.pt")
        if self._backend_name == "normalizing_flow" and Path(flow_path).exists():
            if self._flow is None:
                self._flow = RealNVPFlow(device=device)
            self._flow.load(flow_path, device=device)
        elif Path(bank_path).exists():
            self._bank.load(bank_path)

    # ── check() ──────────────────────────────────────────────────────────────

    def preflight(
        self,
        ood_model_path: str | None = None,
        bank_path: str | None = None,
        device: str = "cpu",
        **kwargs: Any,
    ) -> None:
        """Eagerly load model/bank during preflight to avoid lazy-loading delays during check()."""
        # obs is not available during preflight, so we use dummy data for architecture detection.
        # FeatureExtractor.load now uses checkpoint keys so it is robust to obs shape.
        import numpy as np

        from dam.types.observation import Observation

        if ood_model_path and Path(ood_model_path).exists():
            import time

            dummy_obs = Observation(
                timestamp=time.monotonic(),
                joint_positions=np.zeros(6),  # Default 6-axis guess
                images={},
            )
            try:
                self._maybe_load(ood_model_path, bank_path, dummy_obs, device=device)
            except Exception as e:
                logger.error("OODGuard: preflight load failed: %s", e)

    def check(
        self,
        obs: Observation,
        nn_threshold: float = 2.0,
        nll_threshold: float = 5.0,
        ood_model_path: str | None = None,
        bank_path: str | None = None,
        device: str = "cpu",
    ) -> GuardResult:
        layer = self.get_layer()
        name = self.get_name()

        # Lazy-load model / bank if paths provided and changed
        try:
            self._maybe_load(ood_model_path, bank_path, obs, device=device)
        except Exception as e:
            logger.error("OODGuard: load failed: %s", e)

        if (
            self._backend_name == "normalizing_flow"
            and self._flow is not None
            and self._flow.is_fitted
        ):
            return self._check_flow(obs, nll_threshold, name, layer)
        if self._bank.is_trained:
            return self._check_memory_bank(obs, nn_threshold, name, layer)
        return self._check_welford(obs, name, layer)

    def _maybe_load(
        self,
        model_path: str | None,
        bank_path: str | None,
        obs: Observation,
        device: str = "cpu",
    ) -> None:
        changed = False
        if (
            model_path
            and (model_path != self._model_path or device != self._device)
            and Path(model_path).exists()
        ):
            joint_vec = obs.joint_positions
            has_images = obs.images is not None and len(obs.images) > 0
            self._extractor.load(model_path, joint_vec.shape[0], has_images, device=device)
            self._model_path = model_path
            self._device = device
            changed = True
        if bank_path and bank_path != self._bank_path and Path(bank_path).exists():
            self._bank.load(bank_path)
            self._bank_path = bank_path
            changed = True
        if changed:
            logger.info("OODGuard: reloaded (model=%s, bank=%s)", model_path, bank_path)

    # ── Individual backend checks ─────────────────────────────────────────────

    def _check_memory_bank(
        self,
        obs: Observation,
        threshold: float,
        name: str,
        layer: Any,
    ) -> GuardResult:
        try:
            z = self._extractor.extract(obs)
            dist = self._bank.nearest_distance(z)
            if dist > threshold:
                return GuardResult.reject(
                    reason=f"OOD nn_distance={dist:.4f} > threshold={threshold:.4f}",
                    guard_name=name,
                    layer=layer,
                )
            return GuardResult.success(guard_name=name, layer=layer)
        except Exception as e:
            return GuardResult.fault(e, "guard_code", name, layer)

    def _check_flow(
        self,
        obs: Observation,
        nll_threshold: float,
        name: str,
        layer: Any,
    ) -> GuardResult:
        """Check using Normalizing Flow -log p(z)."""
        try:
            z = self._extractor.extract(obs)
            assert self._flow is not None
            nll = self._flow.neg_log_prob(z)
            if nll > nll_threshold:
                return GuardResult.reject(
                    reason=f"OOD nll={nll:.4f} > threshold={nll_threshold:.4f}",
                    guard_name=name,
                    layer=layer,
                )
            return GuardResult.success(guard_name=name, layer=layer)
        except Exception as e:
            return GuardResult.fault(e, "guard_code", name, layer)

    def _check_welford(
        self,
        obs: Observation,
        name: str,
        layer: Any,
    ) -> GuardResult:
        """Welford online z-score fallback (warm-up of 30 samples)."""
        try:
            # Source array driven: no explicit velocities/features
            features = obs.joint_positions.astype(np.float64)

            if self._welford.n < _WARMUP_SAMPLES:
                self._welford.update(features)
                return GuardResult.success(guard_name=name, layer=layer)

            max_z = self._welford.z_score_max(features)
            z_threshold = 5.0
            self._welford.update(features)

            if max_z > z_threshold:
                return GuardResult.reject(
                    reason=f"OOD z-score={max_z:.2f} > threshold={z_threshold:.2f} (Welford)",
                    guard_name=name,
                    layer=layer,
                )
            return GuardResult.success(guard_name=name, layer=layer)
        except Exception as e:
            return GuardResult.fault(e, "guard_code", name, layer)

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self._backend_name,
            "bank_trained": self._bank.is_trained,
            "bank_size": self._bank.size,
            "bank_backend": self._bank._backend,
            "flow_fitted": self._flow is not None and self._flow.is_fitted,
            "torch_available": self._extractor._torch_available,
            "welford_samples": self._welford.n,
        }
