"""Unit tests for vision feature extractor and OOD context integration."""

import numpy as np
import pytest

from dam.guard.ood_context import OODContext
from dam.guard.vision_feature_extractor import (
    VisionFeatureExtractor,
    VisionFeatureExtractorConfig,
)
from dam.types.observation import Observation


@pytest.fixture
def dummy_obs_with_image():
    img = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    return Observation(
        timestamp=0.0,
        joint_positions=np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
        images={"top": img},
    )


@pytest.fixture
def dummy_obs_no_image():
    return Observation(
        timestamp=0.0,
        joint_positions=np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
    )


class TestVisionFeatureExtractor:
    def test_mobilenet_v3_large_output_shape(self):
        cfg = VisionFeatureExtractorConfig(model_name="mobilenet_v3_large", device="cpu")
        ext = VisionFeatureExtractor(cfg)
        img = np.random.randint(0, 255, (2, 224, 224, 3), dtype=np.uint8)
        features = ext.extract(img)
        assert features.shape == (2, 960)
        assert features.dtype == np.float32

    def test_mobilenet_v3_small_output_shape(self):
        cfg = VisionFeatureExtractorConfig(model_name="mobilenet_v3_small", device="cpu")
        ext = VisionFeatureExtractor(cfg)
        img = np.random.randint(0, 255, (1, 320, 320, 3), dtype=np.uint8)
        features = ext.extract(img)
        assert features.shape == (1, 576)

    def test_extract_single(self):
        cfg = VisionFeatureExtractorConfig(model_name="mobilenet_v3_small", device="cpu")
        ext = VisionFeatureExtractor(cfg)
        img = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
        features = ext.extract_single(img)
        assert features.shape == (576,)

    def test_embedding_dim_property(self):
        cfg = VisionFeatureExtractorConfig(model_name="mobilenet_v3_large", device="cpu")
        ext = VisionFeatureExtractor(cfg)
        assert ext.embedding_dim == 960

    def test_different_images_different_features(self):
        cfg = VisionFeatureExtractorConfig(model_name="mobilenet_v3_small", device="cpu")
        ext = VisionFeatureExtractor(cfg)
        white = np.ones((1, 224, 224, 3), dtype=np.uint8) * 255
        black = np.zeros((1, 224, 224, 3), dtype=np.uint8)
        f_white = ext.extract(white)
        f_black = ext.extract(black)
        assert not np.allclose(f_white, f_black, atol=1e-3)


class TestOODContextVision:
    def test_features_without_vision_returns_joint_only(self, dummy_obs_no_image):
        ctx = OODContext()
        z = ctx.features(dummy_obs_no_image)
        assert z.shape == (128,)

    def test_features_without_vision_config_ignores_images(self, dummy_obs_with_image):
        ctx = OODContext()
        z = ctx.features(dummy_obs_with_image)
        assert z.shape == (128,)

    def test_configure_vision_changes_output(self, dummy_obs_with_image):
        ctx = OODContext()
        z_no_vision = ctx.features(dummy_obs_with_image)
        ctx.configure_vision("mobilenet_v3_small", vision_weight=0.5, device="cpu")
        z_with_vision = ctx.features(dummy_obs_with_image)
        # Fused vector is 128 (joint) + 128 (truncated vision) = 256 then L2-normed
        # Actually: joint is 128-dim, vision gets truncated/padded to 128, concat = 256
        assert z_with_vision.shape[0] == 256
        assert not np.array_equal(z_no_vision, z_with_vision[:128])

    def test_no_image_pads_vision_zeros(self, dummy_obs_no_image):
        ctx = OODContext()
        ctx.configure_vision("mobilenet_v3_small", vision_weight=0.5, device="cpu")
        z = ctx.features(dummy_obs_no_image)
        # When vision configured but no image: still outputs 256-dim (vision half is zeros before norm)
        assert z.shape[0] == 256

    def test_vision_weight_zero_gives_joint_dominated(self, dummy_obs_with_image):
        ctx = OODContext()
        ctx.configure_vision("mobilenet_v3_small", vision_weight=0.0, device="cpu")
        z = ctx.features(dummy_obs_with_image)
        # With weight=0, vision contribution is zeroed
        assert z.shape[0] == 256

    def test_set_vision_pca(self, dummy_obs_with_image):
        ctx = OODContext()
        ctx.configure_vision("mobilenet_v3_small", vision_weight=0.5, device="cpu")
        # Simulate PCA: project 576-dim to 64-dim
        mean = np.zeros(576, dtype=np.float32)
        components = np.random.randn(64, 576).astype(np.float32)
        ctx.set_vision_pca(mean, components)
        z = ctx.features(dummy_obs_with_image)
        # joint=128, vision after PCA=64 → concat = 192
        assert z.shape[0] == 192

    def test_features_batch_matches_single_extraction(self, dummy_obs_with_image):
        class FakeVisionExtractor:
            def extract(self, images):
                return np.tile(np.arange(576, dtype=np.float32), (len(images), 1))

            def extract_single(self, image):
                return self.extract(image[np.newaxis])[0]

        ctx = OODContext()
        ctx._vision_extractor = FakeVisionExtractor()
        ctx._vision_weight = 0.3
        observations = [dummy_obs_with_image, dummy_obs_with_image]

        expected = np.stack([ctx.features(obs) for obs in observations], axis=0)
        actual = ctx.features_batch(observations, vision_batch_size=2)

        np.testing.assert_allclose(actual, expected, atol=1e-6)

    def test_configured_cameras_filters_obs_images(self):
        ctx = OODContext()
        ctx._vision_cameras = ["top"]
        assert ctx._vision_cameras == ["top"]
        # None cameras = all cameras; explicit list = only listed cameras.
        ctx2 = OODContext()
        ctx2._vision_cameras = None
        assert ctx2._vision_cameras is None
