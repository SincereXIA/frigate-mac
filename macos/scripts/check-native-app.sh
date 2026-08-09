#!/bin/zsh

set -euo pipefail

repo_root=${0:A:h:h:h}
app=${1:-${repo_root}/macos/build/Frigate.app}
runtime=${app}/Contents/Resources/Runtime

if [[ ${app} != /* ]]; then
  print -u2 "App path must be absolute"
  exit 1
fi
plutil -lint ${app}/Contents/Info.plist >/dev/null
codesign --verify --deep --strict ${app}

required=(
  ${app}/Contents/MacOS/FrigateMac
  ${runtime}/python/bin/python3
  ${runtime}/bin/ffmpeg
  ${runtime}/bin/ffprobe
  ${runtime}/bin/go2rtc
  ${runtime}/bin/nginx
  ${runtime}/frigate/web/dist/index.html
)
for required_path in ${required}; do
  if [[ ! -e ${required_path} ]]; then
    print -u2 "Missing app resource: ${required_path}"
    exit 1
  fi
done

PYTHONDONTWRITEBYTECODE=1 ${runtime}/python/bin/python3 -I -B -c \
  'import platform, sys; assert platform.machine() == "arm64"; assert sys.version_info[:2] == (3, 11)'
PYTHONDONTWRITEBYTECODE=1 ${runtime}/python/bin/python3 -I -B -c \
  'import sys; sys.path.insert(0, sys.argv[1]); import cv2, fastapi, frigate, numpy, onnxruntime, peewee, pydantic, zmq' \
  ${runtime}/frigate
${runtime}/bin/ffmpeg -hide_banner -hwaccels 2>&1 | grep -q '^videotoolbox$'
${runtime}/bin/ffmpeg -hide_banner -encoders 2>&1 | grep -q 'h264_videotoolbox'
${runtime}/bin/go2rtc -version >/dev/null
${runtime}/bin/nginx -V >/dev/null 2>&1

while IFS= read -r candidate; do
  if [[ ! -x ${candidate} && ${candidate} != *.so && ${candidate} != *.dylib ]]; then
    continue
  fi
  if ! file -b ${candidate} | grep -q 'Mach-O'; then
    continue
  fi
  if ! file -b ${candidate} | grep -q 'arm64'; then
    print -u2 "Non-ARM64 Mach-O file: ${candidate}"
    exit 1
  fi
  if otool -L ${candidate} | awk '/^\t/{print}' | rg -q '/opt/homebrew|/usr/local|/Users/|/private/tmp/'; then
    print -u2 "External dependency in app bundle: ${candidate}"
    otool -L ${candidate} | awk '/^\t/{print}' | rg '/opt/homebrew|/usr/local|/Users/|/private/tmp/' >&2
    exit 1
  fi
done < <(find ${app} -type f -print)

PYTHONDONTWRITEBYTECODE=1 ${runtime}/python/bin/python3 - ${app} <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
for link in root.rglob("*"):
    if link.is_symlink() and not link.resolve().is_relative_to(root):
        raise SystemExit(f"Symlink escapes app bundle: {link}")
PY

print "Native app acceptance passed: ${app}"
