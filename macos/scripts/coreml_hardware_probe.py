#!/usr/bin/env python3
"""Run a bounded CoreML detector hardware and output parity probe."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def parse_arguments() -> argparse.Namespace:
    """Parse probe paths and execution mode."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coreml-only", action="store_true")
    parser.add_argument("--duration", type=float, default=0)
    return parser.parse_args()


def configure_environment(arguments: argparse.Namespace) -> None:
    """Point all Frigate writes at the bounded scratch directory."""
    sys.path.insert(0, str(arguments.workspace))
    os.environ.update(
        {
            "FRIGATE_INSTALL_DIR": str(arguments.workspace),
            "FRIGATE_CONFIG_DIR": str(arguments.scratch / "config"),
            "FRIGATE_MEDIA_DIR": str(arguments.scratch / "media"),
            "FRIGATE_CACHE_DIR": str(arguments.scratch / "cache"),
            "FRIGATE_LOG_DIR": str(arguments.scratch / "logs"),
            "FRIGATE_RUNTIME_DIR": str(arguments.scratch / "runtime"),
        }
    )
    for directory in (
        arguments.scratch / "config",
        arguments.scratch / "media",
        arguments.scratch / "cache",
        arguments.scratch / "logs",
        arguments.scratch / "runtime",
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def load_input(fixture_path: Path):
    """Load one synthetic video frame as a YOLO NCHW float tensor."""
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(fixture_path))
    available, frame = capture.read()
    capture.release()
    if not available:
        raise RuntimeError("Unable to read the synthetic video fixture")
    resized = cv2.resize(frame, (320, 320))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255


def main() -> int:
    """Run CoreML, optionally compare CPU output, and write safe JSON results."""
    arguments = parse_arguments()
    configure_environment(arguments)

    import numpy as np
    import onnxruntime as ort

    from frigate.detectors.detector_config import (
        InputDTypeEnum,
        InputTensorEnum,
        ModelConfig,
        ModelTypeEnum,
    )
    from frigate.detectors.plugins.coreml import CoreMLDetector, CoreMLDetectorConfig
    from frigate.util.model import post_process_yolo

    model = ModelConfig(
        path=str(arguments.model),
        labelmap_path=str(arguments.workspace / "labelmap.txt"),
        width=320,
        height=320,
        input_tensor=InputTensorEnum.nchw,
        input_dtype=InputDTypeEnum.float,
        model_type=ModelTypeEnum.yologeneric,
    )
    status_path = arguments.scratch / "runtime" / "coreml-status.json"
    config = CoreMLDetectorConfig(
        type="coreml",
        model=model,
        profile_compute_plan=True,
    )
    config.set_runtime_status_path(str(status_path))
    detector = CoreMLDetector(config)
    tensor = load_input(arguments.fixture)

    inference_start = time.perf_counter()
    coreml_output = detector.runner.run({detector.runner.get_input_names()[0]: tensor})
    coreml_result = post_process_yolo(coreml_output, 320, 320)
    probe_inference_ms = (time.perf_counter() - inference_start) * 1000
    deadline = time.monotonic() + arguments.duration
    inference_count = 1
    while time.monotonic() < deadline:
        coreml_result = detector.detect_raw(tensor)
        inference_count += 1

    summary = json.loads(status_path.read_text())
    summary["inference_count"] = inference_count
    summary["probe_inference_ms"] = round(probe_inference_ms, 3)

    if not arguments.coreml_only:
        cpu = ort.InferenceSession(
            str(arguments.model), providers=["CPUExecutionProvider"]
        )
        cpu_output = cpu.run(None, {cpu.get_inputs()[0].name: tensor})
        cpu_result = post_process_yolo(cpu_output, 320, 320)
        cpu_predictions = np.squeeze(cpu_output[0])
        coreml_predictions = np.squeeze(coreml_output[0])
        if cpu_predictions.shape[0] < cpu_predictions.shape[1]:
            cpu_predictions = cpu_predictions.T
            coreml_predictions = coreml_predictions.T
        cpu_scores = cpu_predictions[:, 4:]
        coreml_scores = coreml_predictions[:, 4:]
        score_max_abs = float(np.max(np.abs(cpu_scores - coreml_scores)))
        relevant = (np.max(cpu_scores, axis=1) > 0.005) | (
            np.max(coreml_scores, axis=1) > 0.005
        )
        if not np.any(relevant):
            raise RuntimeError("No candidates exceeded the parity validation floor")
        relevant_bbox_max_abs = float(
            np.max(
                np.abs(cpu_predictions[relevant, :4] - coreml_predictions[relevant, :4])
            )
        )
        relevant_classes_match = bool(
            np.array_equal(
                np.argmax(cpu_scores[relevant], axis=1),
                np.argmax(coreml_scores[relevant], axis=1),
            )
        )
        summary.update(
            {
                "raw_score_max_abs": score_max_abs,
                "relevant_bbox_max_abs_pixels": relevant_bbox_max_abs,
                "relevant_class_ids_match": relevant_classes_match,
                "relevant_candidate_count": int(np.count_nonzero(relevant)),
                "parity_max_abs": float(np.max(np.abs(cpu_result - coreml_result))),
                "parity_allclose_1e_3": bool(
                    np.allclose(cpu_result, coreml_result, rtol=1e-3, atol=1e-3)
                ),
                "cpu_detection_count": int(np.count_nonzero(cpu_result[:, 1] > 0)),
                "coreml_detection_count": int(
                    np.count_nonzero(coreml_result[:, 1] > 0)
                ),
            }
        )

    arguments.output.write_text(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
