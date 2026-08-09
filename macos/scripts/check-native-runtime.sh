#!/bin/zsh

set -euo pipefail

repo_root=${0:A:h:h:h}
python=${repo_root}/.venv-macos/bin/python
check_root=$(mktemp -d "${TMPDIR:-/private/tmp}/frigate-native-runtime.XXXXXX")

function cleanup() {
  if [[ -n ${check_root} && ${check_root} == */frigate-native-runtime.* ]]; then
    find "${check_root}" -depth -delete
  fi
}
trap cleanup EXIT

env \
  FRIGATE_INSTALL_DIR=${repo_root} \
  FRIGATE_CONFIG_DIR=${check_root}/config \
  FRIGATE_MEDIA_DIR=${check_root}/media \
  FRIGATE_CACHE_DIR=${check_root}/cache \
  FRIGATE_LOG_DIR=${check_root}/logs \
  FRIGATE_RUNTIME_DIR=${check_root}/runtime \
  FRIGATE_LABELMAP_PATH=${repo_root}/docker/main/rootfs/labelmap/coco-80.txt \
  FRIGATE_AUDIO_LABELMAP_PATH=${repo_root}/audio-labelmap.txt \
  HF_HOME=${check_root}/cache/huggingface \
  ${python} -c '
import numpy
from norfair.camera_motion import MotionEstimator

import frigate.app

assert tuple(map(int, numpy.__version__.split(".")[:2])) >= (2, 1)
assert MotionEstimator is not None
print("native runtime imports passed")
'
