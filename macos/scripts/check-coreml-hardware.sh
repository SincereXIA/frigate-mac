#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
native_python=${PYTHON:-"$repo_root/.venv-macos/bin/python"}
model_path=${1:-}
fixture_path="$repo_root/macos/tests/fixtures/native-macos-h264.mp4"
probe_script="$repo_root/macos/scripts/coreml_hardware_probe.py"

if [[ -z "$model_path" || ! -f "$model_path" ]]; then
  print -u2 "usage: $0 /absolute/path/to/static-yolo-model.onnx"
  exit 2
fi
if [[ ${model_path:0:1} != / ]]; then
  print -u2 "the model path must be absolute"
  exit 2
fi
if [[ ! -x "$native_python" ]]; then
  print -u2 "native Python is unavailable: $native_python"
  exit 2
fi
if [[ $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
  print -u2 "CoreML hardware acceptance requires Apple Silicon macOS"
  exit 2
fi

probe_root=$(mktemp -d /private/tmp/frigate-coreml-acceptance.XXXXXX)
cleanup() {
  find "$probe_root" -depth -type f -delete 2>/dev/null || true
  find "$probe_root" -depth -type d -empty -delete 2>/dev/null || true
}
trap cleanup EXIT

mkdir -m 700 "$probe_root/standard"
"$native_python" "$probe_script" \
  --model "$model_path" \
  --fixture "$fixture_path" \
  --workspace "$repo_root" \
  --scratch "$probe_root/standard" \
  --output "$probe_root/standard-result.json" \
  >"$probe_root/standard.log" 2>&1

RESULT_PATH="$probe_root/standard-result.json" "$native_python" - <<'PY'
import json
import os
from pathlib import Path

result = json.loads(Path(os.environ["RESULT_PATH"]).read_text())
coreml_partitions = result.get("provider_partitions", {}).get(
    "CoreMLExecutionProvider", 0
)
if result.get("state") != "ready":
    raise SystemExit("CoreML detector did not become ready")
if coreml_partitions < 1:
    raise SystemExit("CoreML accepted no model partitions")
if result.get("unexpected_cpu_fallback"):
    raise SystemExit("unexpected CPU fallback operations were detected")
if not result.get("parity_allclose_1e_3"):
    raise SystemExit("CoreML output exceeded the CPU parity tolerance")
if result.get("raw_score_max_abs", float("inf")) > 0.005:
    raise SystemExit("CoreML confidence scores exceeded the CPU tolerance")
if result.get("relevant_bbox_max_abs_pixels", float("inf")) > 1:
    raise SystemExit("CoreML candidate boxes exceeded the one-pixel CPU tolerance")
if not result.get("relevant_class_ids_match"):
    raise SystemExit("CoreML candidate classes differ from the CPU provider")

print("CoreML detector: ready")
print(f"compute units: {result['compute_units']}")
print(f"provider partitions: {result['provider_partitions']}")
print(f"allowed CPU operations: {result['cpu_operations']}")
print(f"warmup: {result['warmup_ms']:.3f} ms")
print(f"probe inference: {result['probe_inference_ms']:.3f} ms")
print(f"CPU parity max absolute error: {result['parity_max_abs']:.8f}")
print(f"candidate score max error: {result['raw_score_max_abs']:.8f}")
print(
    "candidate box max error: "
    f"{result['relevant_bbox_max_abs_pixels']:.4f} pixels"
)
print(f"candidate classes matched: {result['relevant_candidate_count']}")
PY

if ! command -v xcrun >/dev/null || ! xcrun xctrace list templates 2>/dev/null | grep '^Core ML$' >/dev/null; then
  print -u2 "Core ML Instruments template is unavailable"
  exit 1
fi

mkdir -m 700 "$probe_root/instruments"
PROBE_ROOT="$probe_root" \
MODEL_PATH="$model_path" \
FIXTURE_PATH="$fixture_path" \
REPO_ROOT="$repo_root" \
NATIVE_PYTHON="$native_python" \
PROBE_SCRIPT="$probe_script" \
"$native_python" - <<'PY'
import json
import os
import signal
import subprocess
import time
from pathlib import Path

root = Path(os.environ["PROBE_ROOT"])
target = [
    os.environ["NATIVE_PYTHON"],
    os.environ["PROBE_SCRIPT"],
    "--model",
    os.environ["MODEL_PATH"],
    "--fixture",
    os.environ["FIXTURE_PATH"],
    "--workspace",
    os.environ["REPO_ROOT"],
    "--scratch",
    str(root / "instruments"),
    "--output",
    str(root / "instruments-result.json"),
    "--coreml-only",
    "--duration",
    "60",
]
with (root / "instruments-target.log").open("wb") as target_output:
    target_process = subprocess.Popen(
        target,
        stdout=target_output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    status_path = root / "instruments" / "runtime" / "coreml-status.json"
    readiness_deadline = time.monotonic() + 20
    while time.monotonic() < readiness_deadline:
        if status_path.is_file():
            status = json.loads(status_path.read_text())
            if status.get("state") == "ready":
                break
        if target_process.poll() is not None:
            raise SystemExit("CoreML Instruments target exited before readiness")
        time.sleep(0.2)
    else:
        target_process.terminate()
        target_process.wait(timeout=5)
        raise SystemExit("CoreML Instruments target did not become ready")

    print("Instruments target: ready", flush=True)

    record = [
        "xcrun",
        "xctrace",
        "record",
        "--quiet",
        "--no-prompt",
        "--template",
        "Core ML",
        "--time-limit",
        "5s",
        "--output",
        str(root / "coreml.trace"),
        "--attach",
        str(target_process.pid),
    ]
    previous_sigterm = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
    with (root / "xctrace.log").open("wb") as output:
        record_result = subprocess.run(
            record,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    signal.signal(signal.SIGTERM, previous_sigterm)
    signal.signal(signal.SIGINT, previous_sigint)
    print(f"Instruments recording exit: {record_result.returncode}", flush=True)
    if record_result.returncode:
        target_process.terminate()
        target_process.wait(timeout=5)
        raise SystemExit((root / "xctrace.log").read_text()[-2000:])
    if target_process.poll() is None:
        target_process.terminate()
        target_process.wait(timeout=5)

xpath = (
    '/trace-toc/run[@number="1"]/data/'
    'table[@schema="ane-hw-intervals-internal"]'
)
export = [
    "xcrun",
    "xctrace",
    "export",
    "--quiet",
    "--input",
    str(root / "coreml.trace"),
    "--xpath",
    xpath,
    "--output",
    str(root / "ane.xml"),
]
with (root / "xctrace-export.log").open("wb") as output:
    export_result = subprocess.run(
        export,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
print(f"Instruments export exit: {export_result.returncode}", flush=True)
if export_result.returncode:
    raise SystemExit((root / "xctrace-export.log").read_text()[-2000:])
PY

ANE_PATH="$probe_root/ane.xml" "$native_python" - <<'PY'
import os
import xml.etree.ElementTree as ET
from pathlib import Path

root = ET.fromstring(Path(os.environ["ANE_PATH"]).read_text())
values_by_id = {
    element.attrib["id"]: element.attrib.get("fmt", "")
    for element in root.iter()
    if "id" in element.attrib
}
prediction_intervals = 0
for row in root.findall(".//row"):
    label = row.find("formatted-label")
    if label is None:
        continue
    value = label.attrib.get("fmt", "")
    if not value and "ref" in label.attrib:
        value = values_by_id.get(label.attrib["ref"], "")
    if "Neural Engine" in value and "Prediction" in value:
        prediction_intervals += 1

if prediction_intervals < 1:
    raise SystemExit("Instruments recorded no Apple Neural Engine predictions")
print(f"Instruments ANE prediction intervals: {prediction_intervals}")
PY
