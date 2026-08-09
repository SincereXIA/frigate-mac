"""Test-only ZMQ detector responder for native runtime acceptance."""

import json
import os
import signal
from pathlib import Path

import numpy as np
import zmq


def main() -> None:
    """Serve model readiness and deterministic person detections."""
    runtime_dir = Path(os.environ["FRIGATE_RUNTIME_DIR"])
    endpoint = f"ipc://{runtime_dir / 'zmq_detector'}"
    running = True

    def stop(_signal: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    context = zmq.Context()
    detector = context.socket(zmq.REP)
    detector.setsockopt(zmq.LINGER, 0)
    detector.bind(endpoint)
    poller = zmq.Poller()
    poller.register(detector, zmq.POLLIN)
    detections = np.zeros((20, 6), dtype=np.float32)
    detections[0] = [0, 0.99, 0.15, 0.3, 0.45, 0.7]
    model_requests = 0
    detection_requests = 0

    try:
        while running:
            if detector not in dict(poller.poll(200)):
                continue
            frames = detector.recv_multipart()
            header = json.loads(frames[0].decode())
            if header.get("model_request"):
                model_requests += 1
                detector.send_json(
                    {"model_available": True, "model_loaded": True}
                )
                continue

            detection_requests += 1
            detections[0, 1] = min(0.99, 0.75 + detection_requests * 0.001)
            x_shift = 0.05 if (detection_requests // 10) % 2 else 0.0
            detections[0, 3] = 0.3 + x_shift
            detections[0, 5] = 0.7 + x_shift
            detector.send_multipart(
                [
                    json.dumps(
                        {"shape": [20, 6], "dtype": "float32"}
                    ).encode(),
                    detections.tobytes(),
                ]
            )
    finally:
        metrics_path = runtime_dir / "test-detector-metrics.json"
        metrics_path.write_text(
            json.dumps(
                {
                    "model_requests": model_requests,
                    "detection_requests": detection_requests,
                }
            )
        )
        metrics_path.chmod(0o600)
        detector.close()
        context.term()


if __name__ == "__main__":
    main()
