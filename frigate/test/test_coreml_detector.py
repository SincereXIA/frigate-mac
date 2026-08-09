"""Tests for the native Apple Silicon CoreML detector."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from frigate.detectors.detector_config import (
    InputDTypeEnum,
    InputTensorEnum,
    ModelConfig,
    ModelTypeEnum,
)
from frigate.detectors.plugins import coreml
from frigate.detectors.plugins.coreml import (
    COREML_PROVIDER,
    CPU_PROVIDER,
    CoreMLDetector,
    CoreMLDetectorConfig,
)
from frigate.stats.util import read_detector_runtime_status
from frigate.watchdog import MAX_RESTARTS, FrigateWatchdog


class FakeCoreMLSession:
    """Small ONNX Runtime session stand-in for detector initialization tests."""

    def __init__(self, profile_path: Path):
        self.profile_path = profile_path

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="images")]

    def run(self, _outputs, _inputs) -> list[np.ndarray]:
        prediction = np.array(
            [[0, 1, 2, 3, 4, 0.9, 0]],
            dtype=np.float32,
        )
        return [prediction]

    def get_providers(self) -> list[str]:
        return [COREML_PROVIDER, CPU_PROVIDER]

    def end_profiling(self) -> str:
        events = [
            {
                "cat": "Node",
                "args": {"provider": COREML_PROVIDER, "op_name": "partition"},
            },
            {
                "cat": "Node",
                "args": {"provider": CPU_PROVIDER, "op_name": "Concat"},
            },
        ]
        self.profile_path.write_text(json.dumps(events))
        return str(self.profile_path)


class TestCoreMLDetector(unittest.TestCase):
    """Validate provider selection, status reporting, and cache recovery."""

    def _config(self, model_path: Path, status_path: Path) -> CoreMLDetectorConfig:
        model = ModelConfig(
            path=str(model_path),
            labelmap_path="labelmap.txt",
            width=320,
            height=320,
            input_tensor=InputTensorEnum.nchw,
            input_dtype=InputDTypeEnum.float,
            model_type=ModelTypeEnum.yolonas,
        )
        config = CoreMLDetectorConfig(type="coreml", model=model)
        config.set_runtime_status_path(str(status_path))
        return config

    def test_ane_provider_options_and_runtime_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_path = root / "model.onnx"
            model_path.write_bytes(b"fixture model")
            status_path = root / "runtime" / "detector.json"
            session = FakeCoreMLSession(root / "profile.json")
            inference_session = Mock(return_value=session)
            paths = SimpleNamespace(
                model_cache_dir=root / "config" / "model_cache",
                runtime_dir=root / "runtime",
                labelmap_path=Path("labelmap.txt"),
            )

            with (
                patch.object(coreml.platform, "system", return_value="Darwin"),
                patch.object(coreml.platform, "machine", return_value="arm64"),
                patch.object(
                    coreml.ort,
                    "get_available_providers",
                    return_value=[COREML_PROVIDER, CPU_PROVIDER],
                ),
                patch.object(coreml.ort, "InferenceSession", inference_session),
                patch.object(
                    coreml.RuntimePaths,
                    "from_environment",
                    return_value=paths,
                ),
            ):
                detector = CoreMLDetector(self._config(model_path, status_path))

            providers = inference_session.call_args.kwargs["providers"]
            session_options = inference_session.call_args.kwargs["sess_options"]
            self.assertEqual(providers[0][0], COREML_PROVIDER)
            self.assertEqual(providers[0][1]["MLComputeUnits"], "CPUAndNeuralEngine")
            self.assertEqual(providers[0][1]["ModelFormat"], "NeuralNetwork")
            self.assertEqual(providers[1], CPU_PROVIDER)
            self.assertEqual(
                session_options.get_session_config_entry(
                    "session.intra_op.allow_spinning"
                ),
                "0",
            )
            self.assertEqual(
                session_options.get_session_config_entry(
                    "session.inter_op.allow_spinning"
                ),
                "0",
            )

            status = json.loads(status_path.read_text())
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["provider_partitions"][COREML_PROVIDER], 1)
            self.assertFalse(status["unexpected_cpu_fallback"])
            self.assertFalse(status["thread_spinning"])
            self.assertNotIn(str(model_path), status_path.read_text())
            self.assertEqual(
                detector.detect_raw(np.zeros((1, 3, 320, 320))).shape, (20, 6)
            )

    def test_gpu_backend_maps_to_all_compute_units(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_path = root / "model.onnx"
            model_path.write_bytes(b"fixture model")
            config = self._config(model_path, root / "runtime" / "status.json")
            config.inference_backend = "gpu"
            session = FakeCoreMLSession(root / "profile.json")
            inference_session = Mock(return_value=session)
            paths = SimpleNamespace(
                model_cache_dir=root / "model_cache",
                runtime_dir=root / "runtime",
                labelmap_path=Path("labelmap.txt"),
            )

            with (
                patch.object(coreml.platform, "system", return_value="Darwin"),
                patch.object(coreml.platform, "machine", return_value="arm64"),
                patch.object(
                    coreml.ort,
                    "get_available_providers",
                    return_value=[COREML_PROVIDER, CPU_PROVIDER],
                ),
                patch.object(coreml.ort, "InferenceSession", inference_session),
                patch.object(
                    coreml.RuntimePaths,
                    "from_environment",
                    return_value=paths,
                ),
            ):
                CoreMLDetector(config)

            providers = inference_session.call_args.kwargs["providers"]
            self.assertEqual(providers[0][1]["MLComputeUnits"], "ALL")

    def test_thread_spinning_can_be_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_path = root / "model.onnx"
            model_path.write_bytes(b"fixture model")
            config = self._config(model_path, root / "runtime" / "status.json")
            config.allow_thread_spinning = True
            session = FakeCoreMLSession(root / "profile.json")
            inference_session = Mock(return_value=session)
            paths = SimpleNamespace(
                model_cache_dir=root / "model_cache",
                runtime_dir=root / "runtime",
                labelmap_path=Path("labelmap.txt"),
            )

            with (
                patch.object(coreml.platform, "system", return_value="Darwin"),
                patch.object(coreml.platform, "machine", return_value="arm64"),
                patch.object(
                    coreml.ort,
                    "get_available_providers",
                    return_value=[COREML_PROVIDER, CPU_PROVIDER],
                ),
                patch.object(coreml.ort, "InferenceSession", inference_session),
                patch.object(
                    coreml.RuntimePaths,
                    "from_environment",
                    return_value=paths,
                ),
            ):
                CoreMLDetector(config)

            session_options = inference_session.call_args.kwargs["sess_options"]
            self.assertEqual(
                session_options.get_session_config_entry(
                    "session.intra_op.allow_spinning"
                ),
                "1",
            )
            status = json.loads((root / "runtime" / "status.json").read_text())
            self.assertTrue(status["thread_spinning"])

    def test_missing_coreml_provider_fails_without_cpu_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_path = root / "model.onnx"
            model_path.write_bytes(b"fixture model")
            config = self._config(model_path, root / "runtime" / "status.json")

            with (
                patch.object(coreml.platform, "system", return_value="Darwin"),
                patch.object(coreml.platform, "machine", return_value="arm64"),
                patch.object(
                    coreml.ort,
                    "get_available_providers",
                    return_value=[CPU_PROVIDER],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "CoreML execution provider"):
                    CoreMLDetector(config)

            status = json.loads((root / "runtime" / "status.json").read_text())
            self.assertEqual(status["state"], "error")
            self.assertEqual(status["error_type"], "RuntimeError")

    def test_cache_failure_quarantines_and_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cache_dir = root / "cache"
            cache_dir.mkdir()
            (cache_dir / "invalid.bin").write_bytes(b"invalid")
            model_path = root / "model.onnx"
            model_path.write_bytes(b"fixture")
            replacement_session = Mock()

            with patch.object(
                coreml.ort,
                "InferenceSession",
                side_effect=[RuntimeError("cache failure"), replacement_session],
            ) as inference_session:
                result = CoreMLDetector._create_session_with_cache_recovery(
                    model_path,
                    Mock(),
                    {"ModelCacheDirectory": str(cache_dir)},
                    cache_dir,
                )

            self.assertIs(result, replacement_session)
            self.assertEqual(inference_session.call_count, 2)
            self.assertTrue(cache_dir.is_dir())
            quarantined = list(root.glob("cache.invalid-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertTrue((quarantined[0] / "invalid.bin").is_file())

    def test_runtime_status_reader_rejects_stale_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            status_path = Path(temporary_directory) / "status.json"
            status_path.write_text(json.dumps({"pid": 12, "state": "ready"}))

            self.assertEqual(
                read_detector_runtime_status(status_path, 12),
                {"pid": 12, "state": "ready"},
            )
            self.assertIsNone(read_detector_runtime_status(status_path, 13))

    def test_detector_restart_is_bounded_without_service_restart(self) -> None:
        detector = Mock()
        detector.detect_process = SimpleNamespace(pid=321)
        watchdog = FrigateWatchdog({"coreml": detector}, Mock())

        for _ in range(MAX_RESTARTS):
            self.assertTrue(
                watchdog._restart_detector("coreml", detector, "stopped unexpectedly")
            )

        self.assertFalse(
            watchdog._restart_detector("coreml", detector, "stopped unexpectedly")
        )
        self.assertEqual(detector.start_or_restart.call_count, MAX_RESTARTS)


if __name__ == "__main__":
    unittest.main()
