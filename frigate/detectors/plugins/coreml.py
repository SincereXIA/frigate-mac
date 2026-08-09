"""Apple Silicon object detector using ONNX Runtime's public CoreML provider."""

import hashlib
import json
import logging
import os
import platform
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import numpy as np
import onnxruntime as ort
from pydantic import ConfigDict, Field

from frigate.detectors.detection_api import DetectionApi
from frigate.detectors.detection_runners import ONNXModelRunner
from frigate.detectors.detector_config import (
    BaseDetectorConfig,
    InputDTypeEnum,
    InputTensorEnum,
    ModelTypeEnum,
)
from frigate.runtime.paths import RuntimePaths
from frigate.util.model import (
    post_process_dfine,
    post_process_rfdetr,
    post_process_yolo,
    post_process_yolox,
)

logger = logging.getLogger(__name__)

DETECTOR_KEY = "coreml"
COREML_PROVIDER = "CoreMLExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"


class CoreMLDetectorConfig(BaseDetectorConfig):
    """Configuration for the native Apple Silicon CoreML detector."""

    model_config = ConfigDict(title="CoreML")

    type: Literal[DETECTOR_KEY]
    inference_backend: Literal["ane", "gpu"] = Field(
        default="ane",
        title="CoreML inference backend",
        description="Use Apple Neural Engine or all CoreML compute units.",
    )
    model_format: Literal["NeuralNetwork"] = Field(
        default="NeuralNetwork",
        title="CoreML model format",
        description="CoreML format used to compile the static ONNX graph.",
    )
    require_static_input_shapes: bool = Field(
        default=True,
        title="Require static input shapes",
        description="Reject dynamic input shapes when CoreML builds the model.",
    )
    profile_compute_plan: bool = Field(
        default=False,
        title="Profile the CoreML compute plan",
        description="Emit detailed CoreML compute placement diagnostics at startup.",
    )
    allow_cpu_only: bool = Field(
        default=False,
        title="Allow CPU-only fallback",
        description="Allow startup when CoreML accepts no model partitions.",
    )
    allowed_cpu_fallback_ops: list[str] = Field(
        default_factory=lambda: ["Concat"],
        title="Allowed CPU fallback operations",
        description="Operation names that may remain on CPU without a warning.",
    )
    allow_thread_spinning: bool = Field(
        default=False,
        title="Allow ONNX Runtime thread spinning",
        description=(
            "Keep ONNX Runtime worker threads spinning between inferences. "
            "Disabling this reduces idle detector CPU usage."
        ),
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        while chunk := model_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_summary(path: Path) -> dict[str, Any]:
    """Return a diagnostics-safe provider summary from an ORT profile."""
    try:
        events = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read the CoreML startup profile")
        return {"providers": {}, "cpu_operations": {}}

    providers: Counter[str] = Counter()
    cpu_operations: Counter[str] = Counter()
    for event in events:
        if event.get("cat") != "Node":
            continue
        arguments = event.get("args", {})
        provider = str(arguments.get("provider", "unknown"))
        providers[provider] += 1
        if provider == CPU_PROVIDER:
            cpu_operations[str(arguments.get("op_name", "unknown"))] += 1

    return {
        "providers": dict(sorted(providers.items())),
        "cpu_operations": dict(sorted(cpu_operations.items())),
    }


class CoreMLDetector(DetectionApi):
    """Run supported ONNX detection models through CoreML on Apple Silicon."""

    type_key = DETECTOR_KEY

    def __init__(self, detector_config: CoreMLDetectorConfig):
        super().__init__(detector_config)
        self._status_path = (
            Path(detector_config.runtime_status_path)
            if detector_config.runtime_status_path
            else None
        )
        self._status: dict[str, Any] = {
            "schema_version": 1,
            "detector": DETECTOR_KEY,
            "pid": os.getpid(),
            "state": "initializing",
            "requested_backend": detector_config.inference_backend,
            "model_format": detector_config.model_format,
            "onnxruntime_version": ort.__version__,
        }
        self._last_status_write = 0.0
        self._initializing = True

        try:
            self._initialize(detector_config)
        except Exception as err:
            self._status.update(
                {
                    "state": "error",
                    "error_type": type(err).__name__,
                }
            )
            self._write_status(force=True)
            raise

    def _initialize(self, detector_config: CoreMLDetectorConfig) -> None:
        if platform.system() != "Darwin" or platform.machine().lower() != "arm64":
            raise RuntimeError("The CoreML detector requires Apple Silicon macOS")
        if COREML_PROVIDER not in ort.get_available_providers():
            raise RuntimeError(
                "ONNX Runtime does not include the CoreML execution provider"
            )
        if detector_config.model is None or not detector_config.model.path:
            raise ValueError("The CoreML detector requires an ONNX model path")

        model_path = Path(detector_config.model.path)
        if not model_path.is_file():
            raise FileNotFoundError(f"CoreML model file not found: {model_path}")

        model_hash = _hash_file(model_path)
        paths = RuntimePaths.from_environment()
        cache_key = hashlib.sha256(
            (
                f"{model_hash}:{ort.__version__}:{detector_config.model_format}:"
                f"{detector_config.inference_backend}:"
                f"{detector_config.require_static_input_shapes}"
            ).encode()
        ).hexdigest()[:24]
        cache_dir = paths.model_cache_dir / "coreml" / cache_key
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        compute_units = (
            "CPUAndNeuralEngine"
            if detector_config.inference_backend == "ane"
            else "ALL"
        )
        provider_options = {
            "ModelFormat": detector_config.model_format,
            "MLComputeUnits": compute_units,
            "RequireStaticInputShapes": str(
                int(detector_config.require_static_input_shapes)
            ),
            "EnableOnSubgraphs": "0",
            "ProfileComputePlan": str(int(detector_config.profile_compute_plan)),
            "ModelCacheDirectory": str(cache_dir),
        }

        session_options = ort.SessionOptions()
        allow_spinning = str(int(detector_config.allow_thread_spinning))
        session_options.add_session_config_entry(
            "session.intra_op.allow_spinning",
            allow_spinning,
        )
        session_options.add_session_config_entry(
            "session.inter_op.allow_spinning",
            allow_spinning,
        )
        session_options.enable_profiling = True
        session_options.profile_file_prefix = str(
            paths.runtime_dir / f"coreml-profile-{os.getpid()}"
        )

        logger.info(
            "CoreML: loading model with %s compute units",
            compute_units,
        )
        session = self._create_session_with_cache_recovery(
            model_path,
            session_options,
            provider_options,
            cache_dir,
        )
        self.runner = ONNXModelRunner(
            session,
            model_type=detector_config.model.model_type.value,
        )
        self.onnx_model_type = detector_config.model.model_type
        self.onnx_model_shape = detector_config.model.input_tensor
        self.model_input_name = self.runner.get_input_names()[0]

        if self.onnx_model_type == ModelTypeEnum.yolox:
            self.calculate_grids_strides()

        warmup_start = time.perf_counter()
        self.detect_raw(self._warmup_tensor(detector_config))
        warmup_ms = (time.perf_counter() - warmup_start) * 1000

        profile_path_value = session.end_profiling()
        profile_path = Path(profile_path_value) if profile_path_value else None
        profile = (
            _profile_summary(profile_path)
            if profile_path and profile_path.is_file()
            else {"providers": {}, "cpu_operations": {}}
        )
        if profile_path and profile_path.is_file():
            profile_path.unlink()

        active_providers = session.get_providers()
        coreml_partitions = profile["providers"].get(COREML_PROVIDER, 0)
        if not coreml_partitions and not detector_config.allow_cpu_only:
            raise RuntimeError("CoreML did not accept any model partitions")

        allowed_cpu_ops = set(detector_config.allowed_cpu_fallback_ops)
        unexpected_cpu_ops = sorted(
            operation
            for operation in profile["cpu_operations"]
            if operation not in allowed_cpu_ops
        )
        self._status.update(
            {
                "state": "ready",
                "model_sha256": model_hash,
                "compute_units": compute_units,
                "active_providers": active_providers,
                "provider_partitions": profile["providers"],
                "cpu_operations": profile["cpu_operations"],
                "unexpected_cpu_operations": unexpected_cpu_ops,
                "unexpected_cpu_fallback": bool(unexpected_cpu_ops),
                "warmup_ms": round(warmup_ms, 3),
                "cache_key": cache_key,
                "thread_spinning": detector_config.allow_thread_spinning,
            }
        )
        self._initializing = False
        self._write_status(force=True)
        logger.info("CoreML: model loaded")

    @staticmethod
    def _create_session_with_cache_recovery(
        model_path: Path,
        session_options: ort.SessionOptions,
        provider_options: dict[str, str],
        cache_dir: Path,
    ) -> ort.InferenceSession:
        providers: list[Any] = [
            (COREML_PROVIDER, provider_options),
            CPU_PROVIDER,
        ]
        try:
            return ort.InferenceSession(
                str(model_path),
                sess_options=session_options,
                providers=providers,
            )
        except Exception:
            if not any(cache_dir.iterdir()):
                raise

        quarantine = cache_dir.with_name(
            f"{cache_dir.name}.invalid-{int(time.time())}-{os.getpid()}"
        )
        cache_dir.rename(quarantine)
        cache_dir.mkdir(mode=0o700)
        provider_options["ModelCacheDirectory"] = str(cache_dir)
        logger.warning("CoreML: retrying after quarantining the compiled model cache")
        return ort.InferenceSession(
            str(model_path),
            sess_options=session_options,
            providers=providers,
        )

    @staticmethod
    def _warmup_tensor(detector_config: CoreMLDetectorConfig) -> np.ndarray:
        if detector_config.model.input_tensor == InputTensorEnum.nchw:
            shape = (
                1,
                3,
                detector_config.model.height,
                detector_config.model.width,
            )
        else:
            shape = (
                1,
                detector_config.model.height,
                detector_config.model.width,
                3,
            )

        dtype = (
            np.float32
            if detector_config.model.input_dtype
            in (InputDTypeEnum.float, InputDTypeEnum.float_denorm)
            else np.uint8
        )
        return np.zeros(shape, dtype=dtype)

    def _write_status(self, force: bool = False) -> None:
        if self._status_path is None:
            return
        now = time.monotonic()
        if not force and now - self._last_status_write < 5:
            return

        self._status_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_path = self._status_path.with_name(
            f".{self._status_path.name}.{os.getpid()}.tmp"
        )
        temporary_path.write_text(json.dumps(self._status, sort_keys=True))
        temporary_path.chmod(0o600)
        os.replace(temporary_path, self._status_path)
        self._last_status_write = now

    def detect_raw(self, tensor_input: np.ndarray) -> np.ndarray:
        inference_start = time.perf_counter()
        detections = self._detect_raw(tensor_input)
        inference_ms = (time.perf_counter() - inference_start) * 1000
        if not self._initializing:
            self._status["last_inference_ms"] = round(inference_ms, 3)
            self._write_status()
        return detections

    def _detect_raw(self, tensor_input: np.ndarray) -> np.ndarray:
        if self.onnx_model_type == ModelTypeEnum.dfine:
            tensor_output = self.runner.run(
                {
                    "images": tensor_input,
                    "orig_target_sizes": np.array(
                        [[self.height, self.width]], dtype=np.int64
                    ),
                }
            )
            return post_process_dfine(tensor_output, self.width, self.height)

        tensor_output = self.runner.run({self.model_input_name: tensor_input})

        if self.onnx_model_type == ModelTypeEnum.rfdetr:
            return post_process_rfdetr(tensor_output)
        if self.onnx_model_type == ModelTypeEnum.yolonas:
            predictions = tensor_output[0]
            detections = np.zeros((20, 6), np.float32)
            for index, prediction in enumerate(predictions[:20]):
                (_, x_min, y_min, x_max, y_max, confidence, class_id) = prediction
                if class_id < 0:
                    break
                detections[index] = [
                    class_id,
                    confidence,
                    y_min / self.height,
                    x_min / self.width,
                    y_max / self.height,
                    x_max / self.width,
                ]
            return detections
        if self.onnx_model_type == ModelTypeEnum.yologeneric:
            return post_process_yolo(tensor_output, self.width, self.height)
        if self.onnx_model_type == ModelTypeEnum.yolox:
            return post_process_yolox(
                tensor_output[0],
                self.width,
                self.height,
                self.grids,
                self.expanded_strides,
            )
        raise ValueError(
            f"{self.onnx_model_type} is not supported by the CoreML detector"
        )
