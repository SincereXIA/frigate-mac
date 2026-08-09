#!/bin/zsh

set -euo pipefail

repo_root=${0:A:h:h:h}
macos_root=${repo_root}/macos
output_app=${1:-${macos_root}/build/Frigate.app}
python_version=3.11.15
python_release=20260807
python_archive=cpython-${python_version}+${python_release}-aarch64-apple-darwin-install_only_stripped.tar.gz
python_url=https://github.com/astral-sh/python-build-standalone/releases/download/${python_release}/cpython-${python_version}%2B${python_release}-aarch64-apple-darwin-install_only_stripped.tar.gz
python_sha256=76b27e15a5be9539b830fc698e2646d001b84a66500eeb5228cee46909d6f2cf

if [[ ${output_app} != /* ]]; then
  print -u2 "Output app path must be absolute"
  exit 1
fi

build_root=$(mktemp -d /tmp/frigate-app-build.XXXXXX)
cleanup() {
  find ${build_root} -mindepth 1 -delete
  rmdir ${build_root}
}
trap cleanup EXIT

python_cache=${macos_root}/.runtime/downloads/${python_archive}
mkdir -p ${python_cache:h}
if [[ ! -f ${python_cache} ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 \
    --output ${python_cache}.partial ${python_url}
  mv ${python_cache}.partial ${python_cache}
fi
actual_sha256=$(shasum -a 256 ${python_cache} | awk '{print $1}')
if [[ ${actual_sha256} != ${python_sha256} ]]; then
  print -u2 "Standalone Python checksum mismatch"
  exit 1
fi

swift build --package-path ${macos_root} -c release
app_stage=${build_root}/Frigate.app
contents=${app_stage}/Contents
runtime=${contents}/Resources/Runtime
mkdir -p ${contents}/MacOS ${runtime}/bin ${runtime}/frigate/web
install -m 755 ${macos_root}/.build/release/FrigateMac ${contents}/MacOS/FrigateMac
install -m 644 ${macos_root}/FrigateMac/Resources/Info.plist ${contents}/Info.plist

tar -xzf ${python_cache} -C ${runtime}
if [[ ! -x ${runtime}/python/bin/python3 ]]; then
  print -u2 "Standalone Python archive has an unexpected layout"
  exit 1
fi

${runtime}/python/bin/python3 -m pip install \
  --disable-pip-version-check --cache-dir ${macos_root}/.runtime/pip-cache \
  -r ${macos_root}/requirements-runtime.txt
${runtime}/python/bin/python3 -m pip install \
  --disable-pip-version-check --cache-dir ${macos_root}/.runtime/pip-cache \
  --no-deps 'norfair==2.3.*'

ditto ${repo_root}/frigate ${runtime}/frigate/frigate
ditto ${repo_root}/migrations ${runtime}/frigate/migrations
ditto ${repo_root}/web/dist ${runtime}/frigate/web/dist
ditto ${repo_root}/docker/main/rootfs/labelmap ${runtime}/frigate/docker/main/rootfs/labelmap
install -m 644 ${repo_root}/labelmap.txt ${runtime}/frigate/labelmap.txt
install -m 644 ${repo_root}/audio-labelmap.txt ${runtime}/frigate/audio-labelmap.txt

ffmpeg_path=${FRIGATE_FFMPEG_PATH:-$(command -v ffmpeg)}
ffprobe_path=${FRIGATE_FFPROBE_PATH:-$(command -v ffprobe)}
go2rtc_path=${FRIGATE_GO2RTC_PATH:-${macos_root}/.runtime/bin/go2rtc}
nginx_path=${FRIGATE_NGINX_PATH:-${macos_root}/.runtime/bin/nginx}
for binary in ${ffmpeg_path} ${ffprobe_path} ${go2rtc_path} ${nginx_path}; do
  if [[ ! -x ${binary} ]]; then
    print -u2 "Required native binary is missing: ${binary}"
    exit 1
  fi
done
install -m 755 ${ffmpeg_path} ${runtime}/bin/ffmpeg
install -m 755 ${ffprobe_path} ${runtime}/bin/ffprobe
install -m 755 ${go2rtc_path} ${runtime}/bin/go2rtc
install -m 755 ${nginx_path} ${runtime}/bin/nginx

${macos_root}/scripts/relocate-macho.py ${runtime}

while IFS= read -r binary; do
  codesign --force --sign - ${binary}
done < <(find ${runtime} -type f -print | while IFS= read -r candidate; do
  if [[ -x ${candidate} || ${candidate} == *.so || ${candidate} == *.dylib ]] && \
      file -b ${candidate} | grep -q 'Mach-O'; then
    print -r -- ${candidate}
  fi
done)
codesign --force --sign - \
  --entitlements ${macos_root}/FrigateMac/Resources/FrigateMac.entitlements \
  ${app_stage}

mkdir -p ${output_app:h}
if [[ -e ${output_app} ]]; then
  print -u2 "Refusing to overwrite existing app: ${output_app}"
  exit 1
fi
ditto ${app_stage} ${output_app}
print "Built ${output_app}"
