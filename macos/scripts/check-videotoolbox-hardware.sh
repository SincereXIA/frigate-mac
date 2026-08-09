#!/bin/zsh
set -euo pipefail

repo_root=${0:A:h:h:h}
native_python=${PYTHON:-"$repo_root/.venv-macos/bin/python"}
ffmpeg_path=${FRIGATE_FFMPEG_PATH:-$(command -v ffmpeg)}
ffprobe_path=${FRIGATE_FFPROBE_PATH:-$(command -v ffprobe)}
fixture_path="$repo_root/macos/tests/fixtures/native-macos-h264.mp4"
probe_script="$repo_root/macos/scripts/videotoolbox_hardware_probe.py"

if [[ $(uname -s) != Darwin || $(uname -m) != arm64 ]]; then
  print -u2 "VideoToolbox hardware acceptance requires Apple Silicon macOS"
  exit 2
fi
for executable in "$native_python" "$ffmpeg_path" "$ffprobe_path"; do
  if [[ ! -x "$executable" ]]; then
    print -u2 "required executable is unavailable: $executable"
    exit 2
  fi
done

frameworks=$(otool -L "$ffmpeg_path")
for framework in VideoToolbox AVFoundation CoreMedia CoreVideo AudioToolbox; do
  if [[ "$frameworks" != *"/$framework.framework/"* ]]; then
    print -u2 "FFmpeg is not linked to $framework"
    exit 1
  fi
done
if ! "$ffmpeg_path" -hide_banner -hwaccels 2>&1 | grep '^videotoolbox$' >/dev/null; then
  print -u2 "FFmpeg does not expose the VideoToolbox hwaccel"
  exit 1
fi
if ! "$ffmpeg_path" -hide_banner -devices 2>/dev/null | grep -q 'avfoundation'; then
  print -u2 "FFmpeg does not expose the AVFoundation input device"
  exit 1
fi

probe_root=$(mktemp -d /private/tmp/frigate-videotoolbox.XXXXXX)
cleanup() {
  find "$probe_root" -depth -type f -delete 2>/dev/null || true
  find "$probe_root" -depth -type l -delete 2>/dev/null || true
  find "$probe_root" -depth -type d -empty -delete 2>/dev/null || true
}
trap cleanup EXIT

mkdir -m 700 "$probe_root/work"
PYTHONPATH="$repo_root" "$native_python" "$probe_script" \
  --ffmpeg "$ffmpeg_path" \
  --ffprobe "$ffprobe_path" \
  --fixture "$fixture_path" \
  --scratch "$probe_root/work" \
  --output "$probe_root/result.json"

RESULT_PATH="$probe_root/result.json" "$native_python" - <<'PY'
import json
import os
from pathlib import Path

result = json.loads(Path(os.environ["RESULT_PATH"]).read_text())
print(
    "VideoToolbox decode: "
    f"H.264={result['h264_decode']['frames']} frames, "
    f"HEVC={result['hevc_decode']['frames']} frames"
)
for name in ("birdseye", "preview", "timelapse"):
    output = result["outputs"][name]
    if output["codec"] != "h264" or output["frames"] < 1 or output["bytes"] < 1:
        raise SystemExit(f"invalid {name} VideoToolbox output")
    print(
        f"VideoToolbox {name}: {output['frames']} frames, "
        f"{output['width']}x{output['height']}, {output['bytes']} bytes"
    )
hevc = result["outputs"]["hevc"]
if hevc["codec"] != "hevc" or hevc["frames"] < 1:
    raise SystemExit("invalid HEVC VideoToolbox output")
PY
